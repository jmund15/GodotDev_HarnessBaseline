#!/usr/bin/env python3
"""PreToolUse AskUserQuestion guard for the current session's /overnight run.

Home: commands/overnight.md, Step 2. State is active-<session-id>.json under
.claude/scratch/overnight; legacy anonymous active.json is never claimed.
Expired owned state is removed. Invalid state and hook errors fail open.
Proof: tests/test_overnight_ask_guard.py.
"""
import json
import math
import os
import re
import sys
import time

from _hook_state import write_json_atomic

TTL_HOURS = 16

REASON = (
    "/overnight is armed — no one is here to answer. Decide it if you would have marked "
    "the option (Recommended) AND it is reversible from the branch AND inside the goal's "
    "scope; log it as D<n>. Otherwise park it as Q<n> (fork, options, your recommendation) "
    "in the session's decisions doc and finish everything that does not depend on it. "
    "Rule: commands/overnight.md §Step 2. Disarm: overnight_ask_guard.py --disarm."
)


def _root():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def session_id(value=None):
    value = os.environ.get("CLAUDE_CODE_SESSION_ID", "") if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("A valid CLAUDE_CODE_SESSION_ID is required; anonymous state is not owned.")
    return value


def marker_path(root=None, sid=None):
    return os.path.join(root or _root(), ".claude", "scratch", "overnight", f"active-{session_id(sid)}.json")


def owned_state(root=None, sid=None):
    sid = session_id(sid)
    try:
        with open(marker_path(root, sid), encoding="utf-8") as stream:
            doc = json.load(stream)
        if not isinstance(doc, dict) or doc.get("session_id") != sid:
            return None
        stamp = doc.get("armed_at")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            return None
        if stamp > time.time() or not isinstance(doc.get("goal"), str):
            return None
        return doc
    except (OSError, ValueError):
        return None


def arm(goal, root=None):
    sid = session_id()
    path = marker_path(root, sid)
    if os.path.exists(path) and owned_state(root, sid) is None:
        raise ValueError("Existing marker has invalid ownership or state; preserve it for inspection.")
    if not write_json_atomic(path, {"goal": goal, "session_id": sid, "armed_at": time.time()}):
        raise OSError("Could not persist overnight state.")
    print(f"ARMED {path}")


def disarm(root=None, sid=None):
    if owned_state(root, sid) is not None:
        os.unlink(marker_path(root, sid))
        print("DISARMED")
    else:
        print("NOT_ARMED (no valid owned marker)")


def armed(root=None, sid=None):
    doc = owned_state(root, sid)
    if doc is None:
        return False
    if time.time() - doc["armed_at"] > TTL_HOURS * 3600:
        try:
            os.unlink(marker_path(root, sid))
        except OSError:
            pass
        return False
    return True


def main(argv):
    if len(argv) >= 2:
        try:
            if argv[1] == "--arm":
                goal = " ".join(argv[2:]).strip()
                if not goal:
                    raise ValueError("--arm requires a non-empty goal.")
                arm(goal)
            elif argv[1] == "--disarm" and len(argv) == 2:
                disarm()
            elif argv[1] == "--status" and len(argv) == 2:
                sid = session_id()
                active = armed(sid=sid)
                print(json.dumps({"session_id": sid, "armed": active,
                                  "marker_path": marker_path(sid=sid),
                                  "goal": (owned_state(sid=sid) or {}).get("goal") if active else None}))
            else:
                raise ValueError("Use --arm <goal>, --status, or --disarm.")
            return 0
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
            return 0
        root = payload.get("cwd") or _root()
        if armed(root, payload.get("session_id", "")):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": REASON}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
