"""The floor's one shell lexer (T1). Every floor module reads a shell command through it:

    lex(command)               -> [Segment]  each simple command: words, redirects, heredocs, substitutions, the
                                             operator that ends it, its source span
    walk(command)              -> (kind, text, [Segment], depth) for the command and every body it runs: `$(...)`,
                                  backticks, `<(...)`, `bash -c`, `eval`, `watch`, `cmd /c`, `pwsh -Command`, and a
                                  heredoc fed to a shell
    options(args, ...)         -> Options  GNU/git option parsing: bundled and split short flags, a value in the
                                  bundle or the next word, unique long prefixes, `--name=value`, options after
                                  operands, `--`
    ps_param(word, name)       -> bool  a PowerShell parameter spelled by any prefix, `-Name:value` included
    cmd_switches(words)        -> set   cmd.exe switches: `/S`, `//S` (Git Bash), `/F/S/Q`, `-S` for a tool taking `-`

A word's `value` is what the shell passes (quotes removed, escapes applied; a substitution stays as its source text,
so a reader sees it is not literal), `text` keeps backslashes (a Windows path), `raw` is the source. Quoted text is
one word, so `grep "rm -rf"` holds no `rm` word while `"rm" -rf` does. A heredoc body is data unless a shell reads
it; its `$(...)` still runs when the delimiter is unquoted. An unterminated quote is a literal character, so a malformed
command still splits at every separator and can only over-block. Stdlib only.
"""
import os
import re
from collections import namedtuple

Word = namedtuple("Word", "value text raw start end")
Redirect = namedtuple("Redirect", "op target start end")
Heredoc = namedtuple("Heredoc", "delimiter quoted body start end reader")
Substitution = namedtuple("Substitution", "body start end")
Segment = namedtuple("Segment", "words redirects heredocs substitutions start end op")
Options = namedtuple("Options", "flags exact values operands")

_BLANKS = " \t\r"
_OPERATORS = ("&&", "||", "|&", ";;&", ";;", ";&", "|", "&", ";", "(", ")")
_REDIRECT_OPS = ("&>>", "&>", "<<<", "<<-", "<<", "<>", "<&", ">>", ">&", ">|", "<", ">")
_WORD_STOP = set(_BLANKS) | set("\n;&|()<>")
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "v": "\v",
         "\\": "\\", "'": "'", '"': '"', "?": "?"}
_MAX_DEPTH = 8

# Programs that run a quoted argument or stdin as shell code.
SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh", "su", "flock", "script"))
POWERSHELLS = frozenset(("pwsh", "powershell"))
_SHELL_WORDS = SHELLS | POWERSHELLS


def base(word):
    """The program name a word runs: basename, lowercase, without `.exe`."""
    return os.path.basename(str(word).replace("\\", "/")).lower().removesuffix(".exe")


class _Lexer:
    def __init__(self, s):
        self.s, self.n = s, len(s)

    # -- quoted and substituted spans -----------------------------------------------------------------------------
    def _squote_end(self, i):
        j = self.s.find("'", i)
        return self.n if j < 0 else j

    def _dquote_end(self, i):
        """Index of the `"` closing a double-quoted span whose text starts at `i`."""
        s, n = self.s, self.n
        while i < n and s[i] != '"':
            if s[i] == "\\":
                i += 2
            elif s.startswith("$(", i):
                i = self._paren_end(i + 2) + 1
            elif s[i] == "`":
                i = self._backtick_end(i + 1) + 1
            else:
                i += 1
        return min(i, n)

    def _backtick_end(self, i):
        s, n = self.s, self.n
        while i < n and s[i] != "`":
            i += 2 if s[i] == "\\" else 1
        return min(i, n)

    def _brace_end(self, i):
        depth, s, n = 1, self.s, self.n
        while i < n:
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == "'":
                i = self._squote_end(i + 1) + 1
                continue
            if s[i] == '"':
                i = self._dquote_end(i + 1) + 1
                continue
            depth += (s[i] == "{") - (s[i] == "}")
            if depth == 0:
                return i
            i += 1
        return n

    def _paren_end(self, i):
        """Index of the `)` closing a `$(`/`<(`/`>(` whose body starts at `i`. A `)` inside `case ... esac` closes a
        pattern, not the substitution."""
        s, n = self.s, self.n
        depth = cases = 0
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "'":
                i = self._squote_end(i + 1) + 1
                continue
            if c == '"':
                i = self._dquote_end(i + 1) + 1
                continue
            if c == "`":
                i = self._backtick_end(i + 1) + 1
                continue
            if s.startswith("$(", i):
                i = self._paren_end(i + 2) + 1
                continue
            if c in "ce" and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] in "_-")):
                m = re.match(r"(case|esac)(?![\w-])", s[i:i + 5])
                if m:
                    cases += 1 if m.group(1) == "case" else -1 if cases else 0
                    i += 4
                    continue
            if c == "(":
                depth += 1
            elif c == ")":
                if cases:
                    pass
                elif depth:
                    depth -= 1
                else:
                    return i
            i += 1
        return n

    # -- one word ---------------------------------------------------------------------------------------------------
    def word(self, i, subs):
        """(Word, end) for the word starting at `i`; its substitution bodies are appended to `subs`."""
        s, n = self.s, self.n
        start, value, text = i, [], []
        while i < n:
            c = s[i]
            if c in "<>" and s.startswith("(", i + 1):
                end = self._paren_end(i + 2)
                subs.append(Substitution(s[i + 2:end], i + 2, end))
                value.append(s[i:end + 1])
                text.append(s[i:end + 1])
                i = end + 1
                continue
            if c in _WORD_STOP:
                break
            if c == "\\":
                if s.startswith("\n", i + 1):
                    i += 2
                    continue
                value.append(s[i + 1:i + 2])
                text.append(s[i:i + 2])
                i += 2
                continue
            if c in "'\"" or s.startswith(("$'", '$"'), i):
                opener = 2 if c == "$" else 1
                if self._quote_end(i + opener, s[i:i + opener].lstrip("$") if s[i + opener - 1] == '"'
                                   else s[i:i + opener]) >= n:
                    # An unterminated quote is a literal character, so the separators after it still split: a
                    # malformed command can only over-block.
                    value.append(c)
                    text.append(c)
                    i += 1
                elif s[i + opener - 1] == "'":
                    i = self._ansi(i + 2, value, text) if opener == 2 else self._literal(i + 1, value, text)
                else:
                    i = self._dquote(i + opener, value, text, subs)
                continue
            if s.startswith("$(", i) or c == "`":
                i = self._substitution(i, value, text, subs)
                continue
            if s.startswith("${", i):
                end = self._brace_end(i + 2)
                value.append(s[i:end + 1])
                text.append(s[i:end + 1])
                i = end + 1
                continue
            value.append(c)
            text.append(c)
            i += 1
        i = min(i, n)
        return Word("".join(value), "".join(text), s[start:i], start, i), i

    def _quote_end(self, i, kind):
        """Index of the quote closing a `'`, `$'` or `"` span whose text starts at `i`; len when it never closes."""
        s, n = self.s, self.n
        if kind == '"':
            end = self._dquote_end(i)
        elif kind == "'":
            end = self._squote_end(i)
        else:
            end = i
            while end < n and s[end] != "'":
                end += 2 if s[end] == "\\" else 1
        return end if end < n and s[end] == kind[-1] else n

    def _literal(self, i, value, text):
        end = self._squote_end(i)
        value.append(self.s[i:end])
        text.append(self.s[i:end])
        return end + 1

    def _substitution(self, i, value, text, subs):
        s = self.s
        if s[i] == "`":
            end = self._backtick_end(i + 1)
            subs.append(Substitution(s[i + 1:end], i + 1, end))
        else:
            end = self._paren_end(i + 2)
            subs.append(Substitution(s[i + 2:end], i + 2, end))
        value.append(s[i:end + 1])
        text.append(s[i:end + 1])
        return end + 1

    def _dquote(self, i, value, text, subs):
        s, n = self.s, self.n
        while i < n and s[i] != '"':
            c = s[i]
            if c == "\\" and i + 1 < n:
                nxt = s[i + 1]
                if nxt in '$`"\\\n':
                    value.append("" if nxt == "\n" else nxt)
                else:
                    value.append(s[i:i + 2])
                text.append(s[i:i + 2])
                i += 2
            elif s.startswith("$(", i) or c == "`":
                i = self._substitution(i, value, text, subs)
            elif s.startswith("${", i):
                end = self._brace_end(i + 2)
                value.append(s[i:end + 1])
                text.append(s[i:end + 1])
                i = end + 1
            else:
                value.append(c)
                text.append(c)
                i += 1
        return i + 1

    def _ansi(self, i, value, text):
        s, n = self.s, self.n
        start = i
        while i < n and s[i] != "'":
            if s[i] == "\\" and i + 1 < n:
                esc = s[i + 1]
                m = re.match(r"x([0-9A-Fa-f]{1,2})|([0-7]{1,3})|u([0-9A-Fa-f]{1,4})", s[i + 1:i + 6])
                if esc in _ANSI:
                    value.append(_ANSI[esc])
                    i += 2
                elif m:
                    value.append(chr(int(m.group(1) or m.group(3), 16) if not m.group(2) else int(m.group(2), 8)))
                    i += 1 + len(m.group(0))
                else:
                    value.append(s[i:i + 2])
                    i += 2
            else:
                value.append(s[i])
                i += 1
        text.append(s[start:i])
        return i + 1

    # -- the command ------------------------------------------------------------------------------------------------
    def run(self):
        s, n = self.s, self.n
        segments, pending = [], []
        cur = self._fresh()
        i = 0
        while i < n:
            c = s[i]
            if c in _BLANKS:
                i += 1
            elif c == "\\" and s.startswith("\n", i + 1):
                i += 2
            elif c == "#":
                j = s.find("\n", i)
                i = n if j < 0 else j
            elif c == "\n":
                self._close(segments, cur, "\n")
                cur = self._fresh()
                i = self._heredoc_bodies(i + 1, pending)
            elif c in "<>" and not s.startswith("(", i + 1) or s.startswith("&>", i):
                i = self._redirect(i, i, cur, pending)
            else:
                op = next((o for o in _OPERATORS if s.startswith(o, i)), None)
                if op:
                    self._close(segments, cur, op)
                    cur = self._fresh()
                    i += len(op)
                    continue
                word, end = self.word(i, cur["subs"])
                if word.raw.isdigit() and end < n and s[end] in "<>" and not s.startswith("(", end + 1):
                    i = self._redirect(end, i, cur, pending)
                    continue
                cur["words"].append(word)
                self._span(cur, word.start, word.end)
                i = end
        self._close(segments, cur, "")
        if pending:
            self._heredoc_bodies(n, pending)
        return segments

    @staticmethod
    def _fresh():
        return {"words": [], "redirects": [], "heredocs": [], "subs": [], "start": None, "end": None}

    @staticmethod
    def _span(cur, start, end):
        cur["start"] = start if cur["start"] is None else cur["start"]
        cur["end"] = end

    @staticmethod
    def _close(segments, cur, op):
        if cur["words"] or cur["redirects"]:
            segments.append(Segment(cur["words"], cur["redirects"], cur["heredocs"], cur["subs"],
                                    cur["start"], cur["end"], op))

    def _redirect(self, i, start, cur, pending):
        s, n = self.s, self.n
        op = next(o for o in _REDIRECT_OPS if s.startswith(o, i))
        i += len(op)
        if op in ("<&", ">&") and i < n and (s[i].isdigit() or s[i] == "-"):
            while i < n and (s[i].isdigit() or s[i] == "-"):
                i += 1
            cur["redirects"].append(Redirect(op, None, start, i))
            self._span(cur, start, i)
            return i
        while i < n and s[i] in _BLANKS:
            i += 1
        target = None
        if i < n and s[i] not in _WORD_STOP:
            target, i = self.word(i, cur["subs"])
        cur["redirects"].append(Redirect(op, target, start, i))
        self._span(cur, start, i)
        if op in ("<<", "<<-") and target is not None:
            reader = next((w.value for w in reversed(cur["words"]) if not w.value.startswith("-")), "")
            pending.append((cur["heredocs"], cur["subs"], target.value, any(q in target.raw for q in "'\"\\"),
                            base(reader)))
        return i

    def _heredoc_bodies(self, i, pending):
        s, n = self.s, self.n
        while pending:
            into, subs, delimiter, quoted, reader = pending.pop(0)
            start = end = i
            while i < n:
                j = s.find("\n", i)
                line_end = n if j < 0 else j
                if s[i:line_end].strip() == delimiter:
                    end = i
                    i = n if j < 0 else j + 1
                    break
                i = n if j < 0 else j + 1
                end = i
            into.append(Heredoc(delimiter, quoted, s[start:end], start, end, reader))
            if not quoted:
                self._body_substitutions(start, end, subs)
        return i

    def _body_substitutions(self, i, end, subs):
        """An unquoted heredoc body still expands `$(...)` and backticks: each runs, whoever reads the body."""
        s = self.s
        while i < end:
            if s[i] == "\\":
                i += 2
            elif s.startswith("$(", i):
                close = min(self._paren_end(i + 2), end)
                subs.append(Substitution(s[i + 2:close], i + 2, close))
                i = close + 1
            elif s[i] == "`":
                close = min(self._backtick_end(i + 1), end)
                subs.append(Substitution(s[i + 1:close], i + 1, close))
                i = close + 1
            else:
                i += 1


def lex(command):
    """The simple commands of `command`, in source order (module docstring)."""
    return _Lexer(str(command or "")).run()


# -- bodies a segment runs ----------------------------------------------------------------------------------------
_PS_COMMAND = re.compile(r"[-/]c(?:o(?:m(?:m(?:a(?:n(?:d)?)?)?)?)?)?", re.I)
_CMD_RUN = re.compile(r"/{1,2}[ck]", re.I)
_SHELL_C = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_NOEXEC = frozenset(("bash", "sh", "zsh", "dash", "ksh"))  # `-n` reads the program and runs nothing


def bodies(segment):
    """(kind, text) of each body `segment` runs: kind 'sh', 'pwsh' or 'cmd'. Substitutions in its words and
    redirect targets, a heredoc a shell reads, and the program a launcher word takes (`bash -c`, `su root -c`,
    `eval`, `watch`, `cmd /c`, `pwsh -Command`), wherever the launcher stands in the segment."""
    out = [("sh", sub.body) for sub in segment.substitutions]
    out += [("pwsh" if hd.reader in POWERSHELLS else "sh", hd.body) for hd in segment.heredocs
            if hd.reader in _SHELL_WORDS]
    values = [w.value for w in segment.words]
    for k, value in enumerate(values):
        name = base(value)
        rest = values[k + 1:]
        if name in SHELLS:
            at = next((j for j, a in enumerate(rest) if _SHELL_C.fullmatch(a)), None)
            noexec = name in _NOEXEC and any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", a) for a in rest[:(at or 0) + 1])
            if at is not None and at + 1 < len(rest) and not noexec:
                out.append(("sh", rest[at + 1]))
        elif name == "eval" and rest:
            out.append(("sh", " ".join(rest)))
        elif name == "watch":
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                j += 2 if rest[j] in ("-n", "--interval", "-q", "--equexit") else 1
            if j < len(rest):
                out.append(("sh", " ".join(rest[j:])))
        elif name == "cmd":
            at = next((j for j, a in enumerate(rest) if _CMD_RUN.fullmatch(a)), None)
            if at is not None and at + 1 < len(rest):
                out.append(("cmd", " ".join(rest[at + 1:])))
        elif name in POWERSHELLS:
            at = next((j for j, a in enumerate(rest) if _PS_COMMAND.fullmatch(a)), None)
            if at is not None and at + 1 < len(rest):
                out.append(("pwsh", " ".join(rest[at + 1:])))
    return out


def walk(command, kind="sh", depth=0):
    """(kind, text, segments, depth) for `command` (depth 0) and, depth first, every body its segments run
    (`bodies`)."""
    segments = lex(command)
    yield kind, command, segments, depth
    if depth >= _MAX_DEPTH:
        return
    for segment in segments:
        for body_kind, body in bodies(segment):
            yield from walk(body, body_kind, depth + 1)


# -- option readers -----------------------------------------------------------------------------------------------
def options(args, short_values="", long=(), long_values=(), aliases=None, short_optional=""):
    """Options(flags, exact, values, operands) of a GNU/git-style argument list.

    `flags` holds each short letter present and each long name a typed `--prefix` can mean (every candidate when it is
    ambiguous, so a check for a dangerous option fails closed); `exact` holds the long names spelled in full, for a
    check whose option makes the call safe. A short letter in `short_values` takes the rest of its bundle or the next
    word, one in `short_optional` only the rest of its bundle; a long name in `long_values` takes `=value` or the next
    word. `aliases` maps a long name to the short letter it spells, so both land on one key. `values` maps each key
    to its values in order. Options after operands count until `--`. Every other letter is a flag."""
    aliases = aliases or {}
    flags, exact, values, operands = set(), set(), {}, []
    names = tuple(long) + tuple(long_values)
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            operands.extend(args[i:])
            break
        if a.startswith("--"):
            typed, eq, value = a[2:].partition("=")
            hits = [typed] if typed in names else [n for n in names if n.startswith(typed)] if typed else []
            if not hits and typed.startswith("no-"):
                continue
            hits = hits or [typed]
            takes = all(h in long_values for h in hits)
            if takes and not eq and i < len(args):
                value, i = args[i], i + 1
            for h in hits:
                key = aliases.get(h, h)
                flags.update((h, key))
                if typed == h:
                    exact.add(h)
                if eq or takes:
                    values.setdefault(key, []).append(value)
            continue
        if a.startswith("-") and len(a) > 1:
            for k, letter in enumerate(a[1:], 1):
                flags.add(letter)
                if letter in short_values or letter in short_optional:
                    value = a[k + 1:]
                    if not value and letter in short_values and i < len(args):
                        value, i = args[i], i + 1
                    values.setdefault(letter, []).append(value)
                    break
            continue
        operands.append(a)
    return Options(flags, exact, values, operands)


def ps_param(word, name, minimum=1):
    """True when `word` is PowerShell parameter `name` (lowercase, no dash) spelled by any prefix of at least
    `minimum` letters, `-Name:value` included."""
    if not word[:1] in ("-", "–") or len(word) < 2:
        return False
    typed = word[1:].split(":", 1)[0].lower()
    return len(typed) >= minimum and name.startswith(typed)


def cmd_switches(args, dash=False):
    """The switches of a cmd.exe argument list, lowercase: `/S`, `//S` (Git Bash), `/F/S/Q`, and `-S` when the tool
    takes `-` too (`dash`)."""
    out = set()
    for a in args:
        if a[:1] == "/" and len(a) > 1:
            out.update(p.lower() for p in a.lstrip("/").split("/") if p)
        elif dash and a[:1] == "-" and len(a) > 1 and not a.startswith("--"):
            out.add(a[1:].lower())
    return out


# -- command position -----------------------------------------------------------------------------------------------
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Reserved words that open a command position: the next word is the program the segment runs.
_RESERVED = frozenset({"!", "{", "}", "if", "then", "do", "else", "elif", "while", "until"})
# Prefix commands that execute their operand: name -> (value-taking options, positionals before it).
_WRAPPERS = {
    "command": (frozenset(), 0),
    "env": (frozenset({"-u", "-C", "--unset", "--chdir"}), 0),
    "exec": (frozenset({"-a"}), 0),
    "nice": (frozenset({"-n", "--adjustment"}), 0),
    "nohup": (frozenset(), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e"}), 0),
    "time": (frozenset({"-f", "-o"}), 0),
    "timeout": (frozenset({"-k", "-s", "--kill-after", "--signal"}), 1),
}
# Wrappers that also run their operand but change its user or session; a caller that must prove a repository's
# state passes them in `through` only when it does not need to.
OPAQUE_WRAPPERS = {
    "sudo": (frozenset({"-u", "-g", "-h", "-p", "-C", "--user", "--group", "--host", "--prompt", "--chdir"}), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "builtin": (frozenset(), 0),
    "setsid": (frozenset(), 0),
}


# xargs options that take the next word; the optional-value ones (`-i`, `-l`, `-e`) take only an attached value.
XARGS_VALUES = frozenset(("-I", "-L", "-n", "-P", "-a", "-d", "-E", "-s", "--arg-file", "--delimiter", "--max-args",
                          "--max-procs", "--max-chars", "--process-slot-var"))


def command_index(tokens, through=()):
    """Index of the program `tokens` runs after reserved words, `NAME=value` and wrapper prefixes (`nohup`,
    `timeout 600`, `env -u X FOO=1`, and the `OPAQUE_WRAPPERS` named in `through`)."""
    index = 0
    while index < len(tokens) and (tokens[index] in _RESERVED or _ENV_ASSIGNMENT.match(tokens[index])):
        index += 1
    while index < len(tokens):
        name = base(tokens[index])
        spec = _WRAPPERS.get(name) or (OPAQUE_WRAPPERS.get(name) if name in through else None)
        if spec is None:
            return index
        value_options, positionals = spec
        index += 1
        while index < len(tokens) and tokens[index].startswith("-") and tokens[index] != "-":
            index += 2 if tokens[index] in value_options else 1
        while name == "env" and index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
            index += 1
        index += positionals
    return index


def executable_index(segment):
    """(tokens, index of the program they run); `segment` is a token list."""
    return segment, command_index(segment)


def _shell_operand(tokens, index):
    index += 1
    command_string = False
    while index < len(tokens) and tokens[index][:1] in ("-", "+"):
        option = tokens[index]
        index += 1
        if option == "--":
            break
        if option.startswith("--"):
            if option in ("--rcfile", "--init-file"):
                index += 1
            continue
        letters = option[1:]
        if option[0] == "-" and "n" in letters:
            return len(tokens), False
        command_string |= option[0] == "-" and "c" in letters
        index += letters.count("o") + letters.count("O")
    return index, command_string


LauncherBody = namedtuple("LauncherBody", "body start end scoped")


def literal_launcher_body(tokens, index):
    """Literal eval/bash/sh command body and its argv slice. A shell scopes cwd/env; eval does not.
    `None` means no executable body, including `bash -n`. Callers own body recursion and state."""
    if index >= len(tokens):
        return None
    head = base(tokens[index])
    if head == "eval":
        return LauncherBody(" ".join(tokens[index + 1:]), index + 1, len(tokens), False)
    if head not in ("bash", "sh"):
        return None
    operand, command_string = _shell_operand(tokens, index)
    if command_string and operand < len(tokens):
        return LauncherBody(tokens[operand], operand, operand + 1, True)
    return None


# -- views other modules read ---------------------------------------------------------------------------------------
def executable_text(command):
    """`command` without the heredoc bodies the shell hands to a program as stdin: a body is data, and its free text
    must not reach a content matcher."""
    command = str(command or "")
    out, cursor = [], 0
    for start, end in sorted((hd.start, hd.end) for seg in lex(command) for hd in seg.heredocs):
        out.append(command[cursor:max(start, cursor)])
        cursor = max(end, cursor)
    out.append(command[cursor:])
    return "".join(out)


def executable_spans(command):
    """(start, end, text) of each top-level segment; offsets name the original command."""
    command = str(command or "")
    for seg in lex(command):
        yield seg.start, seg.end, command[seg.start:seg.end]


ShellWord = namedtuple("ShellWord", "value start end")


def command_words(segment):
    """([ShellWord], insertion offset) for a simple literal command, else None. New arguments go before the first
    redirect or comment. Substitutions and heredocs need shell evaluation, so a rewriter leaves those forms alone."""
    segments = lex(segment)
    if len(segments) != 1:
        return None if segments else ([], len(segment.rstrip()))
    seg = segments[0]
    if seg.substitutions or seg.heredocs or re.search(r"\$\(|`|<<", re.sub(r"'[^']*'", "", segment)):
        return None
    out = [ShellWord(w.value, w.start, w.end) for w in seg.words]
    insert = min([r.start for r in seg.redirects] + [len(segment.rstrip())])
    comment = re.search(r"(?:^|(?<=\s))#", segment[seg.end:])
    if comment and not seg.redirects:
        insert = seg.end + comment.start()
    return out, insert


def executable_word(words, segment):
    """The first word after syntactic assignment prefixes, never an assignment's quoted value."""
    return next((i for i, word in enumerate(words)
                 if not _ENV_ASSIGNMENT.match(segment[word.start:word.end])), len(words))


def shell_code(command):
    """`command` with each heredoc body, and each quoted `-c`/`-e`/`-p` program, blanked unless a shell reads it:
    a program another interpreter runs is data to a shell matcher. Offsets of the rest are kept."""
    command = str(command or "")
    blanks = []
    for seg in lex(command):
        blanks += [(hd.start, hd.end) for hd in seg.heredocs if hd.reader not in _SHELL_WORDS]
        ws = seg.words
        for k in range(1, len(ws) - 1):
            if ws[k].value in ("-c", "-e", "-p") and ws[k + 1].raw[:1] in ("'", '"'):
                prog = next((w.value for w in reversed(ws[:k]) if not w.value.startswith("-")), "")
                if base(prog) not in _SHELL_WORDS:
                    blanks.append((ws[k + 1].start + 1, ws[k + 1].end - 1))
    out, cursor = [], 0
    for start, end in sorted(blanks):
        if start < cursor or end < start:
            continue
        out.append(command[cursor:start])
        out.append(re.sub(r"[^\n]", " ", command[start:end]))
        cursor = end
    out.append(command[cursor:])
    return "".join(out)


Invocation = namedtuple("Invocation", "kind script path args")
_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?|py")


def launcher_invocations(command):
    """[Invocation(kind, script, path, args)] for each executed segment that runs the seat launcher (`seat.sh`, paid
    external work), in the command and every body it runs (`walk`); reserved words, `NAME=value` and wrapper prefixes
    are stepped over, and a python or bash script operand is read past its interpreter's options. `bash -n` runs
    nothing."""
    calls = []
    for _kind, _text, segments, _depth in walk(command):
        for seg in segments:
            segment = [w.value for w in seg.words]
            index = command_index(segment)
            if index >= len(segment):
                continue
            executable = base(segment[index])
            script_index = index
            if _PYTHON.fullmatch(executable):
                script_index += 1
                while script_index < len(segment) and segment[script_index].startswith("-"):
                    if segment[script_index] in ("-c", "-m"):
                        script_index = len(segment)
                        break
                    script_index += 1
            elif executable in ("bash", "sh"):
                script_index, command_string = _shell_operand(segment, index)
                if command_string:
                    continue
            if script_index >= len(segment):
                continue
            path = segment[script_index]
            if base(path) == "seat.sh":
                calls.append(Invocation("seat", "seat.sh", path, segment[script_index + 1:]))
    return calls
