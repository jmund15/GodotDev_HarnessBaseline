#!/usr/bin/env python3
"""The shape of `gate_cadence`, a job's cadence table in the adaptation files, and its one validator.

`tools/adaptation.py` owns the layering: `reach` and `reach_exempt` union across layers, so the harness layer's
`.claude/**` entry reaches every consumer. Keys:
  - `slice`, `merge`, `close`: command forms a rails' `Your checks` line prints verbatim (`tools/guard_text.py`).
  - `wide`: one row per wide runner (`runner` invocation regex, `exempt` argument regexes, optional `narrow` rule:
    `requires` regexes or a `declared` {arg, glob, decl, admit} rule, optional `hint` the deny prints as the narrow
    form to write instead), read through `hooks/_wide_runs.py` by `hooks/_dispatch_checks.py` and
    `hooks/wide_test_guard.py`.
  - `reach` ({name, paths: [glob], cmd}), `reach_exempt` (globs) and `reach_fallback` (the full form): the table
    `tools/reach_run.py` runs a slice from.
  - `main` ({command: argv list, retry_exits: [int], outputs: [path]}): the gate `scripts/main_check.py` runs on the
    default branch after a land (absent: the project runs none). Exit 0 passes, exit 1 fails and names each failed
    test as a `FAILED_TEST=<id>` stdout line, an exit in `retry_exits` is rerun once, any other exit is no verdict,
    and `outputs` are paths the gate writes, which owe no gate when a range changes only them and harness paths.

`errors(value)` returns the shape errors of a merged table, [] when it is valid; an unknown key is one. `load()`
stays lenient, so this is the per-value check for this key: `adaptation.py get gate_cadence` exits 1 on an error,
and `hooks/_wide_runs.py` and `scripts/main_check.py` refuse a table it rejects. A new key is a `KEYS` entry plus its check and a case in
`tests/test_gate_cadence.py`.
"""
import re

KEYS = ("slice", "merge", "close", "wide", "reach", "reach_exempt", "reach_fallback", "main")


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


def _command_forms(value, key: str) -> list:
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


def _wide_errors(value) -> list:
    wide = value.get("wide", [])
    if not isinstance(wide, list):
        return ["wide is not a list"]
    return [error for index, row in enumerate(wide) for error in _wide_row_errors(index, row)]


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


def errors(value) -> list:
    """Shape errors of a merged `gate_cadence` value; [] when it is valid."""
    if not isinstance(value, dict):
        return ["is not an object"]
    found = []
    unknown = sorted(set(value) - set(KEYS))
    if unknown:
        found.append("has unknown keys %s" % ", ".join(unknown))
    for key in ("slice", "merge", "close"):
        found += _command_forms(value, key)
    return found + _reach_errors(value) + _main_errors(value) + _wide_errors(value)
