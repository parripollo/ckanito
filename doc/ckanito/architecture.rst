============
Architecture
============

Goals
=====

* **One service.** A CKAN site is a Python process and a PostgreSQL
  database, the way a Django site is. No search engine, no message
  broker, no cache server to install, monitor or upgrade.
* **Nothing hardcoded.** The original mistake of CKAN was not that it
  used Solr and Redis but that it talked to them from all over the code
  base. CKANito puts each of those concerns behind a small contract and
  selects the implementation by configuration, so that Solr, Redis, RQ,
  Elasticsearch or anything else can be plugged back in by an extension
  without touching core.
* **Every test passes.** CKAN's test suite (about 3500 tests), ``ruff``,
  ``pyright``, the Sphinx documentation and the Cypress front end tests
  keep running in GitHub Actions. Tests that asserted Solr or RQ
  internals were rewritten against the contracts; nothing was skipped.
* **Cheap merges from upstream.** CKANito tracks ``ckan/ckan`` ``master``
  and merges it regularly. New code lives in new files; upstream files
  are edited as little as possible and every edit is listed in
  ``CKANITO.md``.

What changed, at a glance
=========================

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Concern
     - CKAN
     - CKANito
   * - Dataset search
     - Solr via ``pysolr``, spread over ``ckan/lib/search``
     - ``SearchBackend`` contract; PostgreSQL implementation on the
       ``package_search_index`` table (JSONB document, weighted
       ``tsvector``)
   * - Background jobs
     - RQ on Redis, ``rq`` types leaking into the plugin API
     - ``JobBackend`` contract; PostgreSQL implementation on the
       ``background_job`` table; own ``Queue`` / ``Job`` objects with
       the RQ-compatible surface CKAN and extensions used
   * - Server side sessions
     - Flask-Session with Redis
     - Flask-Session with a PostgreSQL interface (``session_store``
       table); cookie sessions stay the default
   * - Ad hoc shared state for extensions
     - raw Redis connection (``ckan.lib.redis``)
     - ``ckan.lib.kvstore`` on the ``kv_store`` table
   * - Configuration
     - ``solr_url``, ``ckan.redis.url``...
     - ``ckan.search.backend``, ``ckan.search.postgres.text_config``,
       ``ckan.jobs.backend``, ``SESSION_TYPE = postgres``
   * - Plugin interfaces
     - none for backends
     - ``ISearchBackend``, ``IJobBackend`` (register named backends)

Layout of the new code
======================

::

   ckan/lib/search/backends/
       __init__.py        registry: get_backend(), ckan.search.backend
       base.py            SearchBackend contract, SearchResponse
       postgres/
           __init__.py    PostgresSearchBackend (index, search, facets)
           lucene.py      tokenizer + parser of the Lucene subset -> AST
           compiler.py    AST -> SQL, field typing, sort, facets, date math
   ckan/lib/jobqueue/
       __init__.py        registry: get_backend(), ckan.jobs.backend
       base.py            JobBackend contract, Job
       postgres.py        PostgresJobBackend (background_job table)
   ckan/lib/jobs.py       public jobs API, Queue, Worker (rewritten)
   ckan/lib/kvstore.py    key/value store
   ckan/config/middleware/common_middleware.py
                          CKANPostgresSessionInterface
   ckan/model/package_search_index.py, ckan/model/background_job.py
                          the four new tables
   ckan/migration/versions/110_*, 111_*
                          their migrations (and the ckan_english text
                          search configuration)
   ckan/cli/ckanito.py    ckan ckanito seed-demo

The public modules that CKAN core, extensions and templates import
(``ckan.lib.search``, ``ckan.lib.jobs``, ``ckan.plugins.toolkit``) keep
their names and signatures, with the exceptions listed in
:doc:`upstream-and-migration`.

How a backend is selected
=========================

Both registries work the same way. The config option (``ckan.search.backend``
or ``ckan.jobs.backend``) holds one of:

* a built-in name (``postgres``);
* a name registered by a plugin implementing ``ISearchBackend`` /
  ``IJobBackend`` (``register_search_backends()`` /
  ``register_job_backends()`` return ``{name: class}``);
* a dotted path ``package.module:ClassName``.

Backends are instantiated on every call and hold no state, so a config
change (for instance in a test) takes effect immediately and there is
nothing to reset between requests or worker jobs.

Verification
============

Each phase of the work ended with the complete CKAN test suite green on
PostgreSQL 18 (CI uses PostgreSQL 14), plus ``ruff``, ``pyright`` with
CKAN's strict settings, and a warning free Sphinx build. New tests cover
what CKAN never exercised because Solr and RQ hid it: the query parser
(62 cases), the job backend contract, the forking worker with soft and
hard timeouts, crashing children, and the key/value store.
