from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user, get_owned_rag, role_in_rag
from app.clients import qdrant as qdrant_client
from app.config import get_settings
from app.db import get_db
from app.models import MembershipStatus, Rag, RagMember, RagStatus, User, UserRole
from app.presets import get_preset, list_presets, snapshot_from_env, snapshot_from_preset
from app.schemas import MemberInvite, MemberOut, RagCreate, RagOut

router = APIRouter()


async def _decorate(db: AsyncSession, rag: Rag, user: User) -> RagOut:
    out = RagOut.model_validate(rag)
    role, status = await role_in_rag(db, rag, user)
    out.role = role
    out.member_status = status
    return out


@router.get("/_presets")
async def get_presets() -> list[dict]:
    """List available model presets the UI can surface in the create-RAG dialog."""
    return list_presets()


@router.post("", response_model=RagOut, status_code=201)
async def create_rag(
    payload: RagCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RagOut:
    s = get_settings()
    rag_id = uuid.uuid4()
    collection = qdrant_client.collection_name(rag_id)
    fts_language = (payload.fts_language or s.default_fts_language).strip().lower() or "simple"

    if payload.preset:
        if get_preset(payload.preset) is None:
            raise HTTPException(400, f"unknown preset '{payload.preset}'")
        snapshot = snapshot_from_preset(payload.preset)
    else:
        snapshot = snapshot_from_env()

    rag = Rag(
        id=rag_id,
        name=payload.name,
        description=payload.description,
        status=RagStatus.draft,
        qdrant_collection=collection,
        embed_model=snapshot["embed_model"],
        embed_dim=int(snapshot["embed_dim"]),
        owner_id=user.id,
        settings={"fts_language": fts_language, "models": snapshot},
    )
    db.add(rag)
    await db.commit()
    await db.refresh(rag)

    await qdrant_client.ensure_collection(rag.id, rag.embed_dim)
    (s.data_dir / "rags" / str(rag.id) / "files").mkdir(parents=True, exist_ok=True)
    return await _decorate(db, rag, user)


@router.get("", response_model=list[RagOut])
async def list_rags(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[RagOut]:
    """Returns every RAG the current user can see:
       owner / admin / member (active or revoked)."""
    stmt = select(Rag).order_by(Rag.created_at.desc())
    if user.role != UserRole.admin:
        member_rag_ids = select(RagMember.rag_id).where(RagMember.user_id == user.id)
        stmt = stmt.where(or_(Rag.owner_id == user.id, Rag.id.in_(member_rag_ids)))
    rows = (await db.execute(stmt)).scalars().all()
    out: list[RagOut] = []
    for r in rows:
        out.append(await _decorate(db, r, user))
    return out


@router.get("/{rag_id}", response_model=RagOut)
async def get_rag_detail(
    rag_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RagOut:
    rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
    if rag is None:
        raise HTTPException(404, "rag not found")
    role, _ = await role_in_rag(db, rag, user)
    if role == "none":
        raise HTTPException(403, "not your RAG")
    return await _decorate(db, rag, user)


@router.patch("/{rag_id}/settings", response_model=RagOut)
async def update_rag_settings(
    payload: dict = Body(...),
    rag: Rag = Depends(get_owned_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> RagOut:
    """Owner-only. Whitelisted keys: web_search_enabled, fts_language."""
    ALLOWED = {"web_search_enabled", "fts_language"}
    current = dict(rag.settings or {})
    for k, v in (payload or {}).items():
        if k not in ALLOWED:
            continue
        if k == "web_search_enabled":
            current[k] = bool(v)
        elif k == "fts_language":
            current[k] = str(v).strip().lower()[:32] or "simple"
    rag.settings = current
    await db.commit()
    await db.refresh(rag)
    return await _decorate(db, rag, user)


@router.delete("/{rag_id}", status_code=204)
async def delete_rag(
    rag: Rag = Depends(get_owned_rag), db: AsyncSession = Depends(get_db)
) -> None:
    await qdrant_client.delete_collection(rag.id)
    await db.delete(rag)
    await db.commit()


# ---------------- members ----------------


@router.get("/{rag_id}/members", response_model=list[MemberOut])
async def list_members(
    rag: Rag = Depends(get_owned_rag), db: AsyncSession = Depends(get_db)
) -> list[MemberOut]:
    rows = (
        await db.execute(
            select(RagMember, User)
            .join(User, User.id == RagMember.user_id)
            .where(RagMember.rag_id == rag.id)
            .order_by(RagMember.created_at.asc())
        )
    ).all()
    owner_user = (
        await db.execute(select(User).where(User.id == rag.owner_id))
    ).scalar_one_or_none()

    out: list[MemberOut] = []
    if owner_user is not None:
        out.append(
            MemberOut(
                user_id=owner_user.id,
                email=owner_user.email,
                status="active",
                created_at=owner_user.created_at,  # not the membership date, but informative
                revoked_at=None,
                is_owner=True,
            )
        )
    for m, u in rows:
        out.append(
            MemberOut(
                user_id=u.id,
                email=u.email,
                status=m.status.value,
                created_at=m.created_at,
                revoked_at=m.revoked_at,
                is_owner=False,
            )
        )
    return out


@router.post("/{rag_id}/members", response_model=MemberOut, status_code=201)
async def invite_member(
    payload: MemberInvite,
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    email = payload.email.lower().strip()
    target = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            404, f"user with email {email!r} doesn't exist; ask an admin to create the account first"
        )
    if target.id == rag.owner_id:
        raise HTTPException(409, "user is already the owner of this RAG")

    existing = (
        await db.execute(
            select(RagMember).where(
                RagMember.rag_id == rag.id, RagMember.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Reactivate if previously revoked, otherwise no-op.
        if existing.status == MembershipStatus.revoked:
            existing.status = MembershipStatus.active
            existing.revoked_at = None
            await db.commit()
            await db.refresh(existing)
        m = existing
    else:
        m = RagMember(rag_id=rag.id, user_id=target.id, status=MembershipStatus.active)
        db.add(m)
        await db.commit()
        await db.refresh(m)
    return MemberOut(
        user_id=target.id,
        email=target.email,
        status=m.status.value,
        created_at=m.created_at,
        revoked_at=m.revoked_at,
        is_owner=False,
    )


@router.delete("/{rag_id}/members/{user_id}", status_code=204)
async def revoke_member(
    user_id: uuid.UUID,
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-revoke. The member's existing chat history stays readable to them,
    but new agent runs / new chat sessions are blocked until they're invited back."""
    m = (
        await db.execute(
            select(RagMember).where(
                RagMember.rag_id == rag.id, RagMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "membership not found")
    if m.status == MembershipStatus.revoked:
        return None
    m.status = MembershipStatus.revoked
    m.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return None
