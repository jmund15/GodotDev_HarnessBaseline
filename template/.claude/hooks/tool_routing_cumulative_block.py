#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse companion to tool_routing_cumulative.py — promotes the
cumulative cascade nudge to a hard block at HARD_THRESHOLD when the env
var PP_ROUTING_HARD_BLOCK_CASCADE=true.

Why:
- The PostToolUse cumulative counter detects synthesis-cascade behavior
  (4+ reads/searches with low diversity OR high single-tool streak) and
  emits an `additionalContext` nudge. Per the user transcripts that
  motivated this work, agents acknowledge the nudge but the call has
  already burned context tokens by then. Promoting the same threshold to
  a PreToolUse block forces the agent to bundle into read_files on the
  next call rather than continuing the cascade.

Why this is safe now (post-2026-05-03 schema change):
- Claude Code's hook stdin now exposes `agent_id` for subagent contexts.
- `tool_routing_cumulative.py` keys state by (session_id, agent_id).
- Sibling subagents in parallel dispatches no longer share a counter, so
  blocking at HARD_THRESHOLD won't false-positive across siblings.

Block sequence (HARD_THRESHOLD=7):
  Calls 1-3: PreToolUse exits 0. Cumulative PostToolUse increments. No nudge.
  Call 4:    PostToolUse cumulative fires SOFT nudge if cascade_shape holds
             (focused: ≤LOW_DIVERSITY_DIR_LIMIT distinct dirs, OR cascading:
             ≥HIGH_STREAK_THRESHOLD same-tool streak). Diverse exploration
             skips the nudge.
  Calls 5-6: PreToolUse exits 0. Cumulative increments.
  Call 7:    PreToolUse exits 0 (state still shows count=6). Call executes.
             Cumulative increments to 7, fires HARD nudge IFF (cascade_soft
             already in fired AND cascade_shape still holds) — escalation
             gate, not raw threshold.
  Call 8+:   PreToolUse BLOCKS IFF (cascade_hard in fired) — backstops the
             ignored HARD nudge. Diverse exploration that didn't trip HARD
             also doesn't trip the block; the block's only job is enforcing
             the warning ladder, not setting an independent threshold.
             Agent must reroute to read_files (not in this hook's matcher).

Carve-outs (must NOT block):
- Audit-intent prompts — same AUDIT_INTENT_CUES list as cumulative.py.
  The user explicitly framed the task as needing primary-source engagement;
  bundling into a cheap-model summary would defeat that.
- BURST suppression — if the cumulative hook suppressed nudges due to
  rapid-fire same-agent burst, this hook also suppresses (same signal).

Env-var toggle:
- PP_ROUTING_HARD_BLOCK_CASCADE — default OFF (opt-in).

Wired in: settings.json hooks.PreToolUse with the same matcher as the
PostToolUse cumulative hook.
"""

import json
import os
import sys

# Lift state-key + threshold + cue-list from the PostToolUse counter so
# both halves of the mechanism share definitions verbatim.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tool_routing_cumulative import (
        _state_path,
        _read_state,
        _is_parallel_burst,
        HARD_THRESHOLD,
        AUDIT_INTENT_CUES,
    )
    _IMPORTS_OK = True
except ImportError:
    # Defensive: if the import path is unexpected, become a no-op rather
    # than break the tool call (a module-level sys.exit would also kill
    # pre_read_dispatch.py when imported in-process).
    _IMPORTS_OK = False


HARD_BLOCK_ENV_VAR = "PP_ROUTING_HARD_BLOCK_CASCADE"


def _hard_block_enabled() -> bool:
    return os.environ.get(HARD_BLOCK_ENV_VAR, "").lower() in ("1", "true", "yes")


def _audit_exempt(state: dict) -> bool:
    prompt = (state.get("last_prompt") or "").lower()
    if not prompt:
        return False
    return any(cue in prompt for cue in AUDIT_INTENT_CUES)


def _build_block_message(count: int, calls: list) -> str:
    targets = []
    for c in calls[-min(10, len(calls)):]:
        t = c.get("target") or ""
        if t:
            if t.startswith(("GREP:", "GLOB:", "SEARCH:")):
                t = t.split(":", 2)[-1][:40]
            targets.append(t)
    target_list = "; ".join(targets) if targets else "(targets unavailable)"
    toggle_state = os.environ.get(HARD_BLOCK_ENV_VAR, "unset")
    return (
        f"[tool-routing] BLOCKED: {count} reads/searches this turn — synthesis shape. "
        "Bundle next into "
        "`mcp__ai-worker__read_files(paths=[<accumulated>], question=<...>)`. "
        f"Targets: {target_list}. "
        f"Gate: PP_ROUTING_HARD_BLOCK_CASCADE={toggle_state} (set false to disable). "
        "Audit-cue prompts exempt (audit/debug/security review/line-by-line). "
        "CLAUDE.md §9."
    )


def process(input_data: dict) -> str | None:
    """In-process entry — returns the block message or None.
    Called by pre_read_dispatch.py; main() wraps it for standalone wiring."""
    # Toggle gate FIRST — zero overhead when the block is disabled.
    if not _IMPORTS_OK or not _hard_block_enabled():
        return None

    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id") or ""

    state = _read_state(session_id, agent_id)

    # Audit-shape exemption — same logic as cumulative.py.
    if _audit_exempt(state):
        return None

    # Filter stale calls — only count entries newer than the most recent
    # turn boundary (UserPromptSubmit `touch`es the state file at turn
    # start; pre-turn calls are stale).
    turn_start = state.get("turn_started_mtime") or 0.0
    fresh_calls = [c for c in state.get("calls", []) if c.get("ts", 0) >= turn_start]

    # BURST suppression — same logic as cumulative.py. If the recent
    # calls arrived in a tight time window (e.g., a single agent firing 4
    # tool calls in one message), don't block on cumulative shape — the
    # agent didn't have a chance to see prior nudges yet.
    if _is_parallel_burst(fresh_calls):
        return None

    if len(fresh_calls) < HARD_THRESHOLD:
        return None

    # Escalation-ladder gate: the block backstops the HARD nudge, it is NOT
    # an independent threshold. If HARD did not fire (cascade_shape didn't
    # hold OR soft hadn't fired earlier this turn), there is no warning to
    # ignore — blocking would surprise the agent with no preceding signal,
    # which is the abandonment-inducing failure mode that motivated the
    # softening edits in cumulative.py:_build_nudge / _decide_nudge.
    # Coupling note: if the post-hook crashed and didn't write nudges_fired,
    # the count check above would also short-circuit — failure modes correlate.
    fired = set(state.get("nudges_fired_this_turn") or [])
    if "cascade_hard" not in fired:
        return None

    return _build_block_message(len(fresh_calls), fresh_calls)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    block_msg = process(input_data)
    if block_msg:
        sys.stderr.write(block_msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
