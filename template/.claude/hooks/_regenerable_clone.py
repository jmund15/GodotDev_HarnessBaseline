#!/usr/bin/env python3
"""Is a recursive-delete target a regenerable git clone under `.claude/scratch/`, or a retired
worktree under `.claude/worktrees/` (`is_retired_worktree`)?

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


_GENERATED_UNTRACKED_SUFFIXES = (".import",)
_REGENERABLE_IGNORED_SEGMENTS = frozenset({".godot", "__pycache__", ".pytest_cache", ".search-index", ".cache",
                                           "TestResults"})
_REGENERABLE_IGNORED_PAIRS = frozenset({(".claude", "cache"), (".claude", "logs")})


def _regenerable_ignored(entry: str) -> bool:
    parts = entry.split("/")
    return (bool(_REGENERABLE_IGNORED_SEGMENTS & set(parts))
            or any(pair in _REGENERABLE_IGNORED_PAIRS for pair in zip(parts, parts[1:])))


def _pushed(repo: Path) -> bool:
    out = _git(["rev-list", "HEAD", "--not", "--remotes"], repo)
    return out is not None and not out.strip()


def _registered_unlocked(listing: str, target: Path) -> bool:
    for block in listing.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        try:
            path = Path(lines[0][len("worktree "):]).resolve(strict=True)
        except OSError:
            continue
        if _same_path(path, target):
            return not any(line == "locked" or line.startswith("locked ") for line in lines)
    return False


def is_retired_worktree(token: str, cwd: str, project_root: str) -> bool:
    """True only when `token` names a linked worktree of `project_root`, directly under
    `<project>/.claude/worktrees/`, whose deletion loses nothing.

    `git worktree remove` refuses every worktree carrying a submodule, so a retired one can only go
    by a recursive delete followed by `git worktree prune`. The worktree must be registered and
    unlocked; its status may hold only untracked Godot `.import` files and ignored regenerable caches
    (`_REGENERABLE_IGNORED_SEGMENTS`, `_REGENERABLE_IGNORED_PAIRS`), so ignored scratch evidence or
    local config blocks the delete; its HEAD and every populated
    submodule's HEAD must be reachable from a remote-tracking ref, and each submodule must be clean.
    """
    if not token or _UNSAFE_PATH_CHARS.search(token):
        return False
    parts = [p for p in token.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return False
    raw = Path(token) if os.path.isabs(token) else Path(cwd) / token
    try:
        target = raw.resolve(strict=True)
        root = Path(project_root).resolve(strict=True)
        home = (root / ".claude" / "worktrees").resolve(strict=True)
    except OSError:
        return False
    if not _same_path(target.parent, home) or not (target / ".git").is_file():
        return False

    listing = _git(["worktree", "list", "--porcelain"], root)
    if listing is None or not _registered_unlocked(listing, target):
        return False
    status = _git(["status", "--porcelain", "--untracked-files=all", "--ignored=matching",
                   "--ignore-submodules=none"], target)
    if status is None:
        return False
    for line in status.splitlines():
        entry = line[3:].rstrip("/")
        if line.startswith("?? ") and entry.endswith(_GENERATED_UNTRACKED_SUFFIXES):
            continue
        if line.startswith("!! ") and _regenerable_ignored(entry):
            continue
        return False
    if not _pushed(target):
        return False

    submodules = _git(["submodule", "status", "--recursive"], target)
    if submodules is None:
        return False
    for line in submodules.splitlines():
        fields = line[1:].split()
        if len(fields) < 2:
            return False
        sub = target / fields[1]
        if not (sub / ".git").exists():
            continue
        sub_status = _git(["status", "--porcelain", "--untracked-files=all"], sub)
        if sub_status is None or sub_status.strip() or not _pushed(sub):
            return False
    return True
