"""PreCompact / SessionStart(compact) — the compaction rail for a native Workflow subagent.

A Workflow subagent that compacts got the client's default summary, and nothing re-injects its brief. Measured
2026-10-14 (`.claude/scratch/a2_probe/hook_log.jsonl`, real payloads): both hooks fire for a subagent's compaction and
reach it. The payload carries no `agent_id`; its `session_id` and `transcript_path` are the parent's, so
`_hook_state.is_subagent_payload` is the one test that tells a subagent from the parent.

PreCompact, subagent: print the shared rail (`_compaction_rail.rail_text`, route `subagent`) on stdout, which the
client appends to the summary prompt, and leave a marker for the SessionStart after it. A SessionStart payload has no
`trigger`, so the predicate cannot judge it: SessionStart(compact) prints the resume line only when a fresh marker
exists, and consumes it. A parent PreCompact clears the markers, so a parent's compaction never reads one.
Silent for the parent, for a sidecar child (`CLAUDE_CODE_SIDECAR_PROMPT_FILE` set: `sidecar_recompact_reprompt.py`
owns that route, so no child gets two rails), and on any error (advisory: exit 0, nothing printed).

Scope: lanes of independent items gain from the rail. A review seat's pick is unchanged: the A2b measurement showed
the rail does not rescue a plan-check seat (`.claude/plans/workflow-compaction-rail.md` item 1).
"""
import json
import os
import sys
import tempfile
import time

from _compaction_rail import SUBAGENT_RESUME, rail_text, selected_rail
from _hook_state import is_subagent_payload, update_json_locked

MARKER_TTL_SECONDS = 900  # a SessionStart(compact) follows its PreCompact within one summary call


def _marker_path(session_id) -> str:
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(os.environ.get("CC_CACHESTAT_DIR") or tempfile.gettempdir(),
                        "subagent-compaction-%s.json" % (safe or "unknown"))


def _set_pending(session_id, change) -> None:
    """Apply `change(count)` to the pending-subagent-compaction count, dropping a count older than the TTL."""
    def update(state):
        fresh = time.time() - float(state.get("at") or 0) < MARKER_TTL_SECONDS
        state["pending"] = max(0, change(int(state.get("pending") or 0) if fresh else 0))
        state["at"] = time.time()
    update_json_locked(_marker_path(session_id), update)


def _consume(session_id) -> bool:
    taken = []

    def update(state):
        fresh = time.time() - float(state.get("at") or 0) < MARKER_TTL_SECONDS
        pending = int(state.get("pending") or 0) if fresh else 0
        taken.append(pending > 0)
        state["pending"] = max(0, pending - 1)
        state["at"] = time.time()
    update_json_locked(_marker_path(session_id), update)
    return bool(taken and taken[0])


def main() -> int:
    if os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip():
        return 0
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if payload.get("hook_event_name") == "PreCompact":
        if is_subagent_payload(payload):
            _set_pending(session_id, lambda n: n + 1)
            print(rail_text(selected_rail(os.environ), "subagent"))
        else:
            _set_pending(session_id, lambda n: 0)
    elif payload.get("source") == "compact" and _consume(session_id):
        print(SUBAGENT_RESUME)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
