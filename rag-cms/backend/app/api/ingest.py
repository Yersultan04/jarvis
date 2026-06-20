from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_owned_rag, new_stream_token
from app.db import get_db
from app.ingestion.events import IngestEventBroker, replay_from_disk
from app.ingestion.pipeline import run_ingestion
from app.models import File as FileModel
from app.models import FileStatus, IngestRun, IngestRunStatus, Rag
from app.schemas import IngestRunOut

router = APIRouter()


@router.post("/{rag_id}/index", response_model=IngestRunOut, status_code=202)
async def start_ingestion(
    background: BackgroundTasks,
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
    force: bool = Query(
        False,
        description="When true, all parsed files are also re-processed (chunks + qdrant points are wiped first).",
    ),
) -> IngestRun:
    active = (
        await db.execute(
            select(IngestRun).where(
                IngestRun.rag_id == rag.id,
                IngestRun.status.in_([IngestRunStatus.queued, IngestRunStatus.running]),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(409, "an ingestion run is already in progress")

    if force:
        # Drop the cache: flip every parsed file back to `uploaded` so the
        # pipeline picks it up and wipes its stale chunks/points before re-parsing.
        await db.execute(
            update(FileModel)
            .where(FileModel.rag_id == rag.id, FileModel.status == FileStatus.parsed)
            .values(status=FileStatus.uploaded, error=None)
        )

    run = IngestRun(
        id=uuid.uuid4(),
        rag_id=rag.id,
        status=IngestRunStatus.queued,
        stream_token=new_stream_token(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    background.add_task(run_ingestion, rag.id, run.id)
    return run


@router.get("/{rag_id}/index/status", response_model=IngestRunOut)
async def latest_ingestion_status(
    rag: Rag = Depends(get_owned_rag), db: AsyncSession = Depends(get_db)
) -> IngestRun:
    row = (
        await db.execute(
            select(IngestRun)
            .where(IngestRun.rag_id == rag.id)
            .order_by(IngestRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "no ingestion run yet")
    return row


@router.get("/{rag_id}/index/runs", response_model=list[IngestRunOut])
async def list_ingestion_runs(
    rag: Rag = Depends(get_owned_rag), db: AsyncSession = Depends(get_db)
) -> list[IngestRun]:
    rows = await db.execute(
        select(IngestRun).where(IngestRun.rag_id == rag.id).order_by(IngestRun.created_at.desc())
    )
    return list(rows.scalars().all())


@router.get("/{rag_id}/index/runs/{run_id}/events")
async def get_ingest_run_events(
    run_id: uuid.UUID,
    rag: Rag = Depends(get_owned_rag),
    db: AsyncSession = Depends(get_db),
    since: int = Query(0, ge=0),
) -> list[dict]:
    row = (
        await db.execute(
            select(IngestRun).where(IngestRun.id == run_id, IngestRun.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "run not found")
    return [ev for ev in replay_from_disk(rag.id, run_id) if ev.get("seq", 0) > since]


@router.get("/{rag_id}/index/runs/{run_id}/stream")
async def stream_ingest_run(
    rag_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    token: str = Query(..., description="stream_token from POST /index response"),
    since: int = Query(0, ge=0),
):
    row = (
        await db.execute(
            select(IngestRun).where(IngestRun.id == run_id, IngestRun.rag_id == rag_id)
        )
    ).scalar_one_or_none()
    if row is None or row.stream_token != token:
        raise HTTPException(404, "run not found")

    is_terminal = row.status in (IngestRunStatus.succeeded, IngestRunStatus.failed)

    async def gen() -> AsyncIterator[bytes]:
        if is_terminal:
            for ev in replay_from_disk(rag_id, run_id):
                if ev.get("seq", 0) > since:
                    yield _sse(ev)
            yield _sse({"type": "stream_end", "payload": {}})
            return
        broker = await IngestEventBroker.get_or_create(rag_id, run_id)
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
