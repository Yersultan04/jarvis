"""Sparse text embeddings via fastembed Qdrant/bm25.

Runs in-process (~10MB ONNX model, CPU only). Supports per-language Snowball
stemming: russian, english, german, french, spanish, italian, portuguese,
dutch, danish, swedish, norwegian, finnish, turkish, arabic, romanian, tamil,
hungarian, greek.

Output format is Qdrant's sparse vector dict: {indices: [...], values: [...]}.
"""
from __future__ import annotations

import logging
from threading import Lock
from typing import Iterable

from fastembed import SparseTextEmbedding

log = logging.getLogger("bm25")


# fastembed BM25 — supported Snowball languages. Anything outside this set
# silently falls back to english stemming (acceptable for short / Latin text).
_SUPPORTED_LANGS = {
    "arabic", "danish", "dutch", "english", "finnish", "french", "german",
    "greek", "hungarian", "italian", "norwegian", "portuguese", "romanian",
    "russian", "spanish", "swedish", "tamil", "turkish",
}

_models: dict[str, SparseTextEmbedding] = {}
_lock = Lock()


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return "english"
    s = lang.strip().lower()
    if s == "simple":
        return "english"
    return s if s in _SUPPORTED_LANGS else "english"


def _get_model(language: str) -> SparseTextEmbedding:
    """Lazy-load the BM25 model for a given language. Cached per language."""
    lang = _normalize_lang(language)
    with _lock:
        m = _models.get(lang)
        if m is None:
            log.info("loading fastembed Qdrant/bm25 (language=%s)", lang)
            m = SparseTextEmbedding(model_name="Qdrant/bm25", language=lang)
            _models[lang] = m
        return m


def embed_documents(texts: list[str], *, language: str | None = None) -> list[dict]:
    """Encode a batch of documents to BM25 sparse vectors.
    Returns list of {indices: list[int], values: list[float]} dicts.
    """
    if not texts:
        return []
    model = _get_model(language or "english")
    out: list[dict] = []
    for emb in model.embed(texts):  # SparseEmbedding(indices, values)
        out.append({
            "indices": emb.indices.tolist(),
            "values": [float(v) for v in emb.values.tolist()],
        })
    return out


def embed_query(text: str, *, language: str | None = None) -> dict:
    """Encode a single query. BM25 query encoding is identical to document
    encoding for fastembed's implementation (no separate query-mode call).
    """
    if not text.strip():
        return {"indices": [], "values": []}
    model = _get_model(language or "english")
    # fastembed has a separate .query_embed() that uses IDF properly for queries.
    for emb in model.query_embed([text]):
        return {
            "indices": emb.indices.tolist(),
            "values": [float(v) for v in emb.values.tolist()],
        }
    return {"indices": [], "values": []}


def supported_languages() -> Iterable[str]:
    return sorted(_SUPPORTED_LANGS)
