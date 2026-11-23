#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse dispatcher for the read/search tool family.

One settings entry runs CHAIN in one interpreter, then the project's own members named in
`adaptation.json` `hook_members.post_read`. CHAIN holds the shared member:
  - runaway_scan_reaper.process — throttled orphaned-search reaper, one line per reaped pid. It runs only for
    tools outside the standalone reaper entry's matcher (LSP and mcp__* are inside it): a tool that entry
    covers is reaped there, so this dispatcher reaping it again would run the scan twice per call.

Member contract: `process(payload)` returns `{"context": text}` or None.

Output contract:
- Member notes share one `hookSpecificOutput.additionalContext` payload and exit 0.
- One member fault stays fail-open and does not disable later members.

Wired in: settings.json hooks.PostToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|LSP|mcp__plugin_semantic-search_semantic-search__search|mcp__ai-worker__write_doc".
The members keep their own `main()` for standalone proofs.
"""

import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _optional_hooks  # noqa: E402

CHAIN = (("runaway_scan_reaper", "advisory"),)

# The matcher of the standalone runaway_scan_reaper entry (see the module docstring).
_STANDALONE_REAPER = re.compile(r"^(?:Agent|Workflow|TaskStop|TaskOutput|Skill|ToolSearch|LSP|NotebookEdit|mcp__.*)$")


def runs_reaper(tool_name):
    return not _STANDALONE_REAPER.match(tool_name or "")


def dispatch(input_data, chain=None):
    """Run the members over one payload. Returns the `additionalContext` text, '' when none has a note."""
    contexts = []
    for name, member in (chain if chain is not None else _optional_hooks.chain("post_read", CHAIN, "advisory")):
        if name == "runaway_scan_reaper" and not runs_reaper(input_data.get("tool_name")):
            continue
        try:
            process = member if callable(member) else importlib.import_module(name).process
            result = process(input_data) or {}
        except Exception:
            continue
        if result.get("context"):
            contexts.append(str(result["context"]))
    return "\n\n".join(contexts)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    text = dispatch(input_data)
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                            "additionalContext": text}}))
    sys.exit(0)


if __name__ == "__main__":
    main()
