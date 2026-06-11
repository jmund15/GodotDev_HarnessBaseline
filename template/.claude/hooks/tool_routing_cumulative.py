#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse cumulative counter — catches the death-by-thousand-cuts pattern.

Why:
- The PreToolUse `tool_routing_nudge.py` matches per-call only and doesn't see
  `Read` or `Glob`. The 2026-05-03 routing-compliance battery surfaced 6 failures
  (A1, A2, G2, H1, H2, J2) where agents made 5-21 small reads/searches without
  bundling into one `mcp__ai-worker__read_files(paths=[...])`. The PreToolUse hook
  is structurally incapable of catching that shape — only a turn-aware
  cumulative counter can.

What it does:
- Increments a per-session JSON state file on every matched tool call.
- Computes diversity signals (distinct target dirs, single-tool streak).
- Emits an `additionalContext` nudge via PostToolUse JSON output when the
  cumulative shape matches synthesis territory (count + low-dir-diversity OR
  count + high-tool-streak).
- Per-turn dedupe via `nudges_fired_this_turn` — anti-fatigue.

Output channel:
- PostToolUse `hookSpecificOutput.additionalContext` (proven to land in the
  tool-result frame even in subagent contexts — `plan_memory_reminder.py`
  precedent). Stderr fallback only for state-file write failures (visible
  failure beats silent degradation).

Turn boundaries:
- Mtime-based, race-safe: `tool_routing_cumulative_reset.py` (UserPromptSubmit
  companion) `touch`es the state file at turn start. This hook treats any
  `calls` entries with `ts < turn_started_mtime` as stale and resets.

State file:
- ~/.claude/.routing_state/<session_id_short>.json
- Atomic write via tempfile+rename. Stale-sweep runs in the UPS companion.

Wiring:
- settings.json hooks.PostToolUse with matcher
  "Read|Grep|Glob|mcp__obsidian__obsidian_read_note|
   mcp__obsidian__obsidian_global_search|mcp__plugin_semantic-search_semantic-search__search"
"""

import json
import os
import sys
import tempfile
import time

# --- Tunables ------------------------------------------------------------

# Per-turn cumulative thresholds. Soft = informational; hard = stronger nudge.
SOFT_THRESHOLD = 4
HARD_THRESHOLD = 7

# Diversity gates for the soft nudge. The threshold-cross alone is not enough;
# we want to avoid nudging on legitimate exploratory triangulation (e.g., 5
# reads spanning 5 different module dirs). Either gate alone is sufficient.
LOW_DIVERSITY_DIR_LIMIT = 2          # ≤2 distinct dirs = focused investigation
HIGH_STREAK_THRESHOLD = 3             # ≥3 same-tool-name in a row = cascading

# Parallel-burst suppression — historical context: as of 2026-05-03, Claude
# Code's hook stdin did NOT expose per-subagent identification, so parallel
# subagents' calls accumulated to the parent's state file. Workaround:
# 3-second window detection.
#
# UPDATE: Claude Code now exposes `agent_id` and `agent_type` in the hook
# stdin JSON when the call originates from a subagent context. State files
# are now keyed by (session_id, agent_id), so cross-subagent collision is
# structurally prevented. BURST suppression is retained as belt-and-
# suspenders for rapid-fire same-agent bursts (e.g., a single agent firing
# 4 calls in one message), but it's no longer the primary collision defense.
BURST_WINDOW_SEC = 3.0
BURST_CALL_THRESHOLD = 4

# Audit-exception cue words — per CLAUDE.md §9 "Exception" clause. When the
# user explicitly frames the task as audit/debug/security-review/fact-check
# at the source-code level, the cumulative-cascade rule does NOT apply.
# Centralized in routing_classifier (2026-05-04 extraction); imported here
# for back-compat with existing call sites that reference AUDIT_INTENT_CUES.
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from routing_classifier import AUDIT_INTENT_CUES  # noqa: F401
except ImportError:
    # Defensive fallback — preserves behavior if classifier module is missing.
    AUDIT_INTENT_CUES = (
        "audit", "code review", "security review", "debug this", "debugging this",
        "step through", "trace through", "fact-check", "fact check",
        "line by line", "line-by-line", "inspect the code", "inspect this file",
        "verify the implementation", "review for bugs", "review for issues",
    )

# Cap stored call list length to bound state file size.
MAX_CALLS_RETAINED = 50

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


# --- State ---------------------------------------------------------------

def _state_path(session_id: str, agent_id: str = "") -> str:
    """
    Per-(session, agent) state file path. When agent_id is non-empty (subagent
    context), the file is keyed `<sid>_<aid>.json`. Parent context falls back
    to `<sid>.json` (no agent_id present in hook stdin). This isolation is the
    structural fix that allows the PreToolUse block companion to safely deny
    sibling-subagent cascades without false positives.
    """
    sid_short = (session_id[:8] if session_id else "default")
    if agent_id:
        aid_short = agent_id[:8]
        return os.path.join(STATE_DIR, f"{sid_short}_{aid_short}.json")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_state(session_id: str, agent_id: str = "") -> dict:
    path = _state_path(session_id, agent_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _fresh_state()
        data.setdefault("turn_started_mtime", 0.0)
        data.setdefault("calls", [])
        data.setdefault("nudges_fired_this_turn", [])
        return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return _fresh_state()


def _fresh_state() -> dict:
    return {
        "turn_started_mtime": time.time(),
        "calls": [],
        "nudges_fired_this_turn": [],
    }


def _write_state_atomic(session_id: str, state: dict, agent_id: str = "") -> bool:
    """
    Atomic write: tempfile in same dir, rename over target. Returns True on
    success. False on failure — caller emits the visible-failure stderr line.
    """
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = _state_path(session_id, agent_id)
        # tempfile in same dir so rename is atomic on POSIX/NTFS
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=STATE_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=True)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception as e:
        sys.stderr.write(
            f"[tool-routing-cumulative] state write failed: {e!r}; "
            "counter disabled this call\n"
        )
        return False


# --- Per-tool target extraction ------------------------------------------

def _extract_target(tool_name: str, tool_input: dict) -> str:
    """
    Pull a stable identifier for the call target — used to compute distinct
    target dirs. For path-bearing tools, return file_path. For search tools,
    return a query-derived label (no dir, intentionally — search tools collapse
    into one synthetic 'search' bucket for diversity counting).
    """
    if not isinstance(tool_input, dict):
        return ""
    if tool_name == "Read":
        return str(tool_input.get("file_path") or "")
    if tool_name == "Grep":
        glob = tool_input.get("glob") or ""
        path = tool_input.get("path") or ""
        pattern = (tool_input.get("pattern") or "")[:40]
        return f"GREP:{path}:{glob}:{pattern}"
    if tool_name == "Glob":
        path = tool_input.get("path") or ""
        pattern = tool_input.get("pattern") or ""
        return f"GLOB:{path}:{pattern}"
    if tool_name == "mcp__obsidian__obsidian_read_note":
        return str(tool_input.get("filePath") or "")
    if tool_name in (
        "mcp__obsidian__obsidian_global_search",
        "mcp__plugin_semantic-search_semantic-search__search",
    ):
        return f"SEARCH:{tool_name}:{(tool_input.get('query') or '')[:40]}"
    return ""


def _target_dir(target: str) -> str:
    """
    Bucket for diversity counting:
      - File-path targets → dirname (the directory)
      - GREP / GLOB / SEARCH synthetic targets → a constant bucket per category,
        because we want them to count as 'one kind of activity' rather than
        being inflated to N distinct dirs by query variation.
    """
    if not target:
        return ""
    if target.startswith("GREP:"):
        return "<grep-bucket>"
    if target.startswith("GLOB:"):
        return "<glob-bucket>"
    if target.startswith("SEARCH:"):
        return "<search-bucket>"
    # File path — take dirname. Normalize slashes so Windows/POSIX match.
    norm = target.replace("\\", "/")
    return os.path.dirname(norm).rstrip("/")


# --- Threshold logic -----------------------------------------------------

def _classify(calls: list) -> dict:
    """
    Returns counts/signals over the calls list. All signals are computed only
    on the most-recent run of activity (stale entries already filtered upstream).
    """
    count = len(calls)
    if count == 0:
        return {"count": 0, "distinct_dirs": 0, "tail_streak": 0, "tail_tool": ""}

    distinct_dirs = len({_target_dir(c.get("target") or "") for c in calls})

    # Single-tool streak at the tail of the list.
    tail_tool = calls[-1].get("tool", "")
    tail_streak = 1
    for c in reversed(calls[:-1]):
        if c.get("tool") == tail_tool:
            tail_streak += 1
        else:
            break

    return {
        "count": count,
        "distinct_dirs": distinct_dirs,
        "tail_streak": tail_streak,
        "tail_tool": tail_tool,
    }


def _build_nudge(level: str, signals: dict, calls: list) -> str:
    """
    Compose the nudge text. Different shape for soft vs hard so the model can
    distinguish "informational" from "you should stop and bundle now".
    """
    count = signals["count"]
    if level == "soft":
        return (
            f"[tool-routing] {count} reads/searches this turn "
            f"({signals['distinct_dirs']} dirs, "
            f"{signals['tail_streak']}× `{signals['tail_tool']}`). "
            "If synthesizing, bundle next into "
            "`mcp__ai-worker__read_files(paths=[...], question=...)`. "
            "CLAUDE.md §9."
        )
    # Hard nudge — explicit target list, stronger framing.
    targets = []
    for c in calls[-min(10, len(calls)):]:
        t = c.get("target") or ""
        if t:
            if t.startswith(("GREP:", "GLOB:", "SEARCH:")):
                t = t.split(":", 2)[-1][:40]
            targets.append(t)
    target_list = "; ".join(targets) if targets else "(targets unavailable)"
    return (
        f"[tool-routing] {count} reads/searches this turn — synthesis shape. "
        "**Reroute next call** to "
        "`mcp__ai-worker__read_files(paths=[<accumulated>], question=<...>)`. "
        f"Targets: {target_list}. "
        "Per-query recovery only (don't redo what's done); reroute next. "
        "CLAUDE.md §9."
    )


def _is_parallel_burst(calls: list) -> bool:
    """
    Heuristic: if the last BURST_CALL_THRESHOLD calls arrived within
    BURST_WINDOW_SEC seconds of each other, this looks like a parallel-
    subagent burst (each subagent fires calls inside the same parent session).
    Suppress cumulative nudges in this case — the count doesn't reflect any
    one subagent's actual cascade behavior. Returns False when count is too
    small to judge.
    """
    if len(calls) < BURST_CALL_THRESHOLD:
        return False
    window = calls[-BURST_CALL_THRESHOLD:]
    span = window[-1].get("ts", 0) - window[0].get("ts", 0)
    return span < BURST_WINDOW_SEC


def _prompt_implies_audit(state: dict) -> bool:
    """
    True when the user's prompt explicitly framed the task as audit / debug /
    security-review / fact-check at the source-code level — per CLAUDE.md §9
    Exception clause. Audit work needs frontier-model engagement with primary
    source; cumulative-cascade nudges to "bundle into read_files" would push
    toward a cheap-model summary that can silently miss the bug.
    """
    prompt = (state.get("last_prompt") or "").lower()
    if not prompt:
        return False
    return any(cue in prompt for cue in AUDIT_INTENT_CUES)


def _decide_nudge(state: dict) -> tuple[str | None, str | None]:
    """
    Returns (nudge_id, nudge_text) — nudge_id is what gets stored in
    nudges_fired_this_turn for dedupe. Returns (None, None) when no nudge.
    Architectural note (Phase 1 v2 finding): when parallel-burst is detected,
    the cumulative count aggregates N subagents and nudging is misleading —
    suppress (no signal beats wrong signal). Pre-injection at Agent dispatch
    time is the structural answer for parallel-subagent routing compliance.
    """
    calls = state.get("calls", [])
    fired = set(state.get("nudges_fired_this_turn", []))

    # Parallel-burst suppression — no per-subagent identifier in hook stdin
    # makes accurate per-subagent attribution impossible; suppress the false
    # signal rather than misattribute. State-tracked counter for visibility.
    if _is_parallel_burst(calls):
        state["parallel_burst_suppressions"] = state.get("parallel_burst_suppressions", 0) + 1
        return (None, None)

    # Audit-exception suppression — per CLAUDE.md §9 Exception clause. When
    # the user explicitly framed the task as audit/debug, the agent needs to
    # READ the source, not summarize it. Suppress the "bundle into read_files"
    # nudge that would push toward a cheap-model summary.
    if _prompt_implies_audit(state):
        state["audit_exception_suppressions"] = state.get("audit_exception_suppressions", 0) + 1
        return (None, None)

    signals = _classify(calls)
    focused = signals["distinct_dirs"] <= LOW_DIVERSITY_DIR_LIMIT
    cascading = signals["tail_streak"] >= HIGH_STREAK_THRESHOLD
    cascade_shape = focused or cascading

    # Hard nudge — gated on (soft already fired this turn) AND (cascade-shape
    # diversity signals still hold). Rationale: HARD's intended job is "you saw
    # the soft nudge and kept cascading anyway," not "you happened to make 7
    # reads." Without these gates, legitimate diverse exploration across 7
    # modules trips the same nudge as a tight cascade — false-positive shape
    # that invites premature task abandonment (see brainstorm 2026-05-07).
    # Once fired, suppress all further cumulative nudges this turn.
    if (
        signals["count"] >= HARD_THRESHOLD
        and "cascade_hard" not in fired
        and "cascade_soft" in fired
        and cascade_shape
    ):
        return ("cascade_hard", _build_nudge("hard", signals, calls))

    # Soft nudge — diversity-gated, fires once per turn.
    if signals["count"] >= SOFT_THRESHOLD and "cascade_soft" not in fired and "cascade_hard" not in fired:
        if cascade_shape:
            return ("cascade_soft", _build_nudge("soft", signals, calls))

    return (None, None)


# --- Dispatch ------------------------------------------------------------

def process(input_data: dict) -> str | None:
    """In-process entry — counts the call, returns nudge text or None.
    Called by post_read_dispatch.py (writer-first in the post chain);
    main() wraps it for standalone settings.json wiring."""
    tool_name = input_data.get("tool_name") or ""
    tool_input = input_data.get("tool_input") or {}
    session_id = input_data.get("session_id") or ""
    # agent_id is present only when the call originates inside a subagent
    # context (post-2026-05-03 schema). Empty string in parent context.
    agent_id = input_data.get("agent_id") or ""

    if not tool_name:
        return None

    state = _read_state(session_id, agent_id)

    # Stale-call filter: any entry with ts < turn_started_mtime is from a
    # previous turn (UPS companion `touch`es the state file at turn start).
    turn_start = state.get("turn_started_mtime") or 0.0
    state["calls"] = [c for c in state.get("calls", []) if c.get("ts", 0) >= turn_start]

    # Append this call.
    target = _extract_target(tool_name, tool_input)
    state["calls"].append({"tool": tool_name, "target": target, "ts": time.time()})

    # Cap retained calls.
    if len(state["calls"]) > MAX_CALLS_RETAINED:
        state["calls"] = state["calls"][-MAX_CALLS_RETAINED:]

    # Decide nudge BEFORE writing — so nudges_fired_this_turn reflects the
    # decision atomically.
    nudge_id, nudge_text = _decide_nudge(state)
    if nudge_id is not None:
        if nudge_id not in state["nudges_fired_this_turn"]:
            state["nudges_fired_this_turn"].append(nudge_id)

    if not _write_state_atomic(session_id, state, agent_id):
        # State write failed — visible-failure line already emitted in
        # _write_state_atomic; skip the nudge.
        return None

    return nudge_text


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    nudge_text = process(input_data)
    if nudge_text:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": nudge_text,
            }
        }
        sys.stdout.write(json.dumps(payload))

    sys.exit(0)


if __name__ == "__main__":
    main()
