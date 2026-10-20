"""Must-allow cases for every guard that gates `git commit`: the justified operations a floor guard
sits in front of, which it must never deny (`rules/harness_tooling.md` §Every guard has a
re-runnable proof, or it is a hope).

    must_allow_cases(hook, planted=None) -> ([(label, payload, cwd, "allow"), ...], cleanup)

`cleanup()` removes every fixture root the cases use; the caller invokes it once it has run them.

`planted` is the caller's `{"head": {...}, "offend": {...}, "setup": fn}` spec for `hook`
(`_merge_guard_cases.MERGE_PLANTED[hook]` for the eight it covers); this module holds none, so each
hook's planted content has one home. `head` and `setup` are the committed base; `offend` is the
content the merged branch carries. With no spec the merged branch carries a non-domain file.

    M1  `git merge feature` run in a fresh `git worktree add` checkout                  -> allow
    M1  the commit closing a `--no-commit` merge of `feature` in a fresh worktree         -> allow
    M3  a commit of a non-domain file in a fresh clone, which lacks every gitignored input
        (`.claude/tests/`, `.claude/cache/`, the stamp under `.claude/logs/`)           -> allow

M2 (`git merge --continue` after a conflict resolved to one parent's side) is arm B of
`_merge_guard_cases.merge_run_for`; a floor proof outside it carries its own M2 case.

M1 expects allow because the merge authors nothing (`hooks/_commit_scope.authored_paths`), and M3
because its commit touches no domain file.
The caller runs each payload through its own channel and installs any hook copy it runs into `cwd`.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile

NON_DOMAIN = {"head": {}, "offend": {"notes/merged.txt": "merged from feature\n"}}

# The inputs a checkout never receives from git. Written into the origin's worktree only, so the
# clone proves a guard does not depend on them.
IGNORED = {
    ".claude/tests/test_present_only_here.py": "print('ok')\n",
    ".claude/cache/present_only_here.txt": "cache\n",
    ".claude/logs/harness_tests_stamp.json": "{}\n",
}
GITIGNORE = ".claude/tests/\n.claude/cache/\n.claude/logs/\n"


def _git(repo, *args):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, repo, r.stderr))
    return r.stdout


def _write(repo, files):
    for rel, content in files.items():
        full = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)


def _remove(root):
    def writable(func, path, _exc):
        # Git leaves read-only object files, which Windows refuses to unlink.
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(root, onexc=writable)
    else:
        shutil.rmtree(root, onerror=writable)


def _identity(repo):
    _git(repo, "config", "user.email", "t@t.example")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _origin(spec):
    """A repo on `trunk` holding the spec's base, with `feature` one commit ahead carrying `offend`
    and the gitignored inputs present in its worktree only."""
    repo = os.path.realpath(tempfile.mkdtemp(prefix="mallow_"))
    _git(repo, "init", "-q", "-b", "trunk")
    _identity(repo)
    _write(repo, dict(spec["head"], **{"README.md": "seed\n", ".gitignore": GITIGNORE}))
    if spec.get("setup"):
        spec["setup"](repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, spec["offend"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "trunk")
    _write(repo, IGNORED)
    return repo


def _worktree(origin, name):
    wt = os.path.join(os.path.realpath(tempfile.mkdtemp(prefix="mallow_wt_")), name)
    _git(origin, "worktree", "add", "-q", "-b", name, wt, "trunk")
    return wt


def _clone(origin):
    clone = os.path.join(os.path.realpath(tempfile.mkdtemp(prefix="mallow_clone_")), "clone")
    _git(os.path.dirname(clone), "clone", "-q", origin, clone)
    _identity(clone)
    missing = [rel for rel in IGNORED if os.path.exists(os.path.join(clone, *rel.split("/")))]
    if missing:
        raise RuntimeError("fixture clone holds gitignored inputs: %r" % missing)
    return clone


def must_allow_cases(hook, planted=None):
    spec = planted or NON_DOMAIN
    content = "the planted content" if planted else "a non-domain file"
    origin = _origin(spec)

    def payload(cwd, command):
        return {"tool_name": "Bash", "session_id": "mallow_proof", "cwd": cwd,
                "tool_input": {"command": command}}

    roots = [origin]

    def cleanup():
        for root in roots:
            if os.path.exists(root):
                _remove(root)

    try:
        merge_wt = _worktree(origin, "sync_merge")
        roots.append(os.path.dirname(merge_wt))
        close_wt = _worktree(origin, "sync_close")
        roots.append(os.path.dirname(close_wt))
        _git(close_wt, "merge", "-q", "--no-commit", "--no-ff", "feature")
        merge_head = os.path.join(close_wt, _git(close_wt, "rev-parse", "--git-path", "MERGE_HEAD").strip())
        if not os.path.exists(merge_head):
            raise RuntimeError("fixture worktree is not mid-merge: MERGE_HEAD is absent")
        clone = _clone(origin)
        roots.append(os.path.dirname(clone))
        _write(clone, {"README.md": "seed\nedited in a fresh clone\n"})
        _git(clone, "add", "README.md")
    except BaseException:
        cleanup()
        raise

    return [
        ("M1 %s: `git merge feature` in a fresh worktree, `feature` carrying %s -> allow" % (hook, content),
         payload(merge_wt, "git merge feature"), merge_wt, "allow"),
        ("M1 %s: the commit closing a merge of %s in a fresh worktree -> allow" % (hook, content),
         payload(close_wt, "git commit -q -F m"), close_wt, "allow"),
        ("M3 %s: a commit of a non-domain file in a fresh clone lacking every gitignored input -> allow"
         % hook, payload(clone, "git commit -q -F m"), clone, "allow"),
    ], cleanup
