#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the read/search tool family.

One settings entry runs six sub-hooks in order:
  1. routing_audit.process — classification log
  2. memory_hits_logger.process — auto-memory hit log
  3. model_ladder_gate.mark_loaded — compaction-scoped marker after a full exact-path ladder Read
  4. runaway_scan_reaper.check — throttled orphaned-search reaper, one line per reaped pid; run
     only for tools outside the standalone reaper entry's matcher (LSP and mcp__* are inside it)
  5. skill_load_marker.process — a full SKILL.md Read records that skill as loaded
  6. harness_read_notice — one line at the first Read of a harness-edit-governed file in a window where
     `instruction_quality` is absent. It runs after 5, so the Read that loads the skill never gets it.

Output contract:
- Reaper, skill and harness-read notes share one `hookSpecificOutput.additionalContext` payload and exit 0.
- One sub-hook fault stays fail-open and does not disable later checks.

Wired in: settings.json hooks.PostToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|LSP|mcp__plugin_semantic-search_semantic-search__search|mcp__ai-worker__write_doc".
The sub-hooks keep their own `main()` for standalone proofs.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import routing_audit
import memory_hits_logger
import model_ladder_gate
from _claude_scope import harness_tail
from _hook_state import _agent_suffix, fire_once_since_compaction, skill_in_context

# The matcher of the standalone runaway_scan_reaper entry in settings.base.json. A tool it covers
# is reaped there, so this dispatcher reaping it again would run the scan twice per call.
_STANDALONE_REAPER = re.compile(r"^(?:Agent|Workflow|TaskStop|TaskOutput|Skill|ToolSearch|LSP|NotebookEdit|mcp__.*)$")


def runs_reaper(tool_name):
    return not _STANDALONE_REAPER.match(tool_name or "")


def harness_read_notice(input_data):
    """One line at the first Read of a harness-edit-governed file in a window where `instruction_quality` is
    absent: the edit gate's requirement and the reference file the path's class matches, before the first edit.
    Silent on later Reads, once the skill is loaded, and for a path the edit gate does not govern. A subagent's
    payload `agent_id` gives it its own first notice."""
    if input_data.get("tool_name") != "Read":
        return ""
    path = str(((input_data.get("tool_input") or {}).get("file_path")) or "")
    if harness_tail(path) is None:
        return ""
    import harness_edit_skill_reminder  # imported only for a `.claude/` Read; every Read pays this hook
    if not harness_edit_skill_reminder.is_harness_file(path):
        return ""
    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id")
    if skill_in_context(session_id, "instruction_quality", agent_id,
                        transcript_path=input_data.get("transcript_path")):
        return ""
    if not fire_once_since_compaction(session_id, "harness_read_notice" + _agent_suffix(agent_id)):
        return ""
    return harness_edit_skill_reminder.advance_notice(path)


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

    # 4. Runaway-scan reaper (throttled), for the tools the standalone reaper entry does not cover.
    if runs_reaper(input_data.get("tool_name")):
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

    # 6. The harness-edit gate's advance notice, after the SKILL.md marker: the Read that loads the skill
    # never gets told to load it.
    try:
        note = harness_read_notice(input_data)
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
