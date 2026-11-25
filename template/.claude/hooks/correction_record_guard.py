#!/usr/bin/env python3
"""pre_bash_dispatch sub-hook: a ledger row is written by the pass that decided it.

Class: process, blocking. `autolearn_outcomes.py record` appends the row `/codify` Step 0's history search and
`/autolearn`'s sweep read. Written by hand it reads as having handled a correction while the pass's search for a repeat
never ran (ten hand rows after four text fixes to `CLAUDE.interactive.md` §Corrections and surfaced work: `git log
--oneline -- .claude/CLAUDE.interactive.md`). The call is denied unless one of the commands that own the ledger is in
this context since the last compaction (`OWNERS`: `/codify` and `/autolearn` write their pass's row, `/eval_dashboard`
a declined move), the fact `_hook_state.skill_in_context` records for any skill or command the dispatch gate also reads
(`instruction_quality` §3: a deny requiring a recorded load, behind the surface that names the pass). A correction
whose cause is the work writes no row. A record to a scratch ledger (`--file`) is not the ledger Step 0 reads and passes.
Residual: a command the owner types is not a Skill call, so it counts only once re-attached whole after a compaction;
the one next step is a Skill load.
Channel: hookSpecificOutput.permissionDecision. Proof: tests/test_correction_record_guard.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _command_text import runs_script  # noqa: E402
from _hook_state import skill_in_context  # noqa: E402

OWNERS = ("codify", "autolearn", "eval_dashboard")
REASON = ('BLOCKED ledger row (CLAUDE.interactive.md §Corrections and surfaced work): a row is written by the pass '
          'that decided it, and no pass is in this context. Invoke Skill(skill="codify") on the correction: its '
          'Step 0 searches for a repeat and its Step 6 records the row. A correction whose cause is the work '
          'writes no row.')


def main():
    try:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command")
        if (payload.get("tool_name") not in ("Bash", "PowerShell") or not isinstance(command, str)
                or not runs_script(command, "autolearn_outcomes.py", flag="record")
                or runs_script(command, "autolearn_outcomes.py", flag="--file")):
            return 0
        session, agent = payload.get("session_id") or "", payload.get("agent_id") or ""
        if any(skill_in_context(session, name, agent_id=agent, transcript_path=payload.get("transcript_path"))
               for name in OWNERS):
            return 0
    except Exception:
        return 0  # a process guard that cannot judge lets the call through
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                        "permissionDecision": "deny",
                                                        "permissionDecisionReason": REASON}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
