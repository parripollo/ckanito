# encoding: utf-8
"""Contract for background job backends.

:mod:`ckan.lib.jobs` is the public API used by CKAN core, extensions and
the ``ckan jobs`` commands; it talks to a :class:`JobBackend` selected
with the ``ckan.jobs.backend`` config option. The backend only stores and
hands out jobs; running them is the worker's business
(:class:`ckan.lib.jobs.Worker`).

Queue names handed to a backend are always the full, site-prefixed names
(``ckan:<site_id>:default``), see
:func:`ckan.lib.jobs.add_queue_name_prefix`.
"""
from __future__ import annotations

import datetime
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = ["Job", "JobBackend", "callable_path", "resolve_callable"]

STATUS_QUEUED = "queued"
STATUS_STARTED = "started"
STATUS_FAILED = "failed"


def callable_path(fn: Callable[..., Any]) -> str:
    """``module:qualname`` of a module-level function."""
    module = getattr(fn, "__module__", None)
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    if not module or not name or "<" in name:
        raise ValueError(
            "Background jobs must be module level functions, got %r" % (fn,))
    return "%s:%s" % (module, name)


def resolve_callable(path: str) -> Callable[..., Any]:
    module_name, _, name = path.partition(":")
    target: Any = importlib.import_module(module_name)
    for part in name.split("."):
        target = getattr(target, part)
    return target


@dataclass
class Job:
    """A background job as stored by a backend.

    The attribute names follow the ones CKAN, its tests and extensions
    used to read from ``rq.job.Job``: ``origin`` is the (prefixed) queue
    name and ``meta`` carries the title.
    """
    id: str
    origin: str
    func_name: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    timeout: Optional[int] = None
    status: str = STATUS_QUEUED
    created_at: Optional[datetime.datetime] = None
    scheduled_at: Optional[datetime.datetime] = None
    started_at: Optional[datetime.datetime] = None
    ended_at: Optional[datetime.datetime] = None
    worker: Optional[str] = None
    error: Optional[str] = None
    backend: Optional["JobBackend"] = field(default=None, repr=False,
                                            compare=False)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Job) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    @property
    def func(self) -> Callable[..., Any]:
        return resolve_callable(self.func_name)

    @property
    def description(self) -> str:
        """The call this job makes, as ``func(arg, key=value)``.

        Extensions used to read this from ``rq.job.Job`` to tell jobs
        apart by their arguments.
        """
        parts = [repr(arg) for arg in self.args]
        parts += ["%s=%r" % (key, value)
                  for key, value in self.kwargs.items()]
        return "%s(%s)" % (self.func_name, ", ".join(parts))

    def __str__(self) -> str:
        return "<Job %s: %s>" % (self.id, self.description)

    def perform(self) -> Any:
        """Run the job function in the current process."""
        return self.func(*self.args, **self.kwargs)

    def save(self) -> None:
        """Persist ``meta`` (the only mutable part of a job)."""
        assert self.backend
        self.backend.save_meta(self.id, self.meta)

    def delete(self) -> None:
        """Remove the job from its queue."""
        assert self.backend
        self.backend.delete(self.id)

    def is_scheduled(self, now: datetime.datetime) -> bool:
        return self.scheduled_at is not None and self.scheduled_at > now


class JobBackend:
    """Storage of background jobs. All methods are synchronous and every
    call is its own transaction; instances are cheap and stateless.
    """

    name: str = ""

    def enqueue(self, queue: str, func_name: str, args: list[Any],
                kwargs: dict[str, Any], meta: dict[str, Any],
                timeout: Optional[int], job_id: Optional[str] = None,
                scheduled_at: Optional[datetime.datetime] = None) -> Job:
        """Store a job. ``job_id`` lets callers use deterministic ids (an
        existing job with the same id is replaced); ``scheduled_at``
        (UTC) delays the job."""
        raise NotImplementedError

    def fetch(self, job_id: str) -> Optional[Job]:
        raise NotImplementedError

    def save_meta(self, job_id: str, meta: dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, job_id: str) -> None:
        raise NotImplementedError

    def list_jobs(self, queue: str, limit: Optional[int] = None,
                  include_scheduled: bool = False) -> list[Job]:
        """Queued jobs of ``queue`` in execution order."""
        raise NotImplementedError

    def scheduled_job_ids(self, queue: str) -> list[str]:
        """Ids of the jobs of ``queue`` whose time has not come yet."""
        raise NotImplementedError

    def queues(self, prefix: str) -> list[str]:
        """Names (with prefix) of the queues that currently hold jobs."""
        raise NotImplementedError

    def empty(self, queue: str) -> int:
        """Delete every queued job of ``queue``. Returns how many."""
        raise NotImplementedError

    def dequeue(self, queues: list[str], worker: str) -> Optional[Job]:
        """Atomically claim the next due job from the first non empty
        queue (in the given order) and mark it as started by ``worker``.
        """
        raise NotImplementedError

    def finish(self, job_id: str) -> None:
        """The job ran fine: forget about it."""
        raise NotImplementedError

    def fail(self, job_id: str, error: str) -> None:
        """The job raised, timed out or its worker died: keep it around
        with the error for inspection."""
        raise NotImplementedError

    def fail_stale(self, now: datetime.datetime) -> list[str]:
        """Mark as failed the started jobs whose timeout has long passed
        (their worker must have died). Returns their ids."""
        raise NotImplementedError

    def clear_all(self) -> None:
        """Remove every job of every queue and site (tests)."""
        raise NotImplementedError
