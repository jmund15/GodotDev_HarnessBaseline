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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import (fire_once, fire_once_since_compaction, state_path,
                         update_json_locked)
from _prompt_provenance import is_user_intent_prompt

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import adaptation  # noqa: E402

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")


# High-risk patterns that require explicit acknowledgment (execution mode) — domains
# where gotchas frequently cause issues. The list below is the domain-agnostic floor;
# a project appends its own high-gotcha domain keywords via `adaptation.json`
# `high_risk_patterns` (Design Doc §8), never by editing this list directly.
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


def _append_high_risk_patterns(patterns: list) -> None:
    """Append `adaptation.json` `high_risk_patterns` to `HIGH_RISK_PATTERNS` in place. An
    entry that does not compile as a regex is skipped with one stderr line — the hook must
    never crash on a project's bad pattern."""
    for entry in adaptation.get(_CLAUDE_DIR, "high_risk_patterns"):
        try:
            re.compile(entry)
        except (re.error, TypeError):
            print(
                f"prompt_memory_loader: high_risk_patterns entry {entry!r} does not "
                "compile as a regex -- skipped",
                file=sys.stderr,
            )
            continue
        patterns.append(entry)


_CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_append_high_risk_patterns(HIGH_RISK_PATTERNS)


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
# it can still shape the plan. A project lists its plan-then-execute commands via
# `adaptation.json` `drive_commands` (Design Doc §8); the empty default disables the
# branch.
DRIVE_COMMANDS: tuple = tuple(adaptation.get(_CLAUDE_DIR, "drive_commands"))


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


# Cadence for the STANDARD MemoryCheck nudge only (plan-mode and high-risk nudges stay
# uncapped — higher signal). The full text says the same thing every time, so after the
# first delivery the only new information is a domain the session has not been
# reminded about yet.
MEMORY_CHECK_SESSION_CAP = 5    # `detailed` tier only
# When no domain matched, a repeat of the full text carries nothing the session does not
# already hold: there is no domain name in it to be new. One delivery, every tier, and
# nothing re-arms it — not the repeat cap, not a compaction.
GENERIC_MEMORY_CHECK_KEY = "memory_check_generic"


def _session_tier(session_id: str) -> str:
    """The session's model tier, or `detailed` when the tier module is absent.

    Guarded import: `detailed` is the safe default — it keeps the repeat-capped cadence
    rather than assuming one delivery is enough."""
    try:
        from _model_tier import session_tier
        return str(session_tier(session_id) or "detailed")
    except Exception:
        return "detailed"


def _bump_memory_check_count(session_id: str) -> int:
    """Increment the standard-nudge count; fail open toward nudging."""
    path = state_path(session_id)

    def update(state):
        count = int(state.get("memory_check_fires", 0) or 0) + 1
        state["memory_check_fires"] = count
        return count

    written, count = update_json_locked(path, update)
    return count if written else 1


def _memory_check_is_new(session_id: str) -> bool:
    """True when the FULL MemoryCheck text is still news to this session."""
    if _session_tier(session_id) == "detailed":
        return _bump_memory_check_count(session_id) <= MEMORY_CHECK_SESSION_CAP
    return fire_once_since_compaction(session_id, "memory_check")


def _prompt_domains(prompt: str) -> list:
    """Domain tokens this prompt names, per reference/memory_domains.md (the table
    `plan_memory_reminder.DOMAINS` mirrors). A prompt matching no domain yields
    `unclassified` — an unrecognized token fails toward nudging, not toward silence."""
    try:
        from plan_memory_reminder import infer_domains
        names = [entry[0] for entry in infer_domains(prompt)]
    except Exception:
        return ["unclassified"]
    return names or ["unclassified"]


def _record_reminded_domains(session_id: str, domains: list) -> list:
    """Record reminder delivery; return domains not previously reminded.

    The legacy `searched_domains` key was also written on reminder emission, so
    it is discarded rather than migrated as evidence of a completed search.
    """
    path = state_path(session_id)

    def update(state):
        seen = state.get("reminded_domains")
        seen = list(seen) if isinstance(seen, list) else []
        new = [d for d in domains if d not in seen]
        state.pop("searched_domains", None)
        if new:
            state["reminded_domains"] = (seen + new)[-50:]
        return new

    written, new = update_json_locked(path, update)
    return new if written else list(domains)


def main():
    # Read hook input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")  # Workaround for Claude Code #10463
        sys.exit(0)
    if not isinstance(input_data, dict):
        print("{}")
        sys.exit(0)

    prompt = input_data.get("prompt", "")
    permission_mode = input_data.get("permission_mode", "default")

    if not prompt or not is_user_intent_prompt(prompt):
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
        domains = _prompt_domains(prompt)
        # A generic prompt gets one full delivery per session. Short-circuit order matters:
        # once the generic gate is spent, the domain gate below is left untouched, so a
        # later prompt that DOES name a domain still earns the full text.
        generic = domains == ["unclassified"]
        if generic and not fire_once(session_id, GENERIC_MEMORY_CHECK_KEY):
            fresh = False
        else:
            fresh = _memory_check_is_new(session_id)
        if fresh:
            _record_reminded_domains(session_id, domains)
            print("""<user-prompt-submit-hook>
MEMORY CHECK — before acting, semantic-search .claude/auto-memory (else Grep) for this task's domain gotchas, up to 3 queries. Report `Memory: <query|N/A> | Skills: <invoked|auto-rules|N/A>`.
</user-prompt-submit-hook>""")
        else:
            # `unclassified` means "no domain matched" — it earns the one generic delivery above,
            # never a NEW DOMAIN line once that delivery is spent.
            new = _record_reminded_domains(session_id, [d for d in domains if d != "unclassified"])
            if new:
                print("""<user-prompt-submit-hook>
NEW DOMAIN: %s — search auto-memory before acting.
</user-prompt-submit-hook>""" % ", ".join(new))

    sys.exit(0)


if __name__ == "__main__":
    main()
