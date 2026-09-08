# encoding: utf-8
"""Tables that replace the services CKAN used to need next to PostgreSQL.

* ``background_job``: the job queue (:mod:`ckan.lib.jobqueue.postgres`).
* ``session_store``: server side sessions (``SESSION_TYPE = postgres``).
* ``kv_store``: small key/value store for extensions
  (:mod:`ckan.lib.kvstore`).
"""
from __future__ import annotations

from sqlalchemy import types, Column, Table, Index
from sqlalchemy.dialects.postgresql import JSONB

from ckan.model import meta

__all__ = ["background_job_table", "session_store_table", "kv_store_table"]

background_job_table = Table(
    "background_job",
    meta.metadata,
    Column("id", types.UnicodeText, primary_key=True),
    Column("queue", types.UnicodeText, nullable=False),
    Column("func", types.UnicodeText, nullable=False),
    Column("args", types.LargeBinary),
    Column("kwargs", types.LargeBinary),
    Column("meta", JSONB),
    Column("timeout", types.Integer),
    Column("status", types.UnicodeText, nullable=False),
    Column("scheduled_at", types.DateTime, nullable=False),
    Column("created_at", types.DateTime, nullable=False),
    Column("started_at", types.DateTime),
    Column("ended_at", types.DateTime),
    Column("worker", types.UnicodeText),
    Column("error", types.UnicodeText),
    Index("idx_background_job_queue", "status", "queue", "scheduled_at"),
)

session_store_table = Table(
    "session_store",
    meta.metadata,
    Column("id", types.UnicodeText, primary_key=True),
    Column("data", types.LargeBinary, nullable=False),
    Column("expiry", types.DateTime),
    Index("idx_session_store_expiry", "expiry"),
)

kv_store_table = Table(
    "kv_store",
    meta.metadata,
    Column("key", types.UnicodeText, primary_key=True),
    Column("value", JSONB),
    Column("expires_at", types.DateTime),
    Index("idx_kv_store_expires_at", "expires_at"),
)
