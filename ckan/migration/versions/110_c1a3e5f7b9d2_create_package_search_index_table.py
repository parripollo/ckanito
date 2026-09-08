# -*- coding: utf-8 -*-
"""Create package_search_index table (PostgreSQL search backend)

Revision ID: c1a3e5f7b9d2
Revises: 9445ce34fc23
Create Date: 2026-09-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c1a3e5f7b9d2"
down_revision = "9445ce34fc23"
branch_labels = None
depends_on = None


def upgrade():
    # English stemming without stop words, like CKAN always searched. Text
    # search objects have no IF NOT EXISTS, so check the catalog first.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_ts_dict WHERE dictname = 'ckan_english_stem'
            ) THEN
                CREATE TEXT SEARCH DICTIONARY ckan_english_stem (
                    TEMPLATE = snowball, Language = english);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_ts_config WHERE cfgname = 'ckan_english'
            ) THEN
                CREATE TEXT SEARCH CONFIGURATION ckan_english (
                    COPY = english);
                ALTER TEXT SEARCH CONFIGURATION ckan_english
                    ALTER MAPPING FOR asciiword, asciihword,
                        hword_asciipart, word, hword, hword_part
                    WITH ckan_english_stem;
            END IF;
        END
        $$;
    """)
    op.create_table(
        "package_search_index",
        sa.Column("index_id", sa.UnicodeText, primary_key=True),
        sa.Column("id", sa.UnicodeText, nullable=False),
        sa.Column("site_id", sa.UnicodeText, nullable=False),
        sa.Column("entity_type", sa.UnicodeText),
        sa.Column("dataset_type", sa.UnicodeText),
        sa.Column("name", sa.UnicodeText),
        sa.Column("title", sa.UnicodeText),
        sa.Column("title_string", sa.UnicodeText),
        sa.Column("state", sa.UnicodeText),
        sa.Column("capacity", sa.UnicodeText),
        sa.Column("organization", sa.UnicodeText),
        sa.Column("metadata_created", sa.DateTime),
        sa.Column("metadata_modified", sa.DateTime),
        sa.Column("indexed_ts", sa.DateTime),
        sa.Column("permission_labels", postgresql.ARRAY(sa.UnicodeText)),
        sa.Column("tags", postgresql.ARRAY(sa.UnicodeText)),
        sa.Column("groups", postgresql.ARRAY(sa.UnicodeText)),
        sa.Column("res_format", postgresql.ARRAY(sa.UnicodeText)),
        sa.Column("data_dict", sa.UnicodeText),
        sa.Column("validated_data_dict", sa.UnicodeText),
        sa.Column("doc", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("fts", postgresql.TSVECTOR),
    )
    op.create_index("idx_package_search_index_site_id_id",
                    "package_search_index", ["site_id", "id"])
    op.create_index("idx_package_search_index_site_id_name",
                    "package_search_index", ["site_id", "name"])
    op.create_index("idx_package_search_index_metadata_modified",
                    "package_search_index", ["metadata_modified"])
    op.create_index("idx_package_search_index_fts",
                    "package_search_index", ["fts"], postgresql_using="gin")
    op.create_index("idx_package_search_index_doc",
                    "package_search_index", ["doc"], postgresql_using="gin",
                    postgresql_ops={"doc": "jsonb_path_ops"})
    for column in ("tags", "groups", "res_format", "permission_labels"):
        op.create_index("idx_package_search_index_%s" % column,
                        "package_search_index", [column],
                        postgresql_using="gin")


def downgrade():
    op.drop_table("package_search_index")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS ckan_english")
    op.execute("DROP TEXT SEARCH DICTIONARY IF EXISTS ckan_english_stem")
