from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from app.config import get_settings
from app.ingestion.parser import ParsedPage


@lru_cache
def _enc() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_enc().encode(text))


@dataclass
class Chunk:
    index: int
    text: str
    page_start: int
    page_end: int
    heading: str | None
    token_count: int
    is_table: bool = False
    table_rows: int = 0
    table_cols: int = 0
    table_orientation: str = "vertical"
    table_labels: list[str] | None = None


_HEADING_PATTERN = re.compile(
    r"^\s*("
    r"(?:CHAPTER|ARTICLE|SECTION|PART|SCHEDULE|APPENDIX|ANNEX)\s+[\w\.\-]+"
    r"|\d+(?:\.\d+){0,3}\s+[A-Z][^\n]{2,120}"
    r")\s*$",
    re.MULTILINE,
)

_SENT_SPLIT = re.compile(r"(?<=[\.\!\?])\s+(?=[A-ZА-ЯЁ])")


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _find_heading(text: str) -> str | None:
    m = _HEADING_PATTERN.search(text)
    return m.group(1).strip() if m else None


def chunk_pages(pages: list[ParsedPage]) -> list[Chunk]:
    """Greedy sentence-packing chunker with page-tracking and heading capture.

    Aim for [min_tokens, max_tokens] per chunk. Carry a small token overlap
    from the previous chunk to preserve context.
    """
    s = get_settings()
    max_tokens = s.chunk_max_tokens
    min_tokens = s.chunk_min_tokens
    overlap = s.chunk_overlap

    out: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    buf_page_start: int | None = None
    buf_page_end: int | None = None
    current_heading: str | None = None
    idx = 0

    def flush():
        nonlocal buf, buf_tokens, buf_page_start, buf_page_end, idx
        if not buf:
            return
        text = " ".join(buf).strip()
        if not text:
            buf = []
            buf_tokens = 0
            buf_page_start = None
            buf_page_end = None
            return
        out.append(
            Chunk(
                index=idx,
                text=text,
                page_start=buf_page_start or 1,
                page_end=buf_page_end or buf_page_start or 1,
                heading=current_heading,
                token_count=buf_tokens,
            )
        )
        idx += 1
        if overlap > 0 and buf:
            tail_tokens = 0
            tail: list[str] = []
            for sent in reversed(buf):
                t = count_tokens(sent)
                if tail_tokens + t > overlap:
                    break
                tail.insert(0, sent)
                tail_tokens += t
            buf = tail
            buf_tokens = tail_tokens
        else:
            buf = []
            buf_tokens = 0
        buf_page_start = buf_page_end
        # buf_page_end stays

    for page in pages:
        # Tables: each detected table becomes one ATOMIC chunk — never sentence-split.
        # If a single table is bigger than max_tokens, we still keep it intact
        # (a downstream LLM has 32k+ context; better one big chunk than half-rows).
        for tbl in getattr(page, "tables", []) or []:
            if buf and buf_tokens >= min_tokens:
                flush()
            tok = count_tokens(tbl.markdown)
            out.append(
                Chunk(
                    index=idx,
                    text=tbl.markdown,
                    page_start=tbl.page_number,
                    page_end=tbl.page_number,
                    heading=current_heading,
                    token_count=tok,
                    is_table=True,
                    table_rows=tbl.rows,
                    table_cols=tbl.cols,
                    table_orientation=tbl.orientation,
                    table_labels=list(tbl.labels or []),
                )
            )
            idx += 1

        page_text = page.text
        if not page_text:
            continue
        heading = _find_heading(page_text)
        if heading:
            current_heading = heading
        for sent in _split_sentences(page_text):
            t = count_tokens(sent)
            # If a single sentence is huge, hard-split by tokens
            if t > max_tokens:
                if buf and buf_tokens >= min_tokens:
                    flush()
                enc = _enc()
                ids = enc.encode(sent)
                for j in range(0, len(ids), max_tokens):
                    piece = enc.decode(ids[j : j + max_tokens])
                    out.append(
                        Chunk(
                            index=idx,
                            text=piece,
                            page_start=page.page_number,
                            page_end=page.page_number,
                            heading=current_heading,
                            token_count=min(max_tokens, len(ids) - j),
                        )
                    )
                    idx += 1
                continue
            if buf_tokens + t > max_tokens and buf_tokens >= min_tokens:
                flush()
            if buf_page_start is None:
                buf_page_start = page.page_number
            buf_page_end = page.page_number
            buf.append(sent)
            buf_tokens += t

    if buf and buf_tokens > 0:
        flush()
    return out
