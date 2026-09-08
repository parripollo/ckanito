#!/usr/bin/env python
# encoding: utf-8

u'''
Asynchronous background jobs.

Note that most job management functions are not available from this
module but via the various ``job_*`` API functions.

Jobs are stored by a :class:`ckan.lib.jobqueue.base.JobBackend` (the
default one keeps them in the ``background_job`` table of the CKAN
database) and run by :class:`Worker` processes started with
``ckan jobs worker``. No external queue service is needed.

Internally, queue names are prefixed with a string containing the CKAN
site ID to avoid collisions when the same database is used for multiple
CKAN instances. The functions of this module expect unprefixed queue
names (e.g. ``'default'``) unless noted otherwise. The raw queue objects
(e.g. a queue returned by ``get_queue``) use the full, prefixed names.
Use the functions ``add_queue_name_prefix`` and
``remove_queue_name_prefix`` to manage queue name prefixes.

.. versionadded:: 2.7
'''
from __future__ import annotations

import datetime
import logging
import os
import socket
import time
import traceback
from typing import Any, Callable, Iterable, Optional, Union

from ckan.common import config
from ckan.config.environment import load_environment
from ckan.lib.jobqueue import get_backend
from ckan.lib.jobqueue.base import Job, JobBackend, callable_path
from ckan.model import meta
import ckan.plugins as plugins

__all__ = [
    "DEFAULT_QUEUE_NAME", "DEFAULT_JOB_LIST_LIMIT", "Job", "Queue", "Worker",
    "add_queue_name_prefix", "remove_queue_name_prefix", "get_all_queues",
    "get_queue", "enqueue", "job_from_id", "dictize_job", "test_job",
]

log = logging.getLogger(__name__)

DEFAULT_QUEUE_NAME = u'default'
DEFAULT_JOB_LIST_LIMIT = 200

#: seconds an idle worker sleeps before looking for jobs again
POLL_INTERVAL = 1.0

#: seconds between checks for jobs whose worker died
STALE_CHECK_INTERVAL = 60.0


def _get_queue_name_prefix() -> str:
    u'''
    Get the queue name prefix.
    '''
    # This must be done at runtime since we need a loaded config
    return u'ckan:{}:'.format(config[u'ckan.site_id'])


def add_queue_name_prefix(name: str) -> str:
    u'''
    Prefix a queue name.

    .. seealso:: :py:func:`remove_queue_name_prefix`
    '''
    return _get_queue_name_prefix() + name


def remove_queue_name_prefix(name: str) -> str:
    u'''
    Remove a queue name's prefix.

    :raises ValueError: if the given name is not prefixed.

    .. seealso:: :py:func:`add_queue_name_prefix`
    '''
    prefix = _get_queue_name_prefix()
    if not name.startswith(prefix):
        raise ValueError(u'Queue name "{}" is not prefixed.'.format(name))
    return name[len(prefix):]


def _default_timeout() -> Optional[int]:
    timeout = config.get(u'ckan.jobs.timeout')
    return int(timeout) if timeout is not None else None


def _utc(when: datetime.datetime) -> datetime.datetime:
    if when.tzinfo is not None:
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return when


class ScheduledJobRegistry:
    u'''The jobs of a queue whose time has not come yet.'''

    def __init__(self, queue: "Queue"):
        self.queue = queue

    def get_job_ids(self) -> list[str]:
        return self.queue.backend.scheduled_job_ids(self.queue.name)


class Queue:
    u'''
    A named job queue.

    ``name`` is the full, prefixed queue name. The methods mirror the
    ones CKAN and its extensions used from ``rq.Queue``.
    '''

    def __init__(self, name: str, backend: Optional[JobBackend] = None):
        self.name = name
        self.backend = backend or get_backend()

    def __repr__(self) -> str:
        return u'Queue({!r})'.format(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Queue) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)

    @property
    def jobs(self) -> list[Job]:
        u'''The queued jobs, in execution order.'''
        return self.backend.list_jobs(self.name)

    @property
    def job_ids(self) -> list[str]:
        return [job.id for job in self.jobs]

    @property
    def count(self) -> int:
        return len(self.job_ids)

    def is_empty(self) -> bool:
        return not self.backend.list_jobs(self.name, limit=1)

    @property
    def scheduled_job_registry(self) -> ScheduledJobRegistry:
        return ScheduledJobRegistry(self)

    def fetch_job(self, job_id: str) -> Optional[Job]:
        u'''The job with that id if it belongs to this queue.'''
        job = self.backend.fetch(job_id)
        if job is not None and job.origin == self.name:
            return job
        return None

    def empty(self) -> int:
        u'''Remove every queued job. Returns how many were removed.'''
        return self.backend.empty(self.name)

    def delete(self) -> None:
        u'''Same as :meth:`empty`: queues only exist through their jobs.'''
        self.empty()

    def enqueue_call(self, func: Callable[..., Any],
                     args: Optional[Iterable[Any]] = None,
                     kwargs: Optional[dict[str, Any]] = None,
                     timeout: Optional[int] = None,
                     job_id: Optional[str] = None,
                     meta: Optional[dict[str, Any]] = None,
                     at: Optional[datetime.datetime] = None,
                     **ignored: Any) -> Job:
        u'''
        Store a job. ``at`` (UTC) delays it; ``job_id`` gives it a
        deterministic id, replacing an existing job with the same id.
        Unknown keyword arguments are ignored for compatibility with the
        RQ signature.
        '''
        if timeout is None:
            timeout = _default_timeout()
        return self.backend.enqueue(
            self.name, callable_path(func), list(args or []),
            dict(kwargs or {}), dict(meta or {}), timeout,
            job_id=job_id, scheduled_at=_utc(at) if at else None)

    def enqueue(self, func: Callable[..., Any], *args: Any,
                **kwargs: Any) -> Job:
        options = {key: kwargs.pop(key) for key in
                   ('timeout', 'job_id', 'meta', 'at') if key in kwargs}
        return self.enqueue_call(func, args, kwargs, **options)

    def enqueue_at(self, when: datetime.datetime, func: Callable[..., Any],
                   *args: Any, **kwargs: Any) -> Job:
        return self.enqueue(func, *args, at=when, **kwargs)

    def enqueue_in(self, delay: datetime.timedelta, func: Callable[..., Any],
                   *args: Any, **kwargs: Any) -> Job:
        when = datetime.datetime.utcnow() + delay
        return self.enqueue(func, *args, at=when, **kwargs)


def get_all_queues() -> list[Queue]:
    u'''
    Return all job queues currently in use.

    :returns: The queues.
    :rtype: List of :class:`Queue` instances

    .. seealso:: :py:func:`get_queue`
    '''
    backend = get_backend()
    return [Queue(name, backend) for name in
            backend.queues(_get_queue_name_prefix())]


def get_queue(name: str = DEFAULT_QUEUE_NAME) -> Queue:
    u'''
    Get a job queue.

    :param string name: The name of the queue. If not given then the
        default queue is returned.

    :returns: The job queue.
    :rtype: :class:`Queue`

    .. seealso:: :py:func:`get_all_queues`
    '''
    return Queue(add_queue_name_prefix(name))


def enqueue(fn: Callable[..., Any],
            args: Optional[Union[tuple[Any], list[Any], None]] = None,
            kwargs: Optional[dict[str, Any]] = None,
            title: Optional[str] = None,
            queue: str = DEFAULT_QUEUE_NAME,
            rq_kwargs: Optional[dict[str, Any]] = None) -> Job:
    u'''
    Enqueue a job to be run in the background.

    :param function fn: Function to be executed in the background. It
        has to be a module level function (it is stored by name).

    :param list args: List of arguments to be passed to the function.
        Pass an empty list if there are no arguments (default).

    :param dict kwargs: Dict of keyword arguments to be passed to the
        function. Pass an empty dict if there are no keyword arguments
        (default).

    :param string title: Optional human-readable title of the job.

    :param string queue: Name of the queue. If not given then the
        default queue is used.

    :param dict rq_kwargs: Dict of extra options, named after the RQ ones
        for compatibility: ``timeout`` (seconds, ``-1`` or ``0`` for no
        timeout), ``job_id`` and ``at`` (a UTC datetime to delay the job).
        Other keys are ignored.

    :returns: The enqueued job.
    :rtype: :class:`ckan.lib.jobqueue.base.Job`
    '''
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    if rq_kwargs is None:
        rq_kwargs = {}
    timeout = rq_kwargs.get(u'timeout', _default_timeout())

    job = get_queue(queue).enqueue_call(
        func=fn, args=args, kwargs=kwargs, timeout=timeout,
        job_id=rq_kwargs.get('job_id'), at=rq_kwargs.get('at'),
        meta={"title": title})
    msg = u'Added background job {}'.format(job.id)
    if title:
        msg = u'{} ("{}")'.format(msg, title)

    log.info('%s to queue "%s"', msg, queue)
    return job


def job_from_id(id: str) -> Job:
    u'''
    Look up an enqueued job by its ID.

    :param string id: The ID of the job.

    :returns: The job.
    :rtype: :class:`ckan.lib.jobqueue.base.Job`

    :raises KeyError: if no job with that ID exists.
    '''
    job = get_backend().fetch(id)
    if job is None:
        raise KeyError(u'There is no job with ID "{}".'.format(id))
    return job


def dictize_job(job: Job) -> dict[str, Any]:
    u'''Convert a job to a dict.

    Includes only the attributes that are relevant to our use case and
    promotes the meta attributes that we use (e.g. ``title``).

    :param job: The job to dictize.

    :returns: The dictized job.
    :rtype: dict
    '''
    assert job.created_at
    return {
        "id": job.id,
        "title": job.meta.get("title"),
        "created": job.created_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "queue": remove_queue_name_prefix(job.origin),
    }


def test_job(*args: Any) -> None:
    u'''Test job.

    A test job for debugging purposes. Prints out any arguments it
    receives. Can be scheduled via ``ckan jobs test``.
    '''
    print(args)


class Worker:
    u'''
    Background jobs worker.

    Polls its queues and runs each job in a forked child process, so
    that a crashing or leaking job cannot take the worker down and so
    that the job's timeout can be enforced with a plain kill.

    Note that starting an instance of this class (via the ``work``
    method) disposes the currently active database engine and the
    associated session before every job. This is necessary to prevent
    their corruption by the forked worker process. Both the engine and
    the session automatically re-initialize afterwards once they are
    used. However, non-committed changes are rolled back and instance
    variables bound to the old session have to be re-fetched from the
    database.
    '''
    def __init__(self,
                 queues: Optional[Union[str, Iterable[str]]] = None,
                 *args: Any,
                 **kwargs: Any) -> None:
        u'''
        Constructor.

        :param queues: The job queue(s) to listen on. Can be a string
            with the name of a single queue or a list of queue names.
            If not given then the default queue is used.
        '''
        if isinstance(queues, str):
            queue_names = [queues]
        else:
            queue_names = list(queues or [DEFAULT_QUEUE_NAME])
        self.queues = [get_queue(name) for name in queue_names]
        self.backend = get_backend()
        self.pid = os.getpid()
        self.key = u'ckan:worker:{}.{}'.format(socket.gethostname(), self.pid)
        self.poll_interval = POLL_INTERVAL

    def queue_names(self) -> list[str]:
        u'''The full (prefixed) names of the queues this worker serves.'''
        return [queue.name for queue in self.queues]

    def work(self, burst: bool = False, max_idle_time: Optional[int] = None,
             with_scheduler: bool = True, **kwargs: Any) -> bool:
        u'''
        Fetch and run jobs until told otherwise.

        :param bool burst: Exit as soon as the queues are empty.
        :param int max_idle_time: Exit after being idle for this many
            seconds.
        :param bool with_scheduler: Accepted for compatibility; delayed
            jobs need no separate scheduler here.

        :returns: Whether any job was performed.
        '''
        self.register_birth()
        performed = False
        idle_since: Optional[float] = None
        last_stale_check = time.monotonic()
        try:
            while True:
                job = self.backend.dequeue(self.queue_names(), self.key)
                if job is None:
                    if burst:
                        break
                    now = time.monotonic()
                    if idle_since is None:
                        idle_since = now
                    if max_idle_time is not None and \
                            now - idle_since >= max_idle_time:
                        break
                    if now - last_stale_check >= STALE_CHECK_INTERVAL:
                        last_stale_check = now
                        for stale in self.backend.fail_stale(
                                datetime.datetime.utcnow()):
                            log.warning('Job %s was started but its worker '
                                        'is gone, marked as failed', stale)
                    time.sleep(self.poll_interval)
                    continue
                idle_since = None
                performed = True
                queue = remove_queue_name_prefix(job.origin)
                description = self._describe(job)
                log.info('Worker %s starts job %s from queue "%s"',
                         self.key, description, queue)
                self.execute_job(job)
                log.info('Worker %s has finished job %s from queue "%s"',
                         self.key, description, queue)
        finally:
            self.register_death()
        return performed

    def register_birth(self) -> None:
        names_list = [remove_queue_name_prefix(n) for n in self.queue_names()]
        names = u', '.join(u'"{}"'.format(n) for n in names_list)
        log.info('Worker %s (PID %s) has started on queue(s) %s ',
                 self.key, self.pid, names)

    def register_death(self) -> None:
        log.info('Worker %s (PID %s) has stopped', self.key, self.pid)

    @staticmethod
    def _describe(job: Job) -> str:
        if job.meta.get('title'):
            return '{} ({})'.format(job.id, job.meta['title'])
        return job.id

    @staticmethod
    def _dispose_database() -> None:
        # Shut down all database connections and the engine so that they
        # are not shared with a child process and closed there while
        # still being in use in the main process, see
        #
        #   https://github.com/ckan/ckan/issues/3365
        #
        # Note that this rolls back any non-committed changes in the
        # session. Both `Session` and `engine` automatically
        # re-initialize themselves when they are used the next time.
        try:
            meta.Session.remove()
        except Exception:
            log.exception(u'Error while closing database session')
        try:
            if meta.engine is not None:
                meta.engine.dispose()
        except Exception:
            log.exception(u'Error while disposing database engine')

    def execute_job(self, job: Job) -> None:
        u'''
        Run ``job`` in a forked child process, enforcing its timeout.
        '''
        log.debug(u'Disposing database engine before fork')
        self._dispose_database()
        for plugin in plugins.PluginImplementations(plugins.IForkObserver):
            plugin.before_fork()

        pid = os.fork()
        if pid == 0:
            status = 1
            try:
                status = 0 if self.main_work_horse(job) else 1
            except BaseException:
                log.exception('Unhandled error in worker horse for job %s',
                              job.id)
            finally:
                os._exit(status)

        self._wait_for_horse(pid, job)

    def main_work_horse(self, job: Job) -> bool:
        # Runs in the worker's work horse process right after forking.
        load_environment(config)
        return self.perform_job(job)

    def _wait_for_horse(self, pid: int, job: Job) -> None:
        timeout = job.timeout if job.timeout and job.timeout > 0 else None
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
            if deadline is not None and time.monotonic() >= deadline:
                log.error('Job %s on worker %s timed out after %s seconds, '
                          'killing it', job.id, self.key, timeout)
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
                os.waitpid(pid, 0)
                self.backend.fail(job.id, 'timed out after %s seconds'
                                  % timeout)
                return
            time.sleep(0.05)
        current = self.backend.fetch(job.id)
        if current is not None and current.status != 'failed' and \
                not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            self.backend.fail(job.id, 'worker horse exited with status %r'
                              % status)

    def perform_job(self, job: Job) -> bool:
        u'''
        Run ``job`` in the current process and record the outcome.

        :returns: True if the job succeeded.
        '''
        self._dispose_database()
        try:
            job.perform()
        except BaseException as exc:
            self.handle_exception(job, exc)
            self.backend.fail(job.id, traceback.format_exc())
            return False
        else:
            self.backend.finish(job.id)
            return True
        finally:
            self._dispose_database()

    def handle_exception(self, job: Job, exc: BaseException) -> None:
        log.exception('Job %s on worker %s raised an exception: %s',
                      job.id, self.key, exc)
