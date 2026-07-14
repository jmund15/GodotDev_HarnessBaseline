#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit — synthesis-shape pattern detector.

Why:
- Fix 1 reconciled the brainstorming skill so its Step 1 prescribes a
  bundled read_files call. But free-form synthesis prompts ("compare X
  and Y across the codebase", "summarize how the spell pipeline flows
  through these 5 modules") don't trigger any skill — they go straight
  to the agent's default which historically defaulted to chained Reads
  + Greps. This hook injects a terse routing reminder when the user's
  prompt looks like multi-source synthesis.

What it does:
- Counts cue-phrase matches in the prompt.
- Fires only when prompt length ≥ MIN_PROMPT_WORDS AND match count ≥
  MIN_CUE_MATCHES. The two-gate setup avoids firing on prompts that
  use one cue word incidentally ("trace this back" alone doesn't mean
  "synthesize across files").
- Injects a one-paragraph reminder via the <user-prompt-submit-hook>
  wrapper. Time-bounded ("for THIS turn") so the reminder doesn't
  bleed into unrelated follow-up prompts.

Boundaries:
- Never blocks. Always exits 0.
- No state file — pure prompt classification, no session memory needed.
"""

import json
import re
import sys


# Cue phrases that indicate synthesis intent. Word-boundary matched, case-
# insensitive. Two distinct categories:
#   - "synthesize across N sources" verbs
#   - "trace / explain / how-does-X-work" patterns
# A prompt needs to match at least MIN_CUE_MATCHES distinct cues to fire.
SYNTHESIS_CUES = (
    # Cross-source synthesis verbs
    r"\bcompar(e|ing|ed)\b",
    r"\bsummariz(e|ing|ed)\b",
    r"\bsynthesi[sz](e|ing|ed)\b",
    r"\bcontrast(s|ed|ing)?\b",
    r"\breconcile\b",
    r"\bdiff(erence|er|s) between\b",
    # Trace / how-does-X-work shapes
    r"\btrace (how|the|through)\b",
    r"\bhow does .+ (work|flow|interact)\b",
    r"\bwalk me through\b",
    r"\bend.to.end\b",
    r"\bfull (path|chain|pipeline|trace)\b",
    # Multi-file / multi-doc explicit shapes
    r"\bacross (the |multiple |several )?(files|modules|docs|systems)\b",
    r"\blook at (these |the )?(files|docs)\b",
    r"\b(read|check) (these|all of these|the following)\b",
    r"\bgather (context|info|background)\b",
    # Audit-like (but NOT "audit" alone — that's the audit-shape exception
    # from CLAUDE.md §9, which warrants direct reads)
    r"\binventory\b",
    r"\bcheck whether\b",
    # "X and Y" enumeration shapes (3+ items)
    r"\b\w+\s*,\s*\w+\s*,\s*(and )?\w+\b",  # "A, B, and C" or "A, B, C"
)

MIN_PROMPT_WORDS = 30
MIN_CUE_MATCHES = 2

# Audit-shape phrases per CLAUDE.md §9 — these EXEMPT the prompt from the
# nudge (the user wants direct line-precision reads, not bundled summary).
# INTENTIONALLY NOT shared with routing_classifier.AUDIT_INTENT_CUES: that
# 16-entry list governs per-call cumulative-cascade exemption (debug/trace/
# inspect/etc. are appropriate triggers there). This 9-entry list governs
# prompt-level synthesis-shape exemption (a narrower set focused on user
# explicitly framing the WHOLE TASK as audit). Centralizing would broaden
# this nudge's exemption surface and cause it to misfire on debug/trace
# prompts that should still get the synthesis-shape reminder. The naming
# similarity is misleading; the scopes are distinct by design.
AUDIT_EXEMPT_CUES = (
    "audit",
    "code review",
    "security review",
    "line by line",
    "line-by-line",
    "fact-check",
    "fact check",
    "verify against",
    "review the changed lines",
)


def _word_count(text: str) -> int:
    return len(text.split())


def _count_cue_matches(text: str) -> int:
    """Count distinct SYNTHESIS_CUES that match in text. Case-insensitive."""
    lowered = text.lower()
    matches = 0
    for pattern in SYNTHESIS_CUES:
        if re.search(pattern, lowered):
            matches += 1
    return matches


def _is_audit_exempt(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in AUDIT_EXEMPT_CUES)


def _build_reminder() -> str:
    return (
        "<user-prompt-submit-hook>\n"
        "Synthesis-shape prompt. For first call this turn: prefer "
        "`mcp__ai-worker__read_files(paths=[...], question=...)` over chained "
        "Read/Grep/obsidian/memory. Bundle FIRST — overflow-bundling after "
        "individual searches has already burned context. "
        "Audit-cue prompts exempt (direct Read correct there). CLAUDE.md §9.\n"
        "</user-prompt-submit-hook>"
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")  # Claude Code #10463 workaround
        sys.exit(0)

    prompt = input_data.get("prompt", "") or ""
    if not prompt:
        print("{}")
        sys.exit(0)

    # Audit-shape exemption — direct reads are correct here.
    if _is_audit_exempt(prompt):
        print("{}")
        sys.exit(0)

    if _word_count(prompt) < MIN_PROMPT_WORDS:
        print("{}")
        sys.exit(0)

    if _count_cue_matches(prompt) < MIN_CUE_MATCHES:
        print("{}")
        sys.exit(0)

    print(_build_reminder())
    sys.exit(0)


if __name__ == "__main__":
    main()
