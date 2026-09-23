#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell|Monitor — deny recursive grep, nudge other unbounded scans.

DENY — recursive grep that walks a tree, whatever its output bound:
- A recursive grep that reaches a giant single-line or special file buffers without bound, and on
  Git Bash it keeps running after its parent shell, or a downstream `head`, exits. Measured
  2026-09-14: orphaned `grep -rln ProjectSuite .` and `grep -rn noclobber .claude/` reached
  14.3 GB and 21.5 GB private memory; available memory fell to 1 MB and the client killed
  background shells in several sessions. Output was small; execution was the problem, so neither
  `-l` nor a path operand nor a pipe to `head` makes the command safe.
- Matched shapes: `grep`/`egrep`/`fgrep` with `-r`, `-R`, `--recursive`,
  `--dereference-recursive`, `-d recurse` or `--directories=recurse` (combined short flags
  included), as any segment of a pipeline or list, under `xargs`, inside `bash -c`, and every
  grep run by `find -exec` or by `xargs` downstream of `find`. On PowerShell, `Get-ChildItem`
  (`gci`/`ls`/`dir`) `-Recurse` in the same statement as `Select-String` (`sls`).
- Not denied: a recursive grep whose every path operand is a named file or a one-level file glob
  (`*.py`), with no operand appended by `xargs`; `-r` then reads those files as plain grep does.
- The match is on the invocation: heredoc bodies, quoted arguments and comments are data, so
  `echo "grep -r"` and a commit message naming it are allowed.
- Routes named in the deny: the Grep tool, `rg`, `git grep`. Canon: CLAUDE.md §Tool Routing.
- The reaper `runaway_scan_reaper.py` contains what slips past this parse.

ADVISE — volume: a recursive `rg`/`find`/`ls -R` with no bounding token lands its full match set
in context (measured 2026-08-04: two ~70KB returns). Advice names `--max-count`, `-l`/`-c` and
the Grep tool's `head_limit`, never a pipe to `head`, which bounds output but not execution. An `rg`
whose path operands are all named files or one-level file globs, or that reads a pipe with no path
operand, walks no tree and is not flagged.

ADVISE — scope: `find` is blind to .gitignore and sweeps `.claude/worktrees/` (measured
2026-08-09: 261 worktree hits where the Grep tool returned 0). Not flagged when a flag or a
narrowing path operand scopes the walk, or when cwd is inside a nested checkout
(`.claude/worktrees/<checkout>/` or `.claude/.cache/<name>-worktrees/<checkout>/`).

Advisories are full on the first fire per axis and one line afterwards, re-armed by a compaction
(`_hook_state.fire_once_since_compaction`). The deny is emitted as `permissionDecision: deny`
through `pre_bash_dispatch.py`. Any internal error exits 0 silent.
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_scope import in_nested_checkout
from _command_text import executable_text
from _hook_state import fire_once_since_compaction

# ---------------------------------------------------------------------------------------------
# Recursive grep: shell parse
# ---------------------------------------------------------------------------------------------

GREP_IMAGES = {"grep", "egrep", "fgrep"}
SHELL_IMAGES = {"bash", "sh", "dash", "zsh"}
PWSH_IMAGES = {"powershell", "pwsh"}

# Short grep flags that take a value: the rest of the cluster, or the next token.
_GREP_VALUE_SHORT = set("efmABCdD")
# Long grep flags that take a separate value token when written without `=`.
_GREP_VALUE_LONG = ("regexp", "file", "max-count", "after-context", "before-context", "context",
                    "directories", "devices", "include", "exclude", "exclude-dir", "exclude-from",
                    "label", "binary-files")
_XARGS_VALUE_SHORT = {"-I", "-n", "-P", "-L", "-d", "-s", "-a", "-E"}

# Prefixes that precede the real command word in a segment.
_PREFIX_WORDS = {"!", "{", "}", "then", "do", "else", "elif", "if", "while", "until", "time",
                 "exec", "command", "builtin", "nohup", "sudo", "stdbuf"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PUNCT = "();<>|&\n"
_MAX_DEPTH = 4


def _image(token):
    name = token.lstrip("`").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _tokens(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return text.replace("\n", " \n ").split(" ")


def _long_name_matches(name, full):
    return len(name) >= 3 and full.startswith(name)


def grep_is_recursive(args):
    """True when grep's own arguments select directory recursion."""
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return False
        if tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            if _long_name_matches(name, "recursive") or _long_name_matches(name, "dereference-recursive"):
                return True
            if _long_name_matches(name, "directories"):
                if not eq:
                    value = args[i + 1] if i + 1 < len(args) else ""
                    i += 1
                if value == "recurse":
                    return True
            elif not eq and any(_long_name_matches(name, full) for full in _GREP_VALUE_LONG):
                i += 1
        elif tok.startswith("-") and len(tok) > 1:
            for j in range(1, len(tok)):
                ch = tok[j]
                if ch in "rR":
                    return True
                if ch in _GREP_VALUE_SHORT:
                    value = tok[j + 1:]
                    if not value:
                        value = args[i + 1] if i + 1 < len(args) else ""
                        i += 1
                    if ch == "d" and value == "recurse":
                        return True
                    break
        i += 1
    return False


def _grep_paths(args):
    """grep's path operands: every non-flag token, minus the leading pattern unless `-e`/`-f`
    (`--regexp`/`--file`) supplied it."""
    pattern_given, operands, i = False, [], 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            operands += args[i + 1:]
            break
        if tok.startswith("--"):
            name, eq, _value = tok[2:].partition("=")
            if _long_name_matches(name, "regexp") or _long_name_matches(name, "file"):
                pattern_given = True
            if not eq and any(_long_name_matches(name, full) for full in _GREP_VALUE_LONG):
                i += 1
        elif tok.startswith("-") and len(tok) > 1:
            for j in range(1, len(tok)):
                if tok[j] in _GREP_VALUE_SHORT:
                    pattern_given = pattern_given or tok[j] in "ef"
                    if not tok[j + 1:]:
                        i += 1
                    break
        else:
            operands.append(tok)
        i += 1
    return operands if pattern_given else operands[1:]


def _strip_prefixes(tokens):
    """Drop assignments, keywords and transparent wrappers before the command word."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        image = _image(tok)
        if _ASSIGNMENT.match(tok) or image in _PREFIX_WORDS:
            i += 1
        elif image in ("env", "nice"):
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-") or _ASSIGNMENT.match(tokens[i])):
                i += 2 if tokens[i] == "-n" else 1
        elif image == "timeout":
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 2 if tokens[i] in ("-s", "-k") else 1
            i += 1  # the duration
        else:
            break
    return tokens[i:]


def _xargs_command(tokens):
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in _XARGS_VALUE_SHORT else 1
    return tokens[i:]


def _find_exec_commands(tokens):
    out = []
    i = 0
    while i < len(tokens):
        if tokens[i] in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < len(tokens) and tokens[j] not in (";", "+"):
                j += 1
            out.append(tokens[i + 1:j])
            i = j
        i += 1
    return out


def command_runs_recursive_grep(tokens, depth=0, via=None, upstream_find=False):
    tokens = _strip_prefixes(tokens)
    if not tokens or depth > _MAX_DEPTH:
        return False
    image, rest = _image(tokens[0]), tokens[1:]
    if image in GREP_IMAGES:
        if via == "find" or (via == "xargs" and upstream_find):
            return True
        if not grep_is_recursive(rest):
            return False
        # Only named files and one-level file globs: -r walks no tree. Under xargs, stdin
        # appends operands the text cannot show.
        paths = _grep_paths(rest)
        return via == "xargs" or not paths or not all(_is_file_operand(path) for path in paths)
    if image == "xargs":
        return command_runs_recursive_grep(_xargs_command(rest), depth + 1, "xargs", upstream_find)
    if image == "find":
        return any(command_runs_recursive_grep(cmd, depth + 1, "find")
                   for cmd in _find_exec_commands(rest))
    if image in SHELL_IMAGES:
        for k, tok in enumerate(rest):
            if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", tok) and k + 1 < len(rest):
                return shell_runs_recursive_grep(rest[k + 1], depth + 1)
        return False
    if image in PWSH_IMAGES:
        for k, tok in enumerate(rest):
            if tok.lower() in ("-command", "-c") and k + 1 < len(rest):
                return powershell_runs_recursive_grep(" ".join(rest[k + 1:]), depth + 1)
        return False
    return False


def shell_runs_recursive_grep(text, depth=0):
    """True when POSIX-shell text executes a recursive grep in any segment."""
    tokens = _tokens(executable_text(text))
    segment, pipeline_find, skip_next = [], False, False

    def flush(seg, upstream):
        return bool(seg) and command_runs_recursive_grep(seg, depth, None, upstream)

    for tok in tokens + [";"]:
        if skip_next:
            skip_next = False
            continue
        if tok and set(tok) <= set(_PUNCT):
            if ("<" in tok or ">" in tok) and set(tok) <= set("<>&"):
                if segment and segment[-1].isdigit():
                    segment.pop()  # `2>`: the fd number is the redirect's, not an argument
                skip_next = True
                continue
            if segment and segment[0].startswith("#"):
                segment = []
            if flush(segment, pipeline_find):
                return True
            is_find = bool(segment) and _image((_strip_prefixes(segment) or [""])[0]) == "find"
            pipeline_find = (pipeline_find or is_find) if tok in ("|", "|&") else False
            segment = []
            continue
        if tok.startswith("#") and not segment:
            segment = [tok]
            continue
        if segment and segment[0].startswith("#"):
            continue
        segment.append(tok)
    return False


# ---------------------------------------------------------------------------------------------
# Recursive grep: PowerShell parse
# ---------------------------------------------------------------------------------------------

_PS_HERESTRING = re.compile(r"@(['\"])\r?\n.*?\r?\n\1@", re.DOTALL)
_PS_BLOCK_COMMENT = re.compile(r"<#.*?#>", re.DOTALL)
_PS_SINGLE = re.compile(r"'(?:[^']|'')*'")
_PS_DOUBLE = re.compile(r'"(?:[^"`]|`.)*"', re.DOTALL)
_PS_LINE_COMMENT = re.compile(r"#[^\n]*")
_PS_GCI_RECURSE = re.compile(
    r"(?<![\w-])(?:Get-ChildItem|gci|ls|dir)(?![\w-])[^|;\n)}]*?"
    r"(?<![\w-])-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?(?![\w-])",
    re.IGNORECASE,
)
_PS_SELECT_STRING = re.compile(r"(?<![\w-])(?:Select-String|sls)(?![\w-])", re.IGNORECASE)


def _ps_scrub(text):
    text = _PS_HERESTRING.sub("''", text)
    text = _PS_BLOCK_COMMENT.sub(" ", text)
    text = _PS_SINGLE.sub("''", text)
    text = _PS_DOUBLE.sub("''", text)
    text = _PS_LINE_COMMENT.sub("", text)
    return re.sub(r"(\|\s*\r?\n)|(`\r?\n)", " | ", text).replace(" |  | ", " | ")


def _ps_statements(text):
    out, depth, current = [], 0, []
    for ch in text:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth = max(0, depth - 1)
        if ch in ";\n" and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(ch)
    out.append("".join(current))
    return out


def powershell_runs_recursive_grep(text, depth=0):
    scrubbed = _ps_scrub(text)
    for statement in _ps_statements(scrubbed):
        if _PS_GCI_RECURSE.search(statement) and _PS_SELECT_STRING.search(statement):
            return True
    return depth <= _MAX_DEPTH and shell_runs_recursive_grep(scrubbed, depth + 1)


def runs_recursive_grep(tool, command):
    if not command:
        return False
    if tool == "PowerShell":
        return powershell_runs_recursive_grep(command)
    return shell_runs_recursive_grep(command)


DENY_REASON = (
    "RECURSIVE GREP DENIED: `grep -r`, `find -exec grep`, `find | xargs grep` and "
    "`Get-ChildItem -Recurse | Select-String` can run away without bound. Use the Grep tool "
    "(`path`/`glob`, `head_limit`), `rg -n <pat> <dir> --max-count N` (`-uu` for ignored trees), "
    "or `git grep -n <pat> -- <path>` (CLAUDE.md §Tool Routing)."
)

# ---------------------------------------------------------------------------------------------
# Advisories
# ---------------------------------------------------------------------------------------------

# A scan that walks a tree and prints matching LINES. Recursive grep is denied above.
RECURSIVE_SCAN = re.compile(
    r"(?:^|[|;&]\s*|\s)(?:rg\s|find\s|ls\s+-[a-zA-Z]*R|dir\s+/s)",
    re.IGNORECASE,
)

# Any of these means the caller already bounded or reduced the output.
BOUNDED = re.compile(
    r"(?:\|\s*(?:head|tail|wc|uniq|sort\s+-u|Select-Object|measure))"
    r"|(?:\s-[a-zA-Z]*(?:l|c|q)\b)"
    r"|(?:--files-with-matches|--count|--quiet|--max-count|-m\s*\d|-m\d)"
    r"|(?:\s-print0)"
    r"|(?:head_limit)",
    re.IGNORECASE,
)

# Walks a tree while blind to .gitignore. `rg` and `git grep` honour it.
GITIGNORE_BLIND = re.compile(r"(?:^|[|;&]\s*|\s)find\s", re.IGNORECASE)

# The caller already scoped the walk, or handed it to a gitignore-aware tool.
SCOPED = re.compile(
    r"--exclude-dir|--exclude|-prune|\s-path\s|git\s+grep|git\s+ls-files",
    re.IGNORECASE,
)

# Operands that name the whole tree rather than narrowing it.
BROAD_OPERANDS = {".", "./", "/", "~", "~/", "$HOME", "$PWD", "*"}

# Flags whose VALUE is a separate token, so the value is not a path operand.
VALUE_FLAGS = {
    "-e", "-f", "-m", "-d", "-A", "-B", "-C", "--include", "--exclude",
    "--exclude-dir", "--max-count", "-name", "-iname", "-type", "-path", "-regex",
}

SCAN_VERBS = ("grep", "rg", "find", "ls", "dir")
PATH_FIRST_VERBS = ("find", "ls", "dir")

# Bare operator tokens end the scan's own operand list.
PIPELINE_OPERATORS = {"|", "||", "&&", "&", ";"}


def _scan_paths(command: str):
    """(verb, path operands) of the command's first scan verb, or (None, []).

    Tokenize before splitting on operators: a quoted `\\|` in a pattern is one token."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None, []

    verb_index = None
    for index, token in enumerate(tokens):
        if os.path.basename(token) in SCAN_VERBS:
            verb_index = index
            break
    if verb_index is None:
        return None, []

    operands = _operands_after(tokens, verb_index)
    verb = os.path.basename(tokens[verb_index])
    return verb, (operands if verb in PATH_FIRST_VERBS else operands[1:])


def _operands_after(tokens, verb_index):
    """Non-flag operands of the command word at `verb_index`, up to the next pipeline operator."""
    operands, skip_next = [], False
    for token in tokens[verb_index + 1:]:
        if token in PIPELINE_OPERATORS:
            break
        # shlex glues a separator to the previous word (`'pat';`): that word is the list's last operand.
        ends_list = token.endswith((";", "&&", "||")) and token not in PIPELINE_OPERATORS
        token = token.rstrip(";&|") if ends_list else token
        if skip_next:
            skip_next = False
        elif token.startswith("-"):
            skip_next = token in VALUE_FLAGS
        elif token:
            operands.append(token)
        if ends_list:
            break
    return operands


def has_narrowing_path(command: str) -> bool:
    """True when the scan names a path operand that bounds the walk to a subtree or to explicit files."""
    _verb, paths = _scan_paths(command)
    return any(path not in BROAD_OPERANDS for path in paths)


# A named file: a stem that does not start with a dot, then an extension; no glob characters.
FILE_OPERAND = re.compile(r"^[^*?\[\]./][^*?\[\]]*\.[A-Za-z0-9]{1,8}$")
# A one-level file glob (`*.py`, `test_?.sh`): the shell expands it to files before rg runs.
FILE_GLOB_OPERAND = re.compile(r"^[^./\[\]][^\[\]]*\.[A-Za-z0-9]{1,8}$")


def _is_file_operand(path: str, cwd: str = "") -> bool:
    head, base = os.path.split(path)
    if any(ch in head for ch in "*?[]"):
        return False
    if FILE_OPERAND.match(base):
        # A directory can carry a file-shaped name (`config.d`); rg walks it.
        return not os.path.isdir(os.path.join(cwd, path) if cwd else path)
    return bool(FILE_GLOB_OPERAND.match(base))


def rg_walks_no_tree(command: str, cwd: str = "") -> bool:
    """True when every tree-scan match is an `rg`, and each `rg` either reads a pipe (no path
    operand, fed by `|`) or names only files and one-level file globs."""
    if any("rg" not in match.group(0).lower() for match in RECURSIVE_SCAN.finditer(command)):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    found = False
    for index, token in enumerate(tokens):
        if os.path.basename(token) != "rg":
            continue
        paths = _operands_after(tokens, index)[1:]
        if paths:
            if not all(_is_file_operand(p, cwd) for p in paths):
                return False
        else:
            previous = tokens[index - 1] if index else ""
            if not previous.endswith("|") or previous.endswith("||"):
                return False
        found = True
    return found


def in_worktree(cwd: str) -> bool:
    """Inside a nested checkout (any layout `_claude_scope` rebases) the scope advisory would warn
    against the caller's own tree."""
    return in_nested_checkout(cwd or "")


SCOPE_ADVICE = (
    "⚠ GITIGNORE-BLIND SCAN — `find` sweeps `.claude/worktrees/`, whose duplicate hits look "
    "real; capping output does not fix that. Use the Grep or Glob tool, `git ls-files`, or "
    "`find` with `-path ./.claude -prune` or a narrower root (CLAUDE.md §Tool Routing)."
)

ADVICE = (
    "⚠ UNBOUNDED RECURSIVE SCAN — output size is unknown, and every match stays in context. "
    "Bound it: `rg --max-count N`, `-l`/`-c`, a narrower path/glob, or the Grep tool's "
    "`head_limit` (CLAUDE.md §Tool Routing)."
)

SCOPE_ADVICE_SHORT = (
    "⚠ GITIGNORE-BLIND SCAN — sweeps `.claude/worktrees/`. Use the Grep tool, `git ls-files`, "
    "or `find` with `-path ./.claude -prune`."
)
ADVICE_SHORT = (
    "⚠ UNBOUNDED RECURSIVE SCAN — cap it (`--max-count N`), reduce it (`-l`/`-c`), or use the "
    "Grep tool's `head_limit`."
)


def needs_bound(command: str, cwd: str = "") -> bool:
    if not command:
        return False
    return (bool(RECURSIVE_SCAN.search(command)) and not BOUNDED.search(command)
            and not rg_walks_no_tree(command, cwd))


def needs_scope(command: str, cwd: str = "") -> bool:
    if not command:
        return False
    if not GITIGNORE_BLIND.search(command):
        return False
    if SCOPED.search(command) or has_narrowing_path(command) or in_worktree(cwd):
        return False
    return True


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool = input_data.get("tool_name")
    if tool not in ("Bash", "PowerShell", "Monitor"):
        sys.exit(0)

    tool_input = input_data.get("tool_input") or {}
    command = tool_input.get("command") or ""

    if runs_recursive_grep(tool, command):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        }))
        sys.exit(0)

    session_id = input_data.get("session_id") or ""
    advisories = []
    if needs_bound(command, input_data.get("cwd") or ""):
        first = fire_once_since_compaction(session_id, "unbounded_scan:bound")
        advisories.append(ADVICE if first else ADVICE_SHORT)
    if needs_scope(command, input_data.get("cwd") or ""):
        first = fire_once_since_compaction(session_id, "unbounded_scan:scope")
        advisories.append(SCOPE_ADVICE if first else SCOPE_ADVICE_SHORT)
    if not advisories:
        sys.exit(0)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n\n".join(advisories),
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
