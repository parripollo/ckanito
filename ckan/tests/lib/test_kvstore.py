# encoding: utf-8
import pytest

import ckan.lib.kvstore as kvstore


@pytest.mark.usefixtures("clean_kvstore")
class TestKVStore:
    def test_get_missing_returns_default(self):
        assert kvstore.get("missing") is None
        assert kvstore.get("missing", 42) == 42

    def test_set_and_get_json_values(self):
        kvstore.set("text", "hello")
        kvstore.set("number", 3.5)
        kvstore.set("nested", {"a": [1, 2, {"b": None}]})
        assert kvstore.get("text") == "hello"
        assert kvstore.get("number") == 3.5
        assert kvstore.get("nested") == {"a": [1, 2, {"b": None}]}

    def test_set_replaces_value(self):
        kvstore.set("key", 1)
        kvstore.set("key", 2)
        assert kvstore.get("key") == 2

    def test_delete(self):
        kvstore.set("a", 1)
        kvstore.set("b", 2)
        assert kvstore.delete("a", "b", "nope") == 2
        assert kvstore.keys() == []

    def test_keys_pattern(self):
        for key in ("site:ext:one", "site:ext:two", "site:other:x", "y_z"):
            kvstore.set(key, True)
        assert kvstore.keys("site:ext:*") == ["site:ext:one", "site:ext:two"]
        assert kvstore.keys("site:*:?") == ["site:other:x"]
        # LIKE metacharacters are literal in patterns
        assert kvstore.keys("y_z") == ["y_z"]
        assert kvstore.keys("y%z") == []

    def test_ttl(self):
        kvstore.set("short", 1, ttl=1)
        kvstore.set("long", 2, ttl=3600)
        assert kvstore.get("short") == 1
        assert kvstore.expire("short", -1)
        assert kvstore.get("short") is None
        assert "short" not in kvstore.keys()
        assert kvstore.get("long") == 2

    def test_incr(self):
        assert kvstore.incr("counter") == 1
        assert kvstore.incr("counter", 5) == 6
        assert kvstore.incr("counter", -2) == 4
        assert kvstore.get("counter") == 4

    def test_incr_expired_key_restarts(self):
        kvstore.set("counter", 10, ttl=3600)
        kvstore.expire("counter", -1)
        assert kvstore.incr("counter") == 1

    def test_clear_pattern(self):
        kvstore.set("a:1", 1)
        kvstore.set("a:2", 2)
        kvstore.set("b:1", 3)
        assert kvstore.clear("a:*") == 2
        assert kvstore.keys() == ["b:1"]
