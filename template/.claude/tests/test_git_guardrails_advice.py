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
                       text=True, encoding="utf-8", timeout=30, cwd=repo,
                       env=dict(os.environ, PYTHONUTF8="1"))
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


def ref_checkout_repo():
    """HEAD at c1; c2 changes every file and adds n.txt. d.txt carries an unstaged edit, s.txt a staged
    one, n.txt sits untracked with local content: checking any of those out of c2 loses it."""
    repo = tempfile.mkdtemp(prefix="ggref_")
    git(repo, "init", "-q")
    for name in ("a.txt", "d.txt", "s.txt", "gone.txt"):
        write(repo, name, "c1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "c1")
    git(repo, "tag", "c1")
    for name in ("a.txt", "d.txt", "s.txt", "n.txt", "new.txt", "gone.txt"):
        write(repo, name, "c2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "c2")
    git(repo, "tag", "c2")
    git(repo, "checkout", "-q", "c1")
    write(repo, "d.txt", "mine\n")
    write(repo, "s.txt", "staged\n")
    git(repo, "add", "s.txt")
    write(repo, "n.txt", "local\n")
    os.remove(os.path.join(repo, "gone.txt"))
    return repo


def squashed_branch_repo():
    """main carries a cherry-pick of `sq`'s one commit, so `sq` is patch-equal to main but not merged;
    `uniq` holds a commit no other ref has. `other` sits at the base, and origin/HEAD names a ref that
    carries the cherry-pick, the shape of a branch squash-merged on the remote."""
    repo = tempfile.mkdtemp(prefix="ggsquash_")
    git(repo, "init", "-q", "-b", "main")
    write(repo, "a.txt", "base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "branch", "other")
    for branch, name in (("sq", "sq.txt"), ("uniq", "u.txt")):
        git(repo, "checkout", "-q", "-b", branch, "main")
        write(repo, name, branch + "\n")
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", branch)
    git(repo, "checkout", "-q", "main")
    git(repo, "cherry-pick", "sq")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return repo


def recommends_stash(message):
    return "git stash" in message


def main():
    repo = tempfile.mkdtemp(prefix="ggadvice_")
    _init_repo(repo)

    for command in ("git checkout -- f.txt", "git restore f.txt"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s is blocked" % command, not crashed and rc == 2, msg)
        check("%s block names saving the edit as a blob first" % command,
              "git hash-object -w" in msg and "refs/preserve/" in msg, msg)
        check("%s block recommends no git stash" % command, not recommends_stash(msg), msg)
        check("%s block recommends no git show overwrite" % command, "git show" not in msg, msg)
        check("%s block names one next step: save the edit, then the same command passes" % command,
              msg.count("refs/preserve/") == 1 and "then the same command passes" in msg, msg)
        check("%s block offers no second route (copy aside, edit in place)" % command,
              "cp <path>" not in msg and "aside" not in msg and "in place" not in msg, msg)

    rc, msg, crashed = run_hook("git reset --hard", repo)
    check("git reset --hard is blocked", not crashed and rc == 2, msg)
    check("reset --hard block recommends no git stash", not recommends_stash(msg), msg)
    check("reset --hard block names git reset --keep", "git reset --keep" in msg, msg)

    rc, msg, crashed = run_hook("git stash drop", repo)
    check("git stash drop is still blocked", not crashed and rc == 2, msg)

    # A drop loses nothing when another ref still holds the stash commit: the block's stated
    # reason ("unreferenced once dropped") is then false, so the drop passes. Unpreserved stashes,
    # an unresolvable target and `clear` stay blocked.
    stashed = tempfile.mkdtemp(prefix="ggstash_")
    git(stashed, "init", "-q")
    write(stashed, "a.txt", "base\n")
    git(stashed, "add", "a.txt")
    git(stashed, "commit", "-q", "-m", "base")
    write(stashed, "a.txt", "kept\n")
    git(stashed, "stash", "-q")
    write(stashed, "a.txt", "loose\n")
    git(stashed, "stash", "-q")
    kept = subprocess.run(["git", "rev-parse", "stash@{1}"], cwd=stashed, capture_output=True,
                          text=True, check=True).stdout.strip()
    git(stashed, "update-ref", "refs/preserve/kept", kept)
    rc, msg, crashed = run_hook("git stash drop stash@{1}", stashed)
    check("drop of a stash another ref preserves passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git stash drop 'stash@{1}'", stashed)
    check("quoted preserved target passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git stash drop -q stash@{1}", stashed)
    check("a quiet drop of a preserved stash passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git stash drop --quiet stash@{0}", stashed)
    check("a quiet drop of an unpreserved stash is still blocked", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git stash drop --index stash@{1}", stashed)
    check("an unknown drop flag fails closed", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git stash drop stash@{0}", stashed)
    check("drop of an unpreserved stash is blocked", not crashed and rc == 2, msg)
    check("the drop block names the preserve-then-drop retry",
          "git update-ref refs/preserve/" in msg and "stash@{0}" in msg, msg)
    rc, msg, crashed = run_hook("git stash drop", stashed)
    check("implicit drop of an unpreserved stash@{0} is blocked", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git stash drop stash@{9}", stashed)
    check("drop of a missing stash is blocked", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git stash clear", stashed)
    check("git stash clear stays blocked with a preserved entry", not crashed and rc == 2, msg)

    cite = "CLAUDE.core.md §Shell Discipline: the owner's explicit go-ahead first"
    destructive_ops = (
        ("git reset --hard", "git reset --keep"),
        ("git checkout -- f.txt", "save it first: `git update-ref refs/preserve/"),
        ("git restore f.txt", "save it first: `git update-ref refs/preserve/"),
        ("git push --force", "Rebase onto the remote and push normally"),
        ("git clean -f", "Run the same command with `-n` first"),
        ("git stash drop", "then the same drop passes"),
        ("git stash clear", "ask the user before discarding"),
        ("git reflog expire --expire=now --all", "Leave expiry to git's own schedule"),
        ("git gc --prune=now", "Run `git gc` without `--prune`"),
        ("git filter-branch -- --all", "Ask the user for explicit go-ahead"),
    )
    for command, next_step in destructive_ops:
        rc, msg, crashed = run_hook(command, repo)
        check("%s denial cites the hard-to-reverse rule and next step" % command,
              not crashed and rc == 2 and cite in msg and next_step in msg, msg)
    for command in ("git checkout -- f.txt", "git restore f.txt", "git branch -D obsolete"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s denial claims no reply of the owner lets it through" % command,
              "approving it lets" not in msg and "their next reply" not in msg, msg)

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

    # A path with no staged or unstaged edit, tracked in HEAD, loses nothing when checked out of another
    # commit (landing a merge onto a shared checkout): every byte it held is still in HEAD.
    ref = ref_checkout_repo()
    rc, msg, crashed = run_hook("git checkout c2 -- a.txt", ref)
    check("checkout <ref> -- a clean tracked path passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git checkout c2 -- new.txt", ref)
    check("checkout <ref> -- a path absent from HEAD and the worktree passes", not crashed and rc == 0, msg)
    for command in ("git checkout c2 -- d.txt", "git checkout c2 -- s.txt", "git checkout c2 -- n.txt",
                    "git checkout c2 -- gone.txt",
                    "git checkout c2 -- a.txt d.txt"):
        rc, msg, crashed = run_hook(command, ref)
        check("%s is blocked (an edit or an untracked file would be lost)" % command, not crashed and rc == 2, msg)

    # Python's bytecode cache is regenerated by the next import, so restoring a tracked one discards nothing
    # worth keeping; a `.pyc` outside `__pycache__`, or any other dirty path beside it, still counts.
    cache = tempfile.mkdtemp(prefix="ggpyc_")
    _init_repo(cache)
    os.makedirs(os.path.join(cache, "pkg", "__pycache__"))
    write(cache, "pkg/__pycache__/m.cpython-314.pyc", "old\n")
    write(cache, "pkg/m.cpython-314.pyc", "old\n")
    git(cache, "add", "-f", "pkg")
    git(cache, "commit", "-q", "-m", "tracked caches")
    write(cache, "pkg/__pycache__/m.cpython-314.pyc", "new\n")
    write(cache, "pkg/m.cpython-314.pyc", "new\n")
    for command in ("git checkout -- pkg/__pycache__/m.cpython-314.pyc", "git checkout -- pkg/__pycache__",
                    "git restore pkg/__pycache__/m.cpython-314.pyc", "git restore pkg/__pycache__",
                    "git checkout HEAD -- pkg/__pycache__"):
        rc, msg, crashed = run_hook(command, cache)
        check("%s passes: a tracked bytecode cache regenerates" % command, not crashed and rc == 0, msg)
    for command in ("git checkout -- pkg/__pycache__ f.txt", "git checkout -- pkg/m.cpython-314.pyc",
                    "git checkout -- pkg"):
        rc, msg, crashed = run_hook(command, cache)
        check("%s is blocked (something beside the bytecode cache would be lost)" % command,
              not crashed and rc == 2, msg)

    # A branch whose every commit is already applied to HEAD or to origin/HEAD loses nothing on -D
    # (a squash-merged or cherry-picked branch, which `-d` refuses because it is no ancestor).
    sq = squashed_branch_repo()
    rc, msg, crashed = run_hook("git branch -D sq", sq)
    check("branch -D of a branch whose commits HEAD already carries passes", not crashed and rc == 0, msg)
    for command in ("git branch -D uniq", "git branch -D sq uniq", "git branch -D nosuch",
                    "git branch -D -r origin/main"):
        rc, msg, crashed = run_hook(command, sq)
        check("%s is blocked (a commit would be lost, or the ref is not a local branch)" % command,
              not crashed and rc == 2, msg)
    git(sq, "branch", "scratch/fx", "uniq")
    rc, msg, crashed = run_hook("git branch -D scratch/fx", sq)
    check("branch -D of a scratch/ fixture branch passes though its commits are unmerged",
          not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git branch -D scratch/fx uniq", sq)
    check("branch -D of a scratch/ branch beside an unmerged other branch is blocked", not crashed and rc == 2, msg)
    git(sq, "checkout", "-q", "other")
    rc, msg, crashed = run_hook("git branch -D sq", sq)
    check("branch -D passes when origin/HEAD carries every commit and HEAD does not", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git branch --delete --force sq", sq)
    check("branch --delete --force takes the same pass", not crashed and rc == 0, msg)


    # An edit whose bytes git's object store already holds is lost to no one: a discard of it passes, and the
    # deny names how to get there. A second edit that is not stored keeps the whole command blocked.
    saved = tempfile.mkdtemp(prefix="ggsaved_")
    _init_repo(saved)
    write(saved, "g.txt", "two\nmore\n")
    git(saved, "add", "g.txt")
    git(saved, "commit", "-q", "-m", "g")
    write(saved, "g.txt", "two\nlocal g\n")
    git(saved, "hash-object", "-w", "f.txt")
    for command in ("git restore f.txt", "git checkout -- f.txt"):
        rc, msg, crashed = run_hook(command, saved)
        check("%s of an edit git already stores passes" % command, not crashed and rc == 0, msg)
    for command in ("git restore f.txt g.txt", "git checkout -- f.txt g.txt", "git restore g.txt"):
        rc, msg, crashed = run_hook(command, saved)
        check("%s is blocked: g.txt holds an edit git does not store" % command, not crashed and rc == 2, msg)
    git(saved, "hash-object", "-w", "g.txt")
    rc, msg, crashed = run_hook("git restore f.txt g.txt", saved)
    check("a restore of two stored edits passes", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git checkout HEAD -- f.txt", saved)
    check("checkout <ref> -- an edited path stays blocked though its bytes are stored (the index would go too)",
          not crashed and rc == 2, msg)
    check("the checkout <ref> deny names restore --source, not a retry of the same checkout",
          "git restore --source=HEAD -- " in msg and "then the same command passes" not in msg, msg)
    rc, msg, crashed = run_hook("git restore --source=HEAD -- f.txt", saved)
    check("the restore --source step the checkout <ref> deny names passes once the bytes are stored",
          not crashed and rc == 0, msg)
    os.makedirs(os.path.join(saved, "sub"), exist_ok=True)
    write(saved, "sub/x.txt", "one\n")
    git(saved, "add", "sub/x.txt")
    git(saved, "commit", "-q", "-m", "x")
    write(saved, "sub/x.txt", "edited\n")
    for command in ("git checkout -- .", "git checkout .", "git checkout -- '*.txt'", "git checkout -- sub"):
        rc, msg, crashed = run_hook(command, saved)
        check("%s deny says to name each file, not that the same command passes" % command,
              not crashed and rc == 2 and "name each file" in msg and "then the same command passes" not in msg, msg)

    # `branch -D` loses no commit when a ref outside the deleted set holds every one: a remote-tracking
    # ref, a tag, or refs/preserve. Otherwise the deny names each unmet branch and the preserve step.
    held = squashed_branch_repo()
    rc, msg, crashed = run_hook("git branch -D uniq", held)
    check("branch -D of a branch no other ref holds is blocked", not crashed and rc == 2, msg)
    check("the branch -D block names the branch and the preserve-then-delete retry",
          "uniq" in msg and "git update-ref refs/preserve/" in msg and "their next reply" not in msg, msg)
    rc, msg, crashed = run_hook("git branch -D uniq nosuch", held)
    check("branch -D names every unmet branch in one deny",
          not crashed and rc == 2 and "uniq" in msg and "nosuch" in msg, msg)
    rc, msg, crashed = run_hook("git branch -D nosuch", held)
    check("a branch -D deny with no unpreserved branch names no preserve step and no retry promise",
          not crashed and rc == 2 and "nosuch" in msg and "refs/preserve/" not in msg
          and "the same delete passes" not in msg, msg)
    git(held, "branch", "uniq2", "uniq")
    git(held, "update-ref", "refs/remotes/origin/uniq", "uniq")
    for command in ("git branch -D uniq", "git branch -D uniq uniq2", "git branch --delete --force uniq"):
        rc, msg, crashed = run_hook(command, held)
        check("%s passes: a remote-tracking ref holds the tip" % command, not crashed and rc == 0, msg)
    git(held, "update-ref", "-d", "refs/remotes/origin/uniq")
    rc, msg, crashed = run_hook("git branch -D uniq uniq2", held)
    check("two branches holding only each other's tip are blocked together", not crashed and rc == 2, msg)
    rc, msg, crashed = run_hook("git branch -D uniq", held)
    check("branch -D passes when a sibling branch holds the tip", not crashed and rc == 0, msg)
    git(held, "branch", "-D", "uniq2")
    git(held, "tag", "kept-uniq", "uniq")
    rc, msg, crashed = run_hook("git branch -D uniq", held)
    check("branch -D passes when a tag holds the tip", not crashed and rc == 0, msg)
    git(held, "tag", "-d", "kept-uniq")
    git(held, "update-ref", "refs/preserve/uniq", "uniq")
    rc, msg, crashed = run_hook("git branch -D uniq", held)
    check("branch -D passes after the preserve step", not crashed and rc == 0, msg)
    rc, msg, crashed = run_hook("git branch -D -r origin/main", held)
    check("branch -D of a remote-tracking name stays blocked", not crashed and rc == 2, msg)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
