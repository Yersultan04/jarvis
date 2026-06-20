"""LLM-generates short descriptions for detected tables.

The description is what gets EMBEDDED (so retrieval finds the table by its
semantic meaning — "interest rates by income bracket", "supplier contacts",
etc), while the chunk's actual `text` stays as the raw markdown table so the
agent can answer specific cell-level questions when the chunk is retrieved.
"""
from __future__ import annotations

import asyncio
import logging

from app.clients.llm import chat
from app.config import get_settings

log = logging.getLogger("table_describe")


_SYSTEM = (
    "You describe tabular data for a retrieval index. Given a markdown table, "
    "produce 2-4 sentences in the SAME LANGUAGE as the table that capture:\n"
    "1) what the table is about (subject / domain),\n"
    "2) what each field/label represents,\n"
    "3) any notable numeric ranges, units, or row identifiers.\n"
    "IMPORTANT: tables may be VERTICAL (headers in the first row, one record per "
    "row) or HORIZONTAL/TRANSPOSED (labels in the first column, each column is a "
    "variant or sample). You will be told the orientation. List the actual field "
    "names verbatim (e.g. \"Первоначальный взнос\", \"Срок\"). "
    "Do NOT invent data not in the table. Output plain prose, no bullet points, "
    "no preamble like 'This table'."
)


async def describe_table(
    markdown: str,
    *,
    filename: str | None = None,
    page: int | None = None,
    orientation: str = "vertical",
    labels: list[str] | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    provider_order: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return a 2-4 sentence description of the table. Empty string on failure."""
    s = get_settings()
    head_bits: list[str] = []
    if filename:
        head_bits.append(f"file: {filename}")
    if page is not None:
        head_bits.append(f"page: {page}")
    head_bits.append(f"orientation: {orientation}")
    if labels:
        head_bits.append("labels/fields: " + ", ".join(labels[:20]))
    header = (" | ".join(head_bits) + "\n\n") if head_bits else ""

    # Cap the table sent to the LLM — avoid blowing context on a giant table.
    md = markdown
    if len(md) > 12_000:
        md = md[:12_000] + "\n…(table truncated for description)"

    try:
        return (
            await chat(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": f"{header}{md}"},
                ],
                model=model or s.llm_model,
                temperature=0.2,
                max_tokens=400,
                base_url=base_url,
                api_key=api_key,
                provider_order=provider_order,
            )
        ).strip()
    except Exception as e:
        log.warning("describe_table failed (file=%s page=%s): %s", filename, page, e)
        return ""


async def describe_tables_batched(
    items: list[dict],
    *,
    concurrency: int = 4,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    provider_order: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Describe a list of tables concurrently. Each item: {markdown, filename?, page?}.
    Returns descriptions in input order. Empty string for failures.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(it: dict) -> str:
        async with sem:
            return await describe_table(
                it["markdown"],
                filename=it.get("filename"),
                page=it.get("page"),
                orientation=it.get("orientation", "vertical"),
                labels=it.get("labels"),
                model=model,
                base_url=base_url,
                api_key=api_key,
                provider_order=provider_order,
            )

    return await asyncio.gather(*[one(it) for it in items])
