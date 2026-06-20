"""Web search via DuckDuckGo HTML endpoints. No API key required.

Rate-limited by upstream — keep `max_results` modest and don't loop the agent
on it indefinitely. If DDG blocks, the agent gets an empty result and falls
back to the user's documents.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger("web_search")


@dataclass
class WebHit:
    title: str
    url: str
    snippet: str


async def search_web(query: str, max_results: int = 5) -> list[WebHit]:
    if not query.strip():
        return []
    try:
        # ddgs lib (≥6.x) — synchronous, run in thread
        from duckduckgo_search import DDGS
    except ImportError:
        log.warning("duckduckgo-search not installed")
        return []

    def _do() -> list[WebHit]:
        out: list[WebHit] = []
        try:
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results, safesearch="moderate"):
                    title = (r.get("title") or "").strip()
                    href = (r.get("href") or r.get("url") or "").strip()
                    body = (r.get("body") or r.get("snippet") or "").strip()
                    if title or body:
                        out.append(WebHit(title=title, url=href, snippet=body))
        except Exception as e:
            log.warning("DDG search failed for %r: %s", query, e)
        return out

    return await asyncio.to_thread(_do)
