#!/usr/bin/env python3
"""
Library for harness markdown edits; it emits nothing and is not wired as a hook.

Callers: `self_eval_archive_guard.py` (`pre_edit_text`, `added_lines`), `_owner_text.py`'s turn rule
(`_starts_turn`, `_latest_turn_key`, proved by `test_owner_text.py`) and `git_guardrails.py`'s
commit-time density advisory (`UNIT`, `EXCLUDED`, `DENSITY_AUDIT`, so there is one definition of a
rule-unit).

The per-edit `[harness-growth]` report was removed: a note on a call a hook already admits arrives after
the decision (`instruction_quality` §3). Its measures live later: `triage.py sentences (--staged | --rev R)`
lists the added and removed doctrine sentences before the kind call, `git_guardrails.py` advises a commit
whose staged `.md` adds a rule-unit over `DENSITY_AUDIT`, and `load_census.py` `over_baseline`
feeds the close's hot-set review.
"""

import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _owner_text import classify

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import sentence_diff

MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024

DENSITY_AUDIT = 350      # B per rule-unit; ideal ≤ ~300
EXCLUDED = sentence_diff.EXCLUDED
UNIT = re.compile(r"^\s*(?:[-*] |\d+\. |\|(?!\s*-)|\*\*[^*]+\*\*)")


def pre_edit_text(data, text):
    """Recover the immediate preimage from the client's result, not from HEAD."""
    response = data.get("tool_response") or {}
    original = response.get("originalFile")
    if isinstance(original, str):
        return original
    if response.get("type") == "create":
        return ""
    hunks = response.get("structuredPatch")
    if isinstance(hunks, list) and hunks:
        lines = text.splitlines()
        for hunk in reversed(hunks):
            patch = hunk.get("lines") or []
            new = [line[1:] for line in patch if line.startswith((" ", "+"))]
            old = [line[1:] for line in patch if line.startswith((" ", "-"))]
            start = max(0, hunk["newStart"] - 1)
            if lines[start:start + len(new)] != new:
                return None
            lines[start:start + len(new)] = old
        return "\n".join(lines) + ("\n" if text.endswith("\n") and lines else "")
    if data.get("tool_name") == "Edit":
        inp = data.get("tool_input") or {}
        old, new = inp.get("old_string"), inp.get("new_string")
        if isinstance(old, str) and isinstance(new, str) and new:
            if inp.get("replace_all") or text.count(new) == 1:
                return text.replace(new, old, -1 if inp.get("replace_all") else 1)
    return None


def added_lines(before, text):
    """Net-new occurrences, so a rewrite that retains a bad old line does not report it."""
    remaining = collections.Counter(before.splitlines())
    added = []
    for line in text.splitlines():
        if remaining[line]:
            remaining[line] -= 1
        else:
            added.append(line)
    return added


def _latest_turn_key(transcript_path):
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size > MAX_TRANSCRIPT_BYTES:
                fh.seek(size - MAX_TRANSCRIPT_BYTES)
            content = fh.read()
        if size > MAX_TRANSCRIPT_BYTES and b"\n" in content:
            content = content.split(b"\n", 1)[1]
    except OSError:
        return None

    lines = content.splitlines()
    for number in range(len(lines), 0, -1):
        raw_line = lines[number - 1]
        try:
            entry = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        row = classify(entry, number, raw=raw_line)
        if _starts_turn(row):
            return row.uuid
    return None


def _starts_turn(row):
    """An owner prompt or a command with arguments; the `_owner_text` table's harness_growth_guard column."""
    return bool(row is not None and not row.meta and not row.sidechain and not row.tool_results
                and row.text and row.envelope is None
                and row.kind != "queued_prompt"
                and not (row.kind == "command" and not row.command_args))
