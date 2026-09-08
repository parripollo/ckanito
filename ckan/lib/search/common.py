# encoding: utf-8
from __future__ import annotations

import logging
from typing import Any

from ckan.common import config  # type: ignore  # re-exported for compatibility

log = logging.getLogger(__name__)


class SearchIndexError(Exception):
    pass


class SearchError(Exception):
    pass


class SearchQueryError(SearchError):
    pass


class SearchConnectionError(Exception):
    pass


# Kept for extensions written against CKAN
SolrConnectionError = SearchConnectionError


def is_available() -> bool:
    """
    Return true if we can successfully connect to the search backend.
    """
    from ckan.lib.search.backends import get_backend
    try:
        return get_backend().is_available()
    except Exception as e:
        log.exception(e)
        return False


def make_connection(decode_dates: bool = True) -> Any:
    """
    Return a raw connection to the Solr server.

    Only meaningful with the ``solr`` backend. It is kept so that
    extensions that reach into Solr directly keep importing; new code
    should go through :func:`ckan.lib.search.backends.get_backend`.
    """
    from ckan.lib.search.backends.solr import make_connection as _connect
    return _connect(decode_dates)
