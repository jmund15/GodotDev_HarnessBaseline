#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse routing-audit log — continuous classification of every
tool call against CLAUDE.md §9 routing rules. Persists silent-misses + cue-
exempt overrides to logs/routing_audit.jsonl for /eval_dashboard aggregation.

Why this exists:
- The existing PostToolUse nudge hooks (`tool_routing_post_grep.py`,
  `tool_routing_cumulative.py`) fire real-time advisories but do NOT persist
  per-call classifications. Drift across sessions is invisible until a periodic
  /routing_battery surfaces it. Reddit thread (2026-05-04) load-bearing claim:
  "without the log i would have assumed the rule was working — caught 4-5
  mechanical things a week routing the wrong way."
- This hook fills that gap. It runs after every routing-relevant tool call,
  classifies via routing_classifier.classify_call(), and appends one line per
  NUDGE_WARRANTED or CUE_EXEMPT call to a JSONL audit log.

What gets logged (and what doesn't):
- NUDGE_WARRANTED  : append. The headline silent-miss tier the dashboard surfaces.
- CUE_EXEMPT       : append. Confirms K1/L6/audit-shape carve-outs are firing
                     correctly — counts as "compliant override" in the dashboard.
- COMPLIANT        : skip. Routine routing wins, not interesting.
- ADVISORY_APP.    : skip. Soft-nudge categories (memory_search, broad obsidian
                     search) — too noisy to log every one.
- NOT_ROUTABLE     : skip. Tool has no §9 routing rules.

Each entry also records `nudge_fired: bool` — whether the existing nudge
channel (tool_routing_post_grep.py / tool_routing_nudge.py stderr) actually
reached the agent. Cross-references with the per-session state file's
`post_grep_nudges_fired_this_turn` list. The dashboard can then split:
   silent-miss = NUDGE_WARRANTED + nudge_fired=false   ← the gap to close
   nudged      = NUDGE_WARRANTED + nudge_fired=true    ← channel works

Audit log location:
- logs/routing_audit.jsonl (project-relative; logs/ is gitignored).
- Matches existing convention (logs/transcript_backups/, logs/pre_compact.json).
- Append-only JSONL; one event per line; bounded by /routing_audit aggregate
  rotation (>30 days → logs/routing-audit-archive/YYYY-MM.jsonl).

Boundaries:
- Never blocks. Exit 0 in all paths.
- Never raises (worst case: silent log-write failure, hook still exits clean).
- Reads `last_prompt` from the same per-session state file the other hooks
  populate (~/.claude/.routing_state/<sid>_<aid>.json).

Wired in: settings.json hooks.PostToolUse with the same matcher as
`tool_routing_cumulative.py`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

# Shared classifier (extracted 2026-05-04).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from routing_classifier import classify_call, Classification
except ImportError:
    # Defensive: if import fails, this hook becomes a no-op rather than break
    # the tool call. Tool routing nudges still work via the other hooks.
    sys.exit(0)


# === State + log paths =======================================================

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")

# Project-relative; the cwd at hook-fire time is the project root.
AUDIT_LOG_PATH = "logs/routing_audit.jsonl"

# Cap each log entry's prompt-excerpt to bound JSONL size on long-prompt turns.
PROMPT_EXCERPT_CAP = 400

# Also cap args summary — most tool inputs are small but some (Edit's
# old_string/new_string) can be very large.
ARGS_SUMMARY_CAP = 300


def _state_path(session_id: str, agent_id: str = "") -> str:
    """Per-(session, agent) state file path. Same convention as cumulative.py."""
    sid_short = (session_id[:8] if session_id else "default")
    if agent_id:
        aid_short = agent_id[:8]
        return os.path.join(STATE_DIR, f"{sid_short}_{aid_short}.json")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_state(session_id: str, agent_id: str = "") -> dict:
    try:
        with open(_state_path(session_id, agent_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass
    return {}


# === Nudge-fired detection ==================================================

def _detect_nudge_fired(
    classification: Classification,
    tool_name: str,
    tool_input: dict,
    state: dict,
    session_id: str,
    agent_id: str,
) -> bool:
    """
    Determine whether the existing nudge channel fired for this call.

    Sources of truth:
    - `post_grep_nudges_fired_this_turn` list — written by
      tool_routing_post_grep.py when it emits an additionalContext nudge,
      keyed by Grep pattern. NOTE: post_grep.py writes to the SESSION-LEVEL
      state file (no agent_id suffix), but this hook's `state` arg is loaded
      from the per-(session, agent) file. For subagent Grep calls we must
      also consult the session-level file or every subagent nudge looks
      silent-missed (the +60% pascal-grep-on-cs regression debugged
      2026-05-11 was this measurement bug, not a doctrine drift).
    - For non-Grep nudge-warranted calls (synthesis-shaped Obsidian read), the
      PreToolUse nudge.py emits stderr but does NOT track in state. We can't
      observe it from the post-call vantage point, so we conservatively assume
      the nudge fired — same fire-condition is encoded in classify_call(),
      which already returned NUDGE_WARRANTED.

    Returns False only when we have positive evidence the nudge did NOT fire.
    """
    if classification.severity != "nudge-warranted":
        return False

    if tool_name == "Grep":
        pattern = tool_input.get("pattern") or ""
        fired_a = state.get("post_grep_nudges_fired_this_turn") or []
        if pattern in fired_a:
            return True
        if agent_id:
            sess_state = _read_state(session_id, agent_id="")
            fired_s = sess_state.get("post_grep_nudges_fired_this_turn") or []
            if pattern in fired_s:
                return True
        return False

    # Non-Grep nudge-warranted (synthesis-shaped Read / Obsidian read): the
    # PreToolUse nudge hook records fired rules in the session-level state
    # file (`pre_nudges_fired_this_turn`, keyed by classification rule).
    # Positive evidence only — absence means the nudge did NOT reach the agent.
    rule = classification.rule or ""
    fired_pre = state.get("pre_nudges_fired_this_turn") or []
    if rule in fired_pre:
        return True
    if agent_id:
        sess_state = _read_state(session_id, agent_id="")
        if rule in (sess_state.get("pre_nudges_fired_this_turn") or []):
            return True
    return False


# === Args summary ===========================================================

def _summarize_args(tool_input: dict) -> str:
    """
    Compact one-line summary of tool inputs for the audit log. Truncates
    each value to keep the JSONL line size bounded.
    """
    if not tool_input:
        return ""
    parts = []
    for key in ("pattern", "glob", "type", "filePath", "query", "url", "command"):
        if key in tool_input:
            val = tool_input[key]
            if not isinstance(val, str):
                val = str(val)
            if len(val) > 80:
                val = val[:77] + "..."
            parts.append(f"{key}={val}")
    summary = " ".join(parts)
    if len(summary) > ARGS_SUMMARY_CAP:
        summary = summary[:ARGS_SUMMARY_CAP - 3] + "..."
    return summary


# === Log write ==============================================================

def _append_audit_entry(entry: dict) -> None:
    """
    Best-effort append to the audit JSONL. Failure is silent — this hook
    must NEVER break a tool call.

    Uses an append-mode write rather than read-modify-write to handle the
    parallel-subagent case (two hooks racing on the same file). JSONL's
    line-atomicity guarantees readability even with interleaved appends
    (each line is one self-contained event).
    """
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH) or ".", exist_ok=True)
        line = json.dumps(entry, ensure_ascii=True)
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    except Exception:
        pass


# === Main ===================================================================

def process(input_data: dict) -> None:
    """In-process entry — classifies + logs; emits nothing. Called by
    post_read_dispatch.py; main() wraps it for standalone wiring."""
    tool_name = input_data.get("tool_name") or ""
    tool_input = input_data.get("tool_input") or {}
    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id") or ""

    # Read prompt + nudge-fired state from the per-session file.
    state = _read_state(session_id, agent_id)
    last_prompt = state.get("last_prompt") or ""

    # Classify the call against §9 routing rules.
    try:
        classification = classify_call(tool_name, tool_input, last_prompt)
    except Exception:
        # Classifier should be exception-free, but defensively skip on bug.
        return

    # Only log the interesting tiers.
    if classification.severity not in ("nudge-warranted", "cue-exempt"):
        return

    nudge_fired = _detect_nudge_fired(classification, tool_name, tool_input, state, session_id, agent_id)

    # Excerpt the prompt for context (helps the dashboard explain WHY a
    # call was classified silent-miss without re-loading the full transcript).
    prompt_excerpt = last_prompt[:PROMPT_EXCERPT_CAP] if last_prompt else ""

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session_id[:8] if session_id else "",
        "agent_id": agent_id[:8] if agent_id else "",
        "tool": tool_name,
        "args_summary": _summarize_args(tool_input),
        "classification": classification.severity,
        "rule": classification.rule,
        "reason": classification.reason,
        "nudge_fired": nudge_fired,
        "prompt_excerpt": prompt_excerpt,
    }

    _append_audit_entry(entry)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    process(input_data)
    sys.exit(0)


if __name__ == "__main__":
    main()
