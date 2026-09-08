# encoding: utf-8
"""Table used by the PostgreSQL search backend.

One row per indexed dataset. The typed columns are the fields CKAN core
filters, sorts or facets on all the time; everything else lives in ``doc``
(the complete search document as a JSON object) and ``fts`` holds the
weighted full text vector.
"""
from __future__ import annotations

from sqlalchemy import types, Column, Table, Index
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

from ckan.model import meta

__all__ = ["package_search_index_table"]

package_search_index_table = Table(
    "package_search_index",
    meta.metadata,
    Column("index_id", types.UnicodeText, primary_key=True),
    Column("id", types.UnicodeText, nullable=False),
    Column("site_id", types.UnicodeText, nullable=False),
    Column("entity_type", types.UnicodeText),
    Column("dataset_type", types.UnicodeText),
    Column("name", types.UnicodeText),
    Column("title", types.UnicodeText),
    Column("title_string", types.UnicodeText),
    Column("state", types.UnicodeText),
    Column("capacity", types.UnicodeText),
    Column("organization", types.UnicodeText),
    Column("metadata_created", types.DateTime),
    Column("metadata_modified", types.DateTime),
    Column("indexed_ts", types.DateTime),
    Column("permission_labels", ARRAY(types.UnicodeText)),
    Column("tags", ARRAY(types.UnicodeText)),
    Column("groups", ARRAY(types.UnicodeText)),
    Column("res_format", ARRAY(types.UnicodeText)),
    Column("data_dict", types.UnicodeText),
    Column("validated_data_dict", types.UnicodeText),
    Column("doc", JSONB),
    Column("fts", TSVECTOR),
    Index("idx_package_search_index_site_id_id", "site_id", "id"),
    Index("idx_package_search_index_site_id_name", "site_id", "name"),
    Index("idx_package_search_index_metadata_modified",
          "metadata_modified"),
    Index("idx_package_search_index_fts", "fts", postgresql_using="gin"),
    Index("idx_package_search_index_doc", "doc", postgresql_using="gin",
          postgresql_ops={"doc": "jsonb_path_ops"}),
    Index("idx_package_search_index_tags", "tags", postgresql_using="gin"),
    Index("idx_package_search_index_groups", "groups",
          postgresql_using="gin"),
    Index("idx_package_search_index_res_format", "res_format",
          postgresql_using="gin"),
    Index("idx_package_search_index_permission_labels",
          "permission_labels", postgresql_using="gin"),
)
