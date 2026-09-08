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
- `ckan/lib/jobqueue/__init__.py`, `base.py`, `postgres.py` - background
  jobs contract (`JobBackend`, `Job`), registry (`ckan.jobs.backend`) and
  the PostgreSQL backend on the `background_job` table (claims with
  `FOR UPDATE SKIP LOCKED`, delayed jobs by `scheduled_at`).
- `ckan/lib/kvstore.py` - key/value store on the `kv_store` table
  (replacement for the raw Redis connection extensions used).
- `ckan/model/background_job.py` - `background_job`, `session_store` and
  `kv_store` tables; migration
  `111_d2b4f6a8c0e1_create_background_job_session_kv_tables.py`.
- `ckan/tests/lib/test_jobqueue.py`, `ckan/tests/lib/test_kvstore.py`.
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

- `ckan/lib/redis.py`; `rq` and `redis` from the requirements; Redis
  services from the workflows, docker-compose files and the cookiecutter;
  `ckan.redis.url` option and `CKAN_REDIS_URL` env var; the Redis ping at
  startup.

## Renamed public names (no aliases kept)

Nothing in the code is named after Solr any more. Extensions written
against CKAN may need these one-line changes:

| CKAN name | CKANito name |
|---|---|
| `ckan.lib.search.SolrConnectionError` | `ckan.lib.search.SearchConnectionError` |
| `ckan.lib.search.common.make_connection` | removed (no raw engine connection; use `ckan.lib.search.backends.get_backend()`) |
| `ckan.lib.search.check_solr_schema_version` | `ckan.lib.search.check_schema` |
| `ckan.lib.search.convert_legacy_parameters_to_solr` | `ckan.lib.search.convert_legacy_parameters` |
| `ckan.lib.search.query.solr_literal` | `ckan.lib.search.query.search_literal` |
| `ckan.lib.search.query.VALID_SOLR_PARAMETERS` | `ckan.lib.search.query.VALID_SEARCH_PARAMETERS` |
| `ckan.lib.search.index.SOLR_FIELDS` | `ckan.lib.search.index.INDEX_FIELDS` |
| `ckanext.tracking.cli.tracking.update_tracking_solr` | `update_tracking_index` |
| `ckan.lib.redis.connect_to_redis` | `ckan.lib.kvstore` (different API) |
| `reset_redis` / `clean_redis` fixtures | `reset_kvstore` / `clean_kvstore` (old names still work) |

## Upstream files modified

| file | what changed | why |
|---|---|---|
| `ckan/lib/search/common.py` | pysolr code removed; only the exceptions and `is_available()` remain. `SolrConnectionError` renamed `SearchConnectionError`, `make_connection` removed. | Solr calls moved to the backend, then Solr removed. |
| `ckan/lib/search/index.py` | `clear_index`, `index_package` (tail), `commit`, `delete_package` call `get_backend()`. | Same. |
| `ckan/lib/search/query.py` | `get_all_entity_ids`, `get_index`, `run` (tail) call `get_backend()`; Solr local params (`{!...}`) are always rejected, the allow-list and its pyparsing helper are gone. | Same. |
| `ckan/lib/search/__init__.py` | `check_schema()` delegates to the backend (was `check_solr_schema_version`). | Same. |
| `ckan/views/api.py`, `ckan/views/dataset.py`, `ckan/logic/action/get.py`, `ckan/logic/schema/__init__.py`, `ckan/lib/dictization/model_dictize.py`, `ckan/plugins/interfaces.py`, `ckan/model/meta.py`, `ckan/model/package_relationship.py`, `ckanext/tracking/` | renamed identifiers (see the table above) and comments/docstrings that described Solr behaviour. | No Solr vocabulary left in the code. |
| `ckan/config/environment.py` | calls `search.check_schema()`; `CKAN_SOLR_*` env var mapping removed. | No Solr. |
| `test-core.ini`, `test-core-ci.ini`, `.gitignore`, `pyproject.toml`, `setup.py` | Solr entries removed. | No Solr. |
| `ckan/plugins/interfaces.py` | `ISearchBackend` interface appended. | Lets extensions register backends. |
| `ckan/config/config_declaration.yaml` | `ckan.search.backend` (default `postgres`) and `ckan.search.postgres.text_config` added before `solr_url`. | Backend selection. |
| `ckan/model/__init__.py` | imports `package_search_index_table` so `create_all` / `drop_all` handle it. | Index table lives in the CKAN database. |
| `setup.cfg` | `ckanito` entry in `ckan.click_command`. | Registers `ckan ckanito`. |
| `ckan/lib/jobs.py` | rewritten on top of `ckan.lib.jobqueue`: same public functions, plus own `Queue`/`Job` objects exposing what core, datastore and tests used from RQ (`enqueue_call`, `enqueue_in`, `fetch_job`, `scheduled_job_registry`, `job.meta`, `job.delete()`...); `Worker` polls the backend and forks a child per job, enforcing the timeout with a kill. | No Redis / RQ. `ckanext/datastore` needs no change. |
| `ckan/config/middleware/common_middleware.py`, `flask_app.py` | `CKANRedisSessionInterface` replaced by `CKANPostgresSessionInterface` (`SESSION_TYPE = postgres`, table `session_store`). | Server side sessions without Redis. |
| `ckan/plugins/interfaces.py` | `IJobBackend` appended. | Lets extensions register job backends. |
| `ckan/config/config_declaration.yaml` | `ckan.jobs.backend` added; `ckan.redis.url` removed; `SESSION_TYPE` docs. | Same. |
| `ckan/types/__init__.py` | `FixtureResetKVStore` (old name kept as alias). | Fixture typing. |
| `ckan/tests/pytest_ckan/fixtures.py`, `ckan/tests/helpers.py` | `reset_queues`/`clean_queues` use the job backend; `reset_redis`/`clean_redis` become `reset_kvstore`/`clean_kvstore` (old names kept as aliases); `with_test_worker` patches `Worker.execute_job`; `RQTestBase.all_jobs` uses `get_all_queues`. | Test infrastructure without Redis; third party extension tests keep working. |
| `ckan/tests/lib/test_jobs.py`, `ckan/tests/pytest_ckan/test_fixtures.py`, `ckan/tests/config/test_sessions.py` | RQ/Redis specifics replaced (own `Job` class, foreign queue via `jobs.Queue`, kvstore fixtures, `postgres` session type). | Same. |
| `ckan/tests/lib/search/test_index.py`, `test_search.py`, `test_query.py`, `ckan/tests/cli/test_db.py`, `ckan/tests/controllers/test_api.py`, `test_package.py` | tests that reached into pysolr now go through the backend interface or a stub backend; Solr-only tests (schema XML version, local params) removed; one test made deterministic; a malformed query now yields a 400 instead of a generic error page. | Tests must not depend on a particular engine. |
