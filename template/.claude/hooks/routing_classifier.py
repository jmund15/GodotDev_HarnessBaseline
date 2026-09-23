#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
routing_classifier.py — shared classification library for tool-routing hooks.

Why this exists:
- The PreToolUse and PostToolUse routing hooks had inlined copies of the same cue-word
  lists, PascalCase regex, file-family classifier, and cloud-session detector.
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
    - COMPLIANT          : call followed §Tool Routing; no rule applies → no nudge expected
    - NUDGE_WARRANTED    : call violated a clear rule (PascalCase Grep on
                           indexed file; synthesis-shaped Obsidian read; etc.)
                           Silent-miss if the existing nudge channel doesn't fire.
    - CUE_EXEMPT         : would warrant nudge BUT user prompt cue words
                           legitimize the override (K1 literal-intent, L6
                           verified-unique-name, audit-shape carve-out).
    - ADVISORY_APPLICABLE: soft-nudge category (memory_search, broad obsidian
                           search) — informational, not a violation.
    - NOT_ROUTABLE       : tool has no §Tool Routing routing rules (Bash, Edit, Write,
                           Glob, etc.) OR the call shape doesn't match a rule.

  The audit hook logs NUDGE_WARRANTED, CUE_EXEMPT and CENSUS (vault doc writes, direct
  vs worker — measured, never judged). The other three are noise from the dashboard's
  perspective.

This module is the single home for these helpers and cue lists.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

import _optional_hooks
from _claude_scope import harness_tail

# Version-pinned documentation sources a WebFetch should not bypass, one module per adopted layer.
SOURCE_PINS = tuple(m for m in (_optional_hooks.load("godot_source_pins"),) if m)


# === Cue-word constants =====================================================

# K1 carve-out: user explicitly wanted literal text scan including comments,
# doc-comments, string-literal mentions. LSP would filter those out;
# semantic-search ranks by similarity, not exact-text occurrence.
# Grep remains correct for literal text even when the pattern is PascalCase.
LITERAL_INTENT_CUES = (
    "literal",
    "verbatim",
    # Keep comment cues qualified; a bare "comment" matches ordinary prose.
    "including comments",
    "in comments",
    "comment scan",
    "string literal",
    # Bare "audit" describes review shape, not a literal scan.
    "documentation audit",
    "every occurrence",
    "exact text",
    "raw text",
)

# L6 carve-out: verified-unique names return the same set through Grep as through LSP.
VERIFIED_UNIQUE_CUES = (
    "verified-unique",
    "verified unique",
    "no overloads",
    "name is unique",
    "project-unique",
    "project unique",
    "no other class defines",
)

# Audit-shape carve-out (CLAUDE.md §Tool Routing Exception clause): line-precision direct
# reads are warranted only when user explicitly framed the task as audit/
# debug/security-review/fact-check at source-code level.
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

# High-precision prompt evidence for the worker's copyable bulk-I/O lane. A path,
# extension, or read count is never enough. The paired cue groups accept plain
# task wording while avoiding derived requests such as "compare and recommend".
BULK_SCOPE_CUES = (
    "every file",
    "each file",
    "all files",
    "multiple files",
    "these files",
    "input path",
    "each path",
    "every path",
    "each source",
    "every source",
)
COPYABLE_OUTPUT_CUES = (
    "bulk copyable",
    "bulk-copyable",
    "copyable",
    "raw field",
    "raw data",
    "one entry per",
    "one row per",
    "extract",
)

# Bulk-search thresholds for obsidian_search_notes.
BULK_CONTEXT_THRESHOLD = 100
BULK_MAXMATCHES_THRESHOLD = 5


# === Pattern matchers ========================================================

# PascalCase identifier with no regex metacharacters.
_PASCAL_IDENT = re.compile(r"^[A-Z][A-Za-z0-9_]+$")
_REGEX_METACHARS = set(r".\^$|?*+()[]{}")


def is_pascal_identifier(pattern: str) -> bool:
    """True if pattern is a single PascalCase identifier with no regex meta.
    """
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
    """Cloud sessions disable LSP."""
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


def is_harness_path(path: str) -> bool:
    """True if the canonical path is an agent-runtime instruction surface."""
    return harness_tail(path) is not None


def is_bounded_read(tool_input: dict) -> bool:
    """True if the Read call is windowed (`offset`/`limit`) — surgical by
    construction, so the whole-doc digest advisory does not apply."""
    return tool_input.get("offset") is not None or tool_input.get("limit") is not None


def prompt_requests_bulk_copyable(prompt: str) -> bool:
    """True only when the request names BOTH a multi-input scope and a copyable output shape.
    A path, extension or read count is never evidence (CLAUDE.md §Tool Routing)."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return (any(cue in lowered for cue in BULK_SCOPE_CUES)
            and any(cue in lowered for cue in COPYABLE_OUTPUT_CUES))


def prompt_has_grep_override_cue(prompt: str) -> bool:
    """
    True if prompt contains EITHER literal-intent (K1) OR verified-unique (L6) cue.
    Combined check used by the Grep-on-PascalCase rule.
    """
    return prompt_has_literal_intent(prompt) or prompt_has_verified_unique_intent(prompt)


# === High-level classification (new — used by routing_audit.py) ==============

Severity = Literal[
    "compliant",
    "nudge-warranted",
    "cue-exempt",
    "advisory-applicable",
    "not-routable",
    "census",  # logged for measurement, never a verdict (vault doc writes)
]


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single tool call against §Tool Routing routing rules.

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
    agent_id: str = "",
) -> Classification:
    """
    Classify a single tool call against the §Tool Routing routing rules.

    Inputs:
      tool_name   : The tool that was called (e.g. "Grep").
      tool_input  : The tool's argument dict (e.g. {"pattern": "X", "glob": "*.cs"}).
                    May be None or empty.
      last_prompt : The user's most recent prompt text (for cue-word checks).
                    May be empty — in that case cue exemptions don't fire.
      agent_id    : Non-empty for a dispatched subagent. A subagent handed the bulk inputs
                    IS the bundling delegate, so the native-read rule exempts it here — the
                    one home both the nudge and the audit log read.

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

    # Native Read while the request asks for bulk copyable I/O.
    if tool_name == "Read":
        return _classify_native_read(tool_input, last_prompt, agent_id)

    # Obsidian read under the same prompt-evidence rule.
    if tool_name == "mcp__obsidian__obsidian_get_note":
        return _classify_obsidian_read(tool_input, last_prompt)

    # Obsidian broad search.
    if tool_name == "mcp__obsidian__obsidian_search_notes":
        return _classify_obsidian_search(tool_input, last_prompt)

    # Web transport — deterministic tiers rank ahead of the paid/lossy ones.
    if tool_name == "WebFetch":
        return _classify_webfetch(tool_input, last_prompt)

    if tool_name == "WebSearch":
        return _classify_websearch(tool_input, last_prompt)

    if tool_name == "mcp__ai-worker__read_web":
        return _classify_read_web(tool_input, last_prompt)

    # Vault doc writes — a census, never a verdict. Judgment-dense vs templated is the model's
    # call (CLAUDE.md §3 Obsidian); the audit only measures the direct/worker split, so a model
    # that silently skips `write_doc` shows up in the log instead of in a manual transcript count.
    if tool_name in ("Write", "mcp__ai-worker__write_doc"):
        return _classify_vault_write(tool_name, tool_input)

    # Tools without §Tool Routing routing rules.
    return Classification("not-routable", None, None, tool_name)


def _classify_grep(tool_input: dict, last_prompt: str) -> Classification:
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        # Literal/regex/UID/attribute Grep — §Tool Routing carves these out as legitimate.
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
            reason="bare PascalCase Grep on .cs bypasses LSP anchor-then-navigate (§Tool Routing)",
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
            "PascalCase Grep on indexed-other family bypasses semantic-search (§Tool Routing — "
            ".tscn/.tres/.gd/.md/etc. are indexed)"
        ),
        tool="Grep",
    )


def _classify_native_read(tool_input: dict, last_prompt: str, agent_id: str = "") -> Classification:
    """Native `Read` while the request asks for bulk copyable extraction across inputs.

    The path or file type alone never classifies: focused evidence and derived judgment stay direct.
    """
    path = tool_input.get("file_path") or ""
    if not path:
        return Classification("not-routable", None, None, "Read")
    # Agent-runtime instructions and windowed reads are direct by contract.
    if is_harness_path(path) or is_bounded_read(tool_input):
        return Classification("not-routable", None, None, "Read")
    if not prompt_requests_bulk_copyable(last_prompt):
        return Classification("compliant", None, None, "Read")
    if prompt_has_audit_intent(last_prompt):
        return Classification(
            severity="cue-exempt",
            rule="native-read-bulk-copyable",
            reason=(
                "bulk-copyable request on native Read would route to read_files, "
                "but the prompt also invokes the audit-shape carve-out"
            ),
            tool="Read",
        )
    if agent_id:
        return Classification(
            severity="cue-exempt",
            rule="native-read-bulk-copyable",
            reason=(
                f"native Read of {path} for a bulk-copyable request by a dispatched subagent "
                "[subagent: bundling delegate]"
            ),
            tool="Read",
        )
    return Classification(
        severity="nudge-warranted",
        rule="native-read-bulk-copyable",
        reason=(
            f"native Read of {path} for an explicit bulk-copyable request — "
            "route the extraction through mcp__ai-worker__read_files"
        ),
        tool="Read",
    )


# A vault path is one under the Obsidian vault root (CLAUDE.md §3); doc-sized means a write
# the Documentation Delegation Rule would have routed, not a frontmatter or one-line touch-up.
VAULT_PATH_MARKER = "/obsidianvault/"
VAULT_WRITE_MIN_CHARS = 3000


def is_vault_path(path: str) -> bool:
    return VAULT_PATH_MARKER in (path or "").replace("\\", "/").lower()


def _classify_vault_write(tool_name: str, tool_input: dict) -> Classification:
    """`Write` or `write_doc` landing a doc-sized `.md` in the vault. Both are logged at the
    `census` tier so the dashboard shows the direct/worker split per session; neither is a miss."""
    if tool_name == "mcp__ai-worker__write_doc":
        path = tool_input.get("doc_path") or ""
        if not is_vault_path(path):
            return Classification("not-routable", None, None, tool_name)
        return Classification("census", "vault-write-worker",
                              f"write_doc ({tool_input.get('doc_type') or 'design'}) -> {path}", tool_name)
    path = tool_input.get("file_path") or ""
    if not is_vault_path(path) or not path.lower().endswith(".md"):
        return Classification("not-routable", None, None, tool_name)
    size = len(tool_input.get("content") or "")
    if size < VAULT_WRITE_MIN_CHARS:
        return Classification("not-routable", None, None, tool_name)
    return Classification("census", "vault-write-direct",
                          f"direct Write of {size} chars -> {path}", tool_name)


def _classify_obsidian_read(tool_input: dict, last_prompt: str) -> Classification:
    target = tool_input.get("target") or {}
    path = (target.get("path") if isinstance(target, dict) else "") or ""
    if not path:
        return Classification("compliant", None, None, "mcp__obsidian__obsidian_get_note")
    if not prompt_requests_bulk_copyable(last_prompt):
        return Classification("compliant", None, None, "mcp__obsidian__obsidian_get_note")
    if prompt_has_audit_intent(last_prompt):
        return Classification(
            severity="cue-exempt",
            rule="obsidian-read-bulk-copyable",
            reason=(
                "bulk-copyable request on an Obsidian read would route to read_files, "
                "but the prompt also invokes the audit-shape carve-out"
            ),
            tool="mcp__obsidian__obsidian_get_note",
        )
    return Classification(
        severity="nudge-warranted",
        rule="obsidian-read-bulk-copyable",
        reason=(
            f"direct Obsidian read of {path} for an explicit bulk-copyable request — "
            "route the extraction through mcp__ai-worker__read_files"
        ),
        tool="mcp__obsidian__obsidian_get_note",
    )


def _classify_webfetch(tool_input: dict, last_prompt: str) -> Classification:
    """A single-URL WebFetch is the direct, correct call — the only per-call
    violation is aiming it at a URL a version-pinned source already answers.
    Each adopted source-pin module (SOURCE_PINS) names its URLs and returns
    `(rule, reason)` for one. Multi-call patterns are outside this per-call
    classifier."""
    url = str(tool_input.get("url") or "")
    for pins in SOURCE_PINS:
        hit = pins.classify_webfetch(url)
        if hit:
            return Classification("nudge-warranted", hit[0], hit[1], "WebFetch")
    return Classification("compliant", None, None, "WebFetch")


def _classify_websearch(tool_input: dict, last_prompt: str) -> Classification:
    """Last tier in the web order — advisory, because "the docs are silent" is a
    judgment this classifier cannot make from the call shape alone."""
    return Classification(
        severity="advisory-applicable",
        rule="websearch-last-web-tier",
        reason=(
            "WebSearch is the last web tier — the godot-docs cache, fetch_source.sh, "
            "single-URL WebFetch and context7 own first-party answers (reference/source_trust.md)"
        ),
        tool="WebSearch",
    )


def _classify_read_web(tool_input: dict, last_prompt: str) -> Classification:
    """read_web is the multi-page SYNTHESIS tier, not the 3+-URL reflex: it spends
    real dollars and its extractor silently truncates (measured: 101,558 bytes ->
    71,478 chars extracted), so a quote taken from it is unauditable."""
    urls = tool_input.get("urls")
    count = len(urls) if isinstance(urls, list) else 0
    if count >= 3:
        return Classification("compliant", None, None, "mcp__ai-worker__read_web")
    return Classification(
        severity="advisory-applicable",
        rule="read-web-below-synthesis-floor",
        reason=(
            f"read_web on {count} url(s) — below the multi-page synthesis floor, and it "
            "spends dollars while silently truncating; a single URL goes to WebFetch, "
            "quote-bearing bytes to .claude/scripts/fetch_source.sh"
        ),
        tool="mcp__ai-worker__read_web",
    )


def _classify_obsidian_search(tool_input: dict, last_prompt: str) -> Classification:
    max_matches = tool_input.get("maxMatchesPerHit")
    context_length = tool_input.get("contextLength")
    is_bulk = (
        max_matches is None
        or (isinstance(max_matches, (int, float)) and max_matches > BULK_MAXMATCHES_THRESHOLD)
        or (isinstance(context_length, (int, float)) and context_length > BULK_CONTEXT_THRESHOLD)
    )
    if not is_bulk:
        # Targeted search — fine.
        return Classification(
            "compliant", None, None, "mcp__obsidian__obsidian_search_notes"
        )

    # Broad searches are advisory (the existing nudge is informational, not
    # a "violation"). Don't classify as nudge-warranted; this would inflate the
    # silent-miss count for routine broad searches that may be the right move.
    return Classification(
        severity="advisory-applicable",
        rule="obsidian-broad-search-soft-nudge",
        reason="broad obsidian_search_notes may be better as bundled read_files",
        tool="mcp__obsidian__obsidian_search_notes",
    )
