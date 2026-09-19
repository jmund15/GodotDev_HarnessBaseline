#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit turn-state companion for routing advisories.

The count-based cumulative nudge is retired. This hook remains the single owner
of the last real user prompt and per-turn advisory receipts consumed by the C#
Grep and vault-write routing checks.

What it does:
- Starts a new turn and clears per-turn advisory receipts.
- Stores `last_prompt` only for a real user request. Runtime notifications still
  start turns but never replace the user intent used by later tool hooks.
- Stale-sweep: deletes any state files older than 24h (no per-call cost).

Boundaries:
- Never blocks. Exit 0 in all paths.
- Shared updates use salvage, a per-file lock, and atomic replacement; failures stay silent.

Wired in: settings.json hooks.UserPromptSubmit.
"""

import json
import os
import sys
import time

from _hook_state import state_path, update_json_locked
from _prompt_provenance import is_user_intent_prompt

STALE_AGE_SECONDS = 24 * 3600  # 24 hours


def _state_path(session_id: str) -> str:
    return state_path(session_id)


def _bump_turn(session_id: str, prompt: str) -> None:
    """Clear per-turn receipts and retain only the latest real user prompt."""
    path = _state_path(session_id)
    user_intent = is_user_intent_prompt(prompt)

    def update(state):
        state["post_grep_nudges_fired_this_turn"] = []
        state["pre_nudges_fired_this_turn"] = []
        if user_intent:
            state["last_prompt"] = prompt

    update_json_locked(path, update)


def _stale_sweep() -> None:
    """Delete state files older than STALE_AGE_SECONDS. Non-fatal on errors."""
    state_dir = os.path.dirname(_state_path(""))
    try:
        if not os.path.isdir(state_dir):
            return
        cutoff = time.time() - STALE_AGE_SECONDS
        for name in os.listdir(state_dir):
            # Only sweep our state files; leave hook_fire_log.jsonl + tempfiles alone
            if not name.endswith(".json") or name == "hook_fire_log.jsonl":
                continue
            if name.startswith(".tmp_"):
                continue
            full = os.path.join(state_dir, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.unlink(full)
            except OSError:
                continue
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(input_data, dict):
        sys.exit(0)
    session_id = input_data.get("session_id") or ""
    prompt = input_data.get("prompt") or ""
    _bump_turn(session_id, prompt)
    _stale_sweep()
    sys.exit(0)


if __name__ == "__main__":
    main()
