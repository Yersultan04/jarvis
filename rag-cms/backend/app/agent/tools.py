from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import PoolEntry
from app.clients import qdrant as qdrant_client
from app.clients.llm import chat
from app.clients.embeddings import embed_query
from app.config import get_settings
from app.models import Chunk, File
from app.retrieval.dense import dense_search as do_dense
from app.retrieval.hybrid import hybrid_search
from app.retrieval.rerank import RerankItem, llm_rerank
from app.retrieval.sparse import sparse_search as do_sparse


@dataclass
class AgentContext:
    rag_id: uuid.UUID
    db: AsyncSession
    user_query: str = ""
    scratchpad: dict[str, str] = field(default_factory=dict)
    pool_ref: list = field(default_factory=list)  # the loop's current pool — kept in sync for rerank
    rag_settings: dict = field(default_factory=dict)
    rag_models: dict = field(default_factory=dict)  # llm_model, llm_rerank_model, llm_vision_model, embed_model


@dataclass
class ToolResult:
    """What a tool returns to the loop."""

    summary: str  # short human-readable observation for the model
    pool: list[PoolEntry] = field(default_factory=list)  # chunks added/refreshed
    data: dict[str, Any] = field(default_factory=dict)  # optional structured payload


ToolFn = Callable[[AgentContext, dict[str, Any]], Awaitable[ToolResult]]

REGISTRY: dict[str, ToolFn] = {}


def tool(name: str) -> Callable[[ToolFn], ToolFn]:
    def wrap(fn: ToolFn) -> ToolFn:
        REGISTRY[name] = fn
        return fn

    return wrap


def _require_str(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return v.strip()


def _opt_int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = args.get(key, default)
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


async def _chunks_to_pool(rows: list[Chunk]) -> list[PoolEntry]:
    out: list[PoolEntry] = []
    for c in rows:
        out.append(
            PoolEntry(
                chunk_id=c.id,
                file_id=c.file_id,
                filename=c.file.filename if c.file else "",
                page_start=c.page_start,
                page_end=c.page_end,
                heading=c.heading,
                text=c.text,
                score=0.0,
            )
        )
    return out


# ---------------- core search tools ----------------


@tool("hybrid_search")
async def hybrid_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    query = _require_str(args, "query")
    top_k = _opt_int(args, "top_k", 10, 1, 50)
    pairs = await hybrid_search(ctx.db, ctx.rag_id, query, top_k=top_k, mode="hybrid")
    pool = [
        PoolEntry(
            chunk_id=c.id,
            file_id=c.file_id,
            filename=c.file.filename if c.file else "",
            page_start=c.page_start,
            page_end=c.page_end,
            heading=c.heading,
            text=c.text,
            score=fh.score,
        )
        for c, fh in pairs
    ]
    return ToolResult(summary=f"hybrid_search: {len(pool)} hits for {query!r}", pool=pool)


@tool("dense_search")
async def dense_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    query = _require_str(args, "query")
    top_k = _opt_int(args, "top_k", 10, 1, 50)
    pairs = await hybrid_search(ctx.db, ctx.rag_id, query, top_k=top_k, mode="dense")
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id, filename=c.file.filename if c.file else "",
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=fh.score,
        )
        for c, fh in pairs
    ]
    return ToolResult(summary=f"dense_search: {len(pool)} hits for {query!r}", pool=pool)


@tool("sparse_search")
async def sparse_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    query = _require_str(args, "query")
    top_k = _opt_int(args, "top_k", 10, 1, 50)
    pairs = await hybrid_search(ctx.db, ctx.rag_id, query, top_k=top_k, mode="sparse")
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id, filename=c.file.filename if c.file else "",
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=fh.score,
        )
        for c, fh in pairs
    ]
    return ToolResult(summary=f"sparse_search: {len(pool)} hits for {query!r}", pool=pool)


# ---------------- expansion tools ----------------


@tool("decompose_and_search")
async def decompose_and_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    query = _require_str(args, "query")
    max_sub = _opt_int(args, "max_subqueries", 4, 1, 6)
    top_k_each = _opt_int(args, "top_k_each", 6, 1, 20)

    sub_prompt = [
        {
            "role": "system",
            "content": (
                "Break the user's question into independent sub-questions that can each be answered "
                "by a single retrieval. Return JSON: {\"subqueries\": [\"...\", \"...\"]}. "
                f"Return at most {max_sub} short, self-contained subqueries."
            ),
        },
        {"role": "user", "content": query},
    ]
    raw = await chat(
        sub_prompt,
        model=ctx.rag_models.get("llm_model"),
        temperature=0.1,
        response_format={"type": "json_object"},
        base_url=ctx.rag_models.get("llm_base_url"),
        api_key=ctx.rag_models.get("llm_api_key"),
        provider_order=ctx.rag_models.get("llm_provider_order"),
    )
    try:
        sub = [s for s in json.loads(raw).get("subqueries", []) if isinstance(s, str) and s.strip()]
    except Exception:
        sub = [query]
    sub = sub[:max_sub] or [query]

    seen: dict[uuid.UUID, PoolEntry] = {}
    for q in sub:
        pairs = await hybrid_search(ctx.db, ctx.rag_id, q, top_k=top_k_each, mode="hybrid")
        for c, fh in pairs:
            existing = seen.get(c.id)
            if existing is None or fh.score > existing.score:
                seen[c.id] = PoolEntry(
                    chunk_id=c.id, file_id=c.file_id,
                    filename=c.file.filename if c.file else "",
                    page_start=c.page_start, page_end=c.page_end, heading=c.heading,
                    text=c.text, score=fh.score,
                )
    pool = sorted(seen.values(), key=lambda p: p.score, reverse=True)
    return ToolResult(
        summary=f"decompose_and_search: {len(sub)} subqueries → {len(pool)} unique chunks",
        pool=pool,
        data={"subqueries": sub},
    )


@tool("hyde_search")
async def hyde_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    query = _require_str(args, "query")
    top_k = _opt_int(args, "top_k", 10, 1, 50)
    hyde_prompt = [
        {
            "role": "system",
            "content": (
                "Write a short (3-5 sentence) hypothetical passage that would directly answer "
                "the user's question, using domain-style language. Do not hedge. Output plain text."
            ),
        },
        {"role": "user", "content": query},
    ]
    passage = await chat(
        hyde_prompt,
        model=ctx.rag_models.get("llm_model"),
        temperature=0.2,
        base_url=ctx.rag_models.get("llm_base_url"),
        api_key=ctx.rag_models.get("llm_api_key"),
        provider_order=ctx.rag_models.get("llm_provider_order"),
    )
    vec = await embed_query(
        passage if passage.strip() else query,
        rag_models=ctx.rag_models,
    )
    qhits = await qdrant_client.search_dense(ctx.rag_id, vec, top_k=top_k)
    chunk_ids: list[uuid.UUID] = []
    score_by_id: dict[uuid.UUID, float] = {}
    for h in qhits:
        cid = (h.payload or {}).get("chunk_id") or h.point_id
        try:
            u = uuid.UUID(cid)
            chunk_ids.append(u)
            score_by_id[u] = h.score
        except (ValueError, TypeError):
            continue
    if not chunk_ids:
        return ToolResult(summary="hyde_search: 0 hits", data={"hyde": passage})
    res = await ctx.db.execute(
        select(Chunk).where(Chunk.id.in_(chunk_ids))
    )
    rows = list(res.scalars().all())
    files = {
        r[0]: r[1]
        for r in (
            await ctx.db.execute(
                select(File.id, File.filename).where(File.id.in_({c.file_id for c in rows}))
            )
        ).all()
    }
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id,
            filename=files.get(c.file_id, ""),
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=score_by_id.get(c.id, 0.0),
        )
        for c in rows
    ]
    pool.sort(key=lambda p: p.score, reverse=True)
    return ToolResult(
        summary=f"hyde_search: {len(pool)} hits (hyde={passage[:80]!r}...)",
        pool=pool,
        data={"hyde": passage},
    )


@tool("exact_lookup")
async def exact_lookup_tool(ctx: AgentContext, args: dict) -> ToolResult:
    pattern = _require_str(args, "pattern")
    top_k = _opt_int(args, "top_k", 20, 1, 50)
    try:
        re.compile(pattern)
    except re.error as e:
        return ToolResult(summary=f"exact_lookup: invalid regex ({e})")

    res = await ctx.db.execute(
        select(Chunk).where(Chunk.rag_id == ctx.rag_id, Chunk.text.op("~*")(pattern)).limit(top_k)
    )
    rows = list(res.scalars().all())
    file_ids = {c.file_id for c in rows}
    file_map = {
        f.id: f.filename
        for f in (
            await ctx.db.execute(select(File).where(File.id.in_(file_ids)))
        ).scalars().all()
    }
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id, filename=file_map.get(c.file_id, ""),
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=1.0,
        )
        for c in rows
    ]
    return ToolResult(summary=f"exact_lookup: {len(pool)} matches for /{pattern}/", pool=pool)


# ---------------- fetch tools ----------------


@tool("fetch_page")
async def fetch_page_tool(ctx: AgentContext, args: dict) -> ToolResult:
    file_id_raw = _require_str(args, "file_id")
    try:
        file_id = uuid.UUID(file_id_raw)
    except ValueError:
        return ToolResult(summary=f"fetch_page: invalid file_id {file_id_raw!r}")
    page = _opt_int(args, "page", 1, 1, 100_000)

    res = await ctx.db.execute(
        select(Chunk)
        .where(
            Chunk.rag_id == ctx.rag_id,
            Chunk.file_id == file_id,
            Chunk.page_start <= page,
            Chunk.page_end >= page,
        )
        .order_by(Chunk.chunk_index)
    )
    rows = list(res.scalars().all())
    if not rows:
        return ToolResult(summary=f"fetch_page: no chunks for file={file_id} page={page}")
    file_row = (
        await ctx.db.execute(select(File).where(File.id == file_id))
    ).scalar_one_or_none()
    fname = file_row.filename if file_row else ""
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id, filename=fname,
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=1.0,
        )
        for c in rows
    ]
    return ToolResult(
        summary=f"fetch_page: {len(pool)} chunks on page {page} of {fname}", pool=pool
    )


@tool("fetch_document")
async def fetch_document_tool(ctx: AgentContext, args: dict) -> ToolResult:
    file_id_raw = _require_str(args, "file_id")
    try:
        file_id = uuid.UUID(file_id_raw)
    except ValueError:
        return ToolResult(summary=f"fetch_document: invalid file_id {file_id_raw!r}")
    max_pages = _opt_int(args, "max_pages", 50, 1, 200)

    file_row = (
        await ctx.db.execute(select(File).where(File.id == file_id))
    ).scalar_one_or_none()
    if file_row is None:
        return ToolResult(summary=f"fetch_document: file not found")

    res = await ctx.db.execute(
        select(Chunk)
        .where(
            Chunk.rag_id == ctx.rag_id,
            Chunk.file_id == file_id,
            Chunk.page_start <= max_pages,
        )
        .order_by(Chunk.chunk_index)
    )
    rows = list(res.scalars().all())
    pool = [
        PoolEntry(
            chunk_id=c.id, file_id=c.file_id, filename=file_row.filename,
            page_start=c.page_start, page_end=c.page_end, heading=c.heading,
            text=c.text, score=1.0,
        )
        for c in rows
    ]
    return ToolResult(
        summary=f"fetch_document: {len(pool)} chunks (≤{max_pages} pages) from {file_row.filename}",
        pool=pool,
    )


@tool("list_files")
async def list_files_tool(ctx: AgentContext, args: dict) -> ToolResult:
    rows = (
        await ctx.db.execute(select(File).where(File.rag_id == ctx.rag_id))
    ).scalars().all()
    data = [
        {"file_id": str(f.id), "filename": f.filename, "pages": f.pages}
        for f in rows
    ]
    lines = "\n".join(f"  - {d['file_id']}  {d['filename']}  (pages={d['pages']})" for d in data)
    return ToolResult(
        summary=f"list_files: {len(data)} file(s)\n{lines}",
        data={"files": data},
    )


# ---------------- scratchpad ----------------


@tool("cache_fact")
async def cache_fact_tool(ctx: AgentContext, args: dict) -> ToolResult:
    key = _require_str(args, "key")
    value = _require_str(args, "value")
    ctx.scratchpad[key] = value
    return ToolResult(summary=f"cache_fact: stored {key!r}")


@tool("recall_fact")
async def recall_fact_tool(ctx: AgentContext, args: dict) -> ToolResult:
    key = _require_str(args, "key")
    val = ctx.scratchpad.get(key)
    if val is None:
        return ToolResult(summary=f"recall_fact: {key!r} not found")
    return ToolResult(summary=f"recall_fact: {key!r} = {val}", data={"value": val})


@tool("rerank_pool")
async def rerank_pool_tool(ctx: AgentContext, args: dict) -> ToolResult:
    """LLM-rerank the current evidence pool against the user query (or override).

    Does NOT issue new searches. Updates pool order/score in-place; the loop will
    refresh the prompt with the new ranking on the next step.
    """
    query = args.get("query") or ctx.user_query
    if not isinstance(query, str) or not query.strip():
        return ToolResult(summary="rerank_pool: no query available")
    top_n = _opt_int(args, "top_n", 10, 1, 30)
    blend = args.get("blend", 0.3)
    try:
        blend = float(blend)
    except (TypeError, ValueError):
        blend = 0.3
    blend = max(0.0, min(1.0, blend))

    if not ctx.pool_ref:
        return ToolResult(summary="rerank_pool: pool is empty, run a search first")

    cands = [
        RerankItem(chunk_id=p.chunk_id, text=p.text, score=p.score) for p in ctx.pool_ref
    ]
    res = await llm_rerank(
        query.strip(),
        cands,
        top_n=top_n,
        blend_with_retrieval=blend,
        model=ctx.rag_models.get("llm_rerank_model"),
        base_url=ctx.rag_models.get("llm_base_url"),
        api_key=ctx.rag_models.get("llm_api_key"),
        provider_order=ctx.rag_models.get("llm_provider_order"),
    )
    new_scores = {r.chunk_id: r.score for r in res.items}

    # Mutate in-place: keep all chunks, update scores for those ranked; demote others.
    for p in ctx.pool_ref:
        if p.chunk_id in new_scores:
            p.score = new_scores[p.chunk_id]
        else:
            p.score = max(0.0, p.score * 0.1)  # decay un-ranked
    ctx.pool_ref.sort(key=lambda x: x.score, reverse=True)

    return ToolResult(
        summary=(
            f"rerank_pool: reranked {len(cands)} → top_n={top_n} "
            f"(used_llm={res.used_llm}, blend={blend})"
        ),
        data={"top_n": top_n, "used_llm": res.used_llm},
    )


@tool("web_search")
async def web_search_tool(ctx: AgentContext, args: dict) -> ToolResult:
    """DuckDuckGo web search. Gated by per-RAG setting `web_search_enabled`."""
    if not ctx.rag_settings.get("web_search_enabled"):
        return ToolResult(
            summary="web_search is disabled for this RAG; ignore this tool and rely on documents."
        )
    from app.clients.web_search import search_web

    query = _require_str(args, "query")
    max_results = _opt_int(args, "max_results", 5, 1, 10)
    hits = await search_web(query, max_results=max_results)
    if not hits:
        return ToolResult(summary=f"web_search: 0 results for {query!r}")
    lines = [f"web_search: {len(hits)} result(s) for {query!r}"]
    for i, h in enumerate(hits, start=1):
        title = (h.title or "(no title)")[:140]
        url = h.url or ""
        snippet = (h.snippet or "").replace("\n", " ").strip()[:400]
        lines.append(f"  [{i}] {title}\n      {url}\n      {snippet}")
    return ToolResult(
        summary="\n".join(lines),
        data={
            "results": [
                {"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits
            ]
        },
    )


async def dispatch(ctx: AgentContext, name: str, args: dict) -> ToolResult:
    fn = REGISTRY.get(name)
    if fn is None:
        return ToolResult(summary=f"unknown tool {name!r}. Available: {sorted(REGISTRY)}")
    try:
        return await fn(ctx, args)
    except Exception as e:
        return ToolResult(summary=f"tool {name!r} failed: {type(e).__name__}: {e}")
