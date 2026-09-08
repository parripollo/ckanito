# encoding: utf-8
"""Background jobs stored in the ``background_job`` table.

Workers claim jobs with ``SELECT ... FOR UPDATE SKIP LOCKED`` so any
number of them can poll the same queues without stepping on each other.
Delayed jobs are simply rows whose ``scheduled_at`` is in the future.
"""
from __future__ import annotations

import datetime
import logging
import pickle
import uuid
from typing import Any, Optional

import sqlalchemy as sa

import ckan.model as model
from ckan.lib.jobqueue.base import (
    Job, JobBackend, STATUS_FAILED, STATUS_QUEUED, STATUS_STARTED,
)

__all__ = ["PostgresJobBackend", "TABLE"]

log = logging.getLogger(__name__)

TABLE = "background_job"

_COLUMNS = ("id, queue, func, args, kwargs, meta, timeout, status, "
            "scheduled_at, created_at, started_at, ended_at, worker, error")


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class PostgresJobBackend(JobBackend):
    name = "postgres"

    # -- infrastructure ----------------------------------------------------

    @property
    def engine(self) -> sa.engine.Engine:
        engine = model.meta.engine
        if engine is None:
            raise RuntimeError("The database engine is not ready")
        return engine

    def execute(self, sql: str, **params: Any) -> Any:
        with self.engine.begin() as conn:
            result = conn.execute(sa.text(sql), params)
            if result.returns_rows:
                return result.mappings().all()
            return result.rowcount

    def job_from_row(self, row: Any) -> Job:
        return Job(
            id=row["id"],
            origin=row["queue"],
            func_name=row["func"],
            args=list(pickle.loads(row["args"])) if row["args"] else [],
            kwargs=dict(pickle.loads(row["kwargs"])) if row["kwargs"] else {},
            meta=dict(row["meta"] or {}),
            timeout=row["timeout"],
            status=row["status"],
            created_at=row["created_at"],
            scheduled_at=row["scheduled_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            worker=row["worker"],
            error=row["error"],
            backend=self,
        )

    # -- contract ----------------------------------------------------------

    def enqueue(self, queue: str, func_name: str, args: list[Any],
                kwargs: dict[str, Any], meta: dict[str, Any],
                timeout: Optional[int], job_id: Optional[str] = None,
                scheduled_at: Optional[datetime.datetime] = None) -> Job:
        now = _now()
        job_id = job_id or str(uuid.uuid4())
        self.execute(
            "INSERT INTO %s (%s) VALUES (:id, :queue, :func, :args, :kwargs, "
            "cast(:meta as jsonb), :timeout, :status, :scheduled_at, "
            ":created_at, NULL, NULL, NULL, NULL) "
            "ON CONFLICT (id) DO UPDATE SET queue = EXCLUDED.queue, "
            "func = EXCLUDED.func, args = EXCLUDED.args, "
            "kwargs = EXCLUDED.kwargs, meta = EXCLUDED.meta, "
            "timeout = EXCLUDED.timeout, status = EXCLUDED.status, "
            "scheduled_at = EXCLUDED.scheduled_at, "
            "created_at = EXCLUDED.created_at, started_at = NULL, "
            "ended_at = NULL, worker = NULL, error = NULL" % (
                TABLE, _COLUMNS),
            id=job_id, queue=queue, func=func_name,
            args=pickle.dumps(list(args), protocol=4),
            kwargs=pickle.dumps(dict(kwargs), protocol=4),
            meta=_json(meta), timeout=timeout, status=STATUS_QUEUED,
            scheduled_at=scheduled_at or now, created_at=now)
        job = self.fetch(job_id)
        assert job
        return job

    def fetch(self, job_id: str) -> Optional[Job]:
        rows = self.execute(
            "SELECT %s FROM %s WHERE id = :id" % (_COLUMNS, TABLE), id=job_id)
        return self.job_from_row(rows[0]) if rows else None

    def save_meta(self, job_id: str, meta: dict[str, Any]) -> None:
        self.execute(
            "UPDATE %s SET meta = cast(:meta as jsonb) WHERE id = :id" % TABLE,
            id=job_id, meta=_json(meta))

    def delete(self, job_id: str) -> None:
        self.execute("DELETE FROM %s WHERE id = :id" % TABLE, id=job_id)

    def list_jobs(self, queue: str, limit: Optional[int] = None,
                  include_scheduled: bool = False) -> list[Job]:
        sql = ("SELECT %s FROM %s WHERE queue = :queue AND status = :status"
               % (_COLUMNS, TABLE))
        if not include_scheduled:
            sql += " AND scheduled_at <= :now"
        sql += " ORDER BY scheduled_at, created_at, id"
        if limit is not None and limit > 0:
            sql += " LIMIT :limit"
        rows = self.execute(sql, queue=queue, status=STATUS_QUEUED,
                            now=_now(), limit=limit)
        return [self.job_from_row(row) for row in rows]

    def scheduled_job_ids(self, queue: str) -> list[str]:
        rows = self.execute(
            "SELECT id FROM %s WHERE queue = :queue AND status = :status "
            "AND scheduled_at > :now ORDER BY scheduled_at" % TABLE,
            queue=queue, status=STATUS_QUEUED, now=_now())
        return [row["id"] for row in rows]

    def queues(self, prefix: str) -> list[str]:
        rows = self.execute(
            "SELECT DISTINCT queue FROM %s WHERE queue LIKE :pattern "
            "AND status = :status ORDER BY queue" % TABLE,
            pattern=_like_prefix(prefix), status=STATUS_QUEUED)
        return [row["queue"] for row in rows]

    def empty(self, queue: str) -> int:
        return int(self.execute(
            "DELETE FROM %s WHERE queue = :queue AND status = :status" % TABLE,
            queue=queue, status=STATUS_QUEUED))

    def dequeue(self, queues: list[str], worker: str) -> Optional[Job]:
        if not queues:
            return None
        now = _now()
        # the leftmost non empty queue wins, like the RQ worker did
        order = " ".join(
            "WHEN :q%d THEN %d" % (index, index)
            for index in range(len(queues)))
        params: dict[str, Any] = {
            "q%d" % index: name for index, name in enumerate(queues)}
        placeholders = ", ".join(":q%d" % index for index in range(len(queues)))
        rows = self.execute(
            "UPDATE %s SET status = :started, started_at = :now, "
            "worker = :worker WHERE id = ("
            "SELECT id FROM %s WHERE queue IN (%s) AND status = :queued "
            "AND scheduled_at <= :now ORDER BY CASE queue %s END, "
            "scheduled_at, created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING %s" % (TABLE, TABLE, placeholders, order, _COLUMNS),
            started=STATUS_STARTED, queued=STATUS_QUEUED, now=now,
            worker=worker, **params)
        return self.job_from_row(rows[0]) if rows else None

    def finish(self, job_id: str) -> None:
        self.delete(job_id)

    def fail(self, job_id: str, error: str) -> None:
        self.execute(
            "UPDATE %s SET status = :status, ended_at = :now, error = :error "
            "WHERE id = :id" % TABLE,
            id=job_id, status=STATUS_FAILED, now=_now(), error=error[:10000])

    def fail_stale(self, now: datetime.datetime) -> list[str]:
        rows = self.execute(
            "UPDATE %s SET status = :failed, ended_at = :now, "
            "error = 'worker died or job timed out' WHERE status = :started "
            "AND timeout > 0 AND started_at < :now - make_interval(secs => "
            "timeout + 60) RETURNING id" % TABLE,
            failed=STATUS_FAILED, started=STATUS_STARTED, now=now)
        return [row["id"] for row in rows]

    def clear_all(self) -> None:
        self.execute("DELETE FROM %s" % TABLE)


def _json(value: dict[str, Any]) -> str:
    import json
    return json.dumps(value, default=str)


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace(
        "_", "\\_")
    return escaped + "%"
