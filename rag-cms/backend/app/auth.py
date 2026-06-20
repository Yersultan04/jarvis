from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import MembershipStatus, Rag, RagMember, User, UserRole


# ---------------- password hashing ----------------


def _to_bcrypt_bytes(password: str) -> bytes:
    # bcrypt rejects inputs longer than 72 bytes since 5.0; truncate so very long
    # passphrases still work consistently (entropy is bounded by 72 bytes anyway).
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(password), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------- JWT ----------------


def issue_token(user: User) -> tuple[str, datetime]:
    s = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(hours=s.jwt_ttl_hours)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, expires


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="token expired") from e
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}") from e


# ---------------- one-shot tokens for SSE ----------------


def new_stream_token() -> str:
    return secrets.token_urlsafe(24)


# ---------------- FastAPI dependencies ----------------


async def current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(status_code=401, detail="malformed token subject") from e
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="user not found or inactive")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


async def _membership(
    db: AsyncSession, rag_id: uuid.UUID, user_id: uuid.UUID
) -> RagMember | None:
    return (
        await db.execute(
            select(RagMember).where(RagMember.rag_id == rag_id, RagMember.user_id == user_id)
        )
    ).scalar_one_or_none()


async def get_owned_rag(
    rag_id: uuid.UUID = Path(..., description="RAG id"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Rag:
    """Strict guard — only the owner or a platform admin passes.

    Used for files / ingestion / settings / members management.
    """
    rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
    if rag is None:
        raise HTTPException(status_code=404, detail="rag not found")
    if rag.owner_id == user.id or user.role == UserRole.admin:
        return rag
    raise HTTPException(status_code=403, detail="owner-only operation")


async def get_accessible_rag(
    rag_id: uuid.UUID = Path(..., description="RAG id"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Rag:
    """Owner, admin, or an ACTIVE member. Used for asking the agent,
    creating chat sessions, and other write-ish "use the RAG" operations."""
    rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
    if rag is None:
        raise HTTPException(status_code=404, detail="rag not found")
    if rag.owner_id == user.id or user.role == UserRole.admin:
        return rag
    m = await _membership(db, rag.id, user.id)
    if m is not None and m.status == MembershipStatus.active:
        return rag
    if m is not None and m.status == MembershipStatus.revoked:
        raise HTTPException(status_code=403, detail="your access to this RAG was revoked")
    raise HTTPException(status_code=403, detail="not your RAG")


async def get_visible_rag(
    rag_id: uuid.UUID = Path(..., description="RAG id"),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Rag:
    """Owner, admin, or ANY membership (even revoked) — read-only paths.

    Lets a revoked member still view their old chat history but not act on it.
    Write operations gate with `get_accessible_rag`.
    """
    rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
    if rag is None:
        raise HTTPException(status_code=404, detail="rag not found")
    if rag.owner_id == user.id or user.role == UserRole.admin:
        return rag
    if (await _membership(db, rag.id, user.id)) is not None:
        return rag
    raise HTTPException(status_code=403, detail="not your RAG")


async def role_in_rag(db: AsyncSession, rag: Rag, user: User) -> tuple[str, str | None]:
    """Returns (role, member_status) where role ∈ {'owner','admin','member','none'}
    and member_status ∈ {'active','revoked', None}."""
    if user.role == UserRole.admin and rag.owner_id != user.id:
        return ("admin", None)
    if rag.owner_id == user.id:
        return ("owner", None)
    m = await _membership(db, rag.id, user.id)
    if m is None:
        return ("none", None)
    return ("member", m.status.value)
