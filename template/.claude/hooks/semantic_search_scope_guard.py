#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: block a semantic-search call whose index root is outside the project or is not a git
top-level, or whose restrictToDir cannot match the index's stored paths.

The plugin stores its index under `searchDir` (`<searchDir>/.search-index/search.db`) and reads
`.gitignore` from that directory down. A subdirectory `searchDir` misses the repo-root `.gitignore`
(which excludes `.claude/worktrees/` and `.claude/scratch/`) and indexes every ignored checkout; a
root outside the project indexes another tree. The project root, or a submodule or worktree root
inside it, is the only valid `searchDir`. `restrictToDir` is a prefix filter over stored
repo-relative POSIX paths, so an absolute, backslash or `.`/`..`/empty-segment value matches
nothing and reads as a false "not indexed". Owner: `reference/semantic_search.md`; evidence:
`auto-memory/gotcha_semantic_search_restricttodir_posix.md`.

Output contract: `process(payload)` returns `{"deny": reason}` or None. `pre_read_dispatch.py`, and
this module's `main()` for tests, writes the reason to stderr and exits 2 on a deny.

Fail posture: a decided violation fails CLOSED. A malformed or non-dict payload, or one missing the
inspected fields, returns None without raising -- unknown state is not evidence of a violation.
"""

import os

TOOL_NAME = "mcp__plugin_semantic-search_semantic-search__search"
OWNER = ".claude/reference/semantic_search.md"


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


def process(payload):
    """Return {"deny": reason} for an out-of-scope semantic-search call, else None."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != TOOL_NAME:
        return None

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    root = _project_root(payload)

    search_dir = tool_input.get("searchDir")
    if search_dir:
        resolved = _resolve(search_dir, root)
        inside = _is_within(resolved, root)
        # `root` is already the trusted boundary (CLAUDE_PROJECT_DIR or cwd); only a searchDir
        # that names some OTHER directory inside it (a submodule or nested worktree) needs its
        # own `.git` to prove it is a genuine repo boundary and not an arbitrary subdirectory.
        is_root_itself = inside and _is_same_path(resolved, root)
        if not (inside and (is_root_itself or _is_git_top_level(resolved))):
            where = ("is outside the project root" if not inside
                     else "is not a git top-level (no .git file or directory)")
            return {
                "deny": (
                    f"BLOCKED: semantic-search searchDir '{search_dir}' resolves to '{resolved}', "
                    f"which {where}. The index is built under searchDir and reads .gitignore only "
                    "from there, so any other root indexes the wrong tree.\n"
                    f"Call again with searchDir = the project root ({root}) and scope the query "
                    "with a repo-relative POSIX restrictToDir, e.g. '.claude/auto-memory'. "
                    f"Owner: {OWNER}."
                )
            }

    restrict_dir = tool_input.get("restrictToDir")
    if restrict_dir and _is_restrict_bad(restrict_dir):
        return {
            "deny": (
                f"BLOCKED: semantic-search restrictToDir '{restrict_dir}' is not a canonical "
                "repo-relative POSIX path (absolute, backslash, or a `.`, `..` or empty segment). "
                "It filters the index's stored repo-relative paths by prefix, so this value "
                "silently matches nothing.\n"
                f"Call again with searchDir = the project root ({root}) and restrictToDir like "
                f"'.claude/auto-memory'. Owner: {OWNER}."
            )
        }

    return None


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
    sys.exit(0)


if __name__ == "__main__":
    main()
