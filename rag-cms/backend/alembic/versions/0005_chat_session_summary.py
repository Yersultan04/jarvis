"""chat_sessions: summary + summary_through_run_id (cached rolling summary)

Revision ID: 0005_chat_summary
Revises: 0004_chat_sessions
Create Date: 2026-05-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_chat_summary"
down_revision = "0004_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "chat_sessions",
        sa.Column("summary_through_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "summary_through_run_id")
    op.drop_column("chat_sessions", "summary")
