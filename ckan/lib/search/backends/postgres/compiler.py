# encoding: utf-8
"""Compile CKAN search params into SQL for the ``package_search_index`` table.

Everything here produces SQL fragments with named bind parameters
(``:p0``, ``:p1``...). Field names never end up inside the SQL text
unless they name one of the fixed table columns; any other field is read
from the ``doc`` JSONB column through a bound parameter.
"""
from __future__ import annotations

import datetime
import re
from typing import Any, Optional

from ckan.lib.search.common import SearchQueryError
from ckan.lib.search.backends.postgres import lucene
from ckan.lib.search.backends.postgres.lucene import (
    Node, Term, Range, And, Or, Not, MatchAll,
)

__all__ = ["Compiler", "FieldTypes", "parse_sort", "parse_date"]


class FieldTypes:
    """What we know about the fields of the search document.

    Mirrors the Solr schema CKAN has always shipped: a handful of typed
    columns, ``text``-like fields that are searched with stemming, dates,
    counters, and everything else matched as an exact string.
    """
    scalar_columns = frozenset([
        "index_id", "id", "site_id", "entity_type", "dataset_type",
        "name", "title", "title_string", "state", "capacity",
        "organization",
    ])
    date_columns = frozenset([
        "metadata_created", "metadata_modified", "indexed_ts",
    ])
    array_columns = frozenset([
        "permission_labels", "tags", "groups", "res_format",
    ])
    stored_columns = frozenset(["data_dict", "validated_data_dict"])
    text_fields = frozenset([
        "title", "notes", "urls", "res_name", "res_description",
        "author", "author_email", "maintainer", "maintainer_email",
    ])
    text_prefixes = ("extras_", "res_extras_", "text_")
    ngram_fields = {"name_ngram": "name", "title_ngram": "title"}
    int_fields = frozenset([
        "views_total", "views_recent",
        "resources_accessed_total", "resources_accessed_recent",
    ])
    fulltext_field = "text"

    @classmethod
    def is_column(cls, field: str) -> bool:
        return (field in cls.scalar_columns or field in cls.date_columns
                or field in cls.array_columns
                or field in cls.stored_columns)

    @classmethod
    def is_text(cls, field: str) -> bool:
        return field in cls.text_fields or field.startswith(
            cls.text_prefixes)

    @classmethod
    def is_date(cls, field: str) -> bool:
        return field in cls.date_columns or field.endswith("_date")


_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
_LIKE_ESCAPE_RE = re.compile(r"([%_\\])")


class Compiler:
    """Turn query params into SQL fragments sharing one bind params dict."""

    def __init__(self, text_config: str, default_field: str = "text",
                 now: Optional[datetime.datetime] = None):
        self.params: dict[str, Any] = {"cfg": text_config}
        self.default_field = default_field
        self.now = now or datetime.datetime.utcnow()
        self.rank_parts: list[str] = []
        self._counter = 0

    # -- params ------------------------------------------------------------

    def bind(self, value: Any) -> str:
        name = "p%d" % self._counter
        self._counter += 1
        self.params[name] = value
        return ":" + name

    @property
    def cfg(self) -> str:
        return "cast(:cfg as regconfig)"

    # -- public entry points -----------------------------------------------

    def where(self, query: str) -> str:
        """Compile a Lucene expression (``fq`` entry or fielded ``q``)."""
        return self.node(lucene.parse(query))

    def freetext(self, query: str) -> str:
        """Compile a free text ``q`` (the dismax case) against ``fts``."""
        tsquery = self.tsquery_from_freetext(query)
        if tsquery is None:
            return "TRUE"
        self.rank_parts.append("ts_rank_cd(fts, %s)" % tsquery)
        return "(fts @@ %s)" % tsquery

    def rank(self) -> str:
        if not self.rank_parts:
            # a bare 0 in ORDER BY would be read as a column position
            return "cast(0 as real)"
        return "(" + " + ".join(self.rank_parts) + ")"

    # -- AST ---------------------------------------------------------------

    def node(self, node: Node) -> str:
        if isinstance(node, MatchAll):
            return "TRUE"
        if isinstance(node, And):
            return "(%s AND %s)" % (self.node(node.left),
                                    self.node(node.right))
        if isinstance(node, Or):
            return "(%s OR %s)" % (self.node(node.left),
                                   self.node(node.right))
        if isinstance(node, Not):
            return "(NOT %s)" % self.node(node.operand)
        if isinstance(node, Range):
            return self.range(node)
        assert isinstance(node, Term)
        return self.term(node)

    # -- terms -------------------------------------------------------------

    def term(self, term: Term) -> str:
        field = term.field or self.default_field
        self.check_field(field)
        value = term.value

        if field == FieldTypes.fulltext_field:
            return self.fulltext(value, term.phrase)

        if field in FieldTypes.ngram_fields:
            column = FieldTypes.ngram_fields[field]
            pattern = "%" + _LIKE_ESCAPE_RE.sub(r"\\\1", value) + "%"
            return "coalesce(%s ILIKE %s, false)" % (
                column, self.bind(pattern))

        if FieldTypes.is_text(field):
            return self.text_match(field, value, term.phrase)

        if FieldTypes.is_date(field):
            return self.compare(field, "=", parse_date(value, self.now))

        if field in FieldTypes.int_fields:
            return self.compare(field, "=", parse_number(value))

        if not term.phrase and value == "*":
            return self.exists(field)

        if not term.phrase and _has_wildcard(value):
            return self.like(field, _wildcard_to_like(value))

        return self.equals(field, lucene.unescape(value))

    def fulltext(self, value: str, phrase: bool) -> str:
        tsquery = self.tsquery(value, phrase)
        self.rank_parts.append("ts_rank_cd(fts, %s)" % tsquery)
        return "(fts @@ %s)" % tsquery

    def text_match(self, field: str, value: str, phrase: bool) -> str:
        tsquery = self.tsquery(value, phrase)
        if field in FieldTypes.scalar_columns:
            source = "coalesce(%s, '')" % field
        else:
            source = "coalesce(doc->%s, '\"\"'::jsonb)" % self.bind(field)
        return "(to_tsvector(%s, %s) @@ %s)" % (self.cfg, source, tsquery)

    def tsquery(self, value: str, phrase: bool) -> str:
        if phrase:
            return "phraseto_tsquery(%s, %s)" % (self.cfg, self.bind(value))
        if value.endswith("*") and not _has_wildcard(value[:-1]):
            return self.prefix_tsquery(value[:-1])
        return "plainto_tsquery(%s, %s)" % (
            self.cfg, self.bind(_wildcard_to_plain(value)))

    def prefix_tsquery(self, value: str) -> str:
        # to_tsquery syntax: words joined with & and :* for prefix matching
        words = [w for w in re.split(r"[^\w]+", value, flags=re.UNICODE)
                 if w]
        if not words:
            return "plainto_tsquery(%s, '')" % self.cfg
        words[-1] = words[-1] + ":*"
        return "to_tsquery(%s, %s)" % (self.cfg, self.bind(" & ".join(words)))

    def tsquery_from_freetext(self, query: str) -> Optional[str]:
        """dismax-style free text: phrases, +/- prefixes, trailing *."""
        parts: list[str] = []
        for match in re.finditer(
                r'([+\-]?)(?:"((?:\\.|[^"\\])*)"|(\S+))', query):
            modifier, quoted, word = match.groups()
            if word is not None and word[0] in "+-":
                raise SearchQueryError(
                    "Could not parse query %r: unexpected %r" % (query, word))
            if quoted is not None:
                if not quoted.strip():
                    continue
                piece = "phraseto_tsquery(%s, %s)" % (
                    self.cfg, self.bind(lucene.unescape(quoted)))
            else:
                word = word.strip('"')
                if not word:
                    continue
                piece = self.tsquery(word, phrase=False)
            if modifier == "-":
                piece = "(!! %s)" % piece
            parts.append(piece)
        if not parts:
            return None
        return "(" + " && ".join(parts) + ")"

    # -- field access ------------------------------------------------------

    def check_field(self, field: str) -> None:
        if not _FIELD_NAME_RE.match(field):
            raise SearchQueryError("Invalid field name: %r" % field)

    def scalar(self, field: str) -> str:
        """SQL expression with the text value of a single valued field."""
        if field in FieldTypes.scalar_columns:
            return field
        return "(doc->>%s)" % self.bind(field)

    def typed(self, field: str, value: Any) -> str:
        """SQL expression of ``field`` cast to the type of ``value``."""
        if isinstance(value, datetime.datetime):
            if field in FieldTypes.date_columns:
                return field
            return "cast(doc->>%s as timestamp)" % self.bind(field)
        if isinstance(value, (int, float)):
            key = self.bind(field)
            return ("(CASE WHEN jsonb_typeof(doc->%s) = 'number' "
                    "THEN cast(doc->>%s as numeric) END)" % (key, key))
        return self.scalar(field)

    def elements(self, field: str) -> str:
        """FROM clause item yielding the value(s) of ``field`` as ``v``."""
        if field in FieldTypes.array_columns:
            return "unnest(%s) AS x(v)" % field
        if FieldTypes.is_column(field):
            return "(SELECT %s) AS x(v)" % field
        key = self.bind(field)
        return ("jsonb_array_elements_text(CASE WHEN jsonb_typeof(doc->%s)"
                " = 'array' THEN doc->%s ELSE jsonb_build_array(doc->%s) END)"
                " AS x(v)" % (key, key, key))

    def compare(self, field: str, op: str, value: Any) -> str:
        return "coalesce(%s %s %s, false)" % (
            self.typed(field, value), op, self.bind(value))

    def equals(self, field: str, value: str) -> str:
        param = self.bind(value)
        if field in FieldTypes.array_columns:
            return "coalesce(%s @> ARRAY[cast(%s as text)], false)" % (
                field, param)
        if FieldTypes.is_column(field):
            return "coalesce(%s = %s, false)" % (field, param)
        key = self.bind(field)
        return ("(doc @> jsonb_build_object(%s, cast(%s as text)) OR "
                "doc @> jsonb_build_object(%s, jsonb_build_array("
                "cast(%s as text))))" % (key, param, key, param))

    def like(self, field: str, pattern: str) -> str:
        param = self.bind(pattern)
        if FieldTypes.is_column(field) and \
                field not in FieldTypes.array_columns:
            return "coalesce(%s LIKE %s, false)" % (field, param)
        return "EXISTS (SELECT 1 FROM %s WHERE x.v LIKE %s)" % (
            self.elements(field), param)

    def exists(self, field: str) -> str:
        if field in FieldTypes.array_columns:
            return "coalesce(cardinality(%s) > 0, false)" % field
        if FieldTypes.is_column(field):
            return "(%s IS NOT NULL)" % field
        key = self.bind(field)
        return "(jsonb_typeof(doc->%s) IN ('string', 'number', 'boolean', " \
               "'array', 'object'))" % key

    # -- ranges ------------------------------------------------------------

    def range(self, node: Range) -> str:
        field = node.field or self.default_field
        self.check_field(field)
        if field == FieldTypes.fulltext_field:
            raise SearchQueryError("Range queries are not supported on the "
                                   "full text field")
        low = self.bound(field, node.low)
        high = self.bound(field, node.high)
        conditions: list[str] = []
        if low is not None:
            conditions.append(self.compare(
                field, ">=" if node.include_low else ">", low))
        if high is not None:
            conditions.append(self.compare(
                field, "<=" if node.include_high else "<", high))
        if not conditions:
            return self.exists(field)
        return "(" + " AND ".join(conditions) + ")"

    def bound(self, field: str, value: Optional[str]) -> Any:
        if value is None:
            return None
        if FieldTypes.is_date(field):
            return parse_date(value, self.now)
        if field in FieldTypes.int_fields:
            return parse_number(value)
        return value

    # -- sorting -----------------------------------------------------------

    def order_by(self, sort: str) -> str:
        clauses: list[str] = []
        for field, direction in parse_sort(sort):
            if field == "score":
                expression = self.rank()
            elif field in FieldTypes.ngram_fields:
                expression = FieldTypes.ngram_fields[field]
            elif FieldTypes.is_column(field):
                expression = field
            else:
                self.check_field(field)
                expression = "(doc->%s)" % self.bind(field)
            clauses.append("%s %s" % (expression, direction.upper()))
        # deterministic pagination
        clauses.append("index_id ASC")
        return ", ".join(clauses)

    # -- facets ------------------------------------------------------------

    def facet_sql(self, field: str, where: str, mincount: int,
                  limit: int) -> str:
        self.check_field(field)
        sql = ("SELECT x.v AS value, count(*) AS n "
               "FROM package_search_index, LATERAL %s "
               "WHERE %s AND x.v IS NOT NULL "
               "GROUP BY x.v HAVING count(*) >= %s "
               "ORDER BY n DESC, x.v ASC" % (
                   self.elements(field), where, self.bind(mincount)))
        if limit >= 0:
            sql += " LIMIT %s" % self.bind(limit)
        return sql


# -- value parsing -----------------------------------------------------------

_DATE_MATH_RE = re.compile(
    r"^NOW(?P<ops>(?:[+\-]\d+(?:MILLI|SECOND|MINUTE|HOUR|DAY|MONTH|YEAR)S?"
    r"|/(?:SECOND|MINUTE|HOUR|DAY|MONTH|YEAR))*)$")
_DATE_MATH_OP_RE = re.compile(
    r"([+\-]\d+(?:MILLI|SECOND|MINUTE|HOUR|DAY|MONTH|YEAR)S?"
    r"|/(?:SECOND|MINUTE|HOUR|DAY|MONTH|YEAR))")


def parse_date(value: str, now: datetime.datetime) -> datetime.datetime:
    """Parse an ISO 8601 date or Solr date math (``NOW-7DAYS/DAY``)."""
    value = value.strip()
    match = _DATE_MATH_RE.match(value)
    if match:
        return _apply_date_math(now, match.group("ops"))
    try:
        from dateutil.parser import isoparse
        parsed = isoparse(value)
    except (ValueError, OverflowError):
        raise SearchQueryError("Invalid date value: %r" % value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(
            tzinfo=None)
    return parsed


def _apply_date_math(now: datetime.datetime, ops: str) -> datetime.datetime:
    result = now
    for op in _DATE_MATH_OP_RE.findall(ops):
        if op.startswith("/"):
            result = _round_down(result, op[1:])
            continue
        sign = 1 if op[0] == "+" else -1
        amount = int(re.match(r"[+\-](\d+)", op).group(1))  # type: ignore
        unit = re.sub(r"^[+\-]\d+", "", op).rstrip("S")
        if unit == "MILLI":
            delta = datetime.timedelta(milliseconds=amount)
        elif unit == "SECOND":
            delta = datetime.timedelta(seconds=amount)
        elif unit == "MINUTE":
            delta = datetime.timedelta(minutes=amount)
        elif unit == "HOUR":
            delta = datetime.timedelta(hours=amount)
        elif unit == "DAY":
            delta = datetime.timedelta(days=amount)
        elif unit == "MONTH":
            result = _add_months(result, sign * amount)
            continue
        else:
            result = _add_months(result, sign * amount * 12)
            continue
        result = result + sign * delta
    return result


def _round_down(value: datetime.datetime, unit: str) -> datetime.datetime:
    if unit == "SECOND":
        return value.replace(microsecond=0)
    if unit == "MINUTE":
        return value.replace(second=0, microsecond=0)
    if unit == "HOUR":
        return value.replace(minute=0, second=0, microsecond=0)
    if unit == "DAY":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "MONTH":
        return value.replace(day=1, hour=0, minute=0, second=0,
                             microsecond=0)
    return value.replace(month=1, day=1, hour=0, minute=0, second=0,
                         microsecond=0)


def _add_months(value: datetime.datetime, months: int) -> datetime.datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def parse_number(value: str) -> Any:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            raise SearchQueryError("Invalid numeric value: %r" % value)


def parse_sort(sort: str) -> list[tuple[str, str]]:
    """Parse ``field asc, other desc``. Raises SearchQueryError."""
    result: list[tuple[str, str]] = []
    for item in sort.split(","):
        parts = item.split()
        if not parts:
            continue
        if len(parts) != 2 or parts[1].lower() not in ("asc", "desc") \
                or not _FIELD_NAME_RE.match(parts[0]):
            raise SearchQueryError('Invalid "sort" parameter')
        result.append((parts[0], parts[1].lower()))
    return result


# -- wildcards ---------------------------------------------------------------

def _has_wildcard(value: str) -> bool:
    return re.search(r"(?<!\\)[*?]", value) is not None


def _wildcard_to_like(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            out.append(_LIKE_ESCAPE_RE.sub(r"\\\1", value[i + 1]))
            i += 2
            continue
        if char == "*":
            out.append("%")
        elif char == "?":
            out.append("_")
        else:
            out.append(_LIKE_ESCAPE_RE.sub(r"\\\1", char))
        i += 1
    return "".join(out)


def _wildcard_to_plain(value: str) -> str:
    return re.sub(r"\\([*?])", r"\1", value).replace("*", "").replace(
        "?", "")
