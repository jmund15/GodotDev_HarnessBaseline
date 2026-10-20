#!/usr/bin/env python3
"""Scrub a shell command string down to the part that will actually EXECUTE.

A PreToolUse hook receives one string holding both the command and any data it carries. A
matcher run over the whole string denies on text the shell will never run: a heredoc body
written to a file, a quoted argument, a comment. Measured 2026-09-03 -- writing a script that
merely *contained* a gate invocation was denied by the gate-cadence guard, and a script whose
text described a fan-out was denied by the dispatch guard; six denials, no executing violation
among them.

Use `executable_text()` before matching. Do NOT use it for allow-decisions that must inspect
everything the command could run -- a PreToolUse `allow` approves the entire string, so an
allow-path matcher wants the raw text (`instruction_quality` §13).
"""
import re
import shlex
from dataclasses import dataclass

# `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"` -- capture the delimiter, honouring the `-` variant
# (which permits a tab-indented terminator).
#
# The trailing `\s*$` is load-bearing, not tidiness: a real heredoc opener ENDS its line, so
# requiring that rejects a merely-quoted mention (`echo '<<EOF'`). Without it the stripper would
# swallow every following line up to a matching terminator and hide real commands from every
# matcher -- a false ALLOW, which is strictly worse than the false block this file exists to fix.
_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$")

# `<<<` is a here-STRING: one word, no body, nothing to strip. Must not be read as `<<`.
_HERESTRING = re.compile(r"<<<")


def strip_heredocs(command: str, *, preserve_offsets=False) -> str:
    """Remove heredoc BODIES, keeping the command lines around them.

    The body is data the shell hands to another program; it is not executed by this command.
    An unterminated heredoc drops everything to end-of-string, which is the safe reading -- the
    body is still not executing.
    """
    if "<<" not in command:
        return command
    out, lines = [], command.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = None if _HERESTRING.search(line) else _HEREDOC_START.search(line)
        if not m:
            i += 1
            continue
        delim = m.group(2)
        i += 1
        while i < len(lines) and lines[i].strip() != delim:
            if preserve_offsets:
                out.append(' ' * len(lines[i]))
            i += 1
        if i < len(lines):
            out.append(' ' * len(lines[i]) if preserve_offsets else lines[i])
            i += 1
    return "\n".join(out)


def executable_text(command: str) -> str:
    """The command text a content matcher should judge: heredoc bodies removed."""
    return strip_heredocs(command)


@dataclass(frozen=True)
class ShellWord:
    value: str
    start: int
    end: int


_QUOTE_OR_ESCAPE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"|\\.", re.DOTALL)
_SEPARATOR = re.compile(r"\|\||&&|[;|\n]|(?<![<>])&(?!>)")
_WORD = re.compile(r"(?:[^\s'\"\\<>;&|]+|'[^']*'|\"(?:[^\"\\]|\\.)*\"|\\.)+")
_REDIRECTION = re.compile(r"(?:\d*|&)(?:>>?|<)(?:&(?:\d+-?|-))?")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def comment_offset(command):
    masked = _QUOTE_OR_ESCAPE.sub(lambda m: 'x' * len(m[0]), command)
    match = re.search(r'(?<!\S)#[^\n]*', masked)
    return match.start() if match else len(command)


def _mask_shell_data(command):
    masked = _QUOTE_OR_ESCAPE.sub(lambda m: 'x' * len(m[0]), command)
    return re.sub(r"(?<!\S)#[^\n]*", lambda m: ' ' * len(m[0]), masked)


def executable_spans(command):
    """Source spans outside quoted data, comments and heredoc bodies; offsets name the original."""
    text = strip_heredocs(command, preserve_offsets=True)
    masked = _mask_shell_data(text)
    start = 0
    for match in _SEPARATOR.finditer(masked):
        end = match.start()
        left = start + len(text[start:end]) - len(text[start:end].lstrip())
        right = end - (len(text[start:end]) - len(text[start:end].rstrip()))
        if left < right and masked[left:right].strip():
            yield left, right, command[left:right]
        start = match.end()
    left = start + len(text[start:]) - len(text[start:].lstrip())
    right = len(text.rstrip())
    if left < right and masked[left:right].strip():
        yield left, right, command[left:right]


def command_words(segment):
    """(argv source words, insertion offset) for a simple literal command, else None.

    Insert new arguments before redirects/comments. Substitutions and heredocs need shell
    evaluation, so rewriters leave those forms alone rather than guess their source boundary.
    """
    dynamic = re.sub(r"'[^']*'", lambda m: ' ' * len(m[0]), segment)
    if '$(' in dynamic or '`' in dynamic or '<<' in dynamic:
        return None
    words, i, insert = [], 0, None
    while i < len(segment):
        if segment[i].isspace():
            i += 1
            continue
        if segment[i] == '#':
            insert = i if insert is None else insert
            break
        redirect = _REDIRECTION.match(segment, i)
        if redirect:
            insert = i if insert is None else insert
            i = redirect.end()
            if '&' not in redirect[0].lstrip('0123456789') or redirect[0].startswith('&'):
                while i < len(segment) and segment[i].isspace():
                    i += 1
                target = _WORD.match(segment, i)
                if target is None:
                    return None
                i = target.end()
            continue
        match = _WORD.match(segment, i)
        if match is None:
            return None
        try:
            value, = shlex.split(match[0], posix=True)
        except ValueError:
            return None
        words.append(ShellWord(value, i, match.end()))
        i = match.end()
    return words, insert if insert is not None else len(segment.rstrip())


def executable_word(words, segment):
    """The first word after syntactic assignment prefixes, never an assignment's quoted value."""
    return next((i for i, word in enumerate(words)
                 if not _ASSIGNMENT.match(segment[word.start:word.end])), len(words))
