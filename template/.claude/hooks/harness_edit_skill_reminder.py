#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — require `instruction_quality` in context before authoring harness files.

What it does:
- Classifies the tool's file_path: only .claude/ harness surfaces (see PATH RULES).
- DENIES the edit until `instruction_quality` is in this context window: a Skill call, or a full Read of
  its SKILL.md, recorded by skill_load_marker.py as `skill_in_context`, which a compaction clears unless the
  client re-attached the skill whole (`_hook_state.skill_in_context` reads the attachment). A subagent
  (payload `agent_id`) needs its own load: the parent's does not put the skill in the subagent's context.
  An injection cannot carry the skill: the client saves a hook value over 10,000 characters to a file and
  shows a 2,000-character preview, and the skill is larger. Only the Skill tool delivers it whole.
  The deny and the reminder name the four `reference/` files holding §7-§18 with when each applies
  (`READ_CLASS_FILES`): SKILL.md carries only a pointer per section, and a Skill call delivers no reference.
  The agent reads the files its edit's content matches; the hook does not verify the read.
- Once loaded: emits the gates-summary reminder as `hookSpecificOutput.additionalContext`, once per compaction.
- Adds a maintained artifact's editing note (EDIT_NOTES) on every edit of that path.
- A fault in this hook reaches the dispatcher, which allows the edit and reports the lost check.

PATH RULES (in .claude/ only):
- IN:  **/*.md (CLAUDE.md, skills, commands, rules), hooks/*.py, settings*.json
- OUT: auto-memory/** — governed by consolidate-memory / memory_audit, not this
       skill (see instruction_quality "Composition with other tools").
- OUT: scratch/**, tests/**, __pycache__/**, plans/**, .cache/** — not loaded guidance.
- IN (carve-out): pending_harness_edits.md — queued CLAUDE.md/MEMORY.md
  content awaiting /apply_harness_edits; it IS loaded guidance in transit, and
  authoring errors there land verbatim in the injected files.
- Path scope uses `_claude_scope.harness_tail`, which rebases a worktree root before classifying the checkout's own `.claude/`.

Wired in: pre_edit_dispatch on Write|Edit; `main()` also supports standalone use.
"""

import json
import sys

from _claude_scope import harness_tail
from _hook_state import fire_once_since_compaction, skill_in_context

# Key in the per-session routing state file that the other routing hooks share.
STATE_KEY = "harness_edit_reminder"
# Exact harness-tail paths; each note fires on every edit. The owner command does not load on a direct
# edit, so a note carries its rule's full sentence (/codify Step 0), word for word as the owner states
# it, plus the owner's name; a check a bound proof runs at commit is not named.
EDIT_NOTES = {
    "reference/model_ladder_evidence.md": (
        "Ladder edit: "
        "A cell states whatever decides a pin within the tiers the row claims, for example the work it is "
        "the cheapest reliable pick for, the work where it is the strongest pick when quality outranks "
        "cost, a strength, an efficiency or inefficiency, a weakness or a reliability limit. A `−` clause never bars "
        "a work shape inside a tier the row claims: a row that cannot do a tier's work loses the registry "
        "claim instead. It never states a benchmark "
        "placement or figure: a `bench` result reaches a cell only where it disagrees with `pub` or `use`, "
        "or shows a failure a pin must avoid. "
        "When sources disagree on a shape, first find the scope that separates them (problem size, how open the "
        "problem is, how many parts interlock) and state each verdict with its scope (\"matches opus on scoped "
        "architecting; opus is stronger on complex open design\"). With no separating scope, the owner's `use` "
        "ruling stands; between other sources (`bench`, `pub`, an `/eval_dashboard` readout of rated dispatches), "
        "weigh how many runs back each side and how close each is to the work the pin serves. A thin side (no repeat "
        "behind it, or a gap inside the run-to-run spread) changes no clause the other side supports: queue a repeat "
        "run or a `/pin_ab`. "
        "Lead a cell with its routing verdict (\"the cheapest reliable execution of a converged plan\", never "
        "\"executes a converged plan at the top of the board\"). A row's `effort` and `±` cells cover every "
        "work shape of every tier its registry rows claim, including everyday shapes no battery task measures "
        "(surveys, explore lenses, rubric checks). A measured shape gets its verdict judged against the other rows "
        "claiming its tier (`TASKS.md` maps tasks to shapes). An unmeasured shape takes its verdict and starting effort "
        "from the row's nearest measured shape, its intel and the owner's rulings, and is marked unmeasured. A row that matches a pricier row on a shape is that shape's cheapest reliable pick, "
        "and the cell says so even when the row's other results are unremarkable. A clause about a seat or a "
        "pairing states when an orchestrator adds the row and what to check in its return, never only that it "
        "does less. A strength claim needs a lead "
        "outside the judge band; a cheap row that matches the strongest rows says so, since then no pricier "
        "pin buys quality. A comparison may name another row's family (\"trails opus on open design\"); "
        "`tools/ladder_prose_check.py` fails a name no ladder row covers."
        " `/ladder_ingest` §What lands on the ladder owns this rule and governs every ladder edit."
        " `effort` owns every effort level; a `±` cell names none. A `±` cell compares with another row by"
        " family and scope (\"trails opus on complex open design\"), never by that row's effort, and never says"
        " which effort to pin. An `effort` cell gives the starting effort per work shape: each level, then the"
        " shapes that start there, levels separated by `·`. It adds the signal that moves an effort (how well"
        " a defect can hide, how convoluted the problem) only where that signal changes a pick; `orchestration`"
        " §5 *Effort* owns the raise and the lower. A range names both its ends."
        " A clause is as wide as the evidence behind it. A result from one task, one"
        " effort or runs whose spreads overlap supports a scoped statement (\"showed no gain on chained"
        " research; untested elsewhere\"), never a claim about the model or the effort in general. An effort that"
        " is enough on one task has not shown the others fail, and \"only\", \"always\" and \"never\" need"
        " results across shapes. An effort level earns a place only where it can be the pick, and the owner"
        " accepts any removal. The battery command `ladder-dominance \"<model>·<effort>\"` lists, per task, the"
        " pins that score within the judge band or higher at no more cost, with the speed gap, each pin's rated"
        " real dispatches and whether it is paid from the same plan. Propose removing an effort level only when"
        " same-plan pins dominate it on every shape its cell claims for it. A dominator on another plan leaves"
        " the level as that plan's fallback when its quota runs out; dominance on a shape the cell does not"
        " claim says nothing about the level; an unmeasured shape gives no verdict. A dominator counts only if"
        " it is available and claims the shape's tier. The proposal carries the listing and what the real"
        " dispatches add: they corroborate it, or show the dominator never ran here or ran poorly. The cell"
        " changes after the owner accepts, and a level whose cell states what it buys is not proposed again."
        " Registry `effort.avoid` lists only a level removed this way. A cell is read mid-dispatch, so every"
        " clause changes a pick. Cut a clause that restates another column, the Role definitions or the"
        " selection rules, explains a term, or narrates why. Call an effort level an effort, never a rung. Every shape term"
        " in a cell names work an orchestrator can recognize before it dispatches, and is defined in the"
        " ladder's Role definitions or written as a plain description. An orchestrator reads the ladder cold"
        " to pick a row for work in front of it; a property it cannot see at that moment (a brief whose"
        " premise is secretly false, a benchmark's scoring partition) is a benchmark construct, never a shape,"
        " and a term defined only in a benchmark file is undefined. Version"
        " takeover audits every inherited clause. When a row's model version changes, read each `±` and"
        " `effort` clause against the new version's current board cells, the owner's rulings and what the new"
        " version's published results say. A clause stays when one of them backs it or none contradicts it; a"
        " contradicted clause is rewritten or deleted. Then run the battery command `ladder-lag --audited"
        " <row>`: the session banner names each row whose version moved since its last audit, so inherited"
        " clauses cannot outlive the version they were measured on."
    ),
    "skills/project_subsystems/adaptation.json": (
        "Sensitivity tag edit: `execution_depth.md` §Sensitivity tags governs this edit. A tag sets triage "
        "fields; it never re-implements a guard that already enforces its class."
    ),
    "commands/agents/session_audit_agents.md": (
        "Lens registry edit: `execution_depth.md` §Mandate admission sets which lenses declare "
        "`admission: conditional`."
    ),
}

INCLUDED_MD_ANY_DEPTH = ".md"
INCLUDED_EXACT_DIRS = ("hooks",)          # .claude/hooks/*.py
# plans/: execution docs owned by plan_file_format, not doctrine. .cache/: mirrors (baseline-repo);
# a checkout hosted there is rebased first, so its own .claude/ tail stays gated.
EXCLUDED_DIRS = ("auto-memory", "scratch", "tests", "__pycache__", "plans", ".cache")


def is_harness_file(file_path: str) -> bool:
    """
    True if file_path is a .claude/ surface governed by `instruction_quality`.
    Path separators are normalized — Edit/Write may deliver either on Windows.
    """
    tail = harness_tail(file_path)
    if tail is None:
        return False
    parts = tail.split("/")

    # Queued injected-file content is harness guidance in transit, so the gate
    # covers it even though it is not under a governed subdirectory.
    if tail == "pending_harness_edits.md":
        return True

    # The worklog mirrors are generated by /worklog; their quality is owned by that command.
    if tail in ("worklog-titles.md", "worklog-pending.md"):
        return False

    if any(seg in EXCLUDED_DIRS for seg in parts[:-1]):
        return False

    name = parts[-1]
    if name.endswith(INCLUDED_MD_ANY_DEPTH):
        return True
    if name.startswith("settings") and name.endswith(".json"):
        return True
    if name.endswith(".py") and len(parts) > 1 and parts[-2] in INCLUDED_EXACT_DIRS:
        return True
    return False


def is_doctrine_path(file_path: str) -> bool:
    """The markdown subset of governed harness files; shared by owed and doctrine review admission."""
    return str(file_path).endswith(".md") and is_harness_file(file_path)


# Sections 7-18 live in reference files so the skill fits the client's re-attach cap. Which apply is judgment
# by the edit's content (instruction_quality §3), so the hook states the files and their read-when lines.
READ_CLASS_FILES = (
    "Read in full each `skills/instruction_quality/reference/` file your edit's content matches: "
    "`skills_and_commands.md` (§7-§11) when it changes a skill or a command; "
    "`orchestration.md` (§12) when it changes dispatch, workflow, fan-out or review-lens text, or text that could "
    "name a model or an effort; "
    "`hooks.md` (§13-§16) when it changes a hook, a guard, a hook's registration or a model-facing hook string; "
    "`claims_and_wording.md` (§17-§18) when it changes `.claude/reference/` prose, asserts a fact about a tool, "
    "file or schema, or introduces a new term"
)


def build_reminder(file_path: str) -> str:
    name = harness_tail(file_path) or file_path
    return (
        f"Harness edit `.claude/{name}`: apply `instruction_quality`. Most-missed gates: "
        "§3 SSOT (link, don't restate), §4 verify every cited path resolves, "
        "§6 cut non-decision rationale, §7 skill triggers, §13-16 for hooks."
        + f" {READ_CLASS_FILES}: they hold sections 7-18."
    )


def process(input_data):
    """Dispatcher entry: `{"deny": reason}`, `{"context": reminder}` or None.

    `pre_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None

    session_id = input_data.get("session_id") or ""
    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    note = EDIT_NOTES.get(file_path.replace('\\', '/'), EDIT_NOTES.get(harness_tail(file_path), ""))
    if not is_harness_file(file_path):
        return {"context": note} if note else None

    if not skill_in_context(session_id, "instruction_quality", input_data.get("agent_id"),
                            transcript_path=input_data.get("transcript_path")):
        name = harness_tail(file_path) or file_path
        return {"deny": (
            f"Harness edit to `.claude/{name}` DENIED: `instruction_quality` is not in context. "
            "Invoke Skill(skill=\"instruction_quality\") or Read `skills/instruction_quality/SKILL.md` in full, "
            "no offset or limit"
            + f", then {READ_CLASS_FILES}, then check this edit against the sections you read "
            "and re-issue it with any change they call for."
        )}
    general = build_reminder(file_path) if fire_once_since_compaction(session_id, STATE_KEY) else ""
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
