#!/usr/bin/env python3
"""The `[Tool]` cascade check must fire for a production `.cs` reached through a
worktree checkout, and must still skip the harness's own and vendored trees.

The first arm is the positive control: without it, a check that never fires would
also pass. The last arm is the negative that must survive the fix.

    python3 .claude/tests/test_pattern_enforcer_tool_cascade.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS = os.path.join(ROOT, "hooks")
HOOK = os.path.join(HOOKS, "pattern_enforcer.py")
sys.path.insert(0, HOOKS)

import pattern_enforcer as pe  # noqa: E402

MISSING_TOOL = """using Godot;

[GlobalClass]
public partial class BogusResource : Resource
{
    [Export] public int Value { get; set; }
}
"""

HAS_TOOL = MISSING_TOOL.replace("[GlobalClass]", "[GlobalClass, Tool]")

CASES = []


def case(label):
    def wrap(fn):
        CASES.append((label, fn))
        return fn
    return wrap


@case("a production .cs missing [Tool] is blocked")
def _positive():
    blocked, msg = pe.check_tool_cascade(MISSING_TOOL, "C:/repo/Spells/Tuning/BogusResource.cs")
    return blocked and "missing [Tool]" in msg


@case("the same .cs inside a .claude/worktrees checkout is still blocked")
def _worktree_production():
    path = "C:/repo/.claude/worktrees/sync-pr110/Spells/Tuning/BogusResource.cs"
    blocked, msg = pe.check_tool_cascade(MISSING_TOOL, path)
    return blocked and "missing [Tool]" in msg


@case("the checkout's own .claude/ tree is skipped")
def _worktree_harness():
    path = "C:/repo/.claude/worktrees/sync-pr110/.claude/hooks/BogusResource.cs"
    return pe.check_tool_cascade(MISSING_TOOL, path) == (False, "")


@case("vendored and test trees are still skipped")
def _skips():
    for path in ("C:/repo/Jmodot/Implementation/BogusResource.cs",
                 "C:/repo/Tests/Logic/BogusResource.cs",
                 "C:/repo/addons/thing/BogusResource.cs"):
        if pe.check_tool_cascade(MISSING_TOOL, path) != (False, ""):
            return False
    return True


@case("a [Tool]-attributed Resource passes")
def _has_tool():
    return pe.check_tool_cascade(HAS_TOOL, "C:/repo/Spells/Tuning/BogusResource.cs") == (False, "")


def _run_live(payload):
    result = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=60)
    if result.returncode not in (0, 2):
        return None
    return result


@case("the live PreToolUse Write channel blocks the production violation")
def _live_positive():
    result = _run_live({
        "hook_event_name": "PreToolUse",
        "session_id": "pattern-live",
        "tool_name": "Write",
        "tool_input": {"file_path": "C:/repo/Spells/Tuning/BogusResource.cs",
                       "content": MISSING_TOOL},
    })
    return (result is not None and result.returncode == 2
            and "missing [Tool]" in result.stderr and result.stdout.strip() == "")


@case("the live PreToolUse Write channel allows the harness path")
def _live_harness_negative():
    result = _run_live({
        "hook_event_name": "PreToolUse",
        "session_id": "pattern-live-negative",
        "tool_name": "Write",
        "tool_input": {"file_path": "C:/repo/.claude/worktrees/sync-pr110/.claude/hooks/BogusResource.cs",
                       "content": MISSING_TOOL},
    })
    return result is not None and result.returncode == 0 and result.stdout.strip() == "{}" and result.stderr == ""


def main():
    failed = 0
    for label, fn in CASES:
        try:
            ok = fn()
        except Exception as e:  # a crash is not a pass
            ok, label = False, "%s (%r)" % (label, e)
        print("%-5s %s" % ("ok" if ok else "FAIL", label))
        failed += 0 if ok else 1
    print("\n%d/%d cases pass" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
