#!/usr/bin/env python3
"""The one answer to "where is the main checkout" and "is this directory a linked worktree".

`main_checkout_root(start)` is the directory holding the repository's `.git` directory, whichever
checkout `start` lies in; `start` itself when git cannot answer or the common dir is not a `.git`
directory (a submodule's gitdir). `linked_main_root(start)` is that root only when `start` is in a linked
worktree, else "". `worktree_chain(start, rev_parse)` yields each working-tree root from `start` outward
through its superprojects, nearest first (a submodule inside a linked worktree yields both).
`session_checkout(payload)` is the checkout a hook payload's `cwd` lies in, the root that
repository-relative paths resolve against. `repo_arg(argv)` pops a `--repo <path>` pair from an argv list and returns
that checkout resolved, or None. `harness_root(start)` is the harness root a run belongs to (below). CLI: `_checkout_root.py <dir>` prints
`linked_main_root` in posix form.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import _git_run


def git_dirs(start):
    """(git-dir, git-common-dir) resolved for `start`, or None when git cannot answer."""
    args = ["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"]
    try:
        out = _git_run.run(args, cwd=start, timeout=10).check(args).text.splitlines()
    except (OSError, TypeError, subprocess.SubprocessError):
        return None
    return (Path(out[0].strip()).resolve(), Path(out[1].strip()).resolve()) if len(out) == 2 else None


def main_checkout_root(start):
    dirs = git_dirs(start)
    if dirs and dirs[1].name == ".git":
        return str(dirs[1].parent)
    return str(start)


def linked_main_root(start):
    dirs = git_dirs(start)
    return str(dirs[1].parent) if dirs and dirs[0] != dirs[1] and dirs[1].name == ".git" else ""


def worktree_chain(start, rev_parse):
    """Each working-tree root from `start` outward through its superprojects, nearest first. `rev_parse(flag, cwd)`
    returns git's stdout for `git rev-parse <flag>` at `cwd`, or None when git cannot answer; the chain is read
    lazily, so a caller that matches the nearest root spawns no call for the ones above it."""
    current = rev_parse("--show-toplevel", start)
    while current and current.strip():
        root = current.strip()
        yield root
        current = rev_parse("--show-superproject-working-tree", root)


def payload_cwd(payload):
    """The hook payload's working directory as an absolute forward-slash path, or "" when it is missing,
    relative or holds a `..` segment (review_fanout.js refuses such a `fold.repoRoot`)."""
    cwd = payload.get("cwd")
    root = cwd.strip().replace("\\", "/").rstrip("/") if isinstance(cwd, str) else ""
    if not re.match(r"^([A-Za-z]:)?/", root) or re.search(r"(^|/)\.\.(/|$)", root):
        return ""
    return root


def _git_line(cwd, *argv):
    try:
        done = _git_run.run(["-C", cwd, *argv], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.text.strip().replace("\\", "/").rstrip("/") if done.returncode == 0 else ""


def session_checkout(payload):
    """The checkout the payload cwd belongs to, absolute with forward slashes: the superproject working tree
    when the cwd sits inside a submodule, else the cwd's own top level (a linked worktree is its own root).
    "" when git cannot answer. Repository-relative paths resolve against this, never the raw cwd: the session's
    shell drifts into subdirectories."""
    cwd = payload_cwd(payload)
    if not cwd:
        return ""
    return _git_line(cwd, "rev-parse", "--show-superproject-working-tree") or _git_line(cwd, "rev-parse", "--show-toplevel")


def harness_root(start=None):
    """The harness root a run belongs to: the nearest ancestor of `start` (default: the cwd) that holds `.claude/`,
    which is the git toplevel in most projects and a directory below it in a nested one (the baseline's own
    `template/`). The walk stops at the directory holding `.git`, so a `.claude/` above the repository (a user's
    `~/.claude`) never answers for it; `start` itself when none is found. A shell's cwd drifts into subdirectories,
    so a tool whose default root is the cwd calls this, never `Path.cwd()`."""
    here = Path(start or os.getcwd()).resolve()
    for found in (here, *here.parents):
        if (found / ".claude").is_dir():
            return str(found)
        if (found / ".git").exists():
            break
    return str(here)


def repo_arg(argv):
    """Pop `--repo <path>` from `argv` in place; the resolved path, or None when the flag is absent."""
    if "--repo" not in argv:
        return None
    i = argv.index("--repo")
    path = Path(argv.pop(i + 1)).resolve()
    argv.pop(i)
    return path


if __name__ == "__main__":
    root = linked_main_root(sys.argv[1])
    print(Path(root).as_posix() if root else "")
