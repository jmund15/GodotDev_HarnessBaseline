#!/usr/bin/env python3
"""Proof for hooks/prompt_git_state_delta.py's emission decision, on a real throwaway repository.

The delta speaks when something the next action depends on moved: the branch, HEAD, the
upstream counts, a watched submodule, or the index (`staged`). A change confined to the
unstaged or untracked counts is the session's own edits and stays silent, while the
cache still follows it so a later delta's "was" is current. The first prompt of a session
primes the cache silently.

    python3 -m pytest .claude/tests/test_prompt_git_state_delta_emission.py -q
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "prompt_git_state_delta.py")


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _run_cases(results):
    def case(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory(prefix="gsd_emit_") as root, \
            tempfile.TemporaryDirectory(prefix="gsd_cache_") as cache:
        repo = os.path.join(root, "repo")
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True, stdin=subprocess.DEVNULL)
        _git(repo, "config", "user.email", "a@example.com")
        _git(repo, "config", "user.name", "a")
        tracked = os.path.join(repo, "f.txt")
        with open(tracked, "w") as fh:
            fh.write("x")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        env = dict(os.environ, HARNESS_GIT_STATE_CACHE_DIR=cache)

        def prompt(session="emit0001"):
            run = subprocess.run([sys.executable, HOOK], input=json.dumps({"session_id": session}),
                                 capture_output=True, text=True, timeout=60, cwd=repo, env=env, encoding="utf-8")
            return run.returncode, run.stdout

        rc, out = prompt()
        case("the first prompt primes the cache and says nothing", rc == 0 and out == "", repr(out))

        with open(tracked, "w") as fh:
            fh.write("edited")
        with open(os.path.join(repo, "new.txt"), "w") as fh:
            fh.write("n")
        rc, out = prompt()
        case("an unstaged and untracked change alone is silent", rc == 0 and out == "", repr(out))

        _git(repo, "add", "f.txt")
        rc, out = prompt()
        case("a change to the index is reported with the counts", rc == 0 and "staged=1" in out, repr(out))
        rc, out = prompt()
        case("an unchanged state is silent", rc == 0 and out == "", repr(out))

        _git(repo, "commit", "-q", "-m", "second")
        rc, out = prompt()
        case("a new HEAD is reported", rc == 0 and "HEAD:" in out, repr(out))

        _git(repo, "switch", "-q", "-c", "other")
        rc, out = prompt()
        case("a branch switch is reported", rc == 0 and "Branch: other" in out, repr(out))

        with open(os.path.join(repo, "another.txt"), "w") as fh:
            fh.write("a")
        rc, out = prompt()
        case("the cache followed the silent change: the next delta's was-values are current",
             rc == 0 and out == "", repr(out))
        _git(repo, "add", "another.txt")
        rc, out = prompt()
        case("the staged delta reports the untracked count that dropped silently before",
             "untracked=1" in out and "(was: staged=0 unstaged=0 untracked=2)" in out, repr(out))


def test_emission_decision():
    results = []
    _run_cases(results)
    failed = [(name, detail) for name, ok, detail in results if not ok]
    assert not failed, failed


if __name__ == "__main__":
    results = []
    _run_cases(results)
    for name, ok, detail in results:
        print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail and not ok else ""))
    fails = sum(1 for _, ok, _ in results if not ok)
    print("\ntest_prompt_git_state_delta_emission: %d failed" % fails)
    sys.exit(1 if fails else 0)
