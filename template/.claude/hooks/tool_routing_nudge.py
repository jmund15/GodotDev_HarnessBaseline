#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse on read/search tools — advisory routing nudge + optional
hard block for the highest-confidence smell (bare-PascalCase Grep on .cs).

Why:
- CLAUDE.md §9 codifies tool routing (ai-worker for bulk prose, LSP for C#
  symbols, semantic-search for NL code discovery), but the harness defaults
  to Read/Grep/direct-MCP-read out of habit. The existing `Read >400 lines`
  PostToolUse nudge does NOT see Obsidian-MCP reads or Grep PascalCase
  patterns. This hook closes those gaps at call time.

What it does:
- Inspects the tool input pre-call.
- For the highest-confidence smell — bare-PascalCase Grep on `.cs` with no
  literal-intent / verified-unique cue words in the user's prompt — and ONLY
  when the env var `PP_ROUTING_HARD_BLOCK_CS_GREP=true` is set, exits with
  code 2 + a deny message (Claude Code blocks the call, agent must retry).
  Default OFF so the change is opt-in.
- For everything else, emits a one-line advisory nudge via
  `hookSpecificOutput.additionalContext` JSON on stdout (exit 0). stderr+exit-0
  is NOT model-visible on PreToolUse (verified 2026-06-09 vs hooks docs — see
  archive_hook_gotchas.md); additionalContext is the only advisory channel that
  reaches the model. Fired rules are recorded to the session state file
  (`pre_nudges_fired_this_turn`) so routing_audit.py can split nudged vs
  silent-miss on positive evidence. Never blocks in advisory paths.

Carve-outs (must NOT block — preserve K1/L6 legitimate-override cases):
- `LITERAL_INTENT_CUES` — user prompt contains "literal", "verbatim",
  "comment", "audit", "every occurrence", etc. The K1 case (literal scan
  including comments). Lifted verbatim from tool_routing_post_grep.py.
- `VERIFIED_UNIQUE_CUES` — user prompt contains "verified-unique",
  "no overloads", "name is unique", etc. The L6 case (verified-unique-name
  carve-out documented in csharp_lsp.md).
- Anchored Grep patterns (`class X`, `interface X`, `record X`, `struct X`,
  `enum X`) — the legitimate anchor step. The bare-PascalCase regex
  `_PASCAL_IDENT` only matches ungrouped identifiers, so anchored patterns
  naturally don't reach the block path.

Tool family handling:
- `mcp__obsidian__obsidian_read_note`: nudge when path implies a large doc
  family (Design / Planning / BrainstormingDesigns / Documentation / Brainstorm
  / Architecture / Retrospective) — those are synthesis targets, not surgical
  reads.
- `mcp__obsidian__obsidian_global_search`: nudge when pageSize > 5 OR
  contextLength > 100 OR pageSize is unset (default broad search) — implies
  bulk-result synthesis, fits read_files better.
- `Grep`: nudge when `pattern` is a single PascalCase identifier (no regex
  metachars) AND the target shape suggests `.cs` (glob `*.cs`/`**/*.cs` OR
  type `cs` OR no glob+type at all → likely cross-codebase). Suggests LSP
  if available, otherwise semantic-search.
- `Read`: nudge when `file_path` is a `.md` file under a synthesis-shaped
  folder (Design / Planning / BrainstormingDesigns / Documentation /
  Brainstorm / Architecture / Retrospective / Audit / Review / Postmortem).
  Closes the gap where an agent reads a synthesis-shaped vault doc by
  absolute path instead of routing through `read_files`. Restricted to `.md` to avoid
  false-positives on .cs/.tres at synthesis-named folders.

Boundaries:
- Never blocks. Exit 0 in all paths.
- Silent on tool calls that don't match any pattern.
- Cloud-aware: when CLAUDE_CODE_REMOTE=true, suppresses LSP suggestions
  (LSP unavailable on cloud per settings.local.json) and points at
  semantic-search instead.

Wired in: settings.json hooks.PreToolUse with matcher
"mcp__obsidian__obsidian_read_note|mcp__obsidian__obsidian_global_search|Grep|Read".
"""

import json
import os
import sys

# Shared classifier — extracted 2026-05-04 to eliminate cue-list duplication
# across nudge.py / post_grep.py / cumulative.py and provide the API the new
# routing_audit.py PostToolUse hook depends on. See routing_classifier.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from routing_classifier import (
    LITERAL_INTENT_CUES,
    VERIFIED_UNIQUE_CUES,
    SYNTHESIS_DOC_HINTS,
    BULK_PAGESIZE_THRESHOLD,
    BULK_CONTEXT_THRESHOLD,
    is_pascal_identifier,
    grep_target_family,
    is_cloud_session,
    prompt_has_grep_override_cue,
)

# --- Hard-block configuration (Fix 2) ------------------------------------

# Env var toggle. Default OFF — the hard block is opt-in until the user
# verifies it doesn't fire spuriously in their normal workflow.
HARD_BLOCK_ENV_VAR = "PP_ROUTING_HARD_BLOCK_CS_GREP"

# State file location — same as tool_routing_post_grep.py and
# tool_routing_cumulative_reset.py (which populates last_prompt).
_STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


def _hard_block_enabled() -> bool:
    """Env-var toggle for the Fix 2 hard block. Default OFF."""
    return os.environ.get(HARD_BLOCK_ENV_VAR, "").lower() in ("1", "true", "yes")


def _read_last_prompt(session_id: str) -> str:
    """
    Read the user's most recent prompt from the per-session state file.
    The state file is populated by tool_routing_cumulative_reset.py on
    UserPromptSubmit. Returns "" on any failure (block path treats empty
    prompt as "no cue words found" → defaults to blocking; this is the
    correct fail-closed posture for an opt-in safety hook).
    """
    if not session_id:
        return ""
    sid_short = session_id[:8]
    path = os.path.join(_STATE_DIR, f"{sid_short}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return (data.get("last_prompt") or "")
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass
    return ""


def _should_hard_block_grep(tool_input: dict, session_id: str) -> bool:
    """
    Decision: should this Grep call be hard-blocked (Fix 2)?

    Conditions ALL must hold:
    - Env var toggle is on.
    - Pattern is bare PascalCase (no regex metachars, no anchor).
    - Target family is `.cs` (the highest-confidence smell — indexed-other
      stays as advisory nudge).
    - User prompt does NOT contain a K1 or L6 override cue.

    Cloud sessions: still block (semantic-search is the cloud substitute,
    bare-Grep on .cs is just as wrong there as on local).
    """
    if not _hard_block_enabled():
        return False
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        return False
    if grep_target_family(tool_input) != "cs":
        return False
    if prompt_has_grep_override_cue(_read_last_prompt(session_id)):
        return False
    return True


def _build_block_message(pattern: str) -> str:
    """Compose the deny message for a blocked bare-PascalCase Grep on .cs."""
    if is_cloud_session():
        retry_path = (
            f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')` "
            "(cloud session — LSP unavailable; semantic-search ranks by symbol-match)"
        )
    else:
        retry_path = (
            f"the **anchor-then-navigate** workflow: "
            f"`Grep('class {pattern}\\b' -g '*.cs')` (or `interface`/`record`/`struct`/`enum`) "
            f"to find the declaration file, then `LSP documentSymbol` for line numbers, "
            f"then `LSP findReferences` from the anchored position"
        )
    return (
        f"[tool-routing] BLOCKED: bare `Grep('{pattern}')` on `.cs` is a documented "
        "LSP-bypass smell (CLAUDE.md §9). Retry with " + retry_path + ". "
        "If your task genuinely needs literal-text scan including comments (K1 case) "
        "or verified-unique-name override (L6 case), restate the user's intent in "
        "your response — the next call will succeed because the cue-word allowlist "
        "covers those cases. Hard block is gated on "
        f"PP_ROUTING_HARD_BLOCK_CS_GREP={os.environ.get(HARD_BLOCK_ENV_VAR, 'unset')}; "
        "set to false in env to disable and revert to advisory nudges."
    )


# --- Nudge composition ---------------------------------------------------

def _nudge_obsidian_read(tool_input: dict) -> str | None:
    path = tool_input.get("filePath") or ""
    if not path:
        return None
    if not any(hint.lower() in path.lower() for hint in SYNTHESIS_DOC_HINTS):
        return None
    return (
        "[tool-routing] Synthesis-shaped Obsidian doc detected. If you're loading "
        f"`{path}` to summarize / extract context (not for surgical citation), "
        "prefer `mcp__ai-worker__read_files(paths=[<path>], question=...)` — "
        "cheap model reads, returns a 1-2 KB digest instead of loading the full "
        "doc into your context. See CLAUDE.md §9."
    )


def _nudge_read(tool_input: dict) -> str | None:
    """Native `Read` of a synthesis-shaped `.md` path. Companion to
    `_nudge_obsidian_read` — closes the gap where an agent reads a
    synthesis-shaped doc by absolute path instead of routing through
    `read_files`. Schema note: native Read uses `file_path` (snake_case),
    not `filePath`. Restricted to `.md` to avoid false-positives on .cs/
    .tres paths under folders like `Architecture/` or `Audit/` — those
    have their own routing rules (LSP / Grep) and are line-precision
    targets, not synthesis prose."""
    path = tool_input.get("file_path") or ""
    if not path:
        return None
    if not path.lower().endswith(".md"):
        return None
    if not any(hint.lower() in path.lower() for hint in SYNTHESIS_DOC_HINTS):
        return None
    return (
        "[tool-routing] Synthesis-shaped doc path detected on native `Read`. If "
        f"you're loading `{path}` to summarize / extract context (not for "
        "surgical citation), prefer `mcp__ai-worker__read_files(paths=[<path>], "
        "question=...)` — cheap model reads, returns a 1-2 KB digest instead of "
        "loading the full doc into your context. The synthesis-shape rule is "
        "path-based, not Obsidian-MCP-only — using native `Read` on a "
        "`BrainstormingDesigns/` doc still burns context. See CLAUDE.md §9."
    )


def _nudge_obsidian_search(tool_input: dict) -> str | None:
    page_size = tool_input.get("pageSize")
    context_length = tool_input.get("contextLength")
    is_bulk = (
        page_size is None
        or (isinstance(page_size, (int, float)) and page_size > BULK_PAGESIZE_THRESHOLD)
        or (isinstance(context_length, (int, float)) and context_length > BULK_CONTEXT_THRESHOLD)
    )
    if not is_bulk:
        return None
    return (
        "[tool-routing] Broad Obsidian search detected. If this is the 2nd+ search "
        "for the same investigation, bundle the whole investigation into ONE "
        "`mcp__ai-worker__read_files` call with `files=[doc1, doc2, ...]` and a "
        "specific question — saves context vs chained search-then-read. "
        "See CLAUDE.md §9."
    )


def _nudge_grep(tool_input: dict) -> str | None:
    pattern = tool_input.get("pattern") or ""
    if not is_pascal_identifier(pattern):
        return None

    family = grep_target_family(tool_input)

    # `other` family = not indexed by semantic-search (rare); Grep is fine.
    if family == "other":
        return None

    # `.cs` → LSP wins (or semantic-search if cloud disabled LSP).
    if family == "cs":
        if is_cloud_session():
            return (
                f"[tool-routing] `Grep('{pattern}')` on `.cs` — LSP bypass smell; cloud "
                "session, use "
                f"`mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')`. "
                "CLAUDE.md §7 + §9."
            )
        return (
            f"[tool-routing] Bare `Grep('{pattern}')` on `.cs` — LSP bypass smell. "
            f"Anchor-then-navigate: `Grep('class {pattern}\\b' -g '*.cs')` → "
            f"`LSP documentSymbol` → `findReferences`. CLAUDE.md §7 + §9."
        )

    # Indexed-but-not-`.cs` (.tscn/.tres/.gd/.md/etc.) or mixed → semantic-search.
    # PascalCase shape is the smell; Grep stays correct for literals, UIDs, regex
    # alternation, attribute markers (don't match the single-PascalCase trigger).
    target_label = "indexed types" if family == "indexed-other" else "unrestricted target"
    return (
        f"[tool-routing] `Grep('{pattern}')` against {target_label} bypasses "
        f"semantic-search. Use `mcp__plugin_semantic-search_semantic-search__search(query='{pattern}')`. "
        "CLAUDE.md §9."
    )


# --- Dispatch ------------------------------------------------------------

_DISPATCH = {
    "mcp__obsidian__obsidian_read_note": _nudge_obsidian_read,
    "mcp__obsidian__obsidian_global_search": _nudge_obsidian_search,
    "Grep": _nudge_grep,
    "Read": _nudge_read,
}


# Rule keys recorded to state when an advisory nudge fires — must match the
# routing_classifier.classify_call rule identifiers routing_audit.py logs, so
# the dashboard's nudged-vs-silent-miss split runs on positive evidence.
_RULE_KEYS = {
    "mcp__obsidian__obsidian_read_note": "obsidian-synthesis-doc-direct-read",
    "Read": "native-read-synthesis-doc",
}


def _record_pre_nudge(session_id: str, rule: str) -> None:
    """Best-effort append of a fired rule key to the session-level state file
    (`tool_routing_cumulative_reset.py` clears the list each turn)."""
    if not rule:
        return
    path = os.path.join(_STATE_DIR, f"{session_id[:8] if session_id else 'default'}.json")
    try:
        state: dict = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        fired = state.get("pre_nudges_fired_this_turn") or []
        if rule not in fired:
            fired.append(rule)
        state["pre_nudges_fired_this_turn"] = fired
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=True)
    except Exception:
        pass


def process(input_data: dict) -> tuple[str | None, str | None]:
    """In-process entry — returns (block_msg, nudge_msg); at most one is set.
    Called by pre_read_dispatch.py; main() wraps it for standalone wiring.
    Side effect: records fired advisory rules to session state."""
    tool_name = input_data.get("tool_name") or ""
    tool_input = input_data.get("tool_input") or {}
    session_id = input_data.get("session_id") or ""

    # Fix 2: hard-block path — checked before advisory dispatch. Only fires
    # for Grep tool, only when env var toggle is on, only for bare-PascalCase
    # on .cs without override cues. All other paths fall through to advisory.
    if tool_name == "Grep" and _should_hard_block_grep(tool_input, session_id):
        try:
            pattern = tool_input.get("pattern") or ""
            return (_build_block_message(pattern), None)
        except Exception:
            # Last-resort: block with a generic message rather than crashing
            # (which would let the call through, defeating the block).
            return (
                "[tool-routing] BLOCKED: bare-PascalCase Grep on .cs is an "
                "LSP-bypass smell. Retry with anchor-then-navigate or "
                "semantic-search. See CLAUDE.md §9.",
                None,
            )

    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return (None, None)

    try:
        nudge = handler(tool_input)
    except Exception:
        # Hook must never break the tool call. Swallow any handler bug.
        return (None, None)

    if not nudge:
        return (None, None)

    _record_pre_nudge(session_id, _RULE_KEYS.get(tool_name, ""))
    return (None, nudge)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    block_msg, nudge = process(input_data)

    if block_msg:
        sys.stderr.write(block_msg + "\n")
        sys.exit(2)

    if nudge:
        # additionalContext is the only model-visible advisory channel on
        # PreToolUse exit 0 — stderr here goes to the debug log only.
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": nudge,
            }
        }
        sys.stdout.write(json.dumps(payload))

    sys.exit(0)


if __name__ == "__main__":
    main()
