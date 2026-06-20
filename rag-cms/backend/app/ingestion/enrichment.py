from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.clients.llm import chat
from app.config import get_settings
from app.ingestion.chunker import Chunk, count_tokens

log = logging.getLogger("enrichment")


@dataclass
class EnrichmentResult:
    doc_summary: str
    contexts: list[str]  # parallel to input chunks; "" when generation failed


_DOC_SUMMARY_SYSTEM = (
    "You are summarizing a document so a downstream RAG can position individual "
    "passages within it. In 2-4 sentences, describe what this document is "
    "(type, subject, intent, parties/entities if present). Plain text only, no preamble."
)


_CHUNK_CONTEXT_SYSTEM = (
    "You are generating short situating contexts for excerpts from a document.\n"
    "For each candidate, write ONE concise English sentence (max 25 words) that "
    "names the document's topic and where this excerpt sits within it "
    "(e.g. 'In the parties' obligations section of <doc>, this clause defines …').\n"
    "Return ONLY JSON: {\"contexts\": [{\"i\": <1-based index>, \"c\": \"<sentence>\"}, ...]} "
    "with one entry per candidate, preserving order."
)


def _doc_text_excerpt(chunks: list[Chunk], max_tokens: int = 4000) -> str:
    """Stitch start, middle and tail of the document into a budgeted excerpt for summarization."""
    if not chunks:
        return ""
    total_tokens = sum(c.token_count for c in chunks)
    if total_tokens <= max_tokens:
        return "\n\n".join(c.text for c in chunks)

    third = max_tokens // 3
    take_start: list[str] = []
    used = 0
    for c in chunks:
        if used + c.token_count > third:
            break
        take_start.append(c.text)
        used += c.token_count

    take_end: list[str] = []
    used_end = 0
    for c in reversed(chunks):
        if used_end + c.token_count > third:
            break
        take_end.append(c.text)
        used_end += c.token_count
    take_end.reverse()

    mid = chunks[len(chunks) // 2].text
    return "\n\n".join(
        [
            "[BEGINNING]\n" + "\n\n".join(take_start),
            "[MIDDLE]\n" + mid,
            "[END]\n" + "\n\n".join(take_end),
        ]
    )


def _trim(text: str, max_chars: int = 600) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


async def _summarize_document(doc_excerpt: str, filename: str) -> str:
    if not doc_excerpt.strip():
        return ""
    s = get_settings()
    try:
        return (await chat(
            [
                {"role": "system", "content": _DOC_SUMMARY_SYSTEM},
                {"role": "user", "content": f"Filename: {filename}\n\nDocument:\n{doc_excerpt}"},
            ],
            model=s.contextual_enrichment_model,
            temperature=0.1,
            max_tokens=300,
        )).strip()
    except Exception as e:
        log.warning("doc summary failed for %s: %s", filename, e)
        return ""


async def _contexts_batch(doc_summary: str, items: list[Chunk], filename: str) -> list[str]:
    if not items:
        return []
    s = get_settings()
    payload = "\n".join(f"[{i + 1}] (heading: {c.heading or '—'}) {_trim(c.text)}" for i, c in enumerate(items))
    user = (
        f"DOCUMENT ({filename}): {doc_summary or '(no summary available)'}\n\n"
        f"EXCERPTS (number them in the response):\n{payload}\n\n"
        "Return the JSON now."
    )
    try:
        raw = await chat(
            [
                {"role": "system", "content": _CHUNK_CONTEXT_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=s.contextual_enrichment_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=80 * len(items) + 80,
        )
        data = json.loads(raw)
    except Exception as e:
        log.warning("contexts batch failed: %s", e)
        return ["" for _ in items]

    by_idx: dict[int, str] = {}
    for entry in data.get("contexts", []):
        try:
            i = int(entry.get("i"))
            c = str(entry.get("c") or "").strip()
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(items) and c:
            by_idx[i - 1] = c
    return [by_idx.get(i, "") for i in range(len(items))]


async def enrich_chunks(filename: str, chunks: list[Chunk]) -> EnrichmentResult:
    """Generate contextual prepends for chunks.

    The original chunk text is never mutated by this function — callers decide
    how to combine the returned context with the chunk text (typically prepend
    before embedding, while keeping the bare chunk text in the database).
    """
    if not chunks:
        return EnrichmentResult(doc_summary="", contexts=[])

    s = get_settings()
    excerpt = _doc_text_excerpt(chunks, max_tokens=4000)
    summary = await _summarize_document(excerpt, filename)

    contexts: list[str] = []
    batch = max(1, s.contextual_enrichment_batch)
    for i in range(0, len(chunks), batch):
        contexts.extend(await _contexts_batch(summary, chunks[i : i + batch], filename))

    # Pad just in case
    while len(contexts) < len(chunks):
        contexts.append("")
    return EnrichmentResult(doc_summary=summary, contexts=contexts[: len(chunks)])
