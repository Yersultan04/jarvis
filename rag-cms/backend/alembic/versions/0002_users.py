"""users table + rags.owner_id + drop rags.api_key + agent_runs.stream_token

Revision ID: 0002_users
Revises: 0001_initial
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_users"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


_user_role = postgresql.ENUM("user", "admin", name="user_role", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    _user_role.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", _user_role, nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # Add rags.owner_id. If the table already has rows, the migration aborts —
    # in dev that's fine (drop and re-create); in prod, backfill manually first.
    op.add_column("rags", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_rags_owner_id_users",
        "rags",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_rags_owner_id", "rags", ["owner_id"])

    # Drop per-RAG api_key from phase 3 — superseded by user-owned access.
    op.drop_index("ix_rags_api_key", table_name="rags")
    op.drop_column("rags", "api_key")

    op.execute(
        "UPDATE rags SET owner_id = (SELECT id FROM users LIMIT 1) WHERE owner_id IS NULL"
    )
    op.alter_column("rags", "owner_id", nullable=False)

    op.add_column(
        "agent_runs",
        sa.Column("stream_token", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_agent_runs_stream_token", "agent_runs", ["stream_token"])
    op.alter_column("agent_runs", "stream_token", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_agent_runs_stream_token", table_name="agent_runs")
    op.drop_column("agent_runs", "stream_token")

    op.add_column("rags", sa.Column("api_key", sa.String(64), nullable=True))
    op.create_index("ix_rags_api_key", "rags", ["api_key"], unique=True)

    op.drop_index("ix_rags_owner_id", table_name="rags")
    op.drop_constraint("fk_rags_owner_id_users", "rags", type_="foreignkey")
    op.drop_column("rags", "owner_id")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    _user_role.drop(op.get_bind(), checkfirst=True)
