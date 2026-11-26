#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse on Write|Edit — at the first edit of a runtime harness file, name its counted fixes.

`reference/mechanism_litmus.md` (*A fix re-asks the litmus*) routes a repeated fix into a redesign. The
decision it informs is the edit, and the fact it needs is the file's own fix history, read from git when
the edit happens: `mechanism_census.fix_history` counts the file's fix commits in the census window
(every fix counts; a redesign commit with its plan restarts the count). Two or
more is a repeat (`mechanism_census.MIN_REPEAT_FIXES`, the census's own floor for listing a file). The notice reports facts only: the
count, the newest fix subjects and the clause's pointer. Whether the clause binds this file (it binds the files the
census lists as most-fixed) and what to do is the author's; the hook does not order a redesign. Once per path, agent
and compaction window.

Not read: a recorded census reading. It is written by a manual review step and absent where that
step has not run, which left the earlier Read-time notice silent.

Advisory only. Never blocks, never exits 2, fails open on any internal error. A path outside the
census's runtime harness files costs one string test.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from _claude_scope import harness_tail, normalize
from _hook_state import _agent_suffix, fire_once_since_compaction

SHOWN_SUBJECTS = 3
SUBJECT_CAP = 90


def process(input_data):
    """Dispatcher entry: `{"context": notice}` at the first edit of a file with a repeat of counted fixes, else None."""
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None
    tool_input = input_data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = str(tool_input.get("file_path") or "")
    tail = harness_tail(path)
    if not tail:
        return None
    import mechanism_census
    rel = ".claude/" + tail
    if not mechanism_census.runtime_harness_path(rel):
        return None
    root = normalize(path)[:-len(rel)]
    if not os.path.isdir(root):
        return None
    key = "repeat_fix_notice:" + normalize(path) + _agent_suffix(input_data.get("agent_id"))
    if not fire_once_since_compaction(input_data.get("session_id") or "", key):
        return None
    fixes = mechanism_census.counted_fixes(root, "HEAD", "%d.days" % mechanism_census.DEFAULT_WINDOW_DAYS,
                                          paths=(rel,))[0].get(rel, [])
    if len(fixes) < mechanism_census.MIN_REPEAT_FIXES:
        return None
    subjects = "; ".join("%s %s" % (f["sha"], f["subject"][:SUBJECT_CAP]) for f in fixes[:SHOWN_SUBJECTS])
    return {"context": (
        f"[repeat-fix] {rel} has {len(fixes)} counted fix commits in the last {mechanism_census.DEFAULT_WINDOW_DAYS} days, newest first: "
        f"{subjects}. Clause: reference/mechanism_litmus.md, A fix re-asks the litmus "
        "(binds the files the census lists as most-fixed)."
    )}


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    result = process(input_data) if isinstance(input_data, dict) else None
    if result:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": result["context"],
        }}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # Advisory hook — never block an edit on an internal error
