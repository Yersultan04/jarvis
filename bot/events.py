"""Шина событий / стор состояния агентов (Sana Corp Ф2).

Один источник правды о том, кто чем занят. Автономный движок эмитит сюда события
жизненного цикла (working / reviewing / idle / meeting / done / error); дашборд (Ф3)
и 3D-офис (Ф4) читают snapshot() и рисуют живое состояние.

Хранение: state/agents.json (текущее состояние на агента) + state/events.jsonl (лог).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("jarvis.events")

# Ростер компании (персоны → отделы). idle = на диване, working/review = за столом.
ROSTER = [
    "Sana", "Maya", "Kai", "Shield", "Leo", "Zara",
    "Vex", "Nova", "Alex", "Rex", "Макс", "Sora",
]
ROLES = {
    "Sana": "COO", "Maya": "Eng", "Kai": "QA", "Shield": "Security",
    "Leo": "Product", "Zara": "UX", "Vex": "Critic", "Nova": "Marketing",
    "Alex": "Sales", "Rex": "Finance", "Макс": "CEO", "Sora": "Memory",
}


class StateStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "agents.json"
        self.events_file = self.dir / "events.jsonl"
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {a: {"state": "idle", "detail": "", "since": ""} for a in ROSTER}

    def _save(self, st: dict) -> None:
        try:
            self.state_file.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.exception("state save failed")

    def emit(self, agent: str, state: str, detail: str = "") -> None:
        """Зафиксировать состояние агента + дописать событие в лог."""
        with self._lock:
            st = self._load()
            st[agent] = {"state": state, "detail": detail[:120], "since": self._now()}
            self._save(st)
            try:
                with self.events_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"ts": self._now(), "agent": agent, "state": state, "detail": detail[:120]},
                        ensure_ascii=False,
                    ) + "\n")
            except OSError:
                pass

    def set_idle(self, *agents: str) -> None:
        for a in (agents or tuple(ROSTER)):
            self.emit(a, "idle", "")

    def snapshot(self) -> dict:
        """Текущее состояние всех агентов (для дашборда/офиса)."""
        with self._lock:
            st = self._load()
        for a in ROSTER:
            st.setdefault(a, {"state": "idle", "detail": "", "since": ""})
        return st
