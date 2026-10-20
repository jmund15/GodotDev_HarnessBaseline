#!/usr/bin/env python3
"""Commit-detection proof for hooks/tres_nullstrip_guard.py — shared cases in _commit_guard_cases.py.

    python3 .claude/tests/test_tres_nullstrip_guard_commit_detection.py
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
import tres_nullstrip_guard as guard  # noqa: E402


def deny_text_proof():
    payload = {"tool_name": "Bash", "cwd": os.getcwd(), "tool_input": {"command": "git commit -F msg"}}
    output = io.StringIO()
    with patch.object(guard, "find_strips", return_value=([("Hub/hub.tscn", "CameraHeight")], [], [])), \
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
          and "Set the explicit value" in reason)
    print("%-4s null-strip deny cites Godot file rules and names the explicit value fix"
          % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def _removal_merge(resolution_removes):
    """A repo stopped mid-merge on a scene both parents changed. `resolution_removes=False`: the
    incoming parent deleted the authored `MaxSlots = 12` and the merge carries that deletion.
    True: neither parent deleted it; the merge resolution itself does."""
    from _merge_guard_cases import _git, _init_repo, _install_hooks, _write
    pad = "".join('\n[node name="Pad%d" type="Node" parent="."]\n' % i for i in range(4))
    scene = '[gd_scene format=3]\n\n[node name="Inv" type="Node"]\nMaxSlots = %s\n' + pad + 'Extra = %s\n' + pad + 'Other = %s\n'
    repo = _init_repo()
    _write(repo, {"Game/Inv.cs": "public partial class Inv\n{\n    [Export] public int MaxSlots { get; set; } = 24;\n}\n",
                  "Game/inv.tscn": scene % (12, 1, 1)})
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", "base"])
    _git(repo, ["checkout", "-q", "-b", "upstream"])
    upstream = scene % (12, 1, 2) if resolution_removes else scene.replace("MaxSlots = %s\n", "") % (1, 2)
    _write(repo, {"Game/inv.tscn": upstream})
    _git(repo, ["commit", "-q", "-am", "upstream"])
    _git(repo, ["checkout", "-q", "-"])
    _git(repo, ["checkout", "-q", "-b", "work"])
    _write(repo, {"Game/inv.tscn": scene % (12, 2, 1)})
    _git(repo, ["commit", "-q", "-am", "work"])
    _git(repo, ["merge", "--no-commit", "--no-ff", "upstream"], check=False)
    if resolution_removes:
        _write(repo, {"Game/inv.tscn": scene.replace("MaxSlots = %s\n", "") % (2, 2)})
        _git(repo, ["add", "-A"])
    _install_hooks(repo, "tres_nullstrip_guard.py")
    return repo


def incoming_removal_proof():
    from _merge_guard_cases import _run
    fails = 0
    for resolution_removes, expected, label in (
            (False, "allow", "a merge carrying the incoming parent's own removal of an authored value is allowed"),
            (True, "deny", "a merge resolution that removes an authored value neither parent removed is denied")):
        repo = _removal_merge(resolution_removes)
        verdict, reason = _run(repo, "tres_nullstrip_guard.py", "git commit -q -F m")
        ok = verdict == expected
        fails += not ok
        print("%-4s %s%s" % ("ok" if ok else "FAIL", label, "" if ok else " (got %s: %s)" % (verdict, reason[-200:])))
    return 1 if fails else 0


def blob_read_proof():
    """One hook run reads each (ref, path) blob once, however many findings name the file."""
    repo = _removal_merge(True)
    rows = lambda tag: [("Game/inv.tscn", "%s%d" % (tag, i), "1") for i in range(4)]
    shows = []
    real = guard.run_git

    def counting(args, cwd, env=None):
        if args and args[0] == "show":
            shows.append(args[1])
        return real(args, cwd, env)

    payload = {"tool_name": "Bash", "cwd": repo, "tool_input": {"command": "git commit -q -F m"}}
    previous = os.getcwd()
    try:
        with patch.object(guard, "find_strips", return_value=(rows("N"), rows("L"), rows("U"))), \
                patch.object(guard, "report"), patch.object(guard, "run_git", counting), \
                patch.object(guard.sys, "stdin", io.StringIO(json.dumps(payload))), \
                patch.object(guard.sys, "stdout", io.StringIO()):
            guard.hook()
    finally:
        os.chdir(previous)
    ok = bool(shows) and len(shows) == len(set(shows))
    print("%-4s a hook run reads each staged/incoming blob once across 12 findings on one file (%d reads, %d distinct)"
          % ("ok" if ok else "FAIL", len(shows), len(set(shows))))
    return 0 if ok else 1


if __name__ == "__main__":
    result = (run_for("tres_nullstrip_guard.py") or deny_text_proof() or incoming_removal_proof()
              or blob_read_proof())
    sys.exit(result or merge_run_for("tres_nullstrip_guard.py"))
