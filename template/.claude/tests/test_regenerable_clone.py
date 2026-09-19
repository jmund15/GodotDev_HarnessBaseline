#!/usr/bin/env python3
"""`_regenerable_clone.is_regenerable_clone` passes only a clean, fully pushed standalone clone under
`.claude/scratch/`, and fails closed on every other shape. `pattern_enforcer.py` lets a recursive
delete through when every literal target passes it.

Owner direction 2026-09-14: a stray probe clone left in scratch is bloat and must be removable. The
recursive-delete guard keeps scratch blocked because it holds evidence, so the discriminator is
regenerability proven from the clone itself.

Fixture: a bare remote plus clones under `.claude/scratch/_regenerable_clone_fixture_<pid>/`, since
the rule only admits scratch paths. Several sessions run this battery concurrently:
- Startup removes a same-prefix directory only when it is older than STALE_AFTER_S, so another run's
  live fixture is never touched.
- Teardown clears git's read-only object files and asserts that this run's own fixture is gone.
"""
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
PROJECT = os.path.dirname(CLAUDE)
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))

import _regenerable_clone as rc  # noqa: E402

PREFIX = os.path.join(CLAUDE, "scratch", "_regenerable_clone_fixture_")
FIX = PREFIX + str(os.getpid())
STALE_AFTER_S = 1800  # a live run takes well under a minute
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}


def force_rmtree(path):
    """Remove a fixture tree, clearing the read-only bit git sets on object files."""
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=on_error)


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True)


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def fresh_clone(name, remote):
    path = os.path.join(FIX, name)
    git("clone", "-q", remote, path)
    return path


def rel(path):
    return os.path.relpath(path, PROJECT).replace("\\", "/")


def channel(cmd):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "regen-clone-proof",
               "hook_event_name": "PreToolUse", "cwd": PROJECT}
    proc = subprocess.run([sys.executable, os.path.join(CLAUDE, "hooks", "pre_bash_dispatch.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_PROJECT_DIR": PROJECT})
    if proc.returncode not in (0, 2) or "Traceback" in proc.stderr:
        return "CRASH", proc
    denied = proc.returncode == 2 or '"deny"' in proc.stdout or "BLOCKED" in (proc.stdout + proc.stderr)
    return ("deny" if denied else "allow"), proc


fails = 0
total = 0


def expect(label, token, want, cwd=PROJECT):
    global fails, total
    total += 1
    got = rc.is_regenerable_clone(token, cwd, PROJECT)
    if got != want:
        fails += 1
        print(f"  FAIL {label}: expected {want}, got {got} ({token})")
    else:
        print(f"  ok   {label}: {'pass' if want else 'block'}")


def expect_channel(label, cmd, want):
    global fails, total
    total += 1
    got, proc = channel(cmd)
    if got != want:
        fails += 1
        print(f"  FAIL channel {label}: expected {want}, got {got} (exit {proc.returncode}) "
              f"{(proc.stdout + proc.stderr)[:240]!r}")
    else:
        print(f"  ok   channel {label}: {want}")


print("_regenerable_clone — regenerable scratch clone discriminator")
for stale in glob.glob(PREFIX + "*"):
    if stale != FIX and time.time() - os.path.getmtime(stale) > STALE_AFTER_S:
        force_rmtree(stale)
os.makedirs(FIX)
outside = tempfile.mkdtemp(prefix="regen_clone_outside_")
try:
    seed = os.path.join(FIX, "seed")
    os.makedirs(seed)
    git("init", "-q", "-b", "main", cwd=seed)
    write(os.path.join(seed, "a.txt"), "a\n")
    git("add", "a.txt", cwd=seed)
    git("commit", "-q", "-m", "a", cwd=seed)
    remote = os.path.join(FIX, "remote.git")
    git("clone", "-q", "--bare", seed, remote)

    clean = fresh_clone("clean", remote)
    expect("clean clone, relative token", rel(clean), True)
    expect("clean clone, absolute token", clean, True)

    dirty = fresh_clone("dirty", remote)
    write(os.path.join(dirty, "a.txt"), "changed\n")
    expect("modified tracked file", dirty, False)

    untracked = fresh_clone("untracked", remote)
    write(os.path.join(untracked, "new.txt"), "n\n")
    expect("untracked file", untracked, False)

    stashed = fresh_clone("stashed", remote)
    write(os.path.join(stashed, "a.txt"), "stash me\n")
    git("stash", "-q", cwd=stashed)
    expect("non-empty stash", stashed, False)

    ahead = fresh_clone("ahead", remote)
    write(os.path.join(ahead, "b.txt"), "b\n")
    git("add", "b.txt", cwd=ahead)
    git("commit", "-q", "-m", "local only", cwd=ahead)
    expect("local-only commit", ahead, False)

    tagged = fresh_clone("tagged", remote)
    git("tag", "-a", "local-tag", "-m", "t", cwd=tagged)
    expect("tag at a pushed commit", tagged, True)
    write(os.path.join(tagged, "c.txt"), "c\n")
    git("add", "c.txt", cwd=tagged)
    git("commit", "-q", "-m", "tagged local", cwd=tagged)
    git("reset", "-q", "--hard", "HEAD~1", cwd=tagged)
    git("tag", "local-only-tag", "HEAD@{1}", cwd=tagged)
    expect("tag on a local-only commit", tagged, False)

    no_remote = fresh_clone("no_remote", remote)
    git("remote", "remove", "origin", cwd=no_remote)
    expect("clone without a remote", no_remote, False)

    multi = fresh_clone("multi", remote)
    worktree = os.path.join(FIX, "multi_wt")
    git("worktree", "add", "-q", worktree, cwd=multi)
    expect("clone with a second worktree", multi, False)
    expect("worktree checkout (.git file)", worktree, False)

    plain = os.path.join(FIX, "plain_dir")
    os.makedirs(plain)
    expect("plain directory, no .git", plain, False)

    expect("scratch itself", ".claude/scratch", False)
    expect("scratch itself, absolute", os.path.join(CLAUDE, "scratch"), False)
    outside_clone = os.path.join(outside, "clone")
    git("clone", "-q", remote, outside_clone)
    expect("clean clone outside scratch", outside_clone, False)
    expect("traversal token", rel(clean) + "/../clean", False)
    expect("glob token", ".claude/scratch/_regenerable_clone_fixture_*/clean", False)
    expect("variable token", "$HOME/clean", False)
    expect("home token", "~/clean", False)
    expect("nonexistent path", os.path.join(FIX, "missing"), False)
    expect("empty token", "", False)

    # The real pre_bash_dispatch.py channel: a crash or traceback is never an allow.
    expect_channel("rm -rf clean clone", "rm -rf " + rel(clean), "allow")
    expect_channel("rm -rf dirty clone", "rm -rf " + rel(dirty), "deny")
    expect_channel("rm -rf clean clone plus evidence dir", "rm -rf " + rel(clean) + " " + rel(plain), "deny")
    expect_channel("rm -rf clean clone chained", "rm -rf " + rel(clean) + " && rm -rf " + rel(dirty), "deny")
    expect_channel("rm -rf scratch", "rm -rf .claude/scratch", "deny")
finally:
    force_rmtree(FIX)
    force_rmtree(outside)

total += 1
if os.path.exists(FIX) or os.path.exists(outside):
    fails += 1
    print(f"  FAIL teardown left a fixture directory: {FIX if os.path.exists(FIX) else outside}")
else:
    print("  ok   teardown removed this run's fixture directories")

print()
print(f"{total - fails}/{total} cases pass" if fails else f"ALL PASS ({total} cases)")
sys.exit(1 if fails else 0)
