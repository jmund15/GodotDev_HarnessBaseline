#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — require `instruction_quality` in context before authoring harness files.

What it does:
- Classifies the tool's file_path: only .claude/ harness surfaces (see PATH RULES).
- DENIES the edit until `instruction_quality` is in this context window: a Skill call, or a full Read of
  its SKILL.md, recorded by skill_load_marker.py as `skill_in_context`, which a compaction clears unless the
  client re-attached the skill whole and that copy's body still matches the skill's `SKILL.md`
  (`_hook_state.skill_in_context` reads both). A subagent
  (payload `agent_id`) needs its own load: the parent's does not put the skill in the subagent's context.
  An injection cannot carry the skill: the client saves a hook value over 10,000 characters to a file and
  shows a 2,000-character preview, and the skill is larger. Only the Skill tool delivers it whole.
- The deny embeds the full text of the one `reference/` file the path's class matches (`REFERENCE_FILES`,
  `reference_for`): SKILL.md carries only a pointer per section, and a Skill call delivers no reference.
  An unmapped class names all four.
- `advance_notice` is the earlier surface (instruction_quality §3): `post_read_dispatch` emits one line at the
  first Read of a governed file in a window where the skill is absent, so the deny rarely fires.
- Once loaded: emits the gates-summary reminder as `hookSpecificOutput.additionalContext`, once per compaction.
- Adds a maintained artifact's editing note on the first edit of that path in a context window; a compaction
  re-arms it. An OWNED_NOTES note is its owner's `## ` section, read when the edit fires: a maintaining
  command's, or for an always-loaded layer file `principles.md` §Layers.
- A fault in this hook reaches the dispatcher, which allows the edit and reports the lost check.

PATH RULES (in .claude/ only): an allow-list, `is_harness_file`, shared by this gate, `harness_shell_write_guard`
and the first-Read notice.
- IN:  `.md` under DOCTRINE_DIRS, the CLAUDE chain (`CLAUDE*.md`) and DOCTRINE_ROOT_FILES.
- OUT: every other path, including a directory no one has named yet. Code, `settings*.json` (OF6 O1), state,
  evidence and generated files are not agent-facing text; auto-memory/** is governed by consolidate-memory /
  memory_audit (see instruction_quality "Composition with other tools").
- Path scope uses `_claude_scope.harness_tail`, which rebases a worktree root before classifying the checkout's own `.claude/`.

Wired in: pre_edit_dispatch on Write|Edit; `main()` also supports standalone use.
"""

import json
import os
import sys

from _claude_scope import harness_tail
from _hook_state import fire_once_since_compaction, skill_in_context, spent_mark, unspend
from amend_delivery import HOOK_TEXT_CAP

# Key in the per-session routing state file that the other routing hooks share.
STATE_KEY = "harness_edit_reminder"
# Exact harness-tail paths; each note fires on the first edit of its path in a context window. An owner's rule does
# not load on a direct edit, so OWNED_NOTES reads the owner's `## ` section when the edit fires (/codify Step 4):
# one home, nothing to drift. The owner is a maintaining command, or for an always-loaded layer file `principles.md`
# §Layers. EDIT_NOTES holds rules whose only home is the note.
LAYERS = ("Layer file edit", "skills/instruction_quality/reference/principles.md", "Layers")
OWNED_NOTES = {
    "reference/model_ladder_evidence.md": ("Ladder edit", "commands/ladder_ingest.md", "What lands on the ladder"),
    "reference/external_models.json": ("Registry edit", "commands/ladder_ingest.md", "What lands on the ladder"),
    "CLAUDE.md": LAYERS,
    "CLAUDE.core.md": LAYERS,
    "CLAUDE.coding.md": LAYERS,
    "CLAUDE.godot.md": LAYERS,
    "CLAUDE.interactive.md": LAYERS,
    "guards/common.md": LAYERS,
    "guards/common.godot.md": LAYERS,
}
CLAUDE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
EDIT_NOTES = {
    "skills/project_subsystems/adaptation.json": (
        "Sensitivity tag edit: `execution_depth.md` §Sensitivity tags governs this edit. A tag sets triage "
        "fields; it never re-implements a guard that already enforces its class."
    ),
    "commands/agents/session_audit_agents.md": (
        "Lens registry edit: `execution_depth.md` §Mandate admission sets which lenses declare "
        "`admission: conditional`."
    ),
}

# The allow-list of agent-facing markdown (a surface an agent reads to decide; principles.md opening). A root
# not named here stays outside: a directory of state, evidence, generated output or tool docs (work/, logs/,
# sessions/, scripts/, tools/, hooks/, cache/, plans/, tests/, scratch/, auto-memory/) is never doctrine by default.
DOCTRINE_DIRS = ("agents", "commands", "guards", "output-styles", "reference", "rules", "skills", "user_scope",
                 "workflows")
# Root files: the CLAUDE chain, plus queued CLAUDE.md/MEMORY.md content awaiting /apply_harness_edits (guidance in
# transit: authoring errors land verbatim in the injected files) and the project's harness goal.
DOCTRINE_ROOT_FILES = ("pending_harness_edits.md", "harness-goal.md")


def is_harness_file(file_path: str) -> bool:
    """
    True if file_path is agent-facing markdown under `.claude/` that `instruction_quality` governs.
    Path separators and case are normalized: Edit/Write may deliver either spelling on Windows.
    """
    tail = harness_tail(file_path)
    if tail is None or not tail.lower().endswith(".md"):
        return False
    parts = tail.lower().split("/")
    if len(parts) == 1:
        return parts[0].startswith("claude") or parts[0] in DOCTRINE_ROOT_FILES
    return parts[0] in DOCTRINE_DIRS


# The frozen audit-input packet and the `premises` admission read the same predicate under this name.
is_doctrine_path = is_harness_file


REFERENCE_DIR = "skills/instruction_quality/reference"
# Sections 7-18 live in reference files so the skill fits the client's re-attach cap. One map for both surfaces:
# the deny embeds the matching file, and the Read-time notice names it. Which apply within an unmapped class is
# judgment by the edit's content (instruction_quality §3), so the read-when lines state that.
REFERENCE_FILES = (
    ("skills_and_commands.md", "§7-§11", "it changes a skill or a command"),
    ("orchestration.md", "§12",
     "it changes dispatch, workflow, fan-out or review-lens text, or text that could name a model or an effort"),
    ("hooks.md", "§13-§16",
     "it changes a hook, a guard, a hook's registration or a model-facing hook string"),
    ("claims_and_wording.md", "§17-§18",
     "it changes `.claude/reference/` prose, asserts a fact about a tool, file or schema, or introduces a new term"),
)
READ_CLASS_FILES = (
    f"Read in full each `{REFERENCE_DIR}/` file your edit's content matches: "
    + "; ".join(f"`{name}` ({sections}) when {when}" for name, sections, when in REFERENCE_FILES)
)


PRINCIPLES = (
    "Before changing agent-facing text, read `skills/instruction_quality/reference/principles.md` "
    "(H1–H15, the layer table) and the project's harness goal file when it has one."
)


def reference_for(file_path: str) -> str | None:
    """The `reference/` file whose sections govern this path's class, or None when the class is unmapped (the
    caller then names all four). The orchestration check runs before the wider `skills/` one."""
    tail = harness_tail(file_path)
    if tail is None or not is_harness_file(file_path):
        return None
    parts = tail.split("/")
    if parts[0] == "workflows" or parts[:2] == ["skills", "orchestration"]:
        return "orchestration.md"
    if parts[0] in ("commands", "skills"):
        return "skills_and_commands.md"
    if parts[0] == "reference":
        return "claims_and_wording.md"
    return None


def reference_text(name: str) -> str:
    """A reference file's full text, or "" when it cannot be read: the deny then still names its path."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "instruction_quality",
                        "reference", name)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def advance_notice(file_path: str) -> str:
    """The Read-time line: the edit gate's requirement and the reference file this path's class matches, so the
    agent loads both before its first edit instead of meeting them in a deny."""
    name = harness_tail(file_path) or file_path
    target = reference_for(file_path)
    files = (f"`{REFERENCE_DIR}/{target}`" if target
             else f"`{REFERENCE_DIR}/` ({', '.join(f for f, _, _ in REFERENCE_FILES)})")
    return (f"Harness file `.claude/{name}`: the harness denies any edit of it until `instruction_quality` is "
            "loaded: invoke Skill(skill=\"instruction_quality\"), or with no Skill tool Read "
            "`.claude/skills/instruction_quality/SKILL.md` whole (no offset/limit); "
            f"read {files}. {PRINCIPLES}")


def deny_text(file_path: str) -> str:
    """The deny: the unmet load, then the one reference file this path's class matches, in full. An unmapped
    class names all four instead. The requirement and the next step are the same for both."""
    name = harness_tail(file_path) or file_path
    target = reference_for(file_path)
    head = (f"Harness edit to `.claude/{name}` DENIED: `instruction_quality` is not in context. "
            "Invoke Skill(skill=\"instruction_quality\") or Read `skills/instruction_quality/SKILL.md` in full, "
            "no offset or limit")
    if not target:
        return (f"{head}, then {READ_CLASS_FILES}, then check this edit against the sections you read "
                f"and re-issue it with any change they call for. {PRINCIPLES}")
    sections = next(s for f, s, _ in REFERENCE_FILES if f == target)
    body = reference_text(target)
    cited = f"`{REFERENCE_DIR}/{target}` ({sections})" + (", included in full below" if body else "")
    deny = (f"{head}, then read {cited}, then check this edit against the sections you read and re-issue it "
            f"with any change they call for. {PRINCIPLES}")
    return deny + (f"\n\n--- {REFERENCE_DIR}/{target} ---\n{body}" if body else "")


def build_reminder(file_path: str) -> str:
    name = harness_tail(file_path) or file_path
    return (
        f"Harness edit `.claude/{name}`: apply `instruction_quality`. Most-missed gates: "
        "§3 SSOT (link, don't restate), §4 verify every cited path resolves, "
        "§6 cut non-decision rationale, §7 skill triggers, §13-16 for hooks."
        + f" {READ_CLASS_FILES}: they hold sections 7-18. {PRINCIPLES}"
    )


def _owned_texts(label, rel, heading, root=CLAUDE_DIR):
    """(the owner's `## heading` section under a line naming its home, or None when the section cannot be read;
    the home line alone)."""
    home = f"{label}: `{rel}` §{heading} governs this edit"
    try:
        with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
            parts = fh.read().split(f"\n## {heading}\n", 1)
    except OSError:
        parts = []
    section = parts[1].split("\n## ", 1)[0].strip() if len(parts) == 2 else ""
    return (f"{home}:\n\n{section}" if section else None), f"{home}; Read that section before this edit."


def owned_note(label, rel, heading, room=HOOK_TEXT_CAP, root=CLAUDE_DIR):
    """The owner's `## heading` section under a line naming its home, or that line alone when the section cannot
    be read or would pass `room` characters: past the client's cap it would reach the agent only as a preview."""
    note, home = _owned_texts(label, rel, heading, root)
    return note if note and len(note) <= room else home


def _note_once(session_id, note_key, room=HOOK_TEXT_CAP):
    """The editing note for `note_key`, or "" when none exists or this context window already holds it. An owned
    note the caller's shared `room` cut to its home line is not spent when it fits a whole cap: the next edit,
    with the cap to itself, carries the section."""
    mark = spent_mark()
    if note_key is None:
        return ""
    # An owned note latches per section: several paths share one owner's section, and a window holds it once.
    latch = "edit_note:owned:%s#%s" % OWNED_NOTES[note_key][1:] if note_key in OWNED_NOTES else "edit_note:" + note_key
    if not fire_once_since_compaction(session_id, latch):
        return ""
    if note_key not in OWNED_NOTES:
        return EDIT_NOTES[note_key]
    note, home = _owned_texts(*OWNED_NOTES[note_key])
    if note and len(note) <= room:
        return note
    if note and len(note) <= HOOK_TEXT_CAP:
        unspend(mark)
    return home


def process(input_data):
    """Dispatcher entry: `{"deny": reason}`, `{"context": reminder}` or None.

    `pre_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None

    session_id = input_data.get("session_id") or ""
    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    note_key = next((k for k in (file_path.replace('\\', '/'), harness_tail(file_path))
                     if k in EDIT_NOTES or k in OWNED_NOTES), None)
    if not is_harness_file(file_path):
        note = _note_once(session_id, note_key)
        return {"context": note} if note else None

    if not skill_in_context(session_id, "instruction_quality", input_data.get("agent_id"),
                            transcript_path=input_data.get("transcript_path")):
        return {"deny": deny_text(file_path)}
    general = build_reminder(file_path) if fire_once_since_compaction(session_id, STATE_KEY) else ""
    note = _note_once(session_id, note_key, HOOK_TEXT_CAP - len(general) - 1)
    context = "\n".join(part for part in (general, note) if part)
    return {"context": context} if context else None


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    result = process(input_data) or {}
    if result.get("deny"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result["deny"],
            }
        }))
    elif result.get("context"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": result["context"],
            }
        }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory hook: never block a harness edit because the reminder broke.
        sys.exit(0)
