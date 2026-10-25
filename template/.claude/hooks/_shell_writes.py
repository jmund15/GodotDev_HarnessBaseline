#!/usr/bin/env python3
"""The paths a shell command writes, read from its text: one extractor for the guards that judge a write by where it lands.

`written_targets(command, tool, cwd)` returns `Write(path, source, base, verb)` rows. `path` is the operand as typed,
`base` the directory the command runs in after any earlier `cd`, so the caller resolves `path` against `base` and applies
its own predicate (a governed harness file, the main checkout). `source` is the copied file of a `cp`/`mv`/`install`;
`verb` is the command word, so a caller can pass a form its policy allows (`git show ... > file`).

Forms read: `>`/`>>` redirects, `tee`, `sed -i`/`perl -i`, `dd of=`, `cp`/`mv`/`install` (destination, `-t`,
`--target-directory`), an interpreter's inline code (heredoc body, `-c`/`-e`/`-Command`: `open(<path>, 'w')`,
`.write_text`, `shutil`/`os` copy and move, Node `writeFile*`, `[IO.File]::Write*`, the PowerShell cmdlets), and, for the
PowerShell tool, the same cmdlets and redirects. A path computed at run time is not decidable from the text.

`segment_states(command, cwd)` yields `(base, segment, words)` per simple command with `cd` tracked, for a caller that
reads a form this module does not own (a `git -C` writer).
"""
import os
import posixpath
import re
import shlex
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _command_text import _REDIRECTION, _WORD, executable_spans  # noqa: E402
from _git_commit import resolve_cd  # noqa: E402

Write = namedtuple("Write", "path source base verb")

_UNRESOLVED = re.compile(r"[$`*?\[\]{}()~]")
_QUOTED = re.compile(r"""'[^']*'|"(?:[^"\\]|\\.)*\"""")
_INTERPRETERS = {"python", "python3", "py", "node", "perl", "ruby", "pwsh", "powershell", "bash", "sh"}
_INLINE_FLAGS = {"-c", "-e", "-Command", "-command", "-"}
_COPIERS = {"cp", "mv", "install"}
_WRAPPERS = {"env", "command", "sudo", "nice", "time", "exec"}
_Q = r"(['\"`])"
_LIT = r"(?P<p>[^'\"`\n]+)"
_BODY_WRITES = [
    re.compile(r"open\(\s*" + _Q + _LIT + r"\1\s*,\s*(?:mode\s*=\s*)?['\"][^'\"]*[wax+]"),
    re.compile(r"Path\(\s*" + _Q + _LIT + r"\1\s*\)\s*\.write_(?:text|bytes)\("),
    re.compile(r"shutil\.(?:copy\w*|move)\([^,\n]+,\s*" + _Q + _LIT + r"\1"),
    re.compile(r"os\.(?:replace|rename)\([^,\n]+,\s*" + _Q + _LIT + r"\1"),
    re.compile(r"(?:writeFileSync|appendFileSync|writeFile)\(\s*" + _Q + _LIT + r"\1"),
    re.compile(r"\[(?:System\.)?IO\.File\]::(?:Write|Append)\w*\(\s*" + _Q + _LIT + r"\1"),
]
# A name bound to a path literal: the whole right-hand side is the literal, bare or in Path(...).
_BINDING = re.compile(r"^[ \t]*(\w+)\s*=\s*(?:Path\(\s*)?r?" + _Q + r"(?P<p>[^'\"`\n]+)\2\s*\)?[ \t]*(?:;|$)",
                      re.MULTILINE)
_DEF = re.compile(r"^[ \t]*def\s+(\w+)\(([^)]*)\)", re.MULTILINE)
_PS_TOKEN = re.compile(r"""'[^']*'|"[^"]*"|[^\s'"]+""")
_PS_CMDLETS = {"set-content", "add-content", "out-file", "new-item", "copy-item", "move-item"}
_PS_DESTINATION_ONLY = {"copy-item", "move-item"}
_PS_PATH_PARAMS = ("-path", "-literalpath", "-filepath", "-pspath")
_PS_SWITCHES = {"-append", "-force", "-nonewline", "-noclobber", "-whatif", "-confirm", "-passthru", "-recurse",
                "-asbytestream", "-usingtransaction"}


def _after_redirect(segment, m):
    i = m.end()
    while i < len(segment) and segment[i].isspace():
        i += 1
    return _WORD.match(segment, i), i


def _redirect_targets(segment):
    """Targets of the output redirects outside quoted data."""
    masked = _QUOTED.sub(lambda m: "x" * len(m[0]), segment)
    for m in _REDIRECTION.finditer(masked):
        if "<" in m[0] or "&" in m[0].lstrip("&0123456789"):
            continue
        w, _ = _after_redirect(segment, m)
        if w:
            try:
                yield shlex.split(w[0], posix=True)[0]
            except (ValueError, IndexError):
                pass


def _args(segment):
    """The segment's words with redirects and leading `NAME=value` assignments removed."""
    masked = _QUOTED.sub(lambda m: "x" * len(m[0]), segment)
    for m in reversed(list(_REDIRECTION.finditer(masked))):
        w, i = _after_redirect(segment, m)
        segment = segment[:m.start()] + " " + segment[w.end() if w else i:]
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        words = segment.split()
    while words and re.match(r"[A-Za-z_]\w*=", words[0]):
        words.pop(0)
    return words


def _verb(words):
    i = 0
    while i < len(words) and (re.match(r"^[A-Za-z_]\w*=", words[i]) or posixpath.basename(words[i]) in _WRAPPERS):
        i += 1
    return (posixpath.basename(words[i]).lower().removesuffix(".exe"), words[i + 1:]) if i < len(words) else ("", [])


def _ps_targets(text):
    found = []
    for line in re.split(r"[;\n|]", text):
        tokens = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "'\"" else t for t in _PS_TOKEN.findall(line)]
        start = next((i for i, t in enumerate(tokens) if t.lower().lstrip("{(&") in _PS_CMDLETS), None)
        if start is None:
            continue
        cmdlet = tokens[start].lower().lstrip("{(&")
        named, positional, i = {}, [], start + 1
        while i < len(tokens):
            token, low = tokens[i], tokens[i].lower()
            if token.startswith("-") and len(token) > 1 and not token[1].isdigit():
                if low not in _PS_SWITCHES and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    named[low] = tokens[i + 1]
                    i += 1
            else:
                positional.append(token)
            i += 1
        if cmdlet in _PS_DESTINATION_ONLY:
            target = named.get("-destination")
        else:
            target = next((named[p] for p in _PS_PATH_PARAMS if p in named), positional[0] if positional else None)
        if target:
            found.append(target)
    return found


def _body_targets(body):
    targets = [m.group("p") for rx in _BODY_WRITES for m in rx.finditer(body)]
    bindings = [(m.start(), m.group(1), m.group("p")) for m in _BINDING.finditer(body)]
    for name in {b[1] for b in bindings}:
        use = re.compile(r"\b" + re.escape(name) + r"\.write_(?:text|bytes)\(|open\(\s*" + re.escape(name)
                         + r"\s*,\s*(?:mode\s*=\s*)?['\"][^'\"]*[wax+]")
        for write in use.finditer(body):
            bound = [b for b in bindings if b[1] == name and b[0] < write.start()]
            if bound:
                targets.append(bound[-1][2])
    for m in _DEF.finditer(body):
        func, params = m.group(1), [p.split("=")[0].strip() for p in m.group(2).split(",")]
        for index, name in enumerate(params):
            use = re.compile(r"\b" + re.escape(name) + r"\.write_(?:text|bytes)\(|open\(\s*" + re.escape(name)
                             + r"\s*,\s*(?:mode\s*=\s*)?['\"][^'\"]*[wax+]")
            if not name or not use.search(body, m.end()):
                continue
            for call in re.finditer(r"(?<![\w.])" + re.escape(func) + r"\(", body[m.end():]):
                args = body[m.end() + call.end():].split(")", 1)[0].split(",")
                if index < len(args):
                    literal = re.match(r"\s*r?" + _Q + r"([^'\"`\n]+)\1\s*$", args[index])
                    if literal:
                        targets.append(literal.group(2))
    return targets + _ps_targets(body)


def _inplace(verb, args):
    short = [a for a in args if a.startswith("-") and not a.startswith("--")]
    if verb == "perl":
        return any(re.match(r"-[pnlaw0-9]*i", a) for a in short)
    return "--in-place" in args or any(re.match(r"-[A-Za-z]*i", a) for a in short)


def _copy_writes(args, ops):
    for i, a in enumerate(args):
        directory = args[i + 1] if a in ("-t", "--target-directory") and i + 1 < len(args) else None
        if a.startswith("--target-directory="):
            directory = a.split("=", 1)[1]
        if directory is not None:
            sources = [o for o in ops if o != directory]
            return [(directory.rstrip("/\\") + "/" + posixpath.basename(s), s) for s in sources] or [(directory, None)]
    if len(ops) < 2:
        return []
    *sources, dest = ops
    if dest.endswith(("/", "\\")):
        return [(dest + posixpath.basename(s), s) for s in sources]
    return [(dest, s) for s in sources]


def _segment_writes(verb, args, command):
    ops = [a for a in args if not a.startswith("-") or a == "-"]
    if verb == "tee":
        return [(p, None) for p in ops]
    if verb in ("sed", "gsed", "perl") and _inplace("perl" if verb == "perl" else "sed", args):
        scripted = any(a in ("-e", "-f") for a in args) or (
            verb == "perl" and any("e" in a for a in args if a.startswith("-") and not a.startswith("--")))
        return [(p, None) for p in (ops if scripted else ops[1:])]
    if verb == "dd":
        return [(a[3:], None) for a in args if a.startswith("of=")]
    if verb in _COPIERS:
        return _copy_writes(args, ops)
    if verb in _INTERPRETERS and (set(args) & _INLINE_FLAGS or "<<" in command):
        inline = [args[i + 1] for i, a in enumerate(args[:-1]) if a in _INLINE_FLAGS]
        return [(p, None) for p in _body_targets("\n".join(inline) + "\n" + command)]
    return []


def segment_states(command, cwd=""):
    """(base, segment, words) for each simple command outside quoted data; `cd <path>` moves `base` and yields nothing."""
    base = cwd
    for _start, _end, segment in executable_spans(command):
        words = _args(segment)
        verb, args = _verb(words)
        if verb == "cd" and args:
            if not _UNRESOLVED.search(args[-1]):
                base = resolve_cd(base, args[-1])
            continue
        yield base, segment, words


def written_targets(command, tool="Bash", cwd=""):
    """The `Write` rows for `command`, in the forms the module docstring lists."""
    if tool == "PowerShell":
        return [Write(p, None, cwd, "") for line in re.split(r"[;\n]", command)
                for p in _ps_targets(line) + list(_redirect_targets(line))]
    found = []
    for base, segment, words in segment_states(command, cwd):
        verb, args = _verb(words)
        found += [Write(p, None, base, verb) for p in _redirect_targets(segment)]
        found += [Write(p, source, base, verb) for p, source in _segment_writes(verb, args, command)]
    return found
