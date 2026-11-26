#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the read/search tool family.

One settings entry runs these checks in order:
  indexed_reference_guard.process — indexed whole-file Read → advise the main session, block a delegate
  semantic_search_scope_guard.process — a scope its values settle → corrected via `updatedInput`;
     one that leaves the project → block
  file_size_preblock.process — an unbounded Read of a file at or over its ceiling → the head via `updatedInput`
  video_read_hint.advisory — a Read of a video or GIF → advisory naming the contact-sheet tool

Each check ships in the coding layer and runs only where its file exists. A delegate is not exempt from
either: per-agent input spend multiplies across a fan-out, and an out-of-root searchDir costs the same
index rebuild whoever calls it.

Output contract:
- A block writes stderr and exits 2.
- A rewrite emits `hookSpecificOutput.updatedInput` and exits 0, with its note as `additionalContext`.
- Each sub-hook self-gates on `tool_name`.
- A fault stays fail-open. A present member's import fault names the lost check and repair in
  `additionalContext`; an absent optional member is silent.

Wired in: settings.json hooks.PreToolUse with matcher
"Read|Grep|Glob|Write|WebFetch|WebSearch|mcp__plugin_semantic-search_semantic-search__search".
The sub-hooks keep their own `main()` for standalone proofs.
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


indexed_reference_guard, semantic_search_scope_guard, video_read_hint, file_size_preblock = _optional_hooks.load(
    'indexed_reference_guard', 'semantic_search_scope_guard', 'video_read_hint', 'file_size_preblock',
    on_error=_failed_import)



def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    # Index-served references: advise the main session, block a delegate (a discarded context recovers no input).
    index_advisory = None
    try:
        result = indexed_reference_guard.process(input_data) if indexed_reference_guard else None
        if result and result.get("block"):
            sys.stderr.write(str(result["block"]) + "\n")
            sys.exit(2)
        if result:
            index_advisory = result.get("advisory")
    except SystemExit:
        raise
    except Exception:
        pass

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

    # Large-file clamp: the two rewriting members act on different tools (Read versus search), so at most one fires.
    try:
        result = file_size_preblock.process(input_data) if file_size_preblock else None
        if result and not rewrite:
            rewrite, rewrite_note = result["rewrite"], result["note"]
    except Exception:
        pass

    video = None
    try:
        video = video_read_hint.advisory(input_data) if video_read_hint else None
    except Exception:
        pass

    faults = ['%s failed to import (%s); its checks do not run. Repair hooks/%s.py.' % (name, error, name)
              for name, error in _IMPORT_ERRORS.items()]
    context = "\n".join(c for c in (*faults, index_advisory, rewrite_note, video) if c)
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
