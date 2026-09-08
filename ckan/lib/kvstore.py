# encoding: utf-8
"""Small key/value store in the CKAN database.

This is what extensions should use instead of the raw Redis connection
CKAN used to offer: a place for counters, locks, caches and other bits of
state that need to be shared between processes. Keys should be prefixed
with the site id and the extension name, e.g.
``{site_id}:{extension}:{key}``.

Values are stored as JSON, so anything ``json.dumps`` accepts works.
Expired keys are ignored by every read and removed lazily on writes.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import exc as sa_exc

import ckan.model as model

__all__ = ["get", "set", "delete", "keys", "incr", "expire", "clear"]

log = logging.getLogger(__name__)

TABLE = "kv_store"


def _engine() -> sa.engine.Engine:
    engine = model.meta.engine
    if engine is None:
        raise RuntimeError("The database engine is not ready")
    return engine


def _execute(sql: str, **params: Any) -> Any:
    with _engine().begin() as conn:
        result = conn.execute(sa.text(sql), params)
        if result.returns_rows:
            return result.mappings().all()
        return result.rowcount


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _expiry(ttl: Optional[int]) -> Optional[datetime.datetime]:
    if ttl is None:
        return None
    return _now() + datetime.timedelta(seconds=int(ttl))


def get(key: str, default: Any = None) -> Any:
    """Return the value stored under ``key`` or ``default``."""
    rows = _execute(
        "SELECT value FROM %s WHERE key = :key AND "
        "(expires_at IS NULL OR expires_at > :now)" % TABLE,
        key=key, now=_now())
    if not rows:
        return default
    return rows[0]["value"]


def set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Store ``value`` under ``key``, optionally expiring in ``ttl`` seconds."""
    _execute(
        "INSERT INTO %s (key, value, expires_at) VALUES "
        "(:key, cast(:value as jsonb), :expires_at) ON CONFLICT (key) DO "
        "UPDATE SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at"
        % TABLE,
        key=key, value=json.dumps(value), expires_at=_expiry(ttl))
    _purge()


def delete(*names: str) -> int:
    """Remove the given keys. Returns how many existed."""
    if not names:
        return 0
    placeholders = ", ".join(":k%d" % index for index in range(len(names)))
    params = {"k%d" % index: name for index, name in enumerate(names)}
    return int(_execute(
        "DELETE FROM %s WHERE key IN (%s)" % (TABLE, placeholders), **params))


def keys(pattern: str = "*") -> list[str]:
    """Keys matching a glob ``pattern`` (``*`` and ``?`` wildcards)."""
    like = (pattern.replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_").replace("*", "%").replace("?", "_"))
    rows = _execute(
        "SELECT key FROM %s WHERE key LIKE :like AND "
        "(expires_at IS NULL OR expires_at > :now) ORDER BY key" % TABLE,
        like=like, now=_now())
    return [row["key"] for row in rows]


def incr(key: str, amount: int = 1) -> int:
    """Atomically add ``amount`` to the integer stored under ``key``
    (missing or expired keys count as 0). Returns the new value."""
    expired = ("%s.expires_at IS NOT NULL AND %s.expires_at <= :now"
               % (TABLE, TABLE))
    rows = _execute(
        "INSERT INTO %s (key, value, expires_at) VALUES "
        "(:key, to_jsonb(cast(:amount as numeric)), NULL) "
        "ON CONFLICT (key) DO UPDATE SET value = to_jsonb(CASE WHEN %s "
        "THEN cast(:amount as numeric) ELSE cast(coalesce(cast(%s.value as "
        "text), '0') as numeric) + cast(:amount as numeric) END), "
        "expires_at = CASE WHEN %s THEN NULL ELSE %s.expires_at END "
        "RETURNING value" % (TABLE, expired, TABLE, expired, TABLE),
        key=key, amount=int(amount), now=_now())
    return int(rows[0]["value"])


def expire(key: str, ttl: Optional[int]) -> bool:
    """Set (or with ``None`` remove) the expiry of ``key``."""
    return bool(_execute(
        "UPDATE %s SET expires_at = :expires_at WHERE key = :key" % TABLE,
        key=key, expires_at=_expiry(ttl)))


def clear(pattern: str = "*") -> int:
    """Remove every key matching ``pattern``. Returns how many."""
    names = keys(pattern)
    return delete(*names)


def _purge() -> None:
    try:
        _execute("DELETE FROM %s WHERE expires_at IS NOT NULL AND "
                 "expires_at <= :now" % TABLE, now=_now())
    except sa_exc.SQLAlchemyError as e:  # pragma: no cover
        log.debug("Could not purge expired keys: %s", e)
