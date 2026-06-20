from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_accessible_rag
from app.db import get_db
from app.models import Chunk, Rag
from app.retrieval.hybrid import hybrid_search
from app.schemas import ChunkOut, SearchHit, SearchRequest, SearchResponse

router = APIRouter()


@router.get("/{rag_id}/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(
    chunk_id: uuid.UUID,
    rag: Rag = Depends(get_accessible_rag),
    db: AsyncSession = Depends(get_db),
) -> ChunkOut:
    row = (
        await db.execute(
            select(Chunk)
            .options(selectinload(Chunk.file))
            .where(Chunk.id == chunk_id, Chunk.rag_id == rag.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "chunk not found")
    return ChunkOut(
        chunk_id=row.id,
        file_id=row.file_id,
        filename=row.file.filename if row.file else "",
        page_start=row.page_start,
        page_end=row.page_end,
        heading=row.heading,
        text=row.text,
        token_count=row.token_count,
    )


@router.post("/{rag_id}/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    rag: Rag = Depends(get_accessible_rag),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    pairs = await hybrid_search(
        db=db,
        rag_id=rag.id,
        query=payload.query,
        top_k=payload.top_k,
        mode=payload.mode,
    )
    hits = [
        SearchHit(
            chunk_id=c.id,
            file_id=c.file_id,
            filename=c.file.filename if c.file else "",
            page_start=c.page_start,
            page_end=c.page_end,
            heading=c.heading,
            text=c.text,
            score=fh.score,
            dense_score=fh.dense_score,
            sparse_score=fh.sparse_score,
        )
        for c, fh in pairs
    ]
    return SearchResponse(query=payload.query, mode=payload.mode, hits=hits)
