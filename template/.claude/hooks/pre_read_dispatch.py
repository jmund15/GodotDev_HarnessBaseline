#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

Single settings.json entry replacing three separate hook commands
(file_size_preblock.py, tool_routing_cumulative_block.py,
tool_routing_nudge.py). One interpreter spawn per matched call instead of
three, and deterministic in-process ordering instead of relying on
matcher-block conventions (per docs, matching hooks run in parallel).

Order (blocks before advisories; first block wins):
  1. file_size_preblock.process      — large unbounded Read → block
  2. tool_routing_cumulative_block.process — cascade backstop → block (env-gated)
  3. tool_routing_nudge.process      — hard-block (env-gated) or advisory nudge

Output contract:
  - Any block message → stderr + exit 2 (model-visible).
  - Advisory nudge → hookSpecificOutput.additionalContext JSON + exit 0
    (the only model-visible advisory channel on PreToolUse — see
    archive_hook_gotchas.md).
  - Each sub-hook self-gates on tool_name, so the union matcher is safe.
  - Fail-open: a sub-hook exception is swallowed (advisory lost, tool runs).

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|mcp__obsidian__obsidian_read_note|mcp__obsidian__obsidian_global_search|mcp__plugin_semantic-search_semantic-search__search".
The three sub-hooks keep their own main() for standalone use/testing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import file_size_preblock
import tool_routing_cumulative_block
import tool_routing_nudge


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # 1. Large-file block.
    try:
        block_msg = file_size_preblock.process(input_data)
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    # 2. Cascade backstop block (env-gated).
    try:
        block_msg = tool_routing_cumulative_block.process(input_data)
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    # 3. Routing nudge: hard block (env-gated) or advisory.
    nudge = None
    try:
        block_msg, nudge = tool_routing_nudge.process(input_data)
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    if nudge:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": nudge,
            }
        }
        sys.stdout.write(json.dumps(payload))

    sys.exit(0)


if __name__ == "__main__":
    main()
