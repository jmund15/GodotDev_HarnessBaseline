#!/usr/bin/env python3
"""
Hook (sub-hook of post_read_dispatch.py): logs every auto-memory file a
session actually loaded, so /self_evaluate's `memory_hits` field can be
seeded from fact instead of left blank.

Two provenances:
  via="read"   — a `Read` whose `file_path` contains `.claude/auto-memory/`, or a
                 `mcp__ai-worker__read_files` bundle whose `paths` do (the routed-correct
                 path for 3+ files — without it the log undercounts compliant sessions).
  via="search" — a semantic-search tool call whose `tool_response` embeds
                 one or more `.claude/auto-memory/...md` paths (regex over
                 the JSON-serialized response; the tool's own result shape
                 is not asserted).

Appends `{ts, session_id, agent_id, via, path}` per hit to logs/memory_hits.jsonl via
`_hook_state.append_jsonl_rotating` (`HARNESS_MEMORY_HITS_LOG` redirects it for proofs).
Pure observer: `process()` never raises and never blocks.

Wired in: sub-hook of post_read_dispatch.py (Read + search matcher), and directly on the
PostToolUse `mcp__ai-worker__read_files` matcher.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import log_path, append_jsonl_rotating  # noqa: E402

_MEMORY_PATH_RE = re.compile(r"\.claude/auto-memory/[A-Za-z0-9_./-]+\.md")
_SEARCH_TOOLS = {"mcp__plugin_semantic-search_semantic-search__search"}
_BUNDLE_TOOLS = {"mcp__ai-worker__read_files"}
LOG_ENV = "HARNESS_MEMORY_HITS_LOG"


def _log_path() -> str:
    return log_path("memory_hits.jsonl", LOG_ENV)


def _normalize(path: str) -> str:
    posix = path.replace("\\", "/")
    idx = posix.find(".claude/auto-memory/")
    return posix[idx:] if idx != -1 else posix


def _is_memory(path) -> bool:
    return isinstance(path, str) and ".claude/auto-memory/" in path.replace("\\", "/")


def process(input_data: dict) -> None:
    """Log any auto-memory hits in this PostToolUse call. Never raises."""
    try:
        tool_name = input_data.get("tool_name") or ""
        tool_input = input_data.get("tool_input") or {}
        session_id = input_data.get("session_id") or ""
        agent_id = input_data.get("agent_id") or ""
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        hits = []
        if tool_name == "Read":
            file_path = tool_input.get("file_path") or ""
            if _is_memory(file_path):
                hits.append(("read", _normalize(file_path)))
        elif tool_name in _BUNDLE_TOOLS:
            paths = tool_input.get("paths")
            if isinstance(paths, str):
                paths = [paths]
            for p in dict.fromkeys(paths or []):
                if _is_memory(p):
                    hits.append(("read", _normalize(p)))
        elif tool_name in _SEARCH_TOOLS:
            tool_response = input_data.get("tool_response")
            try:
                blob = json.dumps(tool_response)
            except (TypeError, ValueError):
                blob = str(tool_response)
            blob = blob.replace("\\\\", "/").replace("\\", "/")
            for match in dict.fromkeys(_MEMORY_PATH_RE.findall(blob)):
                hits.append(("search", match))

        if not hits:
            return

        records = [
            {
                "ts": ts,
                "session_id": session_id,
                "agent_id": agent_id,
                "via": via,
                "path": path,
            }
            for via, path in hits
        ]
        append_jsonl_rotating(_log_path(), records)
    except Exception:
        pass


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    process(input_data)
    sys.exit(0)


if __name__ == "__main__":
    main()
