# encoding: utf-8

from ckan.common import g
from ckan.lib.search import text_traceback


def test_text_traceback_dont_fail_on_runtime_error():
    try:
        _ = g.user
    except RuntimeError:
        assert text_traceback()
