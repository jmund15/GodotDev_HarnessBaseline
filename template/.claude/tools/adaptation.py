#!/usr/bin/env python3
"""Reader for `.claude/skills/project_subsystems/adaptation.json`, the one project-config file.

Every key has a default in `DEFAULTS`, and the comment above it names the readers. A key the file sets
replaces its default when it has the right type; a wrong-typed value keeps the default and prints one
stderr line naming the file. An absent file, an unparseable file and an unknown key are not errors.

Command line (run from the repo root):
  - `python3 .claude/tools/adaptation.py get <key>` prints a list one item per line, a dict as one JSON
    line and a scalar as is. A dotted key reads into a table (`get review.checklists`). An unknown key exits 2.
  - `python3 .claude/tools/adaptation.py run <key>` runs each entry of a list key once from the repo root
    as an argv list (`shlex.split`, no shell), reports a failing entry and continues, and exits 0.
  - `python3 .claude/tools/adaptation.py tag <id>` prints the path globs of one `sensitivity_tags` entry
    (`playtest-subject-folders`). An unknown id exits 2.

`gate_cadence` is a job's cadence data: `slice`, `merge` and `close` are command forms a rails' `Your checks`
line prints verbatim. `gate_cadence_errors` validates the table; a malformed row fails `get gate_cadence` (exit 1).
`pre_bash` is the floor's project data: `protected` rows ({pattern, why}: a command whose kill is denied) and
`router_entrypoints` rows ({name, redirect?, detach_flag?}: a script the call router runs from main's copy unless
`redirect` is false, backgrounds for a delegate, and backgrounds for every caller without its `detach_flag`). A
malformed row is dropped with one stderr line. `scratch_keep` names the top-level scratch folders `tools/scratch_prune.py` never prunes.
"""
from __future__ import annotations

import copy
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULTS: dict = {
    # hooks/compound_cd_approver.py: commands the approver allows inside a compound.
    "read_only_commands": [],
    # hooks/prompt_git_state_delta.py: submodule paths whose state the prompt reports.
    "git_submodules": [],
    # hooks/refcounted_free_guard.py (PushinPotions-local): helpers whose teardown frees a refcounted object.
    "teardown_helpers": [],
    # hooks/refcounted_free_guard.py; the architecture docs: the test root.
    "tests_root": "Tests",
    # scripts/regression_gate.ps1, run_integration_batched.ps1 (PushinPotions-local): the integration filter.
    "test_quarantine_filter": "",
    # hooks/refcounted_free_guard.py; commands/commit_push.md, create_pr.md, clean_pull.md: paired repos and their rule files.
    "paired_repos": [],
    "paired_repo_rules": {},
    # commands/doc_start_here_update.md: the content domains it lists.
    "content_domains": [],
    # commands/agents/pr_test_checklist_conventions.md: scope rows of the PR test checklist.
    "content_scopes": [],
    # skills/architecture_philosophy/structure_rules.md: folders exempt from the structure rule.
    "structure_exceptions": [],
    # commands/agents/pr_classification.md: path-to-domain signals of PR classification.
    "pr_domains": [],
    # tools/adaptation.py tag: the tag registry; each entry {id, paths, ...}.
    "sensitivity_tags": [],
    # tools/verify_doc_citations.py: code extensions and vocabulary words the citation checker reads.
    "source_extensions": [],
    "symbol_stopwords": [],
    # hooks/session_context_loader.py, hooks/semantic_search_scope_guard.py: absolute document roots read outside the repo.
    "doc_roots": [],
    # tools/drift_scan.py (MaddManager): {sources, consumers, min_length, ignore}; empty means not configured.
    "drift_scan": {},
    # commands/commit_push.md: the branch prefix a land runner integrates; empty means none.
    "land_prefix": "",
    # commands/commit_push.md step 6: the command that waits on a land and reports it; empty means none.
    "land_watch": "",
    # scripts/main_check.py: the workflow that proves the default branch after each push; empty means none.
    "main_check": "",
    # hooks/_delete_rule.py: ignored build outputs a worktree delete may discard (fnmatch; a directory covers its contents).
    "regenerable_ignored": [],
    # skills/change_control/SKILL.md, the rails' `Your checks` line. The shared default holds the harness slice
    # row; a project's table replaces it whole, so it restates its own rows.
    "gate_cadence": {"slice": ["for `.claude/**` units: the edited unit's proof file, run directly"],
                     "merge": [], "close": []},
    # hooks/kill_guard.py (`protected`: {pattern, why} rows of commands a kill is denied for), hooks/run_call_router.py
    # (`router_entrypoints`: {name, redirect?, detach_flag?} rows of the scripts it routes): the project's values in
    # the floor's code.
    "pre_bash": {"protected": [], "router_entrypoints": []},
    # tools/scratch_prune.py: top-level scratch folders a tool keeps across sessions; the prune never counts them.
    "scratch_keep": [],
    # commands/reindex_search.md (`run`): the commands its Step 3 runs.
    "index_prebuild": [],
    # commands/worklog.md, skills/worklog_reference/SKILL.md: [long form, short form] pairs; `get worklog_domains`.
    "worklog_domains": [],
    # tools/worklog.py, tools/scratch_prune.py and the vault-path commands and skills (`get project_name`,
    # `get vault_root`): the values the vault paths use.
    "project_name": "",
    "vault_root": "",
    # commands/overnight.md, commands/overnight_review.md, hooks/overnight_ask_guard.py: the Overnight Close-doc directory.
    "overnight_dir": "",
    # commands/review.md (`get review.checklists`): the project checklists diff-mode review reads.
    "review": {"checklists": []},
    # tools/launch_check.py (both dispatch routes): command lines run before a job spawns and after it returns; empty runs nothing.
    "dispatch": {"pre_launch": "", "post_return": ""},
    # commands/pr_ready.md (`get pr_ready.checks`): the project check files a PR runs.
    "pr_ready": {"checks": []},
    # hooks/_optional_hooks.py members(): {pre_edit, post_edit, pre_read, post_read, prompt: [module]}, the project-local
    # members each dispatcher runs after `_chain.EVENTS`' shared members.
    "hook_members": {},
}

# Expected Python type of each known key; a known key of the wrong type falls back to its default.
_TYPES: dict = {key: type(value) for key, value in DEFAULTS.items()}

# Sub-keys of the table keys: a wrong-typed sub-key keeps its default, the others load.
_SUB_TYPES: dict = {
    "review": {"checklists": list},
    "dispatch": {"pre_launch": str, "post_return": str},
    "pr_ready": {"checks": list},
    "pre_bash": {"protected": list, "router_entrypoints": list},
}

_MISSING = object()

CADENCE_KEYS = ("slice", "merge", "close")


def _cadence_list(value, key: str) -> list:
    rows = value.get(key, [])
    if not isinstance(rows, list):
        return ["%s is not a list" % key]
    return ["%s[%d] is not a non-empty string" % (key, index)
            for index, row in enumerate(rows) if not isinstance(row, str) or not row.strip()]


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
    for key in CADENCE_KEYS:
        errors += _cadence_list(value, key)
    return errors


def adaptation_path(claude_dir) -> Path:
    """`<claude_dir>/skills/project_subsystems/adaptation.json`."""
    return Path(claude_dir) / "skills" / "project_subsystems" / "adaptation.json"


_CACHE: dict = {}


def _stat_key(path: Path):
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None, None)


def load(claude_dir) -> dict:
    """Every known key at its default, overridden by the valid keys `adaptation.json` sets (module
    docstring). Cached on the file's (path, mtime, size), so a process that reads several keys parses
    the file once and prints each warning once; a rewritten or removed file reloads."""
    path = adaptation_path(claude_dir)
    stat_key = _stat_key(path)
    if stat_key not in _CACHE:
        _CACHE.clear()
        _CACHE[stat_key] = _read_file(path)
    result = copy.deepcopy(DEFAULTS)
    result.update(copy.deepcopy(_CACHE[stat_key]))
    return result


def _warn(path: Path, key: str, why: str) -> None:
    print(f"adaptation: {path} key '{key}' {why} -- using default", file=sys.stderr)


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
            _warn(path, key, f"is wrong-typed (want {expected.__name__})")
            continue
        values[key] = _sub_values(path, key, value) if key in _SUB_TYPES else value
    if "pre_bash" in values:
        values["pre_bash"] = _pre_bash_rows(path, values["pre_bash"])
    return values


def _pre_bash_rows(path: Path, table: dict) -> dict:
    """`table` with each malformed row of `protected` and `router_entrypoints` dropped, one stderr line per row."""
    for sub, ok in (("protected", _protected_row_ok), ("router_entrypoints", _entrypoint_ok)):
        kept = []
        for index, row in enumerate(table[sub]):
            if ok(row):
                kept.append(row)
            else:
                _warn(path, "pre_bash.%s[%d]" % (sub, index), "is malformed")
        table[sub] = kept
    return table


def _protected_row_ok(row) -> bool:
    if not isinstance(row, dict) or set(row) != {"pattern", "why"}:
        return False
    if not all(isinstance(row[k], str) and row[k].strip() for k in row):
        return False
    try:
        re.compile(row["pattern"])
    except re.error:
        return False
    return True


def _entrypoint_ok(row) -> bool:
    """A router row: a non-empty `name`, an optional bool `redirect` and an optional `-flag` `detach_flag`."""
    if not isinstance(row, dict) or "name" not in row or not set(row) <= {"name", "redirect", "detach_flag"}:
        return False
    detach = row.get("detach_flag")
    return (isinstance(row["name"], str) and bool(row["name"].strip()) and isinstance(row.get("redirect", True), bool)
            and (detach is None or (isinstance(detach, str) and len(detach) > 1 and detach.startswith("-"))))


def _sub_values(path: Path, key: str, value: dict) -> dict:
    merged = copy.deepcopy(DEFAULTS[key])
    for sub, expected in _SUB_TYPES[key].items():
        if sub not in value:
            continue
        if isinstance(value[sub], expected) and not isinstance(value[sub], bool):
            merged[sub] = value[sub]
        else:
            _warn(path, f"{key}.{sub}", f"is wrong-typed (want {expected.__name__})")
    return merged


def _default(key: str):
    """The default of a dotted key, or _MISSING when no such key exists."""
    value = DEFAULTS
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return copy.deepcopy(value)


def get(claude_dir, key):
    """One key, or one sub-key of a table key (`review.checklists`), defaulted as `load()` defaults it.
    An unknown key returns None."""
    default = _default(key)
    value = load(claude_dir)
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None if default is _MISSING else default
        value = value[part]
    return value


def tag_paths(claude_dir, tag_id: str):
    """The path globs of the `sensitivity_tags` entry named `tag_id`, or None when no entry has that id."""
    for entry in get(claude_dir, "sensitivity_tags"):
        if isinstance(entry, dict) and entry.get("id") == tag_id:
            return [p for p in entry.get("paths", []) if isinstance(p, str)]
    return None


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    claude_dir = Path(__file__).resolve().parent.parent
    if len(args) == 2 and args[0] == "tag":
        paths = tag_paths(claude_dir, args[1])
        if paths is None:
            print(f"adaptation: no sensitivity tag '{args[1]}'", file=sys.stderr)
            return 2
        for path in paths:
            print(path)
        return 0
    if len(args) != 2 or args[0] not in ("get", "run"):
        print("usage: adaptation.py get <key> | run <key> | tag <id>", file=sys.stderr)
        return 2
    verb, key = args
    if _default(key) is _MISSING:
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
            done = subprocess.run(shlex.split(entry), cwd=str(claude_dir.parent), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
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
