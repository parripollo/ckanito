# encoding: utf-8
from __future__ import annotations

import logging
from typing import Any

from ckan.common import config  # type: ignore # noqa: F401 (re-exported)

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
    Kept so that extensions written against CKAN keep importing. There is
    no Solr in CKANito: go through
    :func:`ckan.lib.search.backends.get_backend` instead.
    """
    raise SearchError(
        "make_connection() is not available: CKANito has no Solr. Use "
        "ckan.lib.search.backends.get_backend() instead.")
