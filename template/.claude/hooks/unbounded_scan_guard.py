#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell|Monitor — rewrite or deny recursive grep, nudge other unbounded scans.

REWRITE — Bash `grep -r` segments with known flags and non-ignored repo paths run as
`git grep --untracked` through `updatedInput`. Literal cwd changes (`cd`, Git Bash drive spellings
included), quoted patterns, pipelines and trailing redirects stay intact; include/exclude filters
become pathspecs. One-level glob operands expand as bash expands them; `**` and an empty match stay
denied. The note names the same files minus gitignored ones (`recursive_grep_rewrite`). A simple
recursive grep whose paths lie outside git (session transcripts, the vault) or under a gitignored
tree runs as `rg -uu --max-count 200` with clipped lines, `-F` kept and `-E` left to rg's default
regex; include/exclude become rg `-g` globs. `-P`, `-z`, unknown flags, backreferences and
basic-regex `+?|(){}` stay denied.

DENY — any other recursive grep that walks a tree, whatever its output bound:
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

ADVISE — volume: a recursive `rg`/`find`/`ls -R` with no bounding token lands its full match set
in context (measured 2026-08-04: two ~70KB returns). Advice names `--max-count`, `-l`/`-c` and
the Grep tool's `head_limit`, never a pipe to `head`, which bounds output but not execution. An `rg`
whose path operands are all named files or one-level file globs, or that reads a pipe with no path
operand, walks no tree and is not flagged.

ADVISE — scope: `find` is blind to .gitignore and sweeps `.claude/worktrees/` (measured
2026-08-09: 261 worktree hits where the Grep tool returned 0). Not flagged when a flag or a
narrowing path operand scopes the walk, or when cwd is inside a nested checkout
(`.claude/worktrees/<checkout>/` or `.claude/.cache/<name>-worktrees/<checkout>/`).

Both advisory axes use `_command_text.executable_text`: heredoc data neither triggers advice nor bounds a real scan.
Advisories are full on the first fire per axis and one line afterwards, re-armed by a compaction
(`_hook_state.fire_once_since_compaction`). The deny is emitted as `permissionDecision: deny`
through `pre_bash_dispatch.py`. Any internal error exits 0 silent.
"""

import glob
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_scope import in_nested_checkout
from _command_text import executable_text
from _hook_state import fire_once_since_compaction
from _win_path import native_path

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


# ---------------------------------------------------------------------------------------------
# Recursive grep: the rewrite the call's own values settle
# ---------------------------------------------------------------------------------------------

def _shell_parts(text):
    """Keep source spans intact; operators inside quoted patterns are not shell operators."""
    parts, start, quote, escaped = [], 0, None, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char in "`$" and quote != "'":
            return None
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char in "()":
            return None
        elif char in "|;&\n":
            if char == "&" and i and text[i - 1] in "<>":
                continue
            separator = char
            if char in "|&" and text[i:i + 2] == char * 2:
                separator = char * 2
                escaped = True
            elif char == "&":
                return None
            parts.append((text[start:i], separator))
            start = i + len(separator)
    if quote or escaped:
        return None
    parts.append((text[start:], ""))
    return parts


_REDIRECT_SUFFIX = re.compile(r'''(?:\s*\d*(?:>>?|<)\s*(?:'[^']*'|"[^"]*"|[^\s<>;&|]+))+\s*$''')


def _grep_redirect(segment):
    quote, escaped = None, False
    for i, char in enumerate(segment):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "<>":
            start = i
            while start and segment[start - 1].isdigit():
                start -= 1
            if start and not segment[start - 1].isspace():
                return None
            suffix = segment[start:]
            return (segment[:start].rstrip(), suffix) if _REDIRECT_SUFFIX.fullmatch(suffix) else None
    return segment.strip(), ""
_GIT_GREP_FLAGS = {"n": "-n", "i": "-i", "l": "-l", "c": "-c", "w": "-w", "F": "-F", "E": "-E", "o": "-o",
                   "h": "-h", "I": "-I"}
_DROPPED_FLAGS = set("rRHs")
_LONG_FLAGS = {"recursive": None, "line-number": "-n", "ignore-case": "-i", "files-with-matches": "-l",
               "count": "-c", "word-regexp": "-w", "fixed-strings": "-F", "extended-regexp": "-E",
               "only-matching": "-o"}
_GNU_BRE = re.compile(r"\\[+?(){}]")


def _git(cwd, *args):
    import subprocess
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def recursive_grep_rewrite(command, cwd):
    """Rewrite known grep segments, preserving other commands and literal cwd changes. Unknown
    shell evaluation, paths or flags leave the whole call denied, never a partial rewrite."""
    parts = _shell_parts(command) if command else None
    if parts is None:
        return None
    rewritten, notes = [], []
    for segment, separator in parts:
        try:
            words = shlex.split(segment)
        except ValueError:
            return None
        if words and words[0] == "cd":
            if len(words) != 2 or separator not in ("&&", ";", "\n"):
                return None
            cwd = os.path.abspath(os.path.join(cwd, native_path(words[1])))
            if not os.path.isdir(cwd):
                return None
        elif words and words[0] in ("pushd", "popd", "for", "while", "until", "if", "eval", "source", "."):
            return None
        elif words and words[0] == "grep" and grep_is_recursive(words[1:]):
            redirect = _grep_redirect(segment)
            if redirect is None:
                return None
            body, suffix = redirect
            found = _plain_grep_rewrite(body, cwd)
            if found is None:
                return None
            replacement, note = found
            notes.append(note)
            leading = segment[:len(segment) - len(segment.lstrip())]
            trailing = segment[len(segment.rstrip()):]
            segment = leading + replacement + (" " + suffix if suffix else trailing)
        rewritten.append(segment + separator)
    result = "".join(rewritten)
    if not notes or runs_recursive_grep("Bash", result):
        return None
    return result, notes[0] if len(set(notes)) == 1 else REWRITE_NOTE_MIXED


def _plain_grep_rewrite(command, cwd):
    """(command, note) for one simple recursive grep, or None when no tool states its meaning."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    flags, patterns, operands, globs, exclusions, i = [], [], [], [], [], 1
    args = tokens[1:]
    while i - 1 < len(args):
        tok = args[i - 1]
        i += 1
        if tok == "--":
            operands += args[i - 1:]
            break
        if tok.startswith("--"):
            name, eq, value = tok[2:].partition("=")
            if name in ("include", "exclude", "exclude-dir"):
                if not eq:
                    if i - 1 >= len(args):
                        return None
                    value = args[i - 1]
                    i += 1
                if not value or "/" in value:
                    return None
                if name == "include":
                    globs.append(value)
                else:
                    exclusions.append((name, value))
            elif name in ("max-count", "after-context", "before-context", "context") and eq and value.isdigit():
                flags += ["--" + name, value]
            elif name in _LONG_FLAGS and not eq:
                if _LONG_FLAGS[name]:
                    flags.append(_LONG_FLAGS[name])
            else:
                return None
        elif tok.startswith("-") and len(tok) > 1:
            j = 1
            while j < len(tok):
                ch = tok[j]
                if ch in _DROPPED_FLAGS:
                    pass
                elif ch in _GIT_GREP_FLAGS:
                    flags.append(_GIT_GREP_FLAGS[ch])
                elif ch in "ABCme":
                    value = tok[j + 1:] or (args[i - 1] if i - 1 < len(args) else None)
                    if not tok[j + 1:]:
                        i += 1
                    if value is None or (ch != "e" and not value.isdigit()):
                        return None
                    if ch == "e":
                        patterns.append(value)
                    else:
                        flags += ["--max-count" if ch == "m" else "-" + ch, value]
                    break
                else:
                    return None
                j += 1
        else:
            operands.append(tok)
    if not patterns:
        if not operands:
            return None
        patterns, operands = [None, operands[0]], operands[1:]
    if any(p and "-F" not in flags and "-E" not in flags and _GNU_BRE.search(p) for p in patterns):
        return None
    operands = _expand_globs(operands or ["."], cwd)
    if operands is None:
        return None
    repo = _git_grep_command(cwd, flags, patterns, operands, globs, exclusions)
    if repo:
        return repo, REWRITE_NOTE
    rg = _rg_command(cwd, flags, patterns, operands, globs, exclusions)
    return (rg, REWRITE_NOTE_RG) if rg else None


def _expand_globs(operands, cwd):
    """Expand one-level glob operands as bash does (sorted, dotfiles skipped). `**` and a glob that
    matches nothing return None: the first walks a tree the text does not bound, the second names no file."""
    out = []
    for path in operands:
        if not re.search(r"[*?\[]", path):
            out.append(path)
        elif "**" in path:
            return None
        else:
            matches = sorted(glob.glob(os.path.join(cwd, path)))
            if not matches:
                return None
            out += [m if os.path.isabs(path) else os.path.relpath(m, cwd).replace("\\", "/") for m in matches]
    return out


def _git_grep_command(cwd, flags, patterns, operands, globs, exclusions):
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return None
    root = os.path.normcase(os.path.abspath(top.stdout.strip()))
    for path in operands:
        full = os.path.normcase(os.path.abspath(os.path.join(cwd, path)))
        if not os.path.exists(full) or not (full == root or full.startswith(root.rstrip(os.sep) + os.sep)):
            return None
        ignored = _git(cwd, "check-ignore", "-q", path)
        if ignored is None or ignored.returncode == 0:
            return None
    if globs:
        specs = [":(glob)%s**/%s" % ("" if p in (".", "./") else p.rstrip("/") + "/", g)
                 for p in operands for g in globs]
    else:
        specs = operands
    specs += [":(exclude,glob)**/%s%s" % (value, "/**" if name == "exclude-dir" else "")
              for name, value in exclusions]
    words = ["git", "grep", "--untracked"] + flags
    if patterns[0] is None:
        words += ["-e", patterns[1]]
    else:
        for p in patterns:
            words += ["-e", p]
    words += ["--"] + specs
    return " ".join(shlex.quote(w) for w in words)


# Bounds for a grep outside git: rg's `--max-count` is per file, `--max-columns` clips a giant
# single-line record (session transcripts) to a preview.
_RG_MAX_COUNT = ["--max-count", "200"]
_RG_CLIP = ["--max-columns", "500", "--max-columns-preview"]
_RG_FLAG_MAP = {"-h": "--no-filename", "-I": None, "-E": None}
_RG_UNSAFE_BRE = re.compile(r"[+?|(){}]")
_BACKREFERENCE = re.compile(r"\\[1-9]")


def _rg_command(cwd, flags, patterns, operands, globs, exclusions):
    """A bounded `rg` for a simple recursive grep whose paths git grep cannot take: outside git, or
    under a gitignored tree. include/exclude become rg `-g` globs. rg's regex reads as ERE, so a
    basic-regex pattern with ERE metacharacters, or any backreference, has no faithful translation."""
    texts = [p for p in patterns if p]
    fixed, extended = "-F" in flags, "-E" in flags
    if any(_BACKREFERENCE.search(t) for t in texts) and not fixed:
        return None
    if not fixed and not extended and any(_RG_UNSAFE_BRE.search(t) for t in texts):
        return None
    paths = []
    for path in operands:
        shown = os.path.expanduser(path).replace("\\", "/") if path.startswith("~") else path
        full = os.path.abspath(os.path.join(cwd, native_path(shown)))
        if not os.path.exists(full):
            return None
        where = full if os.path.isdir(full) else os.path.dirname(full)
        inside = _git(where, "rev-parse", "--is-inside-work-tree")
        if inside is None:
            return None
        if inside.returncode == 0:
            ignored = _git(where, "check-ignore", "-q", full)
            if ignored is None or ignored.returncode != 0:
                return None
        paths.append(shown)
    counting = "-c" in flags or "-l" in flags
    words = ["rg", "-uu"]
    for flag in flags:
        mapped = _RG_FLAG_MAP.get(flag, flag)
        if mapped:
            words.append(mapped)
    if not counting and "--max-count" not in flags:
        words += _RG_MAX_COUNT
    words += _RG_CLIP
    for glob_value in globs:
        words += ["-g", glob_value]
    for name, value in exclusions:
        words += ["-g", "!" + value + ("/" if name == "exclude-dir" else "")]
    for p in texts:
        words += ["-e", p]
    words += ["--"] + paths
    return " ".join(shlex.quote(w) for w in words)


REWRITE_NOTE_RG = ("[scan] recursive grep outside git or under a gitignored tree ran as a bounded `rg -uu` "
                   "(hidden and ignored files included; --max-count and clipped lines; CLAUDE.core.md \u00a79).")
REWRITE_NOTE_MIXED = ("[scan] recursive greps ran as `git grep --untracked` inside the checkout and as a bounded "
                      "`rg -uu` outside git or under gitignored trees (CLAUDE.core.md \u00a79).")
REWRITE_NOTE = ("[scan] recursive grep ran as `git grep --untracked` (the same files minus gitignored ones; "
                "CLAUDE.core.md §9).")

DENY_REASON = (
    "RECURSIVE GREP DENIED: `grep -r`, `find -exec grep`, `find | xargs grep` and "
    "`Get-ChildItem -Recurse | Select-String` can run away without bound. Use the Grep tool "
    "(`path`/`glob`, `head_limit`), `rg -n <pat> <dir> --max-count N` (`-uu` for ignored trees), "
    "or `git grep -n <pat> -- <path>` (CLAUDE.core.md §9)."
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
GITIGNORE_BLIND = re.compile(r"(?:^|[|;&(`{\n]\s*)(?:(?:time|nice|nohup|exec|sudo|xargs|env)\s+)*find\s", re.IGNORECASE)

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
    "`find` with `-path ./.claude -prune` or a narrower root (CLAUDE.core.md §9)."
)

ADVICE = (
    "⚠ UNBOUNDED RECURSIVE SCAN — output size is unknown, and every match stays in context. "
    "Bound it: `rg --max-count N`, `-l`/`-c`, a narrower path/glob, or the Grep tool's "
    "`head_limit` (CLAUDE.core.md §9)."
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
    command = executable_text(command or "")
    if not command:
        return False
    return (bool(RECURSIVE_SCAN.search(command)) and not BOUNDED.search(command)
            and not rg_walks_no_tree(command, cwd))


def needs_scope(command: str, cwd: str = "") -> bool:
    command = executable_text(command or "")
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
        rewrite = recursive_grep_rewrite(command, input_data.get("cwd") or ".") if tool == "Bash" else None
        if rewrite:
            rewritten, note = rewrite
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": dict(tool_input, command=rewritten),
                "additionalContext": note,
            }}))
            sys.exit(0)
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
