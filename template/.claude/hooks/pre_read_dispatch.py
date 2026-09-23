#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

One settings entry runs four checks in order:
  1. indexed_reference_guard.process — indexed whole-file Read → block
  2. semantic_search_scope_guard.process — invalid search scope → block
  3. file_size_preblock.process — large unbounded Read → clamped to a line limit via
     `updatedInput`, or blocked when its first line alone exceeds the budget
  4. tool_routing_nudge.process — optional hard block or advisory

Checks 1, 2 and 4 ship in the coding layer; each runs only where its file exists.
A dispatched subagent skips only the file-size block because its context is discarded after the
returned digest. The index and scope guards prevent per-agent input or index-build cost, and routing
advice remains correct for every agent. `agent_id` is the measured subagent marker; `session_id` is
shared. Each file-size exemption is counted in session state.

Output contract:
- A block writes stderr and exits 2.
- An advisory emits `hookSpecificOutput.additionalContext` and exits 0.
- Each sub-hook self-gates on `tool_name`.
- One sub-hook fault stays fail-open and does not disable later checks.

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|mcp__obsidian__obsidian_get_note|mcp__obsidian__obsidian_search_notes|mcp__plugin_semantic-search_semantic-search__search".
The sub-hooks keep their own `main()` for standalone proofs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import file_size_preblock
try:  # project-local (rail battery); absent in projects that sync only the baseline
    import rail_probe_guard
except ImportError:
    rail_probe_guard = None
import _optional_hooks

indexed_reference_guard, semantic_search_scope_guard, tool_routing_nudge = _optional_hooks.load(
    "indexed_reference_guard", "semantic_search_scope_guard", "tool_routing_nudge")


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
        from _hook_state import update_json_locked
        path = file_size_preblock._state_path(
            str(input_data.get("session_id") or ""), str(input_data.get("agent_id") or ""))

        def count(state):
            state["subagent_read_exemptions"] = int(state.get("subagent_read_exemptions") or 0) + 1
            state["subagent_agent_type"] = str(input_data.get("agent_type") or "")

        update_json_locked(path, count)
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # A dispatched subagent skips the context-cost file-size block; it still receives advice.
    subagent = _is_subagent(input_data)
    if subagent:
        _count_exemption(input_data)

    # Rail-battery cell guard: inert (one stat) unless a battery run is armed for this session.
    try:
        block_msg = rail_probe_guard.decide(input_data) if rail_probe_guard else None
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        pass

    # 0. Index-served reference block. NOT subagent-exempt: the cost it prevents is per-agent
    # input spend multiplied across a fan-out, which a discarded context does not recover.
    try:
        block_msg = indexed_reference_guard.process(input_data) if indexed_reference_guard else None
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    # 0b. Semantic-search scope guard. NOT subagent-exempt: an out-of-root searchDir costs the same
    # index rebuild whoever calls it, unlike the orchestrator-context cost the exemption targets.
    try:
        result = semantic_search_scope_guard.process(input_data) if semantic_search_scope_guard else None
        if result and result.get("deny"):
            sys.stderr.write(str(result["deny"]) + "\n")
            sys.exit(2)
    except Exception:
        pass

    # 1. Large file: clamp the read to the lines that fit, or block when no line range fits.
    clamp = None
    if not subagent:
        try:
            result = file_size_preblock.process(input_data)
            if isinstance(result, dict):
                clamp = result
            elif result:
                sys.stderr.write(result + "\n")
                sys.exit(2)
        except Exception:
            pass

    # 2. Routing nudge: hard block (env-gated) or advisory.
    nudge = None
    try:
        block_msg, nudge = tool_routing_nudge.process(input_data) if tool_routing_nudge else (None, None)
        if block_msg:
            sys.stderr.write(block_msg + "\n")
            sys.exit(2)
    except Exception:
        pass

    context = "\n".join(c for c in ((clamp or {}).get("additionalContext"), nudge) if c)
    if clamp or context:
        output = {"hookEventName": "PreToolUse"}
        if clamp:
            output["updatedInput"] = clamp["updatedInput"]
        if context:
            output["additionalContext"] = context
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}))

    sys.exit(0)


if __name__ == "__main__":
    main()
