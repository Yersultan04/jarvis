"""Шина состояний агентов (Ф2): emit / snapshot / set_idle."""
from events import ROSTER, StateStore


def test_default_snapshot_all_idle(tmp_path):
    s = StateStore(tmp_path / "state")
    snap = s.snapshot()
    assert set(snap) >= set(ROSTER)
    assert all(snap[a]["state"] == "idle" for a in ROSTER)


def test_emit_sets_state_and_detail(tmp_path):
    s = StateStore(tmp_path / "state")
    s.emit("Maya", "working", "рефакторинг")
    snap = s.snapshot()
    assert snap["Maya"]["state"] == "working"
    assert snap["Maya"]["detail"] == "рефакторинг"
    assert snap["Maya"]["since"]            # проставлен таймстемп


def test_detail_truncated(tmp_path):
    s = StateStore(tmp_path / "state")
    s.emit("Kai", "testing", "z" * 300)
    assert len(s.snapshot()["Kai"]["detail"]) == 120


def test_set_idle_returns_to_couch(tmp_path):
    s = StateStore(tmp_path / "state")
    s.emit("Vex", "reviewing", "ревью PR")
    s.set_idle("Vex")
    assert s.snapshot()["Vex"]["state"] == "idle"


def test_events_log_appended(tmp_path):
    base = tmp_path / "state"
    s = StateStore(base)
    s.emit("Sana", "coordinating", "диспатч")
    s.emit("Sana", "idle", "")
    lines = (base / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_persists_across_instances(tmp_path):
    base = tmp_path / "state"
    StateStore(base).emit("Leo", "working", "PRD")
    # новый инстанс читает тот же файл состояния
    assert StateStore(base).snapshot()["Leo"]["state"] == "working"
