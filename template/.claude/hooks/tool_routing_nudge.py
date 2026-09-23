#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PreToolUse routing advice for evidence-backed decisions.

Retained checks:
- Bare PascalCase Grep over C# gets the C# anchor/LSP navigation advice (or
  semantic search when LSP is unavailable). An opt-in env flag may block this
  high-confidence correctness smell.
- A document-sized direct vault Write asks the model to state whether the prose
  is judgment-dense or templated/mechanical.
- A native Read while the user's request asks for bulk copyable extraction across inputs
  points at `read_files`. The verdict comes from the prompt, not the path or file type;
  a dispatched subagent is the bundling delegate and gets no advisory.

Retired checks:
- Read/Obsidian path shape and broad-search argument defaults. A directory name
  cannot distinguish known focused evidence, bulk copyable I/O, or derived
  judgment. `tool_routing_prompt_synthesis.py` presents that litmus once at the
  request boundary instead.

Advisories use `hookSpecificOutput.additionalContext`; optional blocks use
stderr plus exit 2. State writes are bounded, salvageable, and atomic.
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
    prompt_has_grep_override_cue,
)

HARD_BLOCK_ENV_VAR = "HARNESS_ROUTING_HARD_BLOCK_CS_GREP"
_SEEN_CAP = 200


def _hard_block_enabled() -> bool:
    return os.environ.get(HARD_BLOCK_ENV_VAR, "").lower() in ("1", "true", "yes")


def _read_last_prompt(session_id: str) -> str:
    state = read_json_salvage(state_path(session_id))
    return str(state.get("last_prompt") or "")


def _should_hard_block_grep(tool_input: dict, session_id: str) -> bool:
    if not _hard_block_enabled():
        return False
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        return False
    if grep_target_family(tool_input) != "cs":
        return False
    return not prompt_has_grep_override_cue(_read_last_prompt(session_id))


def _build_block_message(pattern: str) -> str:
    if is_cloud_session():
        retry_path = (
            f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')` "
            "(cloud session — LSP unavailable)"
        )
    else:
        retry_path = (
            f"anchor the declaration with `Grep('class {pattern}\\b' -g '*.cs')` "
            "(or interface/record/struct/enum), then use `LSP documentSymbol` and "
            "`LSP findReferences`"
        )
    return (
        f"[tool-routing] BLOCKED: bare `Grep('{pattern}')` on `.cs` bypasses the "
        f"C# navigation contract. Retry: {retry_path}. Literal/comment scans and "
        "verified-unique names are valid overrides when the user request says so. "
        "CLAUDE.md §Tool Routing."
    )


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
                "CLAUDE.md §Tool Routing."
            )
        return (
            f"[tool-routing] Bare `Grep('{pattern}')` on `.cs` bypasses C# navigation. "
            f"Anchor with `Grep('class {pattern}\\b' -g '*.cs')`, then use "
            "`LSP documentSymbol` and `LSP findReferences`. CLAUDE.md §Tool Routing."
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
    return (
        f"[tool-routing] The request asks for bulk copyable extraction across inputs; a direct "
        f"`Read` of `{name}` loads the whole file into context. Route the extraction through "
        "`mcp__ai-worker__read_files(paths=[...], question=...)` when its input fits, and require "
        "one result per input path. CLAUDE.md §Tool Routing."
    )


def _nudge_obsidian_read(tool_input: dict, last_prompt: str, agent_id: str = "") -> str | None:
    tool_name = "mcp__obsidian__obsidian_get_note"
    classification = classify_call(tool_name, tool_input, last_prompt, agent_id)
    if (classification.severity != "nudge-warranted"
            or classification.rule != "obsidian-read-bulk-copyable"):
        return None
    target = tool_input.get("target") or {}
    path = target.get("path") if isinstance(target, dict) else ""
    name = str(path or "note").replace("\\", "/").rsplit("/", 1)[-1]
    return (
        f"[tool-routing] The request asks for bulk copyable extraction across inputs; a direct "
        f"Obsidian read of `{name}` loads the whole note into context. Route the extraction through "
        "`mcp__ai-worker__read_files(paths=[...], question=...)` when its input fits, and require "
        "one result per input path. CLAUDE.md §Tool Routing."
    )


def _nudge_vault_write(tool_input: dict, last_prompt: str, agent_id: str = "") -> str | None:
    classification = classify_call("Write", tool_input, last_prompt)
    if classification.rule != "vault-write-direct":
        return None
    name = (tool_input.get("file_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    size = len(tool_input.get("content") or "")
    return (
        f"[tool-routing] Direct `Write` of `{name}` ({size} chars) into the vault. Name its class "
        "in this turn: judgment-dense (assessment, review, design verdict, retrospective — direct "
        "Write is right) or templated/mechanical (route to `mcp__ai-worker__write_doc`). The routing "
        "audit logs this write either way; unclassified reads as a silent bypass. "
        "CLAUDE.md §Tool Routing."
    )


_DISPATCH = {
    "Grep": _nudge_grep,
    "Read": _nudge_native_read,
    "mcp__obsidian__obsidian_get_note": _nudge_obsidian_read,
    "Write": _nudge_vault_write,
}


def _nudge_target_key(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Write":
        target = tool_input.get("file_path") or ""
    elif tool_name == "Grep":
        target = grep_target_family(tool_input)
    elif tool_name in ("Read", "mcp__obsidian__obsidian_get_note"):
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
    if tool_name == "Write":
        return "vault-write-direct"
    if tool_name == "Grep":
        return "pascal-grep-on-cs" if grep_target_family(tool_input) == "cs" else "pascal-grep-on-indexed"
    if tool_name == "Read":
        return "native-read-bulk-copyable"
    if tool_name == "mcp__obsidian__obsidian_get_note":
        return "obsidian-read-bulk-copyable"
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

    if tool_name == "Grep" and _should_hard_block_grep(tool_input, session_id):
        return (_build_block_message(tool_input.get("pattern") or ""), None)

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
