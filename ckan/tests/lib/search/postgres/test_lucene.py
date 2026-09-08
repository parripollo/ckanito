# encoding: utf-8
import pytest

from ckan.lib.search import SearchQueryError
from ckan.lib.search.backends.postgres.lucene import (
    parse, And, Or, Not, Term, Range, MatchAll,
)


@pytest.mark.parametrize("query,expected", [
    ("", MatchAll()),
    ("   ", MatchAll()),
    ("*:*", MatchAll()),
    ("*", MatchAll()),
    ("water", Term(None, "water")),
    ('"water quality"', Term(None, "water quality", phrase=True)),
    ("name:monkey", Term("name", "monkey")),
    ('tags:"tolstoy"', Term("tags", "tolstoy", phrase=True)),
    ('+site_id:"test.ckan.net"', Term("site_id", "test.ckan.net", True)),
    ("+state:active", Term("state", "active")),
    ("-type:harvest", Not(Term("type", "harvest"))),
    ("NOT type:harvest", Not(Term("type", "harvest"))),
    ("!type:harvest", Not(Term("type", "harvest"))),
    ("field:*", Term("field", "*")),
    ("wat*", Term(None, "wat*")),
    ("wi?ld", Term(None, "wi?ld")),
    ("owner_org:5a1b-2c3d", Term("owner_org", "5a1b-2c3d")),
    ("f:-1", Term("f", "-1")),
    ("title:Monkey^4", Term("title", "Monkey")),
    ("roam~0.8", Term(None, "roam")),
    ("joe@doe.com", Term(None, "joe@doe.com")),
    (u'tags:"with greek omega Ω"',
     Term("tags", u"with greek omega Ω", True)),
    (r"name:a\:b", Term("name", "a:b")),
    (r"name:a\*b", Term("name", r"a\*b")),
    ('name:"say \\"hi\\""', Term("name", 'say "hi"', True)),
])
def test_single_terms(query, expected):
    assert parse(query) == expected


@pytest.mark.parametrize("query,expected", [
    ("a b", And(Term(None, "a"), Term(None, "b"))),
    ("a AND b", And(Term(None, "a"), Term(None, "b"))),
    ("a && b", And(Term(None, "a"), Term(None, "b"))),
    ("a OR b", Or(Term(None, "a"), Term(None, "b"))),
    ("a || b", Or(Term(None, "a"), Term(None, "b"))),
    ("a b c", And(And(Term(None, "a"), Term(None, "b")), Term(None, "c"))),
    ("a OR b c", Or(Term(None, "a"), And(Term(None, "b"), Term(None, "c")))),
    ("a b OR c", Or(And(Term(None, "a"), Term(None, "b")), Term(None, "c"))),
    ("(a OR b) c", And(Or(Term(None, "a"), Term(None, "b")), Term(None, "c"))),
    ("a -b", And(Term(None, "a"), Not(Term(None, "b")))),
    ("+a +b", And(Term(None, "a"), Term(None, "b"))),
    ("NOT a b", And(Not(Term(None, "a")), Term(None, "b"))),
    ("a AND NOT b", And(Term(None, "a"), Not(Term(None, "b")))),
    ("+capacity:public +state:(active OR draft)",
     And(Term("capacity", "public"),
         Or(Term("state", "active"), Term("state", "draft")))),
    ('+permission_labels:("public" OR "creator-abc")',
     Or(Term("permission_labels", "public", True),
        Term("permission_labels", "creator-abc", True))),
    ("+capacity:public  +state:(active)",
     And(Term("capacity", "public"), Term("state", "active"))),
    ('name:"x" OR id:"x"',
     Or(Term("name", "x", True), Term("id", "x", True))),
    ("name_ngram:wat OR title_ngram:wat OR name:wat OR title:wat",
     Or(Or(Or(Term("name_ngram", "wat"), Term("title_ngram", "wat")),
           Term("name", "wat")), Term("title", "wat"))),
    ('+entity_type:package AND +(id:"x" OR name:"x") AND +site_id:"s"',
     And(And(Term("entity_type", "package"),
             Or(Term("id", "x", True), Term("name", "x", True))),
         Term("site_id", "s", True))),
    ("dataset_type:dataset -harvest:*",
     And(Term("dataset_type", "dataset"), Not(Term("harvest", "*")))),
    ("title:water notes:quality",
     And(Term("title", "water"), Term("notes", "quality"))),
])
def test_boolean_combinations(query, expected):
    assert parse(query) == expected


@pytest.mark.parametrize("query,expected", [
    ("metadata_modified:[2020-01-01T00:00:00Z TO 2021-01-01T00:00:00Z]",
     Range("metadata_modified", "2020-01-01T00:00:00Z",
           "2021-01-01T00:00:00Z")),
    ("metadata_modified:[NOW-7DAYS TO NOW]",
     Range("metadata_modified", "NOW-7DAYS", "NOW")),
    ("views_total:[10 TO *]", Range("views_total", "10", None)),
    ("views_total:{* TO 10}", Range("views_total", None, "10", False, False)),
    ("num:[1 TO 5}", Range("num", "1", "5", True, False)),
    ('date:["2020-01-01" TO "2020-02-01"]',
     Range("date", "2020-01-01", "2020-02-01")),
])
def test_ranges(query, expected):
    assert parse(query) == expected


@pytest.mark.parametrize("query", [
    "(a OR b",
    "a OR",
    "AND a",
    "a OR OR b",
    "field:",
    "field:[1 TO",
    "field:[1 TO 2",
    "field:(a:b)",
    '"unterminated',
    "a ) b",
])
def test_invalid_queries(query):
    with pytest.raises(SearchQueryError):
        parse(query)
