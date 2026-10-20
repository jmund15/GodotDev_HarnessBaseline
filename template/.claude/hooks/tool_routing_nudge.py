#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse routing advice for evidence-backed decisions.

Retained checks:
- Bare PascalCase Grep over C# gets the C# anchor/LSP navigation advice (or
  semantic search when LSP is unavailable).
- A native Read while the user's request asks for bulk copyable extraction across inputs
  points at `read_files`. The verdict comes from the prompt, not the path or file type;
  a dispatched subagent is the bundling delegate and gets no advisory.

Retired checks:
- Read/Obsidian path shape and broad-search argument defaults. A directory name
  cannot distinguish known focused evidence, bulk copyable I/O, or derived
  judgment. `tool_routing_prompt_synthesis.py` presents that litmus once at the
  request boundary instead. The obsidian MCP server is no longer registered in
  this project, so its branches are gone rather than left as dead wiring.
- The vault-Write class advisory. It asked the model to name the doc's class after the
  call was admitted; the rule reaches the model in CLAUDE.core.md §9 before the call, and
  the routing audit's census row records the route from the call itself, so the note changed
  no decision (`instruction_quality` §3).

Advisories use `hookSpecificOutput.additionalContext`.
State writes are bounded, salvageable, and atomic.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import read_json_salvage, state_path, update_json_locked
from routing_classifier import (
    classify_call,
    grep_target_family,
    is_cloud_session,
    is_pascal_identifier,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

_SEEN_CAP = 200


def _ai_worker_absent() -> bool:
    """Is ai-worker registered on this machine? `False` whenever that cannot be read.

    `tools/workstation_profile.py` owns the walk and answers `None` for unknown; unknown is NOT
    absent, so an unreadable config keeps the authored advisory rather than narrowing it on a guess.
    The nudge itself is unaffected — this only decides which route it names.
    """
    try:
        import workstation_profile
        return workstation_profile.ai_worker() == "absent"
    except Exception:
        return False



def _read_last_prompt(session_id: str) -> str:
    state = read_json_salvage(state_path(session_id))
    return str(state.get("last_prompt") or "")


def _nudge_grep(tool_input: dict, last_prompt: str, agent_id: str = "") -> str | None:
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        return None

    family = grep_target_family(tool_input)
    if family == "other":
        return None
    classification = classify_call("Grep", tool_input, last_prompt, agent_id)
    if classification.severity != "nudge-warranted":
        return None
    if family == "cs":
        if is_cloud_session():
            return (
                f"[tool-routing] `Grep('{pattern}')` on `.cs` bypasses C# navigation. "
                "LSP is unavailable in this cloud session; use "
                f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')`. "
                "CLAUDE.core.md §9."
            )
        return (
            f"[tool-routing] Bare `Grep('{pattern}')` on `.cs` bypasses C# navigation. "
            f"Anchor with `Grep('class {pattern}\\b' -g '*.cs')`, then use "
            "`LSP documentSymbol` and `LSP findReferences`. CLAUDE.core.md §9."
        )

    target_label = "indexed types" if family == "indexed-other" else "an unrestricted target"
    return (
        f"[tool-routing] `Grep('{pattern}')` against {target_label} is a symbol-shaped "
        "lookup. When the name or its location is unknown, use "
        "`mcp__plugin_semantic-search_semantic-search__search`; Grep owns known literal names "
        "and patterns. CLAUDE.coding.md §Semantic Search MCP."
    )


def _nudge_native_read(tool_input: dict, last_prompt: str, agent_id: str = "") -> str | None:
    classification = classify_call("Read", tool_input, last_prompt, agent_id)
    if classification.severity != "nudge-warranted" or classification.rule != "native-read-bulk-copyable":
        return None
    name = (tool_input.get("file_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    route = ("a bounded Read (offset/limit) or a subagent" if _ai_worker_absent()
             else "`mcp__ai-worker__read_files(paths=[...], question=...)` when its input fits")
    return (
        f"[tool-routing] The request asks for bulk copyable extraction across inputs; a direct "
        f"`Read` of `{name}` loads the whole file into context. Route the extraction through "
        f"{route}, and require one result per input path. CLAUDE.core.md §9."
    )


_DISPATCH = {
    "Grep": _nudge_grep,
    "Read": _nudge_native_read,
}


def _nudge_target_key(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Grep":
        target = grep_target_family(tool_input)
    elif tool_name == "Read":
        target = "bulk-copyable"  # the advice reads identically for every input: once per session
    else:
        target = ""
    return f"{tool_name}:{str(target).replace(chr(92), '/').lower()}"


def _seen_before(session_id: str, key: str) -> bool:
    """Record one advisory per (tool, target family/path) until compaction."""
    path = state_path(session_id)

    def update(state):
        seen = state.get("nudge_targets_seen")
        seen = list(seen) if isinstance(seen, list) else []
        if key in seen:
            return True
        state["nudge_targets_seen"] = (seen + [key])[-_SEEN_CAP:]
        return False

    written, seen_before = update_json_locked(path, update)
    return bool(seen_before) if written else False


def _classification_rule(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Grep":
        return "pascal-grep-on-cs" if grep_target_family(tool_input) == "cs" else "pascal-grep-on-indexed"
    if tool_name == "Read":
        return "native-read-bulk-copyable"
    return ""


def _record_pre_nudge(session_id: str, rule: str) -> None:
    if not rule:
        return
    path = state_path(session_id)

    def update(state):
        fired = state.get("pre_nudges_fired_this_turn")
        fired = list(fired) if isinstance(fired, list) else []
        if rule not in fired:
            fired.append(rule)
        state["pre_nudges_fired_this_turn"] = fired[-20:]

    update_json_locked(path, update)


def process(input_data: dict) -> tuple[str | None, str | None]:
    """Return `(block_message, advisory_message)`; at most one is populated."""
    tool_name = input_data.get("tool_name") or ""
    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return (None, None)
    session_id = input_data.get("session_id") or ""
    agent_id = str(input_data.get("agent_id") or "")

    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return (None, None)
    try:
        last_prompt = _read_last_prompt(session_id)
        nudge = handler(tool_input, last_prompt, agent_id)
    except Exception:
        return (None, None)
    if not nudge:
        return (None, None)

    if _seen_before(session_id, _nudge_target_key(tool_name, tool_input)):
        return (None, None)
    _record_pre_nudge(session_id, _classification_rule(tool_name, tool_input))
    return (None, nudge)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    block_msg, nudge = process(input_data)
    if block_msg:
        sys.stderr.write(block_msg + "\n")
        sys.exit(2)
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
