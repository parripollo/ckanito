Search backends are now pluggable and CKAN no longer needs Solr. The
``SearchBackend`` contract (``ckan.lib.search.backends.base``) is
implemented by a new PostgreSQL backend (``ckan.search.backend = postgres``,
the default) that indexes datasets in the ``package_search_index`` table of
the CKAN database and answers the Lucene query subset used by
``package_search`` with full text search, facets, ranges and sorting. The
former Solr code lives in ``ckan.lib.search.backends.solr`` as a reference.
Run ``ckan db upgrade`` and ``ckan search-index rebuild`` after upgrading.
``SolrConnectionError`` is kept as an alias of ``SearchConnectionError``.
