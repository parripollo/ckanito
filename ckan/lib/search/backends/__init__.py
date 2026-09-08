# encoding: utf-8
"""Search backend registry.

The active backend is chosen with the ``ckan.search.backend`` option. The
value can be:

* the name of a built-in backend (see ``BUILTIN_BACKENDS``);
* a name registered by a plugin through
  :class:`ckan.plugins.interfaces.ISearchBackend`;
* a dotted path ``package.module:ClassName`` to a
  :class:`~ckan.lib.search.backends.base.SearchBackend` subclass.
"""
from __future__ import annotations

import importlib
import logging
from typing import Optional

from ckan.common import config
from ckan.lib.search.backends.base import SearchBackend, SearchResponse
from ckan.lib.search.common import SearchError

__all__ = ["SearchBackend", "SearchResponse", "get_backend",
           "get_backend_class", "BUILTIN_BACKENDS", "DEFAULT_BACKEND"]

log = logging.getLogger(__name__)

DEFAULT_BACKEND = "postgres"

BUILTIN_BACKENDS: dict[str, str] = {
    "postgres": "ckan.lib.search.backends.postgres:PostgresSearchBackend",
    "solr": "ckan.lib.search.backends.solr:SolrSearchBackend",
}


def get_backend(name: Optional[str] = None) -> SearchBackend:
    """Return an instance of the configured (or the named) backend."""
    return get_backend_class(name)()


def get_backend_class(name: Optional[str] = None) -> type[SearchBackend]:
    """Resolve a backend name (or dotted path) to its class."""
    backend_name: str = name or config.get(
        "ckan.search.backend") or DEFAULT_BACKEND

    registered = _plugin_backends()
    if backend_name in registered:
        return _validate(backend_name, registered[backend_name])

    path = BUILTIN_BACKENDS.get(backend_name, backend_name)
    if ":" not in path:
        raise SearchError(
            "Unknown search backend: %r. Available backends: %s" % (
                backend_name, ", ".join(sorted(
                    set(BUILTIN_BACKENDS) | set(registered)))))

    module_name, _, attr = path.partition(":")
    try:
        module = importlib.import_module(module_name)
        cls = getattr(module, attr)
    except (ImportError, AttributeError) as e:
        raise SearchError(
            "Could not load search backend %r: %s" % (backend_name, e))
    return _validate(backend_name, cls)


def _plugin_backends() -> dict[str, type[SearchBackend]]:
    # imported here to avoid a circular import at module load time
    from ckan.plugins import PluginImplementations
    from ckan.plugins.interfaces import ISearchBackend

    backends: dict[str, type[SearchBackend]] = {}
    for plugin in PluginImplementations(ISearchBackend):
        backends.update(plugin.register_search_backends())
    return backends


def _validate(name: str, cls: object) -> type[SearchBackend]:
    if not (isinstance(cls, type) and issubclass(cls, SearchBackend)):
        raise SearchError(
            "Search backend %r is not a SearchBackend subclass: %r" % (
                name, cls))
    return cls
