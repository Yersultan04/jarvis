"""initial schema: rags, files, chunks, ingest_runs, agent_runs

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


# create_type=False — Enum type is created explicitly via .create() in upgrade().
# Without this, op.create_table() would auto-emit CREATE TYPE again and fail.
_rag_status = postgresql.ENUM(
    "draft", "indexing", "ready", "failed", name="rag_status", create_type=False
)
_file_status = postgresql.ENUM(
    "uploaded", "parsing", "parsed", "failed", name="file_status", create_type=False
)
_ingest_run_status = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", name="ingest_run_status", create_type=False
)
_agent_run_status = postgresql.ENUM(
    "queued", "running", "succeeded", "escalated", "failed",
    name="agent_run_status", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _rag_status.create(bind, checkfirst=True)
    _file_status.create(bind, checkfirst=True)
    _ingest_run_status.create(bind, checkfirst=True)
    _agent_run_status.create(bind, checkfirst=True)

    op.create_table(
        "rags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", _rag_status, nullable=False),
        sa.Column("qdrant_collection", sa.String(255), nullable=False, unique=True),
        sa.Column("embed_model", sa.String(255), nullable=False),
        sa.Column("embed_dim", sa.Integer(), nullable=False),
        sa.Column("api_key", sa.String(64), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rags_api_key", "rags", ["api_key"], unique=True)

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("pages", sa.Integer()),
        sa.Column("status", _file_status, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_files_rag_id", "files", ["rag_id"])
    op.create_index("ix_files_sha256", "files", ["sha256"])

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer()),
        sa.Column("page_end", sa.Integer()),
        sa.Column("heading", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("qdrant_point_id", sa.String(64)),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chunks_rag_id", "chunks", ["rag_id"])
    op.create_index("ix_chunks_file_id", "chunks", ["file_id"])
    op.create_index("ix_chunks_rag_file_idx", "chunks", ["rag_id", "file_id", "chunk_index"])

    op.create_table(
        "ingest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", _ingest_run_status, nullable=False),
        sa.Column("files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ingest_runs_rag_id", "ingest_runs", ["rag_id"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", _agent_run_status, nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float()),
        sa.Column("telemetry", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("steps_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_agent_runs_rag_id", "agent_runs", ["rag_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_rag_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_ingest_runs_rag_id", table_name="ingest_runs")
    op.drop_table("ingest_runs")
    op.drop_index("ix_chunks_rag_file_idx", table_name="chunks")
    op.drop_index("ix_chunks_file_id", table_name="chunks")
    op.drop_index("ix_chunks_rag_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_files_sha256", table_name="files")
    op.drop_index("ix_files_rag_id", table_name="files")
    op.drop_table("files")
    op.drop_index("ix_rags_api_key", table_name="rags")
    op.drop_table("rags")

    bind = op.get_bind()
    _agent_run_status.drop(bind, checkfirst=True)
    _ingest_run_status.drop(bind, checkfirst=True)
    _file_status.drop(bind, checkfirst=True)
    _rag_status.drop(bind, checkfirst=True)
