#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse on Write|Edit — surface the design litmus at INTRODUCTION time.

Why:
- `rules/design_litmus.md` says "answer before introducing, not after review",
  but it auto-loads on .cs *reads* — the wrong moment. The three shapes below
  are the ones the litmus most often catches too late: a behavior bool that is
  a strategy slot in disguise, a neutered strategy/config seam, and a subclass
  on an orthogonal axis.

What it does:
- Fires only on a .cs file_path whose written text matches one of three narrow
  regexes (see SHAPES). Emits at most 3 lines of additionalContext.
- One fire per file per session (shared ~/.claude/.routing_state/<sid>.json,
  same convention as critical_analysis_reminder / plan_memory_reminder).

Boundaries:
- Advisory only. Never blocks, never exits 2, fails open on any internal error.
"""

import json
import os
import re
import sys

from _hook_state import state_path, update_json_locked

FILES_FIELD = "design_surface_reminder_files"
FILES_CAP = 100  # bounded state — oldest entries drop off

# (shape_label, litmus_number, pattern)
SHAPES = [
    (
        "an [Export] bool",
        1,
        # An exported behavior flag. Bounded look-ahead keeps this to the
        # attribute's own statement rather than any bool later in the file.
        re.compile(r"\[Export[^\]]*\][^;{}]{0,80}?\bbool\b"),
    ),
    (
        "a null strategy/config argument",
        1,
        # Named-argument form only — `strategy: null` / `config: null`.
        # Positional nulls are too noisy to detect textually.
        re.compile(r"\b\w*(?:strategy|config)\s*:\s*null\b", re.IGNORECASE),
    ),
    (
        # abstract only: a concrete leaf impl IS the sanctioned family extension (994 of them
        # live in the manifest) — firing on those is crying wolf. Axis-fusing happens in new
        # INTERMEDIATE rungs, which are abstract by shape.
        "a new abstract rung under a strategy-family base",
        2,
        re.compile(
            r"\babstract\s+(?:partial\s+)?class\s+\w+\s*:\s*\w*(?:Effect|Runner|Strategy|Behavior|State)\b"
        ),
    ),
]

LITMUS_TEXT = {
    1: ("name the family that already owns the concern — a behavior bool is a "
        "strategy slot in disguise, and a literal null into a strategy/config "
        "parameter is a neutered seam."),
    2: ("name the axis the base varies on — if the new behavior could co-occur "
        "independently, it composes as a config slot instead of a subclass."),
}


def _state_path(session_id: str) -> str:
    return state_path(session_id)


def _claim_file(session_id: str, file_key: str) -> bool:
    """True the first time this session sees `file_key`. Fail-open on I/O error."""
    def claim(state):
        seen = state.get(FILES_FIELD)
        seen = list(seen) if isinstance(seen, list) else []
        if file_key in seen:
            return False
        seen.append(file_key)
        state[FILES_FIELD] = seen[-FILES_CAP:]
        return True

    written, claimed = update_json_locked(_state_path(session_id), claim)
    return claimed if written else True


def detect_shape(text: str):
    """Return (shape_label, litmus_number) for the first matching shape, else None."""
    for label, litmus_no, pattern in SHAPES:
        if pattern.search(text):
            return label, litmus_no
    return None


def build_reminder(label: str, litmus_no: int) -> str:
    return (
        f"[design-litmus] This edit adds {label}. Before proceeding:\n"
        f".claude/rules/design_litmus.md #{litmus_no} — {LITMUS_TEXT[litmus_no]}\n"
        "Answer it now, or record \"no existing family\" explicitly."
    )


def process(input_data):
    """Dispatcher entry: `{"context": reminder}` on a first-seen design shape, else None.

    `post_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    file_path = str(tool_input.get("file_path", ""))
    if not file_path.lower().endswith(".cs"):
        return None

    text = tool_input.get("content") or tool_input.get("new_string") or ""
    if not text:
        return None

    hit = detect_shape(text)
    if not hit:
        return None

    file_key = file_path.replace("\\", "/")
    if not _claim_file(input_data.get("session_id", "") or "", file_key):
        return None

    return {"context": build_reminder(*hit)}


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    result = process(input_data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": result["context"],
            }
        }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # Advisory hook — never block on an internal error
