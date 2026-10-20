#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the read/search tool family.

One settings entry runs five sub-hooks in order:
  1. routing_audit.process — classification log
  2. memory_hits_logger.process — auto-memory hit log
  3. model_ladder_gate.mark_loaded — compaction-scoped marker after a full exact-path ladder Read
  4. runaway_scan_reaper.check — throttled orphaned-search reaper, one line per reaped pid
  5. skill_load_marker.process — a full SKILL.md Read records that skill as loaded

Output contract:
- Reaper and skill notes share one `hookSpecificOutput.additionalContext` payload and exit 0.
- One sub-hook fault stays fail-open and does not disable later checks.

Wired in: settings.json hooks.PostToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|mcp__plugin_semantic-search_semantic-search__search|mcp__ai-worker__write_doc".
The sub-hooks keep their own `main()` for standalone proofs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import routing_audit
import memory_hits_logger
import model_ladder_gate


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    contexts = []

    # 1. Routing-audit classification log (pure reader, no output).
    try:
        routing_audit.process(input_data)
    except Exception:
        pass

    # 2. Memory-hits log (pure reader, no output).
    try:
        memory_hits_logger.process(input_data)
    except Exception:
        pass

    # 3. A successful full Read arms one model-ladder dispatch selection.
    try:
        model_ladder_gate.mark_loaded(input_data)
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

    # 5. A full SKILL.md Read loads that skill, as a Skill call does (skill-gated hooks read it).
    try:
        path = str(((input_data.get("tool_input") or {}).get("file_path")) or "")
        if input_data.get("tool_name") == "Read" and path.replace("\\", "/").endswith("/SKILL.md"):
            import skill_load_marker  # imported only for a SKILL.md Read: every Read pays this hook
            note = skill_load_marker.process(input_data)
        else:
            note = ""
        if note:
            contexts.append(note)
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
