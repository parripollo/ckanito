# encoding: utf-8
import pytest

import ckan.plugins as p
from ckan.common import config
from ckan.lib.search import SearchError, backends
from ckan.lib.search.backends.base import SearchBackend, SearchResponse
from ckan.lib.search.backends.postgres import PostgresSearchBackend


class _DummyBackend(SearchBackend):
    name = "dummy"


class _BackendPlugin(p.SingletonPlugin):
    p.implements(p.ISearchBackend)

    def register_search_backends(self):
        return {"dummy": _DummyBackend}


def test_default_backend_is_postgres():
    assert isinstance(backends.get_backend(), PostgresSearchBackend)


def test_explicit_name():
    assert backends.get_backend_class("postgres") is PostgresSearchBackend


@pytest.mark.ckan_config(
    "ckan.search.backend",
    "ckan.lib.search.backends.postgres:PostgresSearchBackend")
def test_dotted_path_from_config():
    assert backends.get_backend_class() is PostgresSearchBackend


@pytest.mark.ckan_config("ckan.search.backend", "elastic")
def test_unknown_backend_raises():
    with pytest.raises(SearchError, match="Unknown search backend"):
        backends.get_backend()


@pytest.mark.ckan_config("ckan.search.backend", "os.path:join")
def test_not_a_backend_class_raises():
    with pytest.raises(SearchError, match="not a SearchBackend subclass"):
        backends.get_backend()


@pytest.mark.ckan_config("ckan.search.backend", "ckan.nope:Nope")
def test_unimportable_backend_raises():
    with pytest.raises(SearchError, match="Could not load search backend"):
        backends.get_backend()


@pytest.mark.ckan_config("ckan.plugins", "test_search_backend_plugin")
@pytest.mark.provide_plugin("test_search_backend_plugin", _BackendPlugin)
@pytest.mark.usefixtures("with_plugins")
def test_plugin_registered_backend():
    # the backend is set after the plugin is loaded and restored before
    # the plugin is unloaded again (unloading re-runs the schema check)
    previous = config.get("ckan.search.backend")
    config["ckan.search.backend"] = "dummy"
    try:
        assert isinstance(backends.get_backend(), _DummyBackend)
    finally:
        config["ckan.search.backend"] = previous


def test_base_contract_is_abstract():
    backend = SearchBackend()
    assert backend.check_schema() is True
    for method, args in (
            ("is_available", ()),
            ("index", ({},)),
            ("delete", ("x", "site")),
            ("commit", ()),
            ("clear", ("site",)),
            ("search", ({},)),
            ("get_by_reference", ("x", "site")),
            ("get_all_entity_ids", ("site",)),
    ):
        with pytest.raises(NotImplementedError):
            getattr(backend, method)(*args)


def test_search_response_defaults():
    response = SearchResponse(count=0, docs=[])
    assert response.facets == {}


def test_row_indexes_multilingual_fields():
    """title_<lang> and text_<lang> (ckanext-multilingual) are part of the
    full text, with the weight of title and of the catch-all text."""
    backend = PostgresSearchBackend()
    row = backend.row({
        "index_id": "x", "id": "x", "name": "aire", "title": "Aire",
        "title_string": "Aire", "text": "calidad",
        "title_en": "Air quality", "text_en": "measurement stations",
    })
    assert row["fts_a"] == "aire Aire Air quality"
    assert row["fts_d"] == "calidad measurement stations"
