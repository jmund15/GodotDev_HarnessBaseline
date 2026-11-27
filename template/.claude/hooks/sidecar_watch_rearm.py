#!/usr/bin/env python3
"""UserPromptSubmit advisory: re-arm uncovered sidecars after any killed, stopped or completed task notice.

Reads this session's dispatch_table_pending registry, not the transcript. Finished runs are
read immediately; a missing process identity is never proof a launch did not start. One
additionalContext carries each exceptional record not yet delivered to this session (a compaction
re-arms the delivery), a `running or queued (no live watcher)` group, which stays listed while it
is uncovered, and the session-wake command that covers it. A Workflow run reports its own completion and never
blocks Stop, so it is never listed and never the reason to arm. Every prompt clears the in-flight wake mark a session watcher leaves as it exits: a prompt is the turn boundary at which its wake has been delivered. Non-notices and fully covered runs are silent. Advisory errors log to stderr and do not block the prompt. The emitted text is empty when no notice fires.
"""
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
sys.path.insert(0, HERE)
from _hook_state import fire_once_since_compaction, state_path, update_json_locked  # noqa: E402
import watch_records  # noqa: E402
from watch_records import _covered  # noqa: E402
import dispatch_table  # noqa: E402


# Per-session record of the ended-record lines already delivered; capped so a long session's state stays small.
DELIVERED_KEY = "sidecar_rearm_delivered"
DELIVERED_CAP = 200

STATUS = re.compile(r"<task-notification\b[^>]*>.*?<status>\s*(killed|stopped|completed)\s*</status>.*?</task-notification>",
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
                ("DONE", "journal"): "finished",
                ("DIED", "journal"): "died",
                ("WATCH", "journal"): "running",
                ("DIED", "pid"): "died",
                ("DIED", "inventory"): "never started",
                ("WATCH", "pid"): "running",
                ("WATCH", "inventory"): "queued",
                ("GONE", "directory"): "deleted"}


def classify(record, inventory=None, launched=None):
    """The rearm category for `record`, read from the one "has it ended" ladder."""
    state, code, found_by = watch_records.record_state(record, inventory, launched)
    return _STATE_NAMES.get((state, found_by), "unknown"), code


def advice(payload):
    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str) or not STATUS.search(prompt):
        return None
    sid = payload.get("session_id")
    if not sid:
        return None
    state = _state(dispatch_table.read_table(sid))
    pending = [e for e in state.get("dispatch_table_pending", [])
               if isinstance(e, dict) and e.get("kind") == "sidecar"]
    if not pending:
        return None
    for entry in pending:
        if entry.get("kind") == "sidecar":
            dispatch_table.resolve_sidecar_record(entry, sid, pending)
    # The one supersede rule the Stop hook and the watcher apply: a refused call's entry, or one a
    # later-issued run of the same label supersedes, is not a sidecar to inspect or wake for.
    pending = [entry for entry in pending
               if entry.get("kind") != "sidecar" or not watch_records.superseded(entry, pending)]
    watchers = state.get("sidecar_watchers") or []
    # An ended record is news once: a later notice lists only those not yet delivered, until a
    # compaction drops the earlier delivery. An uncovered one stays listed while it needs an arm.
    delivered = set() if fire_once_since_compaction(sid, "sidecar_watch_rearm") else {
        str(key) for key in state.get(DELIVERED_KEY) or []}
    groups = {key: [] for key in ("finished", "never started", "died", "deleted", "unknown")}
    uncovered = []
    for entry in pending:
        record = entry.get("record")
        if not record:
            groups["unknown"].append(str(entry.get("id")))
            continue
        state_name, code = classify(record, entry.get("inventory"), entry.get("issued_at") or entry.get("launched_at"))
        if state_name in ("running", "queued"):
            if not _covered(record, watchers):
                uncovered.append(record)
        elif state_name == "finished":
            groups["finished"].append("%s exit=%s" % (record, code))
        else:
            groups[state_name].append(record)
    fresh = {}
    for group, items in groups.items():
        fresh[group] = [item for item in items if "%s|%s" % (group, item) not in delivered]
    lines = ["[sidecar-watch-rearm] A background task ended; check these session sidecars:"]
    for group, paths in fresh.items():
        if paths:
            action = {"finished": " (read each .out when present)",
                      "never started": " (relaunch with a fresh -R path)",
                      "deleted": " (its directory is gone, so its result is lost)",
                      "unknown": " (inspect before any relaunch)"}.get(group, "")
            lines.append("%s%s: %s" % (group, action, ", ".join(paths)))
    if uncovered:
        lines.append("running or queued (no live watcher): " + ", ".join(dict.fromkeys(uncovered)))
        lines.append("Arm the session watcher %s: %s." % (watch_records.ARM_CALL, watch_records.ARM_COMMAND))
    if len(lines) == 1:
        return None
    shown = ["%s|%s" % (group, item) for group, items in fresh.items() for item in items]
    if shown:
        update_json_locked(state_path(sid), lambda current: current.__setitem__(
            DELIVERED_KEY, (sorted(delivered) + shown)[-DELIVERED_CAP:]))
    return "\n".join(lines)


def main():
    try:
        payload = json.load(sys.stdin)
        sid = payload.get("session_id")
        if sid:
            # Every prompt is a turn boundary, where a queued wake has been delivered or is about to be.
            watch_records.clear_inflight(state_path(sid))
        text = advice(payload)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                    "additionalContext": text}}))
    except Exception as exc:
        print("[sidecar-watch-rearm] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)


if __name__ == "__main__":
    main()
