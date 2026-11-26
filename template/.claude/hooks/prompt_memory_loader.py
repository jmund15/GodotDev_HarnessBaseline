#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit (prompt_dispatch member, audience `owner`) - say once that this session has consulted no
auto-memory entry, while the memory holds some.

The decision rests on two observed facts, never on the prompt's words:
- `.claude/auto-memory/` holds entries (its `*.md` files besides `MEMORY.md`; `archive/` is cold);
- the session's own transcript (`payload.transcript_path`) shows none consulted since the latest compaction: a main
  thread `Read` (or `read_files` bundle) of an entry, or the result of a semantic-search call that names one. A
  compaction boundary row resets the fact, because the compacted context lost what was read.
A session that has consulted memory hears nothing, which is what an agreed-work directive ("apply it") used to need a
phrase list for. The transcript is read through `_transcript_scan` (one resumable fold, shared with the other
transcript readers). The nudge speaks at most once per compaction window (`fire_once_since_compaction`, spent before
the transcript is read): a found hit spends the key too, so no later prompt of the window reads the transcript, and a
compaction, which lost the earlier line, re-arms both. A transcript that cannot be read leaves the nudge standing. A
prompt under MIN_PROMPT_CHARS is a confirmation that earns nothing and spends nothing.

Why a fact and not a match: relevance of an entry to a prompt is not decidable from the index's wording without a
model. Matching prompt words against the entries' name and description fires on mostly unrelated entries, so the
agent, which can judge relevance, is told the state and the search tool. The rule it serves is `CLAUDE.core.md` *Memory*; the plan-file domain table
stays with `plan_memory_reminder`.

Whether a person typed the prompt is the dispatcher's decision (`prompt_dispatch.AUDIENCE`).
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _transcript_scan  # noqa: E402
from _hook_state import fire_once_since_compaction  # noqa: E402

_CLAUDE_DIR = os.path.dirname(HERE)

# A confirmation ("lgtm", "push") starts no task; the floor is the length every such reply stays under.
MIN_PROMPT_CHARS = 20
NUDGE_KEY = "memory_nudge"
_SEARCH_TOOL = "mcp__plugin_semantic-search_semantic-search__search"
_READ_TOOLS = ("Read", "mcp__ai-worker__read_files")  # the second is the routed path for a 3+ file read
_ENTRY = re.compile(r"\.claude/auto-memory/(?:[\w.-]+/)*(?!MEMORY\.md\b)[\w.-]+\.md")
_PENDING_CAP = 50


def entry_count(claude_dir: str = _CLAUDE_DIR) -> int:
    """The auto-memory entries: top-level `*.md` files other than the `MEMORY.md` index."""
    try:
        names = os.listdir(os.path.join(claude_dir, "auto-memory"))
    except OSError:
        return 0
    return sum(1 for name in names if name.endswith(".md") and name != "MEMORY.md")


def _names_entry(*texts) -> bool:
    return any(_ENTRY.search(str(text).replace("\\", "/")) for text in texts)


def _result_text(content) -> str:
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _feed(state, _index, line):
    """Fold one transcript line: `hit` is whether an entry was consulted since the latest compaction boundary;
    `pending` holds the ids of search calls whose result has not arrived."""
    if "auto-memory" not in line and _SEARCH_TOOL not in line and "compact_boundary" not in line:
        return
    try:
        row = json.loads(line)
    except ValueError:
        return
    if not isinstance(row, dict) or row.get("isSidechain"):
        return
    if row.get("subtype") == "compact_boundary":
        state.update(hit=False, pending=[])
        return
    blocks = (row.get("message") or {}).get("content") if row.get("type") in ("assistant", "user") else None
    for block in blocks if isinstance(blocks, list) else ():
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            if block.get("name") in _READ_TOOLS:
                paths = tool_input.get("paths")
                if _names_entry(tool_input.get("file_path"), *(paths if isinstance(paths, list) else [paths])):
                    state["hit"] = True
            elif block.get("name") == _SEARCH_TOOL and block.get("id"):
                state["pending"] = (state["pending"] + [block["id"]])[-_PENDING_CAP:]
        elif block.get("type") == "tool_result" and block.get("tool_use_id") in state["pending"]:
            state["pending"].remove(block["tool_use_id"])
            if _names_entry(_result_text(block.get("content"))):
                state["hit"] = True


SCANNER = _transcript_scan.Scanner("memory_consulted", lambda: {"hit": False, "pending": []}, _feed,
                                   sources=(os.path.abspath(__file__),))


def consulted(transcript_path) -> bool:
    """True when the transcript shows an entry read or found since its latest compaction boundary."""
    if not transcript_path:
        return False
    try:
        return bool(_transcript_scan.scan(transcript_path, SCANNER)["hit"])
    except (OSError, ValueError, KeyError):
        return False


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    prompt = payload.get("prompt") or ""
    count = entry_count() if len(prompt.strip()) >= MIN_PROMPT_CHARS else 0
    if (not count or not fire_once_since_compaction(payload.get("session_id") or "", NUDGE_KEY)
            or consulted(payload.get("transcript_path"))):
        print("{}")
        return
    print("<user-prompt-submit-hook>\n"
          "This session has read no auto-memory entry (%d in .claude/auto-memory/). Before a task in an area the "
          "repo has history in, search it by concept (semantic-search with restrictToDir '.claude/auto-memory', "
          "else Grep) and read the entries that match.\n"
          "</user-prompt-submit-hook>" % count)


if __name__ == "__main__":
    main()
