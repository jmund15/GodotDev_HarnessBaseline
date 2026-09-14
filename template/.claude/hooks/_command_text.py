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


def strip_heredocs(command: str) -> str:
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
            i += 1
        if i < len(lines):
            out.append(lines[i])   # keep the terminator so line structure survives
            i += 1
    return "\n".join(out)


def executable_text(command: str) -> str:
    """The command text a content matcher should judge: heredoc bodies removed."""
    return strip_heredocs(command)
