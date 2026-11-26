#!/usr/bin/env python3
"""Sub-hook of post_edit_dispatch.py: what a scratch brief's `task` block tells its author at the write, before dispatch.

Class: advisory. It never denies. A Write or Edit of a file under `.claude/scratch/` that leaves a ```task block gets
up to two context lines:
- a malformed block (`dispatch_result.task_block` names the defect), with `[task-brief-facts]` and a fix-it ask, once
  per brief and defect since the session started or last compacted;
- the dispatch-prep notice (`model_ladder_gate.advance_notice`, the earlier surface of the doctrine deny): the loads a
  launch is denied for while they are not in context, once per window.
A well-formed block adds no malformed line; the dispatch-prep notice does not depend on the block. A write with no
```task fence adds nothing.

Not here: a table of rows claiming the block's tier, with their price, plan room and window fit. That is delegation-plugin
machinery (model routing, `provider_bands.tier_table`); the same facts reach the pick through `model_registry.py for-role`
(which the notice names) and the launcher's `dispatch_size.fit_lines`. Last carried by this hook at commit e6f2dd72.

Contract: `process(payload) -> {"context": text} | None`.
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
TAG = "[task-brief-facts]"
FENCE = "```task"
MALFORMED_ONCE = "task_brief_facts:malformed:"


def _malformed(session_id, path, text):
    """The fix-it line for a malformed `task` block, empty when the block is sound or the session was already told."""
    import _hook_state
    import dispatch_result
    try:
        dispatch_result.task_block(text)
    except ValueError as exc:
        key = MALFORMED_ONCE + hashlib.sha1(("%s\n%s" % (path, exc)).encode("utf-8", "replace")).hexdigest()[:16]
        if _hook_state.fire_once_since_compaction(session_id, key):
            return "%s %s; fix it before dispatch." % (TAG, exc)
    return ""


def process(payload):
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("tool_name") not in ("Write", "Edit"):
        return None
    path = (payload.get("tool_input") or {}).get("file_path")
    if not isinstance(path, str) or "/.claude/scratch/" not in path.replace("\\", "/"):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    if FENCE not in text:
        return None
    for where in (TOOLS, HERE):
        if where not in sys.path:
            sys.path.insert(0, where)
    import model_ladder_gate
    lines = [line for line in (_malformed(payload.get("session_id") or "default", path, text),
                               model_ladder_gate.advance_notice(payload)) if line]
    return {"context": "\n".join(lines)} if lines else None
