import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RagStatus(str, enum.Enum):
    draft = "draft"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class FileStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    parsed = "parsed"
    failed = "failed"


class IngestRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class AgentRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    escalated = "escalated"
    failed = "failed"


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class MembershipStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.user, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rags: Mapped[list["Rag"]] = relationship(back_populates="owner")
    memberships: Mapped[list["RagMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RagMember(Base):
    __tablename__ = "rag_members"

    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"),
        default=MembershipStatus.active,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rag: Mapped["Rag"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class Rag(Base):
    __tablename__ = "rags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RagStatus] = mapped_column(
        Enum(RagStatus, name="rag_status"), default=RagStatus.draft, nullable=False
    )
    qdrant_collection: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    embed_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner: Mapped["User"] = relationship(back_populates="rags")
    files: Mapped[list["File"]] = relationship(back_populates="rag", cascade="all, delete-orphan")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="rag", cascade="all, delete-orphan")
    runs: Mapped[list["IngestRun"]] = relationship(
        back_populates="rag", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="rag", cascade="all, delete-orphan"
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="rag", cascade="all, delete-orphan"
    )
    members: Mapped[list["RagMember"]] = relationship(
        back_populates="rag", cascade="all, delete-orphan"
    )


class File(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, name="file_status"), default=FileStatus.uploaded, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rag: Mapped["Rag"] = relationship(back_populates="files")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rag: Mapped["Rag"] = relationship(back_populates="chunks")
    file: Mapped["File"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_rag_file_idx", "rag_id", "file_id", "chunk_index"),
    )


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[IngestRunStatus] = mapped_column(
        Enum(IngestRunStatus, name="ingest_run_status"),
        default=IngestRunStatus.queued,
        nullable=False,
    )
    files_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    stream_token: Mapped[str | None] = mapped_column(String(64), index=True)
    current_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    current_stage: Mapped[str | None] = mapped_column(String(32))
    current_progress: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rag: Mapped["Rag"] = relationship(back_populates="runs")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New chat")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cached rolling summary of older turns (everything except the last N).
    # `summary_through_run_id` is the latest run.id included in `summary` — we
    # regenerate when more turns drop out of the recent window.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_through_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rag: Mapped["Rag"] = relationship(back_populates="chat_sessions")
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"),
        default=AgentRunStatus.queued,
        nullable=False,
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    telemetry: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    steps_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, default=40, nullable=False)
    stream_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rag: Mapped["Rag"] = relationship(back_populates="agent_runs")
    session: Mapped["ChatSession | None"] = relationship(back_populates="runs")
