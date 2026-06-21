"""Тонкий Trello REST-клиент для автономного движка Sana Corp (Ф1).

Движок сам читает доску и двигает карты (не через MCP — тот ненадёжен в headless
claude -p). claude получает только текст задачи и делает работу. Ключи — из env
(TRELLO_API_KEY / TRELLO_TOKEN).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger("jarvis.trello")

_BASE = "https://api.trello.com/1"


@dataclass
class Card:
    id: str
    name: str
    desc: str
    url: str


class TrelloClient:
    def __init__(self, api_key: str, token: str, *, timeout: float = 30.0) -> None:
        self._key = api_key
        self._token = token
        self._timeout = timeout

    def _auth(self) -> dict[str, str]:
        return {"key": self._key, "token": self._token}

    def cards_with_label(self, list_id: str, label_id: str) -> list[Card]:
        """Карты из списка с указанной меткой (в порядке доски — сверху вниз)."""
        resp = requests.get(
            f"{_BASE}/lists/{list_id}/cards",
            params={**self._auth(), "fields": "name,desc,idLabels,url", "pos": "true"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        out: list[Card] = []
        for c in resp.json():
            if label_id in (c.get("idLabels") or []):
                out.append(Card(c["id"], c.get("name", ""), c.get("desc", ""), c.get("url", "")))
        return out

    def move_card(self, card_id: str, list_id: str) -> None:
        resp = requests.put(
            f"{_BASE}/cards/{card_id}",
            params={**self._auth(), "idList": list_id},
            timeout=self._timeout,
        )
        resp.raise_for_status()

    def add_comment(self, card_id: str, text: str) -> None:
        resp = requests.post(
            f"{_BASE}/cards/{card_id}/actions/comments",
            params={**self._auth(), "text": text[:16000]},
            timeout=self._timeout,
        )
        resp.raise_for_status()
