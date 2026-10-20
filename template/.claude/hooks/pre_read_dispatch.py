#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

One settings entry runs five checks in order:
  1. indexed_reference_guard.process — indexed whole-file Read → advise the main session, block a delegate
  2. semantic_search_scope_guard.process — a scope its values settle → corrected via `updatedInput`;
     one that leaves the project → block
  3. file_size_preblock.process — large unbounded Read → clamped to a line limit via
     `updatedInput`, or blocked when its first line alone exceeds the budget
  4. tool_routing_nudge.process — optional hard block or advisory
  5. video_read_hint.advisory — a Read of a video or GIF → advisory naming the contact-sheet tool

Checks 1, 2 and 4 ship in the coding layer; each runs only where its file exists.
A dispatched subagent skips only the file-size block because its context is discarded after the
returned digest. The index and scope guards prevent per-agent input or index-build cost, and routing
advice remains correct for every agent. `agent_id` is the measured subagent marker; `session_id` is
shared. Each file-size exemption is counted in session state.

Output contract:
- A block writes stderr and exits 2.
- An advisory emits `hookSpecificOutput.additionalContext` and exits 0.
- Each sub-hook self-gates on `tool_name`.
- One sub-hook fault stays fail-open and does not disable later checks. A present member's
  import fault names the lost check and repair in `additionalContext`; an absent optional member is silent.

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|mcp__plugin_semantic-search_semantic-search__search".
The sub-hooks keep their own `main()` for standalone proofs.
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _optional_hooks

_IMPORT_ERRORS = {}


def _failed_import(name, exc):
    _IMPORT_ERRORS[name] = '%s: %s' % (type(exc).__name__, exc)
    return None


try:
    file_size_preblock = importlib.import_module('file_size_preblock')
except Exception as exc:
    file_size_preblock = _failed_import('file_size_preblock', exc)
rail_probe_guard, indexed_reference_guard, semantic_search_scope_guard, tool_routing_nudge, video_read_hint = (
    _optional_hooks.load('rail_probe_guard', 'indexed_reference_guard', 'semantic_search_scope_guard',
                         'tool_routing_nudge', 'video_read_hint', on_error=_failed_import))


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

    # 0. Index-served references: advise the main session and block delegates. NOT subagent-exempt:
    # per-agent input spend multiplies across a fan-out, and a discarded context does not recover it.
    index_advisory = None
    try:
        result = indexed_reference_guard.process(input_data) if indexed_reference_guard else None
        if result and result.get("block"):
            sys.stderr.write(str(result["block"]) + "\n")
            sys.exit(2)
        if result:
            index_advisory = result.get("advisory")
    except Exception:
        pass

    # 0b. Semantic-search scope guard. NOT subagent-exempt: an out-of-root searchDir costs the same
    # index rebuild whoever calls it, unlike the orchestrator-context cost the exemption targets.
    rewrite, rewrite_note = None, None
    try:
        result = semantic_search_scope_guard.process(input_data) if semantic_search_scope_guard else None
        if result and result.get("deny"):
            sys.stderr.write(str(result["deny"]) + "\n")
            sys.exit(2)
        if result and result.get("rewrite"):
            rewrite, rewrite_note = result["rewrite"], result.get("note")
    except Exception:
        pass

    # 1. Large file: clamp the read to the lines that fit, or block when no line range fits.
    clamp = None
    if not subagent and file_size_preblock is not None:
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

    video = None
    try:
        video = video_read_hint.advisory(input_data) if video_read_hint else None
    except Exception:
        pass

    faults = ['%s failed to import (%s); its checks do not run. Repair hooks/%s.py.' % (name, error, name)
              for name, error in _IMPORT_ERRORS.items()]
    context = "\n".join(c for c in (*faults, index_advisory, rewrite_note, (clamp or {}).get("additionalContext"), nudge, video) if c)
    if clamp or rewrite or context:
        output = {"hookEventName": "PreToolUse"}
        if clamp:
            output["updatedInput"] = clamp["updatedInput"]
        if rewrite:
            output["updatedInput"] = dict(output.get("updatedInput") or {}, **rewrite)
        if context:
            output["additionalContext"] = context
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}))

    sys.exit(0)


if __name__ == "__main__":
    main()
