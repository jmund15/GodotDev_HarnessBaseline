#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: block a whole-file Read of a reference that is served through an index.

Some harness references are scan-all-read-few: a reader must see every entry's trigger to know
which few bear, but needs the body of only a handful. Those files carry an ACCESSOR that emits a
selector index and fetches entries by id (`instruction_quality` §5, *the read pattern picks the
split axis*). Reading the file whole spends the full size to use a fraction of it.

Prose in the consuming command covers the paths it knows about. It cannot cover an agent that
found the file through semantic-search, a `Glob`, or a memory citation and reached for `Read`
next -- which is precisely how a per-entry contract erodes. This hook is that backstop.

NOT SUBAGENT-EXEMPT, unlike the two blocks in pre_read_dispatch.py. Those exist because a read
spends the ORCHESTRATOR's context permanently, a cost a discarded subagent context does not carry.
The cost here is different in kind: a fan-out lens reading 46KB spends 46KB of input tokens on
every lens, N times per dispatch, and the discard at the end recovers none of it. The
multiplication IS the defect, so the actor that multiplies it is the one that must be stopped.

Bounded reads pass. `Read(offset=..., limit=...)` means the agent has already scoped the read, and
editing an entry is a legitimate bounded read of these files.

Registry below is the whole configuration surface. To add a file: give its repo-relative suffix,
the command that replaces the read, and one clause on what the index gives back.

Output contract: returns a block message string (caller writes stderr + exit 2), or None.
Wired in: pre_read_dispatch.py step 0. Not registered in settings.json on its own -- one spawn per
matched call is the budget (`instruction_quality` §15).
"""

import os

# suffix -> (accessor command, what the accessor returns)
INDEXED_REFERENCES = {
    "reference/known_failure_modes_entries.md": (
        "python3 .claude/tools/kfm.py index",
        "a ~7KB selector index (one line per entry: id, name, trigger). Then "
        "`kfm.py get <ID> [<ID> ...]` for the entries whose trigger fires, or "
        "`kfm.py get -s <section>` for a whole domain. Contract: "
        ".claude/commands/checklists/known_failure_modes.md §Access",
    ),
    "commands/agents/plan_check_agents.md": (
        "python3 .claude/tools/lens.py get --shared <KEY> [<KEY> ...]",
        "only the lenses you dispatch. The two `meta`-only lenses are 11.4KB a "
        "`code` plan never runs, and plc-pattern-fit + plc-architecture-quality "
        "are 9KB a `meta` plan never runs. `lens.py index plan_check` lists key, "
        "model, gate and size.",
    ),
    "commands/agents/explore_agents.md": (
        "python3 .claude/tools/lens.py shared explore   # trigger table, no bodies",
        "the lens trigger table alone. Then `lens.py get <KEY> ...` for the "
        "lenses the table selected — a four-lens explore otherwise pays for four "
        "mandates it never dispatches.",
    ),
    "commands/agents/review_agents.md": (
        "python3 .claude/tools/lens.py get --shared <KEY> [<KEY> ...]",
        "the shared contract (spawn rules, scoping, context block, finding "
        "schema) plus only the aspects selected. `lens.py index review` lists "
        "the roster.",
    ),
    "generated/abstraction_families.md": (
        "grep the family rows you need, e.g. "
        "grep -n '<Concern>' .claude/generated/abstraction_families.md",
        "the matching rows only. The file is ~128KB of 341 families; a plan or "
        "design question needs the handful whose name or owning folder matches "
        "the domains in scope (plan_check.md §1g states this rule).",
    ),
}


def _normalize(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _is_bounded(tool_input) -> bool:
    return tool_input.get("offset") is not None or tool_input.get("limit") is not None


def process(input_data):
    """Return a block message for an unbounded whole-file Read of an index-served reference."""
    if input_data.get("tool_name") != "Read":
        return None

    tool_input = input_data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    if _is_bounded(tool_input):
        return None

    path = _normalize(tool_input.get("file_path"))
    if not path:
        return None

    for suffix, (command, gives) in INDEXED_REFERENCES.items():
        if not path.endswith(suffix):
            continue
        try:
            size_kb = os.path.getsize(tool_input["file_path"]) // 1024
            size = f"~{size_kb}KB"
        except OSError:
            size = "the whole file"
        return (
            f"BLOCKED: whole-file Read of {suffix} ({size}).\n"
            f"This reference is served through an index, not read end-to-end.\n"
            f"Run instead:  {command}\n"
            f"That returns {gives}\n"
            f"Editing an entry? Bound the read: Read(file_path=..., offset=N, limit=M)."
        )
    return None


def main() -> None:
    import json
    import sys

    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    msg = process(input_data)
    if msg:
        sys.stderr.write(msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
