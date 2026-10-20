#!/usr/bin/env python3
"""UserPromptSubmit advisory: re-arm uncovered sidecars after any killed/stopped task notice.

Reads this session's dispatch_table_pending registry, not the transcript. Finished runs are
read immediately; a missing process identity is never proof a launch did not start. One
additionalContext carries every exceptional record, a `running or queued (no live watcher)`
group, and the session-wake command that covers them. Non-notices and fully covered runs are
silent. Advisory errors log to stderr and do not block the prompt. Measured through
prompt_dispatch's 5 s member budget, median of 7 reps: killed 18 ms, stopped 18 ms,
ordinary 10 ms; the emitted line is 299 bytes when it fires, 0 when it does not.
"""
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import watch_records  # noqa: E402
from watch_records import _covered  # noqa: E402
import dispatch_table  # noqa: E402

WAKE_COMMAND = "python3 .claude/tools/watch_records.py --session --wake"

STATUS = re.compile(r"<task-notification\b[^>]*>.*?<status>\s*(killed|stopped)\s*</status>.*?</task-notification>",
                    re.DOTALL)


def _state(state):
    for key in ("dispatch_table_pending", "sidecar_watchers"):
        if not isinstance(state.get(key, []), list):
            state[key] = []
    return state


# This view's names for `watch_records.record_state`'s rungs, keyed by (state, found_by). The
# `pid` and `inventory` rungs both report DIED or WATCH; which one answered is what separates a
# launcher that died from one that never started, and a job that is running from one still queued.
_STATE_NAMES = {("DONE", "exit"): "finished",
                ("DIED", "pid"): "died",
                ("DIED", "inventory"): "never started",
                ("WATCH", "pid"): "running",
                ("WATCH", "inventory"): "queued"}


def classify(record, inventory=None):
    """The rearm category for `record`, read from the one "has it ended" ladder."""
    state, code, found_by = watch_records.record_state(record, inventory)
    return _STATE_NAMES.get((state, found_by), "unknown"), code


def advice(payload):
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str) or not STATUS.search(prompt):
        return None
    sid = payload.get("session_id")
    if not sid:
        return None
    state = _state(dispatch_table.read_table(sid))
    pending = [e for e in state.get("dispatch_table_pending", []) if isinstance(e, dict) and e.get("kind") == "sidecar"]
    if not pending:
        return None
    watchers = state.get("sidecar_watchers") or []
    groups = {key: [] for key in ("finished", "never started", "died", "unknown")}
    uncovered = []
    for entry in pending:
        dispatch_table.resolve_sidecar_record(entry, sid, pending)
        record = entry.get("record")
        if not record:
            groups["unknown"].append(str(entry.get("id")))
            continue
        state_name, code = classify(record, entry.get("inventory"))
        if state_name in ("running", "queued"):
            if not _covered(record, watchers):
                uncovered.append(record)
        elif state_name == "finished":
            groups["finished"].append("%s exit=%s" % (record, code))
        else:
            groups[state_name].append(record)
    lines = ["[sidecar-watch-rearm] A background task stopped; check these session sidecars:"]
    for group, paths in groups.items():
        if paths:
            action = {"finished": " (read each .out when present)",
                      "never started": " (relaunch with a fresh -R path)",
                      "unknown": " (inspect before any relaunch)"}.get(group, "")
            lines.append("%s%s: %s" % (group, action, ", ".join(paths)))
    if uncovered:
        lines.append("running or queued (no live watcher): " + ", ".join(dict.fromkeys(uncovered)))
        lines.append("Arm the session watcher: %s (Bash run_in_background, timeout 7200000)" % WAKE_COMMAND)
    return "\n".join(lines) if len(lines) > 1 else None


def main():
    try:
        payload = json.load(sys.stdin)
        text = advice(payload)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                    "additionalContext": text}}))
    except Exception as exc:
        print("[sidecar-watch-rearm] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)


if __name__ == "__main__":
    main()
