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
- ~/.claude/.routing_state/<sid>.json (shared with cumulative hooks).
- Reads/writes the `critical_analysis_session_fired: bool` field.
- Other fields preserved unchanged across read-modify-write.
"""

import json
import os
import re
import sys


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

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
SESSION_FIRED_FLAG = "critical_analysis_session_fired"


def _is_proposal(prompt: str) -> bool:
    lowered = prompt.lower()
    for pat in SKIP_PATTERNS:
        if re.match(pat, lowered):
            return False
    return any(re.search(pat, lowered) for pat in PROPOSAL_PATTERNS)


def _state_path(session_id: str) -> str:
    sid_short = (session_id[:8] if session_id else "default")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _already_fired(session_id: str) -> bool:
    """True if this session already saw the rubric. Defensive on errors."""
    try:
        with open(_state_path(session_id), "r", encoding="utf-8") as f:
            state = json.load(f)
        return bool(state.get(SESSION_FIRED_FLAG, False))
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _mark_fired(session_id: str) -> None:
    """Write the session-fired flag back. Read-modify-write preserves other fields."""
    path = _state_path(session_id)
    state: dict = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
    except (OSError, json.JSONDecodeError, ValueError):
        state = {}
    state[SESSION_FIRED_FLAG] = True
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
    except OSError:
        pass  # Non-fatal: dedupe degrades to per-call (current behavior)


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
