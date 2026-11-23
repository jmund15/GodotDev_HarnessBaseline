#!/usr/bin/env python3
"""
Floor helper (`_floor.HELPER_NAMES`) on Bash|PowerShell|Monitor: deny a recursive grep (V17).

DENY — any recursive grep that walks a tree, whatever its output bound:
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
- Routes named in the deny: the Grep tool, `rg`, `git grep`. Canon: CLAUDE.core.md §9.
- The reaper `runaway_scan_reaper.py` contains what slips past this parse.

Channel: `check(payload)` returns `permissionDecision: deny`; the floor skips a helper that fails to import or
raises. The volume and scope advisories this file once carried are V17's Lost column.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shell_lex import OPAQUE_WRAPPERS, XARGS_VALUES, base, command_index, options, walk  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Recursive grep: shell parse (words and bodies from `_shell_lex`)
# ---------------------------------------------------------------------------------------------

GREP_IMAGES = {"grep", "egrep", "fgrep"}
_THROUGH = tuple(OPAQUE_WRAPPERS)
# grep's options as GNU grep reads them (`_shell_lex.options`), so `-rn`, `-d recurse`, `--dir=recurse`, `--recur`
# and a flag after the operands all read as written.
_GREP = dict(
    short_values="efmABCdD",
    long=("recursive", "dereference-recursive", "extended-regexp", "fixed-strings", "basic-regexp", "perl-regexp",
          "ignore-case", "no-ignore-case", "word-regexp", "line-regexp", "no-messages", "invert-match", "version",
          "help", "byte-offset", "line-number", "with-filename", "no-filename", "only-matching", "quiet", "silent",
          "text", "count", "files-with-matches", "files-without-match", "initial-tab", "null", "null-data", "color",
          "colour", "line-buffered", "binary", "unix-byte-offsets"),
    long_values=("regexp", "file", "max-count", "after-context", "before-context", "context", "directories",
                 "devices", "include", "exclude", "exclude-dir", "exclude-from", "label", "binary-files"),
    aliases={"regexp": "e", "file": "f", "directories": "d", "recursive": "r", "dereference-recursive": "R",
             "max-count": "m", "after-context": "A", "before-context": "B", "context": "C", "devices": "D"})


def _image(token):
    return base(token.lstrip("`"))


def grep_is_recursive(args):
    """True when grep's own arguments select directory recursion."""
    parsed = options(list(args), **_GREP)
    return bool(parsed.flags & {"r", "R"}) or "recurse" in parsed.values.get("d", [])


def _grep_paths(args):
    """grep's path operands: every operand, minus the leading pattern unless `-e`/`-f` (`--regexp`/`--file`)
    supplied it."""
    parsed = options(list(args), **_GREP)
    return parsed.operands if ("e" in parsed.values or "f" in parsed.values) else parsed.operands[1:]


def _xargs_command(tokens):
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in XARGS_VALUES else 1
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


def command_runs_recursive_grep(tokens, via=None, upstream_find=False):
    """True when one command's words run a recursive grep: directly, under `xargs`, or under `find -exec`. A shell or
    PowerShell body is a body of its own (`_shell_lex.walk`)."""
    tokens = tokens[command_index(tokens, through=_THROUGH):]
    if not tokens:
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
        return command_runs_recursive_grep(_xargs_command(rest), "xargs", upstream_find)
    if image == "find":
        return any(command_runs_recursive_grep(cmd, "find") for cmd in _find_exec_commands(rest))
    return False


def shell_runs_recursive_grep(text):
    """True when shell text, or a body it runs, executes a recursive grep in any segment; a PowerShell body is read as
    PowerShell too."""
    for kind, body, segments, _depth in walk(text):
        if kind == "pwsh" and _ps_statement_greps(body):
            return True
        upstream = False
        for seg in segments:
            words = [w.value for w in seg.words]
            if words and command_runs_recursive_grep(words, None, upstream):
                return True
            head = words[command_index(words, through=_THROUGH):]
            is_find = bool(head) and _image(head[0]) == "find"
            upstream = (upstream or is_find) if seg.op in ("|", "|&") else False
    return False


# ---------------------------------------------------------------------------------------------
# Recursive grep: PowerShell parse. Its own scrub, because PowerShell's grammar is not the shell's: `''` inside single
# quotes, backtick escapes, `@'...'@` here-strings and `<# #>` comments.
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


def _ps_statement_greps(text):
    """True when one PowerShell statement of `text` runs `Get-ChildItem -Recurse` and `Select-String`."""
    return any(_PS_GCI_RECURSE.search(statement) and _PS_SELECT_STRING.search(statement)
               for statement in _ps_statements(_ps_scrub(text)))


def powershell_runs_recursive_grep(text):
    return _ps_statement_greps(text) or shell_runs_recursive_grep(_ps_scrub(text))


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
    "or `git grep -n <pat> -- <path>` (CLAUDE.core.md §9)."
)

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


def check(payload):
    """The deny for a Bash, PowerShell or Monitor call that runs a recursive grep, or None."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell", "Monitor"):
        return None
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not isinstance(command, str) or not runs_recursive_grep(payload["tool_name"], command):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": DENY_REASON}}


def main() -> None:
    try:
        out = check(json.load(sys.stdin))
    except Exception:
        sys.exit(0)
    if out:
        sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
