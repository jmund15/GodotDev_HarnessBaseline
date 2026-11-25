#!/usr/bin/env python3
"""The executable-text readers v1 hooks import by this name, served by the one shell lexer (`_shell_lex.py`).

A PreToolUse hook receives one string holding both the command and any data it carries; a matcher run over the
whole string denies on text the shell never runs (a heredoc body, a quoted argument, a comment). `_shell_lex` reads
segments, words and heredocs once for every hook; this module keeps the v1 import names so a caller migrates to
`_shell_lex` when it is next edited, and holds no parser of its own.

    executable_text(command)          -> command without heredoc bodies (match content against this)
    executable_spans(command)         -> (start, end, text) per top-level segment
    command_words(segment)            -> ([ShellWord], insertion offset) for a simple literal command, else None
    executable_word(words, segment)   -> index of the first word after `NAME=value` prefixes
    runs_script(command, script)      -> True when a segment executes `script`

Use `executable_text()` before matching. Do NOT use it for allow-decisions that must inspect everything the command
could run -- a PreToolUse `allow` approves the entire string, so an allow-path matcher wants the raw text
(`instruction_quality` §13).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shell_lex import ShellWord, command_words, executable_spans, executable_text, executable_word  # noqa: E402,F401

# A heredoc opener ends its line (`<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`); `hand_pricing_advisory.py` reads it.
_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$")

_PYTHON = re.compile(r"^(?:python[\d.]*|py)$")
_PREFIXES = frozenset(("nohup", "setsid", "exec"))
_PYTHON_VALUE_OPTIONS = frozenset({"-X", "-W", "-Q"})


def _base(word):
    return word.replace("\\", "/").rsplit("/", 1)[-1]


def runs_script(command, script, *, flag=None):
    """True when a segment of `command` executes `script` (a basename), directly or as python's script, with `flag`
    among its words when given. A mention (a path argument, quoted data, a heredoc body) is not a run."""
    for _left, _right, segment in executable_spans(command):
        parsed = command_words(segment)
        if not parsed:
            continue
        words = [word.value for word in parsed[0][executable_word(parsed[0], segment):]]
        while words and words[0] in _PREFIXES:
            words = words[1:]
        if not words or (flag and flag not in words):
            continue
        program = _base(words[0]).lower().removesuffix(".exe")
        target = _python_script(words[1:]) if _PYTHON.match(program) else words[0]
        if target and _base(target) == script:
            return True
    return False


def _python_script(args):
    """The script python runs from its arguments: the first word past its options. None for `-c`/`-m`, which run
    no script file."""
    skip = False
    for word in args:
        if skip:
            skip = False
        elif word in ("-c", "-m"):
            return None
        elif word in _PYTHON_VALUE_OPTIONS:
            skip = True
        elif not word.startswith("-") or word == "-":
            return word
    return None
