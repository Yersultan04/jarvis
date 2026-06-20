"""Unified embedding interface.

Dispatches to one of:
- Voyage cloud API (EMBED_PROVIDER=voyage)
- Any OpenAI-compatible /v1/embeddings endpoint (EMBED_PROVIDER=openai):
  Ollama, Hugging Face TEI, Infinity, vLLM, Together AI, Fireworks AI, etc.

Per-RAG override: callers pass `rag_models` (the snapshot stored in
`rag.settings.models`) — provider/base_url/api_key/model are read from there
when present, with global env as the fallback. That lets different RAGs in
the same instance use different embedders.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.clients import voyage as voyage_backend
from app.config import get_settings

log = logging.getLogger("embeddings")


def _resolve_cfg(rag_models: dict | None) -> dict[str, Any]:
    """Pick the effective embedding config: per-RAG snapshot wins, env is fallback."""
    s = get_settings()
    snap = rag_models or {}
    provider = (snap.get("embed_provider") or s.embed_provider or "voyage").strip().lower()

    if provider == "voyage":
        return {
            "provider": "voyage",
            "model": snap.get("embed_model") or s.voyage_embed_model,
        }

    # openai-compatible (Ollama / TEI / vLLM / Together / Fireworks / ...)
    return {
        "provider": "openai",
        "model": snap.get("embed_model") or s.embed_model_name,
        "base_url": snap.get("embed_base_url") or s.embed_api_base_url,
        "api_key": snap.get("embed_api_key") or s.embed_api_key or "dummy",
    }


# Cache OpenAI-compatible clients by (base_url, api_key) so we don't spin up a
# new httpx pool for every embed call. Different RAGs hitting different
# providers get their own client.
_openai_client_cache: dict[tuple[str, str], AsyncOpenAI] = {}


def _openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    if not base_url:
        raise RuntimeError(
            "embed_provider=openai requires base_url (snapshot.embed_base_url or "
            "EMBED_API_BASE_URL env)"
        )
    key = (base_url, api_key or "")
    c = _openai_client_cache.get(key)
    if c is None:
        c = AsyncOpenAI(api_key=api_key or "dummy", base_url=base_url)
        _openai_client_cache[key] = c
    return c


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20))
async def _openai_embed(texts: list[str], cfg: dict) -> list[list[float]]:
    if not cfg.get("model"):
        raise RuntimeError(
            "embed_provider=openai requires a model name (snapshot.embed_model or "
            "EMBED_MODEL_NAME env)"
        )
    client = _openai_client(cfg["base_url"], cfg.get("api_key") or "dummy")
    res = await client.embeddings.create(model=cfg["model"], input=texts)
    return [d.embedding for d in res.data]


# ---------------- public API ----------------


async def embed_documents(
    texts: list[str],
    *,
    rag_models: dict | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    cfg = _resolve_cfg(rag_models)
    if cfg["provider"] == "openai":
        return await _openai_embed(texts, cfg)
    return await voyage_backend.embed_documents(texts, model=cfg["model"])


async def embed_query(
    text: str,
    *,
    rag_models: dict | None = None,
) -> list[float]:
    cfg = _resolve_cfg(rag_models)
    if cfg["provider"] == "openai":
        out = await _openai_embed([text], cfg)
        return out[0]
    return await voyage_backend.embed_query(text, model=cfg["model"])


async def embed_documents_batched(
    texts: list[str],
    batch_size: int = 64,
    on_batch=None,
    *,
    rag_models: dict | None = None,
) -> list[list[float]]:
    """Same shape as voyage_backend.embed_documents_batched but provider-agnostic."""
    cfg = _resolve_cfg(rag_models)
    if cfg["provider"] == "voyage":
        return await voyage_backend.embed_documents_batched(
            texts, batch_size, on_batch, model=cfg["model"]
        )

    out: list[list[float]] = []
    total = (len(texts) + batch_size - 1) // batch_size if texts else 0
    done = 0
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        out.extend(await _openai_embed(chunk, cfg))
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


def effective_dim(rag_models: dict | None = None) -> int:
    """The vector dimension a Qdrant collection should be created with."""
    s = get_settings()
    snap = rag_models or {}
    if snap.get("embed_dim"):
        return int(snap["embed_dim"])
    if (snap.get("embed_provider") or s.embed_provider) == "openai" and s.embed_dim:
        return int(s.embed_dim)
    return int(s.voyage_embed_dim)


def effective_model_name(rag_models: dict | None = None) -> str:
    cfg = _resolve_cfg(rag_models)
    return cfg.get("model") or "unknown"
