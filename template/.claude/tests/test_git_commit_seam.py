#!/usr/bin/env python3
"""Re-runnable proof for hooks/_git_commit.py — the one commit parser every commit guard
imports. Each case plants a bypass shape that used to slip past at least one guard.

    python3 .claude/tests/test_git_commit_seam.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
import _git_commit as gc  # noqa: E402


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo():
    repo = tempfile.mkdtemp(prefix="gcseam_")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def main():
    cases = []
    cwd = "/work"

    # --- detection ---------------------------------------------------------------
    found = gc.commit_invocations("cat > m <<'EOF'\ngit commit -am x\nEOF\ngit status", cwd)
    cases.append(("a `git commit` inside a heredoc body is not an invocation", found == []))

    found = gc.commit_invocations("git add -A && git commit -F msg -- .claude", cwd)
    cases.append(("a real commit after && is found with its rest",
                  len(found) == 1 and found[0].sub == "commit" and found[0].rest == ["-F", "msg", "--", ".claude"]))

    found = gc.commit_invocations('git -C "/repo/wt" commit -m x', cwd)
    cases.append(("-C retargets the repo", found and found[0].cwd.replace("\\", "/") == "/work/repo/wt"
                  or (found and found[0].cwd.replace("\\", "/").endswith("/repo/wt"))))

    found = gc.commit_invocations("cd /elsewhere && git commit -m x", cwd)
    cases.append(("a preceding cd retargets the repo",
                  found and found[0].cwd.replace("\\", "/").endswith("/elsewhere")))

    found = gc.commit_invocations("HARNESS_ALLOW_X=1 git commit -m x", cwd)
    cases.append(("inline env prefix is captured", found and found[0].inline_env.get("HARNESS_ALLOW_X") == "1"))

    found = gc.commit_invocations("git merge feature && git cherry-pick abc123", cwd)
    cases.append(("merge and cherry-pick are commit-like", [f.sub for f in found] == ["merge", "cherry-pick"]))

    found = gc.commit_invocations('git log --grep="git commit -am"', cwd)
    cases.append(("a quoted mention inside another git command is not a commit", found == []))

    # --- parse_commit_args ------------------------------------------------------------
    cases.append(("-am reads as -a + -m <msg>", gc.parse_commit_args(["-am", "x"]) == (True, False, [])))
    cases.append(("--amend is read", gc.parse_commit_args(["--amend", "--no-edit"]) == (False, True, [])))
    cases.append(("pathspec after -- is read", gc.parse_commit_args(["-F", "m", "--", "a", "b"]) == (False, False, ["a", "b"])))

    # --- staged_paths / incoming_paths against a real repo ------------------------------
    repo = make_repo()
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("dirty\n")
    paths, err = gc.staged_paths([], repo)
    cases.append(("empty index, plain commit -> no paths", paths == set() and err is None))
    paths, _ = gc.staged_paths(["-a", "-m", "x"], repo)
    cases.append(("-a includes the dirty tracked file", paths == {"a.txt"}))
    paths, _ = gc.staged_paths(["--amend", "--no-edit"], repo)
    cases.append(("--amend republishes HEAD's paths", "a.txt" in paths))
    paths, err = gc.staged_paths([], tempfile.mkdtemp(prefix="gcseam_norepo_"))
    cases.append(("git failure reports the failing subcommand", paths is None and "diff --cached" in err))

    git(repo, "checkout", "-q", "-b", "feature")
    with open(os.path.join(repo, ".claude", "hooks", "h.py") if os.makedirs(os.path.join(repo, ".claude", "hooks"), exist_ok=True) is None else "", "w") as fh:
        fh.write("h = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "feature hook")
    git(repo, "checkout", "-q", "master") if subprocess.run(["git", "rev-parse", "--verify", "master"], cwd=repo, capture_output=True).returncode == 0 else git(repo, "checkout", "-q", "main")
    paths, _ = gc.incoming_paths("merge", ["feature"], repo)
    cases.append(("merge <branch> reports the branch's changed paths", ".claude/hooks/h.py" in paths))
    paths, _ = gc.incoming_paths("merge", ["--no-commit", "feature"], repo)
    cases.append(("merge --no-commit reports nothing (the later commit is judged)", paths == set()))
    paths, _ = gc.incoming_paths("cherry-pick", ["feature"], repo)
    cases.append(("cherry-pick <rev> reports the rev's paths", ".claude/hooks/h.py" in paths))

    # --- bypass_declared ----------------------------------------------------------------
    os.environ.pop("HARNESS_X_TEST", None)
    cases.append(("bypass: inline VAR=1", gc.bypass_declared({"HARNESS_X_TEST": "1"}, "HARNESS_X_TEST")))
    cases.append(("bypass: absent -> False", not gc.bypass_declared({}, "HARNESS_X_TEST")))
    os.environ["HARNESS_X_TEST"] = "1"
    cases.append(("bypass: hook env VAR=1", gc.bypass_declared({}, "HARNESS_X_TEST")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
