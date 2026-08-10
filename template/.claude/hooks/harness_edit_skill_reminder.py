#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — Remind to load `instruction_quality` before authoring harness files.

The
`_note_edit_for_routing()` side-effect is dropped here (it fed the code layer's tool-routing-cascade
cluster, which isn't ported here). Gate/dedupe/advisory logic is otherwise unchanged.

Why:
- A description alone doesn't guarantee consultation — a "small markdown edit" gets
  pattern-matched straight to Edit with no skill ever weighed. Interception fixes
  what a description can't: it fires at temptation-time (the Write/Edit call itself),
  the only point where the target path is knowable.

What it does:
- Gates on the tool's file_path: only .claude/ harness surfaces (see PATH RULES).
- Emits a hookSpecificOutput.additionalContext advisory naming the skill and the
  gates most often missed. additionalContext is the ONLY model-visible advisory
  channel on PreToolUse; stderr on an exit-0 path is dead.
- Dedupes per session (with a re-fire window) so a multi-edit harness session pays
  the advisory once, not per file.

PATH RULES (in .claude/ only):
- IN:  **/*.md (CLAUDE.md, skills, commands, rules), hooks/*.py, settings*.json
- OUT: auto-memory/**, scratch/**, tests/**, __pycache__/** — not loaded guidance.

Boundaries:
- Never blocks. Always exits 0. Advisory only — does not verify the skill loaded.
- Re-fires after REFIRE_AFTER_SECONDS so a long session that compacted away the
  first advisory gets it again.

Wired in: settings.json hooks.PreToolUse with matcher "Write|Edit".
"""

import json
import os
import sys
import tempfile
import time

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
STATE_KEY = "harness_edit_reminder_ts"

# A session that ran long enough to compact may have lost the first advisory.
REFIRE_AFTER_SECONDS = 3600

INCLUDED_MD_ANY_DEPTH = ".md"
INCLUDED_EXACT_DIRS = ("hooks",)          # .claude/hooks/*.py
EXCLUDED_DIRS = ("auto-memory", "scratch", "tests", "__pycache__")


def _state_path(session_id: str) -> str:
    sid_short = (session_id[:8] if session_id else "default")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_state(session_id: str) -> dict:
    try:
        with open(_state_path(session_id), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_state_atomic(session_id: str, state: dict) -> None:
    """Atomic write: tempfile in same dir, rename over target. Best-effort."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        path = _state_path(session_id)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=STATE_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp_path, path)
    except OSError:
        pass


def is_harness_file(file_path: str) -> bool:
    """
    True if file_path is a .claude/ surface governed by `instruction_quality`.
    Path separators are normalized — Edit/Write may deliver either on Windows.
    """
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    if "/.claude/" not in norm and not norm.startswith(".claude/"):
        return False

    tail = norm.split("/.claude/")[-1] if "/.claude/" in norm else norm[len(".claude/"):]
    parts = tail.split("/")

    if any(seg in EXCLUDED_DIRS for seg in parts[:-1]):
        return False

    name = parts[-1]
    if name.endswith(INCLUDED_MD_ANY_DEPTH):
        return True
    if name.startswith("settings") and name.endswith(".json"):
        return True
    if name.endswith(".py") and len(parts) > 1 and parts[-2] in INCLUDED_EXACT_DIRS:
        return True
    return False


def build_reminder(file_path: str) -> str:
    name = file_path.replace("\\", "/").split("/.claude/")[-1]
    return (
        f"Editing harness file `.claude/{name}` — load the `instruction_quality` skill "
        "before authoring. The trigger is the FILE CLASS, not the edit size; "
        '"it\'s a small change" is not an exemption.\n'
        "\n"
        "Gates most often missed:\n"
        "• §3 SSOT — don't restate a rule that has a canonical home elsewhere; cross-reference it.\n"
        "• §4 Cross-reference durability — mechanically verify every cited path/anchor/skill resolves.\n"
        "• §6 Conciseness — cut rationale that doesn't change a runtime decision.\n"
        "• §7 Description-as-trigger (skills) — logical scope, not a keyword list.\n"
        "• §13-16 (hooks) — channel validity, registration, bounded state, fail posture."
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    session_id = input_data.get("session_id") or ""
    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    if not is_harness_file(file_path):
        sys.exit(0)

    state = _read_state(session_id)
    now = time.time()
    last = state.get(STATE_KEY, 0)
    if isinstance(last, (int, float)) and (now - last) < REFIRE_AFTER_SECONDS:
        sys.exit(0)

    state[STATE_KEY] = now
    _write_state_atomic(session_id, state)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": build_reminder(file_path),
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory hook: never block a harness edit because the reminder broke.
        sys.exit(0)
