# encoding: utf-8
from __future__ import annotations

import string
import logging
import collections
import json
import re
from dateutil.parser import parse, ParserError as DateParserError
from typing import Any, NoReturn, Optional

import six
from ckan.common import config


from .backends import get_backend
import ckan.model as model
from ckan.plugins import (PluginImplementations,
                          IPackageController)
import ckan.logic as logic
import ckan.lib.plugins as lib_plugins
import ckan.lib.navl.dictization_functions
from ckan.types import Context

log = logging.getLogger(__name__)

TYPE_FIELD = "entity_type"
PACKAGE_TYPE = "package"
KEY_CHARS = string.digits + string.ascii_letters + "_-"

INDEX_FIELDS = [TYPE_FIELD, "res_url", "text", "urls", "indexed_ts", "site_id"]
RESERVED_FIELDS = INDEX_FIELDS + ["tags", "groups", "res_name", "res_description",
                                 "res_format", "res_url", "res_type"]

# Regular expression used to strip invalid XML characters
_illegal_xml_chars_re = re.compile(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]')

def escape_xml_illegal_chars(val: str, replacement: str='') -> str:
    '''
        Replaces any character not supported by XML with
        a replacement string (default is an empty string)
        Thanks to http://goo.gl/ZziIz
    '''
    return _illegal_xml_chars_re.sub(replacement, val)


def clear_index() -> None:
    get_backend().clear(config.get('ckan.site_id'))


class SearchIndex(object):
    """
    A search index handles the management of documents of a specific type in the
    index, but no queries.
    The default implementation maps many of the methods, so most subclasses will
    only have to implement ``update_dict`` and ``remove_dict``.
    """

    def __init__(self) -> None:
        pass

    def insert_dict(self, data: dict[str, Any]) -> None:
        """ Insert new data from a dictionary. """
        return self.update_dict(data)

    def update_dict(self, data: dict[str, Any], defer_commit: bool = False) -> None:
        """ Update data from a dictionary. """
        log.debug("NOOP Index: %s", ",".join(data.keys()))

    def remove_dict(self, data: dict[str, Any]) -> None:
        """ Delete an index entry uniquely identified by ``data``. """
        log.debug("NOOP Delete: %s", ",".join(data.keys()))

    def clear(self) -> None:
        """ Delete the complete index. """
        clear_index()

    def get_all_entity_ids(self) -> NoReturn:
        """ Return a list of entity IDs in the index. """
        raise NotImplemented

class NoopSearchIndex(SearchIndex): pass

class PackageSearchIndex(SearchIndex):
    def remove_dict(self, pkg_dict: dict[str, Any]) -> None:
        self.delete_package(pkg_dict)

    def update_dict(self,
                    pkg_dict: dict[str, Any],
                    defer_commit: bool = False) -> None:
        self.index_package(pkg_dict, defer_commit)

    def index_package(self,
                      pkg_dict: Optional[dict[str, Any]],
                      defer_commit: bool = False) -> None:
        if pkg_dict is None:
            return
        pkg_dict = dict(pkg_dict)

        # Index validated data-dict
        assert 'with_custom_schema' in pkg_dict, (
            'both default and custom schema data must be passed')
        validated_pkg_dict = pkg_dict.pop('with_custom_schema')

        pkg_dict['data_dict'] = json.dumps(pkg_dict)
        pkg_dict['validated_data_dict'] = json.dumps(validated_pkg_dict,
            cls=ckan.lib.navl.dictization_functions.MissingNullEncoder)

        # add to string field for sorting
        title = pkg_dict.get('title')
        if title:
            pkg_dict['title_string'] = title

        if config.get('ckan.search.remove_deleted_packages'):
            # delete the package if there is no state, or the state is `deleted`
            if pkg_dict.get('state') in [None, 'deleted']:
                return self.delete_package(pkg_dict)

        index_fields = RESERVED_FIELDS + list(pkg_dict.keys())

        # include the extras in the main namespace
        extras = pkg_dict.get('extras', [])
        for extra in extras:
            key, value = extra['key'], extra['value']
            if isinstance(value, (tuple, list)):
                value = " ".join(map(str, value))
            key = ''.join([c for c in key if c in KEY_CHARS])
            pkg_dict['extras_' + key] = value
            if key not in index_fields:
                pkg_dict[key] = value
        pkg_dict.pop('extras', None)

        # add tags, removing vocab tags from 'tags' list and adding them as
        # vocab_<tag name> so that they can be used in facets
        non_vocab_tag_names = []
        tags = pkg_dict.pop('tags', [])
        context = Context()

        for tag in tags:
            if tag.get('vocabulary_id'):
                data = {'id': tag['vocabulary_id']}
                vocab = logic.get_action('vocabulary_show')(context, data)
                key = u'vocab_%s' % vocab['name']
                if key in pkg_dict:
                    pkg_dict[key].append(tag['name'])
                else:
                    pkg_dict[key] = [tag['name']]
            else:
                non_vocab_tag_names.append(tag['name'])

        pkg_dict['tags'] = non_vocab_tag_names

        # add groups
        groups = pkg_dict.pop('groups', [])

        # we use the capacity to make things private in the search index
        if pkg_dict['private']:
            pkg_dict['capacity'] = 'private'
        else:
            pkg_dict['capacity'] = 'public'

        pkg_dict['groups'] = [group['name'] for group in groups]

        # if there is an owner_org we want to add this to groups for index
        # purposes
        if pkg_dict.get('organization'):
           pkg_dict['organization'] = pkg_dict['organization']['name']
        else:
           pkg_dict['organization'] = None

        resource_fields = [('name', 'res_name'),
                           ('description', 'res_description'),
                           ('format', 'res_format'),
                           ('url', 'res_url'),
                           ('resource_type', 'res_type')]
        resource_extras = [(e, 'res_extras_' + e) for e
                            in model.Resource.get_extra_columns()]
        # flatten the structure for indexing:
        for resource in pkg_dict.get('resources', []):
            for (okey, nkey) in resource_fields + resource_extras:
                pkg_dict[nkey] = pkg_dict.get(nkey, []) + [resource.get(okey, u'')]
        pkg_dict.pop('resources', None)

        rel_dict: dict[str, list[Any]] = collections.defaultdict(list)
        subjects = pkg_dict.pop("relationships_as_subject", [])
        objects = pkg_dict.pop("relationships_as_object", [])

        def related_package(rel: dict[str, Any], key: str) -> model.Package:
            # the show schema knows no package id keys, so a validated
            # dict carries them under __extras
            pkg = model.Package.get(
                rel.get(key) or rel.get('__extras', {}).get(key))
            assert pkg
            return pkg

        for rel in objects:
            type = model.PackageRelationship.forward_to_reverse_type(rel['type'])
            rel_dict[type].append(
                related_package(rel, 'subject_package_id').name)
        for rel in subjects:
            rel_dict[rel['type']].append(
                related_package(rel, 'object_package_id').name)
        for key, value in rel_dict.items():
            if key not in pkg_dict:
                pkg_dict[key] = value

        pkg_dict[TYPE_FIELD] = PACKAGE_TYPE

        # Save dataset type
        pkg_dict['dataset_type'] = pkg_dict['type']

        # clean the dict fixing keys and dates
        new_dict = {}
        for key, value in pkg_dict.items():
            key = six.ensure_str(key)
            if key.endswith('_date'):
                if not value:
                    continue
                try:
                    date = parse(value)
                    value = date.isoformat()
                    if not date.tzinfo:
                        value += 'Z'
                except DateParserError:
                    log.warning('%r: %r value of %r is not a valid date', pkg_dict['id'], key, value)
                    continue
            new_dict[key] = value

        pkg_dict = new_dict

        for k in ('title', 'notes', 'title_string'):
            if k in pkg_dict and pkg_dict[k]:
                pkg_dict[k] = escape_xml_illegal_chars(pkg_dict[k])

        # normalize dates: the index gets ISO dates in UTC (trailing Z)
        pkg_dict['metadata_created'] += 'Z'
        pkg_dict['metadata_modified'] += 'Z'

        # mark this CKAN instance as data source:
        pkg_dict['site_id'] = config.get('ckan.site_id')

        # Strip a selection of the fields.
        # These fields are possible candidates for sorting search results on,
        # so we strip leading spaces because " " would sort before "a" or "A".
        for field_name in ['title']:
            try:
                value = pkg_dict.get(field_name)
                if value:
                    pkg_dict[field_name] = value.lstrip()
            except KeyError:
                pass

        # add a unique index_id to avoid conflicts
        import hashlib
        pkg_dict['index_id'] = hashlib.md5(six.b('%s%s' % (pkg_dict['id'],config.get('ckan.site_id')))).hexdigest()

        for item in PluginImplementations(IPackageController):
            pkg_dict = item.before_dataset_index(pkg_dict)

        assert pkg_dict, 'Plugin must return non empty package dict on index'

        # permission labels determine visibility in search, can't be set
        # in original dataset or before_dataset_index plugins
        labels = lib_plugins.get_permission_labels()
        dataset = model.Package.get(pkg_dict['id'])
        pkg_dict['permission_labels'] = labels.get_dataset_labels(
            dataset) if dataset else [] # TestPackageSearchIndex-workaround

        # send to the search backend:
        get_backend().index(pkg_dict, defer_commit=defer_commit)

        commit_debug_msg = 'Not committed yet' if defer_commit else 'Committed'
        log.debug('Updated index for %s [%s]', pkg_dict.get('name'), commit_debug_msg)

    def commit(self) -> None:
        get_backend().commit()

    def delete_package(self, pkg_dict: dict[str, Any]) -> None:
        get_backend().delete(
            str(pkg_dict.get('id')), config.get('ckan.site_id'))
