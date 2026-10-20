#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: correct a semantic-search call whose own values settle the right one (a subdirectory searchDir,
a non-canonical restrictToDir inside its index), and block unauthorized external roots.

The plugin stores its index under `searchDir` (`<searchDir>/.search-index/search.db`) and reads
`.gitignore` from that directory down. A subdirectory `searchDir` misses the repo-root `.gitignore`
(which excludes `.claude/worktrees/` and `.claude/scratch/`) and indexes every ignored checkout; a
root outside the project needs explicit authorization. Valid roots are the project, a submodule or
worktree inside it, and the project's `doc_roots` (`adaptation.json`).
`restrictToDir` is a prefix filter over stored
repo-relative POSIX paths, so an absolute, backslash or `.`/`..`/empty-segment value matches
nothing and reads as a false "not indexed". Owner: `reference/semantic_search.md`; evidence:
`auto-memory/gotcha_semantic_search_restricttodir_posix.md`.

Output contract: `process(payload)` returns `{"deny": reason}`, `{"rewrite": tool_input, "note": text}` or
None. `pre_read_dispatch.py`, and this module's `main()` for tests, writes a deny to stderr and exits 2,
and emits a rewrite as `updatedInput` with the note as `additionalContext`.

Fail posture: a decided violation fails CLOSED. A malformed or non-dict payload, or one missing the
inspected fields, returns None without raising -- unknown state is not evidence of a violation.
"""

import os
import posixpath
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import adaptation  # noqa: E402

TOOL_NAME = "mcp__plugin_semantic-search_semantic-search__search"
OWNER = ".claude/reference/semantic_search.md"
# The design-source roots are the project's own `doc_roots` (adaptation.json).
DESIGN_ROOTS = tuple(
    str(root).replace("\\", "/")
    for root in adaptation.get(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "doc_roots")
)


def _project_root(payload) -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or str(payload.get("cwd") or ".")


def _resolve(raw: str, root: str) -> str:
    """Resolve a searchDir value against `root` the way the tool call itself would."""
    p = str(raw).replace("\\", "/")
    if not (p.startswith("/") or (len(p) >= 2 and p[1] == ":")):
        p = os.path.join(root, p)
    return os.path.normpath(p)


def _is_git_top_level(path: str) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


def _is_within(path: str, root: str) -> bool:
    """True when `path` is `root` or below it, compared by real path so a link out does not pass."""
    try:
        real_root = os.path.normcase(os.path.realpath(root))
        real_path = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([real_root, real_path]) == real_root
    except (ValueError, OSError):
        return False


def _is_same_path(path: str, root: str) -> bool:
    """True when `path` and `root` are the same real path."""
    try:
        return os.path.normcase(os.path.realpath(path)) == os.path.normcase(os.path.realpath(root))
    except (ValueError, OSError):
        return False


def _is_restrict_bad(raw: str) -> bool:
    s = str(raw)
    if "\\" in s or s.startswith("/"):
        return True
    if len(s) >= 2 and s[1] == ":":  # drive letter, e.g. C:
        return True
    # Stored keys are canonical, so a `.`, `..` or empty segment never prefixes one. One trailing
    # slash still prefixes the same keys.
    return any(seg in ("", ".", "..") for seg in s.rstrip("/").split("/"))


def _canonical_restrict(raw, root, prefix):
    """The repo-relative POSIX restrictToDir for `raw` under the subdirectory `prefix`; "" for the whole
    project; None when it leaves the project. A trailing slash is kept: it narrows the prefix match."""
    s = str(raw or "").replace("\\", "/")
    if s.startswith("/") or (len(s) >= 2 and s[1] == ":"):
        full = os.path.normpath(s)
        if not _is_within(full, root):
            return None
        rel = os.path.relpath(full, root).replace(os.sep, "/")
    else:
        rel = posixpath.join(prefix, s) if prefix else s
    norm = posixpath.normpath(rel) if rel else "."
    if norm == ".." or norm.startswith("../"):
        return None
    if norm == ".":
        return ""
    return norm + ("/" if s.endswith("/") else "")


def process(payload):
    """{"deny": reason} for a call whose fix is a choice (an index root outside the project, or a
    restrictToDir that leaves it); {"rewrite": tool_input, "note": text} for one its own values settle
    (a subdirectory searchDir becomes the root plus that restrictToDir; a non-canonical restrictToDir
    inside the project is normalized); else None."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != TOOL_NAME:
        return None

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    root = _project_root(payload)
    updated, prefix = dict(tool_input), ""

    search_dir = tool_input.get("searchDir")
    if search_dir:
        resolved = _resolve(search_dir, root)
        inside = _is_within(resolved, root)
        design_root = next((path for path in DESIGN_ROOTS if _is_same_path(resolved, path)), None)
        if design_root:
            root = resolved
        elif not inside:
            return {
                "deny": (
                    f"BLOCKED (reference/semantic_search.md): semantic-search searchDir '{search_dir}' resolves to "
                    f"'{resolved}', which is outside the project root. "
                    f"Call again with searchDir = the project root ({root}) and scope the query "
                    "with a repo-relative POSIX restrictToDir, e.g. '.claude/auto-memory'."
                )
            }
        # A submodule or nested worktree inside the root is its own index root; any other
        # subdirectory is the project's index narrowed to that prefix.
        if not (_is_same_path(resolved, root) or _is_git_top_level(resolved)):
            prefix = os.path.relpath(resolved, root).replace(os.sep, "/")
            updated["searchDir"] = root

    restrict_dir = tool_input.get("restrictToDir")
    if prefix or (restrict_dir and _is_restrict_bad(restrict_dir)):
        fixed = _canonical_restrict(restrict_dir, root, prefix)
        if fixed is None:
            return {
                "deny": (
                    f"BLOCKED (CLAUDE.md §2 Memory): semantic-search restrictToDir '{restrict_dir}' leaves the "
                    f"project. Call again with searchDir = the project root ({root}) and restrictToDir like "
                    f"'.claude/auto-memory'. Owner: {OWNER}."
                )
            }
        if fixed:
            updated["restrictToDir"] = fixed
        else:
            updated.pop("restrictToDir", None)

    if updated == tool_input:
        return None
    return {"rewrite": updated,
            "note": "[semantic-search] corrected the call to searchDir = the index root, restrictToDir = '%s' "
                    "(reference/semantic_search.md)." % updated.get("restrictToDir", "")}


def main() -> None:
    import json
    import sys

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        result = process(payload)
    except Exception:
        sys.exit(0)

    if result and result.get("deny"):
        sys.stderr.write(str(result["deny"]) + "\n")
        sys.exit(2)
    if result and result.get("rewrite"):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": result["rewrite"],
                                                 "additionalContext": result["note"]}}))
    sys.exit(0)


if __name__ == "__main__":
    main()
