# encoding: utf-8
"""Apache Solr search backend.

This is the code CKAN has always used to talk to Solr, moved behind the
:class:`ckan.lib.search.backends.base.SearchBackend` contract. It is the
reference implementation for anyone writing a backend for another engine.
"""
from __future__ import annotations

import datetime
import logging
import re
import socket
import xml.dom.minidom
from typing import Any, Optional, cast

import pysolr
import requests
import simplejson
from requests.auth import HTTPBasicAuth
from six.moves.urllib.parse import quote_plus  # type: ignore

from ckan.common import config
from ckan.lib.search.backends.base import SearchBackend, SearchResponse
from ckan.lib.search.common import (
    SearchConnectionError, SearchError, SearchIndexError, SearchQueryError,
)

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSIONS = ['2.8', '2.9', '2.10', '2.11', '2.12']

SOLR_SCHEMA_FILE_OFFSET_MANAGED = '/schema?wt=schema.xml'
SOLR_SCHEMA_FILE_OFFSET_CLASSIC = '/admin/file/?file=schema.xml'

TYPE_FIELD = "entity_type"
PACKAGE_TYPE = "package"


def make_connection(decode_dates: bool = True) -> pysolr.Solr:
    solr_url: str = config["solr_url"]
    solr_user: str | None = config["solr_user"]
    solr_password: str | None = config["solr_password"]

    if solr_url and solr_user and solr_password:
        # Rebuild the URL with the username/password
        match = re.search('http(?:s)?://', solr_url)
        assert match
        protocol = match.group()
        solr_url = re.sub(protocol, '', solr_url)
        solr_url = "{}{}:{}@{}".format(protocol,
                                       quote_plus(solr_user),
                                       quote_plus(solr_password),
                                       solr_url)

    timeout = config.get('solr_timeout')

    if decode_dates:
        decoder = simplejson.JSONDecoder(object_hook=solr_datetime_decoder)
        return pysolr.Solr(solr_url, decoder=decoder, timeout=timeout)
    else:
        return pysolr.Solr(solr_url, timeout=timeout)


def solr_datetime_decoder(d: dict[str, Any]) -> dict[str, Any]:
    for k, v in d.items():
        if isinstance(v, str):
            possible_datetime = re.search(pysolr.DATETIME_REGEX, v)
            if possible_datetime:
                date_values: dict[str, Any] = possible_datetime.groupdict()
                for dk, dv in date_values.items():
                    date_values[dk] = int(dv)

                d[k] = datetime.datetime(date_values['year'],
                                         date_values['month'],
                                         date_values['day'],
                                         date_values['hour'],
                                         date_values['minute'],
                                         date_values['second'])
    return d


def _get_schema_from_solr(file_offset: str) -> requests.Response:

    timeout = config.get('ckan.requests.timeout')

    solr_url: str = config["solr_url"]
    solr_user: str | None = config["solr_user"]
    solr_password: str | None = config["solr_password"]

    url = solr_url.strip('/') + file_offset

    if solr_user is not None and solr_password is not None:
        response = requests.get(
            url,
            timeout=timeout,
            auth=HTTPBasicAuth(solr_user, solr_password))
    else:
        response = requests.get(url, timeout=timeout)

    return response


class SolrSearchBackend(SearchBackend):
    name = "solr"

    def is_available(self) -> bool:
        try:
            conn = make_connection()
            conn.search(q="*:*", rows=1)
        except Exception as e:
            log.exception(e)
            return False
        return True

    def check_schema(self, schema_file: Optional[str] = None) -> bool:
        '''
        Checks if the schema version of the SOLR server is compatible
        with this CKAN version.

        The schema will be retrieved from the SOLR server, using the
        offset defined in SOLR_SCHEMA_FILE_OFFSET_MANAGED
        ('/schema?wt=schema.xml'). If SOLR is set to use the manually
        edited `schema.xml`, the schema will be retrieved from the SOLR
        server using the offset defined in
        SOLR_SCHEMA_FILE_OFFSET_CLASSIC ('/admin/file/?file=schema.xml').

        The schema_file parameter allows to override this pointing to
        different schema file, but it should only be used for testing
        purposes.

        If the SOLR server is not available, the function will return
        False, as the version check does not apply. If the SOLR server is
        available, a SearchError exception will be thrown if the version
        could not be extracted or it is not included in the supported
        versions list.
        '''
        if not self.is_available():
            # Something is wrong with the SOLR server
            log.warning(
                'Problems were found while connecting to the SOLR server')
            return False

        # Try to get the schema XML file to extract the version
        if not schema_file:
            try:
                # Try Managed Schema
                res = _get_schema_from_solr(SOLR_SCHEMA_FILE_OFFSET_MANAGED)
                res.raise_for_status()
            except requests.HTTPError:
                # Fallback to Manually Edited schema.xml
                res = _get_schema_from_solr(SOLR_SCHEMA_FILE_OFFSET_CLASSIC)
            schema_content: Any = res.text
        else:
            with open(schema_file, 'rb') as f:
                schema_content = f.read()

        tree = xml.dom.minidom.parseString(schema_content)

        # Up to CKAN 2.9 the schema version was stored in the `version`
        # attribute. Going forward, we are storing it in the `name` one in
        # the form `ckan-X.Y`
        version = ''
        if tree.documentElement is not None:
            name_attr = tree.documentElement.getAttribute('name')
            if name_attr.startswith('ckan-'):
                version = name_attr.split('-')[1]
            else:
                version = tree.documentElement.getAttribute('version')

        if not len(version):
            msg = 'Could not extract version info from the SOLR schema'
            if schema_file:
                msg += ', using file {}'.format(schema_file)
            raise SearchError(msg)

        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise SearchError(
                'SOLR schema version not supported: %s. Supported'
                ' versions are [%s]'
                % (version, ', '.join(SUPPORTED_SCHEMA_VERSIONS)))
        return True

    def index(self, doc: dict[str, Any], defer_commit: bool = False) -> None:
        conn = None
        try:
            conn = make_connection()
            commit = not defer_commit
            if not config.get('ckan.search.solr_commit'):
                commit = False
            conn.add(docs=[doc], commit=commit)
        except pysolr.SolrError as e:
            msg = 'Solr returned an error: {0}'.format(
                e.args[0][:1000]  # limit huge responses
            )
            raise SearchIndexError(msg)
        except socket.error as e:
            assert conn
            err = 'Could not connect to Solr using {0}: {1}'.format(
                conn.url, str(e))
            log.error(err)
            raise SearchIndexError(err)

    def commit(self) -> None:
        try:
            conn = make_connection()
            conn.commit(waitSearcher=False)
        except Exception as e:
            log.exception(e)
            raise SearchIndexError(e)

    def delete(self, reference: str, site_id: str) -> None:
        conn = make_connection()
        query = "+%s:%s AND +(id:\"%s\" OR name:\"%s\") AND +site_id:\"%s\"" % \
                (TYPE_FIELD, PACKAGE_TYPE, reference, reference, site_id)
        try:
            commit = config.get('ckan.search.solr_commit')
            conn.delete(q=query, commit=commit)
        except Exception as e:
            log.exception(e)
            raise SearchIndexError(e)

    def clear(self, site_id: str) -> None:
        conn = make_connection()
        query = "+site_id:\"%s\"" % site_id
        try:
            conn.delete(q=query)
            conn.commit()
        except socket.error as e:
            err = 'Could not connect to SOLR %r: %r' % (conn.url, e)
            log.error(err)
            raise SearchIndexError(err)
        except pysolr.SolrError as e:
            err = 'SOLR %r exception: %r' % (conn.url, e)
            log.error(err)
            raise SearchIndexError(err)

    def search(self, params: dict[str, Any]) -> SearchResponse:
        query = dict(params)

        rows_to_return = int(query.get('rows', 10))
        if rows_to_return > 0:
            # #1683 Work around problem of last result being out of order
            #       in SOLR 1.4
            query['rows'] = rows_to_return + 1
        else:
            query['rows'] = rows_to_return

        conn = make_connection(decode_dates=False)
        log.debug('Package query: %r', query)
        try:
            solr_response = conn.search(**query)
        except pysolr.SolrError as e:
            # Error with the sort parameter.  You see slightly different
            # error messages depending on whether the SOLR JSON comes back
            # or Jetty gets in the way converting it to HTML - not sure why
            #
            if e.args and isinstance(e.args[0], str):
                if "Can't determine a Sort Order" in e.args[0] or \
                        "Can't determine Sort Order" in e.args[0] or \
                        'Unknown sort order' in e.args[0]:
                    raise SearchQueryError('Invalid "sort" parameter')

                if "Failed to connect to server" in e.args[0] or \
                        "Connection to server" in e.args[0]:
                    log.warning(
                        "Connection Error: Failed to connect to Solr server.")
                    raise SearchConnectionError(
                        "Solr returned an error while searching.")

            raise SearchError(
                'SOLR returned an error running query: %r Error: %r' %
                (query, e))

        # #1683 Filter out the last row that is sometimes out of order
        docs = cast("list[dict[str, Any]]", solr_response.docs)
        docs = docs[:rows_to_return]

        facets: dict[str, dict[str, int]] = {}
        facet_fields = solr_response.facets.get('facet_fields', {})
        for field, values in facet_fields.items():
            facets[field] = dict(zip(values[0::2], values[1::2]))

        return SearchResponse(
            count=solr_response.hits, docs=docs, facets=facets)

    def get_by_reference(
            self, reference: str, site_id: str) -> Optional[dict[str, Any]]:
        query = {
            'rows': 1,
            'q': 'name:"%s" OR id:"%s"' % (reference, reference),
            'wt': 'json',
            'fq': '+site_id:"%s" ' % site_id + '+entity_type:package'}

        conn = make_connection(decode_dates=False)
        log.debug('Package query: %r', query)
        try:
            solr_response = conn.search(**query)
        except pysolr.SolrError as e:
            raise SearchError(
                'SOLR returned an error running query: %r Error: %r' %
                (query, e))

        if solr_response.hits == 0:
            return None
        return cast("list[dict[str, Any]]", solr_response.docs)[0]

    def get_all_entity_ids(
            self, site_id: str, max_results: int = 1000) -> list[str]:
        query = "*:*"
        fq = "+site_id:\"%s\" " % site_id
        fq += "+state:active "

        conn = make_connection()
        data = conn.search(query, fq=fq, rows=max_results, fl='id')
        return [r.get('id') for r in data.docs]
