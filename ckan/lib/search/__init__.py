# encoding: utf-8

from __future__ import annotations

import logging
import sys
import traceback

from typing import Collection, Any, Optional, Type, overload

import ckan.model as model
import ckan.model.domain_object as domain_object
import ckan.logic as logic
from ckan.types import Context
from ckan.common import config

from ckan.lib.search.common import (
    SearchIndexError, SearchQueryError,  # type: ignore
    SearchConnectionError,  # type: ignore
    SearchError, is_available  # type: ignore
)
from ckan.lib.search.backends import get_backend
from ckan.lib.search.index import (
    SearchIndex, PackageSearchIndex, NoopSearchIndex
)
from ckan.lib.search.query import (
    SearchQuery,
    TagSearchQuery, ResourceSearchQuery, PackageSearchQuery,
    QueryOptions, convert_legacy_parameters  # type: ignore
)


log = logging.getLogger(__name__)


def text_traceback() -> str:
    return "".join(traceback.format_exception(*sys.exc_info()))


DEFAULT_OPTIONS = {
    'limit': 20,
    'offset': 0,
    # about presenting the results
    'order_by': 'rank',
    'return_objects': False,
    'ref_entity_with_attr': 'name',
    'all_fields': False,
    'search_tags': True,
    'callback': None,  # simply passed through
}

_INDICES: dict[str, Type[SearchIndex]] = {
    'package': PackageSearchIndex
}

_QUERIES: dict[str, Type[SearchQuery]] = {
    'tag': TagSearchQuery,
    'resource': ResourceSearchQuery,
    'package': PackageSearchQuery
}

def _normalize_type(_type: Any) -> str:
    if isinstance(_type, domain_object.DomainObject):
        _type = _type.__class__
    if isinstance(_type, type):
        _type = _type.__name__
    return _type.strip().lower()


@overload
def index_for(_type: Type[model.Package]) -> PackageSearchIndex: ...

@overload
def index_for(_type: Any) -> SearchIndex: ...

def index_for(_type: Any) -> SearchIndex:
    """ Get a SearchIndex instance sub-class suitable for
        the specified type. """
    try:
        _type_n = _normalize_type(_type)
        return _INDICES[_type_n]()
    except KeyError:
        log.warning("Unknown search type: %s", _type)
        return NoopSearchIndex()


@overload
def query_for(_type: Type[model.Package]) -> PackageSearchQuery:
    ...


@overload
def query_for(_type: Type[model.Resource]) -> ResourceSearchQuery:
    ...


@overload
def query_for(_type: Type[model.Tag]) -> TagSearchQuery:
    ...


def query_for(_type: Any) -> SearchQuery:
    """ Get a SearchQuery instance sub-class suitable for the specified
        type. """
    try:
        _type_n = _normalize_type(_type)
        return _QUERIES[_type_n]()
    except KeyError:
        raise SearchError("Unknown search type: %s" % _type)


def rebuild(package_id: Optional[str] = None,
            only_missing: bool = False,
            force: bool = False,
            defer_commit: bool = False,
            package_ids: Optional[Collection[str]] = None,
            quiet: bool = False,
            clear: bool = False):
    '''
        Rebuilds the search index.

        If a dataset id is provided, only this dataset will be reindexed.
        When reindexing all datasets, if only_missing is True, only the
        datasets not already indexed will be processed. If force equals
        True, if an exception is found, the exception will be logged, but
        the process will carry on.
    '''
    log.info("Rebuilding search index...")

    package_index = index_for(model.Package)
    context: Context = {
        'ignore_auth': True,
    }

    if package_id:
        log.info('Indexing just package %r...', package_id)
        logic.index_update_package(context, package_id)
    elif package_ids is not None:
        for package_id in package_ids:
            log.info('Indexing just package %r...', package_id)
            logic.index_update_package(context, package_id, True)
    else:
        packages = model.Session.query(model.Package.id)
        if config.get('ckan.search.remove_deleted_packages'):
            packages = packages.filter(model.Package.state != 'deleted')

        package_ids = [r[0] for r in packages.all()]

        if only_missing:
            log.info('Indexing only missing packages...')
            package_query = query_for(model.Package)
            indexed_pkg_ids = set(package_query.get_all_entity_ids(
                max_results=len(package_ids)))
            # Packages not indexed
            package_ids = set(package_ids) - indexed_pkg_ids

            if len(package_ids) == 0:
                log.info('All datasets are already indexed')
                return
        else:
            log.info('Rebuilding the whole index...')
            # When refreshing, the index is not previously cleared
            if clear:
                package_index.clear()

        total_packages = len(package_ids)
        for counter, pkg_id in enumerate(package_ids):
            if not quiet:
                sys.stdout.write(
                    "\rIndexing dataset {0}/{1}".format(
                        counter +1, total_packages)
                )
                sys.stdout.flush()
            try:
                logic.index_update_package(context, pkg_id, defer_commit)
            except Exception as e:
                log.error('Error while indexing dataset %s: %s',
                    pkg_id, repr(e))
                if force:
                    log.error(text_traceback())
                    continue
                else:
                    raise

    model.Session.commit()
    log.info('Finished rebuilding search index.')


def commit() -> None:
    package_index = index_for(model.Package)
    package_index.commit()
    log.info('Committed pending changes on the search index')


def check() -> None:
    package_query = query_for(model.Package)

    log.debug("Checking packages search index...")
    pkgs_q = model.Session.query(model.Package).filter_by(
        state=model.State.ACTIVE)
    pkgs = {pkg.id for pkg in pkgs_q}
    indexed_pkgs = set(package_query.get_all_entity_ids(max_results=len(pkgs)))
    pkgs_not_indexed = pkgs - indexed_pkgs
    print('Packages not indexed = %i out of %i' % (len(pkgs_not_indexed),
                                                   len(pkgs)))
    for pkg_id in pkgs_not_indexed:
        pkg = model.Session.get(model.Package, pkg_id)
        assert pkg
        print((pkg.metadata_modified.strftime('%Y-%m-%d'), pkg.name))


def show(package_reference: str) -> dict[str, Any]:
    package_query = query_for(model.Package)
    return package_query.get_index(package_reference)


def clear(package_reference: str) -> None:
    package_index = index_for(model.Package)
    log.debug("Clearing search index for dataset %s...",
              package_reference)
    package_index.delete_package({'id': package_reference})


def clear_all() -> None:
    package_index = index_for(model.Package)
    log.debug("Clearing search index...")
    package_index.clear()

def check_schema(schema_file: Optional[str] = None) -> bool:
    '''
        Checks that the schema of the configured search backend is
        compatible with this CKAN version.

        Returns False when the check does not apply (for instance because
        the search engine is not available) and raises SearchError when
        the schema is present but not supported.

        :schema_file: Absolute path to an alternative schema file. Should
                      be only used for testing purposes (Default is None)
    '''
    return get_backend().check_schema(schema_file)

