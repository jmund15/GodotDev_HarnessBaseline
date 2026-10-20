#!/usr/bin/env python3
"""Commit-detection proof for hooks/tres_script_strip_guard.py — shared cases in _commit_guard_cases.py.

    python3 .claude/tests/test_tres_script_strip_guard_commit_detection.py
"""
import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from _commit_guard_cases import run_for  # noqa: E402
from _merge_guard_cases import merge_run_for  # noqa: E402
import tres_script_strip_guard as guard  # noqa: E402


def deny_text_proof():
    payload = {"tool_name": "Bash", "cwd": os.getcwd(), "tool_input": {"command": "git commit -F msg"}}
    output = io.StringIO()
    with patch.object(guard, "find_strips", return_value=[("Data/a.tres", 1, 0)]), \
            patch.object(guard, "report"), \
            patch.object(guard.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(guard.sys, "stdout", output):
        exit_code = guard.hook()
    try:
        result = json.loads(output.getvalue())["hookSpecificOutput"]
        reason = result["permissionDecisionReason"]
        decision = result["permissionDecision"]
    except (KeyError, ValueError):
        reason, decision = "", ""
    ok = (exit_code == 0 and decision == "deny" and "rules/godot_files.md" in reason
          and "Restore the binding" in reason)
    print("%-4s script-strip deny cites Godot file rules and names the restore step"
          % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    result = run_for("tres_script_strip_guard.py") or deny_text_proof()
    sys.exit(result or merge_run_for("tres_script_strip_guard.py"))
