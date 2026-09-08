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
- `ckan/lib/search/backends/postgres/__init__.py` - the PostgreSQL
  backend (`PostgresSearchBackend`): indexing into `package_search_index`,
  search, facets, lookups.
- `ckan/lib/search/backends/postgres/lucene.py` - parser for the Lucene
  query subset accepted by `package_search` (`q`, `fq`).
- `ckan/lib/search/backends/postgres/compiler.py` - AST to SQL, field
  typing (typed columns vs JSONB), free text, ranges and Solr date math,
  sort, facets.
- `ckan/model/package_search_index.py` - the index table.
- `ckan/migration/versions/110_c1a3e5f7b9d2_create_package_search_index_table.py`
  - creates the table and the `ckan_english` text search configuration
  (English stemming, no stop words, like the Solr schema).
- `ckan/cli/ckanito.py` - `ckan ckanito seed-demo`.
- `ckan/tests/lib/search/test_backends.py`,
  `ckan/tests/lib/search/postgres/test_lucene.py`.

## Upstream files and directories removed

- `ckan/lib/search/backends/solr.py` (existed only during the refactor),
  `ckan/config/solr/`, `ckanext/multilingual/solr/`, `bin/solr_init/`.
- `pysolr` from `requirements.in` / `requirements.txt`; Solr services from
  the GitHub workflows, `test-infrastructure/docker-compose.yml`,
  `.devcontainer/docker-compose.yml` and the extension cookiecutter.
- Config options `solr_url`, `solr_user`, `solr_password`, `solr_timeout`,
  `ckan.search.solr_commit`, `ckan.search.solr_allowed_query_parsers`
  (unknown options in an existing `ckan.ini` are ignored with a warning).
  Env var `CKAN_SOLR_URL` is no longer mapped.

## Upstream files modified

| file | what changed | why |
|---|---|---|
| `ckan/lib/search/common.py` | pysolr code removed; only exceptions and `is_available()` remain. `make_connection()` raises a clear `SearchError`. `SearchConnectionError` added, `SolrConnectionError` kept as alias. | Solr calls moved to the backend, then Solr removed. |
| `ckan/lib/search/index.py` | `clear_index`, `index_package` (tail), `commit`, `delete_package` call `get_backend()`. | Same. |
| `ckan/lib/search/query.py` | `get_all_entity_ids`, `get_index`, `run` (tail) call `get_backend()`; Solr local params (`{!...}`) are always rejected, the allow-list and its pyparsing helper are gone. | Same. |
| `ckan/lib/search/__init__.py` | `check_schema()` delegates to the backend; `check_solr_schema_version` kept as an alias. | Same. |
| `ckan/config/environment.py` | calls `search.check_schema()`; `CKAN_SOLR_*` env var mapping removed. | No Solr. |
| `test-core.ini`, `test-core-ci.ini`, `.gitignore`, `pyproject.toml`, `setup.py` | Solr entries removed. | No Solr. |
| `ckan/plugins/interfaces.py` | `ISearchBackend` interface appended. | Lets extensions register backends. |
| `ckan/config/config_declaration.yaml` | `ckan.search.backend` (default `postgres`) and `ckan.search.postgres.text_config` added before `solr_url`. | Backend selection. |
| `ckan/model/__init__.py` | imports `package_search_index_table` so `create_all` / `drop_all` handle it. | Index table lives in the CKAN database. |
| `setup.cfg` | `ckanito` entry in `ckan.click_command`. | Registers `ckan ckanito`. |
| `ckan/tests/lib/search/test_index.py`, `test_search.py`, `test_query.py`, `ckan/tests/cli/test_db.py`, `ckan/tests/controllers/test_api.py`, `test_package.py` | tests that reached into pysolr now go through the backend interface or a stub backend; Solr-only tests (schema XML version, local params) removed; one test made deterministic; a malformed query now yields a 400 instead of a generic error page. | Tests must not depend on a particular engine. |
