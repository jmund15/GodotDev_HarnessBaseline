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
import sys

from _hook_state import state_path, update_json_locked


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
    # Bare name and plugin-prefixed form both record under the bare name.
    bare = skill.split(":")[-1]

    def record(state):
        skills = state.get("skills_loaded")
        skills = list(skills) if isinstance(skills, list) else []
        if bare not in skills:
            skills.append(bare)
        state["skills_loaded"] = skills

    update_json_locked(state_path(session_id), record)
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
