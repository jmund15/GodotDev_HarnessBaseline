#!/usr/bin/env python3
"""Hook: an auto-mode classifier denial teaches the next move instead of ending the turn.

`--record` (PermissionDenied): files `{tag, tool}` in the session state. A PermissionDenied hook can
only return `retry`, so it cannot speak to the model itself.
`--coach` (Stop): if denials were filed since the last coaching, blocks the stop once with each tag's
guidance, then clears them. `stop_hook_active` clears without blocking, so it never loops. No-verdict
denials are skipped: Claude Code's own message already says retry later or move on.

Wired in: settings.project.json hooks.PermissionDenied and hooks.Stop (composed into settings.json).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import state_path, update_json_locked  # noqa: E402
try:
    import approving_gates  # noqa: E402
except Exception:
    approving_gates = None
try:
    import overnight_ask_guard  # noqa: E402
except Exception:
    overnight_ask_guard = None

KEY = "classifier_denials"
_TAG = re.compile(r"\[([^\]]+)\]")
PARK = ("If a question above is off the critical path, park it: keep working, and put it in the "
        "AskUserQuestion packet you send at the end. Ask now only when nothing else can advance without it.")
MOVES = {
    "Self-Modification": "the classifier reads the user's messages and CLAUDE.md, not your findings. If this "
                         "edit does what the user asked, the user's answer to a question naming the exact change "
                         "clears it. Otherwise drop consent or provenance text and keep ask-the-user steps.",
    "Security Test Removal": "running or editing a proof is ordinary test work; ask the user one question that "
                             "names the command and says it is a proof run.",
    "Irreversible Local Destruction": "name the exact path, or copy it aside first; never a glob or variable.",
    "Auto-Mode Bypass": "do not retry it another way: drop it, or ask the user one question naming the action.",
}
ASKS = frozenset(("Self-Modification", "Security Test Removal", "Auto-Mode Bypass"))
GENERIC = ("If the user asked for this action, it is still needed: never replace it with other work. "
           "Ask the user one question naming it exactly.")
CLOSE = "Finish the rest of the task first; do not end the turn idle."
OVERNIGHT = ("/overnight is armed, so no one can answer: follow commands/overnight.md §Step 2 "
             "to decide or park with the actual blocker and default; the classifier denial still "
             "stands. Record the exact denied command in the Q row evidence; do not ask or bypass it.")


def _record(payload):
    match = _TAG.search(payload.get("reason") or "")
    if not match or not payload.get("session_id"):
        return
    entry = {"tag": match.group(1), "tool": payload.get("tool_name") or "tool"}
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, str):
        entry["command"] = command
    try:
        entry["gate"] = approving_gates.explain(payload) if approving_gates is not None else None
    except Exception:
        entry["gate"] = None

    def add(state):
        state.setdefault(KEY, []).append(entry)
        del state[KEY][:-20]
    update_json_locked(state_path(payload["session_id"]), add)


def _overnight_armed(payload):
    try:
        return overnight_ask_guard is not None and overnight_ask_guard.armed(
            payload.get("cwd") or None, payload["session_id"])
    except Exception:
        return False


def _coach(payload):
    if not payload.get("session_id"):
        return None
    _, pending = update_json_locked(state_path(payload["session_id"]), lambda s: s.pop(KEY, None))
    if not pending or payload.get("stop_hook_active"):
        return None
    unattended = _overnight_armed(payload)
    seen, lines, asks = set(), [], False
    for entry in pending:
        tag = entry.get("tag") or "?"
        gate = entry.get("gate")
        command = entry.get("command")
        key = (tag, gate, command)
        if key not in seen:
            seen.add(key)
            move = OVERNIGHT if unattended else gate or MOVES.get(tag, GENERIC)
            asks = asks or move is GENERIC or (tag in ASKS and move is MOVES[tag])
            denied = " Denied command: %s." % json.dumps(command, ensure_ascii=False) if command is not None else ""
            lines.append("[%s] on %s: %s%s" % (tag, entry.get("tool"), move, denied))
    tail = PARK + " " + CLOSE if asks else CLOSE
    return {"decision": "block", "reason": "Auto mode denied: " + " ".join(lines) + " " + tail}


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)
    if "--record" in sys.argv:
        _record(payload)
    elif "--coach" in sys.argv:
        out = _coach(payload)
        if out:
            sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory: a broken coach never blocks a stop
