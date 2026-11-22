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
ledger path scope (`review_mandates`, `review_scope`, read by `tools/review_ledger.py` and `tools/escape_log.py`), the proof runner's excluded/timeout tables and the directories a project proof scans whole (`proof_corpus`, {proof: [dir, ...]}), the absolute document roots the project reads outside the repo (`doc_roots`, re-injected after compaction by `hooks/compact_directive_anchor.py`), the key prefix a registry's lens headings share (`lens_prefixes`, {registry: prefix}, read by `tools/lens.py`; a registry with no row keeps its literal prefix, and review's literal is empty, so it takes every heading), the doctrine files a project attaches to a lens beyond its header `preload:` lines (`lens_preloads`, {lens key: [path relative to `.claude/`]}, read by `tools/lens.py`). The baseline tracks only this file's default seed (manifest `sync: seed`);
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
`usage_bench_markers` is the list of folder-name fragments that mark a benchmark run's project folder, read by
`tools/usage_report.py` so the report credits those runs to `bench` rather than `other` (empty: no project benchmark runs).
`land_prefix` is the branch prefix whose pushes a project's land runner integrates and fast-forwards onto the
default branch, with no proof run (empty: no runner), read by `hooks/git_guardrails.py` and `/commit_push` step 6.
`main_check` names the workflow that proves the default branch after each push (empty: the project has none), read by
`hooks/git_guardrails.py`: a project that declares one needs no local close before a harness push.
`land_watch` is the command `/commit_push` step 6 starts after a land push to wait on the land and report it
(`<land branch>` and `<pushed sha>` filled in; empty: no watcher).
`regenerable_ignored` lists the project's ignored build outputs that a delete of an orphaned or retired worktree may
discard: paths from the worktree root, `fnmatch` patterns, a listed directory covering everything under it; read by
`hooks/_regenerable_clone.py` from the project root it judges for (empty: only the shared caches).
`gate_cadence` is a job's cadence data: `slice`, `merge` and `close` are command forms a rails' `Your checks` line
prints verbatim, and `wide` holds one row per wide runner (invocation regex, exempt arguments, optional narrow rule,
optional `hint` the deny prints as the narrow form to write instead). `reach` ({name, paths: [glob], cmd}),
`reach_exempt` (globs) and `reach_fallback` (the full form) are the table `tools/reach_run.py` runs a slice from;
`reach` and `reach_exempt` union across layers, so the harness layer's `.claude/**` entry reaches every consumer.
`gate_cadence_errors` validates the merged table; `hooks/_dispatch_checks.py` and `hooks/wide_test_guard.py`
read it through `hooks/_wide_runs.py`, `tools/guard_text.py` reads `slice`, `merge` and `close` directly for
its `Your checks` line, and a malformed row fails `get gate_cadence` (exit 1).

`drift_scan` is the config of `tools/drift_scan.py` ({sources, consumers, min_length, ignore}; empty: not configured).

`gate_cadence` is a job's cadence data: `slice`, `merge` and `close` are command forms a rails' `Your checks` line
prints verbatim, and `wide` holds one row per wide runner (invocation regex, exempt arguments, optional narrow rule,
optional `hint` the deny prints as the narrow form to write instead). `reach` ({name, paths: [glob], cmd}),
`reach_exempt` (globs) and `reach_fallback` (the full form) are the table `tools/reach_run.py` runs a slice from;
`reach` and `reach_exempt` union across layers, so the harness layer's `.claude/**` entry reaches every consumer.
`gate_cadence_errors` validates the merged table; `hooks/_dispatch_checks.py` and `hooks/wide_test_guard.py`
read it through `hooks/_wide_runs.py`, `tools/guard_text.py` reads `slice`, `merge` and `close` directly for
its `Your checks` line, and a malformed row fails `get gate_cadence` (exit 1).
`drift_scan` is the config of `tools/drift_scan.py` ({sources, consumers, min_length, ignore}; empty: not configured).
`gate_cadence.main` ({command: argv list, retry_exits: [int], outputs: [path]}) is the gate `scripts/main_check.py` runs on the default
branch after a land (absent: the project runs none); exit 0 passes, exit 1 fails and names each failed test as a
`FAILED_TEST=<id>` stdout line, an exit in `retry_exits` is rerun once, any other exit is no verdict, and `outputs` are
paths the gate writes, which owe no gate when a range changes only them and harness paths.

Unknown top-level keys are ignored; the `gate_cadence` table rejects its own unknown keys (`gate_cadence_errors`).
"""
from __future__ import annotations

import json
import re
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
    "lens_prefixes": {},
    "lens_preloads": {},
    "index_prebuild": [],
    "usage_reset": {"weekday": "sunday", "hour": 13},
    "usage_bench_markers": [],
    "land_prefix": "",
    "main_check": "",
    "land_watch": "",
    "regenerable_ignored": [],
    "gate_cadence": {"slice": [], "merge": [], "close": [], "wide": []},
    "drift_scan": {},
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
    "lens_prefixes": dict,
    "lens_preloads": dict,
    "index_prebuild": list,
    "usage_reset": dict,
    "usage_bench_markers": list,
    "land_prefix": str,
    "main_check": str,
    "land_watch": str,
    "regenerable_ignored": list,
    "gate_cadence": dict,
    "drift_scan": dict,
    "rigor_defaults": dict,
}


CADENCE_KEYS = ("slice", "merge", "close", "wide", "reach", "reach_exempt", "reach_fallback", "main")


def _regex_error(pattern, where: str, one_group: bool = False):
    """The error for a value that must be a compiling regex, or [] when it is one."""
    if not isinstance(pattern, str) or not pattern.strip():
        return ["%s is not a non-empty string" % where]
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return ["%s does not compile: %s" % (where, exc)]
    if one_group and compiled.groups != 1:
        return ["%s needs exactly one capturing group, has %d" % (where, compiled.groups)]
    return []


def _cadence_list(value, key: str) -> list:
    rows = value.get(key, [])
    if not isinstance(rows, list):
        return ["%s is not a list" % key]
    return ["%s[%d] is not a non-empty string" % (key, index)
            for index, row in enumerate(rows) if not isinstance(row, str) or not row.strip()]


def _wide_row_errors(index: int, row) -> list:
    where = "wide[%d]" % index
    if not isinstance(row, dict):
        return ["%s is not an object" % where]
    errors = []
    unknown = sorted(set(row) - {"runner", "exempt", "narrow", "hint"})
    if unknown:
        errors.append("%s has unknown keys %s" % (where, ", ".join(unknown)))
    if "hint" in row and (not isinstance(row["hint"], str) or not row["hint"].strip()):
        errors.append("%s.hint is not a non-empty string" % where)
    errors += _regex_error(row.get("runner"), where + ".runner")
    exempt = row.get("exempt", [])
    if not isinstance(exempt, list):
        errors.append("%s.exempt is not a list" % where)
    else:
        for at, pattern in enumerate(exempt):
            errors += _regex_error(pattern, "%s.exempt[%d]" % (where, at))
    narrow = row.get("narrow")
    if narrow is None:
        return errors
    if not isinstance(narrow, dict):
        return errors + ["%s.narrow is not an object or null" % where]
    if set(narrow) not in ({"requires"}, {"declared"}):
        return errors + ["%s.narrow needs exactly one of requires|declared" % where]
    if "requires" in narrow:
        terms = narrow["requires"]
        if not isinstance(terms, list) or not terms:
            return errors + ["%s.narrow.requires is not a non-empty list" % where]
        for at, pattern in enumerate(terms):
            errors += _regex_error(pattern, "%s.narrow.requires[%d]" % (where, at))
        return errors
    declared = narrow["declared"]
    if not isinstance(declared, dict):
        return errors + ["%s.narrow.declared is not an object" % where]
    unknown = sorted(set(declared) - {"arg", "glob", "decl", "admit"})
    if unknown:
        errors.append("%s.narrow.declared has unknown keys %s" % (where, ", ".join(unknown)))
    for field in ("arg", "glob"):
        if not isinstance(declared.get(field), str) or not declared[field].strip():
            errors.append("%s.narrow.declared.%s is not a non-empty string" % (where, field))
    decl = declared.get("decl")
    if not isinstance(decl, list) or not decl:
        errors.append("%s.narrow.declared.decl is not a non-empty list" % where)
    else:
        for at, pattern in enumerate(decl):
            errors += _regex_error(pattern, "%s.narrow.declared.decl[%d]" % (where, at), one_group=True)
    admit = declared.get("admit")
    if not isinstance(admit, list):
        errors.append("%s.narrow.declared.admit is not a list" % where)
    else:
        for at, pattern in enumerate(admit):
            errors += _regex_error(pattern, "%s.narrow.declared.admit[%d]" % (where, at))
    return errors


def _reach_errors(value) -> list:
    errors = []
    rows = value.get("reach", [])
    if not isinstance(rows, list):
        return ["reach is not a list"]
    for index, row in enumerate(rows):
        where = "reach[%d]" % index
        if not isinstance(row, dict):
            errors.append("%s is not an object" % where)
            continue
        unknown = sorted(set(row) - {"name", "paths", "cmd"})
        if unknown:
            errors.append("%s has unknown keys %s" % (where, ", ".join(unknown)))
        for field in ("name", "cmd"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append("%s.%s is not a non-empty string" % (where, field))
        paths = row.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(g, str) and g.strip() for g in paths):
            errors.append("%s.paths is not a non-empty list of globs" % where)
    exempt = value.get("reach_exempt", [])
    if not isinstance(exempt, list) or not all(isinstance(g, str) and g.strip() for g in exempt):
        errors.append("reach_exempt is not a list of globs")
    fallback = value.get("reach_fallback", "")
    if not isinstance(fallback, str):
        errors.append("reach_fallback is not a string")
    return errors


def _main_errors(value) -> list:
    if "main" not in value:
        return []
    main = value["main"]
    if not isinstance(main, dict):
        return ["main is not an object"]
    errors = []
    unknown = sorted(set(main) - {"command", "retry_exits", "outputs"})
    if unknown:
        errors.append("main has unknown keys %s" % ", ".join(unknown))
    command = main.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(a, str) and a.strip() for a in command):
        errors.append("main.command is not a non-empty argv list of strings")
    exits = main.get("retry_exits", [])
    if not isinstance(exits, list) or not all(isinstance(e, int) and not isinstance(e, bool) for e in exits):
        errors.append("main.retry_exits is not a list of integers")
    outputs = main.get("outputs", [])
    if not isinstance(outputs, list) or not all(isinstance(o, str) and o.strip() for o in outputs):
        errors.append("main.outputs is not a list of paths")
    return errors


def gate_cadence_errors(value) -> list:
    """Shape errors of a merged `gate_cadence` value; [] when it is valid.
    Per-VALUE validation is each consumer's own job (`load()` stays lenient); this function is that job for the
    cadence table."""
    if not isinstance(value, dict):
        return ["is not an object"]
    errors = []
    unknown = sorted(set(value) - set(CADENCE_KEYS))
    if unknown:
        errors.append("has unknown keys %s" % ", ".join(unknown))
    for key in ("slice", "merge", "close"):
        errors += _cadence_list(value, key)
    errors += _reach_errors(value)
    errors += _main_errors(value)
    wide = value.get("wide", [])
    if not isinstance(wide, list):
        errors.append("wide is not a list")
    else:
        for index, row in enumerate(wide):
            errors += _wide_row_errors(index, row)
    return errors


def adaptation_path(claude_dir) -> Path:
    """`<claude_dir>/skills/project_subsystems/adaptation.json`."""
    return Path(claude_dir) / "skills" / "project_subsystems" / "adaptation.json"


_CACHE: dict = {}

LAYER_ORDER = ("pure", "coding", "godot")  # keep in step with baseline_compose.settings_overlay_files; generalize that walk when a third consumer appears


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
    if verb == "get" and key == "gate_cadence":
        errors = gate_cadence_errors(value)
        if errors:
            for error in errors:
                print("adaptation: gate_cadence %s" % error, file=sys.stderr)
            return 1
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
