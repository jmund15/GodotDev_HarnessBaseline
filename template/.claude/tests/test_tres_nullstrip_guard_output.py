#!/usr/bin/env python3
"""Output-cap proof for hooks/tres_nullstrip_guard.py: a mass value loss stays under HOOK_TEXT_CAP.

A planted repo commits a C# [Export] with a meaningful default and .tres files that set it, then each
file drops the line. The guard's report (worktree scan and the commit-deny hook) must hold the verdict,
the instruction and the count line, with the combined output at most HOOK_TEXT_CAP bytes. A small loss
prints every row and no count line.

Both `.tres` guards render through one `_hook_parts.bounded_decision`, so the output-contract cases
(cap, JSON channel, LF-only bytes, overflow path, per-session whole list) are proven once here and the
script-strip twin imports them from this module; each guard supplies only its plant and the token a
reader acts on.

    python3 .claude/tests/test_tres_nullstrip_guard_output.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tres_nullstrip_guard.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
from amend_delivery import HOOK_TEXT_CAP  # noqa: E402

CS = (
    "using Godot;\n"
    "public partial class Cfg : Resource\n{\n"
    "    [Export] public int MaxSlots { get; set; } = 24;\n}\n"
)
ENUM_CS = (
    "using Godot;\n"
    "public partial class Cfg : Resource\n{\n"
    "    [Export] public Mode MaxSlots { get; set; } = Mode.Wide;\n}\n"
)
LINE = "MaxSlots = 5"
BODY = '[gd_resource type="Resource" format=3]\n\n[resource]\n' + LINE + "\nValue = 1\n"


class Guard:
    """What differs between the two guards: the hook, its plant, and the token a reader acts on."""

    def __init__(self, hook, module, plant, instruction, row_ok, small_side,
                 deny_row=None, absent=None, list_prefix=None):
        self.hook = hook
        self.module = module
        self.plant = plant
        self.instruction = instruction
        self.row_ok = row_ok
        self.small_side = small_side
        self.deny_row = deny_row
        self.absent = absent
        self.list_prefix = list_prefix


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="proof", GIT_AUTHOR_EMAIL="proof@example.invalid",
               GIT_COMMITTER_NAME="proof", GIT_COMMITTER_EMAIL="proof@example.invalid")
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, env=env)


def planted_repo(root, count, cs=CS):
    """A repo whose HEAD holds `count` .tres files setting MaxSlots, with each line dropped in the worktree."""
    repo = os.path.join(root, "repo")
    os.makedirs(os.path.join(repo, "Data"))
    git(repo, "init", "-q")
    with open(os.path.join(repo, "Cfg.cs"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(cs)
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
            fh.write(text.replace(LINE + "\n", ""))
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


def run(guard, repo, env, args, stdin=None):
    return Raw(subprocess.run([sys.executable, guard.hook, *args], cwd=repo,
                              input=stdin and stdin.encode("utf-8"), capture_output=True, env=env))


def run_with_scratch(guard, repo, env, args, scratch_dir, stdin=None):
    """Run the guard's own main() with its whole-list directory planted."""
    code = ("import sys; sys.path.insert(0, %r); sys.argv = [%r] + %r; import %s as g; "
            "g.SCRATCH_DIR = %r; sys.exit(g.main())"
            % (os.path.dirname(guard.hook), guard.hook, args, guard.module, scratch_dir))
    return Raw(subprocess.run([sys.executable, "-c", code], cwd=repo,
                              input=stdin and stdin.encode("utf-8"), capture_output=True, env=env))


def run_unwritable(guard, repo, env, args, blocker, stdin=None):
    """Run the guard's own main() with its overflow file planted under a path whose parent is a file."""
    return run_with_scratch(guard, repo, env, args, blocker, stdin)


def size(text):
    return len(text.encode("utf-8"))


def commit_payload(repo):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": repo})


def per_session_case(guard, root):
    """Two sessions' denials keep separate whole lists, and a rerun replaces only its own."""
    repo = guard.plant(os.path.join(root, "sessions"), 200)
    git(repo, "add", "-A")
    lists = os.path.join(root, "lists")
    os.makedirs(lists)
    payload = lambda sid: json.dumps({"session_id": sid, "tool_name": "Bash",
                                      "tool_input": {"command": "git commit -m x"}, "cwd": repo})
    first = run_with_scratch(guard, repo, planted_env(root), ["--hook"], lists, payload("sess-aaa"))
    other = run_with_scratch(guard, repo, planted_env(root), ["--hook"], lists, payload("sess-bbb"))
    again = run_with_scratch(guard, repo, planted_env(root), ["--hook"], lists, payload("sess-aaa"))
    written = sorted(os.listdir(lists))
    owned = [n for n in written if "sess-aaa" in n]
    rows_in_first = open(os.path.join(lists, owned[0]), encoding="utf-8").read() if owned else ""
    ok = (len(written) == 2 and all(n.startswith(guard.list_prefix) for n in written)
          and owned and any("sess-bbb" in n for n in written)
          and "Data/f0000.tres" in rows_in_first
          and first.returncode == other.returncode == again.returncode == 0)
    print("%-4s two sessions keep separate whole lists and a rerun replaces only its own" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def worktree_case(guard, root):
    repo = guard.plant(root, 200)
    proc = run(guard, repo, planted_env(root), ["--worktree"])
    out = proc.stdout + proc.stderr
    ok = (proc.returncode == 0 and size(out) <= HOOK_TEXT_CAP and guard.instruction in out
          and "full list:" in out and "\r" not in out
          and (guard.absent is None or guard.absent not in out))
    print("%-4s worktree scan of 200 stripped files: %d bytes (cap %d), exit %d"
          % ("ok" if ok else "FAIL", size(out), HOOK_TEXT_CAP, proc.returncode))
    return 0 if ok else 1


def hook_case(guard, root):
    repo = guard.plant(os.path.join(root, "hook"), 200)
    git(repo, "add", "-A")
    proc = run(guard, repo, planted_env(root), ["--hook"], stdin=commit_payload(repo))
    out = proc.stdout + proc.stderr
    try:
        hso = json.loads(proc.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError):
        hso = {}
    decision, reason = hso.get("permissionDecision", ""), hso.get("permissionDecisionReason", "")
    ok = (proc.returncode == 0 and decision == "deny" and size(out) <= HOOK_TEXT_CAP and proc.stderr == ""
          and "\r" not in out and guard.instruction in reason and "full list:" in reason
          and (guard.deny_row is None or guard.deny_row in reason))
    print("%-4s commit-deny hook of 200 stripped files carries the report in the deny: %d bytes (cap %d), decision %r"
          % ("ok" if ok else "FAIL", size(out), HOOK_TEXT_CAP, decision))
    return 0 if ok else 1


def unwritable_case(guard, root):
    repo = guard.plant(os.path.join(root, "unwritable"), 200)
    git(repo, "add", "-A")
    blocker = os.path.join(root, "blocker")
    open(blocker, "w").close()
    proc = run_unwritable(guard, repo, planted_env(root), ["--hook"], blocker, stdin=commit_payload(repo))
    try:
        decision = json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError):
        decision = ""
    ok = proc.returncode == 0 and decision == "deny"
    print("%-4s commit-deny hook with an unwritable overflow file still denies: exit %d, decision %r"
          % ("ok" if ok else "FAIL", proc.returncode, decision))
    return 0 if ok else 1


def small_case(guard, root):
    repo = guard.plant(os.path.join(root, "small"), 3)
    proc = run(guard, repo, planted_env(root), ["--worktree"])
    side = proc.stderr if guard.small_side == "stderr" else proc.stdout
    rows = [line for line in side.splitlines() if guard.row_ok(line)]
    ok = (len(rows) == 3 and "full list:" not in side and guard.instruction in side)
    print("%-4s worktree scan of 3 stripped files prints every row, no count line" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def run_guard_cases(guard, prefix):
    root = tempfile.mkdtemp(prefix=prefix)
    try:
        results = [worktree_case(guard, root), hook_case(guard, root), unwritable_case(guard, root),
                   per_session_case(guard, root), small_case(guard, root)]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if any(results) else 0


GUARD = Guard(HOOK, "tres_nullstrip_guard", planted_repo, "HARNESS_ALLOW_TRES_NULLSTRIP",
              lambda line: line.startswith("  Data/f") and "-MaxSlots" in line, "stderr",
              deny_row="Data/f0000.tres", list_prefix="tres_nullstrip_findings.")


def advisory_case(root):
    repo = planted_repo(os.path.join(root, "advisory"), 3, cs=ENUM_CS)
    git(repo, "add", "-A")
    proc = run(GUARD, repo, planted_env(root), ["--hook"], stdin=commit_payload(repo))
    try:
        hso = json.loads(proc.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError):
        hso = {}
    ok = (proc.returncode == 0 and "permissionDecision" not in hso and proc.stderr == ""
          and "Data/f0000.tres" in hso.get("additionalContext", "")
          and size(proc.stdout) <= HOOK_TEXT_CAP and "\r" not in proc.stdout)
    print("%-4s advisory-only commit carries the unverified rows in additionalContext, stderr empty"
          % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    root = tempfile.mkdtemp(prefix="tres_nullstrip_output_")
    try:
        results = [worktree_case(GUARD, root), hook_case(GUARD, root),
                   unwritable_case(GUARD, root), advisory_case(root), per_session_case(GUARD, root),
                   small_case(GUARD, root)]
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if any(results) else 0


if __name__ == "__main__":
    sys.exit(main())
