#!/usr/bin/env python3
"""
Hook: PostToolUse on Skill and Read — record which skills this session has loaded.

Writes `skills_loaded` (list) into the shared per-session routing-state file. A Skill call records
its bare name; a successful full Read of `.claude/skills/<name>/SKILL.md` records `<name>` the same
way. A bounded Read counts when its response starts at line 1 and covers all file lines; a partial
Read records nothing. The ladder note fires once per compaction.

Each load also sets the compaction-scoped `skill_in_context:<name>` key, which a compaction clears. A subagent
shares its parent's session_id, so its load (payload `agent_id`) sets `skill_in_context:<name>@<agent_id>` and
leaves `skills_loaded` and the ladder note to the parent: one agent's load never puts a skill in another's context.
A load records the digest of the `SKILL.md` body it delivered: a Read, the file it read (any checkout's); a Skill
call or typed command, the session project's copy, which the client delivers. A session holding a copy the file has
since left behind is not in context until it reloads, as a skill re-attached whole after a compaction counts only
while its body still matches the file (`_hook_state.skill_in_context`).
Consumers: harness_edit_skill_reminder.py denies harness-file edits until `skill_in_context` holds for
"instruction_quality"; workflow_provider_guard.py skips its ladder fallback once "orchestration" is in
`skills_loaded`, which survives compaction (keyed by session_id, which persists across /compact).

On Skill(orchestration) it also injects the role ladder rows (additionalContext),
so the rows are in context BEFORE a pin is chosen — the Workflow-time injection
arrives with the dispatch result, after the pin (orchestration §5: load the
ladder whenever you pin). On Skill(workflow-authoring) it injects DISPATCH_NOTE once per compaction, so
the dispatch engine is named before an inline script is written.

Fail posture: advisory-side writer — always exits 0; a failed write only means
the consumer keeps denying, which is the safe direction for an enforcement pair.

A command the owner types (`/codify ...`) puts its whole body into the context with no tool call: the
UserPromptSubmit member (prompt_dispatch CHAIN) records it as the same load, from the prompt's leading `/name`.

Wired in: settings.json hooks.PostToolUse with matcher "Skill" runs `main()`. A Read reaches
`process()` through post_read_dispatch.py sub-hook 6, which calls it only for a path ending in
`SKILL.md`.
"""

import json
import os
import re
import sys

from _owner_text import LEADING_INJECTION_RE
from _hook_state import state_path, update_json_locked, fire_once_since_compaction, mark_skill_in_context


# The generic Workflow guidance says to write an inline script; this project's guard denies one that types its own
# evidence schema, after the script is written. The note lands at the decision, before the script exists.
DISPATCH_NOTE = ("In this project, dispatch pinned jobs through /delegate (orchestration section 0), "
                 "not an inline Workflow script.")


# The owner's typed command, at the start of the prompt: the raw `/name args` or the tagged row the client writes.
_TYPED_COMMAND = re.compile(r"(?:<command-message>[^<]*</command-message>\s*<command-name>/|/)([\w:.-]+)(?=\s|<|$)")


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    note = process(input_data)
    if note:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit" if "prompt" in input_data else "PostToolUse",
            "additionalContext": note,
        }}))
    sys.exit(0)


def process(input_data):
    """Record a loaded skill for this session; return the advisory text to emit, or "".

    `post_read_dispatch` calls this for the Read family, so a full SKILL.md Read counts as a load
    without another settings entry; the Skill registration still runs `main()`.
    """
    tool = input_data.get("tool_name")
    if tool is None and "prompt" in input_data:
        bare = typed_command_name(input_data.get("prompt"))
    elif tool == "Skill":
        skill = ((input_data.get("tool_input") or {}).get("skill") or "").strip()
        # Bare name and plugin-prefixed form both record under the bare name.
        bare = skill.split(":")[-1]
    elif tool == "Read":
        bare = read_skill_name(input_data)
    else:
        bare = ""
    if not bare:
        return ""

    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id") or ""

    def record(state):
        skills = state.get("skills_loaded")
        skills = list(skills) if isinstance(skills, list) else []
        if bare not in skills:
            skills.append(bare)
        state["skills_loaded"] = skills

    if not agent_id:
        update_json_locked(state_path(session_id), record)
    digest = _read_digest(input_data) if tool == "Read" else _loaded_digest(bare, input_data)
    mark_skill_in_context(session_id, bare, agent_id, digest=digest)
    if bare == "orchestration" and not agent_id and fire_once_since_compaction(session_id, "skill_load_marker:ladder"):
        return ladder_note()
    if bare == "workflow-authoring" and not agent_id and fire_once_since_compaction(session_id, "skill_load_marker:dispatch"):
        return DISPATCH_NOTE
    return ""


def typed_command_name(prompt):
    """`<name>` when the owner's prompt opens with a slash command (its whole body now fills the context), else "".
    A UserPromptSubmit prompt is the owner's own text, so no agent's field stands in; a command named mid-sentence,
    after other text, is no load."""
    body = prompt if isinstance(prompt, str) else ""
    while True:
        lead = LEADING_INJECTION_RE.match(body)
        if not lead:
            break
        body = body[lead.end():]
    match = _TYPED_COMMAND.match(body.lstrip())
    return match.group(1).split(":")[-1] if match else ""


def _read_digest(payload):
    """The body digest of the `SKILL.md` a full Read just delivered, whichever checkout holds it; None when it
    cannot be read."""
    try:
        import _compaction_record
        import _win_path
        path = _win_path.native_path(str((payload.get("tool_input") or {}).get("file_path") or ""))
        if not os.path.isabs(path):
            path = os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd(), path)
        with open(path, encoding="utf-8") as fh:
            return _compaction_record.skill_body_digest(fh.read())
    except Exception:
        return None


def _loaded_digest(name, payload):
    """The `SKILL.md` body now on disk under the session project for skill `name`, which a Skill call or typed
    command just delivered: kept with the record so a session holding it is denied once the file changes
    (`_hook_state.skill_in_context`). None when it cannot be read."""
    try:
        import _compaction_record
        import _win_path
        root = _win_path.native_path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
        return _compaction_record.skill_file_digest(name, root)
    except Exception:
        return None


def read_skill_name(payload):
    """`<name>` for a successful full Read of `.claude/skills/<name>/SKILL.md`, else ""."""
    tool_input = payload.get("tool_input") or {}
    bounded = tool_input.get("offset") is not None or tool_input.get("limit") is not None
    covered = False
    for key in ("tool_response", "tool_result"):
        response = payload.get(key)
        if isinstance(response, dict):
            if response.get("is_error"):
                return ""
            file = response.get("file") or {}
            if isinstance(file, dict):
                start, count, total = (file.get(k) for k in ("startLine", "numLines", "totalLines"))
                covered |= (all(type(n) is int for n in (start, count, total))
                            and start == 1 and total > 0 and count >= total)
    if bounded and not covered:
        return ""
    parts = os.path.normpath(str(tool_input.get("file_path") or "")).replace("\\", "/").split("/")
    if len(parts) >= 4 and parts[-4:-2] == [".claude", "skills"] and parts[-1] == "SKILL.md":
        return parts[-2]
    return ""


def ladder_note():
    """The role-ladder injection, built by workflow_provider_guard's one formatter ("" on any failure)."""
    try:
        import workflow_provider_guard as wpg
        roles = wpg.ladder_role_lines()
        return wpg.ROLE_LADDER_HEADER + "; ".join(roles) if roles else ""
    except Exception:
        return ""


if __name__ == "__main__":
    main()
