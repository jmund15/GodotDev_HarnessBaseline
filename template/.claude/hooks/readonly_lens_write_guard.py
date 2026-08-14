#!/usr/bin/env python3
"""Advisory observer: a subagent wrote a file while a read-only fan-out was live.

ADVISORY, NEVER ENFORCEMENT. This hook writes at most one warning line to stderr
and ALWAYS exits 0. It emits no `permissionDecision` of any kind, and it must not
grow one without new evidence.

WHY it cannot deny (measured 2026-08-12, `.claude/scratch/_probe_payload.jsonl`):
no field in a PreToolUse payload attributes a write to a specific dispatching run.
A Workflow subagent's payload carries `agent_id` (non-empty) and the constant
`agent_type: "workflow-subagent"`, and its `session_id` is IDENTICAL to the
orchestrator's; `transcript_path` is the orchestrator's file and `prompt_id` is the
user turn. So "any agent_id present" is the only key available, and it does not
distinguish a read-only lens from a legitimate concurrent write-executor (a
`dispatch.js` `shape:'author'` job, a code executor). That collision actually
occurred in the session this guard was written in. A guard that cannot tell whose
write it is does not deny writes. Revisit only if the runtime exposes a run-scoped
field.

The marker is armed by `.claude/hooks/readonly_marker_arm.py`, a PreToolUse hook on
the `Workflow` matcher — a Workflow script cannot arm it itself (no filesystem, no
`require`, no clock, no session id). There is no disarm hook: `Workflow` returns as
soon as the run backgrounds, so the TTL is the disarm.

FAIL-OPEN. The entire body sits inside one `try/except Exception` that exits 0, and
every payload field is read with `.get()`, never indexed. The matcher is broad
(`Write|Edit`), so a fail-closed crash would wedge every edit in every future
session, while a fail-open crash loses one advisory warning.

COST. The marker stat is the FIRST statement: absent (the overwhelmingly common
case) the hook exits before parsing the payload or resolving any path. A Write/Edit
already spawns several interpreters; every edit outside a live read-only fan-out
must cost exactly one stat.

STRANDED MARKERS. Writer-side cleanup is best-effort — a killed orchestrator strands
the marker — so an EXPIRED marker, or one whose JSON will not parse, is treated as
ABSENT: allow, and unlink it.

GAP. A `Write|Edit` matcher does not see `Bash`-mediated writes. A lens shelling out
to `cp`, `>`, `sed -i`, or `git checkout` is not covered and never warns.

Mode:
    readonly_lens_write_guard.py   # PreToolUse on Write|Edit; reads the payload on stdin
"""
import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


def _marker_path(session_id: str) -> str:
    """Mirror of readonly_marker.js markerPath() — same root, same <sid8> key shape."""
    short = session_id[:8] if session_id else "default"
    return os.path.join(STATE_DIR, f"readonly-{short}.json")


def _norm(p: str) -> str:
    """Absolute, forward-slashed, case-folded — the form both sides of the prefix test use."""
    return os.path.normcase(os.path.abspath(str(p))).replace("\\", "/").rstrip("/")


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        return

    session_id = str(payload.get("session_id") or "")

    # 1. Marker stat FIRST — before any other work.
    marker = _marker_path(session_id)
    if not os.path.exists(marker):
        return

    # 2. Expired or unparseable == absent. Allow, and clean up the strand.
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            raise ValueError("marker is not an object")
        expires_at = float(state.get("expires_at") or 0)
    except Exception:
        try:
            os.unlink(marker)
        except OSError:
            pass
        return
    if expires_at <= time.time():
        try:
            os.unlink(marker)
        except OSError:
            pass
        return

    # 3. Only a subagent's write is interesting. The orchestrator's own payload has no `agent_id`.
    agent_id = str(payload.get("agent_id") or "")
    if not agent_id:
        return

    target = (payload.get("tool_input") or {}).get("file_path") or ""
    if not target:
        return

    allowed = state.get("allow_prefixes")
    allowed = allowed if isinstance(allowed, list) else []
    norm_target = _norm(target)
    for prefix in allowed:
        if not prefix:
            continue
        np = _norm(prefix)
        if norm_target == np or norm_target.startswith(np + "/"):
            return

    sys.stderr.write(
        f"[readonly-lens-write] advisory: agent {agent_id} wrote {target} while a read-only "
        f"fan-out marker was live ({marker}). Allowed prefixes: "
        f"{', '.join(allowed) if allowed else '(none)'}. Not blocked -- this hook cannot tell "
        f"which run the write belongs to, so it only reports.\n"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail-open; a swallowed exception is never silent
        sys.stderr.write(f"[readonly-lens-write] guard exited fail-open: {exc}\n")
    sys.exit(0)
