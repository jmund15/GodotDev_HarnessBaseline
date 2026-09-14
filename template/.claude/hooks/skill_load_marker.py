#!/usr/bin/env python3
"""
Hook: PostToolUse on Skill — record which skills this session has loaded.

Writes `skills_loaded` (list) into the shared per-session routing-state file.
Consumers: harness_edit_skill_reminder.py denies harness-file edits until
"instruction_quality" appears in that list; workflow_provider_guard.py skips
its ladder fallback once "orchestration" does. State survives compaction (keyed
by session_id, which persists across /compact).

On Skill(orchestration) it also injects the role ladder rows (additionalContext),
so the rows are in context BEFORE a pin is chosen — the Workflow-time injection
arrives with the dispatch result, after the pin (orchestration §5: load the
ladder whenever you pin).

Fail posture: advisory-side writer — always exits 0; a failed write only means
the consumer keeps denying, which is the safe direction for an enforcement pair.

Wired in: settings.json hooks.PostToolUse with matcher "Skill".
"""

import json
import os
import sys
import tempfile

from _hook_state import read_json_salvage, write_json_atomic

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


def _state_path(session_id: str) -> str:
    sid_short = (session_id[:8] if session_id else "default")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") != "Skill":
        sys.exit(0)

    skill = ((input_data.get("tool_input") or {}).get("skill") or "").strip()
    if not skill:
        sys.exit(0)

    session_id = input_data.get("session_id") or ""
    path = _state_path(session_id)
    try:
        state = read_json_salvage(path)
        skills = state.get("skills_loaded")
        if not isinstance(skills, list):
            skills = []
        # Bare name and plugin-prefixed form both record under the bare name.
        bare = skill.split(":")[-1]
        if bare not in skills:
            skills.append(bare)
        state["skills_loaded"] = skills
        os.makedirs(STATE_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=STATE_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=True)
        os.replace(tmp_path, path)
    except Exception:
        pass
    if skill.split(":")[-1] == "orchestration":
        try:
            from workflow_provider_guard import ladder_role_lines
            roles = ladder_role_lines()
        except Exception:
            roles = []
        if roles:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "[role ladder — pin against THESE rows, not the registry roster (orchestration §5)] " + " ;; ".join(roles),
            }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
