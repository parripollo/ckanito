# encoding: utf-8
"""Contract that every search backend has to implement.

CKAN core never talks to a search engine directly. Everything goes through
:func:`ckan.lib.search.backends.get_backend`, which returns an instance of
a :class:`SearchBackend` subclass selected with the ``ckan.search.backend``
config option.

Two data shapes make up the contract and both are kept exactly as CKAN has
always used them, so that plugins implementing
``IPackageController.before_dataset_index`` / ``before_dataset_search`` keep
working unchanged:

* the *document*: the flat dict built by
  :meth:`ckan.lib.search.index.PackageSearchIndex.index_package` (``id``,
  ``name``, ``title``, ``text``, ``extras_*``, ``vocab_*``, ``res_*``,
  ``permission_labels``, ``data_dict``, ``validated_data_dict``,
  ``site_id``, ``index_id``, ...);
* the *query params*: the dict built by
  :meth:`ckan.lib.search.query.PackageSearchQuery.run` (``q``, ``fq`` as a
  list of clauses, ``sort``, ``rows``, ``start``, ``fl``, ``facet.field``,
  ``facet.limit``, ``facet.mincount``, ``qf``, ``defType``, ``mm``, ``tie``,
  ``df``, ...). The query syntax of ``q``, ``fq`` and ``sort`` is the Lucene
  subset documented for the ``package_search`` action.

Backends raise the exceptions defined in :mod:`ckan.lib.search.common`:
``SearchIndexError`` for indexing problems, ``SearchQueryError`` for
invalid queries, ``SearchConnectionError`` when the engine cannot be
reached and ``SearchError`` for anything else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SearchResponse:
    """Result of :meth:`SearchBackend.search`.

    ``count`` is the total number of matching documents regardless of
    pagination, ``docs`` the page of documents restricted to the requested
    ``fl`` fields and ``facets`` a mapping ``{field: {value: count}}``.
    """
    count: int
    docs: list[dict[str, Any]]
    facets: dict[str, dict[str, int]] = field(default_factory=dict)


class SearchBackend:
    """Base class for search backends.

    Instances are cheap and stateless: CKAN creates one whenever it needs
    it and reads the configuration on every call, so config changes (for
    instance in tests) are picked up immediately.
    """

    #: short name used in ``ckan.search.backend``
    name: str = ""

    def is_available(self) -> bool:
        """Return True if the engine can be reached and queried."""
        raise NotImplementedError

    def check_schema(self, schema_file: Optional[str] = None) -> bool:
        """Verify that the engine schema matches this CKAN version.

        Returns False when the check does not apply (engine down, backend
        without a versioned schema) and raises ``SearchError`` when the
        schema is present but not supported. ``schema_file`` lets tests
        point to an alternative schema definition.
        """
        return True

    def index(self, doc: dict[str, Any], defer_commit: bool = False) -> None:
        """Add or replace ``doc`` (keyed by ``index_id``) in the index."""
        raise NotImplementedError

    def delete(self, reference: str, site_id: str) -> None:
        """Remove the dataset with id or name ``reference`` from the index."""
        raise NotImplementedError

    def commit(self) -> None:
        """Make deferred index changes visible to searches."""
        raise NotImplementedError

    def clear(self, site_id: str) -> None:
        """Remove every document belonging to ``site_id``."""
        raise NotImplementedError

    def search(self, params: dict[str, Any]) -> SearchResponse:
        """Run a dataset search with the given query params."""
        raise NotImplementedError

    def get_by_reference(
            self, reference: str, site_id: str) -> Optional[dict[str, Any]]:
        """Return the indexed document whose id or name is ``reference``."""
        raise NotImplementedError

    def get_all_entity_ids(
            self, site_id: str, max_results: int = 1000) -> list[str]:
        """Return the ids of the active datasets indexed for ``site_id``."""
        raise NotImplementedError
