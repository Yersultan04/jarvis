"""Аудит-лог (G5): запись JSONL, чтение последних, HTML-сводка."""
import jarvis_bot as b


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(b, "AUDIT_FILE", tmp_path / "actions.jsonl")


def test_log_then_read(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    b.audit_log(123, "text", "тест-запрос", route="claude", status="ok", dur_s=1.2)
    recent = b.recent_audit(10)
    assert len(recent) == 1
    rec = recent[0]
    assert rec["chat_id"] == 123
    assert rec["kind"] == "text"
    assert rec["status"] == "ok"


def test_request_truncated(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    b.audit_log(1, "text", "x" * 500)
    assert len(b.recent_audit(1)[0]["request"]) == 200


def test_recent_returns_last_n(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    for i in range(15):
        b.audit_log(1, "text", f"req{i}", status="ok")
    recent = b.recent_audit(5)
    assert len(recent) == 5
    assert recent[-1]["request"] == "req14"   # хронологический порядок


def test_recent_empty_when_no_file(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert b.recent_audit() == []


def test_format_audit_html(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    b.audit_log(1, "task", "сделать X", status="ok", dur_s=3)
    out = b.format_audit(b.recent_audit(1))
    assert "Последние действия" in out
    assert "🛠" in out          # иконка task
    assert "✅" in out          # статус ok
