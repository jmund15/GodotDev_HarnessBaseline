#!/usr/bin/env python3
"""
Hook: PreToolUse - Enforce xvfb-run for dotnet test on cloud

GdUnit4 communicates with Godot via .NET Named Pipes (Unix domain sockets
on Linux at /tmp/CoreFxPipe_gdunit4-message-pipe). Godot needs an X display
to initialize, even for headless test runs. Without proper X auth, Godot
fails silently and the named pipe server never starts — causing a 10+ minute
timeout in the test adapter.

xvfb-run handles display + auth setup correctly (creates temp Xauthority,
starts Xvfb with -auth, sets both DISPLAY and XAUTHORITY). A background
Xvfb daemon without XAUTHORITY management causes auth failures.

This hook rewrites executing bare `dotnet test` segments on cloud to put
`xvfb-run --auto-servernum` directly before `dotnet test`, adding `--settings .runsettings`
when that invocation has none. A command wrapper before it (`time`, `{`, `exec`, `nohup`, `env`,
with any assignments after them) does not hide the test. Quoted mentions, non-shell heredoc
bodies and `dotnet test` named as another command's argument stay unchanged.
"""

import json
import os
import sys

from _claude_scope import is_cloud

_SHELL_PREFIXES = frozenset({'time', '{', 'exec', 'nohup', 'env'})


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only intercept Bash commands
    if tool_name != "Bash":
        print("{}")
        sys.exit(0)

    # Only enforce on cloud
    if not is_cloud():
        print("{}")
        sys.exit(0)

    command = tool_input.get("command", "")

    from _command_text import executable_spans, command_words, executable_word
    replacements = []
    for at, end, segment in executable_spans(command):
        parsed = command_words(segment)
        if parsed is None:
            continue
        source_words, _ = parsed
        head = executable_word(source_words, segment)
        while head < len(source_words) and source_words[head].value in _SHELL_PREFIXES:
            head += 1
            head += executable_word(source_words[head:], segment)
        words = [word.value for word in source_words[head:]]
        if words[:2] != ['dotnet', 'test']:
            continue
        settings = '' if '--settings' in words or any(w.startswith('--settings=') for w in words) else ' --settings .runsettings'
        start = source_words[head].start
        test_end = source_words[head + 1].end
        replacement = ('xvfb-run --auto-servernum ' + segment[start:test_end] + settings)
        replacements.append((at + start, at + test_end, replacement))
    if not replacements:
        print('{}')
        sys.exit(0)
    corrected = command
    for start, end, replacement in reversed(replacements):
        corrected = corrected[:start] + replacement + corrected[end:]
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'updatedInput': dict(tool_input, command=corrected),
        'additionalContext': '[cloud-test] Added xvfb-run and any missing .runsettings for the authenticated display (testing §Quick Start).',
    }}))
    sys.exit(0)


if __name__ == "__main__":
    main()
