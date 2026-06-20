"""Sparse retrieval via Qdrant BM25 sparse vectors (fastembed Qdrant/bm25).

Replaced the Postgres FTS implementation — sparse now lives in Qdrant
alongside dense for native hybrid querying. Per-RAG language picked from
`rag.settings.fts_language` (the same knob users already set at create time).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import bm25 as bm25_client
from app.clients import qdrant as qdrant_client
from app.models import Rag


@dataclass
class SparseHit:
    chunk_id: uuid.UUID
    score: float


async def sparse_search(
    db: AsyncSession,
    rag_id: uuid.UUID,
    query: str,
    top_k: int,
    *,
    language: str | None = None,
) -> list[SparseHit]:
    if not query.strip():
        return []
    if not language:
        rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
        language = (rag.settings or {}).get("fts_language") if rag is not None else None
    sparse_q = bm25_client.embed_query(query, language=language or "english")
    hits = await qdrant_client.search_sparse(rag_id, sparse_q, top_k=top_k)
    out: list[SparseHit] = []
    for h in hits:
        try:
            cid = uuid.UUID(h.point_id)
        except (ValueError, TypeError):
            continue
        out.append(SparseHit(chunk_id=cid, score=h.score))
    return out
