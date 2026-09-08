# encoding: utf-8
from __future__ import annotations

import logging
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

