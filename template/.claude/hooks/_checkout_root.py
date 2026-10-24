#!/usr/bin/env python3
"""The one answer to "where is the main checkout" and "is this directory a linked worktree".

`main_checkout_root(start)` is the directory holding the repository's `.git` directory, whichever
checkout `start` lies in; `start` itself when git cannot answer or the common dir is not a `.git`
directory (a submodule's gitdir). `linked_main_root(start)` is that root only when `start` is in a linked
worktree, else "". CLI: `_checkout_root.py <dir>` prints `linked_main_root` in posix form.
"""
import subprocess
import sys
from pathlib import Path


def _git_dirs(start):
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                             cwd=start, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=10, check=True).stdout.splitlines()
    except (OSError, TypeError, subprocess.SubprocessError):
        return None
    return (Path(out[0].strip()).resolve(), Path(out[1].strip()).resolve()) if len(out) == 2 else None


def main_checkout_root(start):
    dirs = _git_dirs(start)
    if dirs and dirs[1].name == ".git":
        return str(dirs[1].parent)
    return str(start)


def linked_main_root(start):
    dirs = _git_dirs(start)
    return str(dirs[1].parent) if dirs and dirs[0] != dirs[1] and dirs[1].name == ".git" else ""


if __name__ == "__main__":
    root = linked_main_root(sys.argv[1])
    print(Path(root).as_posix() if root else "")
