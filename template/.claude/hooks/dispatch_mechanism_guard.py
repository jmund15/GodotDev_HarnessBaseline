#!/usr/bin/env python3
"""PreToolUse(Workflow|Agent): require /orchestration and a full model-ladder Read since compaction;
deny a brief that tells its delegate to fan out; deny a pinned non-exploratory Agent (Workflow's job).

WHY THIS EXISTS: the SessionStart rail told the model to invoke Skill(orchestration) before the
first dispatch, but a rail is passive text — it fired no enforcement at the dispatch moment, and a
bare `Agent` dispatch went out for what was a known-up-front fan-out. This makes the rail mechanical:
before ANY subagent dispatch, the orchestration skill's §0 dispatch-shape canon must be in the session
transcript. If it is not, the dispatch is DENIED with an actionable reason — the model loads the skill,
applies §0 (single Agent vs Workflow vs sidecar), and re-issues.

CHANNEL: hookSpecificOutput.permissionDecision=deny on stdout (PreToolUse convention shared with
tres_nullstrip_guard.py / prototype_containment_guard.py).

FAIL POSTURE: fail closed for a matched dispatch. Missing, empty, unreadable, or malformed
transcript evidence cannot prove that the mandatory skill was loaded.

STATE: none. "Loaded" is derived from the session transcript — the Skill(orchestration) result lands
in the JSONL (verified: its §0 header line and its fan-out litmus sentence appear immediately after a
load), so no marker file is written or deleted. The marker strings below are intentionally NOT quoted
in this docstring: a session that reads this hook would otherwise inject them into the transcript and
falsely satisfy the guard.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_ladder_gate  # noqa: E402

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
    "Dispatch denied: load Skill(orchestration), pick the mechanism by its §0, then re-issue the "
    "dispatch. A Workflow within the size guideline needs no user opt-in; never swap in a direct "
    "Agent for enumerable jobs."
)

LADDER_REASON = (
    "Dispatch denied: Read `{path}` in full, by this absolute path (a worktree copy does not "
    "count). One full Read covers every dispatch until compaction."
)

UNVERIFIABLE_REASON = (
    "Dispatch denied: the guard cannot verify the session transcript, so the Skill(orchestration) "
    "load is unproven. Restore readable transcript evidence, load the skill, then re-issue."
)



def orchestration_loaded(transcript_path: str):
    """True/False for readable evidence; None when evidence cannot be verified."""
    if not transcript_path:
        return None
    try:
        size = os.path.getsize(transcript_path)
        if size <= 0:
            return None
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            if size > MAX_READ_BYTES:
                fh.seek(size - MAX_READ_BYTES)
            content = fh.read(MAX_READ_BYTES)
    except OSError:
        return None
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
VERB_WORD = r"(?<![\w/\-])(?:dispatch|fan[- ]?out|spawn|launch|invoke|execute|run|use)\b"
DISPATCH_VERB = VERB_LEAD + VERB_WORD
NESTED_RE = re.compile(
    r"(?im)(?:" + DISPATCH_VERB + r"[^\n]{0,160}?" + FANOUT_TARGET
    + r"|" + FANOUT_TARGET + r"[^\n]{0,200}?(?<![\w/\-])(?:execute|run|follow)\b[^\n]{0,40}\b(?:it|them|step by step|the procedure|this)\b)"
)
MAX_BRIEF_BYTES = 512 * 1024
OVERSIZED_BRIEF = object()
OVERSIZED_HIT = "brief exceeds the 512 KiB scan cap"

NESTED_REASON = (
    "Dispatch denied: this brief tells its delegate to dispatch a fan-out ({hit}). Delegates have "
    "no Workflow tool, so those lenses would run unpinned or not at all (orchestration §0). "
    "Materialize them with tools/lens_briefs.py and dispatch them from this session through "
    "dispatch.js, plus a consolidator job."
)


def _read_capped(path: str):
    try:
        with open(path, "rb") as fh:
            content = fh.read(MAX_BRIEF_BYTES + 1)
        if len(content) > MAX_BRIEF_BYTES:
            return OVERSIZED_BRIEF
        return content.decode("utf-8", errors="replace")
    except OSError:
        return ""  # unreadable brief: dispatch.js itself will fail loudly on it — nothing to judge here


def _entries(value):
    return value if isinstance(value, list) else []


def brief_texts(payload: dict) -> list:
    """Every text a delegate will receive as instructions, from the tool input."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return []
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
    jobs = list(_entries(args.get("jobs")))
    for chain in _entries(args.get("chains")):
        if isinstance(chain, dict):
            jobs.extend(_entries(chain.get("jobs")))
    for j in jobs:
        if not isinstance(j, dict):
            continue
        p = j.get("promptPath")
        if p:
            texts.append(_read_capped(str(p)))
    for key in ("agents", "lenses"):
        for entry in _entries(args.get(key)):
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
        if text is OVERSIZED_BRIEF:
            return OVERSIZED_HIT
        m = NESTED_RE.search(instruction_text(text))
        if m:
            return m.group(0)[:120].replace("\n", " ")
    return None


# Third check — a PINNED Agent. orchestration §0: a job whose model you can pin is a job you can
# enumerate, and an enumerable job goes through Workflow, which pins model AND effort and logs the
# PINS row the metrics archive reads. The Agent route pins the model only, inherits session effort
# and leaves no attributable record (2026-09-15, session 3259384b: a 33-file converged edit sweep
# went out as Agent+model). Exploratory types (Explore, Plan) and a fork (which ignores the pin) are
# the §0 exception by construction; any other pinned Agent states its exception in the brief.
EXPLORATORY_TYPES = ("Explore", "Plan", "fork")
AGENT_EXCEPTION_RE = re.compile(r"(?im)^\s*AGENT-EXCEPTION:\s*\S")

PINNED_AGENT_REASON = (
    "Dispatch denied: a pinned Agent ({model}) cannot pin effort or log a PINS row (orchestration "
    "§0). Re-issue through Workflow with model and effort pins, or, for a genuinely exploratory job, "
    "add the prompt line `AGENT-EXCEPTION: <why the job list is not enumerable yet>`."
)


def pinned_agent_hit(payload: dict):
    if payload.get("tool_name") != "Agent":
        return None
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    model = ti.get("model")
    if not model:
        return None
    if str(ti.get("subagent_type") or "") in EXPLORATORY_TYPES:
        return None
    if AGENT_EXCEPTION_RE.search(str(ti.get("prompt") or "")):
        return None
    return str(model)


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        _deny(UNVERIFIABLE_REASON)
        return 0
    if payload.get("tool_name") not in ("Workflow", "Agent"):
        return 0
    loaded = orchestration_loaded(payload.get("transcript_path"))
    if loaded is not True:
        _deny(REASON if loaded is False else UNVERIFIABLE_REASON)
        return 0
    hit = nested_fanout_hit(payload)
    if hit:
        _deny(NESTED_REASON.format(hit=hit))
        return 0
    model = pinned_agent_hit(payload)
    if model:
        _deny(PINNED_AGENT_REASON.format(model=model))
        return 0
    if not model_ladder_gate.claim_loaded(payload.get("session_id") or ""):
        _deny(LADDER_REASON.format(path=model_ladder_gate.expected_path(payload)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
