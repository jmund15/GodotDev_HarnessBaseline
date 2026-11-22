#!/usr/bin/env python3
"""One classifier for wide test runs, shared by the dispatch check and the Bash guard.

`gate_cadence.wide` in the adaptation files holds one row per wide runner: an invocation regex, the
arguments that exempt it, an optional narrow rule, and an optional `hint` the deny prints as the form
to write instead (the data contract is `tools/adaptation.py` `gate_cadence_errors`). This module reads
that table and answers two questions:

    classify(command, cwd, root) -> [{"runner": matched invocation, "reason": why wide}]
    gate_caller(payload, environ, table) -> bool

`classify` matches the INVOCATION, never the noun (`rules/harness_tooling.md`): a row's regex is
anchored at a shell segment's executable word, so `grep`, `cat`, `git show` or a bare name in prose
never match. Its reach is the godot guard's: shell segments, `bash -c`/`pwsh -Command` bodies, Python
`-c` and `.py` files (literal `subprocess`/`os` process-call arguments only), and launched scripts to
depth 2. Not seen: a command built at run time, or a script chain deeper than the scan.

A malformed row fails `classify` closed: the table's validation errors become the one wide result, so
the guard refuses every command until the data is fixed. A placeholder in the invocation (`<spec>:<line>`,
`<Class>`) satisfies a row's narrow form: a brief that prints the form for the reader to fill is not
ordering the wide run, while a row declaring no narrow form stays wide whatever its placeholders say.
A row's `declared` narrowing is evaluated: the
filter argument named by `arg` is narrow when every included filter term names a unit a `decl` regex
finds in the files `glob` matches under the root, or matches an `admit` pattern (the Lint namespace). A
term that is a namespace prefix, ends in a trailing dot, or whose text is built at run time is wide, as
is a run with no filter argument.

`gate_caller` admits the root session (no `agent_id`, no `CLAUDE_CODE_SIDECAR`), a native job whose
payload `agent_type` is the gate kind's `agentType`, and a sidecar whose `CLAUDE_CODE_SIDECAR_KIND` is
`gate`. That `agentType` is read from `guards/tool_profiles.json`'s gate row; a native job is refused
when the table or the row is absent, never admitted on a copy of the name. Every other caller, an unknown
type included, is refused.
"""
import ast
import glob as globmod
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))

import adaptation  # noqa: E402
from sidecar_argv import executable_index, shell_segments_with_comments  # noqa: E402

SCAN_DEPTH, MAX_SCRIPT_BYTES = 2, 300_000
POWERSHELLS = ("pwsh", "powershell")
INTERPRETERS = ("bash", "sh", "zsh", "python", "python3", "py", "node")
LAUNCHER_RE = re.compile(r"\b(pwsh|powershell|python3?|bash|Start-Process|subprocess|Popen)\b|&\s|^\s*\.?[/\\]", re.I)
PRINTED_OPEN = re.compile(r"(?:=|\b(?:write-host|write-output|write-warning|write-error|echo|printf))\s*$", re.I)
RUNTIME_FILTER_RE = re.compile(r"[$`{%]")
# A placeholder is one word in angle brackets (`<spec>`, `<desktop|phone>`); a shell redirect
# (`< in > out`) has spaces inside and must still classify as the run it is.
PLACEHOLDER_RE = re.compile(r"<[A-Za-z][^<>\s]{0,119}>")
FILTER_TERM_RE = re.compile(r"([A-Za-z]+)\s*(!~|!=|~|=)\s*([^|&()]+)")
DECLARED_CACHE: dict = {}
GATE_KIND = "gate"


def _base(word):
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _compile_rows(wide):
    rows = []
    for row in wide:
        try:
            runner = re.compile(row["runner"])
        except (re.error, KeyError, TypeError):
            continue
        narrow = row.get("narrow")
        requires, declared = None, None
        if isinstance(narrow, dict):
            if "requires" in narrow:
                requires = [re.compile(pattern) for pattern in narrow["requires"]]
            elif "declared" in narrow:
                declared = _compile_declared(narrow["declared"])
        rows.append({"runner": runner,
                     "exempt": [re.compile(pattern) for pattern in row.get("exempt") or []],
                     "requires": requires,
                     "declared": declared,
                     "narrowed": isinstance(narrow, dict),
                     "hint": row.get("hint") or ""})
    return rows


def _compile_declared(declared):
    try:
        return {"arg": declared["arg"],
                "glob": declared["glob"],
                "decl": [re.compile(pattern) for pattern in declared["decl"]],
                "admit": [re.compile(pattern) for pattern in declared.get("admit") or []]}
    except (re.error, KeyError, TypeError):
        return None


def _declared_names(root, pattern, decl):
    """Every unit name the `decl` regexes capture across the files `pattern` matches under `root`."""
    key = (root, pattern, tuple(regex.pattern for regex in decl))
    if key in DECLARED_CACHE:
        return DECLARED_CACHE[key]
    names = set()
    for path in globmod.glob(os.path.join(root, pattern), recursive=True):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for regex in decl:
            names.update(match.group(1) for match in regex.finditer(text) if match.group(1))
    DECLARED_CACHE[key] = names
    return names


def _arg_values(args, flag):
    """The values given to the parameter `flag` (`-Filter x`, `-Filter:x`, `--filter x`, `--filter=x`)."""
    target = flag.lstrip("-/").lower()
    values, index = [], 0
    while index < len(args):
        word = args[index]
        token = word.lstrip("-/")
        if word[:1] in "-/" and token:
            name, sep, inline = token.partition(":")
            if not sep:
                name, sep, inline = token.partition("=")
            if name.lower() == target:
                if sep:
                    values.append(inline.strip())
                    index += 1
                    continue
                following = args[index + 1].strip() if index + 1 < len(args) else ""
                if following[:1] in "-/" and following:
                    values.append("")
                else:
                    values.append(following)
                index += 2
                continue
        index += 1
    return values


def _filter_value_reason(value, names, admit):
    """Why this filter value is wide, or None when every included term names a declared unit."""
    if RUNTIME_FILTER_RE.search(value):
        return "its text is built at run time"
    terms = [(prop, value_) for prop, op, value_ in FILTER_TERM_RE.findall(value) if op in ("~", "=")]
    if not terms:
        return "it has no `FullyQualifiedName` filter term"
    for prop, raw in terms:
        term = raw.strip().strip("'\"")
        if prop.lower() != "fullyqualifiedname":
            return "`%s` selects by member name, not by test class" % prop
        if any(regex.search(term) for regex in admit):
            continue
        if term.endswith("."):
            return "`%s` ends in a trailing dot" % term
        parts = term.split(".")
        if len(parts) == 1 and term in names:
            continue
        if len(parts) > 1 and (parts[-1] in names or parts[-2] in names):
            continue
        return "`%s` is a namespace or tier, not a test class or method" % term
    return None


def _declared_wide_reason(declared, args, root):
    """Why this declared-narrow invocation is wide, or None when every filter value narrows it."""
    values = _arg_values(args, declared["arg"])
    if not values:
        return "it carries no `%s` filter" % declared["arg"]
    names = _declared_names(root, declared["glob"], declared["decl"])
    for value in values:
        reason = _filter_value_reason(value, names, declared["admit"])
        if reason:
            return reason
    return None


def _wide(row, text, match, reason):
    """One wide result, carrying the row's `hint` when it authored one."""
    if row["hint"]:
        reason += " Narrow it: %s." % row["hint"]
    return {"runner": text[:match.end()].strip(), "reason": reason}


def _match(rows, text, args, root, written=None):
    """The first wide row this invocation matches, or None. `written` is the invocation as the brief
    wrote it, when `text` was rebuilt from shell tokens (which drop an unshellable `<...>`)."""
    for row in rows:
        match = row["runner"].match(text)
        if not match:
            continue
        if any(pattern.search(arg) for pattern in row["exempt"] for arg in args):
            return None
        # A brief prints a narrow row's form with a placeholder standing for the value
        # (`npm run staging:e2e -- <spec>:<line> --project=<desktop|phone>`): the form to fill,
        # not an order. Only a row that declares a narrow form can be satisfied this way.
        if row["narrowed"] and PLACEHOLDER_RE.search(written if written is not None else text):
            return None
        if row["requires"] is not None:
            if all(any(pattern.search(arg) for arg in args) for pattern in row["requires"]):
                return None
            return _wide(row, text, match, "its arguments do not match the row's narrow `requires`")
        if row["declared"] is not None:
            reason = _declared_wide_reason(row["declared"], args, root)
            if reason is None:
                return None
            return _wide(row, text, match, reason)
        if row["narrowed"]:
            return _wide(row, text, match, "the row's `declared` narrowing could not be read")
        return _wide(row, text, match, "the gate cadence declares no narrow form for it")
    return None


def _logical_lines(text):
    pending = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith(("\\", "`")):
            pending += stripped[:-1] + " "
            continue
        yield pending + line
        pending = ""
    if pending:
        yield pending


def _words(line):
    return [word.strip("\"'") for word in re.findall(r"\"[^\"]*\"|'[^']*'|[^\s,\[\]()]+", line)]


def _in_printed_string(text):
    """True when `text` ends inside a quote that opened as a value or a print argument, so a runner
    name after it is printed text rather than a run."""
    quote, opened = None, 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote, opened = char, index
    return quote is not None and bool(PRINTED_OPEN.search(text[:opened]))


def _python_runs(text, cwd, root, rows, depth):
    """Inspect literal process-call arguments, not Python strings used as paths or output."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    processes = {'subprocess': {'run', 'Popen', 'call', 'check_call', 'check_output'},
                 'os': {'system', 'popen'}}
    modules, functions = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name, alias.name) for alias in node.names
                           if alias.name in processes)
        elif isinstance(node, ast.ImportFrom) and node.module in processes:
            functions.update(alias.asname or alias.name for alias in node.names
                             if alias.name in processes[node.module])
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = node.func
        direct = isinstance(call, ast.Name) and call.id in functions
        qualified = (isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name)
                     and call.attr in processes.get(modules.get(call.value.id), set()))
        if not (direct or qualified):
            continue
        arg = node.args[0] if node.args else next((key.value for key in node.keywords if key.arg == 'args'), None)
        try:
            value = ast.literal_eval(arg)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, (list, tuple)) and all(isinstance(word, str) for word in value):
            value = shlex.join(value)
        if isinstance(value, str):
            yield from _command_runs(value, cwd, root, rows, depth + 1)


def _file_runs(path, cwd, root, rows, depth):
    """Wide runs inside a script file the command executes."""
    for base in (cwd, root):
        full = os.path.normpath(os.path.join(base, path))
        if os.path.isfile(full):
            break
    else:
        return
    rel = full.replace("\\", "/")
    if "/.claude/tests/" in rel or rel.endswith(".md") or os.path.getsize(full) > MAX_SCRIPT_BYTES:
        return
    try:
        with open(full, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return
    if full.lower().endswith(".py"):
        yield from _python_runs(text, cwd, root, rows, depth)
        return
    if full.lower().endswith(".ps1"):
        text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    for line in _logical_lines(text):
        if line.lstrip().startswith(("#", "//", "REM ")):
            continue
        found = list(_command_runs(line, cwd, root, rows, depth + 1))
        yield from found
        if found:
            continue
        for row in rows:
            match = row["runner"].search(line)
            if not match:
                continue
            prefix = line[:match.start()]
            if not LAUNCHER_RE.search(prefix) or _in_printed_string(prefix):
                continue
            hit = _match(rows, line[match.start():], _words(line[match.start():]), root)
            if hit:
                yield hit


def _command_runs(command, cwd, root, rows, depth=0):
    """Wide runs a shell command (or script text) starts."""
    if depth > SCAN_DEPTH:
        return
    for tokens, _comment in shell_segments_with_comments(command):
        segment, index = executable_index(tokens)
        if index >= len(segment):
            continue
        words = segment[index:]
        if words[0] == "&" and len(words) > 1:
            words = words[1:]
        if not words:
            continue
        exe, args = _base(words[0]), words[1:]
        hit = _match(rows, " ".join(words), args, root, written=" ".join(tokens))
        if hit:
            yield hit
            continue
        lowered = [arg.lower() for arg in args]
        if exe in POWERSHELLS:
            body_at = next((lowered.index(flag) + 1 for flag in ("-command", "-c") if flag in lowered), None)
            if body_at is not None and body_at < len(args):
                yield from _command_runs(" ".join(args[body_at:]), cwd, root, rows, depth + 1)
            elif "-file" in lowered and lowered.index("-file") + 1 < len(args):
                yield from _file_runs(args[lowered.index("-file") + 1], cwd, root, rows, depth)
            continue
        if exe in INTERPRETERS:
            if "-c" in args and args.index("-c") + 1 < len(args):
                body = args[args.index("-c") + 1]
                if exe in ("python", "python3", "py"):
                    yield from _python_runs(body, cwd, root, rows, depth)
                else:
                    yield from _command_runs(body, cwd, root, rows, depth + 1)
            else:
                script = next((arg for arg in args if not arg.startswith("-")), None)
                if script:
                    yield from _file_runs(script, cwd, root, rows, depth)
            continue
        if exe.endswith((".sh", ".ps1", ".bash", ".cmd", ".bat")):
            yield from _file_runs(words[0], cwd, root, rows, depth)


def classify(command, cwd="", root=None):
    """The wide runs `command` starts: [{"runner": <matched invocation>, "reason": <why wide>}]."""
    root = os.path.abspath(os.fspath(root)) if root else ROOT
    data = adaptation.get(os.path.join(root, ".claude"), "gate_cadence")
    errors = adaptation.gate_cadence_errors(data)
    if errors:
        return [{"runner": "<gate_cadence>", "reason": "; ".join(errors)}]
    rows = _compile_rows(data.get("wide") or [])
    if not rows:
        return []
    found = []
    for run in _command_runs(command or "", cwd or os.getcwd(), root, rows):
        if run not in found:
            found.append(run)
    return found


def gate_agent_type(table=None):
    """The gate kind's `agentType` from the kinds table (`guards/tool_profiles.json`), or None.

    None means no table or no gate row, so a native job cannot be shown to be the gate: the caller refuses."""
    row = ((table or {}).get("kinds") or {}).get(GATE_KIND) or {}
    return str(row.get("agentType") or "").strip() or None


def gate_caller(payload, environ, table=None):
    """Is this caller allowed to start a wide run? Fail closed: only the three gate callers pass."""
    environ = environ or {}
    payload = payload or {}
    if str(environ.get("CLAUDE_CODE_SIDECAR") or "").strip():
        return str(environ.get("CLAUDE_CODE_SIDECAR_KIND") or "").strip().lower() == GATE_KIND
    if not str(payload.get("agent_id") or "").strip():
        return True
    gate_type = gate_agent_type(table)
    if gate_type is None:
        return False
    return str(payload.get("agent_type") or "").strip().lower() == gate_type
