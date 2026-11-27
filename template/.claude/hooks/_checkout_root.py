#!/usr/bin/env python3
"""The one answer to "where is the main checkout" and "is this directory a linked worktree".

`layout(start)` is the one question all of them ask git: the git dir, the common dir, `start`'s prefix below its
toplevel and the superproject's work tree, from one `rev-parse`. `git_dirs`, `main_checkout_root` and
`linked_main_root` take an `ask` that answers it, so a tool that holds a command's answer passes its own.
`main_checkout_root(start)` is the directory holding the repository's `.git` directory, whichever
checkout `start` lies in; `start` itself when git cannot answer or the common dir is not a `.git`
directory (a submodule's gitdir). `linked_main_root(start)` is that root only when `start` is in a linked
worktree, else "". `worktree_chain(start, rev_parse)` yields each working-tree root from `start` outward
through its superprojects, nearest first (a submodule inside a linked worktree yields both).
`session_checkout(payload)` is the checkout a hook payload's `cwd` lies in, the root that
repository-relative paths resolve against. `repo_arg(argv)` pops a `--repo <path>` pair from an argv list and returns
that checkout resolved, or None. `nearest_checkout(path)` is the checkout a path lies in, found without git.
`harness_root(start)` is the harness root a run belongs to (below). CLI: `_checkout_root.py <dir>` prints
`linked_main_root` in posix form.
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import _git_run


class Layout(NamedTuple):
    git_dir: str  # absolute, as git prints it
    common_dir: str  # absolute, as git prints it
    prefix: str  # `start` below its toplevel, "" or ending in "/"
    superproject: str  # the superproject's work tree when `start` is in a submodule, else ""


LAYOUT_ARGS = ["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir", "--show-prefix",
               "--show-superproject-working-tree"]


def layout(start, timeout=10):
    """The repository `start` lies in, or None when git answers that it lies in none. Raises the launcher's
    OSError or SubprocessError when git cannot run, so each caller keeps its own failure answer."""
    done = _git_run.run(LAYOUT_ARGS, cwd=start, timeout=timeout)
    lines = done.text.splitlines() if done.returncode == 0 else []
    if len(lines) < 2:
        return None
    lines += ["", ""]
    return Layout(*(line.strip() for line in lines[:4]))


def git_dirs(start, ask=layout):
    """(git-dir, git-common-dir) resolved for `start`, or None when git cannot answer."""
    try:
        found = ask(start)
    except (OSError, TypeError, subprocess.SubprocessError):
        return None
    return (Path(found.git_dir).resolve(), Path(found.common_dir).resolve()) if found else None


def main_checkout_root(start, ask=layout):
    dirs = git_dirs(start, ask)
    if dirs and dirs[1].name == ".git":
        return str(dirs[1].parent)
    return str(start)


def linked_main_root(start, ask=layout):
    dirs = git_dirs(start, ask)
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


def nearest_checkout(path):
    """The nearest ancestor of `path` (made absolute, itself included) holding a `.git` file or folder, or None. No git
    spawn: for a caller that needs only which checkout a path lies in, never its layout."""
    here = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


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
