from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.agent.events import EventBroker
from app.agent.grounding import ground_citations
from app.agent.prompts import build_system_message
from app.agent.router import RouteDecision, route_query
from app.agent.schemas import (
    Citation,
    Escalation,
    FinalAnswer,
    NextStepEnvelope,
    PoolEntry,
    ToolCall,
)
from app.agent.tools import AgentContext, ToolResult, dispatch
from app.clients.llm import chat
from app.config import get_settings
from app.db import SessionLocal
from app.models import AgentRun, AgentRunStatus, ChatSession, Rag
from app.presets import resolve_models_for_rag

log = logging.getLogger("agent")

DEFAULT_MAX_STEPS = 40
HISTORY_KEEP_RECENT = 6
POOL_PROMPT_CAP = 12  # how many pool entries shown in the prompt at once
POOL_TEXT_CAP_CHARS = 700  # truncate each entry's text in the prompt
RECENT_TOOL_CALLS_BUFFER = 6
REPEAT_TOOL_LIMIT = 2  # max identical (tool, args) calls before nudge

# Chat-session memory: keep the last N turns verbatim, summarise the rest.
SESSION_RECENT_TURNS = 5


def _pool_dedup_merge(pool: list[PoolEntry], new: list[PoolEntry]) -> list[PoolEntry]:
    by_id: dict[uuid.UUID, PoolEntry] = {p.chunk_id: p for p in pool}
    for n in new:
        cur = by_id.get(n.chunk_id)
        if cur is None or n.score > cur.score:
            by_id[n.chunk_id] = n
    return sorted(by_id.values(), key=lambda p: p.score, reverse=True)


def _format_pool(pool: list[PoolEntry]) -> str:
    if not pool:
        return "(empty)"
    lines = []
    for i, p in enumerate(pool[:POOL_PROMPT_CAP], start=1):
        head = p.heading or "—"
        page = f"p.{p.page_start}" if p.page_start == p.page_end else f"p.{p.page_start}-{p.page_end}"
        snippet = (p.text or "").strip().replace("\n", " ")
        if len(snippet) > POOL_TEXT_CAP_CHARS:
            snippet = snippet[: POOL_TEXT_CAP_CHARS - 1] + "…"
        lines.append(
            f"[{i}] chunk_id={p.chunk_id} file_id={p.file_id} file={p.filename!r} "
            f"{page} score={p.score:.3f} heading={head!r}\n    {snippet}"
        )
    suffix = ""
    if len(pool) > POOL_PROMPT_CAP:
        suffix = f"\n…({len(pool) - POOL_PROMPT_CAP} more chunks in the pool, not shown)"
    return "\n".join(lines) + suffix


def _summarize_history(history: list[dict]) -> str:
    if not history:
        return "(no prior steps)"
    parts: list[str] = []
    for h in history:
        t = h.get("type")
        if t == "tool":
            parts.append(f"#{h['step']} tool={h['tool']} args={h['args']} → {h['summary'][:160]}")
        elif t == "error":
            parts.append(f"#{h['step']} error: {h['summary'][:160]}")
        elif t == "parse_error":
            parts.append(f"#{h['step']} invalid model output: {h['summary'][:160]}")
    return "\n".join(parts)


def _build_messages(
    query: str,
    pool: list[PoolEntry],
    history: list[dict],
    step_idx: int,
    max_steps: int,
    nudge: str | None,
    route: RouteDecision | None = None,
    prior_turns: list[dict] | None = None,
    web_search_enabled: bool = False,
) -> list[dict]:
    recent = history[-HISTORY_KEEP_RECENT:]
    older = history[:-HISTORY_KEEP_RECENT]

    summary_block = ""
    if older:
        summary_block = "EARLIER STEPS (compacted):\n" + _summarize_history(older) + "\n\n"

    recent_block = ""
    if recent:
        recent_lines: list[str] = []
        for h in recent:
            if h["type"] == "tool":
                recent_lines.append(
                    f"#{h['step']} TOOL_CALL tool={h['tool']} args={h['args']}\n"
                    f"OBSERVATION: {h['summary']}"
                )
            elif h["type"] == "error":
                recent_lines.append(f"#{h['step']} ERROR: {h['summary']}")
            elif h["type"] == "parse_error":
                recent_lines.append(f"#{h['step']} INVALID OUTPUT: {h['summary']}")
        recent_block = "RECENT STEPS:\n" + "\n\n".join(recent_lines) + "\n\n"

    nudge_block = f"NUDGE: {nudge}\n\n" if nudge else ""

    route_block = ""
    if route is not None and step_idx <= 2 and not pool:
        route_block = (
            "ROUTER HINT (advisory, use unless clearly wrong):\n"
            f"  kind={route.kind!r}, suggested_tool={route.suggested_tool!r}, "
            f"args={json.dumps(route.suggested_args, ensure_ascii=False)}, "
            f"confidence={route.confidence:.2f}, via={route.via}\n"
            f"  rationale: {route.rationale}\n\n"
        )

    prior_block = ""
    if prior_turns:
        lines = ["PRIOR CONVERSATION (same chat session — use for context only):"]
        for t in prior_turns:
            kind = t.get("kind", "turn")
            if kind == "summary":
                lines.append("  [Earlier in this conversation — summary]")
                summ = (t.get("summary") or "").strip()
                # indent each line so it sits inside the block
                for ln in summ.splitlines():
                    lines.append(f"    {ln}")
            else:
                q = (t.get("query") or "").strip().replace("\n", " ")
                a = (t.get("answer") or "").strip().replace("\n", " ")
                if len(q) > 240:
                    q = q[:240] + "…"
                if len(a) > 600:
                    a = a[:600] + "…"
                lines.append(f"  Q: {q}")
                if a:
                    lines.append(f"  A: {a}")
        prior_block = "\n".join(lines) + "\n\n"

    user_content = (
        f"{prior_block}"
        f"USER QUESTION:\n{query}\n\n"
        f"BUDGET: step {step_idx}/{max_steps}\n\n"
        f"{route_block}"
        f"EVIDENCE POOL ({len(pool)} chunks):\n{_format_pool(pool)}\n\n"
        f"{summary_block}{recent_block}{nudge_block}"
        "Decide the next step. Return ONE NextStepEnvelope JSON object."
    )

    return [
        {"role": "system", "content": build_system_message(web_search_enabled=web_search_enabled)},
        {"role": "user", "content": user_content},
    ]


def _extract_first_json_object(s: str) -> str:
    """Find the first balanced JSON object in `s`.

    Some models (e.g. gpt-5.4 on OpenRouter) ignore json-mode and emit either
    reasoning before/after JSON, or multiple JSON fragments. We slice out the
    first { ... } block by brace-counting at the top level (skipping braces
    inside string literals).
    """
    in_str = False
    esc = False
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return s[start : i + 1]
    return s  # fall back to whole string — let json.loads raise


def _parse_step(raw: str) -> NextStepEnvelope:
    raw = raw.strip()
    # Strip code fences if present.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    # Trim any prose around the actual JSON.
    candidate = _extract_first_json_object(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"Output is not valid JSON: {e}") from e
    # Tolerate models that emit the inner object directly.
    if "step" not in data and "kind" in data:
        data = {"step": data}
    return NextStepEnvelope.model_validate(data)


def _validate_citations(
    final: FinalAnswer, pool: list[PoolEntry]
) -> tuple[list[Citation], list[str]]:
    by_id = {p.chunk_id: p for p in pool}
    valid: list[Citation] = []
    warnings: list[str] = []
    for c in final.citations:
        p = by_id.get(c.chunk_id)
        if p is None:
            warnings.append(f"citation chunk_id={c.chunk_id} not in pool — dropped")
            continue
        valid.append(
            Citation(
                chunk_id=p.chunk_id,
                file_id=p.file_id,
                filename=p.filename,
                page_start=p.page_start,
                page_end=p.page_end,
                quote=c.quote[:400],
            )
        )
    return valid, warnings


async def _call_llm_for_step(
    messages: list[dict],
    step_idx: int,
    broker: EventBroker,
    llm_model: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    provider_order: list[str] | None = None,
) -> NextStepEnvelope:
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            raw = await chat(
                messages,
                model=llm_model,
                temperature=0.1,
                response_format={"type": "json_object"},
                max_tokens=4096,   # Jarvis $0: providers count (input + max_tokens) against TPM.
                                   # 30k reservation blew past Groq free-tier 12k TPM (413 errors).
                                   # 4096 fits the answer + citations for non-reasoning models
                                   # (llama-3.3-70b / gemini-flash) and keeps requests ~9.5k < 12k.
                base_url=base_url,
                api_key=api_key,
                provider_order=provider_order,
            )
            return _parse_step(raw)
        except (ValueError, ValidationError) as e:
            last_err = e
            await broker.publish(
                "parse_error",
                {"step": step_idx, "attempt": attempt, "error": str(e)[:300]},
            )
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous output was invalid. "
                        f"Reason: {e}. "
                        "Return ONE JSON object that validates against NextStepEnvelope. "
                        "No prose, no code fences."
                    ),
                }
            ]
    assert last_err is not None
    raise last_err


async def _summarise_turns(turns: list[tuple[str, str]], previous_summary: str | None) -> str:
    """Roll older Q/A turns into a compact running summary.

    `previous_summary` is the cached summary that already covers some of the
    earliest turns — we extend it rather than re-summarising from scratch.
    """
    if not turns:
        return previous_summary or ""

    s = get_settings()
    parts = []
    if previous_summary:
        parts.append(f"Existing summary so far:\n{previous_summary}\n")
    parts.append("New turns to fold in:")
    for i, (q, a) in enumerate(turns, start=1):
        q = (q or "").strip()
        a = (a or "").strip()
        if len(a) > 1500:
            a = a[:1500] + "…"
        parts.append(f"  T{i} USER: {q}\n  T{i} ASSISTANT: {a}")
    user_msg = "\n".join(parts)

    system_msg = (
        "You are maintaining a compact running summary of a chat session between a user "
        "and an agentic RAG assistant. Update / extend the summary so a future turn of "
        "the agent has the key context.\n\n"
        "Preserve precisely:\n"
        "- Named entities (companies, people, documents, codes, dates, numbers).\n"
        "- Decisions made or facts established by the assistant.\n"
        "- Open questions / loose ends the conversation is following up on.\n"
        "Drop chit-chat and small clarifications. Output 100-300 words, plain prose "
        "or short bullet points, in the SAME language as the conversation."
    )
    try:
        return (await chat(
            [{"role": "system", "content": system_msg},
             {"role": "user", "content": user_msg}],
            model=s.llm_model,
            temperature=0.2,
            max_tokens=800,
        )).strip()
    except Exception as e:
        log.warning("session summarise failed: %s", e)
        return previous_summary or ""


async def _build_session_context(
    db, session_id: uuid.UUID, current_run_id: uuid.UUID,
) -> list[dict]:
    """Return prior_turns blocks for the agent prompt:
       optional [summary] entry + the most recent SESSION_RECENT_TURNS verbatim turns.

    Also lazily updates / extends the cached summary on the ChatSession row so
    we don't re-summarise every request.
    """
    from app.models import ChatSession  # local to avoid cycles

    sess = (await db.execute(select(ChatSession).where(ChatSession.id == session_id))).scalar_one_or_none()
    if sess is None:
        return []

    past = (await db.execute(
        select(AgentRun)
        .where(
            AgentRun.session_id == session_id,
            AgentRun.id != current_run_id,
            AgentRun.status == AgentRunStatus.succeeded,
        )
        .order_by(AgentRun.created_at.asc())
    )).scalars().all()
    past = [r for r in past if r.answer]

    if len(past) <= SESSION_RECENT_TURNS:
        return [{"kind": "turn", "query": r.query, "answer": r.answer} for r in past]

    # We have older turns to compress.
    older = past[:-SESSION_RECENT_TURNS]
    recent = past[-SESSION_RECENT_TURNS:]

    # Find which older turns aren't yet covered by sess.summary.
    already_covered_idx = -1
    if sess.summary_through_run_id is not None:
        for i, r in enumerate(older):
            if r.id == sess.summary_through_run_id:
                already_covered_idx = i
                break
    new_turns = [(r.query, r.answer or "") for r in older[already_covered_idx + 1 :]]

    if new_turns or sess.summary is None:
        sess.summary = await _summarise_turns(new_turns, sess.summary)
        if older:
            sess.summary_through_run_id = older[-1].id
        await db.commit()

    out: list[dict] = []
    if sess.summary:
        out.append({"kind": "summary", "summary": sess.summary})
    out.extend({"kind": "turn", "query": r.query, "answer": r.answer} for r in recent)
    return out


async def run_agent(rag_id: uuid.UUID, run_id: uuid.UUID, query: str, max_steps: int) -> None:
    """Background entrypoint. Owns its own DB session and event broker."""
    t0 = time.monotonic()
    broker = await EventBroker.get_or_create(rag_id, run_id)

    async with SessionLocal() as db:
        run = (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one()
        rag = (await db.execute(select(Rag).where(Rag.id == rag_id))).scalar_one()
        run.status = AgentRunStatus.running
        run.started_at = datetime.now(timezone.utc)
        await db.commit()

        await broker.publish(
            "run_started",
            {"run_id": str(run_id), "rag_id": str(rag_id), "query": query, "max_steps": max_steps},
        )

        pool: list[PoolEntry] = []
        rag_settings = dict(rag.settings or {})
        rag_models = resolve_models_for_rag(rag)
        ctx = AgentContext(
            rag_id=rag.id, db=db, user_query=query, pool_ref=pool,
            rag_settings=rag_settings,
            rag_models=rag_models,
        )
        history: list[dict] = []

        # Pull previous turns of the same chat session, if any.
        prior_turns: list[dict] = []
        if run.session_id is not None:
            prior_turns = await _build_session_context(db, run.session_id, run.id)

        # Pre-loop: route the query
        try:
            route = await route_query(query)
            await broker.publish(
                "router_decision",
                {
                    "kind": route.kind,
                    "tool": route.suggested_tool,
                    "args": route.suggested_args,
                    "rationale": route.rationale,
                    "confidence": route.confidence,
                    "via": route.via,
                    "companions": [t for t, _ in route.companions],
                },
            )
        except Exception as e:
            log.warning("router failed: %s", e)
            route = None
            await broker.publish("router_failed", {"error": str(e)[:200]})

        recent_tool_calls: list[tuple[str, str]] = []

        # Pre-search: run the primary suggested tool + its companions in parallel
        # BEFORE the agent's first reasoning step. This combines precision
        # (exact_lookup with a regex) with recall (hybrid_search). The agent
        # sees a populated pool on step 1 and can often finalize immediately.
        if route is not None and route.confidence >= 0.6:
            pre_calls: list[tuple[str, dict]] = [
                (route.suggested_tool, route.suggested_args)
            ]
            pre_calls.extend(route.companions)

            async def _pre_run(tool: str, args: dict) -> tuple[str, dict, ToolResult | None]:
                try:
                    res = await dispatch(ctx, tool, args)
                    return tool, args, res
                except Exception as exc:
                    log.warning("pre-search %s failed: %s", tool, exc)
                    return tool, args, None

            results = await asyncio.gather(*[_pre_run(t, a) for t, a in pre_calls])
            for tool, args, res in results:
                if res is None:
                    continue
                if res.pool:
                    pool[:] = _pool_dedup_merge(pool, res.pool)
                sig = (tool, json.dumps(args, sort_keys=True, default=str))
                recent_tool_calls.append(sig)
                history.append({
                    "step": 0, "type": "tool", "tool": tool, "args": args,
                    "summary": res.summary,
                })
                await broker.publish(
                    "pre_search",
                    {
                        "tool": tool, "args": args, "summary": res.summary,
                        "pool_size": len(pool),
                    },
                )
        nudge: str | None = None
        final_obj: FinalAnswer | None = None
        escalated: Escalation | None = None
        terminated_reason: str | None = None
        steps_used = 0

        try:
            for step in range(1, max_steps + 1):
                steps_used = step
                messages = _build_messages(
                    query, pool, history, step, max_steps, nudge,
                    route=route, prior_turns=prior_turns,
                    web_search_enabled=bool(rag_settings.get("web_search_enabled")),
                )
                nudge = None
                try:
                    envelope = await _call_llm_for_step(
                        messages, step, broker,
                        llm_model=rag_models["llm_model"],
                        base_url=rag_models.get("llm_base_url"),
                        api_key=rag_models.get("llm_api_key"),
                        provider_order=rag_models.get("llm_provider_order"),
                    )
                except Exception as e:
                    history.append(
                        {"step": step, "type": "parse_error", "summary": f"LLM/parse failed: {e}"}
                    )
                    await broker.publish("step_failed", {"step": step, "error": str(e)[:300]})
                    nudge = "Previous step couldn't be parsed twice. Try a simpler tool call or finalize."
                    continue

                next_step = envelope.step
                await broker.publish(
                    "thought",
                    {"step": step, "thought": next_step.thought, "kind": next_step.kind},
                )

                if isinstance(next_step, FinalAnswer):
                    valid_cites, warns = _validate_citations(next_step, pool)
                    if not valid_cites and pool:
                        # Refuse to finalize without grounding when we have evidence available.
                        nudge = (
                            "You must include at least one citation from the EVIDENCE POOL "
                            "(chunk_id must match). Cite or escalate."
                        )
                        await broker.publish(
                            "final_rejected",
                            {"step": step, "reason": "no_valid_citations", "warnings": warns},
                        )
                        history.append(
                            {
                                "step": step,
                                "type": "error",
                                "summary": "final_answer had no valid citations; retrying",
                            }
                        )
                        continue
                    report = ground_citations(valid_cites, pool, next_step.confidence)
                    await broker.publish(
                        "grounding_report",
                        {
                            "step": step,
                            "grounded": report.grounded_count,
                            "total": report.total,
                            "fraction": round(report.fraction, 3),
                            "adjusted_confidence": round(report.adjusted_confidence, 3),
                            "checks": [
                                {
                                    "chunk_id": str(c.citation.chunk_id),
                                    "grounded": c.grounded,
                                    "method": c.method,
                                    "score": round(c.score, 3),
                                    "note": c.note,
                                }
                                for c in report.checks
                            ],
                        },
                    )
                    final_obj = FinalAnswer(
                        thought=next_step.thought,
                        answer=next_step.answer,
                        citations=valid_cites,
                        confidence=report.adjusted_confidence,
                    )
                    await broker.publish(
                        "final_answer",
                        {
                            "step": step,
                            "answer": final_obj.answer,
                            "confidence": final_obj.confidence,
                            "raw_confidence": next_step.confidence,
                            "citations": [c.model_dump(mode="json") for c in final_obj.citations],
                            "warnings": warns,
                        },
                    )
                    terminated_reason = "final"
                    break

                if isinstance(next_step, Escalation):
                    escalated = next_step
                    await broker.publish(
                        "escalated",
                        {
                            "step": step,
                            "reason": next_step.reason,
                            "confidence": next_step.confidence,
                        },
                    )
                    terminated_reason = "escalated"
                    break

                assert isinstance(next_step, ToolCall)
                sig = (next_step.tool, json.dumps(next_step.args, sort_keys=True, default=str))
                if recent_tool_calls.count(sig) >= REPEAT_TOOL_LIMIT:
                    nudge = (
                        f"You called {next_step.tool} with the same args {REPEAT_TOOL_LIMIT}+ times. "
                        "Try a different tool or argument. If the pool is empty for this question, "
                        "consider hyde_search, decompose_and_search, or escalate."
                    )
                    history.append(
                        {
                            "step": step,
                            "type": "error",
                            "summary": f"repeated tool call blocked: {next_step.tool}",
                        }
                    )
                    await broker.publish(
                        "tool_blocked",
                        {"step": step, "tool": next_step.tool, "reason": "repeat"},
                    )
                    continue
                recent_tool_calls.append(sig)
                recent_tool_calls = recent_tool_calls[-RECENT_TOOL_CALLS_BUFFER:]

                await broker.publish(
                    "tool_call",
                    {"step": step, "tool": next_step.tool, "args": next_step.args},
                )
                result: ToolResult = await dispatch(ctx, next_step.tool, next_step.args)
                if result.pool:
                    merged = _pool_dedup_merge(pool, result.pool)
                    pool.clear()
                    pool.extend(merged)

                history.append(
                    {
                        "step": step,
                        "type": "tool",
                        "tool": next_step.tool,
                        "args": next_step.args,
                        "summary": result.summary,
                    }
                )
                await broker.publish(
                    "observation",
                    {
                        "step": step,
                        "tool": next_step.tool,
                        "summary": result.summary,
                        "pool_size": len(pool),
                        "data": result.data,
                    },
                )
            else:
                # max_steps exhausted without break
                terminated_reason = "max_steps"
                await broker.publish(
                    "budget_exhausted",
                    {"step": steps_used, "pool_size": len(pool)},
                )

            # Persist run result
            if final_obj is not None:
                run.status = AgentRunStatus.succeeded
                run.answer = final_obj.answer
                run.citations = [c.model_dump(mode="json") for c in final_obj.citations]
                run.confidence = final_obj.confidence
            elif escalated is not None:
                run.status = AgentRunStatus.escalated
                run.error = escalated.reason
                run.confidence = escalated.confidence
            else:
                run.status = AgentRunStatus.failed
                run.error = (
                    "Step budget exhausted without producing a final answer or escalation."
                )

            run.steps_used = steps_used
            run.telemetry = {
                "elapsed_sec": round(time.monotonic() - t0, 3),
                "pool_size": len(pool),
                "scratchpad_keys": sorted(ctx.scratchpad.keys()),
                "terminated_reason": terminated_reason,
                "prior_turns_used": len(prior_turns),
                "router": (
                    {
                        "kind": route.kind,
                        "tool": route.suggested_tool,
                        "via": route.via,
                        "confidence": route.confidence,
                    }
                    if route is not None
                    else None
                ),
            }
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()

            await broker.publish(
                "run_finished",
                {
                    "status": run.status.value,
                    "steps_used": steps_used,
                    "elapsed_sec": run.telemetry["elapsed_sec"],
                },
            )
        except Exception as e:
            log.exception("agent run %s crashed", run_id)
            run.status = AgentRunStatus.failed
            run.error = f"{type(e).__name__}: {e}"
            run.finished_at = datetime.now(timezone.utc)
            run.steps_used = steps_used
            await db.commit()
            await broker.publish("run_failed", {"error": run.error})
        finally:
            await broker.close()
            await EventBroker.pop(run_id)
