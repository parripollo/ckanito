# CKANito

CKANito is CKAN running on PostgreSQL only: no Solr, no Redis. Everything
those services did sits behind an interface with a PostgreSQL
implementation, so that anyone can plug another engine back in. See
`PLAN-CKANITO.md` for the roadmap.

CKANito tracks `ckan/ckan` `master` and merges it regularly. To keep those
merges cheap, new code lives in new files and upstream files are touched
as little as possible. This file is the list of upstream files CKANito
modifies, so that merge conflicts can be resolved quickly.

## New files (no conflict risk)

- `ckan/lib/search/backends/__init__.py` - backend registry
  (`get_backend`, `ckan.search.backend`).
- `ckan/lib/search/backends/base.py` - `SearchBackend` contract and
  `SearchResponse`.
- `ckan/lib/search/backends/solr.py` - the Solr code that used to be
  spread over `ckan/lib/search/{common,index,query,__init__}.py`.
- `ckan/tests/lib/search/test_backends.py`.

## Upstream files modified

| file | what changed | why |
|---|---|---|
| `ckan/lib/search/common.py` | pysolr code removed; only exceptions, `is_available()` and a compat `make_connection()` remain. `SearchConnectionError` added, `SolrConnectionError` kept as alias. | Solr calls moved to the backend. |
| `ckan/lib/search/index.py` | `clear_index`, `index_package` (tail), `commit`, `delete_package` call `get_backend()`. | Same. |
| `ckan/lib/search/query.py` | `get_all_entity_ids`, `get_index`, `run` (tail) call `get_backend()`; the Solr `rows+1` workaround and error translation moved to the Solr backend. | Same. |
| `ckan/lib/search/__init__.py` | `check_solr_schema_version` is now an alias of `check_schema()` which delegates to the backend; Solr constants resolved lazily via `__getattr__`. | Same. |
| `ckan/plugins/interfaces.py` | `ISearchBackend` interface appended. | Lets extensions register backends. |
| `ckan/config/config_declaration.yaml` | `ckan.search.backend` option added before `solr_url`. | Backend selection. |
