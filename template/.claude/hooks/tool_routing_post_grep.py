#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse Grep fallback advisory.

The registered PreToolUse route now uses the proven `additionalContext` channel.
This hook emits only when that pre-call route did not already deliver or dedupe
the same target-family advice, preventing a second reminder on the tool result.

Why:
- Pre-call advice is timely and model-visible. Repeating it after a successful
  Grep adds no decision; the post hook remains only as fail-open coverage if the
  pre-call advisory did not run.

What it does:
- After a Grep call completes, verifies a bare PascalCase indexed-family lookup,
  at least one hit, and no literal-intent cue.
- Suppresses output when `tool_routing_nudge.py` already recorded that family in
  `nudge_targets_seen`; otherwise emits one fallback `additionalContext` nudge.

Override suppression:
- Literal/comment scans suppress advice for every indexed family. Verified-unique-name requests
  suppress only the C# route; they do not change semantic-search routing for resources or docs.

Per-turn dedupe:
- Stashes `post_grep_nudges_fired_this_turn: [pattern, ...]` in the per-session
  state file. Same pattern twice in one turn → second nudge suppressed.

Wired through settings.json PostToolUse → `post_read_dispatch.py` for Grep.
"""

import json
import os
import sys

# Shared cue and target-family classification.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _hook_state import read_json_salvage, state_path, update_json_locked
    from routing_classifier import (
        classify_call,
        is_pascal_identifier as _is_pascal_identifier,
        grep_target_family as _grep_target_family,
        is_cloud_session as _is_cloud_session,
    )
except ImportError:
    # Defensive: if the classifier import fails, skip silently rather than
    # break the tool call.
    sys.exit(0)


# --- State helpers -------------------------------------------------------

def _read_state(session_id: str) -> dict:
    return read_json_salvage(state_path(session_id))


def _prompt_has_override(state: dict, tool_input: dict) -> bool:
    prompt = str(state.get("last_prompt") or "")
    return classify_call("Grep", tool_input, prompt).severity == "cue-exempt"


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
        "CLAUDE.md §Tool Routing."
    )


def _pre_advisory_seen(state: dict, family: str) -> bool:
    """Whether the registered PreToolUse hook already handled this family."""
    seen = state.get("nudge_targets_seen")
    return isinstance(seen, list) and f"Grep:{family}" in seen


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

    # Read state for pre-route receipt, cue-word check, and fallback dedupe.
    state = _read_state(session_id)

    if _pre_advisory_seen(state, family):
        return None

    if _prompt_has_override(state, tool_input):
        # The shared classifier applies each override only to its valid target family.
        return None

    fired = state.get("post_grep_nudges_fired_this_turn") or []
    if pattern in fired:
        # Already nudged about this exact pattern this turn — anti-fatigue.
        return None

    nudge = _build_post_grep_nudge(pattern, family, _hit_count_estimate(tool_response))

    def record(latest):
        latest_fired = latest.get("post_grep_nudges_fired_this_turn") or []
        latest_fired = list(latest_fired) if isinstance(latest_fired, list) else []
        if pattern in latest_fired:
            return False
        latest_fired.append(pattern)
        latest["post_grep_nudges_fired_this_turn"] = latest_fired[-50:]
        return True

    written, first = update_json_locked(state_path(session_id), record)
    if written and not first:
        return None
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
