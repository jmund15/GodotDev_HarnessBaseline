#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash|PowerShell — deny a shell command that writes a governed harness file.

Rule home: `CLAUDE.core.md` §9 Writing (`.claude/` harness files use direct Edit/Write). The Write|Edit
route carries the `instruction_quality` load gate and each file's edit notes
(`harness_edit_skill_reminder.py`); a shell write skips both, so it is denied before it runs and the deny
names the Edit/Write route.

Write forms matched, each on a path `harness_edit_skill_reminder.is_harness_file` governs:
- shell: `>`/`>>` redirects, `tee`, `sed -i`/`perl -i`, `dd of=`, and `cp`/`mv`/`install` whose
  destination is governed and whose source is not (a move or copy between governed files authors nothing);
- an interpreter's inline code (heredoc body, `-c`/`-e`/`-Command`): `open(<path>, 'w'|'a'|'x')`,
  `.write_text`/`.write_bytes` on a path literal or a name bound to one, `shutil` copy/move and
  `os.replace`/`os.rename` destinations, Node `writeFile*`, PowerShell `Set-Content`/`Add-Content`/
  `Out-File` and `[IO.File]::Write*`;
- the PowerShell tool: the same cmdlets, `Copy-Item`/`Move-Item -Destination`, and redirects.
Reads, mentions in quoted data, git operations and project tools that write their own state pass.
Coverage limit: a path computed at run time (joined from parts, read from a file) is not decidable from the
command text; the commit advisory in `git_guardrails.py` stays the backstop for it.

Class: process guard, blocking from its first commit at the owner's request (the commit quotes it).
Fail posture: a fault here reaches `pre_bash_dispatch`, which reports the lost check and lets the call run.

Wired in: pre_bash_dispatch on Bash|PowerShell|Monitor; `main()` also supports standalone use.
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _command_text import executable_spans  # noqa: E402
from harness_edit_skill_reminder import is_harness_file  # noqa: E402

_PATH_CHARS = r"[^\s'\"`;&|<>()]"
_REDIRECT = re.compile(r"(?<![<>&\w])(?:\d|&)?>>?(?!&)\s*(['\"]?)(" + _PATH_CHARS + r"+)\1")
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
_PS_WRITE = re.compile(
    r"\b(?:Set-Content|Add-Content|Out-File|Copy-Item|Move-Item|New-Item)\b[^\n;|]*?"
    r"(?:-(?:LiteralPath|FilePath|Path|Destination)\s+)?(['\"]?)(?P<p>[^\s'\";|]*\.claude[/\\][^\s'\";|]+)\1",
    re.IGNORECASE)
_PS_READS_ONLY = re.compile(r"^\s*(?:Copy-Item|Move-Item)\b", re.IGNORECASE)
# A name bound to a path literal: the whole right-hand side is the literal, bare or in Path(...).
_BINDING = re.compile(r"^[ \t]*(\w+)\s*=\s*(?:Path\(\s*)?r?" + _Q + r"(?P<p>[^'\"`\n]+)\2\s*\)?[ \t]*(?:;|$)",
                      re.MULTILINE)
_DEF = re.compile(r"^[ \t]*def\s+(\w+)\(([^)]*)\)", re.MULTILINE)


def _governed(path, cwd):
    path = path.strip().strip("'\"")
    if not path or ("$" in path and ".claude" not in path):
        return None
    if ".claude" not in path.replace("\\", "/").split("/") and not os.path.isabs(path) and cwd:
        path = cwd.rstrip("/\\") + "/" + path
    return path if is_harness_file(path) else None


def _words(segment):
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _verb(words):
    i = 0
    while i < len(words) and (re.match(r"^[A-Za-z_]\w*=", words[i]) or os.path.basename(words[i]) in _WRAPPERS):
        i += 1
    return (os.path.basename(words[i]).lower().removesuffix(".exe"), words[i + 1:]) if i < len(words) else ("", [])


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
    targets += [m.group("p") for m in _PS_WRITE.finditer(body)]
    return targets


def _redirect_targets(segment):
    """Redirect targets outside quoted data; quotes are masked to equal length, so offsets map back."""
    masked = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", lambda m: m[0][0] + "x" * (len(m[0]) - 2) + m[0][-1], segment)
    targets = []
    for m in _REDIRECT.finditer(masked):
        start = m.start(1) if m.group(1) else m.start(2)
        quote = m.group(1)
        original = segment[start:]
        if quote:
            original = original[1:].split(quote, 1)[0]
        else:
            original = re.match(_PATH_CHARS + r"+", original)[0]
        targets.append(original)
    return targets


def _segment_targets(segment, command):
    verb, args = _verb(_words(segment))
    targets = [] if verb == "git" else _redirect_targets(segment)
    operands = [a for a in args if not a.startswith("-")]
    if verb == "tee":
        targets += operands
    elif verb in ("sed", "perl") and any(a.startswith("-i") or a == "--in-place" or a.startswith("-pi") for a in args):
        targets += operands
    elif verb == "dd":
        targets += [a[3:] for a in args if a.startswith("of=")]
    elif verb in _COPIERS and len(operands) >= 2:
        *sources, dest = operands
        if dest.endswith(("/", "\\")):
            targets += [("src", s, dest + os.path.basename(s)) for s in sources]
        else:
            targets += [("src", s, dest) for s in sources]
    elif verb in _INTERPRETERS and (set(args) & _INLINE_FLAGS or "<<" in segment):
        inline = [args[i + 1] for i, a in enumerate(args[:-1]) if a in _INLINE_FLAGS]
        targets += _body_targets("\n".join(inline) + "\n" + command)
    return targets


def written_harness_paths(command, tool, cwd=""):
    """The governed harness paths this command writes, by the forms the module docstring lists."""
    found = []
    if tool == "PowerShell":
        for line in re.split(r"[;\n]", command):
            if _PS_READS_ONLY.match(line) and not re.search(r"-Destination", line, re.IGNORECASE):
                continue
            found += [m.group("p") for m in _PS_WRITE.finditer(line)]
            found += [m.group(2) for m in _REDIRECT.finditer(line)]
        return sorted({g for g in (_governed(p, cwd) for p in found) if g})
    governed = set()
    for _start, _end, segment in executable_spans(command):
        verb, args = _verb(_words(segment))
        if verb == "cd" and args:
            cwd = _cd(cwd, args[-1])
            continue
        for target in _segment_targets(segment, command):
            if isinstance(target, tuple):
                _, source, dest = target
                if _governed(dest, cwd) and not _governed(source, cwd):
                    governed.add(_governed(dest, cwd))
            elif _governed(target, cwd):
                governed.add(_governed(target, cwd))
    return sorted(governed)


def _cd(cwd, target):
    target = re.sub(r"^/([a-zA-Z])/", lambda m: m.group(1).upper() + ":/", target.strip("'\""))
    if os.path.isabs(target) or re.match(r"^[A-Za-z]:[/\\]", target):
        return target
    return (cwd.rstrip("/\\") + "/" + target) if cwd else target


def deny_reason(paths):
    shown = ", ".join("`%s`" % p for p in paths)
    return ("Shell write to harness file %s DENIED: harness files are authored with the Edit or Write tool "
            "(CLAUDE.core.md §9 Writing), where the instruction_quality gate and the file's edit notes run. "
            "Make this change with Edit or Write." % shown)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool = payload.get("tool_name")
    if tool not in ("Bash", "PowerShell"):
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command") or ""
    paths = written_harness_paths(command, tool, payload.get("cwd") or "")
    if paths:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": deny_reason(paths),
        }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
