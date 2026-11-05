#!/usr/bin/env python3
"""A forced recursive delete of a literal `.claude/scratch/` path holding no git repository is a no-op
or a regenerable entry, so the guard allows it instead of denying it.

The general rule is the same one: a scratch entry holding no git repository and no registered worktree
loses nothing git holds, and the guard reads no reply (whether the owner asked is the auto-mode
classifier's to judge).

The proof plants a temp project root (`CLAUDE_PROJECT_DIR`) so its fixtures never land in the shared
`.claude/scratch/`. Arms: allowed (absent literal, once-assigned variable, `cd`-relative, quoted,
nested-absent, absolute, an existing non-git entry, `rm -r` without force, a PowerShell shape) and still
blocked (a glob, an unresolved variable, `..` escape, scratch itself, a worktree path, a path outside
scratch, an entry holding a git repository, another session's id folder, a symlink escape), plus the
hook's real stdin channel, where an exit outside {0, 2} or a traceback is a CRASH, never an allow.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # the repo's .claude directory
HOOKS = os.path.join(ROOT, "hooks")
sys.path.insert(0, HOOKS)

import dangerous_shell_guard as dsg  # noqa: E402

# Planted INSIDE the repo's gitignored `.claude/tmp`, uniquely named per run. A root under this checkout's
# `.claude/scratch` makes every target a delete inside the scratch of a linked worktree (the land runner's
# checkout is one), which the guard allows for a different reason. A root under the guard's temp directory
# makes every target a temp-dir delete, which it also allows, and the runner's checkout lives under the
# system temp directory: so the guard runs with its temp directory (TEMP/TMP/TMPDIR, and this process's
# `tempfile`) pointed at an empty dir outside the planted root, wherever this checkout lives.
SCRATCH_HOME = os.path.join(ROOT, "tmp")
os.makedirs(SCRATCH_HOME, exist_ok=True)
TMP = tempfile.mkdtemp(prefix="pe_absent_delete_", dir=SCRATCH_HOME)
OUTSIDE = tempfile.mkdtemp(prefix="pe_absent_outside_", dir=SCRATCH_HOME)
GUARD_TEMP = tempfile.mkdtemp(prefix="pe_absent_guardtemp_", dir=SCRATCH_HOME)
for _var in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_var] = GUARD_TEMP
tempfile.tempdir = GUARD_TEMP
os.makedirs(os.path.join(TMP, ".claude", "scratch"), exist_ok=True)
PROJECT_SCRATCH = os.path.join(TMP, ".claude", "scratch")
EVIDENCE = os.path.join(PROJECT_SCRATCH, "evidence_tree")
os.makedirs(EVIDENCE, exist_ok=True)
with open(os.path.join(EVIDENCE, "run.json"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("{}\n")
CLONE = os.path.join(PROJECT_SCRATCH, "probe")
os.makedirs(CLONE, exist_ok=True)
subprocess.run(["git", "init", "-q", CLONE], capture_output=True)
PEER = os.path.join(PROJECT_SCRATCH, "0f0e0d0c-1b1a-2918-1716-151413121110", "out")
os.makedirs(PEER, exist_ok=True)
with open(os.path.join(PEER, "job.log"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("peer output\n")

os.environ["CLAUDE_PROJECT_DIR"] = TMP


def blocked(cmd, cwd=None):
    return dsg.check_bash_command(cmd, cwd or TMP)[0]


TMP_FWD = TMP.replace("\\", "/")

# A literal scratch target that holds no git repository is allowed, though every one names a delete.
ALLOWED = [
    # F6402's own setup idiom, once-assigned variable and leading `cd` included.
    'cd "%s" && T=.claude/scratch/explore_t1key4/applytest && rm -rf $T && mkdir -p $T' % TMP_FWD,
    "rm -rf .claude/scratch/applytest",
    # The parent is absent too: a nonexistent path under a nonexistent path is equally nothing.
    "rm -rf .claude/scratch/absent/deeper",
    'rm -rf ".claude/scratch/applytest"',
    "rm -rf -f -r .claude/scratch/applytest",
    "cd .claude/scratch && rm -rf applytest",
    # Absolute literal target, still inside this project's scratch.
    "rm -rf %s/.claude/scratch/applytest" % TMP_FWD,
    # An existing entry: it holds no git repository, so nothing git holds is lost.
    "rm -rf .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/evidence_tree/run.json",
    # No force flag: the guard judges the target, never the flag's spelling.
    "rm -r .claude/scratch/applytest",
    "rm --recursive .claude/scratch/applytest",
    # One removable target beside another does not make the delete lossy.
    "rm -rf .claude/scratch/applytest .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/applytest && rm -rf .claude/scratch/evidence_tree",
    "rm -rf .claude/scratch/applytest; rm -rf .claude/scratch/evidence_tree",
    # A PowerShell recursive delete is judged by the same target rule.
    "Remove-Item -Recurse -Force .claude/scratch/applytest",
]

BLOCKED = [
    # Wildcards and unresolved expansions are not one resolvable path.
    "rm -rf .claude/scratch/absent*",
    "rm -rf .claude/scratch/absent?",
    "rm -rf $UNSET/x",
    "rm -rf ${UNSET}/x",
    # Escapes: `..`, a path outside this project's scratch, and scratch itself.
    "rm -rf .claude/scratch/../applytest",
    "rm -rf .claude/scratch",
    "rm -rf .claude/worktrees/absent",
    "rm -rf Assets/absent",
    # A scratch entry holding a git repository may hold commits on no remote.
    "rm -rf .claude/scratch/probe",
    # A folder another session's id names is that session's output.
    "rm -rf .claude/scratch/0f0e0d0c-1b1a-2918-1716-151413121110",
]

SYMLINK = os.path.join(PROJECT_SCRATCH, "escape")
symlink_made = False
try:
    os.symlink(OUTSIDE, SYMLINK, target_is_directory=True)
    symlink_made = True
except (OSError, NotImplementedError, AttributeError):
    pass
if symlink_made:
    BLOCKED.append("rm -rf .claude/scratch/escape/x")

fails = 0
print("dangerous_shell_guard — a literal scratch delete holding no git repository")
for cmd in ALLOWED:
    if blocked(cmd):
        print(f"  FAIL must-allow blocked: {cmd} :: {dsg.check_bash_command(cmd, TMP)[1][:200]}")
        fails += 1
    else:
        print(f"  ok   allow: {cmd}")

for cmd in BLOCKED:
    if not blocked(cmd):
        print(f"  FAIL must-block allowed: {cmd}")
        fails += 1
    else:
        print(f"  ok   block: {cmd}")


def channel(cmd, cwd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "dsg-absent-proof",
               "hook_event_name": "PreToolUse", "cwd": cwd}
    proc = subprocess.run([sys.executable, os.path.join(HOOKS, "dangerous_shell_guard.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": TMP})
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc, None
    if proc.returncode == 2:
        return "deny", proc, None
    try:
        hso = (json.loads(proc.stdout or "{}") or {}).get("hookSpecificOutput") or {}
    except ValueError:
        return "CRASH", proc, None
    return ("rewrite" if hso.get("updatedInput") else "allow"), proc, hso.get("updatedInput")


CHANNEL = [
    ('cd "%s" && T=.claude/scratch/explore_t1key4/applytest && rm -rf $T && mkdir -p $T' % TMP_FWD, "allow"),
    ("rm -rf .claude/scratch/evidence_tree", "allow"),
    ("rm -rf .claude/scratch/absent*", "deny"),
    ("ls .claude", "allow"),
]
for cmd, want in CHANNEL:
    got, proc, updated = channel(cmd, TMP)
    ok = got == want and updated is None
    if not ok:
        print(f"  FAIL channel {cmd!r}: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:300]!r}")
        fails += 1
    else:
        print(f"  ok   channel {want}: {cmd}")

# A delegate in a LINKED worktree removes its own scratch file: that worktree's scratch counts, not
# only the main checkout's. The hard conditions stay: a git repository inside, an unregistered checkout.
def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                   capture_output=True, check=True)


def _plant(path, rel, text="x\n"):
    full = os.path.join(path, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


MAIN = os.path.join(TMP, "main_repo")
os.makedirs(MAIN)
_git("init", "-q", "-b", "main", cwd=MAIN)
_plant(MAIN, "README.md")
_git("add", "-A", cwd=MAIN)
_git("commit", "-q", "-m", "init", cwd=MAIN)
LINKED = os.path.join(TMP, "linked_wt")
_git("worktree", "add", "-q", "--detach", LINKED, cwd=MAIN)
STRAY = os.path.join(TMP, "stray_checkout")
os.makedirs(STRAY)
_git("init", "-q", "-b", "main", cwd=STRAY)
for tree in (LINKED, STRAY):
    _plant(tree, ".claude/scratch/fan_dbg.json")
    _plant(tree, ".claude/scratch/dir_with_repo/.keep")
    _git("init", "-q", cwd=os.path.join(tree, ".claude", "scratch", "dir_with_repo"))

WORKTREE_CASES = [
    ("a linked worktree's own scratch file", LINKED, "rm -rf .claude/scratch/fan_dbg.json", False),
    ("a linked worktree's scratch file, absolute", LINKED,
     "rm -rf %s/.claude/scratch/fan_dbg.json" % LINKED.replace("\\", "/"), False),
    ("a linked worktree's scratch entry holding a git repository", LINKED,
     "rm -rf .claude/scratch/dir_with_repo", True),
    ("an unregistered checkout's scratch file", STRAY, "rm -rf .claude/scratch/fan_dbg.json", True),
    ("a linked worktree's scratch itself", LINKED, "rm -rf .claude/scratch", True),
    ("a linked worktree's tracked file", LINKED, "rm -rf README.md", True),
]
os.environ["CLAUDE_PROJECT_DIR"] = MAIN
for label, cwd, cmd, want_block in WORKTREE_CASES:
    got = dsg.check_bash_command(cmd, cwd)[0]
    if bool(got) != want_block:
        print(f"  FAIL {label}: expected {'block' if want_block else 'allow'}, got "
              f"{'block' if got else 'allow'} :: {dsg.check_bash_command(cmd, cwd)[1][:200]}")
        fails += 1
    else:
        print(f"  ok   {'block' if want_block else 'allow'}: {label}")
os.environ["CLAUDE_PROJECT_DIR"] = TMP

total = len(ALLOWED) + len(BLOCKED) + len(CHANNEL) + len(WORKTREE_CASES)
print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
def _force_remove(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


for _dir in (TMP, OUTSIDE, GUARD_TEMP):
    shutil.rmtree(_dir, onerror=_force_remove)
sys.exit(1 if fails else 0)
