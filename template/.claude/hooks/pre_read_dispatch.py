#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

One settings entry runs one check:
  semantic_search_scope_guard.process — a scope its values settle → corrected via `updatedInput`;
     one that leaves the project → block

The check ships in the coding layer and runs only where its file exists.

Output contract:
- A block writes stderr and exits 2.
- A rewrite emits `hookSpecificOutput.updatedInput` and exits 0, with its note as `additionalContext`.
- The sub-hook self-gates on `tool_name`.
- A fault stays fail-open. A present member's import fault names the lost check and repair in
  `additionalContext`; an absent optional member is silent.

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|mcp__plugin_semantic-search_semantic-search__search".
The sub-hook keeps its own `main()` for standalone proofs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _optional_hooks

_IMPORT_ERRORS = {}


def _failed_import(name, exc):
    _IMPORT_ERRORS[name] = '%s: %s' % (type(exc).__name__, exc)
    return None


semantic_search_scope_guard = _optional_hooks.load('semantic_search_scope_guard', on_error=_failed_import)

# Hooks the v1 M1 cut unwired from this dispatcher. Their files stay until the cutover deletes them; naming them here
# keeps `tools/layer_closure.py`'s registration check seeing each as reached. Nothing loads or runs them.
UNWIRED = (
    "file_size_preblock",
    "indexed_reference_guard",
    "tool_routing_nudge",
    "video_read_hint",
)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Semantic-search scope guard: an out-of-root searchDir costs the same index rebuild whoever calls it.
    rewrite, rewrite_note = None, None
    try:
        result = semantic_search_scope_guard.process(input_data) if semantic_search_scope_guard else None
        if result and result.get("deny"):
            sys.stderr.write(str(result["deny"]) + "\n")
            sys.exit(2)
        if result and result.get("rewrite"):
            rewrite, rewrite_note = result["rewrite"], result.get("note")
    except SystemExit:
        raise
    except Exception:
        pass

    faults = ['%s failed to import (%s); its checks do not run. Repair hooks/%s.py.' % (name, error, name)
              for name, error in _IMPORT_ERRORS.items()]
    context = "\n".join(c for c in (*faults, rewrite_note) if c)
    if rewrite or context:
        output = {"hookEventName": "PreToolUse"}
        if rewrite:
            output["updatedInput"] = dict(rewrite)
        if context:
            output["additionalContext"] = context
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}))

    sys.exit(0)


if __name__ == "__main__":
    main()
