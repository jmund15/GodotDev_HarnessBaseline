"""Re-runnable proof for the merge-commit scope rule (`hooks/_commit_scope.py`) as
`hooks/git_guardrails.py` and `hooks/baseline_classification_guard.py` read it.

A merge commit re-commits its parents' content; only staged content differing from BOTH parents
is this commit's own. Before that rule the guard denied a `git commit` closing a merge of
`origin/main` in a fresh PR-sync worktree, because a deletion main made leaves the staged blob
and `MERGE_HEAD:<path>` both absent, which read as "authored here". `git merge --continue` was
not judged at all (so PRs 101/108 closed the same merges with no check).

Every case feeds the hook a real PreToolUse payload against a planted temp repo and asserts on
the emitted channel (deny via stderr + exit 2, or allow via exit 0). An exit outside {0, 2}, or
a traceback, is a CRASH and fails its case -- a hook that cannot import must not read as allow.

    python3 .claude/tests/test_merge_commit_scope.py
"""
import json
import os
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_TESTS_DIR)
HOOK = os.path.join(_CLAUDE_DIR, "hooks", "git_guardrails.py")
RUNNER = os.path.join(_CLAUDE_DIR, "scripts", "harness_tests.py")

ALLOW, DENY, CRASH = "allow", "deny", "crash"


def git(repo, args, check=True):
    r = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, r.stderr))
    return r


def write(repo, rel, content):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def make_repo():
    repo = tempfile.mkdtemp(prefix="mscope_")
    git(repo, ["init", "-q"])
    git(repo, ["config", "user.email", "t@t.example"])
    git(repo, ["config", "user.name", "t"])
    return repo


def head(repo):
    return git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def head_file(repo, name):
    """The absolute path of a git state file (`MERGE_HEAD`, `CHERRY_PICK_HEAD`, ...)."""
    rel = git(repo, ["rev-parse", "--git-path", name]).stdout.strip()
    return rel if os.path.isabs(rel) else os.path.join(repo, rel)


def run_hook(command, repo):
    payload = {"tool_name": "Bash", "session_id": "mscope_proof", "cwd": repo,
               "tool_input": {"command": command}}
    env = dict(os.environ)
    env["HARNESS_TEST_STAMP"] = os.path.join(repo, "stamp.json")
    env["HARNESS_HOOK_STATE_DIR"] = os.path.join(repo, "hookstate")
    env["HARNESS_NO_PASS_CACHE"] = "1"
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=120, env=env, cwd=repo)
    if r.returncode not in (0, 2) or "Traceback (most recent call last)" in r.stderr:
        return CRASH, (r.stderr or r.stdout)[:400]
    return (DENY, r.stderr) if r.returncode == 2 else (ALLOW, r.stdout)


def scoped_run(repo):
    """`harness_tests.py --for <the staged paths>` in this checkout. The stamp is redirected into the
    temp repo so the real checkout's stamp is never touched."""
    env = dict(os.environ, HARNESS_TEST_STAMP=os.path.join(repo, "stamp.json"),
               HARNESS_SLOT_DIR=os.path.join(repo, "slots"), HARNESS_NO_PASS_CACHE="1")
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, capture_output=True,
                            text=True, check=True).stdout.split()
    return subprocess.run([sys.executable, RUNNER, "--repo", repo, "--for"] + staged, cwd=repo,
                          capture_output=True, text=True, timeout=600, env=env)


def case(label, got, expected, needle=""):
    needles = (needle,) if isinstance(needle, str) else needle
    verdict, reason = got
    ok = verdict == expected and all(not part or part in reason for part in needles)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        return "%s\n     expected=%s got=%s reason=%r" % (label, expected, verdict, reason[:300])
    return None


def seed(repo, branch_edits):
    """A trunk commit, then one branch per `(name, edits)` applied on top of it; HEAD is left on
    the last branch. `edits` is a list of `(relpath, content)` or `(relpath, None)` to delete."""
    write(repo, "README.md", "seed\n")
    write(repo, ".claude/hooks/h.py", "h = 1\n")
    write(repo, ".claude/hooks/old.py", "old = 1\n")
    write(repo, ".claude/tests/test_h_ok.py", "# proof\n")
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "seed"])
    base = head(repo)
    for name, edits in branch_edits:
        git(repo, ["checkout", "-q", base])
        git(repo, ["checkout", "-q", "-b", name])
        for rel, content in edits:
            full = os.path.join(repo, rel)
            if content is None:
                os.remove(full)
            else:
                write(repo, rel, content)
        git(repo, ["add", "-A"])
        git(repo, ["commit", "-q", "-m", name])
    return base


def upstream_fixture(repo):
    """HEAD (`work`) adds feat.py; `upstream` -- what MERGE_HEAD will name -- deletes old.py and
    adds new_from_main.py. A clean `--no-commit` merge stages all three."""
    seed(repo, [
        ("upstream", [(".claude/hooks/old.py", None), (".claude/hooks/new_from_main.py", "n = 1\n")]),
        ("work", [(".claude/hooks/feat.py", "f = 1\n")]),
    ])
    git(repo, ["merge", "-q", "--no-commit", "--no-ff", "upstream"])


def unit_cases(failures):
    """The helper's own contract, read directly: the two questions both guards ask it."""
    sys.path.insert(0, os.path.join(_CLAUDE_DIR, "hooks"))
    import _commit_scope as cs

    for sub, rest, want in (
            ("commit", ["-F", "msg"], True),
            ("merge", ["--continue"], True),
            ("cherry-pick", ["--continue"], True),
            ("revert", ["--continue"], True),
            ("rebase", ["--continue"], True),
            ("am", ["--continue"], True),
            ("am", ["--resolved"], True),
            ("rebase", ["main"], False),
            ("am", ["patch.mbox"], False),
            ("merge", ["--abort"], False),
            ("merge", ["upstream"], False),
            ("cherry-pick", ["<rev>"], False),
            ("revert", ["<rev>"], False),
    ):
        failures.append(case("publishes_index(%r, %r) -> %s" % (sub, rest, want),
                             (cs.publishes_index(sub, rest), ""), want))

    plain = make_repo()
    write(plain, ".claude/hooks/h.py", "h = 1\n")
    git(plain, ["add", "-A"])
    git(plain, ["commit", "-q", "-m", "seed"])
    failures.append(case("authored_paths with no merge in progress returns every path",
                         (cs.authored_paths([".claude/hooks/h.py"], plain), ""), [".claude/hooks/h.py"]))
    failures.append(case("incoming_heads with nothing in progress -> []",
                         (cs.incoming_heads(plain), ""), []))

    small, small_calls = bulk_merge_authored(cs, 10)
    large, large_calls = bulk_merge_authored(cs, 80)
    expected = [".claude/hooks/edited.py", ".claude/hooks/h.py", ".claude/hooks/resolved.py"]
    failures.append(case("authored_paths keeps only the edit, the authored deletion and the conflict "
                         "resolution; upstream's additions and deletion, this side's file and an absent "
                         "path arrive judged", (sorted(small), ""), sorted(expected)))
    failures.append(case("authored_paths result does not depend on how many paths arrive unchanged",
                         (sorted(large), ""), sorted(expected)))
    failures.append(case("authored_paths spawns a process count independent of the path count "
                         "(%d for 10 bulk paths, %d for 80)" % (small_calls, large_calls),
                         (ALLOW if small_calls == large_calls else DENY, ""), ALLOW))


def bulk_merge_authored(cs, bulk):
    """`authored_paths` over a merge staging `bulk` upstream additions plus one path of each other
    shape, with the subprocess spawns it made counted. (result, spawn count)."""
    repo = make_repo()
    seed(repo, [
        ("upstream", [(".claude/hooks/old.py", None), (".claude/hooks/new_from_main.py", "n = 1\n"),
                      (".claude/hooks/resolved.py", "r = 2  # upstream\n")]
         + [("bulk/f%d.txt" % i, "b%d\n" % i) for i in range(bulk)]),
        ("work", [(".claude/hooks/feat.py", "f = 1\n"), (".claude/hooks/resolved.py", "r = 3  # work\n")]),
    ])
    git(repo, ["merge", "--no-commit", "--no-ff", "upstream"], check=False)
    write(repo, ".claude/hooks/resolved.py", "r = 4  # resolved here\n")
    write(repo, ".claude/hooks/edited.py", "e = 1\n")
    git(repo, ["add", ".claude/hooks/resolved.py", ".claude/hooks/edited.py"])
    git(repo, ["rm", "-q", "--cached", ".claude/hooks/h.py"])
    paths = sorted(set(git(repo, ["diff", "--cached", "--name-only"]).stdout.split())
                   | {".claude/hooks/old.py", "absent/nowhere.py"})
    calls = []
    real = subprocess.run

    def counting(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return real(*args, **kwargs)

    subprocess.run = counting
    try:
        result = cs.authored_paths(paths, repo)
    finally:
        subprocess.run = real
    return result, len(calls)


def main():
    failures = []

    # M1/M2/M6: the PR-sync shape -- a merge of the upstream ref re-commits upstream's deletion
    # and upstream's addition, plus this branch's own file. Only a conflict resolution or an
    # edit differs from both parents, so nothing here is this commit's to prove.
    repo = make_repo()
    upstream_fixture(repo)
    failures.append(case(
        "M1 merge state planted (MERGE_HEAD present, index holds the merge result)",
        (ALLOW if os.path.exists(head_file(repo, "MERGE_HEAD")) else DENY, ""), ALLOW))
    staged = git(repo, ["diff", "--cached", "--name-only"]).stdout
    failures.append(case(
        "M1 setup: the merge stages upstream's deletion and addition",
        (ALLOW if "old.py" in staged and "new_from_main.py" in staged else DENY, staged), ALLOW))
    failures.append(case(
        "M1 a merge taking a parent's deletion of a harness file -> allow",
        run_hook("git commit -F msg", repo), ALLOW))
    failures.append(case(
        "M2 the same merge closed by `git merge --continue` -> allow",
        run_hook("git merge --continue", repo), ALLOW))

    # M3: a conflict resolution commits without a stamp; on-demand scoped results still work.
    repo = make_repo()
    seed(repo, [
        ("upstream", [(".claude/hooks/h.py", "h = 2  # upstream\n")]),
        ("work", [(".claude/hooks/h.py", "h = 3  # work\n")]),
    ])
    r = git(repo, ["merge", "--no-commit", "--no-ff", "upstream"], check=False)
    failures.append(case(
        "M3 setup: the merge conflicts on the harness file",
        (ALLOW if os.path.exists(head_file(repo, "MERGE_HEAD")) and r.returncode != 0 else DENY,
         r.stderr), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 4  # resolved here\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M3 a conflict-resolved harness file with no stamp -> allow; close proves it before push",
        run_hook("git commit -F msg", repo), ALLOW))
    failures.append(case(
        "M3 setup: the checkout has no full-run stamp",
        (ALLOW if not os.path.exists(os.path.join(repo, "stamp.json")) else DENY, ""), ALLOW))
    scoped = scoped_run(repo)
    failures.append(case(
        "M3 `--for` the staged paths in a checkout with no full-run stamp -> runs the bound proof and stamps",
        (ALLOW if scoped.returncode == 0 else DENY, scoped.stdout + scoped.stderr), ALLOW))
    stamp = {}
    try:
        with open(os.path.join(repo, "stamp.json"), encoding="utf-8") as fh:
            stamp = json.load(fh)
    except OSError:
        pass
    failures.append(case(
        "M3 the scoped stamp covers exactly the staged harness path",
        (ALLOW if list(stamp.get("files") or {}) == [".claude/hooks/h.py"] else DENY, json.dumps(stamp)[:300]),
        ALLOW))
    failures.append(case(
        "M3 the same commit after the scoped run -> allow",
        run_hook("git commit -F msg", repo), ALLOW))

    # M4/M5: --continue publishes the index and keeps the quick commit checks, never a proof stamp.
    repo = make_repo()
    upstream_fixture(repo)
    write(repo, ".claude/hooks/h.py", "h = 2  # edited during the merge\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M4 `git merge --continue` with an authored harness file and no stamp -> allow",
        run_hook("git merge --continue", repo), ALLOW))

    repo = make_repo()
    seed(repo, [
        ("picked", [(".claude/hooks/h.py", "h = 2  # on the picked commit\n")]),
        ("work", [(".claude/hooks/h.py", "h = 9  # work\n")]),
    ])
    r = git(repo, ["cherry-pick", "picked"], check=False)
    failures.append(case(
        "M4 setup: the cherry-pick stops in a conflicted state",
        (ALLOW if os.path.exists(head_file(repo, "CHERRY_PICK_HEAD")) and r.returncode != 0 else DENY,
         r.stderr), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 5  # resolved here\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M4 `git cherry-pick --continue` with an authored harness file and no stamp -> allow",
        run_hook("git cherry-pick --continue", repo), ALLOW))

    repo = make_repo()
    seed(repo, [
        ("reverted", [(".claude/hooks/h.py", "h = 2  # to be reverted\n")]),
        ("after", [(".claude/hooks/h.py", "h = 3  # moved on\n")]),
    ])
    rev = git(repo, ["rev-parse", "reverted"]).stdout.strip()
    r = git(repo, ["revert", rev], check=False)
    failures.append(case(
        "M4 setup: the revert conflicts on the harness file",
        (ALLOW if os.path.exists(head_file(repo, "REVERT_HEAD")) and r.returncode != 0 else DENY,
         r.stderr), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 1  # resolved here\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M4 `git revert --continue` with an authored harness file and no stamp -> allow",
        run_hook("git revert --continue", repo), ALLOW))

    # M7: the incoming ref is `CHERRY_PICK_HEAD` / `REVERT_HEAD`, not `MERGE_HEAD`. Resolving a
    # conflict to the incoming side is still authors nothing, so the closing `--continue` allows
    # instead of denying content the picked commit already had judged.
    repo = make_repo()
    seed(repo, [
        ("picked", [(".claude/hooks/h.py", "h = 2  # on the picked commit\n")]),
        ("work", [(".claude/hooks/h.py", "h = 9  # work\n")]),
    ])
    git(repo, ["cherry-pick", "picked"], check=False)
    failures.append(case(
        "M7 setup: CHERRY_PICK_HEAD is the incoming ref",
        (ALLOW if os.path.exists(head_file(repo, "CHERRY_PICK_HEAD")) else DENY, ""), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 2  # on the picked commit\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M7 a cherry-pick resolved to the picked side -> allow",
        run_hook("git cherry-pick --continue", repo), ALLOW))

    repo = make_repo()
    seed(repo, [
        ("reverted", [(".claude/hooks/h.py", "h = 2  # to be reverted\n")]),
        ("after", [(".claude/hooks/h.py", "h = 3  # moved on\n")]),
    ])
    rev = git(repo, ["rev-parse", "reverted"]).stdout.strip()
    git(repo, ["revert", rev], check=False)
    failures.append(case(
        "M7 setup: REVERT_HEAD is the incoming ref",
        (ALLOW if os.path.exists(head_file(repo, "REVERT_HEAD")) else DENY, ""), ALLOW))
    write(repo, ".claude/hooks/h.py", "h = 2  # to be reverted\n")
    git(repo, ["add", ".claude/hooks/h.py"])
    failures.append(case(
        "M7 a revert resolved to the reverted side -> allow",
        run_hook("git revert --continue", repo), ALLOW))

    # M5: nothing authored beyond MERGE_HEAD, so the closing `--continue` needs no stamp.
    repo = make_repo()
    upstream_fixture(repo)
    failures.append(case(
        "M5 `git merge --continue` with nothing authored beyond MERGE_HEAD -> allow",
        run_hook("git merge --continue", repo), ALLOW))

    unit_cases(failures)

    total = len(failures)
    failures = [f for f in failures if f]
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
