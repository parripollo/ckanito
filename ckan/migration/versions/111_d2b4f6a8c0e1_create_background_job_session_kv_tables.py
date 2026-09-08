# -*- coding: utf-8 -*-
"""Create background_job, session_store and kv_store tables

Revision ID: d2b4f6a8c0e1
Revises: c1a3e5f7b9d2
Create Date: 2026-09-08 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "d2b4f6a8c0e1"
down_revision = "c1a3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_job",
        sa.Column("id", sa.UnicodeText, primary_key=True),
        sa.Column("queue", sa.UnicodeText, nullable=False),
        sa.Column("func", sa.UnicodeText, nullable=False),
        sa.Column("args", sa.LargeBinary),
        sa.Column("kwargs", sa.LargeBinary),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("timeout", sa.Integer),
        sa.Column("status", sa.UnicodeText, nullable=False),
        sa.Column("scheduled_at", sa.DateTime, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("ended_at", sa.DateTime),
        sa.Column("worker", sa.UnicodeText),
        sa.Column("error", sa.UnicodeText),
    )
    op.create_index("idx_background_job_queue", "background_job",
                    ["status", "queue", "scheduled_at"])

    op.create_table(
        "session_store",
        sa.Column("id", sa.UnicodeText, primary_key=True),
        sa.Column("data", sa.LargeBinary, nullable=False),
        sa.Column("expiry", sa.DateTime),
    )
    op.create_index("idx_session_store_expiry", "session_store", ["expiry"])

    op.create_table(
        "kv_store",
        sa.Column("key", sa.UnicodeText, primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("expires_at", sa.DateTime),
    )
    op.create_index("idx_kv_store_expires_at", "kv_store", ["expires_at"])


def downgrade():
    op.drop_table("kv_store")
    op.drop_table("session_store")
    op.drop_table("background_job")
