#!/usr/bin/env python3
"""Arm the read-only fan-out marker that `readonly_lens_write_guard.py` reads.

WHY THIS LIVES ON THE `Workflow` MATCHER, not in the engines. The marker has to be
written by something that knows the session id, can reach the filesystem, and has a
clock. A Workflow script has none of the three -- the sandbox has no `require`, no
Node API, and `Date.now()` throws (stated in the engines themselves at
`dispatch.js:20`, `explore_fanout.js:39`, `review_fanout.js:23`). The layer that DOES
have all three, without depending on any agent remembering an instruction, is a
PreToolUse hook on the `Workflow` call itself. Wiring the commands with `Bash` calls
was the alternative and was rejected: a marker written by a markdown instruction is
armed only when the agent remembers, which is not a mechanical guarantee.

WHAT IT ARMS ON. Only a dispatch whose script is a read-only fan-out ENGINE: a script
file carrying the `// READONLY-FANOUT-ENGINE` line. Those engines inject a per-lens
`Read-only: do NOT modify...` contract and are the surface whose read-only claim this
guard exists to observe. A `dispatch.js` run is NOT armed: it mixes read-only and
author jobs in one run, so arming it would warn on every legitimate authored write.

THE FOLD EXCEPTION. A `review_fanout.js` run with `args.fold` ends in a fold stage that writes claims and the
digest under the scratch prefixes below, then edits the files of each `args.fold.subjects[]` entry: its
`files` that exist, and its declared `newPaths`, each under the subject's `root`. Only files join
`allow_prefixes`, so a seat's write anywhere else, a directory's other files included, still warns.

NO DISARM, BY DESIGN. `Workflow` returns as soon as the run is backgrounded, so a
PostToolUse disarm would clear the marker while the lenses are still running. The TTL
is the disarm instead -- which is also what makes a killed orchestrator harmless
(`readonly_lens_write_guard.py` treats an expired marker as absent and unlinks it).

FAIL-OPEN. The whole body sits in one `try/except` that exits 0, and every field is
read with `.get()`. The matcher covers every `Workflow`/`Agent` dispatch in every
future session; a crash here must never block one. Not arming costs one advisory
warning; blocking a dispatch costs the session.
"""
import json
import os
import re
import sys
import time

from _hook_state import read_json_salvage, write_json_atomic

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
TTL_SECONDS = 1800

# The line a read-only fan-out engine declares itself with. `dispatch.js` deliberately lacks
# it -- see module docstring.
ENGINE_MARKER = "// READONLY-FANOUT-ENGINE"


def _marker_path(session_id: str) -> str:
    """Mirror of readonly_lens_write_guard._marker_path -- same root, same <sid8> key."""
    short = session_id[:8] if session_id else "default"
    return os.path.join(STATE_DIR, f"readonly-{short}.json")


def _names_readonly_engine(tool_input: dict, cwd: str) -> bool:
    """True when this dispatch runs a script file that declares ENGINE_MARKER.

    `scriptPath` is the normal route. `name` covers a saved-workflow invocation that names
    a script file. A relative path resolves against the payload `cwd`, then the project
    root. The inline `script` body is NOT inspected: an ad-hoc script that merely mentions
    the marker is not a dispatch of an engine.
    """
    roots = [r for r in (cwd, os.environ.get("CLAUDE_PROJECT_DIR")) if r]
    for key in ("scriptPath", "name"):
        value = str(tool_input.get(key) or "")
        if not value.endswith(".js"):
            continue
        for path in [value] if os.path.isabs(value) else [os.path.join(r, value) for r in roots]:
            try:
                with open(path, encoding="utf-8") as handle:
                    if any(line.strip() == ENGINE_MARKER for line in handle):
                        return True
            except OSError:
                continue
    return False


def _allow_prefixes() -> list:
    """Where a read-only lens is legitimately allowed to write.

    Spill files and per-lens mandate files land under the session scratchpad (below
    the OS temp `claude/` tree) or the in-repo scratch dir. Everything else -- source,
    scenes, plans, docs -- is what the guard reports on.
    """
    prefixes = []
    for var in ("TEMP", "TMP", "TMPDIR"):
        base = os.environ.get(var)
        if base:
            prefixes.append(os.path.join(base, "claude"))
            break
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    if project:
        prefixes.append(os.path.join(project, ".claude", "scratch"))
    return prefixes


def _is_repo_relative(path: str) -> bool:
    """True for a repository-relative path: not empty, not absolute, not drive-lettered and not escaping with `..`."""
    return bool(path) and not os.path.isabs(path) and not re.match(r"^[A-Za-z]:", path) \
        and not re.search(r"(^|[\\/])\.\.([\\/]|$)", path)


def _subject_file(root: str, rel: str, cwd: str, must_exist: bool) -> str:
    """The absolute file `rel` names under the subject `root`, or "" when it is not a file the fold may write.

    An existing file qualifies. A declared new path qualifies when nothing but a missing file stands there.
    A directory never does: the guard matches an allow entry as a prefix, so it would open every write under it.
    """
    if not _is_repo_relative(root) or not _is_repo_relative(rel) or rel.endswith(("/", "\\")):
        return ""
    roots = [r for r in (cwd, os.environ.get("CLAUDE_PROJECT_DIR")) if r]
    for base in roots:
        path = os.path.join(base, root, rel)
        if os.path.isfile(path):
            return os.path.abspath(path)
        if os.path.isdir(path):
            return ""
    if must_exist or not roots:
        return ""
    return os.path.abspath(os.path.join(roots[0], root, rel))


def _fold_subject_files(tool_input: dict, cwd: str) -> list:
    """The files `review_fanout.js`'s fold stage may edit: each subject's existing `files` and declared `newPaths`."""
    args = tool_input.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return []
    fold = args.get("fold") if isinstance(args, dict) else None
    subjects = fold.get("subjects") if isinstance(fold, dict) else None
    if not isinstance(subjects, list):
        return []
    found = []
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        root = str(subject.get("root") or ".")
        for field, must_exist in (("files", True), ("newPaths", False)):
            listed = subject.get(field) if isinstance(subject.get(field), list) else []
            found += [p for p in (_subject_file(root, str(item or ""), cwd, must_exist) for item in listed) if p]
    return found


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        return
    if payload.get("tool_name") != "Workflow":
        return

    tool_input = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or "")
    if not isinstance(tool_input, dict) or not _names_readonly_engine(tool_input, cwd):
        return

    session_id = str(payload.get("session_id") or "")
    marker = _marker_path(session_id)
    allow = _allow_prefixes()
    allow.extend(_fold_subject_files(tool_input, cwd))
    state = {
        "readonly": True,
        "expires_at": int(time.time()) + TTL_SECONDS,
        "allow_prefixes": allow,
    }
    write_json_atomic(marker, state)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail-open; never block a dispatch, never fail silently
        sys.stderr.write(f"[readonly-marker-arm] exited fail-open: {exc}\n")
    sys.exit(0)
