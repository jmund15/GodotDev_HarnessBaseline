#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — Remind to load `instruction_quality` before authoring harness files.

Why:
- The skill's description already says "ALWAYS load when reviewing, refining, or
  authoring claude code harness files". The observed failure (2026-07-19) was not
  mis-triggering but NON-consultation: a "small markdown edit" was pattern-matched
  straight to Edit, so no skill was ever weighed. A description can't fix that —
  a trigger you don't consult can't be sharpened into firing. Interception can.
- Fires at temptation-time (the Write/Edit call itself), which is the only point
  where the target path is knowable. UserPromptSubmit hooks (prompt_memory_loader)
  fire too early to see a path; PostToolUse fires too late to inform authoring.

What it does:
- Gates on the tool's file_path: only .claude/ harness surfaces (see PATH RULES).
- DENIES the edit (permissionDecision) until `instruction_quality` appears in the
  session's `skills_loaded` state, written by skill_load_marker.py (PostToolUse on
  Skill). Escalated from advisory: the advisory channel was observed being ignored.
- Once loaded: emits the hookSpecificOutput.additionalContext gates-summary
  advisory. Per the verified channel matrix, additionalContext is the ONLY
  model-visible advisory channel on PreToolUse.
- Dedupes the advisory per session so a multi-edit harness session pays it once.

PATH RULES (in .claude/ only):
- IN:  **/*.md (CLAUDE.md, skills, commands, rules), hooks/*.py, settings*.json
- OUT: auto-memory/** — governed by consolidate-memory / memory_audit, not this
       skill (see instruction_quality "Composition with other tools").
- OUT: scratch/**, tests/**, __pycache__/** — not loaded guidance.
- IN (carve-out): pending_harness_edits.md — queued CLAUDE.md/MEMORY.md
  content awaiting /apply_harness_edits; it IS loaded guidance in transit, and
  authoring errors there land verbatim in the injected files.

Boundaries:
- The deny path is deterministic (harness file AND skill not loaded). Unexpected
  errors exit 0 open — a hook bug must not brick every edit; the marker writer's
  failure direction (keep denying) covers the enforcement side.
- Re-fires the advisory after REFIRE_AFTER_SECONDS so a long session that
  compacted away the first advisory gets it again.

Wired in: settings.json hooks.PreToolUse with matcher "Write|Edit".
"""

import json
import os
import sys
import tempfile
import time

from _hook_state import read_json_salvage, write_json_atomic

# Shared with tool_routing_cumulative.py / critical_analysis_reminder.py
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

    # Queued injected-file content is harness guidance in transit, so the gate
    # covers it even though it is not under a governed subdirectory. Lived in
    # scratch/ until 2026-08-16; moved to .claude/ root when scratch was ignored.
    if tail == "pending_harness_edits.md":
        return True

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
        f"Editing harness file `.claude/{name}` — `instruction_quality` is loaded (verified); "
        "apply it to THIS edit. The trigger is the FILE CLASS, not the edit size; "
        '"it\'s a small change" is not an exemption.\n'
        "\n"
        "Gates most often missed:\n"
        "• §3 SSOT — don't restate a rule that has a canonical home elsewhere; cross-reference it.\n"
        "• §4 Cross-reference durability — mechanically verify every cited path/anchor/skill resolves.\n"
        "• §6 Conciseness — cut rationale that doesn't change a runtime decision.\n"
        "• §7 Description-as-trigger (skills) — logical scope, not a keyword list.\n"
        "• §13-16 (hooks) — channel validity, registration, bounded state, fail posture."
    )


_ROUTING_STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


def _note_edit_for_routing(session_id: str) -> None:
    """Set `edit_seen_this_turn` in the tool-routing session state, consumed by
    tool_routing_cumulative.py. Cleared per turn by
    tool_routing_cumulative_reset.py. Best-effort; never raises."""
    path = os.path.join(
        _ROUTING_STATE_DIR, f"{session_id[:8] if session_id else 'default'}.json"
    )
    try:
        state = read_json_salvage(path)
        state["edit_seen_this_turn"] = True
        write_json_atomic(path, state)
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    session_id = input_data.get("session_id") or ""
    # Side effect, all Write|Edit calls: mark the turn as edit-shaped for the
    # tool-routing cascade nudge. Reads that satisfy Edit's read-first
    # precondition are not a bundleable synthesis cascade, and prompt cue words
    # miss the case where the user says "address this" and means "edit it".
    # Piggybacks this hook's existing spawn rather than adding another.
    _note_edit_for_routing(session_id)

    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    if not is_harness_file(file_path):
        sys.exit(0)

    state = _read_state(session_id)

    skills_loaded = state.get("skills_loaded")
    if not (isinstance(skills_loaded, list) and "instruction_quality" in skills_loaded):
        name = file_path.replace("\\", "/").split("/.claude/")[-1]
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Harness edit to `.claude/{name}` DENIED: `instruction_quality` is not in this "
                    "session's loaded-skill state. Invoke Skill(skill=\"instruction_quality\"), then "
                    "retry this exact edit — only a real Skill invocation clears the gate; prior "
                    "familiarity or a compaction summary does not."
                ),
            }
        }))
        sys.exit(0)
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
