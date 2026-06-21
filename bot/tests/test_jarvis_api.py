"""jarvis_api: retry-обёртка claude -p, ротация Groq-ключей, парсинг ревью,
fallback, сборка контекста. Всё на моках — без сети и без реального LLM."""
import subprocess

import pytest

import jarvis_api
from jarvis_api import JarvisAPI


class _Proc:
    def __init__(self, rc, out=b"", err=b""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _api():
    a = JarvisAPI("http://x", "", "", "rag1")
    a._claude_bin = "claude"
    a._builder_settings = "settings.json"
    return a


# ---------------- _run_claude retry ----------------


def test_run_claude_success(monkeypatch):
    monkeypatch.setattr(jarvis_api.subprocess, "run", lambda *a, **k: _Proc(0, "  ответ\n".encode()))
    out = _api()._run_claude(["claude"], "p", timeout=5)
    assert out == "ответ"


def test_run_claude_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        return _Proc(0, b"ok") if calls["n"] == 2 else _Proc(1, err=b"cold start")

    monkeypatch.setattr(jarvis_api.subprocess, "run", fake_run)
    monkeypatch.setattr(jarvis_api.time, "sleep", lambda *_: None)  # не ждать 1.5с
    out = _api()._run_claude(["claude"], "p", timeout=5, retries=1)
    assert out == "ok"
    assert calls["n"] == 2


def test_run_claude_all_fail_raises(monkeypatch):
    monkeypatch.setattr(jarvis_api.subprocess, "run", lambda *a, **k: _Proc(1, err=b"boom"))
    monkeypatch.setattr(jarvis_api.time, "sleep", lambda *_: None)
    with pytest.raises(RuntimeError, match="rc!=0"):
        _api()._run_claude(["claude"], "p", timeout=5, retries=1)


def test_run_claude_timeout_no_retry(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(jarvis_api.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="таймаут"):
        _api()._run_claude(["claude"], "p", timeout=5, retries=2)


# ---------------- ask_review парсинг ----------------


@pytest.mark.parametrize("out,expected", [
    ("PASS\nвсё чисто", True),
    ("FAIL\nесть баг", False),
    ("мусор без вердикта", False),
    ("pass — годно", True),
])
def test_ask_review_verdict(monkeypatch, out, expected):
    a = _api()
    monkeypatch.setattr(a, "_run_claude", lambda *args, **kw: out)
    passed, notes = a.ask_review()
    assert passed is expected
    assert notes == out.strip()


# ---------------- _build_context ----------------


def test_build_context_dedups_sources():
    hits = [
        {"filename": "a.md", "text": "один"},
        {"filename": "a.md", "text": "два"},
        {"filename": "b.md", "text": "три"},
    ]
    ctx, sources = JarvisAPI._build_context(hits)
    assert sources == ["a.md", "b.md"]
    assert "[1]" in ctx and "[3]" in ctx


# ---------------- Groq ротация ключей ----------------


def test_groq_chat_rotates_on_429(monkeypatch):
    a = _api()
    a.configure_groq(["k1", "k2"], "http://groq", "model")
    seq = [_Resp(429), _Resp(200, {"choices": [{"message": {"content": "готово"}}]})]
    used_keys = []

    def fake_post(url, headers=None, **k):
        used_keys.append(headers["Authorization"])
        return seq.pop(0)

    monkeypatch.setattr(jarvis_api.requests, "post", fake_post)
    out = a._groq_chat([{"role": "user", "content": "hi"}])
    assert out == "готово"
    assert used_keys == ["Bearer k1", "Bearer k2"]   # ротировал на второй ключ
    assert a._groq_idx == 1                            # рабочий ключ запомнен


def test_groq_chat_all_keys_dead(monkeypatch):
    a = _api()
    a.configure_groq(["k1", "k2"], "http://groq", "model")
    monkeypatch.setattr(jarvis_api.requests, "post", lambda *a, **k: _Resp(500))
    with pytest.raises(RuntimeError, match="недоступны"):
        a._groq_chat([{"role": "user", "content": "hi"}])


# ---------------- transcribe ротация ----------------


def test_transcribe_rotates(monkeypatch):
    a = _api()
    a.configure_groq(["k1", "k2"], "http://groq", "model")
    seq = [_Resp(429), _Resp(200, {"text": "  привет "})]
    monkeypatch.setattr(jarvis_api.requests, "post", lambda *a, **k: seq.pop(0))
    assert a.transcribe(b"audio", filename="v.ogg") == "привет"


def test_transcribe_no_keys():
    with pytest.raises(RuntimeError, match="ключи не сконфигурированы"):
        _api().transcribe(b"x")


# ---------------- ask_cheap fallback Groq→claude ----------------


def test_ask_cheap_falls_back_to_claude(monkeypatch):
    a = _api()
    monkeypatch.setattr(a, "search", lambda q, **k: [{"filename": "m.md", "text": "факт"}])

    def groq_dead(*args, **kw):
        raise RuntimeError("все Groq-ключи недоступны")

    monkeypatch.setattr(a, "_groq_chat", groq_dead)
    monkeypatch.setattr(a, "_claude_chat", lambda system, user: "ответ от claude")
    ans = a.ask_cheap("вопрос")
    assert ans.status == "succeeded"
    assert ans.answer == "ответ от claude"
    assert ans.citations == [{"filename": "m.md"}]


def test_ask_cheap_empty_memory(monkeypatch):
    a = _api()
    monkeypatch.setattr(a, "search", lambda q, **k: [])
    ans = a.ask_cheap("вопрос")
    assert "ничего не нашёл" in ans.answer


# ---------------- конфиг-гарды ----------------


def test_ask_chelsea_unconfigured():
    with pytest.raises(RuntimeError, match="Chelsea"):
        JarvisAPI("x", "", "", "r").ask_chelsea("hi")


def test_ask_builder_unconfigured():
    with pytest.raises(RuntimeError, match="Builder"):
        JarvisAPI("x", "", "", "r").ask_builder("task")
