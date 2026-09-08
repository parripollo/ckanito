=============================
Sessions and key/value store
=============================

Sessions
========

CKAN 2.11 moved to Flask-Session, which is already pluggable; the default
``SESSION_TYPE = cookie`` never needed Redis. CKANito removes the
``redis`` wiring from ``ckan/config/middleware`` and adds
``SESSION_TYPE = postgres``: ``CKANPostgresSessionInterface`` is a
``ServerSideSessionInterface`` from Flask-Session that keeps the
serialized session in the ``session_store`` table (``id`` = prefixed
session id, ``data``, ``expiry``). Expired rows are deleted on every
write. CKAN's ``CKANJsonSessionSerializer`` (which lets flash messages
carry ``Markup``) applies as to any other server side backend. Any other
Flask-Session backend still works if its package is installed.

The session tests that exercised Redis (storage, session fixation on
login, the serializer) now run against ``postgres``.

Key/value store
===============

CKAN's best-practices document told extension authors they could use the
raw Redis connection for their own keys, prefixed with the site id and
the extension name. ``ckan.lib.kvstore`` is the sanctioned replacement:
``get``, ``set(key, value, ttl=None)``, ``delete``, ``keys(pattern)``
(glob), ``incr`` (atomic), ``expire``, ``clear``. Values are JSON, stored
in the ``kv_store`` table with an optional ``expires_at``; expired keys
are invisible to reads and purged on writes. It is deliberately small:
it is for counters, locks and cached bits of state, not for large data.

The test fixtures ``reset_redis`` / ``clean_redis`` became
``reset_kvstore`` / ``clean_kvstore``; the old names are kept as aliases
so third party extension tests keep collecting.
