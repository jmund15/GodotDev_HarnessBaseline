#!/usr/bin/env python3
"""Hook: PreToolUse Write|Edit, run by pre_edit_dispatch.py — block a direct edit of a composed file.

`baseline_sync.py compose` regenerates each composed file from its inputs, so a direct edit is
silently dropped by the next compose (a settings.json permission edit, 2026-09-22). The block
names the inputs to edit instead. The composed set has one home: `WATCH_COMPOSED_INPUTS` in
`.claude/tools/baseline_sync.py`.

Contract: `process(payload)` returns {"block": message} or None (pre_edit_dispatch.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_scope import harness_tail  # noqa: E402

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")


# Basenames of the composed files, checked before importing baseline_sync on every edit. A proof
# pins this set to WATCH_COMPOSED_INPUTS, so the composed set keeps one home.
COMPOSED_NAMES = {"settings.json", "memory_domains.md"}


def _composed_inputs():
    sys.path.insert(0, _TOOLS)
    from baseline_sync import WATCH_COMPOSED_INPUTS
    return {rel[len(".claude/"):]: inputs for rel, inputs in WATCH_COMPOSED_INPUTS.items()}


def process(payload):
    if payload.get("tool_name") not in ("Write", "Edit"):
        return None
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    tail = harness_tail(path.replace("\\", "/"))
    if not tail or os.path.basename(tail) not in COMPOSED_NAMES:
        return None
    inputs = _composed_inputs().get(tail)
    if not inputs:
        return None
    return {"block": "BLOCKED: .claude/%s is composed; `compose` overwrites a direct edit. Edit %s "
                     "(a project override goes in the local input; `$disable` removes a base entry), then "
                     "run `python3 .claude/tools/baseline_sync.py compose`." % (tail, " or ".join(inputs))}


if __name__ == "__main__":
    try:
        result = process(json.load(sys.stdin))
    except (ValueError, OSError):
        sys.exit(0)
    if result:
        sys.stderr.write(result["block"] + "\n")
        sys.exit(2)
    sys.exit(0)
