#!/usr/bin/env python3
"""Shared reader for `.claude/skills/project_subsystems/adaptation.json` (Design Doc §8,
plan `sync-baseline-v2.md`).

`SKILL.md` in the same folder holds the canonical subsystem registry; this file holds the
other adaptation tables the design routes through `project_subsystems` -- read-only commands,
watched submodules, high-risk prompt patterns, drive commands, teardown helpers, the test
root, the Integration quarantine filter, memory domains, paired repos, the rule file each one's commit path follows (`paired_repo_rules`), content domains and
scopes, structure exceptions, PR domains, content nouns, the work-triage tag registry
(`sensitivity_tags`, read by `tools/triage.py`), the citation checker's code extensions and
vocabulary words (`source_extensions`, `symbol_stopwords`, read by `tools/verify_doc_citations.py`), the rule-text scope and row-file location (`instruction_scope`, read by `tools/extract_numbers.py` and `tools/restate_scan.py`), the review mandate catalog and
ledger path scope (`review_mandates`, `review_scope`, read by `tools/review_ledger.py` and `tools/escape_log.py`), the proof runner's excluded/timeout tables and the directories a project proof scans whole (`proof_corpus`, {proof: [dir, ...]}), the absolute document roots the project reads outside the repo (`doc_roots`, re-injected after compaction by `hooks/compact_directive_anchor.py`), and the mandates a later review round seats for added facts and changed seams (`later_round_roles`, {stage: {grounding, coherence: {meta, code}}}, read by `tools/post_check.py`, which reports `roles: omitted` for a stage with no row), the key prefix a registry's lens headings share (`lens_prefixes`, {registry: prefix}, read by `tools/lens.py`; a registry with no row takes every heading), the doctrine files a project attaches to a lens beyond its header `preload:` lines (`lens_preloads`, {lens key: [path relative to `.claude/`]}, read by `tools/lens.py`). The baseline tracks only this file's default seed (manifest `sync: seed`);
each consumer's copy is `local` and never published.

Framework data the shipped tools need lives beside it in `adaptation.<layer>.defaults.json`
(`pure`, `coding`, `godot`; manifest `sync: auto`, so a pull delivers it to every consumer that adopted
the layer). `load()` reads `DEFAULTS`, then each defaults file present on disk in that layer order, then
`adaptation.json`: a list is the earlier files' items followed by the later file's with exact
duplicates dropped, a dict merges key by key by these same rules (so a list nested in a dict key, such
as a `lens_preloads` entry, also unions, and a project cannot drop a defaults item), and a scalar takes
the last file that sets it. The godot defaults file holds `index_prebuild` (`list[str]`, default `[]`), the
commands `/reindex_search` Step 3 runs. Command line:
  - `python3 .claude/tools/adaptation.py get <key>` prints a list one item per line, a dict as one JSON
    line and a scalar as is; an unknown key exits 2.
  - `python3 .claude/tools/adaptation.py run <key>` runs each entry of a list key once from the repo
    root as an argv list (`shlex.split`, no shell), prints each command's output, reports a failing
    entry and continues, and exits 0; a non-list key exits 2.

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

`bench_liveness` supplies the installation root, logs provider and process patterns/labels
read by `scripts/sync_plan_config.py`; an absent key means no project benchmark extras.
`rigor_defaults` maps project classes to path globs and levels, read by `tools/triage.py`.
`usage_reset` is the weekly quota reset ({weekday, hour}, machine-local), read by `tools/usage_report.py`.
`land_prefix` is the branch prefix a project's land runner proves and fast-forwards onto the default branch
(empty: no runner), read by `hooks/git_guardrails.py` and `/commit_push` step 6.

Unknown keys are ignored everywhere.
"""
from __future__ import annotations

import json
import shlex
import subprocess
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
    "paired_repo_rules": {},
    "content_domains": [],
    "content_scopes": [],
    "structure_exceptions": [],
    "pr_domains": [],
    "content_nouns": [],
    "proof_excluded": {},
    "proof_heavy": {},
    "proof_serial": {},
    "proof_timeouts": {},
    "proof_corpus": {},
    "review_mandates": {},
    "review_scope": {},
    "sensitivity_tags": [],
    "source_extensions": [],
    "symbol_stopwords": [],
    "instruction_scope": {"paths": [], "rows_dir": None},
    "doc_roots": [],
    "bench_liveness": {},
    "later_round_roles": {},
    "lens_prefixes": {},
    "lens_preloads": {},
    "index_prebuild": [],
    "usage_reset": {"weekday": "sunday", "hour": 13},
    "land_prefix": "",
    "rigor_defaults": {
        "harness": {"paths": [".claude/**"], "level": "normal"},
        "side-tools": {"paths": ["tools/**", "scripts/**", ".claude/tools/**", ".claude/scripts/**"], "level": "light"},
    },
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
    "paired_repo_rules": dict,
    "content_domains": list,
    "content_scopes": list,
    "structure_exceptions": list,
    "pr_domains": list,
    "content_nouns": list,
    "proof_excluded": dict,
    "proof_heavy": dict,
    "proof_serial": dict,
    "proof_timeouts": dict,
    "proof_corpus": dict,
    "review_mandates": dict,
    "review_scope": dict,
    "sensitivity_tags": list,
    "source_extensions": list,
    "symbol_stopwords": list,
    "instruction_scope": dict,
    "doc_roots": list,
    "bench_liveness": dict,
    "later_round_roles": dict,
    "lens_prefixes": dict,
    "lens_preloads": dict,
    "index_prebuild": list,
    "usage_reset": dict,
    "land_prefix": str,
    "rigor_defaults": dict,
}


def adaptation_path(claude_dir) -> Path:
    """`<claude_dir>/skills/project_subsystems/adaptation.json`."""
    return Path(claude_dir) / "skills" / "project_subsystems" / "adaptation.json"


_CACHE: dict = {}

LAYER_ORDER = ("pure", "coding", "godot")


def _adaptation_files(claude_dir) -> list:
    """Candidate files in read order: each layer's defaults file, then `adaptation.json`."""
    skill_dir = Path(claude_dir) / "skills" / "project_subsystems"
    return [skill_dir / f"adaptation.{layer}.defaults.json" for layer in LAYER_ORDER] + [adaptation_path(claude_dir)]


def _stat_key(path: Path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def load(claude_dir) -> dict:
    """Every known key at its default, overridden by the layer defaults files and `adaptation.json`
    that are present and parseable, merged in read order (module docstring). An absent file
    contributes nothing, silently. An unparseable file, non-object root, or a known key of the wrong
    type contributes nothing for the affected key(s) plus one stderr line each, naming the file.
    Unknown keys are ignored.

    Cached per (path, mtime, size) of every candidate file so a process that reads several keys via
    `get()` re-parses the files and re-prints a malformed-value warning once, not once per key
    requested; a rewritten, added or removed file invalidates the cache and reloads."""
    files = _adaptation_files(claude_dir)
    cache_key = tuple(_stat_key(path) for path in files)
    if cache_key in _CACHE:
        return dict(_CACHE[cache_key])

    result = dict(DEFAULTS)
    seen: dict = {}
    for path in files:
        for key, value in _read_file(path).items():
            seen[key] = _merge_value(seen[key], value) if key in seen else value
    result.update(seen)
    _CACHE[cache_key] = dict(result)
    return result


def _merge_value(earlier, later):
    if isinstance(later, list) and isinstance(earlier, list):
        return earlier + [item for item in later if item not in earlier]
    if isinstance(later, dict) and isinstance(earlier, dict):
        merged = dict(earlier)
        for key, value in later.items():
            merged[key] = _merge_value(merged[key], value) if key in merged else value
        return merged
    return later


def _read_file(path: Path) -> dict:
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"adaptation: {path} is unparseable -- using defaults", file=sys.stderr)
        return {}

    if not isinstance(raw, dict):
        print(f"adaptation: {path} is not a JSON object -- using defaults", file=sys.stderr)
        return {}

    values: dict = {}
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
        values[key] = value
    return values


def get(claude_dir, key):
    """One key, defaulted the same way `load()` defaults it."""
    return load(claude_dir).get(key, DEFAULTS.get(key))


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("get", "run"):
        print("usage: adaptation.py get|run <key>", file=sys.stderr)
        return 2
    verb, key = args
    claude_dir = Path(__file__).resolve().parent.parent
    if key not in DEFAULTS:
        print(f"adaptation: unknown key '{key}'", file=sys.stderr)
        return 2
    value = get(claude_dir, key)
    if verb == "get":
        if isinstance(value, list):
            for item in value:
                print(item)
        elif isinstance(value, dict):
            print(json.dumps(value))
        else:
            print(value)
        return 0
    if not isinstance(value, list):
        print(f"adaptation: key '{key}' is not a list", file=sys.stderr)
        return 2
    for entry in value:
        try:
            done = subprocess.run(shlex.split(entry), cwd=str(claude_dir.parent), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            print(f"adaptation: {entry} failed to start: {exc}", file=sys.stderr)
            continue
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        if done.returncode != 0:
            print(f"adaptation: {entry} failed (exit {done.returncode})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
