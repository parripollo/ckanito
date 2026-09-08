===============
Background jobs
===============

What extensions relied on
=========================

CKAN exposed RQ objects to extensions through ``toolkit.enqueue_job``,
``toolkit.get_job_queue`` and ``toolkit.job_from_id``, and the
``background-tasks`` documentation told authors to reach for raw
``rq.Queue`` and ``rq.job.Job`` for anything beyond a plain enqueue. In
the CKAN tree the datastore plugin does exactly that: it calls
``get_job_queue().enqueue_in(delay, func, ..., job_id=deterministic_id)``,
cancels with ``job_from_id(id).delete()`` and its tests read
``queue.scheduled_job_registry.get_job_ids()`` and ``queue.fetch_job(id)``.

CKANito therefore keeps ``ckan.lib.jobs`` as the public API with the same
function names, and gives it its own ``Queue`` and ``Job`` classes that
expose that surface: ``Queue.name`` (prefixed), ``jobs``, ``job_ids``,
``count``, ``is_empty()``, ``empty()``, ``delete()``, ``enqueue_call()``,
``enqueue()``, ``enqueue_at()``, ``enqueue_in()``, ``fetch_job()``,
``scheduled_job_registry.get_job_ids()``; ``Job.id``, ``origin``, ``meta``
(the title), ``args``, ``kwargs``, ``timeout``, ``created_at``,
``func_name``, ``save()``, ``delete()``, ``perform()``, equality by id.
``ckanext/datastore`` needed no change at all.

Two things RQ offered inside a running job are provided too:
``ckan.lib.jobs.get_current_job()`` and ``ckan.lib.jobs.JobTimeoutException``
(see timeouts below). ``ckanext-xloader`` uses both and works with a two
line import change.

The contract
============

``ckan.lib.jobqueue.base.JobBackend`` stores jobs and hands them out; it
knows nothing about running them:

``enqueue``, ``fetch``, ``save_meta``, ``delete``, ``list_jobs``,
``scheduled_job_ids``, ``queues``, ``empty``, ``dequeue``, ``finish``,
``fail``, ``fail_stale``, ``clear_all``.

Queue names reaching a backend are always the full, site-prefixed names
(``ckan:<site_id>:default``), so several CKAN sites can share one
database, as they could share one Redis.

The PostgreSQL implementation
=============================

Table ``background_job``: ``id``, ``queue``, ``func`` (``module:qualname``
of a module level function), ``args`` and ``kwargs`` (pickle, as RQ did:
the datastore passes ``datetime`` values), ``meta`` (JSONB, the title),
``timeout``, ``status`` (``queued``, ``started``, ``failed``),
``scheduled_at``, ``created_at``, ``started_at``, ``ended_at``,
``worker``, ``error``.

* ``dequeue`` is one ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP
  LOCKED) RETURNING``: any number of workers can poll the same queues
  without taking the same job twice, and a job is claimed and marked
  ``started`` atomically. The leftmost queue given to the worker wins,
  as with RQ.
* Delayed jobs are rows with ``scheduled_at`` in the future; ``dequeue``
  and ``Queue.jobs`` ignore them until then. No separate scheduler
  process is needed (``--no-scheduler`` is accepted and does nothing).
* A deterministic ``job_id`` replaces an existing job with the same id
  (this is how the datastore debounces its "recalculate record count"
  job).
* Finished jobs are deleted, as RQ discarded them. Failed jobs stay with
  ``status = failed``, ``ended_at`` and the traceback in ``error``, and do
  not appear in ``ckan jobs list``.
* ``fail_stale`` marks as failed the jobs that have been ``started`` for
  longer than their timeout plus a minute: their worker died. Workers run
  it while idle.

The worker
==========

``ckan.lib.jobs.Worker`` polls its queues (one second between empty
polls) and runs each job in a forked child process, as the RQ worker did:

1. before forking: ``Session.remove()`` and ``engine.dispose()`` so no
   connection is shared with the child, then ``IForkObserver.before_fork``
   for plugins (the datastore disposes its own engines there);
2. the child loads the environment, sets the job as
   ``get_current_job()``, arms a soft timeout, runs the function, records
   ``finish`` or ``fail`` (with the traceback) and exits with
   ``os._exit``;
3. the parent waits. If the child does not exit within ``timeout`` plus a
   grace period it is killed with ``SIGKILL`` and the job is marked
   failed; a child that exits abnormally without recording anything is
   marked failed as well.

Timeouts are per job (``rq_kwargs={"timeout": ...}``, default
``ckan.jobs.timeout``; ``-1`` or ``0`` disables them). The soft timeout is
a ``SIGALRM`` in the child raising ``JobTimeoutException`` inside the job
function, so a job can clean up (xloader marks its own job record as
errored); the hard kill follows ``TIMEOUT_GRACE`` seconds later if the job
ignores it.

``work(burst=True)`` exits when the queues are empty; ``max_idle_time``
exits after that many idle seconds. The log messages
("Worker ... has started on queue(s)", "starts job", "has finished job",
"has stopped") are the ones the CKAN tests count.

In tests, the ``with_test_worker`` fixture replaces ``Worker.execute_job``
(the forking path) with ``Worker.perform_job`` (in process), exactly as
RQ's ``SimpleWorker`` was substituted before.

Differences from RQ
===================

* No job results are stored (neither did CKAN's setup).
* Failed jobs are kept in the table instead of a Redis registry; there
  is no automatic retry.
* ``Queue.delete()`` is ``empty()``: queues only exist through their jobs.
* Job functions must be module level functions (they are stored by
  dotted path); RQ had the same practical restriction because of pickling.
* Extensions importing ``rq`` directly have to switch to
  ``ckan.lib.jobs`` (``get_current_job``, ``JobTimeoutException``).
