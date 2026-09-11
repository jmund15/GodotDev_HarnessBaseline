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
    # skills/orchestration/SKILL.md:15 — the "## 0." section heading (verified 2026-09-01).
    "Dispatch Shape — decide this " + "FIRST",
    # skills/orchestration/SKILL.md:74 — the §5 bold lead sentence about model/effort pins
    # (verified 2026-09-01). Drawn from a DIFFERENT section than marker 1 so a partial edit
    # to either section leaves the guard matching.
    "Every dispatched agent " + "pins both",
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


# ---------------------------------------------------------------------------------------------
# Second check — a NESTED fan-out. A Workflow agent has neither the Workflow tool nor the Agent
# tool, and an Agent-tool subagent has no Workflow tool (measured 2026-09-03, both arms:
# auto-memory/archive/gotcha_subagents_have_no_workflow_tool.md). A brief that tells its delegate to
# dispatch lenses through an engine, or to execute a command whose body fans out, therefore runs
# those lenses unpinned (Agent fallback) or not at all. Home: orchestration §0 "never for". The
# orchestrator materializes the lenses (tools/lens_briefs.py) and dispatches them itself.
#
# Matched on the ACTION, not the noun (rules/harness_tooling.md): an engine or fan-out command name
# alone is allowed (a brief may cite one descriptively); a dispatch verb within the same line span
# of that name is what denies. The generated lens briefs strip the caller-facing spawn rules for
# this reason (lens_briefs.lens_facing).
import re

FANOUT_COMMANDS = r"(?:delegate|review_pr|plan_check|session_audit|structure_audit|pr_ready|explore|doc_full|architecture_brainstorm_redteam|idea_brainstorm_fanout|rule_consistency|pr_pipeline)"
FANOUT_TARGET = (
    r"(?:review_fanout\.js|dispatch\.js|dispatch_chains\.js|\bWorkflow\(\s*\{"
    r"|commands/" + FANOUT_COMMANDS + r"\.md|/" + FANOUT_COMMANDS + r"\b)"
)
# A verb is a whole word outside any path or identifier (`part_execute.md`, `sidecar-dispatch`,
# `sidecar/dispatch work` name things) in imperative position: line/sentence start, after an
# addressing word (`you need to`, `you must`), or after a step lead (`next,`, `then`).
# Descriptive proximity never denies.
VERB_LEAD = (
    r"(?:^[ \t]*(?:[-*+]\s+)?(?:next\s*,\s*)?|[.:;!?)]\s+|"
    r"\b(?:you\s+(?:need\s+to\s+|to\s+)?|(?:then|please|now|must|should|will)\s*,?\s+))"
)
VERB_WORD = r"(?<![\w/\-])(?:dispatch|fan[- ]?out|spawn|launch|invoke|execute|run)\b"
DISPATCH_VERB = VERB_LEAD + VERB_WORD
NESTED_RE = re.compile(
    r"(?im)(?:" + DISPATCH_VERB + r"[^\n]{0,160}?" + FANOUT_TARGET
    + r"|" + FANOUT_TARGET + r"[^\n]{0,200}?(?<![\w/\-])(?:execute|run|follow)\b[^\n]{0,40}\b(?:it|them|step by step|the procedure|this)\b)"
)
MAX_BRIEF_BYTES = 512 * 1024

NESTED_REASON = (
    "This brief tells its delegate to dispatch a fan-out ({hit}). A Workflow agent has neither the "
    "Workflow tool nor the Agent tool, and an Agent-tool subagent has no Workflow tool, so the inner "
    "lenses would run unpinned or not at all (orchestration §0 'never for'; "
    "gotcha_subagents_have_no_workflow_tool). Materialize the lenses as briefs "
    "(tools/lens_briefs.py) and dispatch them from THIS session through dispatch.js, then a "
    "consolidator job; delegates execute, they do not delegate."
)


def _read_capped(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(MAX_BRIEF_BYTES)
    except OSError:
        return ""  # unreadable brief: dispatch.js itself will fail loudly on it — nothing to judge here


def brief_texts(payload: dict) -> list:
    """Every text a delegate will receive as instructions, from the tool input."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if tool == "Agent":
        return [str(ti.get("prompt") or "")]
    args = ti.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return [args]
    if not isinstance(args, dict):
        return []
    texts = []
    jobs = list(args.get("jobs") or [])
    for chain in args.get("chains") or []:
        jobs.extend((chain or {}).get("jobs") or [])
    for j in jobs:
        if not isinstance(j, dict):
            continue
        p = j.get("promptPath")
        if p:
            texts.append(_read_capped(str(p)))
    for key in ("agents", "lenses"):
        for entry in args.get(key) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("prompt"):
                texts.append(str(entry["prompt"]))
            if entry.get("promptPath"):
                texts.append(_read_capped(str(entry["promptPath"])))
    return texts


# DATA the delegate judges, never an instruction to it: fenced blocks (a diff of a command file, a
# quoted brief), blockquotes, and task-list blocks (`- [ ] …` plus its indented sub-bullets — every
# worklog item quoted into a brief). A lens over harness hunks legitimately carries "run X" lines.
FENCED_RE = re.compile(r"```.*?```", re.S)
QUOTED_DATA_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:>|[-*+]\s+\[[ xX]\]|\d+[.)]\s+\[[ xX]\])"
)


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def instruction_text(text: str) -> str:
    out = []
    item_indent = None
    for ln in FENCED_RE.sub("", text).split("\n"):
        match = QUOTED_DATA_RE.match(ln)
        if match:
            item_indent = _indent_width(match.group("indent"))
            continue
        if item_indent is not None:
            if not ln.strip() or _indent_width(ln) > item_indent:
                continue
            item_indent = None
        out.append(ln)
    return "\n".join(out)


def nested_fanout_hit(payload: dict):
    for text in brief_texts(payload):
        m = NESTED_RE.search(instruction_text(text))
        if m:
            return m.group(0)[:120].replace("\n", " ")
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed payload — never block
    if payload.get("tool_name") not in ("Workflow", "Agent"):
        return 0
    transcript_path = payload.get("transcript_path")
    if transcript_path and not orchestration_loaded(transcript_path):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }}))
        return 0
    hit = nested_fanout_hit(payload)
    if hit:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": NESTED_REASON.format(hit=hit),
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
