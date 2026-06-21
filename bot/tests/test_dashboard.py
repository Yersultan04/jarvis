"""dashboard.read_activity — чистый ридер аудит-лога для ленты на /grid."""
import json

import dashboard as d


def test_read_activity_parses(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text(
        json.dumps({"ts": "2026-06-21T14:05:30", "kind": "task", "request": "сделать X", "status": "ok"}) + "\n"
        + json.dumps({"ts": "2026-06-21T14:10:00", "kind": "brief", "request": "бриф", "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    acts = d.read_activity(f, 8)
    assert len(acts) == 2
    assert acts[0]["ts"] == "06-21 14:05"      # MM-DD ЧЧ:ММ
    assert acts[0]["kind"] == "task"
    assert acts[1]["request"] == "бриф"


def test_read_activity_limit_and_order(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text(
        "".join(json.dumps({"ts": "2026-06-21T00:00:00", "kind": "text", "request": f"r{i}", "status": "ok"}) + "\n"
                for i in range(20)),
        encoding="utf-8",
    )
    acts = d.read_activity(f, 5)
    assert len(acts) == 5
    assert acts[-1]["request"] == "r19"        # последние, в хронологическом порядке


def test_read_activity_truncates_request(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text(json.dumps({"ts": "2026-06-21T00:00:00", "kind": "text", "request": "y" * 200, "status": "ok"}) + "\n",
                 encoding="utf-8")
    assert len(d.read_activity(f, 8)[0]["request"]) == 80


def test_read_activity_missing_file(tmp_path):
    assert d.read_activity(tmp_path / "nope.jsonl") == []


def test_read_activity_skips_bad_lines(tmp_path):
    f = tmp_path / "actions.jsonl"
    f.write_text("не json\n" + json.dumps({"ts": "2026-06-21T09:00:00", "kind": "sync", "request": "ok", "status": "ok"}) + "\n",
                 encoding="utf-8")
    acts = d.read_activity(f, 8)
    assert len(acts) == 1 and acts[0]["kind"] == "sync"
