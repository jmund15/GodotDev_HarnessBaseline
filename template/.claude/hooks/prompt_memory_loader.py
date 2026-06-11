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
            text=True,
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
PLAN MODE — gather context first: Skills for matching workflows, auto-memory for domain gotchas (semantic-search, restrictToDir=.claude/auto-memory; CLAUDE.md domain table for query seeds), Obsidian for design docs, flag unresolved questions. Max ~3 searches.
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
HIGH-RISK TASK — search auto-memory first (semantic-search, restrictToDir=.claude/auto-memory). Identify domain(s) from CLAUDE.md table, load matching Skills. Output: [x] CONTEXT: [Skills | N/A] | Memory: [query]. Re-search if scope grows.
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
MEMORY CHECK — search auto-memory for domain gotchas before proceeding (semantic-search, restrictToDir=.claude/auto-memory). Identify domain(s) from CLAUDE.md table for query seeds; max ~3 searches. Output: [x] Memory: [query | N/A — reason] | Skills: [loaded | N/A]. Re-search NEW domains if scope grows.
</user-prompt-submit-hook>""")

    sys.exit(0)


if __name__ == "__main__":
    main()
