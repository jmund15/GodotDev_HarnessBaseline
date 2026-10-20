#!/usr/bin/env python3
"""Hook (PreToolUse, Bash/PowerShell, via pre_bash_dispatch.py): advisory, process class.

Fires when an inline script (`python -c`, `python -` heredoc, `node -e`) reads a run's token or cost fields
(`modelUsage`, `costUSD`, `total_cost_usd`, cache-read/cache-write token counts), unless the command uses
run_cost. The advisory names `tools/run_cost.py`, the one command that prices runs from the registry's list
rates, and its paired `--a/--b` mode, the only valid way to compare two models' cost. A command that only
names those fields (a grep, a cat) is not a pricing action and gets nothing.

The rule it backs lives in `tools/run_cost.py`'s docstring. Never denies; a crash is reported by the
dispatcher and the call proceeds.
"""
import json
import re
import shlex
import sys

from _command_text import _HEREDOC_START, executable_spans, executable_text
from _hook_state import fire_once_since_compaction

PYTHON = re.compile(r"^(?:python\d*(?:\.\d+)*|py)(?:\.exe)?$")
COST_FIELD = re.compile(r"modelUsage|costUSD|total_cost_usd|cache_?read_?input_?tokens|cache_?creation_?input_?tokens"
                        r"|cacheReadTokens|cacheWriteTokens", re.I)
ADVISORY = ("[pricing] This inline script reads run token or cost fields. Price runs with "
            "`python3 .claude/tools/run_cost.py <runs>...` (registry list rates, per-part USD, each result counted "
            "once) and compare two models with `run_cost.py --a <runs>... --b <runs>...` over the same cells.")


def _reader(segment):
    try:
        words = shlex.split(segment, comments=True)
    except ValueError:
        return None, False
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        words.pop(0)
    if not words:
        return None, False
    exe = words.pop(0).replace("\\", "/").rsplit("/", 1)[-1].lower()
    python = bool(PYTHON.fullmatch(exe))
    if not python and exe not in ("node", "node.exe"):
        return None, False
    for index, word in enumerate(words):
        if word in (("-c",) if python else ("-e", "--eval")):
            return (words[index + 1] if index + 1 < len(words) else ""), False
        if word == "-":
            return None, True
        if word.startswith(("<", ">")):
            break
        if not word.startswith("-") or word == "-m":
            return None, False
    return None, python


def inline_scripts(command):
    scripts = []
    for _, _, segment in executable_spans(executable_text(command)):
        script, _ = _reader(segment)
        if script is not None:
            scripts.append(script)
    lines = command.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        opening = _HEREDOC_START.search(line)
        index += 1
        if not opening:
            continue
        body = []
        while index < len(lines) and lines[index].strip() != opening.group(2):
            body.append(lines[index])
            index += 1
        index += 1
        spans = list(executable_spans(line[:opening.start()]))
        if spans and _reader(spans[-1][2])[1]:
            scripts.append("\n".join(body))
    return scripts


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not any("run_cost" not in script and COST_FIELD.search(script) for script in inline_scripts(cmd)):
        return
    if not fire_once_since_compaction(data.get('session_id') or '', 'hand_pricing_advisory'):
        return
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                        "additionalContext": ADVISORY}}))


if __name__ == "__main__":
    main()
