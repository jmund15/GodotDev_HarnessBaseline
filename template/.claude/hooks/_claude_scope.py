#!/usr/bin/env python3
"""Classify paths relative to the checkout's own `.claude/` root.

`harness_tail` canonicalizes separators and dot segments, removes any checkout
prefix (`.claude/worktrees/<checkout>/` or `.claude/.cache/<name>-worktrees/<checkout>/`,
e.g. `baseline-worktrees`), and returns the original-spelling
tail below that checkout's `.claude/`. It returns ``None`` for project paths
inside a worktree.
"""

from __future__ import annotations

import os
import re


_WORKTREE_RE = re.compile(
    r"(?:^|/)\.claude/(?:worktrees|\.cache/[\w.-]*worktrees)/[^/]+(?:/|$)",
    re.IGNORECASE,
)
_CLAUDE_RE = re.compile(r"(?:^|/)\.claude(?:/|$)", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def _canonicalize(norm: str) -> str:
    """Resolve separators and ``.``/``..`` without changing component spelling."""
    absolute = norm.startswith("/")
    stack: list[str] = []
    for component in norm.split("/"):
        if not component or component == ".":
            continue
        if component == "..":
            can_pop = bool(stack) and not (len(stack) == 1 and _DRIVE_RE.fullmatch(stack[0]))
            if can_pop:
                stack.pop()
            elif not absolute:
                stack.append(component)
            continue
        stack.append(component)
    result = "/".join(stack)
    return "/" + result if absolute else result


def normalize(path: str) -> str:
    """Return a canonical forward-slashed path while preserving component case."""
    return _canonicalize(str(path or "").replace("\\", "/"))


def in_nested_checkout(path: str) -> bool:
    """True when `path` lies inside a checkout nested under a `.claude/` root."""
    return bool(_WORKTREE_RE.search(normalize(path)))


def rebase(norm: str) -> str:
    """Drop the innermost checkout prefix: `.claude/worktrees/<checkout>/` or
    `.claude/.cache/<name>-worktrees/<checkout>/`."""
    norm = normalize(norm)
    while True:
        matches = list(_WORKTREE_RE.finditer(norm))
        if not matches:
            return norm
        norm = norm[matches[-1].end():]


def harness_tail(path: str) -> str | None:
    """Return the path below `.claude/`, or ``None`` outside the harness.

    Classification is case-insensitive for Windows paths. The returned tail
    retains the caller's component spelling so hook messages remain useful.
    """
    norm = rebase(path)
    matches = list(_CLAUDE_RE.finditer(norm))
    if not matches:
        return None
    return norm[matches[-1].end():]


def is_harness_path(path: str) -> bool:
    """True below `.claude/` and for any `CLAUDE.md` or `MEMORY.md` file at any depth, case-insensitively."""
    return harness_tail(path) is not None or rebase(path).rsplit("/", 1)[-1].lower() in ("claude.md", "memory.md")


_EXECUTABLE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".sh", ".ps1")
# Records and working notes: a `.json` here is read by no tool as configuration.
_RECORD_DIRS = ("plans/", "scratch/", "logs/")


def is_executable_harness_path(path: str) -> bool:
    """True for a harness path that runs or configures what runs: a script or proof (`.py`, `.js`, `.mjs`,
    `.cjs`, `.sh`, `.ps1`); a `settings*.json`; and any `.json` in a subfolder other than plans, scratch and logs,
    since a tool reads it as data that changes what runs (a schema, the adaptation seam, a registry). The other
    `.json` files at the `.claude/` root are ledgers tools write (the baseline lock, verdicts, archives)."""
    tail = harness_tail(path)
    if tail is None:
        return False
    tail = tail.lower()
    if tail.endswith(_EXECUTABLE_SUFFIXES):
        return True
    if not tail.endswith(".json"):
        return False
    if "/" not in tail:
        return tail.startswith("settings")
    return not tail.startswith(_RECORD_DIRS)


def is_prose_harness_scope(paths) -> bool:
    """True for a non-empty scope whose every path is a harness path that does not run. The review ledger's
    candidate exemption and triage's `candidate-lenses` width share this one predicate."""
    paths = list(paths)
    return bool(paths) and all(is_harness_path(p) and not is_executable_harness_path(p) for p in paths)


def is_cloud() -> bool:
    """True in a Claude Code cloud session (CLAUDE_CODE_REMOTE set to 1, true or yes)."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() in ("1", "true", "yes")
