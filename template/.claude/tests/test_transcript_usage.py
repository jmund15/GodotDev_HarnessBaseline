"""Proof for tools/transcript_usage.py: the one per-`message.id` usage reader.

orchestration_metrics.agent_usage and plan_usage_calc both read transcript usage through it. The
cases plant transcripts, a projects root and a sidecar spend ledger under a temp dir.

    python3 .claude/tests/test_transcript_usage.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
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
    return path


def test_last_record_wins_for_a_message_id():
    root = tempfile.mkdtemp(prefix="tu_last_")
    path = write_jsonl(os.path.join(root, "t.jsonl"), [
        assistant("m1", "2026-09-22T10:00:00Z", 5),
        assistant("m1", "2026-09-22T10:00:01Z", 40),
        assistant("m1", "2026-09-22T10:00:02Z", 120),
        assistant("m2", "2026-09-22T10:00:03Z", 7),
    ])
    calls = tu.read_transcript(path)["calls"]
    assert [c["id"] for c in calls] == ["m1", "m2"], calls
    assert calls[0]["out"] == 120, calls[0]
    assert calls[0]["cr"] == 1000 and calls[0]["inp"] == 10, calls[0]
    assert sum(c["out"] for c in calls) == 127, calls


def test_cache_write_split_and_timestamp():
    root = tempfile.mkdtemp(prefix="tu_split_")
    split = {"cache_creation_input_tokens": 1000,
             "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 900}}
    path = write_jsonl(os.path.join(root, "t.jsonl"), [
        assistant("m1", "2026-09-22T10:00:00Z", 5, usage=split),
        assistant("m1", "2026-09-22T10:00:05Z", 9, usage=split),
        assistant("m2", "2026-09-22T10:01:00Z", 3, usage={"cache_creation_input_tokens": 50}),
    ])
    calls = tu.read_transcript(path)["calls"]
    first, second = calls
    assert (first["cw"], first["cw5m"], first["cw1h"]) == (1000, 100, 900), first
    assert first["ts"] == epoch("2026-09-22T10:00:05Z"), first
    assert first["model"] == "claude-test-a" and first["session"] == "s-host", first
    assert second["cw"] == 50 and second["cw5m"] is None and second["cw1h"] is None, second


def test_proxied_session_is_excluded_and_counted():
    root = tempfile.mkdtemp(prefix="tu_proxy_")
    projects = os.path.join(root, "projects")
    ledger = write_jsonl(os.path.join(root, "ledger.jsonl"), [
        {"sessionId": "s-codex", "transport": "codex"},
        {"sessionId": "s-child", "transport": "anthropic"},
        "not json",
    ])
    write_jsonl(os.path.join(projects, "p1", "s-host.jsonl"),
                [assistant("h1", "2026-09-22T10:00:00Z", 10, session="s-host")])
    write_jsonl(os.path.join(projects, "p1", "s-host", "subagents", "agent-a.jsonl"),
                [assistant("h2", "2026-09-22T10:00:01Z", 11, session="s-host")])
    write_jsonl(os.path.join(projects, "p2", "s-child.jsonl"),
                [assistant("c1", "2026-09-22T10:00:02Z", 12, session="s-child")])
    write_jsonl(os.path.join(projects, "p2", "s-codex.jsonl"),
                [assistant("x1", "2026-09-22T10:00:03Z", 13, model="claude-test-a", session="s-codex")])
    write_jsonl(os.path.join(projects, "p2", "s-codex", "subagents", "agent-b.jsonl"),
                [assistant("x2", "2026-09-22T10:00:04Z", 14, session="s-codex")])
    write_jsonl(os.path.join(projects, "p3", "s-gpt.jsonl"),
                [assistant("g1", "2026-09-22T10:00:05Z", 15, model="vendor-x-1", session="s-gpt")])
    write_jsonl(os.path.join(projects, "p3", "s-bad.jsonl"), ["{torn", "[1, 2]"])
    scan = tu.scan(projects, ledger_path=ledger)
    assert sorted(c["id"] for c in scan["calls"]) == ["c1", "h1", "h2"], scan["calls"]
    assert scan["excluded"] == {"s-codex": "codex", "s-gpt": "proxied"}, scan["excluded"]
    assert scan["files"] == 7, scan["files"]
    assert scan["unparseable_lines"] == 2 and scan["unparseable_files"] == [
        os.path.join(projects, "p3", "s-bad.jsonl")], scan


def test_scan_dedupes_a_message_copied_into_a_resumed_session():
    root = tempfile.mkdtemp(prefix="tu_resume_")
    projects = os.path.join(root, "projects")
    write_jsonl(os.path.join(projects, "p", "s-a.jsonl"),
                [assistant("m1", "2026-09-22T10:00:00Z", 30, session="s-a")])
    write_jsonl(os.path.join(projects, "p", "s-b.jsonl"),
                [assistant("m1", "2026-09-22T10:00:00Z", 30, session="s-b"),
                 assistant("m2", "2026-09-22T11:00:00Z", 4, session="s-b")])
    scan = tu.scan(projects, ledger_path=os.path.join(root, "absent.jsonl"))
    assert sorted(c["id"] for c in scan["calls"]) == ["m1", "m2"], scan["calls"]


def test_since_skips_older_files_by_mtime():
    root = tempfile.mkdtemp(prefix="tu_since_")
    projects = os.path.join(root, "projects")
    old = write_jsonl(os.path.join(projects, "p", "s-old.jsonl"),
                      [assistant("o1", "2026-01-01T10:00:00Z", 3, session="s-old")])
    write_jsonl(os.path.join(projects, "p", "s-new.jsonl"),
                [assistant("n1", "2026-09-22T10:00:00Z", 3, session="s-new")])
    os.utime(old, (epoch("2026-01-01T10:00:00Z"),) * 2)
    scan = tu.scan(projects, since=epoch("2026-09-01T00:00:00Z"),
                   ledger_path=os.path.join(root, "absent.jsonl"))
    assert [c["id"] for c in scan["calls"]] == ["n1"] and scan["files"] == 1, scan


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("FAIL", name, "-", repr(exc)[:300])
    sys.exit(1 if fails else 0)
