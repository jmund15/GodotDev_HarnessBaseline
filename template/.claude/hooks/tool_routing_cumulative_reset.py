#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit companion to tool_routing_cumulative.py.

Why:
- The cumulative counter needs a clear turn boundary. UserPromptSubmit fires
  before any tool call of the new turn — perfect place to bump
  `turn_started_mtime` in the per-session state file.

What it does:
- For the current session, sets turn_started_mtime = now() and clears
  nudges_fired_this_turn. Leaves calls list intact (the cumulative hook will
  filter stale entries on next call via the mtime gate).
- Stale-sweep: deletes any state files older than 24h (no per-call cost).

Boundaries:
- Never blocks. Exit 0 in all paths.
- Silent on failure (companion of cumulative hook, which has its own
  visible-failure path).

Wired in: settings.json hooks.UserPromptSubmit.
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
STALE_AGE_SECONDS = 24 * 3600  # 24 hours


def _state_path(session_id: str) -> str:
    sid_short = (session_id[:8] if session_id else "default")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _bump_turn(session_id: str, prompt: str) -> None:
    """
    Reset turn_started_mtime + clear nudges_fired_this_turn for this session.
    Also stash the prompt text so PostToolUse hooks can read it for cue-word
    suppression (e.g., tool_routing_post_grep.py needs to know if the user
    explicitly requested a literal/verbatim scan).
    """
    path = _state_path(session_id)
    now = time.time()
    state: dict = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
    except Exception:
        state = {}
    state["turn_started_mtime"] = now
    state["nudges_fired_this_turn"] = []
    state["post_grep_nudges_fired_this_turn"] = []
    state["pre_nudges_fired_this_turn"] = []
    state["last_prompt"] = (prompt or "")[:4000]  # cap; cue-word check needs only first lines
    state.setdefault("calls", [])
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
    except Exception:
        pass


def _stale_sweep() -> None:
    """Delete state files older than STALE_AGE_SECONDS. Non-fatal on errors."""
    try:
        if not os.path.isdir(STATE_DIR):
            return
        cutoff = time.time() - STALE_AGE_SECONDS
        for name in os.listdir(STATE_DIR):
            # Only sweep our state files; leave hook_fire_log.jsonl + tempfiles alone
            if not name.endswith(".json") or name == "hook_fire_log.jsonl":
                continue
            if name.startswith(".tmp_"):
                continue
            full = os.path.join(STATE_DIR, name)
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
    session_id = input_data.get("session_id") or ""
    prompt = input_data.get("prompt") or ""
    _bump_turn(session_id, prompt)
    _stale_sweep()
    sys.exit(0)


if __name__ == "__main__":
    main()
