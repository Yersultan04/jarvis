"""Qdrant client with NAMED dense + sparse vectors.

Collections are created with two vector fields:
  - "dense": cosine-similarity dense embedding (bge-m3 / Qwen3-Embedding-8B / voyage)
  - "lex":   sparse BM25 vector from fastembed Qdrant/bm25

Search returns raw scores from each leg; the weighted blend with min-max
normalization happens client-side in retrieval/hybrid.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from app.config import get_settings


DENSE_VEC = "dense"
SPARSE_VEC = "lex"


@dataclass
class QdrantHit:
    point_id: str
    score: float
    payload: dict


@lru_cache
def get_client() -> AsyncQdrantClient:
    s = get_settings()
    return AsyncQdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, prefer_grpc=False)


def collection_name(rag_id: uuid.UUID | str) -> str:
    return f"rag_{str(rag_id).replace('-', '')}"


async def ensure_collection(rag_id: uuid.UUID | str, dense_size: int) -> str:
    """Create a dual-vector collection (dense + sparse) if it doesn't exist."""
    client = get_client()
    name = collection_name(rag_id)
    if await client.collection_exists(name):
        return name
    await client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VEC: qm.VectorParams(size=dense_size, distance=qm.Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_VEC: qm.SparseVectorParams(
                modifier=qm.Modifier.IDF,  # required for Qdrant/bm25
            ),
        },
    )
    return name


async def delete_collection(rag_id: uuid.UUID | str) -> None:
    client = get_client()
    name = collection_name(rag_id)
    if await client.collection_exists(name):
        await client.delete_collection(name)


async def upsert_chunks(
    rag_id: uuid.UUID | str,
    points: list[tuple[str, list[float], dict, dict]],
) -> None:
    """Upsert chunks. Each point: (id, dense_vec, sparse_vec, payload).
    sparse_vec is {"indices": list[int], "values": list[float]}.
    """
    if not points:
        return
    client = get_client()
    name = collection_name(rag_id)
    pstructs: list[qm.PointStruct] = []
    for pid, dense, sparse, payload in points:
        vector: dict[str, object] = {DENSE_VEC: dense}
        if sparse and sparse.get("indices"):
            vector[SPARSE_VEC] = qm.SparseVector(
                indices=sparse["indices"], values=sparse["values"],
            )
        pstructs.append(qm.PointStruct(id=pid, vector=vector, payload=payload))
    await client.upsert(collection_name=name, points=pstructs, wait=True)


async def delete_points(
    rag_id: uuid.UUID | str,
    point_ids: list[str],
) -> None:
    if not point_ids:
        return
    client = get_client()
    name = collection_name(rag_id)
    if not await client.collection_exists(name):
        return
    await client.delete(
        collection_name=name,
        points_selector=qm.PointIdsList(points=point_ids),
        wait=True,
    )


async def search_dense(
    rag_id: uuid.UUID | str,
    query_vector: list[float],
    top_k: int = 50,
) -> list[QdrantHit]:
    """Dense leg of hybrid search. Returns raw cosine scores."""
    client = get_client()
    name = collection_name(rag_id)
    res = await client.query_points(
        collection_name=name,
        query=query_vector,
        using=DENSE_VEC,
        limit=top_k,
        with_payload=True,
    )
    return [
        QdrantHit(point_id=str(p.id), score=float(p.score), payload=p.payload or {})
        for p in res.points
    ]


async def search_sparse(
    rag_id: uuid.UUID | str,
    query_sparse: dict,
    top_k: int = 50,
) -> list[QdrantHit]:
    """Sparse (BM25) leg of hybrid search. Returns raw IDF×TF dot-product scores."""
    client = get_client()
    name = collection_name(rag_id)
    if not query_sparse or not query_sparse.get("indices"):
        return []
    res = await client.query_points(
        collection_name=name,
        query=qm.SparseVector(
            indices=query_sparse["indices"],
            values=query_sparse["values"],
        ),
        using=SPARSE_VEC,
        limit=top_k,
        with_payload=True,
    )
    return [
        QdrantHit(point_id=str(p.id), score=float(p.score), payload=p.payload or {})
        for p in res.points
    ]
