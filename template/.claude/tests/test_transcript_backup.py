#!/usr/bin/env python3
"""Proof for hooks/transcript_backup.py and the shared PreCompact discriminator in hooks/_hook_state.py.

A subagent's PreCompact payload carries the parent's `session_id` and `transcript_path`. Client 2.1.283 sends
no agent field at all (measured 2026-09-28: subagent aa11f55a's compaction logged only session_id,
transcript_path, cwd, prompt_id, hook_event_name and trigger), so an `auto` trigger while the parent's
status-line gauge reads under the floor is the subagent signal; `agent_id` is still honored when present.
Session 8d42ad95 wrote five parent-transcript backups for compactions the parent never ran, and each one popped
the parent's `model_ladder_ready`. Each case runs the real hook as a process against a planted state dir,
project dir and gauge dir; an exit outside {0} or a traceback is a CRASH, never a pass.

    python3 .claude/tests/test_transcript_backup.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
HOOK = os.path.join(HOOKS, "transcript_backup.py")
sys.path.insert(0, HOOKS)
import _hook_state  # noqa: E402

SID = "8d42ad95-291b-460f-ae26-bb26828ad554"


def run(tmp, payload, state_dir, env_extra=None):
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state_dir), PYTHONIOENCODING="utf-8",
               CLAUDE_PROJECT_DIR=str(tmp), CC_CACHESTAT_DIR=str(Path(tmp) / "gauge"))
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
    state = {"model_ladder_ready": True, "nudge_targets_seen": ["a"],
             "fired_since_compaction": {"k": True}, "fired_once": {"o": True}}
    Path(state_dir, os.path.basename(path)).write_text(json.dumps(state), encoding="utf-8")


def read_state(state_dir):
    return json.loads(Path(state_dir, os.path.basename(_hook_state.state_path(SID))).read_text(encoding="utf-8"))


def backups(tmp):
    d = Path(tmp) / "logs" / "transcript_backups"
    return sorted(p.name for p in d.glob("*")) if d.exists() else []


def plant_gauge(tmp, raw):
    gauge = Path(tmp) / "gauge"
    gauge.mkdir(parents=True, exist_ok=True)
    (gauge / f"cc-cachestat-{SID}.json").write_text(raw, encoding="utf-8")


def write_transcript(path, used=None, tail_chars=0):
    """A parent transcript: one prompt, then (when `used` is set) the parent's last API call with
    `used` input tokens, then a tool result of `tail_chars` characters written after that call."""
    lines = [{"type": "user", "message": {"role": "user", "content": "hi"}}]
    if used is not None:
        lines.append({"type": "assistant", "message": {"role": "assistant", "content": [], "usage": {
            "input_tokens": 1, "cache_read_input_tokens": used - 1, "cache_creation_input_tokens": 0,
            "output_tokens": 10}}})
    if tail_chars:
        lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "x" * tail_chars}]}})
    Path(path).write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def gauge_text(used, pct):
    return json.dumps({"last": f"0:{used}:0", "context_pct": pct})


def scenario(payload_extra, base_tmp, gauge=None, used=None, tail_chars=0, env_extra=None):
    """Fresh project dir + state dir, the parent's gauge file when `gauge` is raw text, and a parent
    transcript per `write_transcript`; returns
    (out, crashed, state_after, backup_names, log_exists, manifest_exists)."""
    tmp = tempfile.mkdtemp(dir=base_tmp)
    state_dir = Path(tmp) / "state"
    plant_state(state_dir)
    if gauge is not None:
        plant_gauge(tmp, gauge)
    transcript = Path(tmp) / f"{SID}.jsonl"
    write_transcript(transcript, used, tail_chars)
    payload = {"hook_event_name": "PreCompact", "trigger": "auto", "session_id": SID,
               "transcript_path": str(transcript)}
    payload.update(payload_extra)
    out, crashed = run(tmp, payload, state_dir, env_extra)
    return (out, crashed, read_state(state_dir), backups(tmp),
            (Path(tmp) / "logs" / "pre_compact.json").exists(),
            (Path(tmp) / "logs" / "session_files" / f"{SID}.json").exists())


def main():
    cases = []
    with tempfile.TemporaryDirectory() as base:
        out, crashed, state, files, log_exists, manifest = scenario({}, base)
        cases.append(("parent: no crash", not crashed))
        cases.append(("parent: writes a raw backup", any(f.endswith(".jsonl") for f in files)))
        cases.append(("parent: clears model_ladder_ready", "model_ladder_ready" not in state))
        cases.append(("parent: re-arms fire_once_since_compaction", "fired_since_compaction" not in state))
        cases.append(("parent: keeps the once-per-session gate", state.get("fired_once") == {"o": True}))
        cases.append(("parent: logs the event", log_exists))
        cases.append(("parent: reminder reaches the model", "POST-CONTINUATION REMINDER" in out))

        out, crashed, state, files, log_exists, manifest = scenario({"agent_id": "a1b2c3d4e5f6"}, base)
        cases.append(("subagent: no crash", not crashed))
        cases.append(("subagent: writes no backup", files == []))
        cases.append(("subagent: leaves model_ladder_ready set", state.get("model_ladder_ready") is True))
        cases.append(("subagent: leaves compaction-scoped keys alone",
                      state.get("fired_since_compaction") == {"k": True}
                      and state.get("nudge_targets_seen") == ["a"]))
        cases.append(("subagent: writes no parent log or manifest", not log_exists and not manifest))
        cases.append(("subagent: emits no parent reminder", "POST-CONTINUATION" not in out))

        def parent_ran(files, state):
            return any(f.endswith(".jsonl") for f in files) and "model_ladder_ready" not in state

        # A 1M parent whose last call sent 150k: 15% of its window.
        low = dict(gauge=gauge_text(150_000, 15), used=150_000)
        out, crashed, state, files, log_exists, manifest = scenario({}, base, **low)
        cases.append(("auto under the floor: no crash", not crashed))
        cases.append(("auto under the floor: no backup, ladder kept",
                      files == [] and state.get("model_ladder_ready") is True))
        cases.append(("auto under the floor: no parent log or reminder",
                      not log_exists and not manifest and "POST-CONTINUATION" not in out))
        # A 200k parent whose gauge still reads its last call (96k, 48%) while that call's tool results
        # (about 70k tokens) are what pushed it to compact: the pending tail makes it the parent's.
        stale = dict(gauge=gauge_text(96_000, 48), used=96_000)
        out, crashed, state, files, _, _ = scenario({}, base, tail_chars=210_000, **stale)
        cases.append(("200k parent, stale 48% gauge plus a large pending tail counts as the parent",
                      not crashed and parent_ran(files, state)))
        out, crashed, state, files, _, _ = scenario({}, base, tail_chars=2_000, **stale)
        cases.append(("200k parent at 48% with a small tail is under the floor",
                      not crashed and files == [] and state.get("model_ladder_ready") is True))
        out, crashed, state, files, _, _ = scenario({}, base, env_extra={"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "40"},
                                                    gauge=gauge_text(300_000, 30), used=300_000)
        cases.append(("an autocompact override of 40% lowers the floor to 20%: 30% counts as the parent",
                      not crashed and parent_ran(files, state)))
        for label, extra, kwargs in (
                ("auto at 92%", {}, dict(gauge=gauge_text(920_000, 92), used=920_000)),
                ("manual at 15%", {"trigger": "manual"}, low),
                ("auto, no gauge file", {}, dict(used=150_000)),
                ("auto, unreadable gauge", {}, dict(gauge="not json", used=150_000)),
                ("auto, gauge without context_pct", {}, dict(gauge='{"last": "0:150000:0"}', used=150_000)),
                ("auto, gauge without last", {}, dict(gauge='{"context_pct": 15}', used=150_000)),
                ("auto, non-numeric context_pct", {}, dict(gauge='{"context_pct": "low", "last": "0:1:0"}',
                                                           used=150_000)),
                ("auto, transcript with no parent API call", {}, dict(gauge=gauge_text(150_000, 15))),
                ("auto, unreadable transcript", {"transcript_path": "Z:/nowhere/none.jsonl"}, low)):
            out, crashed, state, files, _, _ = scenario(extra, base, **kwargs)
            ran = parent_ran(files, state) if "transcript_path" not in extra else (
                "model_ladder_ready" not in state)
            cases.append((f"{label} counts as the parent", not crashed and ran))

        for label, empty in (("empty", ""), ("whitespace", "  "), ("null", None)):
            out, crashed, state, files, _, _ = scenario({"agent_id": empty}, base)
            cases.append((f"{label} agent_id counts as a parent",
                          not crashed and any(f.endswith(".jsonl") for f in files)
                          and "model_ladder_ready" not in state))

    gauge_dir = tempfile.mkdtemp()
    os.environ["CC_CACHESTAT_DIR"] = gauge_dir
    os.environ.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
    Path(gauge_dir, f"cc-cachestat-{SID}.json").write_text(gauge_text(300_000, 30), encoding="utf-8")
    transcript = Path(gauge_dir, "parent.jsonl")
    write_transcript(transcript, used=300_000)
    base_payload = {"session_id": SID, "transcript_path": str(transcript)}
    for label, payload, want in (
            ("auto, parent at 30%", dict(base_payload, trigger="auto"), True),
            ("manual, parent at 30%", dict(base_payload, trigger="manual"), False),
            ("auto, no gauge for the session", dict(base_payload, session_id="other", trigger="auto"), False),
            ("auto, no transcript_path", {"session_id": SID, "trigger": "auto"}, False),
            ("no agent_id", {}, False), ("agent_id set", {"agent_id": "x"}, True),
                                 ("empty agent_id", {"agent_id": ""}, False),
                                 ("whitespace agent_id", {"agent_id": " "}, False),
                                 ("null agent_id", {"agent_id": None}, False),
                                 ("non-object payload", [], False), ("None payload", None, False)):
        try:
            got = _hook_state.is_subagent_payload(payload)
        except Exception as exc:
            got = f"CRASH {exc!r}"
        cases.append((f"is_subagent_payload: {label} -> {want}", got is want))

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("PASS  " if ok else "FAIL  ") + name)
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
