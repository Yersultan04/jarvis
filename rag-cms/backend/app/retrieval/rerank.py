from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from app.clients.llm import chat
from app.config import get_settings

log = logging.getLogger("rerank")


@dataclass
class RerankItem:
    """Generic shape — chunk_id + score + text. Caller maps back to its objects."""

    chunk_id: uuid.UUID
    text: str
    score: float = 0.0


@dataclass
class RerankResult:
    items: list[RerankItem]
    used_llm: bool


_RERANK_SYSTEM = (
    "You are a retrieval reranker. You receive a USER QUERY and a numbered list of "
    "CANDIDATES (excerpts from a document corpus). Score each candidate from 0.0 to 1.0 "
    "for how directly it answers the query. Be strict: 1.0 = fully answers, 0.5 = related "
    "but partial, 0.0 = irrelevant. Return ONLY valid JSON of the form "
    '{"scores": [{"i": <1-based candidate index>, "s": <float 0..1>}, ...]} '
    "with one entry per candidate."
)


def _trim(text: str, max_chars: int = 700) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


async def llm_rerank(
    query: str,
    candidates: list[RerankItem],
    *,
    top_n: int | None = None,
    blend_with_retrieval: float = 0.3,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    provider_order: list[str] | None = None,
) -> RerankResult:
    """Rerank candidates with the configured LLM. Returns at most top_n items.

    blend_with_retrieval: 0..1. Final score = (1-b) * llm_score + b * normalized_retrieval_score.
    Set to 0 for pure LLM, to 1 for pure retrieval (== no-op).
    """
    if not candidates:
        return RerankResult(items=[], used_llm=False)
    if len(candidates) == 1:
        return RerankResult(items=candidates, used_llm=False)

    s = get_settings()
    block = "\n".join(
        f"[{i + 1}] {_trim(c.text)}" for i, c in enumerate(candidates)
    )
    user = f"USER QUERY:\n{query}\n\nCANDIDATES:\n{block}\n\nReturn the JSON now."

    try:
        raw = await chat(
            [
                {"role": "system", "content": _RERANK_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model or s.llm_rerank_model,
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=600 + len(candidates) * 18,
            base_url=base_url,
            api_key=api_key,
            provider_order=provider_order,
        )
        parsed = json.loads(raw)
    except Exception as e:
        log.warning("llm_rerank failed, returning original order: %s", e)
        items = candidates[: top_n] if top_n else candidates
        return RerankResult(items=items, used_llm=False)

    llm_scores: dict[int, float] = {}
    for entry in parsed.get("scores", []):
        try:
            i = int(entry.get("i"))
            sc = float(entry.get("s"))
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(candidates):
            llm_scores[i - 1] = max(0.0, min(1.0, sc))

    max_retr = max((c.score for c in candidates), default=0.0) or 1.0
    out: list[RerankItem] = []
    for idx, c in enumerate(candidates):
        llm_s = llm_scores.get(idx, 0.0)
        retr_norm = (c.score / max_retr) if max_retr > 0 else 0.0
        fused = (1.0 - blend_with_retrieval) * llm_s + blend_with_retrieval * retr_norm
        out.append(RerankItem(chunk_id=c.chunk_id, text=c.text, score=fused))

    out.sort(key=lambda r: r.score, reverse=True)
    if top_n is not None:
        out = out[:top_n]
    return RerankResult(items=out, used_llm=True)
