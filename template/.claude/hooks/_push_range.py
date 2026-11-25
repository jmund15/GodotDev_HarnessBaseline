"""Library (not a hook): the facts of a push — its destination, outgoing range and covering close. Stdlib and
`_git_run` only, so the push guard, the proof runner and the approval hook read one description.

    push_destination(root, git_env=None) -> (remote, target)
    close_base(root, base=None, *, push_ref=None, head="HEAD", git_env=None, remote="origin") -> str
    close_paths(root, base, head, remote="origin", git_env=None) -> list[str]
    default_base_covers(root, base, head, remote="origin", git_env=None) -> bool
    green_close(root) -> dict | None
    close_covers(close, base, rev, paths, root, remote="origin", git_env=None) -> bool

`EMPTY_TREE` names git's built-in empty tree for each object format. Git resolves both ids without the object
being written, so a close with no base names the constant and leaves the repository untouched. `git_env` is an
overlay with `_git_run.run`'s meaning (a `None` value unsets), passed to every query a caller scopes.
"""
import os

import _git_run
from _hook_state import SETUP_SURFACE_FILES as _SETUP_SURFACES, read_json_salvage

# git's empty tree per object format; `git rev-parse --show-object-format` names the format.
EMPTY_TREE = {
    "sha1": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
    "sha256": "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321",
}


def _soft(repo_root, args, git_env=None):
    """Stripped stdout of a read-only git call in `repo_root`, or "": these readers fall back when a value is
    absent and never raise."""
    return (_git_run.soft_text(args, cwd=repo_root, timeout=None, env=git_env) or "").strip()


def _object_format(repo_root, git_env=None):
    """The repository's object format, or "sha1" when the probe predates SHA-256 support (a nonzero exit or no
    output). `close_base` denies a printed name `EMPTY_TREE` does not carry."""
    result = _git_run.run(["rev-parse", "--show-object-format"], cwd=repo_root, timeout=None, env=git_env)
    return (result.text.strip() if result.returncode == 0 else "") or "sha1"


def push_destination(repo_root, git_env=None):
    """(remote, target branch) of a bare `git push` from `repo_root`, in git's own order: the branch's
    pushRemote, remote.pushDefault, the branch's remote, else origin; the target is the branch's merge
    ref, else the branch itself. The close and the push guard both scope by this remote."""
    branch = _soft(repo_root, ["branch", "--show-current"], git_env)
    config = (lambda key: _soft(repo_root, ["config", "--get", key % branch], git_env)) if branch \
        else (lambda key: "")
    remote = config("branch.%s.pushRemote") or _soft(repo_root, ["config", "--get", "remote.pushDefault"], git_env) \
        or config("branch.%s.remote") or "origin"
    target = config("branch.%s.merge").replace("refs/heads/", "", 1) or branch or "main"
    return remote, target


def _default_ref(repo_root, remote="origin", git_env=None):
    """`remote`'s default-branch tracking ref, whose commits a branch's close leaves to the default
    branch; None when neither `<remote>/HEAD` nor `<remote>/main` resolves, and for `remote=None`: a close of a
    range that is already on the default branch (`--range-start`) subtracts nothing."""
    if remote is None:
        return None
    for ref in (remote + "/HEAD", remote + "/main"):
        if _soft(repo_root, ["rev-parse", "--verify", "-q", ref + "^{commit}"], git_env):
            return ref
    return None


def default_base_covers(repo_root, base, head, remote="origin", git_env=None):
    """True when `base` is `remote`'s default-branch commit and `head` contains it: a branch that merged the
    default branch may close against that commit, so the push guard accepts it as the base."""
    ref = _default_ref(repo_root, remote, git_env)
    if not base or not ref or base != _soft(repo_root, ["rev-parse", "--verify", "-q", ref + "^{commit}"], git_env):
        return False
    return _git_run.run(["merge-base", "--is-ancestor", base, head], cwd=repo_root, timeout=None,
                        env=git_env).returncode == 0


def close_base(repo_root, base=None, *, push_ref=None, head="HEAD", git_env=None, remote="origin"):
    """Explicit base, or the push destination's; an unpublished ref uses `remote`'s default-branch merge-base,
    else git's built-in empty tree for the repository's object format. Writes no object."""
    if base:
        resolved = _soft(repo_root, ["rev-parse", "--verify", base], git_env)
        if not resolved:
            raise RuntimeError("cannot resolve explicit close base " + base)
        return resolved
    refs = (push_ref,) if push_ref is not None else (
        "@{push}", "@{upstream}", remote + "/" + (_soft(repo_root, ["branch", "--show-current"], git_env) or ""))
    for ref in refs:
        resolved = _soft(repo_root, ["rev-parse", "--verify", ref + "^{commit}"], git_env)
        if resolved:
            return resolved
    default = _default_ref(repo_root, remote, git_env)
    resolved = _soft(repo_root, ["merge-base", head, default], git_env) if default else ""
    if resolved:
        return resolved
    fmt = _object_format(repo_root, git_env)
    if fmt not in EMPTY_TREE:
        raise RuntimeError("cannot resolve an empty base for git object format " + fmt)
    return EMPTY_TREE[fmt]


def _is_empty_base(repo_root, base, git_env=None):
    return _git_run.run(["cat-file", "-t", base], cwd=repo_root, timeout=None,
                        env=git_env).check(["cat-file"]).text.strip() == "tree"


def _outgoing(repo_root, base, head, remote="origin", git_env=None):
    """The revision range of commits `head` adds over `base` that `remote`'s default branch lacks."""
    ref = _default_ref(repo_root, remote, git_env)
    return [base + ".." + head] + (["^" + ref] if ref else [])


def close_paths(repo_root, base, head, remote="origin", git_env=None):
    """Every harness path changed by an outgoing commit the default branch lacks, including later
    reversions. A merge adds only the paths that differ from every parent, so a sync's merged-in
    commits, already closed on the default branch, add none."""
    if _is_empty_base(repo_root, base, git_env):
        out = _git_run.run(["ls-tree", "-r", "--name-only", head], cwd=repo_root, timeout=None,
                           env=git_env).check(["ls-tree"]).text
    else:
        log = ["log", "--format=", "--name-only", "--no-renames", "-c"] + _outgoing(repo_root, base, head, remote,
                                                                                    git_env)
        out = _git_run.run(log, cwd=repo_root, timeout=None, env=git_env).check(log).text
    return sorted({p for p in out.splitlines() if p.startswith(".claude/") or p in _SETUP_SURFACES})


def green_close(root):
    """The stored close record when it proved a green verdict with no reds, else None. `HARNESS_TEST_STAMP`
    selects another path; the default is `root/.claude/logs/harness_tests_stamp.json`."""
    path = os.environ.get("HARNESS_TEST_STAMP") or os.path.join(
        str(root), ".claude", "logs", "harness_tests_stamp.json")
    try:
        close = (read_json_salvage(path) or {}).get("close")
    except Exception:
        return None
    if not isinstance(close, dict) or close.get("verdict") != "green" or close.get("red"):
        return None
    return close


def close_covers(close, base, rev, paths, root, remote="origin", git_env=None):
    """True when `close` proved `rev` green at `base` (or `base` is the remote's default-branch commit `rev`
    contains) with every path of `paths` in its record."""
    if not close or close.get("head") != rev:
        return False
    recorded = close.get("base")
    if recorded != base and not default_base_covers(root, recorded, rev, remote, git_env):
        return False
    return set(paths) <= set(close.get("paths") or [])
