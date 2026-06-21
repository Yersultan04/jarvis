"""Форматирование ответов для Telegram: format_answer, источники, статус."""
import jarvis_bot as b
from jarvis_api import AgentAnswer


def test_format_answer_succeeded():
    ans = AgentAnswer(status="succeeded", answer="**Готово**", confidence=0.9,
                      citations=[{"filename": "project_haul.md"}])
    out = b.format_answer(ans)
    assert "<b>Готово</b>" in out
    assert "project_haul" in out          # .md убран
    assert "90%" in out                   # уверенность


def test_format_answer_budget_failure():
    ans = AgentAnswer(status="failed", error="step budget exceeded")
    assert "Не уложился" in b.format_answer(ans)


def test_format_answer_generic_failure():
    ans = AgentAnswer(status="error", error="boom")
    out = b.format_answer(ans)
    assert "Не получилось" in out


def test_pretty_sources_dedup_and_strip():
    out = b._pretty_sources([
        {"filename": "a.md"}, {"filename": "a.md"}, {"filename": "b.md"}, {"filename": ""},
    ])
    assert "a" in out and "b" in out
    assert ".md" not in out


def test_pretty_sources_empty():
    assert b._pretty_sources([]) == ""


def test_format_status(monkeypatch):
    class _Store:
        def snapshot(self):
            return {"Maya": {"state": "working", "detail": "рефакторинг"},
                    "Kai": {"state": "idle", "detail": ""}}

    monkeypatch.setattr(b, "STORE", _Store())
    out = b.format_status()
    assert "Maya" in out and "working" in out
    assert "Kai" in out                   # idle → попал в «на диване»
