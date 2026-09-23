#!/usr/bin/env python3
"""Classify paths relative to the checkout's own `.claude/` root.

`harness_tail` canonicalizes separators and dot segments, removes any checkout
prefix (`.claude/worktrees/<checkout>/` or `.claude/.cache/<name>-worktrees/<checkout>/`,
e.g. `baseline-worktrees`), and returns the original-spelling
tail below that checkout's `.claude/`. It returns ``None`` for project paths
inside a worktree.
"""

from __future__ import annotations

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
