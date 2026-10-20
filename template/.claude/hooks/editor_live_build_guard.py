#!/usr/bin/env python3
"""
Hook: PreToolUse floor guard - no agent build into a checkout whose Godot editor or playtest is live.

A checkout's editor loads the assemblies in its `.godot/mono/temp/bin/`. A build there (`dotnet
build|test|run|publish|msbuild`, or a test/gate runner told `-IgnoreEditor`) rewrites them under it; on the
next Play the editor reloads them and can crash, taking the attached game with it. The runners route a live
checkout's run to its twin worktree (`scripts/TwinCheckout.ps1`) and refuse only when the twin cannot be synced;
this guard closes the two ways around that route. Instruction it backs: `skills/testing/reference/running.md`'s
entry-guard paragraph.

Matches executing invocations only; mentions in quotes, comments and heredoc bodies pass. The target checkout
is a runner's `-Repo` value, a dotnet project or solution path's checkout, or the working directory after
any `cd`. Liveness is `Get-LiveEditor` from `scripts/GodotProcess.ps1`, skipped when no Godot process runs.
`HARNESS_LIVE_EDITOR_CHECKOUTS` (os.pathsep-separated roots) replaces the probe in proofs. On Windows a probe
that fails denies, matching the runners' own fail-closed posture. Off Windows the probe's tools (tasklist, pwsh)
do not exist and the only host is a headless cloud VM with no editor, so the guard allows. Class: floor.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "scripts"))
RUNNERS = ("run_test_suite.ps1", "regression_gate.ps1", "run_integration_batched.ps1", "verify.ps1")
DOTNET_BUILDS = {"build", "test", "run", "publish", "msbuild"}


def _root(path):
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return os.path.normcase(os.path.normpath(path))
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _live_roots_override():
    raw = os.environ.get("HARNESS_LIVE_EDITOR_CHECKOUTS")
    if raw is None:
        return None
    return {r for r in (_root(p) for p in raw.split(os.pathsep) if p) if r}


def _godot_running():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return "godot" in out.lower()


def _is_live(root):
    override = _live_roots_override()
    if override is not None:
        return root in override
    if sys.platform != "win32":
        return False
    if not _godot_running():
        return False
    script = os.path.join(SCRIPTS, "GodotProcess.ps1").replace("'", "''")
    probe = f". '{script}'; @(Get-LiveEditor -Checkout '{root}').Count"
    try:
        out = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", probe],
                             capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
        return int(out[-1]) > 0
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return True


def _targets(command, cwd):
    """Yield (checkout_root, what) for each executing build into a checkout."""
    from _command_text import executable_spans, command_words, executable_word
    here = cwd
    for _at, _end, segment in executable_spans(command):
        parsed = command_words(segment)
        if parsed is None:
            continue
        source_words, _ = parsed
        head = executable_word(source_words, segment)
        words = [w.value for w in source_words[head:]]
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            here = words[1] if os.path.isabs(words[1]) else os.path.join(here, words[1])
            continue
        if words[0] == "dotnet" and len(words) > 1 and words[1] in DOTNET_BUILDS:
            project = next((w for w in words[2:] if w.endswith((".csproj", ".sln"))), None)
            base = project if project and os.path.isabs(project) else os.path.join(here, project or ".")
            yield _root(base), "dotnet " + words[1]
            continue
        runner = next((w for w in words if os.path.basename(w.replace("\\", "/")) in RUNNERS), None)
        if runner and any(w.lower().split(":", 1)[0] == "-ignoreeditor" for w in words):
            repo = next((words[i + 1] for i, w in enumerate(words[:-1]) if w.lower() == "-repo"), None)
            yield _root(repo if repo else here), os.path.basename(runner.replace("\\", "/")) + " -IgnoreEditor"


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("{}")
        return 0
    if payload.get("tool_name") != "Bash":
        print("{}")
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    cwd = payload.get("cwd") or os.getcwd()
    for root, what in _targets(command, cwd):
        if root and _is_live(root):
            retry = ("run it without -IgnoreEditor: the runner sends it to the checkout's twin. -IgnoreEditor is only "
                     "for an editor process that is already dead"
                     if what.endswith("-IgnoreEditor") else
                     "run the tests through run_test_suite.ps1, verify.ps1 or the gate, which build in the checkout's twin")
            sys.stderr.write(
                f"BLOCKED {what} -- a Godot editor or playtest is live on {root}; this build would rewrite the "
                f"assemblies it has loaded (skills/testing/reference/running.md, entry guard). Next: {retry}.\n")
            return 2
    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
