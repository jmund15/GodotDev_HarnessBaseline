#!/usr/bin/env python3
"""Proof: the peer-revert commit check judges what it observes, so a lane's own line of work is not a peer's.

2026-11-03 (receipt rootcause/spill/x4-s1b.md): a sidecar delegate in a linked worktree, with no session trailer
to match, was denied twice with "would revert a peer's commit": once for a deliberate `git revert --no-commit`
resolution, once for a `--pathspec-from-file` commit. Each case plants a repo and feeds the real PreToolUse hook
(`hooks/git_guardrails.py` as a child on stdin) a Bash `git commit` payload, then asserts exit 2 vs exit 0.

    python3 .claude/tests/test_git_guardrails_revert_lane.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "git_guardrails.py")
ALLOW, DENY, CRASH = "allow", "deny", "crash"
FAILS = []
NEEDLE = "would revert a peer's commit"


def git(repo, *args, stdin=None):
    r = subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false", *args], capture_output=True,
                       input=stdin.encode() if stdin is not None else None)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")))
    return r.stdout.decode("utf-8", "replace").strip()


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def commit(repo, name, text, message):
    write(repo, name, text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", message)


def make_repo(tmp, label):
    """A shared checkout on `main` whose origin/main (the default branch) is its tip."""
    repo = os.path.join(tmp, label)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    commit(repo, "seed.txt", "seed\n", "seed")
    return repo


def publish_default(repo):
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def stage_blob(repo, name, text):
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=text)
    git(repo, "update-index", "--cacheinfo", "100644,%s,%s" % (blob, name))


def hook(repo, command):
    """The real PreToolUse entry: (verdict, stderr)."""
    payload = {"tool_name": "Bash", "session_id": "no-such-session", "cwd": repo, "tool_input": {"command": command}}
    home = os.path.join(repo, "..", "home")
    os.makedirs(home, exist_ok=True)
    env = dict(os.environ, HOME=home, USERPROFILE=home, HARNESS_NO_PASS_CACHE="1")
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       timeout=120, env=env, cwd=repo)
    if r.returncode == 2:
        return DENY, r.stderr
    if r.returncode != 0 or "Traceback (most recent call last)" in r.stderr:
        return CRASH, r.stderr[-400:]
    return ALLOW, r.stdout


def check(label, got, expected, needle=""):
    verdict, text = got
    ok = verdict == expected and (not needle or needle in text)
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILS.append("%s\n     expected=%s got=%s text=%r" % (label, expected, verdict, text[:300]))


def lane_cases(tmp):
    """M5a: commits above the merge-base with the default branch, in a linked worktree, are the lane's own."""
    repo = make_repo(tmp, "lane")
    commit(repo, "m.txt", "A\n", "m A")
    commit(repo, "q.txt", "Q1\n", "q1")
    commit(repo, "q.txt", "Q2\n", "peer replaced Q1 before the lane forked")
    publish_default(repo)
    lane = os.path.join(tmp, "lane_wt")
    git(repo, "worktree", "add", "-q", "-b", "lane", lane)
    git(lane, "config", "user.email", "t@t")
    commit(lane, "m.txt", "B\n", "lane replaces A with B")
    stage_blob(lane, "m.txt", "A\n")
    write(lane, "m.txt", "A\n")
    check("M5a a lane re-staging the blob its own lane commit replaced -> allow",
          hook(lane, "git commit -F msg"), ALLOW)
    write(lane, "q.txt", "Q2\n")
    stage_blob(lane, "q.txt", "Q1\n")
    write(lane, "q.txt", "Q1\n")
    check("M5a a lane re-staging a blob a commit below the merge-base replaced -> still deny",
          hook(lane, "git commit -F msg"), DENY, NEEDLE)

    shared = make_repo(tmp, "shared")
    commit(shared, "m.txt", "A\n", "m A")
    publish_default(shared)
    commit(shared, "m.txt", "B\n", "peer replaces A with B in the shared checkout")
    git(shared, "worktree", "add", "-q", "-b", "other", os.path.join(tmp, "shared_wt"))
    stage_blob(shared, "m.txt", "A\n")
    write(shared, "m.txt", "A\n")
    check("M5a the shared checkout after a planted peer commit -> still deny",
          hook(shared, "git commit -F msg"), DENY, NEEDLE)


def revert_cases(tmp):
    """M5b: a commit while REVERT_HEAD names a commit stages older blobs by construction, for the paths that
    commit changed; a path it never changed is still judged."""
    repo = make_repo(tmp, "revert")
    commit(repo, "f.txt", "a\n.\n.\n.\nbx\n", "c0")
    commit(repo, "f.txt", "a\n.\n.\n.\nb\n", "c1 replaces the c0 blob")
    commit(repo, "f.txt", "ax\n.\n.\n.\nb\n", "c2, the commit to revert")
    commit(repo, "f.txt", "ax\n.\n.\n.\nbx\n", "c3")
    git(repo, "revert", "--no-commit", "HEAD~1")
    assert os.path.exists(os.path.join(repo, ".git", "REVERT_HEAD"))
    check("M5b `git revert --no-commit` whose result equals a blob an older commit replaced -> allow",
          hook(repo, "git commit -F msg"), ALLOW)

    repo = make_repo(tmp, "revert_other")
    commit(repo, "g.txt", "g0\n", "g0")
    commit(repo, "g.txt", "g1\n", "peer changes g")
    commit(repo, "f.txt", "f1\n", "the commit to revert")
    git(repo, "revert", "--no-commit", "HEAD")
    stage_blob(repo, "g.txt", "g0\n")
    write(repo, "g.txt", "g0\n")
    check("M5b a revert in progress still denies a stale blob of a path the reverted commit never changed",
          hook(repo, "git commit -F msg"), DENY, "g.txt")

    repo = make_repo(tmp, "revert_merge")
    commit(repo, "f.txt", "A\n", "c0")
    commit(repo, "f.txt", "B\n", "c1 replaces the c0 blob")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "f.txt", "C\n", "side replaces B")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    git(repo, "revert", "--no-commit", "-m", "1", "HEAD")
    assert os.path.exists(os.path.join(repo, ".git", "REVERT_HEAD"))
    check("M5d `git revert -m 1` of a merge stages the mainline parent's blob, which the merge replaced -> allow",
          hook(repo, "git commit -F msg"), ALLOW)


def pathspec_file_cases(tmp):
    """M5c: `--pathspec-from-file` publishes the working tree like `-- <paths>`."""
    repo = make_repo(tmp, "pathspec")
    commit(repo, "f.txt", "A\n", "f A")
    commit(repo, "f.txt", "B\n", "peer replaces A with B")
    stage_blob(repo, "f.txt", "A\n")
    write(repo, "f.txt", "A\n")
    spec = os.path.join(tmp, "paths.txt")
    with open(spec, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("f.txt\n")
    spec = spec.replace("\\", "/")
    check("M5c `-- <paths>` is a pathspec commit -> allow",
          hook(repo, "git commit -F msg -- f.txt"), ALLOW)
    check("M5c `--pathspec-from-file=<abs path>` is a pathspec commit -> allow",
          hook(repo, "git commit -F msg --pathspec-from-file=%s" % spec), ALLOW)
    check("M5c `--pathspec-from-file <abs path>` is a pathspec commit -> allow",
          hook(repo, "git commit -F msg --pathspec-from-file %s" % spec), ALLOW)
    check("M5c the same stale index with no pathspec -> deny",
          hook(repo, "git commit -F msg"), DENY, NEEDLE)


def main():
    tmp = tempfile.mkdtemp(prefix="revert_lane_")
    try:
        lane_cases(tmp)
        revert_cases(tmp)
        pathspec_file_cases(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for fail in FAILS:
        print("FAILED:", fail)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
