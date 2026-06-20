from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.clients.embeddings import embed_query
from app.clients.qdrant import search_dense


@dataclass
class DenseHit:
    chunk_id: uuid.UUID
    score: float


async def dense_search(
    rag_id: uuid.UUID,
    query: str,
    top_k: int,
    *,
    rag_models: dict | None = None,
) -> list[DenseHit]:
    vec = await embed_query(query, rag_models=rag_models)
    qhits = await search_dense(rag_id, vec, top_k=top_k)
    out: list[DenseHit] = []
    for h in qhits:
        cid_str = (h.payload or {}).get("chunk_id") or h.point_id
        try:
            cid = uuid.UUID(cid_str)
        except (ValueError, TypeError):
            continue
        out.append(DenseHit(chunk_id=cid, score=h.score))
    return out
