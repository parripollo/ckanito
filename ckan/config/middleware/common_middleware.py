"""Additional middleware used by the Flask app stack."""
from __future__ import annotations

import datetime
from typing import Any, Optional

import sqlalchemy as sa
from flask.sessions import SecureCookieSessionInterface
from flask_session.base import ServerSideSession, ServerSideSessionInterface

from ckan.types import CKANApp, Request
import ckan.model as model


class RootPathMiddleware(object):
    '''
    Prevents the SCRIPT_NAME server variable conflicting with the ckan.root_url
    config. The routes package uses the SCRIPT_NAME variable and appends to the
    path and ckan adds the root url causing a duplication of the root path.
    This is a middleware to ensure that even redirects use this logic.
    '''
    def __init__(self, app: Any):
        self.app = app

    def __call__(self, environ: Any, start_response: Any):
        # Prevents the variable interfering with the root_path logic
        if 'SCRIPT_NAME' in environ:
            environ['SCRIPT_NAME'] = ''

        return self.app(environ, start_response)


class CKANSecureCookieSessionInterface(SecureCookieSessionInterface):
    """Flask cookie-based sessions with expiration support.

    Parent class supports only cookies stored till the end of the browser's
    session. Current class extends its functionality and adds support of
    permanent sessions.

    """

    def __init__(self, app: CKANApp):
        pass

    def open_session(self, app: CKANApp, request: Request):
        session = super().open_session(app, request)
        if session:
            # Cookie-based sessions expire with the browser's session. The line
            # below changes this behavior, extending session's lifetime by
            # `PERMANENT_SESSION_LIFETIME` seconds. `SESSION_PERMANENT` option
            # is used as indicator of permanent sessions by flask-session
            # package, so we also should rely on it, for predictability.
            session.setdefault("_permanent", app.config["SESSION_PERMANENT"])

        return session


class CKANPostgresSessionInterface(ServerSideSessionInterface):
    """Flask-Session server side sessions stored in the CKAN database.

    Session data lives in the ``session_store`` table, keyed by the
    prefixed session id and expiring with ``PERMANENT_SESSION_LIFETIME``.
    Expired rows are removed whenever a session is written.
    """

    table = "session_store"

    def __init__(self, app: CKANApp):
        super().__init__(
            app,
            app.config["SESSION_KEY_PREFIX"],
            app.config["SESSION_USE_SIGNER"],
            app.config["SESSION_PERMANENT"],
        )

    def _execute(self, sql: str, **params: Any) -> Any:
        engine = model.meta.engine
        assert engine is not None, "The database engine is not ready"
        with engine.begin() as conn:
            result = conn.execute(sa.text(sql), params)
            if result.returns_rows:
                return result.mappings().all()
            return result.rowcount

    def _retrieve_session_data(
            self, store_id: str) -> Optional[dict[str, Any]]:
        rows = self._execute(
            "SELECT data FROM %s WHERE id = :id AND "
            "(expiry IS NULL OR expiry > :now)" % self.table,
            id=store_id, now=datetime.datetime.utcnow())
        if not rows:
            return None
        assert self.serializer
        return self.serializer.decode(bytes(rows[0]["data"]))

    def _delete_session(self, store_id: str) -> None:
        self._execute("DELETE FROM %s WHERE id = :id" % self.table,
                      id=store_id)

    def _upsert_session(self, session_lifetime: datetime.timedelta,
                        session: ServerSideSession, store_id: str) -> None:
        now = datetime.datetime.utcnow()
        assert self.serializer
        self._execute(
            "INSERT INTO %s (id, data, expiry) VALUES (:id, :data, :expiry) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, "
            "expiry = EXCLUDED.expiry" % self.table,
            id=store_id, data=self.serializer.encode(session),
            expiry=now + session_lifetime)
        self._execute("DELETE FROM %s WHERE expiry IS NOT NULL AND "
                      "expiry <= :now" % self.table, now=now)
