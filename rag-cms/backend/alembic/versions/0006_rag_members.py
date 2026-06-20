"""rag_members + chat_sessions.user_id (per-user shared access)

Revision ID: 0006_rag_members
Revises: 0005_chat_summary
Create Date: 2026-05-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_rag_members"
down_revision = "0005_chat_summary"
branch_labels = None
depends_on = None


_membership_status = postgresql.ENUM(
    "active", "revoked", name="membership_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    _membership_status.create(bind, checkfirst=True)

    op.create_table(
        "rag_members",
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", _membership_status, nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_rag_members_user_id", "rag_members", ["user_id"])

    # chat_sessions.user_id — author of the session. Add nullable, backfill,
    # then enforce NOT NULL.
    op.add_column(
        "chat_sessions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE chat_sessions cs SET user_id = r.owner_id "
        "FROM rags r WHERE r.id = cs.rag_id AND cs.user_id IS NULL"
    )
    op.alter_column("chat_sessions", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_chat_sessions_user_id_users",
        "chat_sessions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_constraint("fk_chat_sessions_user_id_users", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "user_id")
    op.drop_index("ix_rag_members_user_id", table_name="rag_members")
    op.drop_table("rag_members")
    _membership_status.drop(op.get_bind(), checkfirst=True)
