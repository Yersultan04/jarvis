"""Hybrid retrieval: dense (Qdrant cosine) + sparse (Qdrant BM25) with
client-side WEIGHTED score blend.

The blend uses min-max normalization within each leg's result set so the
weights (0.8 dense / 0.2 sparse by default) actually behave proportionally —
without normalization, sparse dot-product scores (5-30) would dominate dense
cosine scores (0-1) regardless of weight.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.clients import bm25 as bm25_client
from app.clients import qdrant as qdrant_client
from app.clients.embeddings import embed_query
from app.config import get_settings
from app.models import Chunk, Rag
from app.presets import resolve_models_for_rag


@dataclass
class FusedHit:
    chunk_id: uuid.UUID
    score: float
    dense_score: float | None
    sparse_score: float | None


def _minmax(scores: list[float]) -> list[float]:
    """Return min-max normalized copy of `scores` (output in [0, 1]).
    Empty / single-element list passes through unchanged."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [1.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


def weighted_blend(
    dense_hits: list[qdrant_client.QdrantHit],
    sparse_hits: list[qdrant_client.QdrantHit],
    *,
    w_dense: float = 0.8,
    w_sparse: float = 0.2,
) -> list[FusedHit]:
    """Score-based weighted blend with per-leg min-max normalization."""
    d_norm = _minmax([h.score for h in dense_hits])
    s_norm = _minmax([h.score for h in sparse_hits])

    table: dict[str, dict] = {}
    for h, n in zip(dense_hits, d_norm, strict=True):
        table.setdefault(h.point_id, {"score": 0.0, "dense": None, "sparse": None})
        table[h.point_id]["dense"] = h.score
        table[h.point_id]["score"] += w_dense * n
    for h, n in zip(sparse_hits, s_norm, strict=True):
        table.setdefault(h.point_id, {"score": 0.0, "dense": None, "sparse": None})
        table[h.point_id]["sparse"] = h.score
        table[h.point_id]["score"] += w_sparse * n

    out: list[FusedHit] = []
    for pid, v in table.items():
        try:
            cid = uuid.UUID(pid)
        except (ValueError, TypeError):
            continue
        out.append(FusedHit(
            chunk_id=cid, score=v["score"],
            dense_score=v["dense"], sparse_score=v["sparse"],
        ))
    out.sort(key=lambda h: h.score, reverse=True)
    return out


async def hybrid_search(
    db: AsyncSession,
    rag_id: uuid.UUID,
    query: str,
    top_k: int,
    mode: str = "hybrid",
) -> list[tuple[Chunk, FusedHit]]:
    s = get_settings()

    fts_lang: str = "english"
    rag_models: dict | None = None
    rag_row = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one_or_none()
    if rag_row is not None:
        fts_lang = (rag_row.settings or {}).get("fts_language") or "english"
        rag_models = resolve_models_for_rag(rag_row)

    async def _dense_leg() -> list[qdrant_client.QdrantHit]:
        if mode not in ("dense", "hybrid"):
            return []
        vec = await embed_query(query, rag_models=rag_models)
        return await qdrant_client.search_dense(rag_id, vec, top_k=s.retrieval_top_k_dense)

    async def _sparse_leg() -> list[qdrant_client.QdrantHit]:
        if mode not in ("sparse", "hybrid"):
            return []
        sparse_q = bm25_client.embed_query(query, language=fts_lang)
        return await qdrant_client.search_sparse(rag_id, sparse_q, top_k=s.retrieval_top_k_sparse)

    dense_hits, sparse_hits = await asyncio.gather(_dense_leg(), _sparse_leg())

    if mode == "dense":
        fused = [FusedHit(
            chunk_id=uuid.UUID(h.point_id), score=h.score,
            dense_score=h.score, sparse_score=None,
        ) for h in dense_hits if _is_uuid(h.point_id)]
    elif mode == "sparse":
        fused = [FusedHit(
            chunk_id=uuid.UUID(h.point_id), score=h.score,
            dense_score=None, sparse_score=h.score,
        ) for h in sparse_hits if _is_uuid(h.point_id)]
    else:
        fused = weighted_blend(dense_hits, sparse_hits)

    fused = fused[:top_k]
    if not fused:
        return []

    ids = [f.chunk_id for f in fused]
    rows = await db.execute(
        select(Chunk).options(selectinload(Chunk.file)).where(Chunk.id.in_(ids))
    )
    chunks_by_id = {c.id: c for c in rows.scalars().all()}
    out: list[tuple[Chunk, FusedHit]] = []
    for f in fused:
        c = chunks_by_id.get(f.chunk_id)
        if c is not None:
            out.append((c, f))
    return out


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except (ValueError, TypeError):
        return False
