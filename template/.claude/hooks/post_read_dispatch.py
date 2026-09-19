#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the read/search tool family.

One settings entry runs four sub-hooks in order:
  1. tool_routing_post_grep.process — fallback Grep advisory and receipt writer
  2. routing_audit.process — classification log
  3. memory_hits_logger.process — auto-memory hit log
  4. runaway_scan_reaper.check — throttled orphaned-search reaper, one line per reaped pid

Output contract:
- Grep advice emits one `hookSpecificOutput.additionalContext` payload and exits 0.
- One sub-hook fault stays fail-open and does not disable later checks.

Wired in: settings.json hooks.PostToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|mcp__obsidian__obsidian_get_note|mcp__obsidian__obsidian_search_notes|mcp__plugin_semantic-search_semantic-search__search|mcp__ai-worker__write_doc".
The sub-hooks keep their own `main()` for standalone proofs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tool_routing_post_grep
import routing_audit
import memory_hits_logger


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    contexts = []

    # 1. Retroactive Grep nudge and per-pattern receipt.
    try:
        nudge = tool_routing_post_grep.process(input_data)
        if nudge:
            contexts.append(nudge)
    except Exception:
        pass

    # 2. Routing-audit classification log (pure reader, no output).
    try:
        routing_audit.process(input_data)
    except Exception:
        pass

    # 3. Memory-hits log (pure reader, no output).
    try:
        memory_hits_logger.process(input_data)
    except Exception:
        pass

    # 4. Runaway-scan reaper (throttled; replaces its PostToolUse `*` registration for this family).
    try:
        import runaway_scan_reaper
        lines = runaway_scan_reaper.check(input_data)
        if lines:
            contexts.append("\n".join(lines))
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
