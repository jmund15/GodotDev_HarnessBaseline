#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell -- a delegate may not start a wide test run its brief does not name.

Class: process guard. The owner asked for a blocking guard after two text fixes failed, so it denies from its
first commit. `guards/author.md` owns the rule: a delegate runs only its changed items' class or method filters;
the namespace, tier, `verify.ps1 -Scope`, regression-gate and whole-assembly runs are the dispatcher's at close.
Weakness compensated: delegates that ignore that line and loop `run_test_suite.ps1` over domain filters. It
retires when delegate transcripts show no wide run and no denial from it.

Wide: a filter term that does not end in a test class or a `Class.Method` declared under `Tests/` (a namespace
prefix, a trailing dot, `Name~`, a tier, a filter whose text is built at run time), `verify.ps1 -Scope`,
`regression_gate.ps1`, `run_integration_batched.ps1` (with or without `-Only`), and `dotnet test` without a
filter. `!~`/`!=` terms only narrow. A filter naming two classes is narrow.
The `Tests.Logic.Lint` namespace is narrow when `Tests/Logic/Lint/` exists: its classes are the repo-scan lints every
writer runs (`guards/author.godot.md`).

Delegate, from facts: the payload's transcript sits under `/subagents/` (Agent-tool and Workflow agents), or the
process carries CLAUDE_CODE_SIDECAR (every `claude -p` launcher exports it). The main session is never refused.

Pass: the delegate's brief names the run -- the exact filter string; `verify.ps1` plus every `-Scope` name;
`run_integration_batched.ps1` plus every `-Only` name; `regression_gate`; `dotnet test`. The brief is the dispatcher's text: a subagent's user-role messages in its own
transcript, or a sidecar's prompt file (CLAUDE_CODE_SIDECAR_PROMPT_FILE, set by `sc_scrub_env`) plus its
`.amend.md`. Text the checked agent writes is never read.

Reach: the command, `bash -c`/`pwsh -Command` bodies, a Python `-c` body or `.py` file (literal `subprocess`/`os` process-call arguments only), and (to depth 2) the other script files the command runs, so a
loop in a script is caught; a `.ps1` file's `<# #>` help blocks are not scanned, and a launcher inside a quoted value (`x = "..."`) or a print argument (`Write-Host "..."`) on a script line is printed text, not a run. The `.ps1` entry points call this file's `--entry` mode through
`scripts/WideRunGuard.ps1`, which covers sidecar children at any depth. Not seen from the hook: a script that
builds its command at runtime, and a subagent's script chain deeper than the scan.
Fail posture: a crash here reports the lost check through additionalContext and admits the call.
Proof: `.claude/tests/test_wide_test_guard.py`.
"""
import argparse
import ast
import shlex
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))

from _command_text import executable_text  # noqa: E402
from sidecar_argv import executable_index, shell_segments_with_comments  # noqa: E402

RUN_SUITE, VERIFY, GATE, BATCHED = ("run_test_suite.ps1", "verify.ps1", "regression_gate.ps1",
                                    "run_integration_batched.ps1")
ENTRIES = (RUN_SUITE, VERIFY, GATE, BATCHED)
EXEMPT = {RUN_SUITE: ("reapreport", "routeonly"), VERIFY: ("selftest", "routeonly"),
          GATE: ("staticonly", "routeonly"), BATCHED: ("dryrun", "bootstrapfromtrx", "routeonly")}
POWERSHELLS = ("pwsh", "powershell")
INTERPRETERS = ("bash", "sh", "zsh", "python", "python3", "py", "node")
LAUNCHER_RE = re.compile(r"\b(pwsh|powershell|python3?|bash|Start-Process|subprocess|Popen)\b|&\s|^\s*\.?[/\\]", re.I)
TERM_RE = re.compile(r"([A-Za-z]+)\s*(!~|!=|~|=)\s*([^|&()]+)")
LINT_TERM = re.compile(r"(?:^|\.)Tests\.Logic\.Lint\.?$")
CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
UNREADABLE = re.compile(r"[$`{%]")
ADMIT_SET = re.compile(r"(?:^|[\s;&|(])(?:export\s+|set\s+)?HARNESS_WIDE_RUN_ADMITTED\s*=|\$env:HARNESS_WIDE_RUN_ADMITTED\s*="
                       r"|SetEnvironmentVariable\(\s*['\"]HARNESS_WIDE_RUN_ADMITTED", re.I)
SCAN_DEPTH, MAX_SCRIPT_BYTES = 2, 300_000
_classes = {}
_roots = [ROOT]


def _base(word):
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def test_classes(root):
    if root not in _classes:
        found = set()
        for folder, _, files in os.walk(os.path.join(root, "Tests")):
            for name in files:
                if name.endswith(".cs"):
                    try:
                        with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as fh:
                            found.update(CLASS_RE.findall(fh.read()))
                    except OSError:
                        pass
        _classes[root] = found
    return _classes[root]


def checkout_of(path):
    """The checkout holding `path`: the nearest ancestor with both Tests/ and .claude/, else None."""
    here = os.path.abspath(path) if path else ""
    for _ in range(8):
        if not here or not os.path.isdir(here):
            here = os.path.dirname(here)
        elif os.path.isdir(os.path.join(here, "Tests")) and os.path.isdir(os.path.join(here, ".claude")):
            return here
        else:
            here = os.path.dirname(here)
    return None


def set_roots(command, cwd):
    """The checkouts whose Tests/ name a class this command may filter on. A delegate's worktree is not the
    checkout the hook runs from, and a class it just wrote exists only there."""
    named = [cwd]
    for match in re.finditer(r"\bcd\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))|-Repo[ :=]+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
                             command or ""):
        named.append(next(g for g in match.groups() if g))
    found = [ROOT]
    for path in named:
        root = None if UNREADABLE.search(path or "") else checkout_of(path)
        if root and root not in found:
            found.append(root)
    _roots[:] = found


def filter_wide_reason(value):
    """None when every included term of the filter ends in a test class or Class.Method, else why not."""
    if UNREADABLE.search(value):
        return "its text is built at run time"
    terms = [t for t in TERM_RE.findall(value) if t[1] in ("~", "=")]
    if not terms:
        return "it has no FullyQualifiedName term"
    classes = set().union(*(test_classes(root) for root in _roots))
    for prop, _, raw in terms:
        term = raw.strip().strip("'\"")
        if prop.lower() != "fullyqualifiedname":
            return "`%s` selects by member name, not by test class" % prop
        if LINT_TERM.search(term) and any(os.path.isdir(os.path.join(r, "Tests", "Logic", "Lint")) for r in _roots):
            continue
        parts = term.split(".")
        if term.endswith(".") or not (parts[-1] in classes or (len(parts) > 1 and parts[-2] in classes)):
            return "`%s` is a namespace or tier, not a test class or method" % term
    return None


def _params(args):
    """Lowercased PowerShell parameter name -> its value words (empty for a switch)."""
    found, name = {}, None
    for word in args:
        if word.startswith("-") and len(word) > 1 and not word[1].isdigit():
            name, _, inline = word[1:].partition(":")
            name = name.lower()
            found[name] = [inline] if inline else []
        elif name:
            found[name].append(word)
    return found


def classify(entry, args):
    """(label, needles) when the run is wide, else None. needles: strings the brief must all contain;
    None when no brief text can name it."""
    if entry == "dotnet":
        raw = next((a.split("=", 1)[1] for a in args if a.startswith("--filter=")), None)
        if raw is None and "--filter" in args and args.index("--filter") + 1 < len(args):
            raw = args[args.index("--filter") + 1]
        return _by_filter(raw, "dotnet test")
    params = _params(args)
    if any(flag in params for flag in EXEMPT[entry]):
        return None
    if entry == GATE:
        if "watch" in params and "detach" not in params:
            return None
        return "the regression gate", ["regression_gate"]
    if entry == VERIFY and "scope" in params:
        scopes = [s for word in params["scope"] for s in word.split(",") if s]
        return "verify.ps1 -Scope %s" % ",".join(scopes), ["verify.ps1"] + scopes
    if entry == BATCHED:
        domains = [s for word in params.get("only") or [] for s in word.split(",") if s]
        if domains:
            return "%s -Only %s" % (BATCHED, ",".join(domains)), [BATCHED] + domains
        return "%s (the whole Integration suite)" % BATCHED, [BATCHED]
    return _by_filter((params.get("filter") or [None])[0], entry)


def _by_filter(raw, via):
    if raw is None:
        return "%s with no filter" % via, [via]
    reason = filter_wide_reason(raw)
    if reason is None:
        return None
    return "%s -Filter \"%s\" (%s)" % (via, raw, reason), (None if UNREADABLE.search(raw) else [raw])


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
    return [w.strip("\"'") for w in re.findall(r"\"[^\"]*\"|'[^']*'|[^\s,\[\]()]+", line)]


def _python_runs(text, cwd, depth):
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
        arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == 'args'), None)
        try:
            value = ast.literal_eval(arg)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, (list, tuple)) and all(isinstance(word, str) for word in value):
            value = shlex.join(value)
        if isinstance(value, str):
            yield from command_runs(value, cwd, depth + 1)


PRINTED_OPEN = re.compile(r"(?:=|\b(?:write-host|write-output|write-warning|write-error|echo|printf))\s*$", re.IGNORECASE)


def _in_printed_string(text):
    """True when `text` ends inside a quote that opened as a value or a print argument, so a run name after
    it is printed text. A quote opened as a command argument (`-ArgumentList '...'`) is still a run."""
    quote, opened = None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote, opened = ch, i
    return quote is not None and bool(PRINTED_OPEN.search(text[:opened]))


def _file_runs(path, cwd, depth):
    """Wide runs inside a script file the command executes."""
    for base in (cwd, ROOT):
        full = os.path.normpath(os.path.join(base, path))
        if os.path.isfile(full):
            break
    else:
        return
    rel = full.replace("\\", "/")
    if "/.claude/tests/" in rel or rel.endswith(".md") or os.path.getsize(full) > MAX_SCRIPT_BYTES:
        return
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return
    if full.lower().endswith('.py'):
        yield from _python_runs(text, cwd, depth)
        return
    if full.lower().endswith('.ps1'):
        text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    for line in _logical_lines(text):
        if line.lstrip().startswith(("#", "//", "REM ")):
            continue
        found = list(command_runs(line, cwd, depth + 1))
        yield from found
        if found:
            continue
        for entry in ENTRIES:
            if entry in line and LAUNCHER_RE.search(line[:line.index(entry)]) and not _in_printed_string(line[:line.index(entry)]):
                wide = classify(entry, _words(line[line.index(entry) + len(entry):]))
                if wide:
                    yield wide
        match = re.search(r"dotnet\W+test\b", line)
        if match:
            wide = classify("dotnet", _words(line[match.end():]))
            if wide:
                yield wide


def command_runs(command, cwd, depth=0):
    """Wide runs a shell command (or script text) starts: (label, needles) pairs."""
    if depth > SCAN_DEPTH:
        return
    for raw, _ in shell_segments_with_comments(command):
        segment, index = executable_index(raw)
        if index >= len(segment):
            continue
        exe, args = _base(segment[index]), segment[index + 1:]
        if exe == "&" and args:
            exe, args = _base(args[0]), args[1:]
        lowered = [a.lower() for a in args]
        entry = exe if exe in ENTRIES else None
        if exe in POWERSHELLS:
            if "-file" in lowered and lowered.index("-file") + 1 < len(args):
                at = lowered.index("-file") + 1
                if _base(args[at]) in ENTRIES:
                    entry, args = _base(args[at]), args[at + 1:]
                else:
                    yield from _file_runs(args[at], cwd, depth)
            else:
                for flag in ("-command", "-c"):
                    if flag in lowered:
                        yield from command_runs(" ".join(args[lowered.index(flag) + 1:]), cwd, depth + 1)
                        break
        elif exe == "dotnet" and args[:1] == ["test"]:
            wide = classify("dotnet", args[1:])
            if wide:
                yield wide
            continue
        elif exe in INTERPRETERS:
            if "-c" in args and args.index("-c") + 1 < len(args):
                body = args[args.index("-c") + 1]
                if exe in ('python', 'python3', 'py'):
                    yield from _python_runs(body, cwd, depth)
                else:
                    yield from command_runs(body, cwd, depth + 1)
            else:
                script = next((a for a in args if not a.startswith("-")), None)
                if script:
                    yield from _file_runs(script, cwd, depth)
            continue
        elif entry is None and exe.endswith((".sh", ".ps1", ".bash", ".cmd", ".bat")):
            yield from _file_runs(segment[index], cwd, depth)
            continue
        if entry:
            wide = classify(entry, args)
            if wide:
                yield wide


def brief_text(transcript, env=None):
    """The dispatcher's text for this delegate, or None when no source is readable."""
    env = os.environ if env is None else env
    transcript = (transcript or "").replace("\\", "/")
    chunks = []
    if "/subagents/" in transcript:
        try:
            with open(transcript, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    message = (json.loads(line).get("message") or {}) if line.strip() else {}
                    if message.get("role") != "user":
                        continue
                    content = message.get("content")
                    if isinstance(content, str):
                        chunks.append(content)
                    elif isinstance(content, list):
                        chunks += [b.get("text", "") for b in content
                                   if isinstance(b, dict) and b.get("type") == "text"]
        except (OSError, ValueError):
            return None
    prompt = (env.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE") or "").strip()
    if prompt:
        for path in (prompt, prompt + ".amend.md"):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read())
            except OSError:
                pass
    return "\n".join(chunks) if chunks else None


def is_delegate(transcript, env=None):
    env = os.environ if env is None else env
    return "/subagents/" in (transcript or "").replace("\\", "/") or bool(env.get("CLAUDE_CODE_SIDECAR"))


def unnamed(runs, brief):
    """Labels of the runs the brief does not name."""
    text = re.sub(r"\s+", " ", brief or "")
    out = []
    for label, needles in runs:
        if needles is None or not all(re.sub(r"\s+", " ", n) in text for n in needles):
            if label not in out:
                out.append(label)
    return out


def deny_text(labels, where=""):
    shown = "; ".join(labels[:3]) + ("; ..." if len(labels) > 3 else "")
    return ("Wide test run refused%s: %s. It is not a test class or method filter and your brief does not name it. "
            "Run the changed items' class filters (`-Filter \"FullyQualifiedName~Tests.<Suite>.<Class>\"`); the "
            "namespace, tier, scope and gate runs are the dispatcher's at close. Canon: guards/author.md; "
            "change_control §Gate cadence." % (where, shown))


def hook(payload):
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return None
    transcript = payload.get("transcript_path")
    if not is_delegate(transcript):
        return None
    command = executable_text((payload.get("tool_input") or {}).get("command") or "")
    if ADMIT_SET.search(command):
        return ("Setting HARNESS_WIDE_RUN_ADMITTED refused: the .ps1 entry points set it themselves after their own "
                "check passes, so a delegate's command never sets it. Drop the assignment and run the changed "
                "items' class filters. Canon: guards/author.md.")
    cwd = payload.get("cwd") or os.getcwd()
    set_roots(command, cwd)
    runs = list(command_runs(command, cwd))
    labels = unnamed(runs, brief_text(transcript)) if runs else []
    return deny_text(labels) if labels else None


def main():
    if "--entry" in sys.argv:
        return entry_main()
    try:
        reason = hook(json.load(sys.stdin))
    except Exception as exc:  # noqa: BLE001 - a lost check is reported, not silent
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
              "wide_test_guard failed (%s: %s); the wide-run check did not run." % (type(exc).__name__, exc)}}))
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                   "permissionDecisionReason": reason}}))
    else:
        print("{}")
    return 0


def entry_main():
    """CLI for the .ps1 entry points: exit 2 with the deny on stderr when this process is a delegate's and
    the run is wide and unnamed. The entry points hold no payload, so the environment is the delegate fact."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True, choices=ENTRIES)
    parser.add_argument("--filter", default=None)
    parser.add_argument("--scope", default=None)
    parser.add_argument("--repo", default="")
    parser.add_argument("--flags", default="", help="comma-separated switches the caller passed")
    args = parser.parse_args()
    if os.environ.get("HARNESS_WIDE_RUN_ADMITTED") or not is_delegate(None):
        return 0
    set_roots("-Repo %s" % args.repo if args.repo else "", os.getcwd())
    words = ["-" + f for f in args.flags.split(",") if f]
    if args.filter is not None:
        words += ["-Filter", args.filter]
    if args.scope:
        words += ["-Scope", args.scope]
    wide = classify(args.entry, words)
    if wide and unnamed([wide], brief_text(None)):
        sys.stderr.write(deny_text([wide[0]], " by %s" % args.entry) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
