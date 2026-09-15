#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- deny a `git commit` that adds a `.claude/` file with
no `baseline.lock.json` row.

Why: `baseline_sync.py`'s `candidates` sweep finds an unclassified addition only after it
already shipped, so an unreviewed universal-shaped file can sit unclassified and drift from
the baseline it should have joined. Classifying AT CREATION ties every `.claude/` addition to
a status decision the moment it becomes committable (Design Doc §4, plan `sync-baseline-v2.md`).

Scope: `git commit` only -- a `merge`/`cherry-pick`/`revert` is judged by the `git commit` that
follows its `--no-commit` staging, per `_git_commit.py`'s own contract. Commit detection reuses
`_git_commit.commit_invocations`, so a quoted mention (`git log --grep="git commit"`), a heredoc
body, or an adjacent subcommand (`git commit-graph write`) is never treated as a commit.

Added-file scope: `git diff --cached --name-only --no-renames --diff-filter=A`, run under the
commit's pathspec when one is given without `-i`/`--include` (so a pathspec commit is judged
only on what it actually publishes), intersected with `_git_commit.staged_paths` -- the same
seam the S4 fix corrected -- so a file merely staged by another session never gets attributed
to this commit. `--no-renames` makes a `git mv` into `.claude/` read as an addition of the new
path, which is exactly the shape this guard must catch.

Target repository: resolved via `git rev-parse --show-toplevel` against the invocation's own
cwd (payload cwd + any `cd`/`-C` the command carries). The guard acts only when that top level
holds `.claude/baseline.lock.json` -- so a commit made inside a baseline author worktree under
`.claude/.cache/baseline-worktrees/<id>/` is never judged: that worktree mirrors the baseline
repo's own layout (`template/`, `tools/`, `tests/`), which has no `.claude/` at its root.

Fail-closed: `main()` catches every exception, including a `baseline.lock.json` the JSON
parser cannot read, and denies rather than allow an unclassified file through un-verified.
`pre_bash_dispatch.py` wraps the IMPORT of this module in its own try/except: a broken guard
that cannot even import registers a stub that denies every `git commit` and names the import
error, so a broken guard can never fail open.

No bypass variable -- the S9 migration classifies every row before its own commit runs, so
nothing needs one.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", via
`pre_bash_dispatch.HOOKS` directly after `git_guardrails`.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
_TOOLS_DIR = os.path.join(_CLAUDE_DIR, "tools")
for _p in (_HOOKS_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _git_commit import commit_invocations, parse_commit_args, run_git, staged_paths  # noqa: E402
import baseline_sync  # noqa: E402

CLASSIFY_HINT = (
    "Classify first: python3 .claude/tools/baseline_sync.py classify <relpath> "
    "--status tracked|local|forked|composed [--from <source-relpath>] [--inputs <relpath>,...]"
)


def _repo_root_for(cwd: str) -> str | None:
    root = run_git(["rev-parse", "--show-toplevel"], cwd)
    return root.strip() if root else None


def _excluded(relpath: str) -> bool:
    if any(relpath.startswith(prefix) for prefix in baseline_sync.CANDIDATE_EXCLUDE_PREFIXES):
        return True
    name = relpath.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in baseline_sync.CANDIDATE_EXCLUDE_NAMES)


def _added_claude_paths(rest: list, cwd: str):
    """`(paths, None) | (None, failing-git-subcommand)` -- staged `.claude/` additions this
    commit will actually publish, minus the engine's own candidate exclusions."""
    _include_dirty, _amend, pathspec = parse_commit_args(rest)
    include_flag = any(a in ("-i", "--include") for a in rest)
    args = ["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=A"]
    if pathspec and not include_flag:
        args = args + ["--"] + pathspec
    out = run_git(args, cwd)
    if out is None:
        return None, " ".join(args)
    added = {ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()}

    committed, failed_cmd = staged_paths(rest, cwd)
    if committed is None:
        return None, failed_cmd

    scoped = sorted(
        p for p in added
        if p in committed and p.startswith(".claude/") and not _excluded(p)
    )
    return scoped, None


def _judge_commit(rest: list, cwd: str) -> str | None:
    """The deny message for one `git commit` invocation, or `None` to let it through."""
    root = _repo_root_for(cwd)
    if root is None:
        return None  # not inside a git repository at all -- nothing to judge

    lock_path = Path(root) / baseline_sync.LOCK_RELPATH
    if not lock_path.is_file():
        return None  # not a baseline consumer repo (e.g. a baseline author worktree)

    try:
        lock = baseline_sync.load_lock(Path(root))
    except (Exception, SystemExit) as exc:  # a v1 read helper `sys.exit`s on bad JSON
        return (
            "BLOCKED git commit -- .claude/baseline.lock.json could not be read "
            "(%s: %s). Fix the lock before committing." % (type(exc).__name__, exc)
        )

    added, failed_cmd = _added_claude_paths(rest, cwd)
    if added is None:
        return (
            "BLOCKED git commit -- `git %s` failed; cannot verify .claude/ classification."
            % failed_cmd
        )
    if not added:
        return None

    files = lock.get("files") or {}
    unclassified = [p for p in added if p not in files]
    if not unclassified:
        return None

    lines = ["BLOCKED git commit -- new .claude/ file(s) have no baseline.lock.json row:"]
    lines.extend("  " + p for p in unclassified)
    lines.append(CLASSIFY_HINT)
    return "\n".join(lines)


def _run() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or "."

    for invocation in commit_invocations(command, cwd):
        if invocation.sub != "commit":
            continue
        reason = _judge_commit(invocation.rest, invocation.cwd)
        if reason:
            print(reason, file=sys.stderr)
            return 2
    return 0


def main() -> int:
    try:
        return _run()
    except (Exception, SystemExit) as exc:  # fail CLOSED -- an unhandled crash must not allow
        print(
            "BLOCKED git commit -- baseline_classification_guard crashed (%s: %s). "
            "Fix the guard before committing to .claude/." % (type(exc).__name__, exc),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
