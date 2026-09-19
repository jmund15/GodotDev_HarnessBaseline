#!/usr/bin/env python3
"""Is a recursive-delete target a regenerable git clone under `.claude/scratch/`?

`pattern_enforcer.py` blocks every recursive delete outside its cache allowlist, and it keeps
`.claude/scratch` blocked on purpose: scratch holds evidence. A throwaway clone a session made
there (a probe of a remote repo) is not evidence. It is regenerable exactly when everything in it
already exists on a remote. This module proves that from the target itself, and fails closed: any
git error, missing input or ambiguous shape is "not regenerable".

A target qualifies only when all of these hold:
- the token is a literal path: no glob, variable, `~` or `..`;
- it resolves (following links) strictly inside `<project>/.claude/scratch/`, never scratch itself;
- it has a `.git` DIRECTORY (a standalone clone, not a worktree's `.git` file) and is its own
  top level;
- the clone has exactly one worktree, at least one remote, an empty `git status --porcelain`,
  an empty `git stash list`, and no branch or tag commit unreachable from a remote-tracking ref.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_UNSAFE_PATH_CHARS = re.compile(r"[*?\[\]{}$~%]")
_GIT_TIMEOUT_S = 10


def _git(args: list[str], cwd: Path) -> str | None:
    """stdout of `git <args>` run in `cwd`, or None on any failure (fail closed).

    Read-only by construction:
    - `--no-optional-locks` stops `status` from taking the index lock to refresh it. A timeout kill
      would otherwise leave a stale `index.lock`.
    - `GIT_CEILING_DIRECTORIES` stops git from climbing into an enclosing repository when the
      target's `.git` disappears mid-check; git would otherwise lock and judge the project repo.
    """
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(cwd.parent)}
    try:
        proc = subprocess.run(["git", "--no-optional-locks", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=_GIT_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def is_regenerable_clone(token: str, cwd: str, project_root: str) -> bool:
    """True only when `token` names a clean, fully pushed standalone clone under scratch."""
    if not token or _UNSAFE_PATH_CHARS.search(token):
        return False
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return False
    raw = Path(token) if os.path.isabs(token) else Path(cwd) / token
    try:
        target = raw.resolve(strict=True)
        scratch = (Path(project_root) / ".claude" / "scratch").resolve(strict=True)
    except OSError:
        return False
    if _same_path(target, scratch) or not any(_same_path(p, scratch) for p in target.parents):
        return False
    if not (target / ".git").is_dir():
        return False

    top = _git(["rev-parse", "--show-toplevel"], target)
    if top is None:
        return False
    try:
        if not _same_path(Path(top.strip()).resolve(strict=True), target):
            return False
    except OSError:
        return False

    worktrees = _git(["worktree", "list", "--porcelain"], target)
    if worktrees is None or sum(1 for line in worktrees.splitlines() if line.startswith("worktree ")) != 1:
        return False
    remotes = _git(["remote"], target)
    if not remotes or not remotes.strip():
        return False
    for args in (["status", "--porcelain"], ["stash", "list"],
                 ["rev-list", "--branches", "--tags", "--not", "--remotes"]):
        out = _git(args, target)
        if out is None or out.strip():
            return False
    return True
