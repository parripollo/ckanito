Search backends are now pluggable. All Solr code lives in
``ckan.lib.search.backends.solr`` behind the ``SearchBackend`` contract
(``ckan.lib.search.backends.base``); the backend in use is selected with the
new ``ckan.search.backend`` config option and extensions can register their
own through the ``ISearchBackend`` plugin interface.
``SolrConnectionError`` is kept as an alias of the new
``SearchConnectionError``.
