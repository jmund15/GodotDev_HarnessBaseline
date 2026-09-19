#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit — encourage critical analysis of user suggestions.

Fires once per session (session-level dedupe). The 2026-05-07 Phase A audit
showed this hook was firing ~5 times per session on average (one outlier
session: 48 fires) at ~200 tokens each, accumulating 25-28K tokens of
identical rubric across recent sessions. After fire #1, the model has
internalized the framing — subsequent fires were pure context bloat.

Detection: distinctive suggestion-shape phrases ("i think we should",
"how about", "right?", etc.). Two-gate: PROPOSAL_PATTERNS match AND no
prior fire this session.

State file:
- The shared routing state file, `_hook_state.state_path(session_id)`.
- Reads/writes the `critical_analysis_session_fired: bool` field under the shared lock, so fields
  other hooks write survive.
"""

import json
import re
import sys

from _hook_state import read_json_salvage, state_path, update_json_locked


PROPOSAL_PATTERNS = [
    r"\b(i think we should|i think you should|we should|you should)\b",
    r"\b(how about|what about|what if)\b",
    r"\b(i suggest|i propose|my idea|my thought)\b",
    r"\b(i was thinking|i've been thinking)\b",
    r"\b(maybe we could|maybe you could|perhaps we could)\b",
    r"\b(wouldn't it be better|would it be better|isn't it better)\b",
    r"\b(shouldn't we|shouldn't you|couldn't we)\b",
    r"\b(let's just|why don't we|why not just)\b",
    r"\b(i believe|i think)\b",
    r"\bright\s*\?",
    r"\b(does that make sense|make sense)\s*\?",
    r"\b(don't you think|do you think|do you agree)\b",
    r"\b(sound good|sounds good)\s*\?",
    r"\b(what do you think about|thoughts on)\b",
    r"\b(your thoughts)\b",
    r"\b(instead of|rather than|better approach)\b",
    r"\b(i'd prefer|i would prefer|i'd rather)\b",
    # NOTE: bare "right?" intent is covered by the \bright\s*\? pattern above;
    # an unescaped `right?` here would match the word "right" anywhere.
    r"\b(can we just|can't we just)\b",
]

SKIP_PATTERNS = [
    r"^\s*(yes|no|ok|okay|sure|thanks|go ahead|do it|please do|lgtm)\s*[.!?]*\s*$",
    r"^\s*/",  # Slash commands
]

SESSION_FIRED_FLAG = "critical_analysis_session_fired"


def _is_proposal(prompt: str) -> bool:
    lowered = prompt.lower()
    for pat in SKIP_PATTERNS:
        if re.match(pat, lowered):
            return False
    return any(re.search(pat, lowered) for pat in PROPOSAL_PATTERNS)


def _state_path(session_id: str) -> str:
    return state_path(session_id)


def _already_fired(session_id: str) -> bool:
    """True if this session already saw the rubric. Defensive on errors."""
    return bool(read_json_salvage(_state_path(session_id)).get(SESSION_FIRED_FLAG, False))


def _mark_fired(session_id: str) -> None:
    """Set the session-fired flag under the shared lock so concurrent hooks keep their fields.

    Non-fatal: a failed update degrades dedupe to per-call."""
    update_json_locked(_state_path(session_id), lambda state: state.__setitem__(SESSION_FIRED_FLAG, True))


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        sys.exit(0)

    prompt = input_data.get("prompt", "") or ""
    session_id = input_data.get("session_id", "") or ""

    if not prompt or not _is_proposal(prompt):
        print("{}")
        sys.exit(0)

    if _already_fired(session_id):
        print("{}")
        sys.exit(0)

    # Compressed reminder — model has the full rubric in training; this is
    # the in-session anchor, not the explainer.
    print("<user-prompt-submit-hook>\n"
          "Suggestion-shape prompt detected. Critically analyze before agreeing — "
          "verify against codebase, point out issues exhaustively, then act. "
          "Agreement is fine when earned.\n"
          "</user-prompt-submit-hook>")
    _mark_fired(session_id)
    sys.exit(0)


if __name__ == "__main__":
    main()
