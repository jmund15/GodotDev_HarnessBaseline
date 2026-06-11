#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse Grep retroactive nudge — for-next-time advisory after the
fact, using the more-visible additionalContext channel.

Why:
- The PreToolUse `tool_routing_nudge.py` fires on the same Grep shape (bare
  PascalCase + indexed family) but emits to stderr, which agents inconsistently
  surface in their narrative response. The 2026-05-03 routing-compliance battery
  scored 8 failures on PascalCase Grep (C2, C3, D1, D2, E1, E2, E3, I1) — the
  Phase 0 diagnostic confirmed the hook fires for subagents AND that
  sophisticated agents cite §9 explicitly when they override. This post-hook
  reinforces via the `additionalContext` JSON channel (proven to land in
  tool-result frames per `plan_memory_reminder.py` precedent).

What it does:
- After a Grep call completes, checks: pattern is bare PascalCase + glob targets
  indexed file family + result has ≥1 hit + user prompt does NOT contain literal-
  intent cue words.
- If all hold, emits an `additionalContext` nudge phrased as "for next time" —
  does NOT pressure a re-do for THIS query (per CLAUDE.md §9 first-call-recovery
  rule, which is per-query-not-per-turn — see nudge text for full statement).

K1 suppression:
- The K1 trap-case test ("Show me every literal occurrence of FireballBehavior
  including comments") is the canonical legitimate Grep override. Any user
  prompt containing literal-intent cue words suppresses the nudge.

Per-turn dedupe:
- Stashes `post_grep_nudges_fired_this_turn: [pattern, ...]` in the per-session
  state file. Same pattern twice in one turn → second nudge suppressed.

Wired in: settings.json hooks.PostToolUse with matcher "Grep".
"""

import json
import os
import sys

# Shared classifier — extracted 2026-05-04 to centralize cue lists + helpers
# that were previously duplicated across nudge.py / post_grep.py / cumulative.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from routing_classifier import (
        LITERAL_INTENT_CUES,
        is_pascal_identifier as _is_pascal_identifier,
        grep_target_family as _grep_target_family,
        is_cloud_session as _is_cloud_session,
    )
except ImportError:
    # Defensive: if the classifier import fails, skip silently rather than
    # break the tool call.
    sys.exit(0)


STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


# --- State helpers -------------------------------------------------------

def _state_path(session_id: str) -> str:
    sid_short = (session_id[:8] if session_id else "default")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_state(session_id: str) -> dict:
    try:
        with open(_state_path(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass
    return {}


def _write_state(session_id: str, state: dict) -> None:
    """Best-effort write — failure is silent (cumulative hook owns the visible-failure path)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_state_path(session_id), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
    except Exception:
        pass


# --- Cue-word + result parsing -------------------------------------------

def _prompt_has_literal_intent(state: dict) -> bool:
    prompt = (state.get("last_prompt") or "").lower()
    if not prompt:
        return False
    return any(cue in prompt for cue in LITERAL_INTENT_CUES)


def _grep_result_has_hits(tool_response) -> bool:
    """
    Defensive parser — Grep's tool_response shape varies by output_mode.
    Treat anything non-empty as ≥1 hit. Special-case the 'No matches found'
    pattern that ripgrep returns.
    """
    if tool_response is None:
        return False
    if isinstance(tool_response, dict):
        # Common shapes: {"content": "..."} or list of files
        content = tool_response.get("content")
        if isinstance(content, list):
            return len(content) > 0 and any(c for c in content)
        if isinstance(content, str):
            return _string_has_hits(content)
        # Fallback: any truthy value in dict
        return any(v for v in tool_response.values())
    if isinstance(tool_response, str):
        return _string_has_hits(tool_response)
    if isinstance(tool_response, list):
        return len(tool_response) > 0
    return False


def _string_has_hits(s: str) -> bool:
    if not s or not s.strip():
        return False
    lowered = s.strip().lower()
    if lowered.startswith("no matches found") or lowered == "no files found":
        return False
    return True


def _hit_count_estimate(tool_response) -> int:
    """Rough hit count for the nudge text. Returns 0 on uncertainty."""
    if isinstance(tool_response, dict):
        content = tool_response.get("content")
        if isinstance(content, list):
            return len(content)
        if isinstance(content, str):
            # Crude: count non-empty lines
            return sum(1 for ln in content.splitlines() if ln.strip())
    if isinstance(tool_response, str):
        return sum(1 for ln in tool_response.splitlines() if ln.strip())
    if isinstance(tool_response, list):
        return len(tool_response)
    return 0


# --- Nudge composition ---------------------------------------------------

def _build_post_grep_nudge(pattern: str, family: str, hit_count: int) -> str:
    count_phrase = f"{hit_count} hits" if hit_count > 0 else "results"
    if family == "cs":
        if _is_cloud_session():
            tool_suggestion = (
                f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')` "
                "(cloud — LSP unavailable)"
            )
        else:
            # Anchor-then-navigate: Grep('class X') is LEGITIMATE as anchor step
            # (not a bypass), unlike bare Grep(X). See csharp_lsp.md.
            tool_suggestion = (
                f"anchor-then-navigate: `Grep('class {pattern}\\b' -g '*.cs')` → "
                f"`LSP documentSymbol` → `LSP findReferences`"
            )
    else:
        tool_suggestion = (
            f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')`"
        )
    return (
        f"[tool-routing] Retroactive: `Grep('{pattern}')` returned {count_phrase} — "
        "bare PascalCase on indexed types is the LSP/semantic-search bypass smell. "
        f"NEXT PascalCase lookup: {tool_suggestion}. "
        "(Per-query recovery: don't redo this; reroute future PascalCase lookups.) "
        "CLAUDE.md §9."
    )


# --- Main ----------------------------------------------------------------

def process(input_data: dict) -> str | None:
    """In-process entry — returns the retroactive nudge text or None.
    Called by post_read_dispatch.py; main() wraps it for standalone wiring."""
    if input_data.get("tool_name") != "Grep":
        return None

    tool_input = input_data.get("tool_input") or {}
    tool_response = input_data.get("tool_response")
    session_id = input_data.get("session_id") or ""

    pattern = tool_input.get("pattern") or ""
    if not _is_pascal_identifier(pattern):
        return None

    family = _grep_target_family(tool_input)
    # `other` = non-indexed family — Grep is fine; no nudge. Same gate as PreToolUse.
    if family == "other":
        return None

    if not _grep_result_has_hits(tool_response):
        # No hits → nothing to retroactively suggest improving on.
        return None

    # Read state for cue-word check + dedupe.
    state = _read_state(session_id)

    if _prompt_has_literal_intent(state):
        # K1-style legitimate override — the user explicitly wanted literal scan.
        return None

    fired = state.get("post_grep_nudges_fired_this_turn") or []
    if pattern in fired:
        # Already nudged about this exact pattern this turn — anti-fatigue.
        return None

    nudge = _build_post_grep_nudge(pattern, family, _hit_count_estimate(tool_response))

    # Update state: record this pattern as nudged this turn.
    fired.append(pattern)
    state["post_grep_nudges_fired_this_turn"] = fired
    _write_state(session_id, state)
    return nudge


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    nudge = process(input_data)
    if nudge:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": nudge,
            }
        }
        sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    main()
