Background jobs, server side sessions and the key/value store no longer
need Redis: CKAN needs nothing but PostgreSQL. Jobs are stored by a
pluggable ``JobBackend`` (``ckan.jobs.backend``, default ``postgres``,
table ``background_job``) and run by ``ckan jobs worker`` processes that
poll it and fork a child per job; ``ckan.lib.jobs`` keeps its public API
and exposes its own ``Queue`` and ``Job`` objects instead of RQ's.
``SESSION_TYPE = postgres`` stores sessions in the ``session_store``
table. ``ckan.lib.kvstore`` replaces ``ckan.lib.redis`` for extensions.
``ckan.redis.url``, ``rq`` and ``redis`` are gone. Run ``ckan db
upgrade`` after upgrading.
Inside a job, ``ckan.lib.jobs.get_current_job()`` returns the running job
and the timeout is first signalled with ``ckan.lib.jobs.JobTimeoutException``
so the job can clean up before it is killed.
