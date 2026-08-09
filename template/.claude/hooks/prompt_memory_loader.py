#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit - Mode-aware context loading

Purpose:
- PLAN MODE: Focus on exploring existing workflows, design docs, and architectural context
- EXECUTION MODE: Focus on implementation gotchas; remind to reload context if scope expands
- LOG ANALYSIS: Auto-analyze Godot logs when user mentions log-related keywords
- Scalable: No hardcoded skill/workflow names - uses generic language

Design: Mode-aware progressive disclosure
"""

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")


# High-risk patterns that require explicit acknowledgment (execution mode)
# These are domains where gotchas frequently cause issues
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
    # HSM/State machines
    r"\bstate\s*(machine|transition)\b",
    r"\bHSM\b",
    r"\btransition\b",
    r"\bfreeze\b",
    r"\bstun\b",
    r"\bstatus\s*effect\b",
    # Refactoring
    r"\brefactor\b",
    r"\bmigrat\b",
    r"\bdeprecat\b",
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


# Log analysis keywords - triggers automatic Godot log analysis
LOG_ANALYSIS_KEYWORDS = [
    r"\bgodot\s*log",
    r"\banalyze\s*(the\s*)?(log|output)",
    r"\bcheck\s*(the\s*)?(log|output|error)",
    r"\bgame\s*(crash|error|issue)",
    r"\bdebug\s*output",
    r"\bwhat.*(error|warning|issue)",
    r"\brun\s*(the\s*)?game.*check",
    r"\bafter\s*running",
    r"\bpool\s*(issue|error|warning)",
    r"\bspawn\s*(issue|error|lag)",
]

# Resource file keywords - triggers UID/resource reminder
RESOURCE_FILE_KEYWORDS = [
    r"\.tres\b",
    r"\.tscn\b",
    r"\buid\b",
    r"\bresource\s*(file|path|reference)",
    r"\bedit.*(scene|resource)",
    r"\bmodify.*(scene|resource)",
    r"\bmissing\s*(dependency|reference|uid)",
]


def should_remind_resource_files(prompt: str) -> bool:
    """Check if prompt mentions resource file editing."""
    prompt_lower = prompt.lower()
    for pattern in RESOURCE_FILE_KEYWORDS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def get_resource_reminder() -> str:
    """Return a resource file editing reminder."""
    return """<resource-file-reminder>
Editing Godot resource files (.tres/.tscn):
- Use get_uid MCP tool to verify UIDs before manual edits
- Search Memory for "UID" if you encounter dependency issues
- Read the file first to understand existing structure
</resource-file-reminder>"""

# Drive commands (/part_drive etc.) run a whole plan→execute cycle in the normal
# session flow, so the plan-mode branch below never fires for them, and
# plan_memory_reminder only fires once the plan FILE is written — i.e. after
# drafting has already begun. Their invocation string is also far under the
# 20-char floor. This branch re-keys the Memory obligation onto the command
# itself, at invocation, which is the last moment it can still shape the plan.
DRIVE_COMMANDS = ("/part_drive", "/feature_drive", "/design_drive")


def get_drive_reminder(prompt: str) -> str:
    """Drive-command Memory obligation, plus the argument to infer domains from."""
    stripped = prompt.lstrip()
    parts = stripped.split(None, 1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""

    lines = [
        "<drive-memory-obligation>",
        f"{command} runs plan-then-execute with no approval gate before code — the Memory pass is MANDATORY and yours to run:",
        "1. Dispatch /explore (its exp-memory floor lens covers the per-domain auto-memory search plus the unconditional `arch_rule_*` sweep).",
        "2. Record the dossier's constraint claims + their evidence in the plan file under Constraints — an unrecorded pass did not happen.",
    ]
    if argument:
        lines.append(f"Infer domains from: {argument[:200]}")
    lines.append("</drive-memory-obligation>")
    return "\n".join(lines)


ANALYZER_SCRIPT = Path(__file__).parent / "analyze_godot_logs.py"

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


def _get_godot_log_path() -> Path:
    """Return the Godot log path for the current platform."""
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        return home / "AppData/Roaming/Godot/app_userdata/{{PROJECT_NAME}}/logs/godot.log"
    elif system == "Darwin":
        return home / "Library/Application Support/Godot/app_userdata/{{PROJECT_NAME}}/logs/godot.log"
    else:  # Linux and other Unix
        return home / ".local/share/godot/app_userdata/{{PROJECT_NAME}}/logs/godot.log"


DEFAULT_LOG_PATH = _get_godot_log_path()


def should_analyze_logs(prompt: str) -> bool:
    """Check if prompt mentions log-related keywords."""
    prompt_lower = prompt.lower()
    for pattern in LOG_ANALYSIS_KEYWORDS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def run_log_analysis() -> str:
    """Run the Godot log analyzer and return formatted results."""
    if not ANALYZER_SCRIPT.exists():
        return ""

    if not DEFAULT_LOG_PATH.exists():
        return ""

    try:
        # sys.executable guarantees the same interpreter that runs this hook
        # ("python" can resolve to the MS-Store stub on Windows and fail silently).
        result = subprocess.run(
            [sys.executable, str(ANALYZER_SCRIPT), str(DEFAULT_LOG_PATH), "--json"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
            cwd=str(ANALYZER_SCRIPT.parent)
        )

        if result.returncode != 0:
            return ""

        data = json.loads(result.stdout)

        # Skip if no issues
        counts = data.get("counts", {})
        errors = counts.get("errors", 0)
        warnings = counts.get("warnings", 0)

        if errors == 0 and warnings == 0:
            return ""

        # Format concise summary
        lines = [
            "<godot-log-analysis>",
            f"Godot Log: {errors} errors | {warnings} warnings",
        ]

        # Top error
        grouped_errors = data.get("grouped_errors", [])
        if grouped_errors:
            top = grouped_errors[0]
            lines.append(f"Top Error: \"{top[0][:50]}...\" ({top[1]}x)")

        # Top warning
        grouped_warnings = data.get("grouped_warnings", [])
        if grouped_warnings:
            top = grouped_warnings[0]
            lines.append(f"Top Warning: \"{top[0][:50]}...\" ({top[1]}x)")

        # Recommendations
        recs = data.get("recommendations", [])
        if recs:
            rec = recs[0]
            lines.append(f"Recommendation: [{rec.get('severity', 'MEDIUM')}] {rec.get('fix', '')[:60]}")

        lines.append("Use /analyze_godot_logs for full details.")
        lines.append("</godot-log-analysis>")

        return "\n".join(lines)

    except Exception:
        return ""


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

    # === CONDITIONAL CONTEXT (prepended to other output) ===
    extra_context = []

    # Log analysis
    if should_analyze_logs(prompt):
        log_output = run_log_analysis()
        if log_output:
            extra_context.append(log_output)

    # Resource file reminder
    if should_remind_resource_files(prompt):
        extra_context.append(get_resource_reminder())

    # === PLAN MODE ===
    if permission_mode == "plan":
        output = """<user-prompt-submit-hook>
PLAN-PERMISSION MODE — gather context first: dispatch /explore for the state sweep (memory gotchas, prior art, design docs, blast radius), load Skills for matching workflows, and flag unresolved questions.
</user-prompt-submit-hook>"""
        for ctx in extra_context:
            print(ctx)
        print(output)

    # === DIRECTIVE: EXECUTE PRIOR-AGREED WORK (no fresh memory search needed) ===
    elif is_execution(prompt):
        # Relevant context was loaded when the work was scoped; re-nudging on
        # "apply it" / "do the refinements" is a false positive. Still surface
        # any conditional context (log analysis, resource reminder) if relevant.
        for ctx in extra_context:
            print(ctx)
        sys.exit(0)

    # === EXECUTION MODE (High-Risk) ===
    elif is_high_risk(prompt):
        output = """<user-prompt-submit-hook>
HIGH-RISK TASK — search auto-memory first (semantic-search, restrictToDir=.claude/auto-memory); identify domain(s) from CLAUDE.md table, load matching Skills. Report: Skills: [invoked|auto-rules|N/A] | Memory: [query]. Re-search if scope grows.
</user-prompt-submit-hook>"""
        for ctx in extra_context:
            print(ctx)
        print(output)

    # === CONVERSATIONAL / META (no memory needed) ===
    elif is_conversational(prompt):
        # Lightweight — no memory search instruction
        output = """<user-prompt-submit-hook>
Avoid reflexive agreement. Instead, provide substantive technical analysis.
</user-prompt-submit-hook>"""
        for ctx in extra_context:
            print(ctx)
        print(output)
        sys.exit(0)

    # === EXECUTION MODE (Standard) ===
    else:
        for ctx in extra_context:
            print(ctx)
        session_id = input_data.get("session_id", "") or ""
        if _bump_memory_check_count(session_id) <= MEMORY_CHECK_SESSION_CAP:
            print("""<user-prompt-submit-hook>
MEMORY CHECK — search auto-memory for domain gotchas before proceeding (semantic-search, restrictToDir=.claude/auto-memory); use CLAUDE.md table for query seeds, max ~3 searches. Report: Memory: [query | N/A] | Skills: [invoked|auto-rules|N/A]. Re-search NEW domains if scope grows.
</user-prompt-submit-hook>""")

    sys.exit(0)


if __name__ == "__main__":
    main()
