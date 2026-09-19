#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit — synthesis-shape pattern detector.

Why:
- A synthesis-shaped request benefits from an early routing decision, but source
  count and directory names do not make that decision. The current contract
  distinguishes known focused evidence, large exact scans, bulk copyable I/O,
  and derived multi-file judgment.

What it does:
- Uses prompt cues only to identify that a routing choice is needed.
- Emits the existing four-way evidence/output/judgment litmus; it does not pick
  a route from read count, file count, or path.
- Ignores runtime notification rows that carry no user intent.

Boundaries:
- Never blocks. Always exits 0.
- No state file — pure prompt classification, no session memory needed.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _prompt_provenance import is_user_intent_prompt


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
    # from CLAUDE.md §Tool Routing, which warrants direct reads)
    r"\binventory\b",
    r"\bcheck whether\b",
    # "X and Y" enumeration shapes (3+ items)
    r"\b\w+\s*,\s*\w+\s*,\s*(and )?\w+\b",  # "A, B, and C" or "A, B, C"
)

# Two distinct cues already establish synthesis shape; the floor only drops fragments (owner R11).
MIN_PROMPT_WORDS = 8
MIN_CUE_MATCHES = 2

# Explicit audit/review requests already establish the known focused or
# line-level branch. Keep this exemption narrow so generic trace/synthesis
# requests still receive the four-way litmus.
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
        "Synthesis-shaped request: route by evidence need and output, not read count or path. "
        "Known focused evidence or line-level judgment: use direct Read/search. "
        "Large exact scans/joins: use deterministic bounded extraction. "
        "Bulk copyable I/O: use `mcp__ai-worker__read_files` with complete per-input results. "
        "Derived multi-file judgment/execution: load `orchestration` and use one qualified "
        "delegate. Do not redo evidence already retrieved. CLAUDE.md §Tool Routing.\n"
        "</user-prompt-submit-hook>"
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")  # Claude Code #10463 workaround
        sys.exit(0)
    if not isinstance(input_data, dict):
        print("{}")
        sys.exit(0)

    prompt = input_data.get("prompt", "") or ""
    if not is_user_intent_prompt(prompt):
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
