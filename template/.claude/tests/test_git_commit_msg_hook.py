#!/usr/bin/env python3
"""Proof: the commit-msg hook re-runs the peer-revert check when git writes the commit, not before the hooks run.

The PreToolUse commit guard judges the index when the tool call starts; the commit hooks after it can take most of a
minute. A temp index built from HEAD before a peer's commit landed in that window was then committed and silently
reverted the peer's files (2026-10-05: 42 files of 2cd5c1f8d). Each case builds a throwaway repo with the real
`.claude/githooks/commit-msg` installed through core.hooksPath and runs real git commits.

    python3 .claude/tests/test_git_commit_msg_hook.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
FAILS = []
PEER = "Claude-Session: https://claude.ai/code/session_peer"
MINE = "Claude-Session: https://claude.ai/code/session_mine"


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        FAILS.append(label)


def git(repo, *args, env=None, check_rc=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, **(env or {})))
    if check_rc and r.returncode:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r


def write(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def make_repo(tmp):
    """A repo carrying the real hook and the guard it runs, with a base commit and one peer commit on f."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(repo, ".claude"))
    for rel in (".claude/githooks", ".claude/hooks"):
        shutil.copytree(os.path.join(ROOT, rel), os.path.join(repo, rel),
                        ignore=shutil.ignore_patterns("__pycache__"))
    for name in os.listdir(os.path.join(repo, ".claude", "githooks")):
        os.chmod(os.path.join(repo, ".claude", "githooks", name), 0o755)  # git ignores a non-executable hook; a checkout from a Windows-authored tree is 644
    os.makedirs(os.path.join(repo, '.claude', 'tools'))
    shutil.copy2(os.path.join(ROOT, '.claude', 'tools', 'sidecar_argv.py'),
                 os.path.join(repo, '.claude', 'tools', 'sidecar_argv.py'))
    git(repo, "init", "-q")
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false"),
                       ("core.hooksPath", ".claude/githooks")):
        git(repo, "config", key, value)
    write(repo, "f.txt", "base\n")
    write(repo, "g.txt", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "base")
    return repo


def stale_index(repo, tmp):
    """The race: a temp index read from HEAD, then a peer commit lands on f, then the temp index gains g."""
    index = os.path.join(tmp, "tmp_index")
    git(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": index})
    write(repo, "f.txt", "peer\n")
    git(repo, "commit", "-q", "-m", "peer change\n\n" + PEER, "--", "f.txt")
    write(repo, "g.txt", "mine\n")
    blob = git(repo, "hash-object", "-w", "g.txt").stdout.strip()
    git(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": index})
    return index


def head_file(repo, name):
    return git(repo, "show", "HEAD:" + name).stdout


def main():
    if not os.path.isfile(os.path.join(ROOT, ".claude", "githooks", "commit-msg")):
        check(False, ".claude/githooks/commit-msg exists")
        return 1
    tmp = tempfile.mkdtemp(prefix="commit_msg_hook_")
    try:
        message = os.path.join(tmp, "msg")
        with open(message, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("planted commit\n")
        r = subprocess.run(["sh", os.path.join(ROOT, ".claude", "githooks", "commit-msg"), message],
                           cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
        check(r.returncode == 1 and "BLOCKED commit" in r.stderr
              and "cannot find the repository root" in r.stderr and "peer-revert guard" in r.stderr,
              "a failed repository-root lookup refuses the commit with its missing prerequisite (rc=%d) %s"
              % (r.returncode, r.stderr[-300:]))

        repo = make_repo(os.path.join(tmp, "a"))
        index = stale_index(repo, os.path.join(tmp, "a"))
        r = git(repo, "commit", "-q", "-m", "mine\n\n" + MINE, env={"GIT_INDEX_FILE": index}, check_rc=False)
        check(r.returncode != 0 and "revert a peer's commit" in r.stderr and head_file(repo, "f.txt") == "peer\n",
              "THE INCIDENT: a temp index read before a peer's commit is refused when git writes the commit "
              "(rc=%d, f=%r) %s" % (r.returncode, head_file(repo, "f.txt"), r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "b"))
        index = stale_index(repo, os.path.join(tmp, "b"))
        git(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": index})
        blob = git(repo, "hash-object", "-w", "g.txt").stdout.strip()
        git(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": index})
        r = git(repo, "commit", "-q", "-m", "mine\n\n" + MINE, env={"GIT_INDEX_FILE": index}, check_rc=False)
        check(r.returncode == 0 and head_file(repo, "f.txt") == "peer\n" and head_file(repo, "g.txt") == "mine\n",
              "must-allow: a temp index rebuilt from the current HEAD commits (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "c"))
        write(repo, "f.txt", "mine\n")
        git(repo, "commit", "-q", "-m", "mine\n\n" + MINE, "--", "f.txt")
        write(repo, "f.txt", "base\n")
        git(repo, "add", "f.txt")
        r = git(repo, "commit", "-q", "-m", "undo mine\n\n" + MINE, check_rc=False)
        check(r.returncode == 0 and head_file(repo, "f.txt") == "base\n",
              "must-allow: a session may revert its own commit (same Claude-Session trailer) (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "d"))
        stale_index(repo, os.path.join(tmp, "d"))
        write(repo, "g.txt", "pathspec\n")
        r = git(repo, "commit", "-q", "-m", "pathspec\n\n" + MINE, "--", "g.txt", check_rc=False)
        check(r.returncode == 0 and head_file(repo, "f.txt") == "peer\n",
              "must-allow: a pathspec commit takes the working tree, never a stale snapshot (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "f"))
        stale_index(repo, os.path.join(tmp, "f"))
        r = git(repo, "commit", "-q", "-a", "-m", "all\n\n" + MINE, check_rc=False)
        check(r.returncode == 0 and head_file(repo, "f.txt") == "peer\n",
              "must-allow: a commit -a takes the working tree (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "g"))
        git(repo, "checkout", "-q", "-b", "side")
        write(repo, "s.txt", "side\n")
        git(repo, "add", "s.txt")
        git(repo, "commit", "-q", "-m", "side")
        git(repo, "checkout", "-q", "-")
        write(repo, "f.txt", "main\n")
        git(repo, "commit", "-q", "-m", "main change\n\n" + PEER, "--", "f.txt")
        r = git(repo, "merge", "-q", "--no-edit", "side", check_rc=False)
        check(r.returncode == 0 and head_file(repo, "s.txt") == "side\n",
              "must-allow: a merge commit (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "h"))
        index = os.path.join(tmp, "h", "next_stage_index")
        git(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": index})
        with open(index + ".paths.json", "w", encoding="utf-8", newline="\n") as fh:
            fh.write('{"base_head": 12345}')
        write(repo, "g.txt", "malformed\n")
        blob = git(repo, "hash-object", "-w", "g.txt").stdout.strip()
        git(repo, "update-index", "--cacheinfo", "100644,%s,g.txt" % blob, env={"GIT_INDEX_FILE": index})
        r = git(repo, "commit", "-q", "-m", "mine\n\n" + MINE, env={"GIT_INDEX_FILE": index}, check_rc=False)
        check(r.returncode != 0 and "stage record" in r.stderr and head_file(repo, "g.txt") == "base\n",
              "a stage record whose base_head is not a commit id is refused, never skipped (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "i"))
        guard = os.path.join(repo, ".claude", "hooks", "git_guardrails.py")
        with open(guard, encoding="utf-8") as fh:
            body = fh.read()
        anchor = "    environ = os.environ if environ is None else environ\n"
        with open(guard, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body.replace(anchor, "    raise RuntimeError('planted fault')\n" + anchor, 1))
        write(repo, "g.txt", "crash\n")
        git(repo, "add", "g.txt")
        r = git(repo, "commit", "-q", "-m", "crash\n\n" + MINE, check_rc=False)
        check(r.returncode != 0 and "planted fault" in r.stderr and "repair" in r.stderr.lower()
              and head_file(repo, "g.txt") == "base\n",
              "a guard that crashes refuses the commit and names the repair (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "j"))
        os.remove(os.path.join(repo, ".claude", "hooks", "git_guardrails.py"))
        write(repo, "g.txt", "noguard\n")
        git(repo, "add", "g.txt")
        r = git(repo, "commit", "-q", "-m", "noguard\n\n" + MINE, check_rc=False)
        check(r.returncode != 0 and "git_guardrails.py" in r.stderr and head_file(repo, "g.txt") == "base\n",
              "a missing guard refuses the commit and names the file to restore (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))

        bare = os.pathsep.join(os.path.dirname(shutil.which(t) or "") for t in ("git", "sh"))
        if shutil.which("git") and shutil.which("sh") and shutil.which("python3", path=bare) and os.name == "posix":
            # On a POSIX host git, sh and python3 share /usr/bin: link just the first two into a bare directory.
            bare = os.path.join(tmp, "bare_bin")
            os.makedirs(bare)
            for tool in ("git", "sh"):
                os.symlink(shutil.which(tool), os.path.join(bare, tool))
        if shutil.which("git") and shutil.which("sh") and not shutil.which("python3", path=bare):
            repo = make_repo(os.path.join(tmp, "k"))
            write(repo, "g.txt", "nopython\n")
            git(repo, "add", "g.txt")
            r = git(repo, "commit", "-q", "-m", "nopython\n\n" + MINE, env={"PATH": bare}, check_rc=False)
            check(r.returncode != 0 and "python3" in r.stderr and head_file(repo, "g.txt") == "base\n",
                  "a hook that cannot find python3 refuses the commit and names it (rc=%d) %s"
                  % (r.returncode, r.stderr[-200:]))
        else:
            check(False, "a PATH holding git and sh without python3 can be planted (%r)" % bare)

        repo = make_repo(os.path.join(tmp, "l"))
        wt = os.path.join(tmp, "l", "wt")
        git(repo, "worktree", "add", "-q", "-b", "fresh", wt)
        write(wt, "g.txt", "worktree\n")
        git(wt, "add", "g.txt")
        r = git(wt, "commit", "-q", "-m", "worktree\n\n" + MINE, check_rc=False)
        check(r.returncode == 0 and head_file(wt, "g.txt") == "worktree\n",
              "must-allow: a commit in a fresh `git worktree add` checkout (rc=%d) %s" % (r.returncode, r.stderr[-200:]))

        repo = make_repo(os.path.join(tmp, "e"))
        write(repo, "g.txt", "plain\n")
        git(repo, "add", "g.txt")
        r = git(repo, "commit", "-q", "-m", "plain", check_rc=False)
        check(r.returncode == 0, "must-allow: an ordinary commit with no trailer (an owner's commit) (rc=%d) %s"
              % (r.returncode, r.stderr[-200:]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
