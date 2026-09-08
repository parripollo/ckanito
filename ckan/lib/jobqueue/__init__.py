# encoding: utf-8
"""Background job backends registry.

The backend is chosen with ``ckan.jobs.backend``: a built-in name, a name
registered by a plugin through
:class:`ckan.plugins.interfaces.IJobBackend`, or a dotted path
``package.module:ClassName`` to a
:class:`~ckan.lib.jobqueue.base.JobBackend` subclass.
"""
from __future__ import annotations

import importlib
from typing import Optional

from ckan.common import config
from ckan.lib.jobqueue.base import Job, JobBackend

__all__ = ["Job", "JobBackend", "get_backend", "get_backend_class",
           "BUILTIN_BACKENDS", "DEFAULT_BACKEND"]

DEFAULT_BACKEND = "postgres"

BUILTIN_BACKENDS: dict[str, str] = {
    "postgres": "ckan.lib.jobqueue.postgres:PostgresJobBackend",
}


def get_backend(name: Optional[str] = None) -> JobBackend:
    return get_backend_class(name)()


def get_backend_class(name: Optional[str] = None) -> type[JobBackend]:
    backend_name: str = name or config.get(
        "ckan.jobs.backend") or DEFAULT_BACKEND

    registered = _plugin_backends()
    if backend_name in registered:
        return _validate(backend_name, registered[backend_name])

    path = BUILTIN_BACKENDS.get(backend_name, backend_name)
    if ":" not in path:
        raise ValueError(
            "Unknown background jobs backend: %r. Available backends: %s"
            % (backend_name, ", ".join(sorted(
                set(BUILTIN_BACKENDS) | set(registered)))))

    module_name, _, attr = path.partition(":")
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, attr)
    except (ImportError, AttributeError) as e:
        raise ValueError(
            "Could not load background jobs backend %r: %s" % (
                backend_name, e))
    return _validate(backend_name, cls)


def _plugin_backends() -> dict[str, type[JobBackend]]:
    from ckan.plugins import PluginImplementations
    from ckan.plugins.interfaces import IJobBackend

    backends: dict[str, type[JobBackend]] = {}
    for plugin in PluginImplementations(IJobBackend):
        backends.update(plugin.register_job_backends())
    return backends


def _validate(name: str, cls: object) -> type[JobBackend]:
    if not (isinstance(cls, type) and issubclass(cls, JobBackend)):
        raise ValueError(
            "Background jobs backend %r is not a JobBackend subclass: %r"
            % (name, cls))
    return cls
