#!/usr/bin/env python3
"""The `[Tool]` cascade check must fire for a production `.cs` reached through a
worktree checkout, and must still skip the harness's own and vendored trees.

Violation samples are assembled from fragments: this proof is not an exempt
fixture path, so a literal pattern in its own source would block edits to it.
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


@case("a violation written to a proof file's own path is blocked, not exempt")
def _proof_path_not_exempt():
    content = "class Fixture { void Go() { " + "GD" + ".Print(1); } }"
    result = _run_live({"tool_name": "Write", "tool_input": {
        "file_path": os.path.join(HERE, "ProofFixture.cs"),
        "content": content}})
    return (result is not None and result.returncode == 2
            and "Use JmoLogger" in result.stderr)


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


@case("the live PreToolUse Write channel applies [GlobalClass, Tool]")
def _live_positive():
    result = _run_live({
        "hook_event_name": "PreToolUse",
        "session_id": "pattern-live",
        "tool_name": "Write",
        "tool_input": {"file_path": "C:/repo/Spells/Tuning/BogusResource.cs",
                       "content": MISSING_TOOL},
    })
    if result is None or result.returncode != 0 or result.stderr:
        return False
    out = json.loads(result.stdout)["hookSpecificOutput"]
    return (out.get("permissionDecision") is None
            and out["updatedInput"]["content"] == HAS_TOOL)


@case("GD.Print is blocked on the live channel with csharp rule and logger next step")
def _gd_print():
    result = _run_live({"tool_name": "Write", "tool_input": {
        "file_path": "C:/repo/Spells/Bad.cs",
        "content": "class Bad { void Go() { " + "GD" + ".Print(1); } }"}})
    return (result is not None and result.returncode == 2
            and "rules/csharp_patterns.md" in result.stderr
            and "Use JmoLogger" in result.stderr and result.stdout.strip() == "")


@case("GD.PrintErr is blocked on the live channel like GD.Print")
def _gd_print_err():
    result = _run_live({"tool_name": "Write", "tool_input": {
        "file_path": "C:/repo/Spells/Bad.cs",
        "content": "class Bad { void Go() { " + "GD" + ".PrintErr(1); } }"}})
    return (result is not None and result.returncode == 2
            and "rules/csharp_patterns.md" in result.stderr
            and "Use JmoLogger" in result.stderr and result.stdout.strip() == "")


@case("RequiredExport is applied on the live channel")
def _required_export():
    result = _run_live({"tool_name": "Write", "tool_input": {
        "file_path": "C:/repo/Spells/Bad.cs",
        "content": ("class Bad { [Export] public string Name "
                    + "= " + "null!" + "; }")}})
    if result is None or result.returncode != 0 or result.stderr:
        return False
    out = json.loads(result.stdout)["hookSpecificOutput"]
    return (out.get("permissionDecision") is None
            and "[Export, RequiredExport]" in out["updatedInput"]["content"])


@case("GetNode in _Process advises through the live channel")
def _getnode_process():
    result = _run_live({"tool_name": "Write", "tool_input": {
        "file_path": "C:/repo/Spells/Bad.cs",
        "content": "public override void _Process(double delta) { "
                   + "Get" + "Node<Node>(\"X\"); }"}})
    if result is None or result.returncode != 0 or result.stderr:
        return False
    out = json.loads(result.stdout)["hookSpecificOutput"]
    return (out.get("permissionDecision") is None
            and "each frame on a stable path" in out["additionalContext"])


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
