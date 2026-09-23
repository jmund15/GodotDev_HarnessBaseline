#!/usr/bin/env python3
"""Compaction-scoped proof that the current session fully loaded the model ladder."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import state_path, update_json_locked  # noqa: E402

STATE_KEY = "model_ladder_ready"
RELATIVE_PATH = Path(".claude") / "reference" / "model_ladder_evidence.md"


def _root(payload):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()


def expected_path(payload):
    """The absolute ladder path a Read must hit; denials print it so a worktree cwd cannot misresolve it."""
    return os.path.realpath(_root(payload if isinstance(payload, dict) else {}) / RELATIVE_PATH)


def _read_succeeded(payload):
    response = payload.get("tool_response")
    if isinstance(response, dict) and response.get("is_error"):
        return False
    result = payload.get("tool_result")
    if isinstance(result, dict) and result.get("is_error"):
        return False
    return True


def mark_loaded(payload):
    """Arm readiness only for a successful, unbounded Read of the exact ladder path."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not _read_succeeded(payload):
        return False
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return False
    raw_path = tool_input.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    root = _root(payload)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        actual = os.path.normcase(os.path.realpath(candidate))
        expected = os.path.normcase(expected_path(payload))
    except OSError:
        return False
    if actual != expected:
        return False

    def update(state):
        state[STATE_KEY] = True
        return True

    written, marked = update_json_locked(state_path(payload.get("session_id") or ""), update)
    return bool(written and marked)


def claim_loaded(session_id):
    """True while the ladder is in context; compaction clears it. Unreadable state denies."""
    def update(state):
        return state.get(STATE_KEY) is True

    written, ready = update_json_locked(state_path(session_id or ""), update)
    return bool(written and ready)
