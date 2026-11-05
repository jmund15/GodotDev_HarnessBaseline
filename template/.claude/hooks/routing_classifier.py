#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
routing_classifier.py — shared classification library for tool-routing hooks.

Why this exists:
- The PreToolUse and PostToolUse routing hooks had inlined copies of the same cue-word
  lists and classifiers.
- The `routing_audit.py` hook (continuous silent-miss logger) needs a
  `classify_call()` API that combines every rule family into a single
  classification per tool call. Building it forced the extraction.

Design contract:
- Pure module. No I/O, no `sys.exit`, no stderr writes. Hooks own their output
  channels (stderr for nudge.py, additionalContext JSON for post_grep.py /
  cumulative.py / routing_audit.py).
- Constants and helpers are public (no leading underscore).
- The high-level `classify_call(tool, tool_input, last_prompt)` returns a
  `Classification` dataclass. Severity values:
    - COMPLIANT          : call followed §Tool Routing; no rule applies → no nudge expected
    - NUDGE_WARRANTED    : call violated a clear rule (a bulk-copyable native Read; a
                           version-pinned WebFetch). Silent-miss if the nudge channel doesn't fire.
    - CUE_EXEMPT         : would warrant nudge BUT the prompt or caller legitimizes it
                           (audit-shape carve-out, bundling subagent).
    - ADVISORY_APPLICABLE: soft-nudge category (memory_search, read_web below
                           the synthesis floor) — informational, not a violation.
    - NOT_ROUTABLE       : tool has no §Tool Routing routing rules (Grep, Edit, Glob, etc.)
                           OR the call shape doesn't match a rule.
    - CENSUS             : measured, never judged: vault doc writes (direct vs worker) and
                           lookups (Bash searches and source reads, inline scripts, LSP).

  The audit hook logs NUDGE_WARRANTED, CUE_EXEMPT and CENSUS. The other three are noise from
  the dashboard's perspective.

This module is the single home for these helpers and cue lists.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Literal

import _optional_hooks
from _claude_scope import harness_tail
from _command_text import command_words, executable_spans, executable_word

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))


def _ai_worker_absent() -> bool:
    """Is ai-worker registered on this machine? `False` whenever that cannot be read.

    This module is the one home of every routing VERDICT, and a verdict's reason names the route
    its nudge will print — so the name has to be one this machine has. The verdict itself never
    depends on the machine: `tools/workstation_profile.py` answers `None` for unknown, and unknown
    keeps the authored reason rather than narrowing it on a guess.
    """
    try:
        import workstation_profile
        return workstation_profile.ai_worker() == "absent"
    except Exception:
        return False


# Version-pinned documentation sources a WebFetch should not bypass, one module per adopted layer.
SOURCE_PINS = tuple(m for m in (_optional_hooks.load("godot_source_pins"),) if m)


# === Cue-word constants =====================================================

# Audit-shape carve-out (CLAUDE.core.md §9 known-evidence bullet): line-precision direct
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

# === Cue-word checkers ========================================================

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
    A path, extension or read count is never evidence (CLAUDE.core.md §9)."""
    if not prompt:
        return False
    lowered = prompt.lower()
    return (any(cue in lowered for cue in BULK_SCOPE_CUES)
            and any(cue in lowered for cue in COPYABLE_OUTPUT_CUES))


# === High-level classification (new — used by routing_audit.py) ==============

Severity = Literal[
    "compliant",
    "nudge-warranted",
    "cue-exempt",
    "advisory-applicable",
    "not-routable",
    "census",  # logged for measurement, never a verdict (vault doc writes, lookups)
]


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single tool call against §Tool Routing routing rules.

    Fields:
      severity : See `Severity` literal — the routing-correctness bucket.
      rule     : Short identifier of the rule that applied (e.g.
                 "native-read-bulk-copyable"), or None if no rule matched.
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

    # Lookups are a census, never a verdict: whether a name was known or verified unique is not
    # observable from the call, so Grep carries no rule and Bash and LSP are only counted.
    if tool_name == "Bash":
        return _classify_bash(tool_input)

    if tool_name == "LSP":
        return Classification("census", "lsp-lookup", f"LSP {tool_input.get('operation') or ''}".strip(), "LSP")

    # Native Read while the request asks for bulk copyable I/O.
    if tool_name == "Read":
        return _classify_native_read(tool_input, last_prompt, agent_id)

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


_SEARCH_EXECUTABLES = frozenset(("grep", "egrep", "fgrep", "rg", "find"))
_READ_EXECUTABLES = frozenset(("cat", "head", "tail", "sed", "awk"))
_SCRIPT_FIRST = frozenset(("sed", "awk"))  # the first operand is the script, not a file
_GIT_LOOKUPS = frozenset(("grep", "ls-files", "ls-tree", "check-ignore"))
_GIT_VALUE_OPTIONS = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace"))
_INTERPRETERS = frozenset(("python", "python3", "py", "node"))
_INLINE_FLAGS = frozenset(("-c", "-e", "--eval", "-"))
_SOURCE_FILE = re.compile(
    r"\.(cs|gd|tscn|tres|godot|py|js|ts|sh|ps1|md|json|jsonl|yaml|yml|toml|txt|cfg|ini|xml|csproj|sln)$", re.I)


def _git_subcommand(args: list[str]) -> str:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _GIT_VALUE_OPTIONS:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return ""


def _segment_kind(segment: str) -> str:
    """"lookup", "opaque" or "" for one simple command."""
    parsed = command_words(segment)
    if parsed:
        words = [w.value for w in parsed[0]]
        index = executable_word(parsed[0], segment)
    else:  # a heredoc or substitution: the executable and flags still sit at the front
        words = segment.split()
        index = next((i for i, word in enumerate(words) if not re.match(r"[A-Za-z_]\w*=", word)), len(words))
    if index >= len(words):
        return ""
    name = words[index].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    args = words[index + 1:]
    if name in _SEARCH_EXECUTABLES:
        return "lookup"
    if name == "git":
        return "lookup" if _git_subcommand(args) in _GIT_LOOKUPS else ""
    if name in _READ_EXECUTABLES:
        operands = [a for a in args if not a.startswith("-")]
        if name in _SCRIPT_FIRST:
            operands = operands[1:]
        return "lookup" if any(_SOURCE_FILE.search(a) for a in operands) else ""
    if name in _INTERPRETERS:
        return "opaque" if any(a in _INLINE_FLAGS for a in args) else ""
    return ""


def _classify_bash(tool_input: dict) -> Classification:
    """One census row per Bash call: a search or source read outranks an inline script in the same
    chain. Quoted mentions, comments and heredoc bodies are not executed, so they never count."""
    kinds = {_segment_kind(segment) for _left, _right, segment in executable_spans(tool_input.get("command") or "")}
    if "lookup" in kinds:
        return Classification("census", "bash-lookup", "Bash search or source read", "Bash")
    if "opaque" in kinds:
        return Classification("census", "bash-opaque-script", "Bash inline interpreter script", "Bash")
    return Classification("not-routable", None, None, "Bash")


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
            + ("route the extraction through bounded Read or a subagent "
               "[no ai-worker on this machine]"
               if _ai_worker_absent() else
               "route the extraction through mcp__ai-worker__read_files")
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
