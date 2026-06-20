"""ingest_runs: stream_token + current_file_id + current_stage + current_progress

Revision ID: 0003_ingest_progress
Revises: 0002_users
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_ingest_progress"
down_revision = "0002_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingest_runs", sa.Column("stream_token", sa.String(64), nullable=True))
    op.create_index("ix_ingest_runs_stream_token", "ingest_runs", ["stream_token"])
    op.add_column(
        "ingest_runs",
        sa.Column("current_file_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("ingest_runs", sa.Column("current_stage", sa.String(32), nullable=True))
    op.add_column("ingest_runs", sa.Column("current_progress", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingest_runs", "current_progress")
    op.drop_column("ingest_runs", "current_stage")
    op.drop_column("ingest_runs", "current_file_id")
    op.drop_index("ix_ingest_runs_stream_token", table_name="ingest_runs")
    op.drop_column("ingest_runs", "stream_token")
