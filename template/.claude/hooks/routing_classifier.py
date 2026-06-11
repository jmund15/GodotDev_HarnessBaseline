#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
routing_classifier.py — shared classification library for tool-routing hooks.

Why this exists:
- Three hooks (`tool_routing_nudge.py` PreToolUse, `tool_routing_post_grep.py`
  PostToolUse, `tool_routing_cumulative.py` PostToolUse) each had inlined
  copies of the same cue-word lists, PascalCase regex, file-family classifier,
  and cloud-session detector. This is the classic DRY-violation shape —
  the inline copies will silently diverge over time as one hook is updated
  and the others aren't.
- The new `routing_audit.py` hook (continuous silent-miss logger) needs a
  `classify_call()` API that combines all three rule families into a single
  classification per tool call. Building it forced the extraction.

Design contract:
- Pure module. No I/O, no `sys.exit`, no stderr writes. Hooks own their output
  channels (stderr for nudge.py, additionalContext JSON for post_grep.py /
  cumulative.py / routing_audit.py).
- Constants and helpers are public (no leading underscore). Hooks may
  `from routing_classifier import is_pascal_identifier, LITERAL_INTENT_CUES, ...`.
- The high-level `classify_call(tool, tool_input, last_prompt)` returns a
  `Classification` dataclass. Severity values:
    - COMPLIANT          : call followed §9; no rule applies → no nudge expected
    - NUDGE_WARRANTED    : call violated a clear rule (PascalCase Grep on
                           indexed file; synthesis-shaped Obsidian read; etc.)
                           Silent-miss if the existing nudge channel doesn't fire.
    - CUE_EXEMPT         : would warrant nudge BUT user prompt cue words
                           legitimize the override (K1 literal-intent, L6
                           verified-unique-name, audit-shape carve-out).
    - ADVISORY_APPLICABLE: soft-nudge category (memory_search, broad obsidian
                           search) — informational, not a violation.
    - NOT_ROUTABLE       : tool has no §9 routing rules (Bash, Edit, Write,
                           Glob, etc.) OR the call shape doesn't match the
                           rule (e.g. `Read` of a non-`.md` file — Read is
                           classified only when path is a `.md` synthesis
                           target). The cumulative hook handles cascade-
                           shape detection for these; this classifier does
                           not duplicate that logic.

  The audit hook logs only NUDGE_WARRANTED and CUE_EXEMPT. The other three are
  noise from the dashboard's perspective.

This module is the single home for these helpers and cue lists — the former
inline copies in tool_routing_nudge.py / tool_routing_post_grep.py /
tool_routing_cumulative.py were removed at extraction (2026-05-04).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal


# === Cue-word constants (extracted verbatim from existing hooks) =============

# K1 carve-out: user explicitly wanted literal text scan including comments,
# doc-comments, string-literal mentions. LSP would filter those out;
# semantic-search ranks by similarity, not exact-text occurrence. Grep is
# correct here despite the PascalCase shape. Source: tool_routing_nudge.py:77-87
# and tool_routing_post_grep.py:59-69 (now centralized here).
LITERAL_INTENT_CUES = (
    "literal",
    "verbatim",
    # Narrowed from bare "comment" (2026-06-09): substring matching made any
    # prompt mentioning "a comment" exempt the literal-scan rule.
    "including comments",
    "in comments",
    "comment scan",
    "string literal",
    "audit",
    "documentation audit",
    "every occurrence",
    "exact text",
    "raw text",
)

# L6 carve-out: user explicitly invoked the verified-unique-name override
# documented in csharp_lsp.md. Grep returns the same set as LSP for verified-
# unique names, so anchor-then-navigate is unnecessary overhead.
# Source: tool_routing_nudge.py:92-100.
VERIFIED_UNIQUE_CUES = (
    "verified-unique",
    "verified unique",
    "no overloads",
    "name is unique",
    "project-unique",
    "project unique",
    "no other class defines",
)

# Audit-shape carve-out (CLAUDE.md §9 Exception clause): line-precision direct
# reads are warranted only when user explicitly framed the task as audit/
# debug/security-review/fact-check at source-code level. Source: tool_routing_
# cumulative.py:80-97.
AUDIT_INTENT_CUES = (
    "audit",
    "code review",
    "security review",
    "debug this",
    "debugging this",
    "step through",
    "trace through",
    "fact-check",
    "fact check",
    "line by line",
    "line-by-line",
    "inspect the code",
    "inspect this file",
    "verify the implementation",
    "review for bugs",
    "review for issues",
)

# Path-fragment hints that an Obsidian read is a synthesis-shaped target
# (large doc, design/architecture/retrospective shape — better to route through
# read_files than load the full doc into context). Case-insensitive substring
# on the file path. Source: tool_routing_nudge.py:111-122.
SYNTHESIS_DOC_HINTS = (
    "Design",
    "Planning",
    "BrainstormingDesigns",
    "Documentation",
    "Brainstorm",
    "Architecture",
    "Retrospective",
    "Audit",
    "Review",
    "Postmortem",
)

# Bulk-search thresholds for obsidian_global_search. Source: tool_routing_nudge.py:125-126.
BULK_PAGESIZE_THRESHOLD = 5
BULK_CONTEXT_THRESHOLD = 100


# === Pattern matchers (extracted verbatim from tool_routing_nudge.py) ========

# PascalCase identifier with no regex metacharacters. Source: tool_routing_nudge.py:129-130.
_PASCAL_IDENT = re.compile(r"^[A-Z][A-Za-z0-9_]+$")
_REGEX_METACHARS = set(r".\^$|?*+()[]{}")


def is_pascal_identifier(pattern: str) -> bool:
    """True if pattern is a single PascalCase identifier with no regex meta.
    Verbatim from tool_routing_nudge.py:_is_pascal_identifier()."""
    if not pattern or not _PASCAL_IDENT.match(pattern):
        return False
    return not any(c in _REGEX_METACHARS for c in pattern)


def grep_target_family(tool_input: dict) -> str:
    """
    Classify the Grep target by file family for routing dispatch.

    Returns one of: "cs", "indexed-other", "mixed", "other".
      cs            — `.cs` only (use LSP)
      indexed-other — `.tscn` / `.tres` / `.gd` / `.md` / `.godot` / `.json` /
                      `.yaml` / `.toml` / `.txt` — semantic-search indexes these
      mixed         — unrestricted search (no glob, no type) — likely crosses
                      indexed file families; semantic-search broadly applicable
      other         — file family not indexed by semantic-search (rare)

    Verbatim from tool_routing_nudge.py:_grep_target_family().
    """
    glob = (tool_input.get("glob") or "").lower()
    type_ = (tool_input.get("type") or "").lower()

    if type_ == "cs" or "*.cs" in glob:
        return "cs"

    indexed_extensions = (
        "*.gd", "*.md", "*.tscn", "*.tres", "*.godot",
        "*.json", "*.yaml", "*.toml", "*.txt",
    )
    indexed_types = ("gd", "md", "godot", "json", "yaml", "toml", "txt")
    if type_ in indexed_types:
        return "indexed-other"
    if any(ext in glob for ext in indexed_extensions):
        return "indexed-other"
    if any(ext in glob for ext in ("tscn", "tres", "gd", "godot")) and "{" in glob:
        return "indexed-other"

    if not glob and not type_:
        return "mixed"

    return "other"


def is_cloud_session() -> bool:
    """Cloud sessions disable LSP. Verbatim from tool_routing_nudge.py:_is_cloud_session()."""
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() in ("1", "true", "yes")


# === Cue-word checkers (consolidating duplicate logic across hooks) ==========

def prompt_has_literal_intent(prompt: str) -> bool:
    """True if prompt contains any K1-style literal-scan cue word."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(cue in lowered for cue in LITERAL_INTENT_CUES)


def prompt_has_verified_unique_intent(prompt: str) -> bool:
    """True if prompt contains any L6-style verified-unique-name cue word."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(cue in lowered for cue in VERIFIED_UNIQUE_CUES)


def prompt_has_audit_intent(prompt: str) -> bool:
    """True if prompt contains any audit-shape exception cue word."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(cue in lowered for cue in AUDIT_INTENT_CUES)


def prompt_has_grep_override_cue(prompt: str) -> bool:
    """
    True if prompt contains EITHER literal-intent (K1) OR verified-unique (L6) cue.
    Combined check used by the Grep-on-PascalCase rule.
    Verbatim from tool_routing_nudge.py:_prompt_has_override_cue().
    """
    return prompt_has_literal_intent(prompt) or prompt_has_verified_unique_intent(prompt)


# === High-level classification (new — used by routing_audit.py) ==============

Severity = Literal[
    "compliant",
    "nudge-warranted",
    "cue-exempt",
    "advisory-applicable",
    "not-routable",
]


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single tool call against §9 routing rules.

    Fields:
      severity : See `Severity` literal — the routing-correctness bucket.
      rule     : Short identifier of the rule that applied (e.g.
                 "pascal-grep-on-cs"), or None if no rule matched.
      reason   : Human-readable one-line explanation suitable for the audit
                 log's `reason` field. None if no rule matched.
      tool     : Echo of the tool name (for audit log convenience).
    """
    severity: Severity
    rule: str | None
    reason: str | None
    tool: str


_NOT_ROUTABLE = Classification(
    severity="not-routable", rule=None, reason=None, tool="(unset)"
)


def classify_call(
    tool_name: str,
    tool_input: dict | None,
    last_prompt: str = "",
) -> Classification:
    """
    Classify a single tool call against the §9 routing rules.

    Inputs:
      tool_name   : The tool that was called (e.g. "Grep").
      tool_input  : The tool's argument dict (e.g. {"pattern": "X", "glob": "*.cs"}).
                    May be None or empty.
      last_prompt : The user's most recent prompt text (for cue-word checks).
                    May be empty — in that case cue exemptions don't fire.

    Returns:
      Classification dataclass.

    Side effects: none.
    """
    if not tool_name:
        return Classification("not-routable", None, None, tool_name or "(unset)")

    tool_input = tool_input or {}

    # Grep — the highest-confidence routing rule.
    if tool_name == "Grep":
        return _classify_grep(tool_input, last_prompt)

    # Native Read of synthesis-shaped `.md` path.
    if tool_name == "Read":
        return _classify_native_read(tool_input, last_prompt)

    # Obsidian read of synthesis-shaped doc.
    if tool_name == "mcp__obsidian__obsidian_read_note":
        return _classify_obsidian_read(tool_input, last_prompt)

    # Obsidian broad search.
    if tool_name == "mcp__obsidian__obsidian_global_search":
        return _classify_obsidian_search(tool_input, last_prompt)

    # Tools without §9 routing rules.
    return Classification("not-routable", None, None, tool_name)


def _classify_grep(tool_input: dict, last_prompt: str) -> Classification:
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        # Literal/regex/UID/attribute Grep — §9 carves these out as legitimate.
        return Classification("compliant", None, None, "Grep")

    family = grep_target_family(tool_input)
    if family == "other":
        # Not an indexed family — Grep is fine.
        return Classification("compliant", None, None, "Grep")

    # PascalCase + indexed family = the bypass smell.
    if family == "cs":
        # Check K1 / L6 cue-word exemptions first.
        if prompt_has_grep_override_cue(last_prompt):
            return Classification(
                severity="cue-exempt",
                rule="pascal-grep-on-cs",
                reason=(
                    "PascalCase Grep on .cs would normally route to anchor-then-navigate, "
                    "but user prompt invokes K1 literal-intent or L6 verified-unique override"
                ),
                tool="Grep",
            )
        return Classification(
            severity="nudge-warranted",
            rule="pascal-grep-on-cs",
            reason="bare PascalCase Grep on .cs bypasses LSP anchor-then-navigate (§9)",
            tool="Grep",
        )

    # indexed-other or mixed — semantic-search is the right tool.
    if prompt_has_literal_intent(last_prompt):
        # K1 also applies for non-.cs indexed files (literal scan in .tres).
        return Classification(
            severity="cue-exempt",
            rule="pascal-grep-on-indexed",
            reason=(
                "PascalCase Grep on indexed file would route to semantic-search, "
                "but user prompt invokes literal-intent override"
            ),
            tool="Grep",
        )
    return Classification(
        severity="nudge-warranted",
        rule="pascal-grep-on-indexed",
        reason=(
            "PascalCase Grep on indexed-other family bypasses semantic-search (§9 — "
            ".tscn/.tres/.gd/.md/etc. are indexed)"
        ),
        tool="Grep",
    )


def _classify_native_read(tool_input: dict, last_prompt: str) -> Classification:
    """Native `Read` of a `.md` file under a synthesis-shaped path. Mirrors
    `_classify_obsidian_read` but consumes the snake_case `file_path` arg
    used by the native Read tool. Restricted to `.md` to avoid flagging
    .cs/.tres at synthesis-named folders — those have their own §9 rules."""
    path = tool_input.get("file_path") or ""
    if not path:
        return Classification("not-routable", None, None, "Read")
    if not path.lower().endswith(".md"):
        return Classification("not-routable", None, None, "Read")
    is_synthesis_shape = any(hint.lower() in path.lower() for hint in SYNTHESIS_DOC_HINTS)
    if not is_synthesis_shape:
        # Surgical read of a non-synthesis-shaped .md (e.g. CLAUDE.md, README) — fine.
        return Classification("compliant", None, None, "Read")
    # Audit-shape carve-out — mirrors `_classify_obsidian_read`.
    if prompt_has_audit_intent(last_prompt):
        return Classification(
            severity="cue-exempt",
            rule="native-read-synthesis-doc",
            reason=(
                "synthesis-shaped path on native Read would route to read_files, "
                "but user prompt invokes audit-shape direct-read carve-out"
            ),
            tool="Read",
        )
    return Classification(
        severity="nudge-warranted",
        rule="native-read-synthesis-doc",
        reason=(
            f"native Read of synthesis-shaped path ({path}) — "
            "should route through mcp__ai-worker__read_files for digest"
        ),
        tool="Read",
    )


def _classify_obsidian_read(tool_input: dict, last_prompt: str) -> Classification:
    path = tool_input.get("filePath") or ""
    if not path:
        return Classification("compliant", None, None, "mcp__obsidian__obsidian_read_note")
    is_synthesis_shape = any(hint.lower() in path.lower() for hint in SYNTHESIS_DOC_HINTS)
    if not is_synthesis_shape:
        # Surgical read of a non-synthesis-shaped doc (e.g. Worklog) — fine.
        return Classification("compliant", None, None, "mcp__obsidian__obsidian_read_note")

    # Audit-shape carve-out: explicit line-precision framing legitimizes
    # direct read over read_files bundling.
    if prompt_has_audit_intent(last_prompt):
        return Classification(
            severity="cue-exempt",
            rule="obsidian-synthesis-doc-direct-read",
            reason=(
                "synthesis-shaped Obsidian doc would route to read_files, "
                "but user prompt invokes audit-shape direct-read carve-out"
            ),
            tool="mcp__obsidian__obsidian_read_note",
        )
    return Classification(
        severity="nudge-warranted",
        rule="obsidian-synthesis-doc-direct-read",
        reason=(
            f"direct read of synthesis-shaped Obsidian doc ({path}) — "
            "should route through mcp__ai-worker__read_files for digest"
        ),
        tool="mcp__obsidian__obsidian_read_note",
    )


def _classify_obsidian_search(tool_input: dict, last_prompt: str) -> Classification:
    page_size = tool_input.get("pageSize")
    context_length = tool_input.get("contextLength")
    is_bulk = (
        page_size is None
        or (isinstance(page_size, (int, float)) and page_size > BULK_PAGESIZE_THRESHOLD)
        or (isinstance(context_length, (int, float)) and context_length > BULK_CONTEXT_THRESHOLD)
    )
    if not is_bulk:
        # Targeted search — fine.
        return Classification(
            "compliant", None, None, "mcp__obsidian__obsidian_global_search"
        )

    # Broad searches are advisory (the existing nudge is informational, not
    # a "violation"). Don't classify as nudge-warranted; this would inflate the
    # silent-miss count for routine broad searches that may be the right move.
    return Classification(
        severity="advisory-applicable",
        rule="obsidian-broad-search-soft-nudge",
        reason="broad obsidian_global_search may be better as bundled read_files",
        tool="mcp__obsidian__obsidian_global_search",
    )
