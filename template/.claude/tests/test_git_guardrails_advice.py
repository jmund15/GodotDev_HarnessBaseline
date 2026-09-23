"""Re-runnable proof for the advice text in hooks/git_guardrails.py's destructive-command blocks.

The stash list is shared by every session in a checkout, and a whole-file overwrite through an
uninspected verb (`git show <ref>:<path> > <path>`) discards a peer's edit as surely as the blocked
command. So the checkout and restore blocks advise copying the file aside and editing your own change
out in place, `reset --hard` advises `git reset --keep`, and no block recommends `git stash` or
`git show`. Real PreToolUse payloads; deny is stderr + exit 2.

    python3 .claude/tests/test_git_guardrails_advice.py
"""
import json
import os
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(_TESTS_DIR), "hooks", "git_guardrails.py")
FAILURES = []


def run_hook(command, repo):
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo, "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, cwd=repo)
    crashed = r.returncode not in (0, 2) or "Traceback" in r.stderr
    return r.returncode, r.stderr + r.stdout, crashed


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail[:400] if detail else ""))


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false"]
                   + list(args), cwd=repo, check=True, capture_output=True)


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _init_repo(repo):
    """f.txt carries a real uncommitted edit (the case the block exists for); clean.txt and
    crlf.txt are committed, crlf.txt rewritten with CRLF endings only."""
    git(repo, "init", "-q")
    for name in ("f.txt", "clean.txt", "crlf.txt", "staged.txt"):
        write(repo, name, "one\ntwo\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, "f.txt", "one\nmine\n")
    write(repo, "crlf.txt", "one\r\ntwo\r\n")
    write(repo, "staged.txt", "one\nstaged\n")
    git(repo, "add", "staged.txt")


def conflicted_repo():
    """A repo mid-merge with unmerged a.json and a.tscn."""
    repo = tempfile.mkdtemp(prefix="ggmerge_")
    git(repo, "init", "-q")
    for name in ("a.json", "a.tscn"):
        write(repo, name, "base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "other")
    for name in ("a.json", "a.tscn"):
        write(repo, name, "theirs\n")
    git(repo, "commit", "-q", "-am", "theirs")
    git(repo, "checkout", "-q", "-")
    for name in ("a.json", "a.tscn"):
        write(repo, name, "ours\n")
    git(repo, "commit", "-q", "-am", "ours")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "merge", "-q", "other"],
                   cwd=repo, capture_output=True)
    return repo


def recommends_stash(message):
    return "git stash" in message.replace("Never `git stash`", "")


def main():
    repo = tempfile.mkdtemp(prefix="ggadvice_")
    _init_repo(repo)

    for command in ("git checkout -- f.txt", "git restore f.txt"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s is blocked" % command, not crashed and rc == 2, msg)
        check("%s block recommends no git stash" % command, not recommends_stash(msg), msg)
        check("%s block recommends no git show overwrite" % command, "git show" not in msg, msg)
        check("%s block names copying the file aside" % command, "cp <path> .claude/scratch/" in msg, msg)
        check("%s block names the in-place edit" % command, "in place" in msg, msg)

    rc, msg, crashed = run_hook("git reset --hard", repo)
    check("git reset --hard is blocked", not crashed and rc == 2, msg)
    check("reset --hard block recommends no git stash", not recommends_stash(msg), msg)
    check("reset --hard block names git reset --keep", "git reset --keep" in msg, msg)

    rc, msg, crashed = run_hook("git stash drop", repo)
    check("git stash drop is still blocked", not crashed and rc == 2, msg)

    rc, msg, crashed = run_hook("git restore --staged f.txt", repo)
    check("git restore --staged still passes", not crashed and rc == 0, msg)

    # A discard that discards nothing is not a discard: no worktree diff, or a CR-only one.
    for command in ("git checkout -- clean.txt", "git checkout HEAD -- clean.txt", "git restore clean.txt",
                    "git checkout -- crlf.txt", "git checkout -- clean.txt crlf.txt"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s passes (nothing to lose)" % command, not crashed and rc == 0, msg)
    # staged.txt: worktree matches the index, but the index holds an edit HEAD lacks. Restoring
    # both sides, or from a source named by the short `-s`, loses that edit.
    for command in ("git checkout -- clean.txt f.txt", "git checkout -- .", "git checkout .",
                    "git restore .", "git checkout -- '*.txt'",
                    "git restore --staged --worktree staged.txt", "git restore -SW staged.txt",
                    "git restore -s HEAD staged.txt", "git checkout -f -- f.txt"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s is blocked" % command, not crashed and rc == 2, msg)

    # Taking one side of an unmerged path resolves the merge; .tscn/.tres stay blocked because
    # --theirs silently drops the other side's Export wiring there (KFM #10).
    merge = conflicted_repo()
    rc, msg, crashed = run_hook("git checkout --theirs -- a.json", merge)
    check("--theirs on an unmerged .json passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git checkout --ours -- a.json", merge)
    check("--ours on an unmerged .json passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git checkout --theirs -- a.tscn", merge)
    check("--theirs on an unmerged .tscn is blocked", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git checkout --theirs -- f.txt", repo)
    check("--theirs on a dirty, merged path is blocked", not crashed and rc == 2, msg)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
