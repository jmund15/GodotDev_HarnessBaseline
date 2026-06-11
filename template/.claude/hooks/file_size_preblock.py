#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse Read — block explicit FULL reads of large files.

Why:
- Reading a large file in full burns context tokens proportional to file size.
  A 30 KB source file is ~10,000 estimated tokens; a bundled
  `mcp__ai-worker__read_files(paths=[...], question=...)` call reads the file
  in a worker process and returns a 1-2 KB digest at near-zero context cost.
- The existing post-Read 400-line nudge fires AFTER the tokens are spent —
  informational only. Per the cumulative_block.py rationale (lines 11-13):
  "agents acknowledge the nudge but the call has already burned context tokens
  by then." A PreToolUse block is the only mechanism that actually prevents
  the cost.
- Companion to tool_routing_cumulative.py / cumulative_block.py — they catch
  the cascade shape (many small reads); this catches the single-large-read
  shape. Orthogonal cost surfaces; both needed.

What it does:
- On Read tool calls, stats the target file. If size exceeds
  LARGE_FILE_BYTE_THRESHOLD AND the read is unbounded (no offset, no limit),
  blocks via exit code 2 + stderr message.
- Exempts:
  * Bounded reads (offset OR limit set) — agent has explicitly scoped the read
  * Non-existent files — let Read produce its own error
  * Binary/visual formats Read handles specially (.pdf, .ipynb, images)
  * Instruction-shape `.md` under `.claude/` — skills, commands, rules,
    agents, CLAUDE.md, worklog mirror. These are meta-instructions for the
    agent's own behavior; a worker digest cannot substitute (the agent needs
    the actual content in context to follow the rules). Note: deliberately
    NOT a blanket `.claude/**` exemption — state and log files there
    (.json/.jsonl, .routing_state/) can grow huge and full reads ARE
    expensive; they remain gated.
  * Audit-shape prompts — same AUDIT_INTENT_CUES carve-out as cumulative.py
    (line-precision audit needs frontier-model engagement with primary source)

What it does NOT do:
- Nudge — we already have the post-Read 400-line nudge. This hook is purely a
  hard block above a clearly-large threshold. "We have enough nudges" was the
  explicit framing for this hook's scope.
- Block bounded large reads — `Read(file_path=X, offset=0, limit=2000)` of a
  100 KB file is allowed; the agent committed to a specific window.

Block contract:
- print to stderr, exit 2 — matches pattern_enforcer.py and
  cumulative_block.py convention.

Wiring:
- settings.json hooks.PreToolUse with matcher "Read".
"""

import json
import os
import sys

# --- Tunables ------------------------------------------------------------

# Byte threshold for blocking. ~40 KB ≈ ~13,000 estimated tokens of source code
# (3 bytes/token rough estimate). Above this, a worker-bundled read is
# unambiguously cheaper than a full-file context load. Tune downward if false
# negatives observed (legitimate full reads of medium files); upward if false
# positives observed (legitimate audit work tripping the gate without using
# the audit cue words). Original value 30 KB bumped to 40 KB after observing
# borderline files like complex spell components occupy 30-40 KB legitimately.
LARGE_FILE_BYTE_THRESHOLD = 40 * 1024  # 40 KB

# Bytes-per-token estimate for messaging only (not for the gate decision).
# Source code averages ~3-3.5 bytes/token; prose ~4. Use 3 for visibility
# (overstates token count slightly, which is the safer direction for a
# user-facing message).
BYTES_PER_TOKEN_ESTIMATE = 3

# File extensions Read handles via specialized mechanisms (paginated PDF reads,
# notebook cell extraction, visual image rendering). Full-file size is NOT a
# proxy for context cost on these — exempt entirely.
EXEMPT_EXTENSIONS = frozenset({
    ".pdf",        # Read uses `pages` param for pagination
    ".ipynb",      # Notebook cell extraction has its own context shape
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".heic",
    ".ico", ".avif",
})

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")


# --- Audit-shape carve-out (mirrors cumulative.py) -----------------------

def _state_path(session_id: str, agent_id: str = "") -> str:
    """Mirror of tool_routing_cumulative._state_path — same key shape."""
    sid_short = (session_id[:8] if session_id else "default")
    if agent_id:
        aid_short = agent_id[:8]
        return os.path.join(STATE_DIR, f"{sid_short}_{aid_short}.json")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_last_prompt(session_id: str, agent_id: str = "") -> str:
    """
    Read the user's last prompt from the routing state file. Used for the
    audit-shape exemption check. Returns empty string on any failure
    (defensive — a missing prompt should not cause this hook to mis-block or
    mis-allow). State file is populated by tool_routing_cumulative_reset.py
    at every UserPromptSubmit.
    """
    path = _state_path(session_id, agent_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if isinstance(state, dict):
            return (state.get("last_prompt") or "")
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return ""


def _is_audit_exempt(prompt: str) -> bool:
    """
    Mirror cumulative.py's audit-cue allowlist. Imported lazily so a missing
    routing_classifier module degrades to a sensible default rather than
    breaking the hook.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from routing_classifier import AUDIT_INTENT_CUES
    except ImportError:
        AUDIT_INTENT_CUES = (
            "audit", "code review", "security review", "debug this",
            "debugging this", "step through", "trace through", "fact-check",
            "fact check", "line by line", "line-by-line", "inspect the code",
            "inspect this file", "verify the implementation",
            "review for bugs", "review for issues",
        )
    lowered = prompt.lower()
    return any(cue in lowered for cue in AUDIT_INTENT_CUES)


# --- Gate logic ----------------------------------------------------------

def _is_bounded_read(tool_input: dict) -> bool:
    """
    True when the agent has explicitly bounded the read with offset or limit.
    Either parameter present (non-None) counts as bounded — even if the bound
    is large, the agent committed to a specific window rather than asking for
    the entire file blindly.
    """
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    return offset is not None or limit is not None


def _is_exempt_extension(file_path: str) -> bool:
    """Extensions Read handles via specialized mechanisms — size doesn't proxy cost."""
    _, ext = os.path.splitext(file_path.lower())
    return ext in EXEMPT_EXTENSIONS


def _is_exempt_instruction_file(file_path: str) -> bool:
    """
    True for `.md` files under `.claude/` — skills, commands, rules, agents,
    CLAUDE.md, worklog mirror. These are meta-instructions for the agent's
    own behavior; the agent needs the actual content in context to follow the
    rules, so worker bundling is semantically wrong here.

    Path normalization mirrors pattern_enforcer.py:65 (replace backslashes,
    case-fold) for cross-platform consistency. Handles both absolute paths
    (`C:/.../.claude/skills/foo/SKILL.md`) and relative paths
    (`.claude/skills/foo/SKILL.md`).

    Deliberately NOT a blanket `.claude/**` exemption — state/log files like
    `.claude/self_evaluate_archive.json` or `.claude/.routing_state/*.json`
    can grow into hundreds of KB and the size protection still applies there.
    """
    normalized = file_path.replace('\\', '/').lower()
    if not normalized.endswith('.md'):
        return False
    return '/.claude/' in normalized or normalized.startswith('.claude/')


def _build_block_message(file_path: str, size_bytes: int) -> str:
    est_tokens = size_bytes // BYTES_PER_TOKEN_ESTIMATE
    size_kb = size_bytes // 1024
    threshold_kb = LARGE_FILE_BYTE_THRESHOLD // 1024
    return (
        f"[file-size-block] {file_path} is ~{size_kb} KB (~{est_tokens} tokens) "
        f"— above {threshold_kb} KB threshold for unbounded reads. "
        f"Recover: bounded `Read(file_path=..., offset=N, limit=M)` "
        f"or `mcp__ai-worker__read_files(paths=[\"{file_path}\"], question=<...>)`."
    )


# --- Dispatch ------------------------------------------------------------

def process(input_data: dict) -> str | None:
    """In-process entry — returns the block message or None.
    Called by pre_read_dispatch.py; main() wraps it for standalone wiring."""
    tool_name = input_data.get("tool_name") or ""
    if tool_name != "Read":
        return None

    tool_input = input_data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if not file_path:
        return None

    # Bounded read — agent explicitly scoped the call. Pass through.
    if _is_bounded_read(tool_input):
        return None

    # Exempt extension — Read handles specially, size isn't a context proxy.
    if _is_exempt_extension(file_path):
        return None

    # Exempt .claude/ instruction files (.md only) — skills, commands, rules,
    # agents, CLAUDE.md, worklog mirror. Worker digest can't substitute for
    # meta-instructions the agent needs in its own context.
    if _is_exempt_instruction_file(file_path):
        return None

    # File doesn't exist or isn't readable — let Read produce its own error.
    try:
        if not os.path.isfile(file_path):
            return None
        size_bytes = os.path.getsize(file_path)
    except OSError:
        return None

    # Below threshold — pass through silently.
    if size_bytes <= LARGE_FILE_BYTE_THRESHOLD:
        return None

    # Audit-shape exemption — line-precision direct read warranted by user
    # framing per CLAUDE.md §9 audit exception. Check after the size gate so
    # we don't pay the state-file read on every Read call.
    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id") or ""
    last_prompt = _read_last_prompt(session_id, agent_id)
    if last_prompt and _is_audit_exempt(last_prompt):
        return None

    # All gates passed — block.
    return _build_block_message(file_path, size_bytes)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin — never block on a parse failure.
        sys.exit(0)

    block_msg = process(input_data)
    if block_msg:
        sys.stderr.write(block_msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
