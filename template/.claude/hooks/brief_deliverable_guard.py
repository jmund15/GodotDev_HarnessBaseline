#!/usr/bin/env python3
"""PreToolUse Write: the first `*.brief.md` write of a session is denied once with deliverable guidance.

Class: process (it protects the quality of a brief, not peer work or owner authority). It lands blocking on an
explicit owner request, approved as tested. Backs `orchestration` skill §4 Agent Prompt Structure ("Scope a brief
by the defect class, never by the candidates you already found").

Measured (q7 probe): with a one-time deny at the briefer's first `*.brief.md` write, 4 of 4 briefer runs wrote a
goal-indexed deliverable; 0 of 4 without it.

weakness: a briefer writes a deliverable indexed by its own candidate changes instead of by what the goal's
consumers need answered.
removeWhen: briefers write goal-indexed deliverables without this deny.

Fail posture (`instruction_quality` §16): a process guard reports a lost check, it never denies on its own
failure. State that cannot be written passes with an `additionalContext` note; an unparseable payload passes.
"""
import json
import sys

MSG = (
    "Before this brief is written (first brief of the session; write the same file again after adjusting it): "
    "if the receiver's job is to find or decide the best solution (a plan, design, review, audit, diagnosis), "
    "write its deliverable from the goal: name what the goal's consumers need answered, not your own candidate "
    "changes. Put your candidates under a heading that calls them hypotheses, and ask for each to be kept, "
    "changed or dropped with a reason, and for anything that beats them to lead. If the receiver carries out a "
    "decision, state the decision whole. Rule: `orchestration` skill §4 Agent Prompt Structure."
)
KEY = "brief_deliverable_shape"


def _emit(**fields):
    print(json.dumps({"hookSpecificOutput": dict({"hookEventName": "PreToolUse"}, **fields)}))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Write":
        return 0
    path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if not path.replace("\\", "/").endswith(".brief.md"):
        return 0
    try:
        from _hook_state import fire_once
        first = fire_once(payload.get("session_id"), KEY, on_failure=None)
    except Exception:
        first = None
    if first is None:
        _emit(additionalContext="brief_deliverable_guard could not record its once-per-session state; the "
                                "brief deliverable-shape check was skipped. " + MSG)
        return 0
    if first:
        _emit(permissionDecision="deny", permissionDecisionReason=MSG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
