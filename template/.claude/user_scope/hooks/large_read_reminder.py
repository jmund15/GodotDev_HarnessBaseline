#!/usr/bin/env python3
"""PostToolUse Read advisory; home: ~/.claude/CLAUDE.md, Local worker.

Measure returned text, not total file size. Unknown response shapes stay silent.
Proof: .claude/tests/test_large_read_reminder.py. Never blocks a tool.
"""
import json
import sys


def returned_lines(response):
    if isinstance(response, str):
        return response.count("\n") + int(bool(response) and not response.endswith("\n"))
    if isinstance(response, list):
        return sum(returned_lines(block.get("text", "")) for block in response
                   if isinstance(block, dict) and block.get("type") == "text")
    if isinstance(response, dict):
        file = response.get("file")
        if isinstance(file, dict) and response.get("type") == "text":
            count = file.get("numLines")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count
            return returned_lines(file.get("content"))
        return returned_lines(response.get("content", response.get("text")))
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict) or event.get("tool_name") != "Read":
            return 0
        count = returned_lines(event.get("tool_response", event.get("tool_result")))
        if count > 400:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[ai-worker] Read returned {count} lines. If the request is copyable bulk "
                    "extraction across inputs, route it through mcp__ai-worker__read_files with a "
                    "bounded answer; focused evidence, exact edits and verbatim checks stay with "
                    "Read. Home: ~/.claude/CLAUDE.md, Local worker."
                )}}))
    except (ValueError, TypeError, OSError, RecursionError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
