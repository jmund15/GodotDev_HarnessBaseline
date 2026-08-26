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

SUBAGENTS ARE EXEMPT FROM THE TWO BLOCKS (1 and 2), NOT FROM THE ADVISORY (3).
Both blocks exist for one reason: a read spends the ORCHESTRATOR's context, permanently, and a
PreToolUse block is the only thing that can prevent the spend. A dispatched subagent's context is
discarded the moment it returns its digest -- discarding it is the entire reason it was dispatched.
So on a subagent those blocks fire against a cost that does not exist, and they stop exactly the
work the agent was spawned to do: a reader told to bundle its reads into a worker has nowhere to
bundle them, because it IS the bundling step.

The advisory survives, because its content is a CORRECTNESS claim rather than a cost one -- "use
LSP rather than a bare Grep for a C# symbol" is as true inside a subagent as outside, and it is
exit-0 advice that blocks nothing.

Detection is `agent_id`, measured (see readonly_lens_write_guard.py's header): a Workflow
subagent's PreToolUse payload carries a non-empty `agent_id` and `agent_type:
"workflow-subagent"`, while the orchestrator's payload carries no `agent_id` at all. `session_id`
is IDENTICAL for both and cannot be used.

Every exemption is COUNTED into the session state file. An exemption that is silently right and an
exemption that is silently wrong emit identical evidence, so the count is the named trigger: if
subagent reads are being exempted far more often than subagents are being dispatched, the
detection is matching something it should not.

Output contract:
  - Any block message → stderr + exit 2 (model-visible).
  - Advisory nudge → hookSpecificOutput.additionalContext JSON + exit 0
    (the only model-visible advisory channel on PreToolUse — see
    archive_hook_gotchas.md).
  - Each sub-hook self-gates on tool_name, so the union matcher is safe.
  - Fail-open: a sub-hook exception is swallowed (advisory lost, tool runs).

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|mcp__obsidian__obsidian_get_note|mcp__obsidian__obsidian_search_notes|mcp__plugin_semantic-search_semantic-search__search".
The three sub-hooks keep their own main() for standalone use/testing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import file_size_preblock
import indexed_reference_guard
import tool_routing_cumulative_block
import tool_routing_nudge


def _is_subagent(input_data) -> bool:
    """True when this call comes from a dispatched subagent rather than the orchestrator.

    `agent_id` is the only field that separates them -- `session_id` is shared. Fail CLOSED (treat
    as orchestrator) on anything unexpected, so a payload change re-arms the blocks rather than
    silently disabling them everywhere.
    """
    try:
        return bool(str(input_data.get("agent_id") or "").strip())
    except Exception:
        return False


def _count_exemption(input_data) -> None:
    """Record that a context-cost block was skipped, so the exemption is auditable.

    Best-effort and never raises: an accounting failure must not change whether a tool runs.
    """
    try:
        path = file_size_preblock._state_path(
            str(input_data.get("session_id") or ""), str(input_data.get("agent_id") or ""))
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                state = {}
        except (OSError, json.JSONDecodeError, ValueError):
            state = {}
        state["subagent_read_exemptions"] = int(state.get("subagent_read_exemptions") or 0) + 1
        state["subagent_agent_type"] = str(input_data.get("agent_type") or "")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # A dispatched subagent skips the two CONTEXT-COST blocks; see the module docstring. It still
    # receives the advisory below, whose content is about correctness rather than cost.
    subagent = _is_subagent(input_data)
    if subagent:
        _count_exemption(input_data)

    # 0. Index-served reference block. NOT subagent-exempt: the cost it prevents is per-agent
    # input spend multiplied across a fan-out, which a discarded context does not recover.
    try:
        block_msg = indexed_reference_guard.process(input_data)
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    # 1. Large-file block.
    if not subagent:
        try:
            block_msg = file_size_preblock.process(input_data)
            if block_msg:
                sys.stderr.write(block_msg + "\n")
                sys.exit(2)
        except Exception:
            pass

    # 2. Cascade backstop block (env-gated).
    if not subagent:
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
