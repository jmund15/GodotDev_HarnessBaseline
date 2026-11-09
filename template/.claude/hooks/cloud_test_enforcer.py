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
when that invocation has none. A shell wrapper before it, for example `time`, `sudo`, `env -i`
or `!`, together with that wrapper's flag arguments, does not hide the test. Quoted mentions,
non-shell heredoc bodies and `dotnet test` named as another command's argument stay unchanged.
"""

import json
import os
import sys

from _claude_scope import is_cloud
from _command_text import executable_spans, command_words, executable_word

# A wrapper runs the words after it instead of taking them as data. These are examples, not the
# definition: a wrapper missing here leaves the test unwrapped, so add it with a case in
# .claude/tests/test_cloud_test_enforcer.py.
_WRAPPERS = frozenset({'time', '{', 'exec', 'nohup', 'env', 'command', 'builtin', '!', 'nice',
                       'stdbuf', 'sudo'})
# The wrapper options that consume the next word; every other `-` word is a switch.
_WRAPPER_VALUE_OPTS = {
    'nice': ('-n', '--adjustment'),
    'sudo': ('-u', '-g', '-p', '-C', '-T', '-U', '-r', '-t', '-D', '-R', '-h', '-a', '-c'),
    'stdbuf': ('-i', '-o', '-e', '--input', '--output', '--error'),
    'env': ('-u', '-C', '-S', '--unset', '--chdir', '--split-string'),
    'exec': ('-a',),
}


def _test_index(source_words, segment):
    """Index of the `dotnet test` word pair the segment runs, after assignments, wrappers and their
    flag arguments; None when `dotnet test` is another command's data."""
    head = executable_word(source_words, segment)
    wrapper = ''
    while head < len(source_words):
        if [word.value for word in source_words[head:head + 2]] == ['dotnet', 'test']:
            return head
        value = source_words[head].value
        if value in _WRAPPERS:
            wrapper = value
        elif value.startswith('-'):
            pass
        else:
            return None
        head += 1
        if source_words[head - 1].value in _WRAPPER_VALUE_OPTS.get(wrapper, ()):
            head += 1
        head += executable_word(source_words[head:], segment)
    return None


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

    replacements = []
    for at, end, segment in executable_spans(command):
        parsed = command_words(segment)
        if parsed is None:
            continue
        source_words, _ = parsed
        head = _test_index(source_words, segment)
        if head is None:
            continue
        words = [word.value for word in source_words[head:]]
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
