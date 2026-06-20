from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.clients.llm import chat

log = logging.getLogger("router")


@dataclass
class RouteDecision:
    kind: str  # "lookup" | "multi_entity" | "aggregate" | "definition" | "free_text"
    suggested_tool: str
    suggested_args: dict
    rationale: str
    confidence: float
    via: str  # "regex" | "llm"
    # Tools to run in parallel with the primary BEFORE the agent's first step,
    # to combine precision (exact_lookup) with recall (hybrid_search). Each is
    # (tool_name, args).
    companions: list[tuple[str, dict]] = field(default_factory=list)


def _attach_companions(route: RouteDecision, query: str) -> RouteDecision:
    """Pair complementary tools so the first pool always has both precision and recall.

    Currently: any exact_lookup gets hybrid_search as a companion. The user's
    literal regex catches verbatim matches; the embedder catches paraphrases
    and surrounding context.
    """
    if route.suggested_tool == "exact_lookup":
        route.companions.append((
            "hybrid_search",
            {"query": query, "top_k": 10},
        ))
    return route


# The ONLY regex preempt: when the user explicitly quoted a phrase, that's an
# unambiguous "search for this verbatim" intent — no LLM judgement needed.
# Everything else (codes, percentages, dates, named values) is handled by the
# LLM router: it sees the full query and decides whether the right tool is
# exact_lookup (with a tolerant pattern) or a semantic search.
_QUOTED = re.compile(
    r"""
    "([^"]{2,80})"                # "text"
    | «([^»]{2,80})»              # «text»
    | „([^"]{2,80})"              # „text"
    """,
    re.VERBOSE,
)


_ROUTE_SYSTEM = (
    "You are a query router for a RAG system over a private document collection. "
    "Classify the user query and pick the best first tool to retrieve evidence.\n\n"
    "OUTPUT JSON exactly:\n"
    '{"kind": "lookup|multi_entity|aggregate|definition|free_text",\n'
    ' "tool":  "hybrid_search|dense_search|sparse_search|decompose_and_search|hyde_search|exact_lookup|list_files",\n'
    ' "args":  { … tool-specific arguments … },\n'
    ' "rationale": "<one short sentence>",\n'
    ' "confidence": <0.0..1.0>}\n\n'
    "TOOL SELECTION (read CAREFULLY):\n\n"
    "1. exact_lookup — ALWAYS use this when the query references something the user "
    "would EXPECT to find verbatim in the documents. Examples that should route here:\n"
    "  • specific identifier code: \"RM-2\", \"OSON-3\", \"SC-7\", \"ARB/2025\"\n"
    "  • specific number with unit: \"13%\", \"25 000 сум\", \"$1200\", \"5 лет\"\n"
    "  • specific date: \"12.05.2024\", \"в 2025 году\"\n"
    "  • specific field/term from a known domain: \"первоначальный взнос\", \"процентная ставка\", \"срок кредита\"\n"
    "  • a quoted phrase: \"найди упоминания X\"\n"
    "  Build args.pattern as a TOLERANT POSIX regex (case-insensitive). Allow flex:\n"
    "    - whitespace: \\s+\n"
    "    - decimal separator: [.,]\n"
    "    - punctuation between chars of a code: [-/_ ]?\n"
    "    - inflection of Russian words: use stem + \\w*, e.g. ставк\\w+, первоначальн\\w+\\s+взнос\n"
    "  GOOD patterns:\n"
    '    "ставк\\\\w*\\\\s+13[.,]?5?\\\\s*%"   ← rate ~13%\n'
    '    "первоначальн\\\\w+\\\\s+взнос"      ← \"первоначальный взнос\"\n'
    '    "(RM|SC|OSON)[-/_ ]?\\\\d+"           ← any of those codes\n\n'
    "2. decompose_and_search — multi-entity / comparative / aggregate questions "
    "(\"compare X and Y\", \"сколько всего\", \"each of the products\").\n\n"
    "3. hyde_search — abstract / paraphrased questions where the user's vocabulary "
    "is unlikely to appear in the corpus verbatim.\n\n"
    "4. hybrid_search — default for free-text questions that don't fit above.\n\n"
    "Be opportunistic about exact_lookup: if there is ANY literal token the user "
    "names, prefer it over hybrid_search. Even if uncertain, set confidence 0.7+ "
    "and let downstream tools also run."
)


def _regex_route(query: str) -> RouteDecision | None:
    """Single-purpose fast path: when the user explicitly quotes a phrase, use
    it verbatim — that's an unambiguous signal that doesn't need an LLM call.

    All other "looks like a literal" decisions (codes, percentages, dates,
    precise field names) are delegated to the LLM router below, which has the
    full query context and won't miss things our regex would never anticipate
    ("в районе 13 процентов", "ID начинается с RM").
    """
    qm = _QUOTED.search(query)
    if qm is not None:
        phrase = next(g for g in qm.groups() if g)
        pattern = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
        return RouteDecision(
            kind="lookup",
            suggested_tool="exact_lookup",
            suggested_args={"pattern": pattern, "top_k": 20},
            rationale=f"user quoted phrase: {phrase!r}",
            confidence=0.95,
            via="regex",
        )
    return None


async def _llm_route(query: str) -> RouteDecision | None:
    try:
        raw = await chat(
            [
                {"role": "system", "content": _ROUTE_SYSTEM},
                {"role": "user", "content": query},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        data = json.loads(raw)
    except Exception as e:
        log.warning("llm_route failed: %s", e)
        return None

    kind = data.get("kind") or "free_text"
    tool = data.get("tool") or "hybrid_search"
    args = data.get("args") or {}
    if tool in {"hybrid_search", "dense_search", "sparse_search", "hyde_search"} and "query" not in args:
        args["query"] = query
    if tool == "decompose_and_search" and "query" not in args:
        args["query"] = query
    return RouteDecision(
        kind=str(kind),
        suggested_tool=str(tool),
        suggested_args=args if isinstance(args, dict) else {},
        rationale=str(data.get("rationale", "")),
        confidence=float(data.get("confidence", 0.5)),
        via="llm",
    )


async def route_query(query: str) -> RouteDecision:
    fast = _regex_route(query)
    if fast is not None and fast.confidence >= 0.8:
        return _attach_companions(fast, query)
    llm = await _llm_route(query)
    if llm is not None:
        return _attach_companions(llm, query)
    if fast is not None:
        return _attach_companions(fast, query)
    return RouteDecision(
        kind="free_text",
        suggested_tool="hybrid_search",
        suggested_args={"query": query, "top_k": 10},
        rationale="fallback default",
        confidence=0.3,
        via="regex",
    )
