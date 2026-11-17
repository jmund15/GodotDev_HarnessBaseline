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
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # this checkout's `.claude`: the hooks under test load from here
sys.path.insert(0, os.path.join(ROOT, "hooks"))

# The project the guard protects is a repository planted in temp: the guard asks git about its cwd (`--show-toplevel`,
# `worktree list`, `--git-common-dir`), so those answers come from here, never from this checkout's HEAD and worktrees.
# The guard treats anything under the system temp directory as disposable, so the proof's own "system temp" is a sibling
# folder (`tempfile.tempdir`, and TEMP/TMP/TMPDIR for the hook child): the project sits outside it, as a checkout does.
_PLANT = tempfile.mkdtemp(prefix="dsg_cache_")
PROJECT = os.path.join(_PLANT, "project")
GUARD_TEMP = os.path.join(_PLANT, "systemtemp")
os.makedirs(os.path.join(PROJECT, ".claude"))
os.makedirs(GUARD_TEMP)
tempfile.tempdir = GUARD_TEMP
CHILD_TEMP = {"TEMP": GUARD_TEMP, "TMP": GUARD_TEMP, "TMPDIR": GUARD_TEMP}
atexit.register(lambda: (os.chdir(HERE), shutil.rmtree(_PLANT, ignore_errors=True)))
with open(os.path.join(PROJECT, ".claude", "README.md"), "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write("seed\n")
for _args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"]):
    subprocess.run(["git", *_args], cwd=PROJECT, check=True, capture_output=True)
os.environ["CLAUDE_PROJECT_DIR"] = PROJECT
os.chdir(PROJECT)

import dangerous_shell_guard as dsg  # noqa: E402

# An absolute path outside this repo on the running platform: `C:/...` is relative on POSIX,
# where a cache segment under it would be an in-repo cache the rule rightly admits.
OTHER_CHECKOUT = "C:/any/other/checkout" if os.name == "nt" else "/any/other/checkout"
OTHER_HOME = "C:/Users/someone" if os.name == "nt" else "/home/someone"

MUST_PASS = [
    "rm -rf .claude/.search-index",
    "rm -rf .claude/auto-memory/.search-index .claude/hooks/.search-index Tests/.search-index",
    # The owner's cleanup names the project's own cache by absolute path; built from this checkout,
    # so the case holds in any clone, worktree or machine.
    "rm -rf " + PROJECT.replace("\\", "/") + "/.claude/.search-index",
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
    # `.claude/tmp` holds the owner's loose files beside the folders a proof plants there under its own
    # unique names (`test_dangerous_shell_guard_absent_target.py`); only those planted folders are exhaust.
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq",
    "rm -rf .claude/tmp/pe_absent_delete_o_kj1zsx .claude/tmp/pe_absent_outside_fcmdrh_4",
    "rm -rf .claude/tmp/pe_absent_guardtemp_9itfqwag/",
    "rm -rf " + PROJECT.replace("\\", "/") + "/.claude/tmp/pe_absent_delete_689z3vmv",
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
    "rm -rf .claude/worktrees/audio_system",
    "rm -rf .claude",
    "rm -rf .",
    # chained second delete riding along
    "rm -rf .search-index && rm -rf Assets",
    # absolute targets outside this repo, even with a cache segment (defect 2)
    "rm -rf " + OTHER_CHECKOUT + "/.search-index",
    "rm -rf /elsewhere/__pycache__",
    "rm -rf " + OTHER_HOME + "/.claude/.cache",
    'rm -rf "' + OTHER_CHECKOUT + '/.search-index"',
    # quoting must not let a real target, variable or chained delete ride along
    'rm -rf ".search-index" Assets',
    'rm -rf "$HOME/.search-index"',
    'rm -rf ".search-index" && rm -rf Assets',
    'rm -rf ".search-index;rm -rf Assets"',
    # `.claude/tmp` itself and the owner's loose files there (transcripts, drafts) are not exhaust
    "rm -rf .claude/tmp",
    "rm -rf .claude/tmp/procgen_video_script_draft.md",
    "rm -rf .claude/tmp/DestructibleTerrainUsingTilesets_YouTubeTranscript.txt",
    "rm -rf .claude/tmp/notes_dir",
    # a look-alike name, a glob, a traversal out of a planted folder, a planted folder beside a real file
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq*",
    "rm -rf .claude/tmp/pe_absent_delete",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq0",
    "rm -rf .claude/tmp/xpe_absent_delete_dxf68feq",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq/../procgen_video_script_draft.md",
    "rm -rf .claude/tmp/pe_absent_delete_dxf68feq .claude/tmp/dump_stack.txt",
    "rm -rf " + OTHER_CHECKOUT + "/.claude/tmp/pe_absent_delete_dxf68feq",
]


def blocked(cmd):
    return dsg.check_bash_command(cmd)[0]


def channel(cmd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "dsg-cache-proof",
               "hook_event_name": "PreToolUse", "cwd": PROJECT}
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "pre_bash_dispatch.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, **CHILD_TEMP, "CLAUDE_PROJECT_DIR": PROJECT}, cwd=PROJECT)
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc
    denied = proc.returncode == 2 or '"deny"' in proc.stdout or "BLOCKED" in (proc.stdout + proc.stderr)
    return ("deny" if denied else "allow"), proc


fails = 0
print("dangerous_shell_guard — regenerable-cache recursive delete")
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

# A planted folder is exhaust by its name only while it holds no git repository at any depth: a repository
# inside one may hold commits no remote has, so that delete is judged like any other.
_PLANTED_REPO = os.path.join(PROJECT, ".claude", "tmp", "pe_absent_repo_abcdefgh")
_PLANTED_FILES = os.path.join(PROJECT, ".claude", "tmp", "pe_absent_files_abcdefgh")
os.makedirs(os.path.join(_PLANTED_REPO, "repo"))
os.makedirs(os.path.join(_PLANTED_FILES, "sub"))
with open(os.path.join(_PLANTED_FILES, "sub", "fixture.txt"), "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write("a proof's own fixture\n")
for _args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "only here"]):
    subprocess.run(["git", *_args], cwd=os.path.join(_PLANTED_REPO, "repo"), check=True, capture_output=True)
for cmd, want_blocked in (("rm -rf .claude/tmp/pe_absent_repo_abcdefgh", True),
                          ("rm -rf .claude/tmp/pe_absent_repo_abcdefgh/repo", True),
                          ("rm -rf .claude/tmp/pe_absent_files_abcdefgh", False),
                          ("rm -rf .claude/tmp/pe_absent_files_abcdefgh/sub", False)):
    if blocked(cmd) != want_blocked:
        print(f"  FAIL planted folder {'allowed' if want_blocked else 'blocked'}: {cmd}")
        fails += 1
    else:
        print(f"  ok   planted folder {'block' if want_blocked else 'pass'}: {cmd}")

# A scratch folder named by this session's full id is this session's own output (2026-10-09: the guard
# blocked deleting .claude/scratch/session_end/<own id>/p1/inputs, a folder the close had just written).
SID = "b48600e4-2ced-4055-895c-18867ef93b99"
OTHER = "11111111-2222-3333-4444-555555555555"
transcript = os.path.join(tempfile.mkdtemp(prefix="pe_scratch_"), SID + ".jsonl")
REPO = PROJECT
OWN_PASS = ["rm -rf .claude/scratch/session_end/%s/p1/inputs" % SID, "rm -rf .claude/scratch/%s" % SID]
OWN_BLOCK = [("rm -rf .claude/scratch/session_end/%s/p1" % OTHER, transcript),
             ("rm -rf .claude/scratch/session_end/%s/p1" % SID, None),
             ("rm -rf .claude/scratch/session_end/%s/../.." % SID, transcript),
             ("rm -rf .claude/scratch/x%s" % SID, transcript),
             ("rm -rf .claude/%s" % SID, transcript),
             ("rm -rf .claude/scratch/%s Assets" % SID, transcript)]
for cmd in OWN_PASS:
    if dsg.check_bash_command(cmd, REPO, transcript)[0]:
        print(f"  FAIL own-scratch must-pass blocked: {cmd}")
        fails += 1
    else:
        print(f"  ok   own-scratch pass: {cmd}")
for cmd, tp in OWN_BLOCK:
    if not dsg.check_bash_command(cmd, REPO, tp)[0]:
        print(f"  FAIL own-scratch must-block allowed: {cmd} (transcript {tp is not None})")
        fails += 1
    else:
        print(f"  ok   own-scratch block: {cmd}")

# A target outside every `.claude/scratch` never asks git for the cwd's toplevel: that call only widens the scratch bases.
from unittest import mock  # noqa: E402
real_run, toplevel_calls = subprocess.run, []


def watch(cmd, *args, **kwargs):
    if isinstance(cmd, list) and "--show-toplevel" in cmd:
        toplevel_calls.append(cmd)
    return real_run(cmd, *args, **kwargs)


with mock.patch.object(subprocess, "run", watch):
    for cmd in ("rm -rf .claude/.cache/../..", "rm -rf Assets", "rm -rf .claude/worktrees/audio_system"):
        dsg.check_bash_command(cmd, REPO)
if toplevel_calls:
    print(f"  FAIL a target outside scratch spawned git rev-parse --show-toplevel {len(toplevel_calls)} times")
    fails += 1
else:
    print("  ok   no toplevel spawn for a target outside scratch")

# One command's targets share one `git worktree list`: the answer cannot change while the guard decides.
planted_root = tempfile.mkdtemp(prefix="pe_worktrees_")
for name in ("a", "b", "c"):
    os.makedirs(os.path.join(planted_root, ".claude", "scratch", name))
    with open(os.path.join(planted_root, ".claude", "scratch", name, "output.txt"), "w", encoding="utf-8") as fh:
        fh.write("a study's output")  # an empty folder is a leftover, judged without a worktree list
worktree_calls = []


def watch_worktrees(cmd, *args, **kwargs):
    if isinstance(cmd, list) and "worktree" in cmd and "list" in cmd:
        worktree_calls.append(cmd)
    return real_run(cmd, *args, **kwargs)


saved_project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
os.environ["CLAUDE_PROJECT_DIR"] = planted_root
try:
    with mock.patch.object(subprocess, "run", watch_worktrees):
        dsg.check_bash_command("rm -rf .claude/scratch/a .claude/scratch/b .claude/scratch/c", planted_root)
finally:
    if saved_project_dir is None:
        del os.environ["CLAUDE_PROJECT_DIR"]
    else:
        os.environ["CLAUDE_PROJECT_DIR"] = saved_project_dir
if len(worktree_calls) != 1:
    print(f"  FAIL three scratch targets in one command listed worktrees {len(worktree_calls)} times, not once")
    fails += 1
else:
    print("  ok   three scratch targets share one worktree list")

total = len(MUST_PASS) + len(MUST_BLOCK) + 2 + len(OWN_PASS) + len(OWN_BLOCK) + 2
print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
sys.exit(1 if fails else 0)
