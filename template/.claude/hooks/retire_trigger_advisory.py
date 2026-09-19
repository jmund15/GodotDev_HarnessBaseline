#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PostToolUse (Write) — advises when a new memory or rules file lands with no retirement trigger.

Scope, outside `tools/rule_retirement.SKIP_DIRS`:
- `.claude/auto-memory/**/*.md` needs a frontmatter `retire_when:` trigger;
- `.claude/rules/**/*.md` needs a `<!-- retire-when: ... -->` comment, because rules files load by
  path and `rule_retirement.review_due` reviews them.
Both forms are `/codify` Step 6 (`commands/codify.md:93`). Commands and skills load on demand and
are out of scope here.

Fires only on a Write that creates a file: the PostToolUse payload's `tool_response.type` is
`create`. An update Write, or a missing or unknown `tool_response`, is silent, and so is the
`MEMORY.md` index, which is not a memory file.

Fires once per in-scope create: silent when the file's frontmatter declares a well-formed
trigger, one line naming the gap when it declares none, and one line naming the malformed
row when `rule_retirement.frontmatter_triggers` reports one (unclosed frontmatter, or
`retire_when:` declared with no list items). Never evaluates whether a trigger STRING
matches a known kind — that judgment belongs to `rule_retirement.evaluate`, run later by
`/eval_dashboard` §4e and `/autolearn`. Never blocks; fail-open on any error.

Wired in: settings.json PostToolUse "Write|Edit", via post_edit_dispatch.CHAIN.
"""

import json
import os
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_HOOKS_DIR, "..", "tools"))
sys.path.insert(0, _TOOLS_DIR)
import rule_retirement  # noqa: E402


def _rel_claude_path(file_path, root):
    """Repo-relative POSIX path starting with `.claude/`, or None outside the tree."""
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, file_path))
    try:
        if os.path.normcase(os.path.commonpath((root, path))) != os.path.normcase(root):
            return None
        rel = os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return None
    return rel if rel.startswith(".claude/") else None


def in_memory_scope(claude_rel):
    """`claude_rel` is relative to `.claude/` (no leading `.claude/`). True inside
    `auto-memory/`, matching the portion of `rule_retirement.scan`'s walk that is never
    pruned by its SKIP_DIRS."""
    parts = claude_rel.split("/")
    if (len(parts) < 2 or parts[0] != "auto-memory" or not parts[-1].endswith(".md")
            or parts[-1] == "MEMORY.md"):
        return False
    return not any(seg in rule_retirement.SKIP_DIRS for seg in parts[1:-1])


def in_rules_scope(claude_rel):
    """`claude_rel` is relative to `.claude/`. True for a `.md` file anywhere under `rules/`."""
    parts = claude_rel.split("/")
    if len(parts) < 2 or parts[0] != "rules" or not parts[-1].endswith(".md"):
        return False
    return not any(seg in rule_retirement.SKIP_DIRS for seg in parts[1:-1])


def process(data):
    """Dispatcher entry: `{"context": <one line>}` on a missing/malformed trigger, else None."""
    if data.get("tool_name") != "Write":
        return None
    response = data.get("tool_response")
    if not isinstance(response, dict) or response.get("type") != "create":
        return None
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    content = tool_input.get("content")
    if not file_path or not isinstance(content, str):
        return None
    root = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    rel = _rel_claude_path(file_path, root)
    if not rel:
        return None
    claude_rel = rel[len(".claude/"):]
    if in_rules_scope(claude_rel):
        if rule_retirement.COMMENT.findall(rule_retirement.prose_only(content)):
            return None
        return {"context": "[retire-trigger-advisory] %s: no `<!-- retire-when: ... -->` comment declared "
                           "(codify.md Step 6)." % rel}
    if not in_memory_scope(claude_rel):
        return None

    malformed = []
    triggers = rule_retirement.frontmatter_triggers(content, claude_rel, malformed)
    if malformed:
        return {"context": "[retire-trigger-advisory] %s: %s (codify.md Step 6)."
                           % (rel, malformed[0]["problem"])}
    if not triggers:
        return {"context": "[retire-trigger-advisory] %s: no retire_when trigger declared "
                           "(codify.md Step 6)." % rel}
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    result = process(data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": result["context"]}}))


if __name__ == "__main__":
    main()
