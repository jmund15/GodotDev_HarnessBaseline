#!/usr/bin/env python3
"""Proof for hooks/transcript_backup.py and the shared PreCompact discriminator in hooks/_hook_state.py.

A subagent's PreCompact payload carries the parent's `session_id` and `transcript_path`. Client 2.1.283 sends
no agent field at all (measured 2026-09-28: subagent aa11f55a's compaction logged only session_id,
transcript_path, cwd, prompt_id, hook_event_name and trigger), so an `auto` trigger beside a subagent
transcript written after the parent's (`_hook_state.is_subagent_payload` owns the full test) is the subagent signal; `agent_id`
is still honored when present. Session 8d42ad95 wrote five parent-transcript backups for compactions the parent
never ran, and each one popped the parent's compaction-scoped keys. Session f3a34186 lost the opposite way: its
500k auto-compact window put the parent's compaction at 46% of the status-line window, under the old 50% gauge
floor, so the parent's five auto compactions wrote no backup. Each case runs the real hook as a process against
a planted state dir, project dir and subagent transcript; an exit outside {0} or a traceback is a CRASH, never
a pass.

"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "transcript_backup.py")
sys.path.insert(0, HOOKS)
import _hook_state  # noqa: E402

SID = "8d42ad95-291b-460f-ae26-bb26828ad554"


def run(tmp, payload, state_dir, env_extra=None):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state_dir), PYTHONIOENCODING="utf-8",
               CLAUDE_PROJECT_DIR=str(tmp))
    env.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, cwd=tmp, timeout=120)
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    crashed = r.returncode not in (0, 2) or "Traceback" in err
    return out, crashed


def plant_state(state_dir):
    path = _hook_state.state_path(SID)
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    state = {"fired_since_compaction": {"k": True}, "fired_once": {"o": True}}
    Path(state_dir, os.path.basename(path)).write_text(json.dumps(state), encoding="utf-8")


def read_state(state_dir):
    return json.loads(Path(state_dir, os.path.basename(_hook_state.state_path(SID))).read_text(encoding="utf-8"))


def backups(tmp):
    d = Path(tmp) / "logs" / "transcript_backups"
    return sorted(p.name for p in d.glob("*")) if d.exists() else []


def call(used, sidechain=False):
    return {"type": "assistant", "isSidechain": sidechain, "message": {"role": "assistant", "content": [], "usage": {
        "input_tokens": 1, "cache_read_input_tokens": used - 1, "cache_creation_input_tokens": 0,
        "output_tokens": 10}}}


def write_transcript(path, used=None, tail_chars=0, post=None):
    """A parent transcript: one prompt, then (when `used` is set) the parent's last API call with
    `used` input tokens, then (when `post` is set) a compaction boundary with `post` postTokens, then a
    tool result of `tail_chars` characters."""
    lines = [{"type": "user", "message": {"role": "user", "content": "hi"}}]
    if used is not None:
        lines.append(call(used))
    if post is not None:
        lines.append({"type": "system", "subtype": "compact_boundary", "isSidechain": False,
                      "compactMetadata": {"trigger": "auto", "preTokens": used, "postTokens": post}})
    if tail_chars:
        lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "x" * tail_chars}]}})
    Path(path).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def plant_subagent(transcript, used, age=0):
    """A Workflow agent's transcript beside the parent's, at `used` tokens, last written `age` seconds ago."""
    path = Path(str(transcript)[:-len(".jsonl")]) / "subagents" / "workflows" / "wf_1" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(call(used, sidechain=True)) + "\n", encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    elif os.path.getmtime(transcript) > os.path.getmtime(path) - 2:
        stamp = os.path.getmtime(path) - 2
        os.utime(transcript, (stamp, stamp))


def plant_transcripts(tmp, used=None, tail_chars=0, post=None, subagent=None, subagent_age=0, parent_age=0):
    """A parent transcript per `write_transcript` last written `parent_age` seconds ago and, when `subagent` is set,
    a subagent transcript at that many tokens; returns the parent's path."""
    transcript = Path(tmp) / f"{SID}.jsonl"
    write_transcript(transcript, used, tail_chars, post)
    if parent_age:
        stamp = time.time() - parent_age
        os.utime(transcript, (stamp, stamp))
    if subagent is not None:
        plant_subagent(transcript, subagent, subagent_age)
    return transcript


def scenario(payload_extra, tmp, **plant):
    """The hook run on a fresh state dir and the planted transcripts; returns
    (out, crashed, state_after, backup_names, log_exists, manifest_exists)."""
    state_dir = Path(tmp) / "state"
    plant_state(state_dir)
    transcript = plant_transcripts(tmp, **plant)
    payload = {"hook_event_name": "PreCompact", "trigger": "auto", "session_id": SID,
               "transcript_path": str(transcript)}
    payload.update(payload_extra)
    out, crashed = run(tmp, payload, state_dir)
    return (out, crashed, read_state(state_dir), backups(tmp),
            (Path(tmp) / "logs" / "pre_compact.json").exists(),
            (Path(tmp) / "logs" / "session_files" / f"{SID}.json").exists())


def test_parent_compaction_backs_up_rearms_and_stays_silent(tmp_path):
    out, crashed, state, files, log_exists, manifest = scenario({}, tmp_path)
    assert not crashed
    assert any(f.endswith(".jsonl") for f in files), "no raw backup"
    assert "fired_since_compaction" not in state, "fire_once_since_compaction was not re-armed"
    assert state.get("fired_once") == {"o": True}, "the once-per-session gate was lost"
    assert log_exists
    assert not out.strip(), "a successful backup adds no compaction context"
    assert any(f.endswith(".summary.json") for f in files) and manifest


def test_parent_backup_failure_still_reports_the_lost_backup(tmp_path):
    out, crashed, *_ = scenario({"transcript_path": "Z:/missing-transcript.jsonl"}, tmp_path)
    assert not crashed and "backup unavailable" in out.lower()


def test_agent_id_subagent_writes_nothing_and_leaves_the_parents_keys(tmp_path):
    out, crashed, state, files, log_exists, manifest = scenario({"agent_id": "a1b2c3d4e5f6"}, tmp_path)
    assert not crashed and files == []
    assert state.get("fired_since_compaction") == {"k": True}
    assert not log_exists and not manifest and "POST-CONTINUATION" not in out


def test_auto_beside_a_live_larger_subagent_is_the_subagents_compaction(tmp_path):
    # A Workflow agent at its window compacts while the parent sits at 150k.
    out, crashed, state, files, log_exists, manifest = scenario({}, tmp_path, used=150_000, subagent=469_000)
    assert not crashed and files == [] and state.get("fired_since_compaction") == {"k": True}
    assert not log_exists and not manifest and "POST-CONTINUATION" not in out


# Decision rows: (label, payload extra, planted transcripts, whether the payload counts as the parent's own
# compaction). Session f3a34186 23:08: the parent idled after its own compaction (last call 434k, boundary
# postTokens 41k, then about 120k tokens of re-injected rows) while a Workflow agent at 469k compacted. Session
# f3a34186 01:02: a Workflow agent on a smaller window compacted at 238k while the parent, at 383k, had written
# nothing for 130 s. Session f3a34186: autoCompactWindow 500k on a 1M model, the parent compacting at 467k (46%).
LIVE = dict(used=150_000, subagent=469_000)
PAYLOAD_ROWS = [
    ("idle parent after its own compaction reads from the boundary", {},
     dict(used=434_000, post=41_000, tail_chars=360_000, subagent=469_000), False),
    ("smaller live subagent beside a parent quiet for 130 s", {},
     dict(used=383_000, subagent=230_000, parent_age=130), False),
    ("the parent wrote after a larger live subagent", {}, dict(used=150_000, subagent=469_000, subagent_age=5), True),
    ("the parent wrote after a smaller live subagent", {},
     dict(used=150_000, subagent=230_000, subagent_age=10, parent_age=5), True),
    ("467k on a 1M window, no live subagent", {}, dict(used=467_000), True),
    ("beside a smaller live subagent", {}, dict(used=467_000, subagent=200_000), True),
    ("beside a subagent near the parent's size", {}, dict(used=467_000, subagent=480_000), True),
    ("beside a larger subagent last written a minute ago", {},
     dict(used=150_000, subagent=469_000, subagent_age=60), True),
    ("manual beside a larger live subagent", {"trigger": "manual"}, LIVE, True),
    ("transcript with no parent API call", {}, {}, True),
    ("a live subagent and a parent transcript with no call", {}, dict(subagent=469_000), True),
    ("unreadable transcript", {"transcript_path": "Z:/nowhere/none.jsonl"}, LIVE, True),
    ("empty agent_id", {"agent_id": ""}, {}, True),
    ("whitespace agent_id", {"agent_id": "  "}, {}, True),
    ("null agent_id", {"agent_id": None}, {}, True),
]


@pytest.mark.parametrize("label,extra,plant,parent", PAYLOAD_ROWS, ids=[row[0] for row in PAYLOAD_ROWS])
def test_discriminator_rows_for_planted_sessions(tmp_path, label, extra, plant, parent):
    transcript = plant_transcripts(tmp_path, **plant)
    payload = {"trigger": "auto", "session_id": SID, "transcript_path": str(transcript)}
    payload.update(extra)
    assert _hook_state.is_subagent_payload(payload) is (not parent)


def test_is_subagent_payload_rows(tmp_path):
    transcript = plant_transcripts(tmp_path, used=300_000, subagent=469_000)
    quiet = tmp_path / "quiet.jsonl"
    write_transcript(quiet, used=300_000)
    base_payload = {"session_id": SID, "transcript_path": str(transcript)}
    for label, payload, want in (
            ("auto, a live subagent at 469k beside a parent at 300k", dict(base_payload, trigger="auto"), True),
            ("manual, the same transcripts", dict(base_payload, trigger="manual"), False),
            ("auto, no subagent transcript", {"session_id": SID, "transcript_path": str(quiet), "trigger": "auto"}, False),
            ("auto, no transcript_path", {"session_id": SID, "trigger": "auto"}, False),
            ("no agent_id", {}, False), ("agent_id set", {"agent_id": "x"}, True),
            ("empty agent_id", {"agent_id": ""}, False), ("whitespace agent_id", {"agent_id": " "}, False),
            ("null agent_id", {"agent_id": None}, False),
            ("non-object payload", [], False), ("None payload", None, False)):
        assert _hook_state.is_subagent_payload(payload) is want, label
