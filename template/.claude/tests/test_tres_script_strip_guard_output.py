#!/usr/bin/env python3
"""Output-cap proof for hooks/tres_script_strip_guard.py: a mass strip stays under HOOK_TEXT_CAP.

A planted repo commits .tres files with a script binding, then strips it from each. The guard's
report (worktree scan and the commit-deny hook) must hold the verdict, the instruction and the count
line, with the combined output at most HOOK_TEXT_CAP bytes. A small strip prints every row and no count line.

    python3 .claude/tests/test_tres_script_strip_guard_output.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tres_script_strip_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
from amend_delivery import HOOK_TEXT_CAP  # noqa: E402

BINDING = 'script = ExtResource("1_a")'
BODY = (
    '[gd_resource type="Resource" format=3]\n\n'
    '[ext_resource type="Script" path="res://Scripts/cfg.cs" id="1_a"]\n\n'
    "[resource]\n" + BINDING + "\nValue = 1\n"
)


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="proof", GIT_AUTHOR_EMAIL="proof@example.invalid",
               GIT_COMMITTER_NAME="proof", GIT_COMMITTER_EMAIL="proof@example.invalid")
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, env=env)


def planted_repo(root, count):
    """A repo whose HEAD holds `count` .tres bindings, with every binding stripped in the worktree."""
    repo = os.path.join(root, "repo")
    os.makedirs(os.path.join(repo, "Data"))
    git(repo, "init", "-q")
    for i in range(count):
        with open(os.path.join(repo, "Data", "f%04d.tres" % i), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(BODY)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    for i in range(count):
        path = os.path.join(repo, "Data", "f%04d.tres" % i)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(BINDING + "\n", ""))
    return repo


def planted_env(root):
    home = os.path.join(root, "home")
    os.makedirs(home, exist_ok=True)
    gitconfig = os.path.join(root, "gitconfig")
    open(gitconfig, "w").close()
    return dict(os.environ, HOME=home, USERPROFILE=home, GIT_CONFIG_GLOBAL=gitconfig,
                GIT_CONFIG_NOSYSTEM="1", CLAUDE_PROJECT_DIR=root)


class Raw:
    """A child's output decoded without newline folding, so a CR the guard writes stays visible."""

    def __init__(self, proc):
        self.returncode = proc.returncode
        self.stdout = proc.stdout.decode("utf-8")
        self.stderr = proc.stderr.decode("utf-8")


def run(repo, env, args, stdin=None):
    return Raw(subprocess.run([sys.executable, HOOK, *args], cwd=repo, input=stdin and stdin.encode("utf-8"),
                              capture_output=True, env=env))


def run_unwritable(repo, env, args, blocker, stdin=None):
    """Run the guard's own main() with its overflow file planted under a path whose parent is a file."""
    code = ("import sys; sys.path.insert(0, %r); sys.argv = [%r] + %r; import %s as g; "
            "g.SCRATCH_LIST = %r; sys.exit(g.main())"
            % (os.path.dirname(HOOK), HOOK, args, "tres_script_strip_guard", os.path.join(blocker, "list.txt")))
    return Raw(subprocess.run([sys.executable, "-c", code], cwd=repo, input=stdin and stdin.encode("utf-8"),
                              capture_output=True, env=env))


def size(text):
    return len(text.encode("utf-8"))


def worktree_case(root):
    repo = planted_repo(root, 200)
    proc = run(repo, planted_env(root), ["--worktree"])
    out = proc.stdout + proc.stderr
    ok = (proc.returncode == 0 and size(out) <= HOOK_TEXT_CAP and "tres-script-strip-guard" in out
          and "repair-inplace" in out and "full list:" in out and "Data/f0199.tres" not in out)
    print("%-4s worktree scan of 200 stripped files: %d bytes (cap %d), exit %d"
          % ("ok" if ok else "FAIL", size(out), HOOK_TEXT_CAP, proc.returncode))
    return 0 if ok else 1


def hook_case(root):
    repo = planted_repo(os.path.join(root, "hook"), 200)
    git(repo, "add", "-A")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": repo})
    proc = run(repo, planted_env(root), ["--hook"], stdin=payload)
    out = proc.stdout + proc.stderr
    try:
        hso = json.loads(proc.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError):
        hso = {}
    decision, reason = hso.get("permissionDecision", ""), hso.get("permissionDecisionReason", "")
    ok = (proc.returncode == 0 and decision == "deny" and size(out) <= HOOK_TEXT_CAP and proc.stderr == ""
          and "repair-inplace" in reason and "full list:" in reason)
    print("%-4s commit-deny hook of 200 stripped files carries the report in the deny: %d bytes (cap %d), decision %r"
          % ("ok" if ok else "FAIL", size(out), HOOK_TEXT_CAP, decision))
    return 0 if ok else 1


def unwritable_case(root):
    repo = planted_repo(os.path.join(root, "unwritable"), 200)
    git(repo, "add", "-A")
    blocker = os.path.join(root, "blocker")
    open(blocker, "w").close()
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": repo})
    proc = run_unwritable(repo, planted_env(root), ["--hook"], blocker, stdin=payload)
    try:
        decision = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError):
        decision = ""
    ok = proc.returncode == 0 and decision == "deny"
    print("%-4s commit-deny hook with an unwritable overflow file still denies: exit %d, decision %r"
          % ("ok" if ok else "FAIL", proc.returncode, decision))
    return 0 if ok else 1


def small_case(root):
    repo = planted_repo(os.path.join(root, "small"), 3)
    proc = run(repo, planted_env(root), ["--worktree"])
    rows = [line for line in proc.stdout.splitlines() if line.startswith("  Data/f")]
    ok = (len(rows) == 3 and "full list:" not in proc.stdout and "repair-inplace" in proc.stdout)
    print("%-4s worktree scan of 3 stripped files prints every row, no count line" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    root = tempfile.mkdtemp(prefix="tres_script_output_")
    try:
        results = [worktree_case(root), hook_case(root), unwritable_case(root), small_case(root)]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if any(results) else 0


if __name__ == "__main__":
    sys.exit(main())
