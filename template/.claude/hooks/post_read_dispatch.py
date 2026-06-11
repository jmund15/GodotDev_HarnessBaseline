#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the read/search tool family.

Single settings.json entry replacing three separate hook commands
(tool_routing_cumulative.py, tool_routing_post_grep.py, routing_audit.py).
One interpreter spawn per matched call instead of three, and in-process
writer-first ordering — the shared-state ordering that previously depended
on same-matcher-block convention (gotcha_posttooluse_hook_read_after_write_
ordering) is now structural.

Order:
  1. tool_routing_cumulative.process — WRITER (counts the call, owns state)
  2. tool_routing_post_grep.process  — reader + writer (per-pattern dedupe)
  3. routing_audit.process           — pure reader (classification log)

Output contract:
  - Nudge texts from 1+2 merge into ONE hookSpecificOutput.additionalContext
    payload (exit 0) — the only model-visible advisory channel on PostToolUse.
  - Fail-open: a sub-hook exception is swallowed; later sub-hooks still run.

Wired in: settings.json hooks.PostToolUse with matcher
"Read|Grep|Glob|mcp__obsidian__obsidian_read_note|mcp__obsidian__obsidian_global_search|mcp__plugin_semantic-search_semantic-search__search".
The three sub-hooks keep their own main() for standalone use/testing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tool_routing_cumulative
import tool_routing_post_grep
import routing_audit


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    contexts = []

    # 1. Cumulative counter (state writer — must run first).
    try:
        nudge = tool_routing_cumulative.process(input_data)
        if nudge:
            contexts.append(nudge)
    except Exception:
        pass

    # 2. Retroactive Grep nudge (reads state written above).
    try:
        nudge = tool_routing_post_grep.process(input_data)
        if nudge:
            contexts.append(nudge)
    except Exception:
        pass

    # 3. Routing-audit classification log (pure reader, no output).
    try:
        routing_audit.process(input_data)
    except Exception:
        pass

    if contexts:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n\n".join(contexts),
            }
        }
        sys.stdout.write(json.dumps(payload))

    sys.exit(0)


if __name__ == "__main__":
    main()
