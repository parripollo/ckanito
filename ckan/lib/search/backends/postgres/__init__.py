# encoding: utf-8
"""PostgreSQL full text search backend.

Datasets are indexed in the ``package_search_index`` table of the CKAN
database (see :mod:`ckan.model.package_search_index`): a few typed
columns for the fields CKAN filters and sorts on all the time, the whole
search document as JSONB and a weighted ``tsvector`` for full text
search. Queries written in the Lucene subset accepted by
``package_search`` are parsed by :mod:`.lucene` and compiled to SQL by
:mod:`.compiler`.

No extra services are needed: everything runs inside PostgreSQL.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import exc as sa_exc

from ckan.common import config
import ckan.model as model
from ckan.lib.search.backends.base import SearchBackend, SearchResponse
from ckan.lib.search.common import (
    SearchConnectionError, SearchError, SearchIndexError, SearchQueryError,
)
from ckan.lib.search.backends.postgres.compiler import (
    Compiler, FieldTypes,
)

__all__ = ["PostgresSearchBackend", "TABLE"]

log = logging.getLogger(__name__)

TABLE = "package_search_index"

DEFAULT_SORT = "score desc"

_TEXT_CONFIG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAM_RE = re.compile(r"(?<![:\w\\]):([A-Za-z_]\w*)(?!:)")

# fields copied into the full text vector, by weight (mirrors the
# copy rules of CKAN's historical search schema)
_FTS_A = ("name", "title")
_FTS_B = ("tags", "groups", "organization")
_FTS_C = ("notes", "res_name", "res_description")
_FTS_D = ("url", "ckan_url", "download_url", "res_url", "license",
          "license_id", "license_title", "author", "maintainer", "text",
          "urls")
_FTS_D_PREFIXES = ("extras_", "res_extras_", "vocab_")

_COLUMNS = (
    "index_id", "id", "site_id", "entity_type", "dataset_type", "name",
    "title", "title_string", "state", "capacity", "organization",
    "metadata_created", "metadata_modified", "indexed_ts",
    "permission_labels", "tags", "groups", "res_format",
    "data_dict", "validated_data_dict", "doc", "fts",
)

_FTS_SQL = (
    "setweight(to_tsvector(cast(:cfg as regconfig), :fts_a), 'A') || "
    "setweight(to_tsvector(cast(:cfg as regconfig), :fts_b), 'B') || "
    "setweight(to_tsvector(cast(:cfg as regconfig), :fts_c), 'C') || "
    "setweight(to_tsvector(cast(:cfg as regconfig), :fts_d), 'D')"
)

_INSERT_SQL = (
    "INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (index_id) DO UPDATE SET %s"
    % (
        TABLE,
        ", ".join(_COLUMNS),
        ", ".join(
            "cast(:doc as jsonb)" if column == "doc"
            else _FTS_SQL if column == "fts"
            else ":" + column
            for column in _COLUMNS),
        ", ".join("%s = EXCLUDED.%s" % (column, column)
                  for column in _COLUMNS if column != "index_id"),
    )
)


def _params_for(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    names = set(_PARAM_RE.findall(sql))
    return {name: value for name, value in params.items() if name in names}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item is not None)
    return str(value)


def _as_datetime(value: Any) -> Optional[datetime.datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        parsed = value
    else:
        try:
            from dateutil.parser import isoparse
            parsed = isoparse(str(value))
        except (ValueError, OverflowError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(
            tzinfo=None)
    return parsed


class PostgresSearchBackend(SearchBackend):
    name = "postgres"

    # -- infrastructure ----------------------------------------------------

    @property
    def engine(self) -> sa.engine.Engine:
        engine = model.meta.engine
        if engine is None:
            raise SearchConnectionError("The database engine is not ready")
        return engine

    def text_config(self) -> str:
        value = config.get("ckan.search.postgres.text_config") or "ckan_english"
        if not _TEXT_CONFIG_RE.match(value):
            raise SearchError(
                "Invalid ckan.search.postgres.text_config: %r" % value)
        return value

    def table_exists(self) -> bool:
        if model.meta.engine is None:
            # plugins are loaded (and the schema checked) before the
            # database engine is configured
            return False
        try:
            return sa.inspect(self.engine).has_table(TABLE)
        except sa_exc.SQLAlchemyError as e:
            log.warning("Could not check the search index table: %s", e)
            return False

    def execute(self, sql: str, params: dict[str, Any]) -> Any:
        try:
            with self.engine.begin() as conn:
                result = conn.execute(sa.text(sql), _params_for(sql, params))
                if result.returns_rows:
                    return result.mappings().all()
                return result.rowcount
        except sa_exc.OperationalError as e:
            log.warning("Search index database error: %s", e)
            raise SearchConnectionError(
                "Could not connect to the search index database")

    # -- availability ------------------------------------------------------

    def is_available(self) -> bool:
        return self.table_exists()

    def check_schema(self, schema_file: Optional[str] = None) -> bool:
        if not self.table_exists():
            log.warning(
                "The %s table does not exist yet: run 'ckan db upgrade' "
                "and then 'ckan search-index rebuild'", TABLE)
            return False
        return True

    # -- writing -----------------------------------------------------------

    def index(self, doc: dict[str, Any], defer_commit: bool = False) -> None:
        try:
            self.execute(_INSERT_SQL, self.row(doc))
        except SearchConnectionError as e:
            raise SearchIndexError(str(e))
        except (sa_exc.SQLAlchemyError, ValueError, TypeError) as e:
            log.exception(e)
            raise SearchIndexError(
                "Could not index dataset %s: %s" % (doc.get("id"), e))

    def row(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc = dict(doc)
        data_dict = doc.pop("data_dict", None)
        validated_data_dict = doc.pop("validated_data_dict", None)
        permission_labels = _as_list(doc.pop("permission_labels", None))

        fts: dict[str, list[str]] = {"a": [], "b": [], "c": [], "d": []}
        for key, value in doc.items():
            text = _as_text(value)
            if not text:
                continue
            if key in _FTS_A:
                fts["a"].append(text)
            elif key in _FTS_B:
                fts["b"].append(text)
            elif key in _FTS_C:
                fts["c"].append(text)
            elif key in _FTS_D or key.startswith(_FTS_D_PREFIXES):
                fts["d"].append(text)

        return {
            "index_id": str(doc["index_id"]),
            "id": str(doc["id"]),
            "site_id": str(doc.get("site_id") or config.get("ckan.site_id")),
            "entity_type": _as_text(doc.get("entity_type")),
            "dataset_type": _as_text(doc.get("dataset_type")),
            "name": _as_text(doc.get("name")),
            "title": _as_text(doc.get("title")),
            "title_string": _as_text(doc.get("title_string")),
            "state": _as_text(doc.get("state")),
            "capacity": _as_text(doc.get("capacity")),
            "organization": _as_text(doc.get("organization")),
            "metadata_created": _as_datetime(doc.get("metadata_created")),
            "metadata_modified": _as_datetime(doc.get("metadata_modified")),
            "indexed_ts": datetime.datetime.utcnow(),
            "permission_labels": permission_labels,
            "tags": _as_list(doc.get("tags")),
            "groups": _as_list(doc.get("groups")),
            "res_format": _as_list(doc.get("res_format")),
            "data_dict": data_dict,
            "validated_data_dict": validated_data_dict,
            "doc": json.dumps(doc, default=str),
            "cfg": self.text_config(),
            "fts_a": " ".join(fts["a"]),
            "fts_b": " ".join(fts["b"]),
            "fts_c": " ".join(fts["c"]),
            "fts_d": " ".join(fts["d"]),
        }

    def delete(self, reference: str, site_id: str) -> None:
        sql = ("DELETE FROM %s WHERE site_id = :site_id AND "
               "entity_type = 'package' AND (id = :ref OR name = :ref)"
               % TABLE)
        try:
            self.execute(sql, {"site_id": site_id, "ref": reference})
        except (SearchConnectionError, sa_exc.SQLAlchemyError) as e:
            log.exception(e)
            raise SearchIndexError(str(e))

    def commit(self) -> None:
        # every write is committed on its own
        pass

    def clear(self, site_id: str) -> None:
        if not self.table_exists():
            # e.g. right after `ckan db clean` dropped every table
            log.debug("Search index table missing, nothing to clear")
            return
        try:
            self.execute("DELETE FROM %s WHERE site_id = :site_id" % TABLE,
                         {"site_id": site_id})
        except (SearchConnectionError, sa_exc.SQLAlchemyError) as e:
            log.exception(e)
            raise SearchIndexError(str(e))

    # -- reading -----------------------------------------------------------

    def search(self, params: dict[str, Any]) -> SearchResponse:
        compiler = Compiler(self.text_config(),
                            default_field=params.get("df") or "text")
        where = self.where_clause(compiler, params)
        order_by = compiler.order_by(params.get("sort") or DEFAULT_SORT)

        try:
            rows = int(params.get("rows", 10))
            start = int(params.get("start", 0) or 0)
        except (TypeError, ValueError):
            raise SearchQueryError("Invalid rows/start parameters")

        fields = _split_fields(params.get("fl") or "name")
        select_items = self.select_items(compiler, fields)

        try:
            count_rows = self.execute(
                "SELECT count(*) AS n FROM %s WHERE %s" % (TABLE, where),
                compiler.params)
            count = int(count_rows[0]["n"])

            docs: list[dict[str, Any]] = []
            if rows > 0:
                sql = ("SELECT %s FROM %s WHERE %s ORDER BY %s "
                       "LIMIT %s OFFSET %s" % (
                           ", ".join(select_items), TABLE, where, order_by,
                           compiler.bind(rows), compiler.bind(start)))
                for row in self.execute(sql, compiler.params):
                    docs.append(self.doc_from_row(row, fields))

            facets = self.facets(compiler, params, where)
        except (sa_exc.DataError, sa_exc.ProgrammingError) as e:
            log.debug("Search query failed: %s", e)
            raise SearchError("The search backend returned an error running "
                              "the query: %s" % _short_error(e))

        return SearchResponse(count=count, docs=docs, facets=facets)

    def where_clause(self, compiler: Compiler,
                     params: dict[str, Any]) -> str:
        conditions: list[str] = []

        q = params.get("q")
        if isinstance(q, str) and q.strip() and q.strip() != "*:*":
            if ":" in q or params.get("defType") == "edismax":
                conditions.append(compiler.where(q))
            else:
                conditions.append(compiler.freetext(q))

        fq = params.get("fq") or []
        if isinstance(fq, str):
            fq = [fq]
        for clause in fq:
            if isinstance(clause, str) and clause.strip():
                conditions.append(compiler.where(clause))

        return " AND ".join(conditions) if conditions else "TRUE"

    def select_items(self, compiler: Compiler,
                     fields: list[str]) -> list[str]:
        items: list[str] = []
        for position, field in enumerate(fields):
            if field == "*":
                items.append(
                    "doc AS f%d, data_dict AS f%d_data, "
                    "validated_data_dict AS f%d_validated" % (
                        position, position, position))
            elif field == "score":
                items.append("%s AS f%d" % (compiler.rank(), position))
            elif FieldTypes.is_column(field):
                items.append("%s AS f%d" % (field, position))
            else:
                compiler.check_field(field)
                items.append("(doc->%s) AS f%d" % (
                    compiler.bind(field), position))
        return items

    def facets(self, compiler: Compiler, params: dict[str, Any],
               where: str) -> dict[str, dict[str, int]]:
        facets: dict[str, dict[str, int]] = {}
        if not _is_true(params.get("facet", "true")):
            return facets
        mincount = int(params.get("facet.mincount", 1) or 0)
        limit = int(params.get("facet.limit", 50))
        for field in _split_fields(params.get("facet.field") or []):
            sql = compiler.facet_sql(field, where, mincount, limit)
            facets[field] = {
                str(row["value"]): int(row["n"])
                for row in self.execute(sql, compiler.params)}
        return facets

    def doc_from_row(self, row: Any, fields: list[str]) -> dict[str, Any]:
        doc: dict[str, Any] = {}
        for position, field in enumerate(fields):
            key = "f%d" % position
            if field == "*":
                doc.update(row[key] or {})
                if row[key + "_data"] is not None:
                    doc["data_dict"] = row[key + "_data"]
                if row[key + "_validated"] is not None:
                    doc["validated_data_dict"] = row[key + "_validated"]
            else:
                value = row[key]
                if isinstance(value, datetime.datetime):
                    value = value.isoformat() + "Z"
                doc[field] = value
        return doc

    def get_by_reference(
            self, reference: Any, site_id: str) -> Optional[dict[str, Any]]:
        if not isinstance(reference, str):
            # e.g. a Row from ``Session.query(Package.id)``
            try:
                reference = str(reference[0])
            except (TypeError, IndexError, KeyError):
                reference = str(reference)
        sql = ("SELECT doc, data_dict, validated_data_dict FROM %s "
               "WHERE site_id = :site_id AND entity_type = 'package' AND "
               "(id = :ref OR name = :ref) LIMIT 1" % TABLE)
        rows = self.execute(sql, {"site_id": site_id, "ref": reference})
        if not rows:
            return None
        doc = dict(rows[0]["doc"] or {})
        if rows[0]["data_dict"] is not None:
            doc["data_dict"] = rows[0]["data_dict"]
        if rows[0]["validated_data_dict"] is not None:
            doc["validated_data_dict"] = rows[0]["validated_data_dict"]
        return doc

    def get_all_entity_ids(
            self, site_id: str, max_results: int = 1000) -> list[str]:
        sql = ("SELECT id FROM %s WHERE site_id = :site_id AND "
               "state = 'active' LIMIT :limit" % TABLE)
        rows = self.execute(sql, {"site_id": site_id,
                                  "limit": max(int(max_results), 0)})
        return [row["id"] for row in rows]


def _split_fields(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,]+", value) if item]
    return [str(item) for item in value]


def _is_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() not in ("false", "0", "off", "no", "")
    return bool(value)


def _short_error(error: Exception) -> str:
    text = str(getattr(error, "orig", error)).strip().splitlines()
    return text[0] if text else str(error)
