from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.agent.schemas import Citation, PoolEntry


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\W_]+", re.UNICODE)


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def _norm_loose(s: str) -> str:
    """Aggressive normalization for fuzzy match — strip all non-alphanumerics."""
    return _PUNCT.sub(" ", s).lower()


@dataclass
class CitationCheck:
    citation: Citation
    grounded: bool
    method: str  # "substring" | "fuzzy" | "none"
    score: float  # 0..1
    note: str


@dataclass
class GroundingReport:
    checks: list[CitationCheck]
    grounded_count: int
    total: int
    adjusted_confidence: float

    @property
    def fraction(self) -> float:
        return self.grounded_count / self.total if self.total else 1.0


_SUBSTRING_MIN_LEN = 16  # treat very short quotes as too weak to be authoritative
_FUZZY_THRESHOLD = 0.78


def _best_substring_ratio(needle: str, haystack: str) -> float:
    """Sliding ratio between needle and best matching haystack window."""
    n = len(needle)
    if n == 0 or len(haystack) == 0:
        return 0.0
    if n >= len(haystack):
        return SequenceMatcher(None, needle, haystack).ratio()
    # Use SequenceMatcher.get_matching_blocks heuristic via a single ratio call.
    return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()


def check_citation(citation: Citation, pool_by_id: dict[uuid.UUID, PoolEntry]) -> CitationCheck:
    chunk = pool_by_id.get(citation.chunk_id)
    if chunk is None:
        return CitationCheck(citation, False, "none", 0.0, "chunk not in pool")
    quote = citation.quote or ""
    if not quote.strip():
        return CitationCheck(citation, False, "none", 0.0, "empty quote")

    q_norm = _norm(quote)
    t_norm = _norm(chunk.text)
    if len(q_norm) >= _SUBSTRING_MIN_LEN and q_norm in t_norm:
        return CitationCheck(citation, True, "substring", 1.0, "exact substring match")

    q_loose = _norm_loose(quote)
    t_loose = _norm_loose(chunk.text)
    if len(q_loose) >= _SUBSTRING_MIN_LEN and q_loose in t_loose:
        return CitationCheck(citation, True, "substring", 0.95, "match after punctuation strip")

    ratio = _best_substring_ratio(q_norm, t_norm)
    if ratio >= _FUZZY_THRESHOLD:
        return CitationCheck(citation, True, "fuzzy", ratio, f"fuzzy ratio={ratio:.2f}")
    return CitationCheck(citation, False, "none", ratio, f"no support (best ratio={ratio:.2f})")


def ground_citations(
    citations: list[Citation],
    pool: list[PoolEntry],
    original_confidence: float,
) -> GroundingReport:
    pool_by_id = {p.chunk_id: p for p in pool}
    checks = [check_citation(c, pool_by_id) for c in citations]
    grounded = sum(1 for c in checks if c.grounded)
    total = len(checks)
    fraction = grounded / total if total else 1.0
    # Confidence is bounded by grounded fraction — you cannot claim 0.9 confidence
    # while half your citations don't ground.
    adjusted = min(original_confidence, max(0.0, fraction))
    return GroundingReport(
        checks=checks,
        grounded_count=grounded,
        total=total,
        adjusted_confidence=adjusted,
    )
