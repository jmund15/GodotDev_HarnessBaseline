"""The memory-domain table, one reader for the surface that matches against it.

A row is `{name, paths, memory_keywords, skills, rules}`: the rows of `adaptation.json` `memory_domains`
(`reference/memory_domains.md` renders the same rows). A later row of the same name replaces an earlier one; a key
outside this list (a retired `triggers`) is ignored. A malformed row is skipped with one stderr line, so a bad seed
never crashes a hook.

`by_paths(paths)`: a plan declares the files it edits, so a row owns a file by its `paths` globs
(`plan_memory_reminder`). A row with no `paths` never matches a plan. Globs are gitignore-style and case-sensitive
(a `Tests/` glob is not the harness's `tests/`): `**` spans directories, `*` and `?` stay inside one segment, and a
glob may match from any directory of the path, so `Jmodot/**` finds `C:/repo/Jmodot/Core/a.cs`.
"""

import os
import re
import sys

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
import adaptation  # noqa: E402

CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LISTS = ("paths", "memory_keywords", "skills", "rules")


def load(claude_dir=CLAUDE_DIR):
    """The project's rows, each with every list field present."""
    rows = []
    for row in adaptation.get(claude_dir, "memory_domains") or []:
        if not isinstance(row, dict) or "name" not in row:
            print(f"memory_domains row {row!r} is missing 'name' -- skipped", file=sys.stderr)
            continue
        row = {key: row.get(key, []) for key in ("name",) + LISTS}
        if not all(isinstance(row[key], list) for key in LISTS):
            print(f"memory_domains row {row['name']!r} has a non-list field -- skipped", file=sys.stderr)
            continue
        rows = [kept for kept in rows if kept["name"] != row["name"]] + [row]
    return rows


def _glob(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def owns(pattern, path):
    """True when glob `pattern` matches `path` from any of its directories."""
    path = path.replace("\\", "/")
    rx = _glob(pattern.replace("\\", "/"))
    starts = [0] + [m.end() for m in re.finditer("/", path)]
    return any(rx.match(path[start:]) for start in starts)


def by_paths(paths, rows=None):
    """The rows owning at least one of `paths`, each with the paths it owns: [(row, [path, ...])], table order."""
    found = []
    for row in (load() if rows is None else rows):
        mine = [p for p in paths if any(owns(glob, p) for glob in row["paths"])]
        if mine:
            found.append((row, mine))
    return found
