#!/usr/bin/env python3
"""Shared reader for `.claude/skills/project_subsystems/adaptation.json` (Design Doc §8,
plan `sync-baseline-v2.md`).

`SKILL.md` in the same folder holds the canonical subsystem registry; this file holds the
other adaptation tables the design routes through `project_subsystems` -- read-only commands,
watched submodules, high-risk prompt patterns, drive commands, teardown helpers, the test
root, the Integration quarantine filter, memory domains, paired repos, content domains and
scopes, structure exceptions, PR domains, content nouns, and the proof runner's excluded/
timeout tables. The baseline tracks only this file's default seed (manifest `sync: seed`);
each consumer's copy is `local` and never published.

Two contracts share this loader:
  - advisory hook/script consumers (every row below except `classify`/`compose`/`publish`)
    want the lenient behavior `load()` gives: an absent file or key silently returns the
    default, and a malformed file or a known key of the wrong type returns the default plus
    one stderr line naming the file (per-VALUE validation -- an individual list/dict entry
    that fails its own shape check -- is each consumer's own job, since the shape differs
    per key);
  - `baseline_sync.py`'s `classify`, `compose` and `publish` enforce the harder contract
    directly (exit 1, naming the key) when the lock exists and the file or the `subsystems`
    YAML block is absent, unparseable, or a known key is wrong-typed; they do not call
    `load()` for that check, because a hook must never block and these three must.

Unknown keys are ignored everywhere.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULTS: dict = {
    "read_only_commands": [],
    "git_submodules": [],
    "high_risk_patterns": [],
    "drive_commands": [],
    "teardown_helpers": [],
    "tests_root": "Tests",
    "test_quarantine_filter": "",
    "memory_domains": [],
    "paired_repos": [],
    "content_domains": [],
    "content_scopes": [],
    "structure_exceptions": [],
    "pr_domains": [],
    "content_nouns": [],
    "proof_excluded": {},
    "proof_timeouts": {},
}

# Expected Python type for each known key. `bool` is excluded from `list`/`dict` checks
# below because `isinstance(True, int)` and friends can otherwise slip a bool past a
# scalar check that never applies here, but the guard costs nothing to state.
_TYPES: dict = {
    "read_only_commands": list,
    "git_submodules": list,
    "high_risk_patterns": list,
    "drive_commands": list,
    "teardown_helpers": list,
    "tests_root": str,
    "test_quarantine_filter": str,
    "memory_domains": list,
    "paired_repos": list,
    "content_domains": list,
    "content_scopes": list,
    "structure_exceptions": list,
    "pr_domains": list,
    "content_nouns": list,
    "proof_excluded": dict,
    "proof_timeouts": dict,
}


def adaptation_path(claude_dir) -> Path:
    """`<claude_dir>/skills/project_subsystems/adaptation.json`."""
    return Path(claude_dir) / "skills" / "project_subsystems" / "adaptation.json"


_CACHE: dict = {}


def load(claude_dir) -> dict:
    """Every known key at its default, overridden by `adaptation.json` when present, parseable
    and the right type. Absent file: defaults, no message. Unparseable file, non-object root,
    or a known key of the wrong type: defaults for the affected key(s) plus one stderr line
    each, naming the file. Unknown keys are ignored.

    Cached per (path, mtime, size) so a process that reads several keys via `get()` re-parses
    the file and re-prints a malformed-value warning once, not once per key requested; a
    rewritten file (different mtime or size) invalidates the cache and reloads."""
    path = adaptation_path(claude_dir)
    try:
        st = path.stat()
        cache_key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        cache_key = (str(path), None, None)
    if cache_key in _CACHE:
        return dict(_CACHE[cache_key])

    result = _load_uncached(path)
    _CACHE[cache_key] = dict(result)
    return result


def _load_uncached(path: Path) -> dict:
    result = dict(DEFAULTS)
    if not path.is_file():
        return result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"adaptation: {path} is unparseable -- using defaults", file=sys.stderr)
        return result

    if not isinstance(raw, dict):
        print(f"adaptation: {path} is not a JSON object -- using defaults", file=sys.stderr)
        return result

    for key, expected in _TYPES.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, expected):
            print(
                f"adaptation: {path} key '{key}' is wrong-typed (want {expected.__name__}) "
                "-- using default",
                file=sys.stderr,
            )
            continue
        result[key] = value
    return result


def get(claude_dir, key):
    """One key, defaulted the same way `load()` defaults it."""
    return load(claude_dir).get(key, DEFAULTS.get(key))
