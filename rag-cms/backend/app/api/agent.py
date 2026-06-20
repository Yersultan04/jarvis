from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.events import EventBroker, replay_from_disk
from app.agent.loop import DEFAULT_MAX_STEPS, run_agent
from app.agent.schemas import AgentRunStartRequest
from app.auth import current_user, get_accessible_rag, get_visible_rag, new_stream_token
from app.db import get_db
from app.models import AgentRun, AgentRunStatus, ChatSession, Rag, RagStatus, User, UserRole

router = APIRouter()


def _truncate_title(query: str, n: int = 80) -> str:
    q = query.strip().replace("\n", " ")
    return q if len(q) <= n else q[: n - 1] + "…"


def _is_owner_or_admin(rag: Rag, user: User) -> bool:
    return rag.owner_id == user.id or user.role == UserRole.admin


async def _own_session_or_404(
    db: AsyncSession, rag: Rag, session_id: uuid.UUID, user: User
) -> ChatSession:
    sess = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise HTTPException(404, "session not found")
    # Admin can see anything; everyone else only their own sessions.
    if user.role != UserRole.admin and sess.user_id != user.id:
        raise HTTPException(404, "session not found")
    return sess


# ---------------- chat sessions ----------------


@router.get("/{rag_id}/chat_sessions")
async def list_chat_sessions(
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(ChatSession).where(ChatSession.rag_id == rag.id)
    if user.role != UserRole.admin:
        stmt = stmt.where(ChatSession.user_id == user.id)
    rows = (
        await db.execute(
            stmt.order_by(
                ChatSession.last_run_at.desc().nulls_last(), ChatSession.created_at.desc()
            )
        )
    ).scalars().all()
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        }
        for s in rows
    ]


@router.post("/{rag_id}/chat_sessions", status_code=201)
async def create_chat_session(
    payload: dict,
    rag: Rag = Depends(get_accessible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    title = (payload.get("title") or "Новый чат").strip()[:255] if isinstance(payload, dict) else "Новый чат"
    s = ChatSession(id=uuid.uuid4(), rag_id=rag.id, user_id=user.id, title=title or "Новый чат")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"id": str(s.id), "title": s.title, "created_at": s.created_at.isoformat()}


@router.get("/{rag_id}/chat_sessions/{session_id}")
async def get_chat_session(
    session_id: uuid.UUID,
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sess = await _own_session_or_404(db, rag, session_id, user)
    runs = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.session_id == sess.id)
            .order_by(AgentRun.created_at.asc())
        )
    ).scalars().all()
    return {
        "id": str(sess.id),
        "title": sess.title,
        "created_at": sess.created_at.isoformat(),
        "last_run_at": sess.last_run_at.isoformat() if sess.last_run_at else None,
        "runs": [
            {
                "id": str(r.id),
                "status": r.status.value,
                "query": r.query,
                "answer": r.answer,
                "citations": r.citations,
                "confidence": r.confidence,
                "steps_used": r.steps_used,
                "max_steps": r.max_steps,
                "stream_token": r.stream_token,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
    }


@router.patch("/{rag_id}/chat_sessions/{session_id}")
async def rename_chat_session(
    session_id: uuid.UUID,
    payload: dict,
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sess = await _own_session_or_404(db, rag, session_id, user)
    title = (payload.get("title") or "").strip()[:255] if isinstance(payload, dict) else ""
    if title:
        sess.title = title
    await db.commit()
    return {"id": str(sess.id), "title": sess.title}


@router.delete("/{rag_id}/chat_sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: uuid.UUID,
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    sess = await _own_session_or_404(db, rag, session_id, user)
    await db.delete(sess)
    await db.commit()


# ---------------- agent runs ----------------


@router.post("/{rag_id}/agent/runs", status_code=202)
async def start_agent_run(
    payload: AgentRunStartRequest,
    background: BackgroundTasks,
    rag: Rag = Depends(get_accessible_rag),  # blocks revoked members
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if rag.status != RagStatus.ready:
        raise HTTPException(409, f"RAG is not ready (status={rag.status.value})")
    max_steps = payload.max_steps or DEFAULT_MAX_STEPS

    # Resolve / create the chat session. Sessions are scoped to the user.
    session: ChatSession | None = None
    if payload.session_id is not None:
        session = (
            await db.execute(
                select(ChatSession).where(
                    ChatSession.id == payload.session_id, ChatSession.rag_id == rag.id
                )
            )
        ).scalar_one_or_none()
        if session is None or (
            session.user_id != user.id and user.role != UserRole.admin
        ):
            raise HTTPException(404, "session not found")
    else:
        session = ChatSession(
            id=uuid.uuid4(),
            rag_id=rag.id,
            user_id=user.id,
            title=_truncate_title(payload.query),
        )
        db.add(session)
        await db.flush()

    run = AgentRun(
        id=uuid.uuid4(),
        rag_id=rag.id,
        session_id=session.id,
        query=payload.query.strip(),
        status=AgentRunStatus.queued,
        max_steps=max_steps,
        stream_token=new_stream_token(),
    )
    session.last_run_at = datetime.now(timezone.utc)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background.add_task(run_agent, rag.id, run.id, run.query, max_steps)
    return {
        "id": str(run.id),
        "session_id": str(session.id),
        "status": run.status.value,
        "max_steps": max_steps,
        "stream_token": run.stream_token,
    }


@router.get("/{rag_id}/agent/runs")
async def list_agent_runs(
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List the caller's runs in this RAG (admins see everything)."""
    stmt = (
        select(AgentRun)
        .join(ChatSession, ChatSession.id == AgentRun.session_id, isouter=True)
        .where(AgentRun.rag_id == rag.id)
        .order_by(AgentRun.created_at.desc())
    )
    if user.role != UserRole.admin:
        stmt = stmt.where(ChatSession.user_id == user.id)

    rows = (await db.execute(stmt)).scalars().all()
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "id": str(r.id),
                "status": r.status.value,
                "query": r.query,
                "answer": r.answer,
                "confidence": r.confidence,
                "steps_used": r.steps_used,
                "max_steps": r.max_steps,
                "stream_token": r.stream_token,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
        )
    return out


async def _own_run_or_404(
    db: AsyncSession, rag: Rag, run_id: uuid.UUID, user: User
) -> AgentRun:
    row = (
        await db.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "run not found")
    if user.role == UserRole.admin:
        return row
    # Match through the session
    if row.session_id is None:
        # Legacy runs without session — only the RAG owner can see them.
        if rag.owner_id == user.id:
            return row
        raise HTTPException(404, "run not found")
    sess = (
        await db.execute(select(ChatSession).where(ChatSession.id == row.session_id))
    ).scalar_one_or_none()
    if sess is None or sess.user_id != user.id:
        raise HTTPException(404, "run not found")
    return row


@router.get("/{rag_id}/agent/runs/{run_id}")
async def get_agent_run(
    run_id: uuid.UUID,
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await _own_run_or_404(db, rag, run_id, user)
    return {
        "id": str(row.id),
        "rag_id": str(row.rag_id),
        "status": row.status.value,
        "query": row.query,
        "answer": row.answer,
        "citations": row.citations,
        "confidence": row.confidence,
        "telemetry": row.telemetry,
        "error": row.error,
        "steps_used": row.steps_used,
        "max_steps": row.max_steps,
        "stream_token": row.stream_token,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{rag_id}/agent/runs/{run_id}/events")
async def get_agent_run_events(
    run_id: uuid.UUID,
    rag: Rag = Depends(get_visible_rag),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    since: int = Query(0, ge=0),
) -> list[dict]:
    await _own_run_or_404(db, rag, run_id, user)
    return [ev for ev in replay_from_disk(rag.id, run_id) if ev.get("seq", 0) > since]


@router.get("/{rag_id}/agent/runs/{run_id}/stream")
async def stream_agent_run(
    rag_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    token: str = Query(..., description="stream_token from POST /agent/runs response"),
    since: int = Query(0, ge=0),
):
    row = (
        await db.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.rag_id == rag_id)
        )
    ).scalar_one_or_none()
    if row is None or row.stream_token != token:
        raise HTTPException(404, "run not found")

    is_terminal = row.status in (
        AgentRunStatus.succeeded,
        AgentRunStatus.escalated,
        AgentRunStatus.failed,
    )

    async def gen() -> AsyncIterator[bytes]:
        if is_terminal:
            for ev in replay_from_disk(rag_id, run_id):
                if ev.get("seq", 0) > since:
                    yield _sse(ev)
            yield _sse({"type": "stream_end", "payload": {}})
            return
        broker = await EventBroker.get_or_create(rag_id, run_id)
        try:
            async for ev in broker.subscribe(since_seq=since):
                if ev is None:
                    yield _sse({"type": "stream_end", "payload": {}})
                    return
                yield _sse(ev)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event: dict) -> bytes:
    event_type = event.get("type", "message")
    seq = event.get("seq")
    data = json.dumps(event, ensure_ascii=False, default=str)
    head = f"event: {event_type}\n"
    if seq is not None:
        head += f"id: {seq}\n"
    return (head + f"data: {data}\n\n").encode("utf-8")
