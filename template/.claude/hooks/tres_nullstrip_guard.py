#!/usr/bin/env python3
"""Guard against value-type Export null-strips in .tres / .tscn files.

Godot's editor resave silently rewrites every .tres/.tscn referencing a script
that grew a new [Export], writing previously-omitted Exports as explicit
`Field = null`. On load, null coerces to the type-zero / empty value rather than
the C# field-init default -- so an Export whose C# side has a MEANINGFUL default
(bool `= true`, a non-zero int or float, a non-empty NodePath) silently loses it,
with a green build and no warning. A stripped NodePath default is the sharpest
case: `GetNode(empty)` NREs in `_Ready` on a scene that resaved cleanly.

Detection is deterministic, not name-heuristic: for each added `Field = null`
line in the diff, we look up whether ANY tracked C# `[Export]` named `Field`
declares a *meaningful* default (non-null / non-zero / non-false / non-empty /
non-`new`). If so, the scene has thrown that default away -> flag. This catches
bools (`= true`), private underscore exports (`_someTunedValue`), and NodePath
defaults that a numeric-name heuristic structurally cannot. Reference-type
Exports with a null/absent/`new()` default coerce harmlessly and are never in
the "meaningful default" set, so they don't false-positive.

The regression shape is a pure line *addition* (`+Field = null`) -- diff-scoped,
so already-committed nulls aren't re-flagged; this catches the *next* strip. The
C# index is built lazily, only when the diff actually contains `= null` additions.

Modes:
    tres_nullstrip_guard.py              # scan staged (git diff --cached); exit 1 on strip
    tres_nullstrip_guard.py --range A..B # scan a commit range (CI / PR review); exit 1 on strip
    tres_nullstrip_guard.py --hook       # PreToolUse: deny a `git commit` that strips a default

Escape hatch (rare -- a field genuinely made nullable, C# default now null):
set ALLOW_TRES_NULLSTRIP=1 in the environment.
"""
import json
import os
import re
import subprocess
import sys

# Windows pipes default to cp1252 through Python 3.14; git diff text now decodes as real
# UTF-8, so a non-ASCII .tres string payload or path would raise UnicodeEncodeError on write.
sys.stdout.reconfigure(encoding="utf-8")

# An added scene line `+Field = null` -- Field is a leading-Uppercase Export or a
# leading-underscore private Export (the `[Export] private _field` form).
ADDED_NULL = re.compile(r"^\+(_?[A-Za-z][A-Za-z0-9_]*)\s*=\s*null\s*$")
FILE_HDR = re.compile(r"^\+\+\+ b/(.*)$")

# C# [Export] property with an initializer: `[Export...] <mods> Type Name { get; set; } = DEFAULT;`
CS_PROP = re.compile(
    r"\[Export\b[^\]]*\]"
    r"(?:\s*\[[^\]]*\])*"
    r"\s*(?:public|private|protected|internal|static|virtual|override|sealed|\s)+"
    r"[\w<>\?,\.\[\]]+\s+"
    r"(_?\w+)\s*"
    r"\{[^{}]*\}\s*"
    r"=\s*([^;]+);"
)
# C# [Export] field with an initializer: `[Export...] <mods> Type Name = DEFAULT;`
CS_FIELD = re.compile(
    r"\[Export\b[^\]]*\]"
    r"(?:\s*\[[^\]]*\])*"
    r"\s*(?:public|private|protected|internal|static|readonly|\s)+"
    r"[\w<>\?,\.\[\]]+\s+"
    r"(_?\w+)\s*"
    r"=\s*([^;{]+);"
)

# Defaults that coerce to the same value `= null` loads as -> harmless, not a strip.
SAFE_DEFAULTS = {
    "null", "default", "false", '""', "string.empty",
    "0", "0f", "0d", "0m", "0u", "0l", "0ul", "0.0", "0.0f", "0.0d",
}


def is_meaningful(default: str) -> bool:
    """True if losing this C# default (to a scene `= null`) changes behavior."""
    d = default.strip().rstrip(";").strip()
    # `null!` / `default!` -- the null-forgiving [RequiredExport] convention. The
    # C# default IS null (assigned in-editor); a scene `= null` loses nothing.
    d = d.rstrip("!").strip()
    dl = d.lower()
    if dl in SAFE_DEFAULTS:
        return False
    if dl.startswith("new"):      # collection / ref default -- Godot replaces on load
        return False
    if dl.startswith(">"):        # mis-captured expression-bodied member
        return False
    if dl.endswith(".zero"):      # Vector*.Zero etc -- null coerces to the same zero
        return False
    return True


def diff_lines(range_arg):
    cmd = ["git", "diff", "-U0", "--no-color"]
    cmd += [range_arg] if range_arg else ["--cached"]
    cmd += ["--", "*.tres", "*.tscn"]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.splitlines()


def added_null_fields(range_arg):
    """Return [(path, field)] for every added `Field = null` line in the diff."""
    out = []
    current = None
    for line in diff_lines(range_arg):
        line = line.rstrip("\r")
        hdr = FILE_HDR.match(line)
        if hdr:
            current = hdr.group(1)
            continue
        m = ADDED_NULL.match(line)
        if m:
            out.append((current, m.group(1)))
    return out


def meaningful_export_names():
    """Set of C# [Export] names whose declared default is meaningful (built lazily)."""
    names = set()
    # --recurse-submodules: the Jmodot framework .cs live in a submodule, which a
    # plain `ls-files` reports as one gitlink -- missing every Export decl inside it.
    files = subprocess.run(
        ["git", "ls-files", "--recurse-submodules", "*.cs"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.splitlines()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if "[Export" not in text:
            continue
        for rx in (CS_PROP, CS_FIELD):
            for name, default in rx.findall(text):
                if is_meaningful(default):
                    names.add(name)
    return names


def find_strips(range_arg):
    """Return [(path, field)] where a scene `= null` throws away a meaningful C# default."""
    candidates = added_null_fields(range_arg)
    if not candidates:
        return []
    dangerous = meaningful_export_names()
    return [(p, f) for (p, f) in candidates if f in dangerous]


def report(findings):
    print(
        "[tres-nullstrip-guard] Suspected value-type Export null-strip "
        f"({len(findings)} line(s)):",
        file=sys.stderr,
    )
    for path, field in findings:
        print(f"  {path}: {field} = null", file=sys.stderr)
    print(
        "\nEach of these Exports has a MEANINGFUL C# default (non-null/zero/false) that "
        "`= null` throws away -- the scene loads the type-zero / empty value instead "
        "(gotcha_godot_editor_resave_hazards). Set the explicit intended "
        "value (e.g. `SomeCount = 24`, `SomeFlag = true`) -- do NOT delete the line, "
        "the editor re-strips it. If the field was genuinely made nullable (C# default "
        "now null), set ALLOW_TRES_NULLSTRIP=1.",
        file=sys.stderr,
    )


def standalone(range_arg):
    if os.environ.get("ALLOW_TRES_NULLSTRIP"):
        return 0
    findings = find_strips(range_arg)
    if not findings:
        return 0
    report(findings)
    return 1


def hook():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0
    if data.get("tool_name") != "Bash":
        print("{}")
        return 0
    if "git commit" not in data.get("tool_input", {}).get("command", ""):
        print("{}")
        return 0
    if os.environ.get("ALLOW_TRES_NULLSTRIP"):
        print("{}")
        return 0

    findings = find_strips(None)
    if not findings:
        print("{}")
        return 0

    report(findings)  # surfaced in the transcript alongside the deny
    reason = (
        f"Blocked: {len(findings)} staged scene line(s) null-strip a value-type Export "
        "with a meaningful C# default (loads type-zero, not the default -> silent "
        "behavior break with a green build). Set the explicit value, or set "
        "ALLOW_TRES_NULLSTRIP=1 if the field was genuinely made nullable."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "--hook":
        return hook()
    range_arg = args[1] if len(args) > 1 and args[0] == "--range" else None
    return standalone(range_arg)


if __name__ == "__main__":
    sys.exit(main())
