#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- deny a `git commit` that would lose a baseline lock row:
a dropped HEAD row whose file still exists (`_dropped_lock_rows`), or a repeated row key
(`_repeated_lock_rows`).

Why: a peer's temp-index commit moves HEAD but leaves the shared working-tree lock behind, so
committing that lock reverts the peer's rows. A new `.claude/` file needs no row: a path with no
row is unjudged, `candidates` lists it, and a `never_ship` match means `local` (X2 D1). So this
guard judges only what a commit would lose, never what it adds.

Scope: the invocations that publish the index -- `git commit`, and the `merge` / `cherry-pick` /
`revert` `--continue` that closes one. A bare `merge`/`cherry-pick`/`revert` only re-commits
content judged on its source ref. Commit detection reuses `_git_commit.commit_invocations`, so a
quoted mention (`git log --grep="git commit"`), a heredoc body, or an adjacent subcommand
(`git commit-graph write`) is never treated as a commit.

Target repository: resolved via `git rev-parse --show-toplevel` against the invocation's own
cwd (payload cwd + any `cd`/`-C` the command carries). The guard acts only when that top level
holds `.claude/baseline.lock.json` -- so a commit made inside a baseline author worktree under
`.claude/.cache/baseline-worktrees/<id>/` is never judged: that worktree mirrors the baseline
repo's own layout (`template/`, `tools/`, `tests/`), which has no `.claude/` at its root.

Fail-closed: `main()` catches every exception, including a `baseline.lock.json` the JSON
parser cannot read, and denies rather than allow an unverified lock through.
`pre_bash_dispatch.py` wraps the IMPORT of this module in its own try/except: a broken guard
that cannot even import registers a stub that denies every `git commit` and names the import
error, so a broken guard can never fail open.

No bypass variable -- nothing needs one: a dropped row has a documented home (`never_ship`).

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", via
`pre_bash_dispatch.HOOKS` before the guards that must judge its rewritten command.
"""
from __future__ import annotations

import json
from collections import Counter
import os
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_CLAUDE_DIR = os.path.dirname(_HOOKS_DIR)
_TOOLS_DIR = os.path.join(_CLAUDE_DIR, "tools")
for _p in (_HOOKS_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _git_commit import (base_rev, commit_invocations, parse_commit_args, resolve_index, run_git, staged_paths,
                         full_pathspec, unexpanded_hint)  # noqa: E402
from _commit_scope import publishes_index  # noqa: E402
import baseline_sync  # noqa: E402


def _repo_root_for(cwd: str) -> str | None:
    root = run_git(["rev-parse", "--show-toplevel"], cwd)
    return root.strip() if root else None


def _committed_lock_text(rest: list, cwd: str, root: str, env: dict | None = None):
    """`(text, None)`, `("", None)` when the commit does not carry the lock, or `(None, failing-step)`.
    The committed copy is the working tree's for `-a` or a pathspec naming the lock, else the index's."""
    committed, failed_cmd = staged_paths(rest, cwd, env)
    if committed is None:
        return None, failed_cmd
    lock = baseline_sync.LOCK_RELPATH
    if lock not in committed:
        return "", None
    include_dirty, _amend, pathspec = parse_commit_args(rest)
    pathspec, failed_spec = full_pathspec(rest, cwd, pathspec)
    if pathspec is None:
        return None, failed_spec
    base = base_rev(cwd, env)
    named = run_git(["diff", "--name-only", base, "--"] + pathspec, cwd, env) if pathspec else ""
    if named is None:
        return None, "diff --name-only %s -- %s" % (base, " ".join(pathspec))
    if include_dirty or lock in {ln.strip() for ln in named.splitlines()}:
        return (Path(root) / lock).read_text(encoding="utf-8"), None
    new_text = run_git(["show", ":" + lock], cwd, env)
    return (new_text, None) if new_text is not None else (None, "show :" + lock)


def _repeated_lock_rows(new_text: str) -> list:
    """Row keys the committed lock text holds more than once. `json.loads` keeps the last copy
    silently, so a text merge that re-adds a row a peer committed elsewhere passes every parsed check."""
    doc = json.loads(new_text, object_pairs_hook=list)
    files = next((value for key, value in doc if key == "files"), None)
    keys = [key for key, _value in files] if isinstance(files, list) else []
    return sorted(key for key, count in Counter(keys).items() if count > 1)


def _dropped_lock_rows(rest: list, cwd: str, root: str, env: dict | None = None):
    """`(rows, None) | (None, failing-step)` -- rows HEAD's lock holds that the committed lock
    lacks while their file still exists. A peer's temp-index commit moves HEAD but leaves the
    shared working-tree lock behind, so committing that lock reverts the peer's rows. A row whose
    file is gone left with its file, except a declined row (`absent`), which never has one.
    A declined row also left upstream when the same commit moves `synced_commit`: the engine is
    consuming a baseline publication that deleted the declined file. A `local` row under the
    committed lock's `never_ship` is project policy, not a decision a drop could revert."""
    new_text, failed_cmd = _committed_lock_text(rest, cwd, root, env)
    if new_text is None:
        return None, failed_cmd
    if new_text == "":
        return [], None
    lock = baseline_sync.LOCK_RELPATH
    head_text = run_git(["show", "HEAD:" + lock], cwd, env)
    if head_text is None:
        listed = run_git(["ls-tree", "--name-only", "HEAD", "--", lock], cwd, env)
        if not (listed or "").strip():
            return [], None  # no HEAD, or HEAD has no lock: nothing to drop
        return None, "show HEAD:" + lock
    head_doc, new_doc = json.loads(head_text), json.loads(new_text)
    head_rows, new_rows = head_doc.get("files") or {}, new_doc.get("files") or {}
    pin_moved = head_doc.get("synced_commit") != new_doc.get("synced_commit")
    never_ship = tuple(new_doc.get("never_ship") or ())

    def policy_local(path, entry):
        return isinstance(entry, dict) and entry.get("status") == "local" and path.startswith(never_ship)

    return sorted(p for p, entry in head_rows.items() if p not in new_rows and not policy_local(p, entry) and (
        (Path(root) / p).exists()
        or (isinstance(entry, dict) and entry.get("absent") and not pin_moved))), None


def _judge_commit(rest: list, cwd: str, env: dict | None = None) -> str | None:
    """The deny message for one `git commit` invocation, or `None` to let it through."""
    root = _repo_root_for(cwd)
    if root is None:
        return None  # not inside a git repository at all -- nothing to judge
    env, unreadable = resolve_index(env, cwd)
    if unreadable:
        return "BLOCKED git commit -- " + unreadable

    lock_path = Path(root) / baseline_sync.LOCK_RELPATH
    if not lock_path.is_file():
        return None  # not a baseline consumer repo (e.g. a baseline author worktree)

    try:
        baseline_sync.load_lock(Path(root))
    except (Exception, SystemExit) as exc:  # a v1 read helper `sys.exit`s on bad JSON
        return (
            "BLOCKED git commit -- .claude/baseline.lock.json could not be read "
            "(%s: %s). Fix the lock before committing." % (type(exc).__name__, exc)
        )

    lock_text, failed_cmd = _committed_lock_text(rest, cwd, root, env)
    repeated = _repeated_lock_rows(lock_text) if lock_text else []
    if repeated:
        lines = ["BLOCKED git commit -- baseline.lock.json holds these row keys more than once:"]
        lines.extend("  " + p for p in repeated)
        lines.append("A text merge re-added rows HEAD already holds. Keep one copy of each, then commit.")
        return "\n".join(lines)

    dropped, failed_cmd = _dropped_lock_rows(rest, cwd, root, env)
    if dropped is None:
        return (
            "BLOCKED git commit -- `git %s` failed; cannot verify baseline.lock.json keeps HEAD's rows."
            % failed_cmd + unexpanded_hint(failed_cmd)
        )
    if dropped:
        lines = ["BLOCKED git commit -- baseline.lock.json drops row(s) HEAD holds while the file exists:"]
        lines.extend("  " + p for p in dropped)
        lines.append("A peer likely committed them from a private index. Restore each row from "
                     "`git show HEAD:.claude/baseline.lock.json`, then commit.")
        return "\n".join(lines)

    return None


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
        if not publishes_index(invocation.sub, invocation.rest):
            continue
        reason = _judge_commit(invocation.rest, invocation.cwd, invocation.git_env)
        if reason:
            print(reason, file=sys.stderr)
            return 2
    return 0


# A floor guard: pre_bash_dispatch blocks the call when it crashes.
FAIL_CLOSED = True


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
