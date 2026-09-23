#!/usr/bin/env python3
"""Hook: PreToolUse Write|Edit, run by pre_edit_dispatch.py — loaded guidance carries no provenance.

A line ADDED to a skill, command, rule or CLAUDE*.md that labels its origin — "(owner decision)",
"by user decision" — gives the agent no action, and the auto-mode classifier reads it as consent
the agent wrote for itself. The rule belongs in the file; its provenance belongs in the commit
message or auto-memory (`instruction_quality` §5). History homes stay open: failure_archaeology,
reference/, auto-memory and plans.

Contract: `process(payload)` returns {"block": message} or None (pre_edit_dispatch.py).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_scope import harness_tail  # noqa: E402

LABEL = re.compile(r"\((?:owner|user) decision|\bby (?:owner|user) decision", re.IGNORECASE)
_MENTION = re.compile(r'`[^`\n]*`|"[^"\n]*"')  # a quoted label is a rule describing it, not a label


def _labels(text):
    return LABEL.findall(_MENTION.sub(" ", text))
_LOADED_DIRS = ("skills/", "commands/", "rules/", "agents/")
_HISTORY = ("skills/failure_archaeology/",)


def _loaded_guidance(path):
    norm = path.replace("\\", "/")
    name = os.path.basename(norm)
    tail = harness_tail(norm)
    if tail is None:
        return name.startswith("CLAUDE") and name.endswith(".md")
    if tail.startswith(_HISTORY):
        return False
    return tail.startswith(_LOADED_DIRS) or ("/" not in tail and name.startswith("CLAUDE") and name.endswith(".md"))


def _before_after(payload):
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name") == "Edit":
        return tool_input.get("old_string") or "", tool_input.get("new_string") or ""
    try:
        with open(tool_input.get("file_path") or "", encoding="utf-8", errors="replace") as fh:
            before = fh.read()
    except OSError:
        before = ""
    return before, tool_input.get("content") or ""


def process(payload):
    if payload.get("tool_name") not in ("Write", "Edit"):
        return None
    if not _loaded_guidance((payload.get("tool_input") or {}).get("file_path") or ""):
        return None
    before, after = _before_after(payload)
    if len(_labels(after)) <= len(_labels(before)):
        return None
    hits = [ln.strip() for ln in after.splitlines() if _labels(ln) and ln not in before]
    hits = hits or [ln.strip() for ln in after.splitlines() if _labels(ln)]
    return {"block": "BLOCKED: loaded guidance states the rule, not its provenance: %r. Drop the "
                     "decision label; put who decided and when in the commit message or an auto-memory file."
                     % hits[0][:120]}


if __name__ == "__main__":
    try:
        result = process(json.load(sys.stdin))
    except (ValueError, OSError):
        sys.exit(0)
    if result:
        sys.stderr.write(result["block"] + "\n")
        sys.exit(2)
    sys.exit(0)
