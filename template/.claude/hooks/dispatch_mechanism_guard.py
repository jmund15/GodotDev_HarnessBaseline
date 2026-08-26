#!/usr/bin/env python3
"""PreToolUse(Workflow|Agent): deny a dispatch when /orchestration has not been loaded this session.

WHY THIS EXISTS: the SessionStart rail told the model to invoke Skill(orchestration) before the
first dispatch, but a rail is passive text — it fired no enforcement at the dispatch moment, and a
bare `Agent` dispatch went out for what was a known-up-front fan-out. This makes the rail mechanical:
before ANY subagent dispatch, the orchestration skill's §0 dispatch-shape canon must be in the session
transcript. If it is not, the dispatch is DENIED with an actionable reason — the model loads the skill,
applies §0 (single Agent vs Workflow vs sidecar), and re-issues.

CHANNEL: hookSpecificOutput.permissionDecision=deny on stdout (PreToolUse convention shared with
tres_nullstrip_guard.py / prototype_containment_guard.py).

FAIL POSTURE: fail-open and silent. A hook that cannot read its inputs must never block a dispatch:
absent/empty/unreadable/oversized transcript all exit 0 with no output.

STATE: none. "Loaded" is derived from the session transcript — the Skill(orchestration) result lands
in the JSONL (verified: its §0 header line and its fan-out litmus sentence appear immediately after a
load), so no marker file is written or deleted. The marker strings below are intentionally NOT quoted
in this docstring: a session that reads this hook would otherwise inject them into the transcript and
falsely satisfy the guard.
"""
import json
import os
import sys

# Distinctive strings that appear in the session transcript ONLY after Skill(orchestration) is loaded.
# Assembled from fragments so the full phrases are never literals in THIS file — a session reading this
# hook for debugging would otherwise inject them into the transcript and falsely satisfy the guard.
ORCHESTRATION_MARKERS = (
    "Dispatch Shape — decide this " + "FIRST",
    "Can I enumerate the jobs " + "right now?",
)

# Cap the transcript read. A marker that sat past the cap (skill loaded very early in a huge session)
# degrades to one re-load on next dispatch, never a false block.
MAX_READ_BYTES = 16 * 1024 * 1024

REASON = (
    "First dispatch attempted without /orchestration loaded this session. Load Skill(orchestration) "
    "first — its §0 owns the dispatch-mechanism decision (single Agent vs Workflow vs sidecar) and "
    "the fan-out litmus. Re-issue the dispatch after loading."
)


def orchestration_loaded(transcript_path: str) -> bool:
    try:
        size = os.path.getsize(transcript_path)
        if size <= 0:
            return True  # no transcript content yet — fail open, don't block a fresh session's first action
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            if size > MAX_READ_BYTES:
                fh.seek(size - MAX_READ_BYTES)
            content = fh.read(MAX_READ_BYTES)
    except OSError:
        return True  # cannot read the transcript — fail open, never block on our own I/O failure
    return any(marker in content for marker in ORCHESTRATION_MARKERS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed payload — never block
    if payload.get("tool_name") not in ("Workflow", "Agent"):
        return 0
    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0  # no transcript to check — fail open
    if orchestration_loaded(transcript_path):
        return 0  # canon is in context — dispatch freely
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": REASON,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
