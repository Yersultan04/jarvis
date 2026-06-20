import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import FileStatus, IngestRunStatus, RagStatus, UserRole


class UserRegister(BaseModel):
    """Used only for the one-shot bootstrap admin registration."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class UserAdminCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: UserRole = UserRole.user


class UserAdminUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class RagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    fts_language: str | None = Field(
        default=None,
        description="Postgres ts_config name. Common: 'simple', 'english', 'russian'.",
    )
    preset: str | None = Field(
        default=None,
        description="Model preset id (cloud / oss / fast / …). When omitted, "
                    "current server env is captured as the snapshot.",
    )


class RagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: RagStatus
    qdrant_collection: str
    embed_model: str
    embed_dim: int
    owner_id: uuid.UUID
    settings: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    # Per-request, populated in handlers — not stored on the model.
    role: str | None = None             # owner | admin | member | none
    member_status: str | None = None    # active | revoked | None


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    status: str
    created_at: datetime
    revoked_at: datetime | None = None
    is_owner: bool = False


class MemberInvite(BaseModel):
    email: EmailStr


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rag_id: uuid.UUID
    filename: str
    mime_type: str | None
    size_bytes: int
    pages: int | None
    status: FileStatus
    error: str | None
    created_at: datetime


class IngestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rag_id: uuid.UUID
    status: IngestRunStatus
    files_total: int
    files_done: int
    chunks_total: int
    error: str | None
    stream_token: str | None = None
    current_file_id: uuid.UUID | None = None
    current_stage: str | None = None
    current_progress: float | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    mode: str = Field(default="hybrid", pattern="^(dense|sparse|hybrid)$")


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    text: str
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str
    hits: list[SearchHit]


class ChunkOut(BaseModel):
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    page_start: int | None
    page_end: int | None
    heading: str | None
    text: str
    token_count: int
