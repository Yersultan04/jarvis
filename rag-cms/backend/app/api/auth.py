from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    current_user,
    hash_password,
    issue_token,
    require_admin,
    verify_password,
)
from app.db import get_db
from app.models import User, UserRole
from app.schemas import (
    TokenOut,
    UserAdminCreate,
    UserAdminUpdate,
    UserLogin,
    UserOut,
    UserRegister,
)

router = APIRouter()


async def _user_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(User.id)))).scalar_one()


@router.get("/registration-status")
async def registration_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Tells the UI whether the bootstrap-admin registration form should be shown.

    The form is exposed ONLY when no users exist yet. After the first admin is
    created (via env bootstrap or this endpoint), open registration is closed
    forever — further accounts must be created by an admin.
    """
    open_ = (await _user_count(db)) == 0
    return {"open": open_, "reason": "bootstrap" if open_ else "closed"}


@router.post("/register", response_model=TokenOut, status_code=201)
async def bootstrap_register(
    payload: UserRegister, db: AsyncSession = Depends(get_db)
) -> TokenOut:
    """One-shot bootstrap of the first admin. Closed once any user exists."""
    if (await _user_count(db)) > 0:
        raise HTTPException(
            status_code=403,
            detail="registration is closed; ask an admin to create your account",
        )
    user = User(
        id=uuid.uuid4(),
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        role=UserRole.admin,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="email already registered")
    await db.refresh(user)
    token, expires = issue_token(user)
    return TokenOut(access_token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> TokenOut:
    user = (
        await db.execute(select(User).where(User.email == payload.email.lower().strip()))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    token, expires = issue_token(user)
    return TokenOut(access_token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> User:
    return user


# ---------------- admin-only user management ----------------


@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[User]:
    res = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(res.scalars().all())


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_user(
    payload: UserAdminCreate, db: AsyncSession = Depends(get_db)
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="email already exists")
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdate,
    actor: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> User:
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "user not found")

    # Don't let the last admin demote or disable themselves — easy way to lock
    # everyone out of the platform.
    if target.id == actor.id and (
        (payload.role is not None and payload.role != UserRole.admin)
        or payload.is_active is False
    ):
        admin_count = (
            await db.execute(select(func.count(User.id)).where(User.role == UserRole.admin))
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(
                400,
                "cannot demote or deactivate the last remaining admin",
            )

    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.password is not None:
        target.password_hash = hash_password(payload.password)
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    actor: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    if user_id == actor.id:
        raise HTTPException(400, "you cannot delete your own account")
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "user not found")
    if target.role == UserRole.admin:
        admin_count = (
            await db.execute(select(func.count(User.id)).where(User.role == UserRole.admin))
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(400, "cannot delete the last admin")
    await db.delete(target)
    await db.commit()
