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

This hook blocks bare `dotnet test` commands on cloud and tells the agent
to prefix with `xvfb-run --auto-servernum`.
"""

import json
import os
import sys


def is_cloud() -> bool:
    """Return True if running in a Claude Code cloud environment."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() == "true"


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

    # Check if this is a dotnet test command (not dotnet build, clean, etc.)
    if "dotnet test" not in command:
        print("{}")
        sys.exit(0)

    # Already has xvfb-run — allow through
    if "xvfb-run" in command:
        print("{}")
        sys.exit(0)

    # Block and provide the corrected command
    corrected = f"xvfb-run --auto-servernum {command}"
    message = (
        f"BLOCKED: Cloud environment requires xvfb-run for GdUnit4 tests.\n"
        f"GdUnit4 uses .NET Named Pipes (Unix domain sockets). Godot needs "
        f"a properly authenticated X display to start, or the pipe server "
        f"never initializes (10+ min timeout).\n"
        f"Rewrite as:\n  {corrected}"
    )
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
