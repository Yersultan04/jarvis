"""Состояние автономии: kill-switch + дневной лимит + авто-сброс по дате.

Все тесты перенаправляют AUTONOMY_STATE_FILE в tmp, чтобы НЕ трогать боевой файл.
"""
import json

import jarvis_bot as b


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(b, "AUTONOMY_STATE_FILE", tmp_path / "autonomy_state.json")


def test_default_state_fresh(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    st = b._autonomy_state()
    assert st["done_today"] == 0
    assert "enabled" in st and "date" in st


def test_set_autonomy_persists(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    b.set_autonomy(True)
    assert b._autonomy_state()["enabled"] is True
    b.set_autonomy(False)
    assert b._autonomy_state()["enabled"] is False


def test_bump_done_increments(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    b._autonomy_state()
    assert b._bump_done() == 1
    assert b._bump_done() == 2
    assert b._autonomy_state()["done_today"] == 2


def test_new_day_resets_counter_keeps_enabled(monkeypatch, tmp_path):
    f = tmp_path / "autonomy_state.json"
    monkeypatch.setattr(b, "AUTONOMY_STATE_FILE", f)
    # вчерашнее состояние: автономия ВКЛ, уже сделано 7 задач
    f.write_text(json.dumps({"date": "2000-01-01", "done_today": 7, "enabled": True}), encoding="utf-8")
    st = b._autonomy_state()
    assert st["done_today"] == 0          # счётчик обнулился на новый день
    assert st["enabled"] is True          # но флаг автономии сохранился


def test_corrupt_file_recovers(monkeypatch, tmp_path):
    f = tmp_path / "autonomy_state.json"
    monkeypatch.setattr(b, "AUTONOMY_STATE_FILE", f)
    f.write_text("{ битый json", encoding="utf-8")
    st = b._autonomy_state()              # не падает, отдаёт дефолт
    assert st["done_today"] == 0
