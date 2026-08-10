#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit - Mode-aware context loading

Purpose:
- PLAN MODE: Focus on exploring existing workflows, design docs, and architectural context
- EXECUTION MODE: Focus on implementation gotchas; remind to reload context if scope expands
- Scalable: No hardcoded skill/workflow names - uses generic language

Design: Mode-aware progressive disclosure
"""

import json
import os
import re
import sys

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")


# High-risk patterns that require explicit acknowledgment (execution mode)
# These are domains where gotchas frequently cause issues.
# PROJECT-CONFIG: append your project's high-gotcha domain keywords (the rows
# below are the domain-agnostic floor).
HIGH_RISK_PATTERNS = [
    # Debugging/Investigation
    r"\bdebug\b",
    r"\bfix\b",
    r"\bnot working\b",
    r"\bbroken\b",
    r"\btrace\b",
    r"\binvestigat",
    r"\bcheck.*(log|output)\b",
    r"\bwhy.*(not|isn't|doesn't)\b",
    # Refactoring
    r"\brefactor\b",
    r"\bmigrat\b",
    r"\bdeprecat\b",
    # Outward-facing / irreversible
    r"\bpublish\b",
    r"\bupload\b",
    r"\bdelete\b",
]


def is_high_risk(prompt: str) -> bool:
    """Check if prompt matches any high-risk patterns."""
    prompt_lower = prompt.lower()
    for pattern in HIGH_RISK_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


# Conversational / meta patterns — no memory search needed
CONVERSATIONAL_PATTERNS = [
    r"\b(is|are|do|does|can|could|should|would|will)\b.*\b(you|we|it|this|that)\b.*\?",  # questions about process
    r"\b(what do you think|how about|thoughts on|opinion on)\b",
    r"\b(hook|skill|command|setting|config|permission|memory|compact)s?\b.*\b(useful|helpful|worth|valuable|deprecated|hurting|redundant|beneficial|bloat|bloating|oversteer|oversteering|overkill|excessive|necessary|noise)\b",
    r"\b(let'?s|go ahead|go forward|proceed|sounds good|I agree|I like)\b",
    r"\b(explain|tell me|walk me through|help me understand)\b",
]


def is_conversational(prompt: str) -> bool:
    """Check if prompt is conversational/meta rather than a task."""
    prompt_lower = prompt.lower()
    # Must not also match high-risk (task takes priority)
    if is_high_risk(prompt):
        return False
    for pattern in CONVERSATIONAL_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


# Execution patterns — directives to carry out already-scoped, prior-agreed work
# ("apply the refinements", "do it", "go ahead and commit"). The domain-gotcha
# search already happened when the work was scoped, so a fresh MEMORY CHECK is a
# false positive. Deliberately checked BEFORE is_high_risk in the router: phrases
# like "apply the fix" overlap the high-risk `\bfix\b` keyword, and execution
# intent should win. Kept narrow (high precision) — a missed bypass just falls
# back to the standard nudge (no regression); an over-broad bypass would suppress
# a real domain task, which is the worse error.
EXECUTION_PATTERNS = [
    r"\b(do|apply|make|implement|execute|perform)\s+(it|all|both|these|those|that|the)\b.{0,40}\b(recommendation|refinement|fix|change|edit|patch|cleanup|sweep|scrub|plan|suggestion|proposal|item|step|task)s?\b",
    r"\bgo ahead (and|with)\b",
    r"\bproceed (with|to|and)\b",
    r"\b(please\s+)?(do|apply|implement|run|commit|push|stage|ship)\s+(it|them|that|this|those|all|the\s+(above|recommendation|plan|fix|change|refinements?|edits?|tests?|suite|build|gate))\b",
]


def is_execution(prompt: str) -> bool:
    """Directive to execute already-scoped work — bypass the memory nudge."""
    prompt_lower = prompt.lower()
    for pattern in EXECUTION_PATTERNS:
        if re.search(pattern, prompt_lower):
            return True
    return False


# Commands that run a whole plan→execute cycle in the normal session flow, so
# the plan-mode branch below never fires for them, and any plan-file reminder
# only fires once drafting has already begun. This branch re-keys the Memory
# obligation onto the command itself, at invocation, which is the last moment
# it can still shape the plan.
# PROJECT-CONFIG: list your project's plan-then-execute commands (empty tuple
# disables the branch; e.g. the code layer's ("/part_drive", "/plan_drive")).
DRIVE_COMMANDS: tuple = ()


def get_drive_reminder(prompt: str) -> str:
    """Drive-command Memory obligation, plus the argument to infer domains from."""
    stripped = prompt.lstrip()
    parts = stripped.split(None, 1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""

    lines = [
        "<drive-memory-obligation>",
        f"{command} runs plan-then-execute with no approval gate before code — the Memory pass is MANDATORY and yours to run:",
        "1. Search auto-memory for each inferred domain's gotchas (semantic-search if connected, else Grep).",
        "2. Record the constraint claims + their evidence in the plan file under Constraints — an unrecorded pass did not happen.",
    ]
    if argument:
        lines.append(f"Infer domains from: {argument[:200]}")
    lines.append("</drive-memory-obligation>")
    return "\n".join(lines)


# Session cap for the STANDARD MemoryCheck nudge only (plan-mode and high-risk
# nudges stay uncapped — higher signal). Same anti-fatigue rationale as
# critical_analysis_reminder's session dedupe: after a few fires the model has
# the discipline in-context; further repeats are pure token cost.
STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
MEMORY_CHECK_SESSION_CAP = 5


def _bump_memory_check_count(session_id: str) -> int:
    """Increment + return this session's standard-nudge fire count.
    Best-effort read-modify-write on the shared routing-state file;
    returns 1 on any failure (fail-open toward nudging)."""
    sid_short = (session_id[:8] if session_id else "default")
    path = os.path.join(STATE_DIR, f"{sid_short}.json")
    state: dict = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
    except Exception:
        state = {}
    count = int(state.get("memory_check_fires", 0) or 0) + 1
    state["memory_check_fires"] = count
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
    except Exception:
        pass
    return count


def main():
    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)

    prompt = input_data.get("prompt", "")
    permission_mode = input_data.get("permission_mode", "default")

    if not prompt:
        print("{}")
        sys.exit(0)

    # Slash commands bypass the length floor below — a bare "/part_drive P3.2"
    # is under 20 chars but is the highest-stakes prompt shape there is.
    # Uncapped, same as the plan-mode / high-risk branches: this is the only
    # Memory nudge a drive session ever sees.
    stripped_prompt = prompt.lstrip()
    if stripped_prompt.startswith("/"):
        first_token = stripped_prompt.split(None, 1)[0]
        if first_token in DRIVE_COMMANDS:
            print(get_drive_reminder(prompt))
            sys.exit(0)

    # Skip very short prompts (confirmations, commands). This gate also covers
    # every short acknowledgement ("lgtm", "thanks", "push") — no separate
    # phrase list needed.
    if len(prompt) < 20:
        print("{}")
        sys.exit(0)

    # === PLAN MODE ===
    if permission_mode == "plan":
        print("""<user-prompt-submit-hook>
PLAN-PERMISSION MODE — gather context first: search auto-memory for gotchas, prior art, design docs, and blast radius; load Skills for matching workflows; flag unresolved questions.
</user-prompt-submit-hook>""")

    # === DIRECTIVE: EXECUTE PRIOR-AGREED WORK (no fresh memory search needed) ===
    elif is_execution(prompt):
        # Relevant context was loaded when the work was scoped; re-nudging on
        # "apply it" / "do the refinements" is a false positive.
        sys.exit(0)

    # === EXECUTION MODE (High-Risk) ===
    elif is_high_risk(prompt):
        print("""<user-prompt-submit-hook>
HIGH-RISK TASK — search auto-memory first (semantic-search if connected, else Grep, restrictToDir=.claude/auto-memory); identify domain(s) from CLAUDE.md, load matching Skills. Report: Skills: [invoked|auto-rules|N/A] | Memory: [query]. Re-search if scope grows.
</user-prompt-submit-hook>""")

    # === CONVERSATIONAL / META (no memory needed) ===
    elif is_conversational(prompt):
        # Lightweight — no memory search instruction
        print("""<user-prompt-submit-hook>
Avoid reflexive agreement. Instead, provide substantive technical analysis.
</user-prompt-submit-hook>""")
        sys.exit(0)

    # === EXECUTION MODE (Standard) ===
    else:
        session_id = input_data.get("session_id", "") or ""
        if _bump_memory_check_count(session_id) <= MEMORY_CHECK_SESSION_CAP:
            print("""<user-prompt-submit-hook>
MEMORY CHECK — search auto-memory for domain gotchas before proceeding (semantic-search if connected, else Grep, restrictToDir=.claude/auto-memory); use CLAUDE.md for query seeds, max ~3 searches. Report: Memory: [query | N/A] | Skills: [invoked|auto-rules|N/A]. Re-search NEW domains if scope grows.
</user-prompt-submit-hook>""")

    sys.exit(0)


if __name__ == "__main__":
    main()
