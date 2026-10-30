#!/usr/bin/env python3
"""Trigger proof for hooks/tres_script_strip_guard.py's restore/checkout advisory.

The advisory fires only when a `git restore` / `git checkout` names a pathspec that could hold a
.tres: a .tres file, `.`, a directory or a glob. A command whose every named path is a non-.tres
file, a branch switch, or text that merely quotes the command stays silent.

    python3 .claude/tests/test_tres_script_strip_guard_trigger.py
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "tres_script_strip_guard.py")

FIRES = [
    "git checkout -- a.tres",
    "git restore Data/encounter.tres",
    "git checkout HEAD~1 -- Data/encounter.tres",
    "git restore .",
    "git checkout .",
    "git checkout -- Data/",
    "git restore Data",
    "git restore -- '*.tres'",
    "git -C ../other restore --source HEAD -- Data/x.tres",
    "git status && git checkout -- a.tres",
]
SILENT = [
    "git checkout -- f.txt",
    "git checkout -- clean.txt",
    "git restore .claude/hooks/pre_bash_dispatch.py",
    "git restore --staged README.md",
    "git checkout main",
    "git checkout -b feature/x",
    "git checkout -- clean.txt && ls Data/*.tres",
    "echo \"git checkout -- a.tres\"",
    "git status",
]


def advisory(command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "."})
    out = subprocess.run([sys.executable, HOOK, "--hook"], input=payload, capture_output=True,
                         text=True, encoding="utf-8").stdout.strip()
    ctx = json.loads(out or "{}").get("hookSpecificOutput", {}).get("additionalContext")
    return ctx


def main():
    failures = []
    text = None
    for command in FIRES:
        ctx = advisory(command)
        if not ctx:
            failures.append(f"expected advisory, got silence: {command}")
        else:
            text = ctx
    for command in SILENT:
        if advisory(command):
            failures.append(f"expected silence, got advisory: {command}")
    if text is not None:
        if "--worktree --repair-inplace" not in text:
            failures.append("advisory lost the repair command")
        if len(text.encode("utf-8")) > 200:
            failures.append(f"advisory is {len(text.encode('utf-8'))} B, over 200 B")
    import contextlib
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location('strip_proof', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    findings = [('Data/probe.tres', 1, 0)]
    module.find_strips = lambda *_a, **_k: findings
    for name, stream, action in [('report', 'stderr', lambda: module.report(findings)),
                                 ('worktree', 'stdout', lambda: module.worktree_scan(False))]:
        captured = io.StringIO()
        with getattr(contextlib, 'redirect_' + stream)(captured):
            action()
        message = captured.getvalue()
        if '--repair-inplace' not in message or 'git checkout' in message or 'git restore' in message:
            failures.append(name + ' must name only repair-inplace')
    for failure in failures:
        print("FAIL", failure)
    print(f"{'FAIL' if failures else 'OK'}: {len(FIRES) + len(SILENT)} trigger cases + text check, "
          f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
