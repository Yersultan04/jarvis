from __future__ import annotations

import asyncio
from functools import lru_cache

import voyageai
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


@lru_cache
def get_voyage_client() -> voyageai.AsyncClient:
    s = get_settings()
    if not s.voyage_api_key:
        raise RuntimeError("VOYAGE_API_KEY is not set")
    return voyageai.AsyncClient(api_key=s.voyage_api_key)


def _effective_model(explicit: str | None) -> str:
    if explicit:
        return explicit
    return get_settings().voyage_embed_model


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20))
async def embed_documents(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    client = get_voyage_client()
    res = await client.embed(
        texts, model=_effective_model(model), input_type="document"
    )
    return res.embeddings


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20))
async def embed_query(text: str, *, model: str | None = None) -> list[float]:
    client = get_voyage_client()
    res = await client.embed(
        [text], model=_effective_model(model), input_type="query"
    )
    return res.embeddings[0]


async def embed_documents_batched(
    texts: list[str],
    batch_size: int = 64,
    on_batch=None,
    *,
    model: str | None = None,
) -> list[list[float]]:
    """Embed in batches to respect Voyage rate/payload limits."""
    out: list[list[float]] = []
    total = (len(texts) + batch_size - 1) // batch_size if texts else 0
    done = 0
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        out.extend(await embed_documents(chunk, model=model))
        done += 1
        if on_batch is not None:
            try:
                res = on_batch(done, total)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                pass
        await asyncio.sleep(0)
    return out
