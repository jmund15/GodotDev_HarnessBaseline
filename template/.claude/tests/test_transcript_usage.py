"""Proof for tools/transcript_usage.py: the per-`message.id` usage reader, transport, and login dirs.

    python -m pytest .claude/tests/test_transcript_usage.py -q
"""
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import transcript_usage as tu  # noqa: E402


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def assistant(mid, ts, out, model="claude-test-a", session="s-host", usage=None, uuid=None):
    u = {"input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": 1000,
         "cache_creation_input_tokens": 0}
    u.update(usage or {})
    return {"type": "assistant", "sessionId": session, "uuid": uuid or ("u-%s-%d" % (mid, out)),
            "timestamp": ts, "message": {"id": mid, "model": model, "usage": u, "content": []}}


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return str(path)


def test_last_record_wins_for_a_message_id(tmp_path):
    path = write_jsonl(tmp_path / "t.jsonl", [
        assistant("m1", "2026-09-22T10:00:00Z", 5),
        assistant("m1", "2026-09-22T10:00:01Z", 40),
        assistant("m1", "2026-09-22T10:00:02Z", 120),
        assistant("m2", "2026-09-22T10:00:03Z", 7),
    ])
    calls = tu.read_transcript(path)["calls"]
    assert [c["id"] for c in calls] == ["m1", "m2"]
    assert calls[0]["out"] == 120
    assert calls[0]["cr"] == 1000 and calls[0]["inp"] == 10


def test_cache_write_split_and_timestamp(tmp_path):
    split = {"cache_creation_input_tokens": 1000,
             "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 900}}
    path = write_jsonl(tmp_path / "t.jsonl", [
        assistant("m1", "2026-09-22T10:00:00Z", 5, usage=split),
        assistant("m1", "2026-09-22T10:00:05Z", 9, usage=split),
        assistant("m2", "2026-09-22T10:01:00Z", 3, usage={"cache_creation_input_tokens": 50}),
    ])
    first, second = tu.read_transcript(path)["calls"]
    assert (first["cw"], first["cw5m"], first["cw1h"]) == (1000, 100, 900)
    assert first["ts"] == epoch("2026-09-22T10:00:05Z")
    assert second["cw"] == 50 and second["cw5m"] is None and second["cw1h"] is None


def test_transport_is_proxied_when_any_call_names_a_non_claude_model(tmp_path):
    host = write_jsonl(tmp_path / "host.jsonl", [assistant("h1", "2026-09-22T10:00:00Z", 1, session="s-host")])
    seat = write_jsonl(tmp_path / "seat.jsonl", [
        assistant("p1", "2026-09-22T10:00:00Z", 1, model="claude-test-a", session="s-seat"),
        assistant("p2", "2026-09-22T10:00:01Z", 1, model="antigravity/gemini-4-neon", session="s-seat"),
    ])
    assert tu.read_transcript(host)["transport"] == tu.ANTHROPIC
    assert tu.read_transcript(seat)["transport"] == tu.PROXIED


def test_scan_tags_each_call_with_its_transport_and_drops_synthetic(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(projects / "p1" / "s-host.jsonl", [assistant("h1", "2026-09-22T10:00:00Z", 10, session="s-host")])
    write_jsonl(projects / "p1" / "s-host" / "subagents" / "agent-a.jsonl",
                [assistant("h2", "2026-09-22T10:00:01Z", 11, session="s-host")])
    write_jsonl(projects / "p2" / "s-gpt.jsonl", [
        assistant("g1", "2026-09-22T10:00:05Z", 15, model="vendor-x-1", session="s-gpt"),
        assistant("g2", "2026-09-22T10:00:06Z", 1, model=tu.SYNTHETIC_MODEL, session="s-gpt"),
    ])
    write_jsonl(projects / "p3" / "s-bad.jsonl", ["{torn", "[1, 2]"])
    scan = tu.scan([str(projects)])
    by_id = {c["id"]: c for c in scan["calls"]}
    assert sorted(by_id) == ["g1", "h1", "h2"]
    assert by_id["h2"]["session"] == "s-host"
    assert by_id["g1"]["transport"] == tu.PROXIED and by_id["h1"]["transport"] == tu.ANTHROPIC
    assert scan["files"] == 4
    assert scan["unparseable_lines"] == 2
    assert scan["unparseable_files"] == [str(projects / "p3" / "s-bad.jsonl")]


def test_scan_dedupes_a_message_copied_into_a_resumed_session(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(projects / "p" / "s-a.jsonl", [assistant("m1", "2026-09-22T10:00:00Z", 30, session="s-a")])
    write_jsonl(projects / "p" / "s-b.jsonl", [
        assistant("m1", "2026-09-22T10:00:00Z", 30, session="s-b"),
        assistant("m2", "2026-09-22T11:00:00Z", 4, session="s-b"),
    ])
    assert sorted(c["id"] for c in tu.scan([str(projects)])["calls"]) == ["m1", "m2"]


def test_since_skips_older_files_by_mtime(tmp_path):
    projects = tmp_path / "projects"
    old = write_jsonl(projects / "p" / "s-old.jsonl", [assistant("o1", "2026-01-01T10:00:00Z", 3, session="s-old")])
    write_jsonl(projects / "p" / "s-new.jsonl", [assistant("n1", "2026-09-22T10:00:00Z", 3, session="s-new")])
    os.utime(old, (epoch("2026-01-01T10:00:00Z"),) * 2)
    scan = tu.scan([str(projects)], since=epoch("2026-09-01T00:00:00Z"))
    assert [c["id"] for c in scan["calls"]] == ["n1"] and scan["files"] == 1


def test_a_missing_root_is_a_named_gap(tmp_path):
    missing = str(tmp_path / "absent")
    scan = tu.scan([missing])
    assert scan["gaps"] == [missing] and scan["files"] == 0 and scan["calls"] == []


def test_projects_dirs_are_each_login_dirs_projects(monkeypatch):
    monkeypatch.setattr(tu.launch_check, "login_dirs", lambda: ["/a", "/b"])
    assert tu.projects_dirs() == [os.path.join("/a", "projects"), os.path.join("/b", "projects")]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
