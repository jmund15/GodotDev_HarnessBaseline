#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_subagent_tools.py — extract the ordered list of tool calls from a
Claude Code subagent's JSONL transcript.

Background: when a parent session dispatches a subagent via the Agent tool,
the subagent's tool calls do NOT appear in the parent's JSONL — they live in
a separate file at:

    ~/.claude/projects/<sanitized-cwd>/<session_id>/subagents/agent-<agent_id>.jsonl

Each line is a JSON object; assistant messages contain `content` arrays whose
`tool_use` blocks carry the tool `name`. This module returns those names in
call order, providing a deterministic alternative to the agent-cooperation-
dependent `[TOOLS-USED:]` self-report header.

Usage from Python:

    from extract_subagent_tools import extract_tool_names
    tools = extract_tool_names(agent_id="a0a831cf99ddd6663", session_id="5f2759b6-...")
    # → ["mcp__obsidian__obsidian_global_search", "mcp__ai-worker__read_files", ...]

Usage from CLI:

    python3 extract_subagent_tools.py <agent_id> [<session_id>]
    # Prints one tool name per line in call order. Empty output if not found.

Discovery: if `session_id` is omitted, the script globs all session
subdirectories under the project root for the matching agent file. With
session_id provided the lookup is O(1).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def sanitize_cwd(cwd: str | os.PathLike[str]) -> str:
    """
    Reproduce Claude Code's project-directory sanitization. Empirically:
    every non-alphanumeric, non-dash character is replaced with a dash.
    Confirmed against `~/.claude/projects/` listings (e.g.
    '{{PROJECT_ROOT}}' →
    'C--Users-you-Godot-Projects-{{PROJECT_NAME}}').
    """
    return re.sub(r"[^A-Za-z0-9-]", "-", str(cwd))


def find_subagent_jsonl(
    agent_id: str,
    session_id: str | None = None,
    project_dir: str | os.PathLike[str] | None = None,
) -> Path | None:
    """
    Locate a subagent's JSONL file. Returns None if not found.

    Resolution order:
    1. If session_id + project_dir provided: direct path lookup (O(1)).
    2. If session_id provided but no project_dir: glob across all project
       roots for the session. (Subagent files sometimes appear under the
       worktree's project dir even when cwd is the main repo.)
    3. Fallback: glob across all projects for any
       `*/<session_id>/subagents/agent-<agent_id>.jsonl` if session_id given,
       otherwise `*/*/subagents/agent-<agent_id>.jsonl`.
    """
    if not agent_id:
        return None

    target_name = f"agent-{agent_id}.jsonl"

    # Path 1: O(1) direct lookup
    if session_id and project_dir:
        candidate = Path(project_dir) / session_id / "subagents" / target_name
        if candidate.exists():
            return candidate

    if not PROJECTS_ROOT.exists():
        return None

    # Path 2 & 3: glob scan
    if session_id:
        # Narrow scan: only look in the named session dir under any project
        for project in PROJECTS_ROOT.iterdir():
            if not project.is_dir():
                continue
            candidate = project / session_id / "subagents" / target_name
            if candidate.exists():
                return candidate
    else:
        # Wide scan: any project, any session
        # Use glob rather than rglob for performance — depth is fixed.
        for project in PROJECTS_ROOT.iterdir():
            if not project.is_dir():
                continue
            for sess in project.iterdir():
                if not sess.is_dir():
                    continue
                candidate = sess / "subagents" / target_name
                if candidate.exists():
                    return candidate

    return None


def extract_tool_names(
    agent_id: str,
    session_id: str | None = None,
    project_dir: str | os.PathLike[str] | None = None,
) -> list[str]:
    """
    Return tool-call names in call order from a subagent's JSONL.

    Returns an empty list if:
    - the file is not found
    - the subagent made zero tool calls
    - the file is malformed (each unparseable line is skipped, not raised)

    Each tool call appears once per `tool_use` content block in `assistant`
    messages. Parallel tool calls within a single message preserve their
    block order. ToolSearch / TodoWrite are NOT filtered here — that
    classification belongs to the caller (e.g. score_routing_battery.py
    has its own SKIP_TOOLS set).
    """
    jsonl_path = find_subagent_jsonl(agent_id, session_id, project_dir)
    if jsonl_path is None:
        return []

    tools: list[str] = []
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue

                content = msg.get("content")
                if not isinstance(content, list):
                    continue

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    name = block.get("name")
                    if isinstance(name, str) and name:
                        tools.append(name)
    except OSError:
        return []

    return tools


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write(
            "usage: extract_subagent_tools.py <agent_id> [<session_id>]\n"
        )
        return 2

    agent_id = sys.argv[1]
    session_id = sys.argv[2] if len(sys.argv) >= 3 else None

    tools = extract_tool_names(agent_id, session_id)
    for t in tools:
        print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
