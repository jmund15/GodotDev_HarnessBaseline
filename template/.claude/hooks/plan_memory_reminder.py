#!/usr/bin/env python3
"""
Sub-hook of post_edit_dispatch.py: at a plan write, name the memory and skills of the domains the plan's files fall in.

Class: advisory. It never blocks.

Why: `CLAUDE.core.md` *Memory* asks for a memory search and the relevant skills before planning, and a routine plan
gets no plan check (`drive_spine.md` §Sizing). The plan file is the one artifact every planning path writes, so the
write is the moment to say which domains the plan enters.

Decision: the files the plan declares, never its words. The declared set is the write-set cells of the plan's
`## Job map` table (`plan_file_format.md` requires it: one row per slice, its write set), read through `_job_map`;
`.claude/` paths are not scope. `_memory_domains.by_paths` returns the `adaptation.json` `memory_domains` rows whose
`paths` globs own a declared file. A plan that declares no owned file (a harness plan, a plan whose Job map has no
write-set column, a path under `.claude/`, a row with no `paths`) is silent.

Output: one imperative context line naming the domains and the files that placed them, the skills not yet loaded in
this session, the memory search terms and the rules to read. It fires once per session and domain set. The plan is
read from disk, so a section edit sees the files section it did not rewrite.

Wired in: post_edit_dispatch.py CHAIN (PostToolUse Write|Edit).
"""

import re
from pathlib import Path

import _job_map
import _memory_domains
from _claude_scope import harness_tail
from _hook_state import state_path, read_json_salvage, fire_once

# A path in a write-set cell is a slashed token ending in a file extension or a slash, or a bare file with a lettered
# extension. Prose such as `e.g.`, `read/write`, `0.1.36` and `3.5` ("their tests") is not a path.
_PATH_LIKE_RE = re.compile(
    r"(?<![\w.*{}~+@:/\\-])(?:[A-Za-z]:)?(?:"
    r"(?:[\w.*{}~+@:-]+[/\\])+[\w*{}~+@:-]*\.[A-Za-z][\w-]{0,15}"
    r"|(?:[\w.*{}~+@:-]+[/\\])+(?![\w.*{}~+@:-])"
    r"|[A-Za-z_][\w*{}~+@-]*\.[A-Za-z][A-Za-z0-9]{1,9}\b)",
    re.IGNORECASE,
)


def _is_plan(file_path):
    path = str(file_path or "").replace("\\", "/")
    return ".claude/plans/" in path and path.lower().endswith(".md")


def declared_files(plan_text):
    """The project paths in the plan's Job map write-set cells, in first-seen order. A path under `.claude/` is the
    harness's, never a domain's, whatever a glob would match."""
    paths = [p for cell in _job_map.write_set_cells(plan_text) for p in _PATH_LIKE_RE.findall(cell.replace("\\", "/"))]
    return [p for p in dict.fromkeys(paths) if harness_tail(p) is None]


def _joined(values):
    return list(dict.fromkeys(v for value in values for v in value))


def build_reminder(matches, loaded=()):
    """`matches` is `_memory_domains.by_paths` output."""
    names = ", ".join(row["name"] for row, _ in matches)
    files = ", ".join(_joined(paths[:2] for _, paths in matches))
    parts = [f"Plan edits files in {names} ({files}). Before implementing:"]
    skills = [s for s in _joined(row["skills"] for row, _ in matches) if s not in loaded]
    if skills:
        parts.append(f"load skills {', '.join(skills)};")
    terms = _joined(row["memory_keywords"] for row, _ in matches)
    if terms:
        parts.append(f"search auto-memory for: {' / '.join(terms)};")
    rules = _joined(row["rules"] for row, _ in matches)
    if rules:
        parts.append(f"read rules {', '.join(rules)};")
    return " ".join(parts).rstrip(";") + "."


def process(input_data):
    """Dispatcher entry: `{"context": text}` for a plan whose declared files a domain row owns, else None."""
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None
    file_path = (input_data.get("tool_input") or {}).get("file_path")
    if not _is_plan(file_path):
        return None
    try:
        plan = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _memory_domains.by_paths(declared_files(plan))
    if not matches:
        return None
    session_id = input_data.get("session_id") or ""
    if not fire_once(session_id, "plan_memory_reminder:" + ",".join(sorted(row["name"] for row, _ in matches))):
        return None
    loaded = read_json_salvage(state_path(session_id)).get("skills_loaded") or []
    return {"context": build_reminder(matches, loaded)}
