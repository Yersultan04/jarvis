"""chat_sessions + agent_runs.session_id

Revision ID: 0004_chat_sessions
Revises: 0003_ingest_progress
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_chat_sessions"
down_revision = "0003_ingest_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False, server_default="New chat"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_chat_sessions_rag_id", "chat_sessions", ["rag_id"])

    op.add_column(
        "agent_runs",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_session_id_chat_sessions",
        "agent_runs",
        "chat_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_session_id_chat_sessions", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "session_id")
    op.drop_index("ix_chat_sessions_rag_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
