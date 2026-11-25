"""Compaction rail for native Workflow subagents, and the parent's compaction-state clear.

PreCompact: print the shared rail (`_compaction_rail.rail_text`, route `subagent`) for a subagent.
`_hook_state.is_subagent_payload` also handles older PreCompact payloads without `agent_id`.

SessionStart(compact): a non-empty `agent_id` gets the resume line without touching parent state.
The client sends it on Workflow subagent starts, but not on parent starts (session b9a508a5,
`logs/compaction_hooks.jsonl`, 2026-11-24). If a client omits it on a subagent start, the hook
cannot tell it from a parent start and takes the parent path.

On the parent path, clear the skill and ladder markers and stamp the clear. PreCompact is too early:
the old context can keep working for up to 99 minutes. On the measured parent compactions the client
writes the boundary after SessionStart runs, so a skill re-attached in that batch counts.

Sidecar children (`CLAUDE_CODE_SIDECAR_PROMPT_FILE` set) still clear their own parent state but
print nothing: `sidecar_recompact_reprompt.py` owns their rail. Errors are silent (advisory).

The A2b measurement did not change the pick for a plan-check seat
(`.claude/plans/workflow-compaction-rail.md` item 1).
"""
import json
import os
import sys

from _compaction_rail import SUBAGENT_RESUME, rail_text, selected_rail
from _hook_state import clear_compaction_keys, is_subagent_payload


def main() -> int:
    sidecar = bool(os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip())
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if payload.get("hook_event_name") == "PreCompact":
        if not sidecar and is_subagent_payload(payload):
            print(rail_text(selected_rail(os.environ), "subagent"))
    elif payload.get("source") == "compact":
        if str(payload.get("agent_id") or "").strip():
            if not sidecar:
                print(SUBAGENT_RESUME)
        else:
            clear_compaction_keys(session_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
