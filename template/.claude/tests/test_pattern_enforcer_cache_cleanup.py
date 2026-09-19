#!/usr/bin/env python3
"""A recursive delete of a REGENERABLE CACHE passes; every other recursive delete stays blocked.

Owner direction 2026-09-14: the guard exists because agents have deleted what they should not; it
must not hand justified cleanup back to the owner. The session that prompted this left four stray
semantic-search indexes on disk (3.4 GB under `.claude/.search-index`) because `rm -rf` of a
regenerable cache was blocked like any other delete.

Allowlisted classes, matched by PATH SEGMENT (never substring): `.claude/.cache`, `.claude/logs`,
`.search-index`, `__pycache__`, `.pytest_cache`. A target qualifies only when it sits at or under one
of those segments with no `..`, glob, `$` or `~` in it, and every target in the command qualifies.

Arms: MUST_PASS (the owner's actual cleanup plus cache shapes), MUST_BLOCK (traversal, prefix look-
alikes, globs, variables, mixed targets, evidence and checkout folders), and the real
pre_bash_dispatch.py channel for one planted pass and one planted block, where an exit outside
{0, 2} or a traceback is a CRASH, never an allow.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import pattern_enforcer as pe  # noqa: E402

MUST_PASS = [
    "rm -rf .claude/.search-index",
    "rm -rf .claude/auto-memory/.search-index .claude/hooks/.search-index Tests/.search-index",
    # The owner's cleanup names the project's own cache by absolute path; built from this checkout,
    # so the case holds in any clone, worktree or machine.
    "rm -rf " + os.path.dirname(ROOT).replace("\\", "/") + "/.claude/.search-index",
    "rm -r .search-index/",
    "rm -rf .claude/hooks/__pycache__",
    "rm -rf .pytest_cache",
    "rm -rf .claude/.cache/baseline-repo",
    "rm -rf .claude/logs/transcript_backups",
    "Remove-Item -Recurse -Force .claude/hooks/.search-index",
    # quoted literal targets are the same paths (defect 1: blanked quotes denied them)
    'rm -rf ".search-index"',
    "rm -rf '.claude/.cache'",
    'rm -rf "Tests/.search-index" "Tests/__pycache__"',
]

MUST_BLOCK = [
    # traversal out of an allowlisted segment
    "rm -rf .claude/.cache/../..",
    "rm -rf .search-index/../.claude",
    "rm -rf .claude/logs/../../Assets",
    # look-alike names: substring, prefix and suffix tricks
    "rm -rf .claude/logs-old",
    "rm -rf .search-index-backup",
    "rm -rf my.search-index",
    "rm -rf .claude/.cachex",
    # globs, variables, home
    "rm -rf */.search-index",
    "rm -rf .search-index*",
    "rm -rf $HOME/.search-index",
    "rm -rf ~/.search-index",
    # one cache target plus one real target
    "rm -rf .search-index Assets",
    # evidence and checkouts are not caches
    "rm -rf .claude/scratch",
    "rm -rf .claude/scratch/harness-finish-3a",
    "rm -rf .claude/worktrees/audio_system",
    "rm -rf .claude",
    "rm -rf .",
    # chained second delete riding along
    "rm -rf .search-index && rm -rf Assets",
    # absolute targets outside this repo, even with a cache segment (defect 2)
    "rm -rf C:/any/other/checkout/.search-index",
    "rm -rf /elsewhere/__pycache__",
    "rm -rf C:/Users/someone/.claude/.cache",
    'rm -rf "C:/any/other/checkout/.search-index"',
    # quoting must not let a real target, variable or chained delete ride along
    'rm -rf ".search-index" Assets',
    'rm -rf "$HOME/.search-index"',
    'rm -rf ".search-index" && rm -rf Assets',
    'rm -rf ".search-index;rm -rf Assets"',
]


def blocked(cmd):
    return pe.check_bash_command(cmd)[0]


def channel(cmd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "pe-cache-proof",
               "hook_event_name": "PreToolUse", "cwd": ROOT}
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "pre_bash_dispatch.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": os.path.dirname(ROOT)})
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc
    denied = proc.returncode == 2 or '"deny"' in proc.stdout or "BLOCKED" in (proc.stdout + proc.stderr)
    return ("deny" if denied else "allow"), proc


fails = 0
print("pattern_enforcer — regenerable-cache recursive delete")
for cmd in MUST_PASS:
    if blocked(cmd):
        print(f"  FAIL must-pass blocked: {cmd}")
        fails += 1
    else:
        print(f"  ok   pass: {cmd}")
for cmd in MUST_BLOCK:
    if not blocked(cmd):
        print(f"  FAIL must-block allowed: {cmd}")
        fails += 1
    else:
        print(f"  ok   block: {cmd}")

for cmd, want in (("rm -rf Tests/.search-index", "allow"), ("rm -rf .claude/.cache/../..", "deny")):
    got, proc = channel(cmd)
    if got != want:
        print(f"  FAIL channel {cmd!r}: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:200]!r}")
        fails += 1
    else:
        print(f"  ok   channel {want}: {cmd}")

total = len(MUST_PASS) + len(MUST_BLOCK) + 2
print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
sys.exit(1 if fails else 0)
