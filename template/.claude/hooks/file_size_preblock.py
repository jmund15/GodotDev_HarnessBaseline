#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse Read — block explicit FULL reads of large files.

Why:
- Reading a large file in full burns context tokens proportional to file size.
  A 30 KB file is ~10,000 estimated tokens; a bundled
  `mcp__ai-worker__read_files(paths=[...], question=...)` call reads the file
  in a worker process and returns a 1-2 KB digest at near-zero context cost.
- A PreToolUse block is the only mechanism that actually prevents the cost —
  a post-Read nudge fires after the tokens are already spent.

What it does:
- On Read tool calls, stats the target file. If size exceeds
  LARGE_FILE_BYTE_THRESHOLD AND the read is unbounded (no offset, no limit),
  blocks via exit code 2 + stderr message.
- Exempts:
  * Bounded reads (offset OR limit set) — agent has explicitly scoped the read
  * Non-existent files — let Read produce its own error
  * Binary/visual formats Read handles specially (.pdf, .ipynb, images)
  * Instruction-shape `.md` under `.claude/` — skills, commands, rules,
    CLAUDE.md. These are meta-instructions for the agent's own behavior; a
    worker digest cannot substitute (the agent needs the actual content in
    context to follow the rules). Deliberately NOT a blanket `.claude/**`
    exemption — state/log files there can grow huge and stay gated.
  * Audit-shape prompts — "audit", "fact-check", "line by line", etc.

What it does NOT do:
- Nudge — this is purely a hard block above a clearly-large threshold, not an
  additional reminder layer.
- Block bounded large reads — `Read(file_path=X, offset=0, limit=2000)` of a
  100 KB file is allowed; the agent committed to a specific window.

Block contract: print to stderr, exit 2.

Wiring: settings.json hooks.PreToolUse with matcher "Read".
"""

import json
import os
import sys

# --- Tunables ------------------------------------------------------------

# Byte threshold for blocking. ~40 KB ≈ ~13,000 estimated tokens of source
# (3 bytes/token rough estimate). Above this, a worker-bundled read is
# unambiguously cheaper than a full-file context load.
LARGE_FILE_BYTE_THRESHOLD = 40 * 1024  # 40 KB

# Bytes-per-token estimate for messaging only (not for the gate decision).
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

# Fallback audit-intent cue list (no routing_classifier.py in this project —
# the cluster it belongs to isn't ported; this hardcoded tuple is the whole
# mechanism, not a degraded fallback of a richer one).
AUDIT_INTENT_CUES = (
    "audit", "code review", "security review", "debug this",
    "debugging this", "step through", "trace through", "fact-check",
    "fact check", "line by line", "line-by-line", "inspect the code",
    "inspect this file", "verify the implementation",
    "review for bugs", "review for issues",
)


# --- Audit-shape carve-out -------------------------------------------------

def _state_path(session_id: str, agent_id: str = "") -> str:
    sid_short = (session_id[:8] if session_id else "default")
    if agent_id:
        aid_short = agent_id[:8]
        return os.path.join(STATE_DIR, f"{sid_short}_{aid_short}.json")
    return os.path.join(STATE_DIR, f"{sid_short}.json")


def _read_last_prompt(session_id: str, agent_id: str = "") -> str:
    """
    Read the user's last prompt from the routing state file, IF something else
    populated it. Returns empty string on any failure or absence — a missing
    prompt should not cause this hook to mis-block or mis-allow.
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
    lowered = prompt.lower()
    return any(cue in lowered for cue in AUDIT_INTENT_CUES)


# --- Gate logic ------------------------------------------------------------

def _is_bounded_read(tool_input: dict) -> bool:
    """
    True when the agent has explicitly bounded the read with offset or limit.
    Either parameter present (non-None) counts as bounded.
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
    True for `.md` files under `.claude/` — skills, commands, rules, CLAUDE.md.
    These are meta-instructions for the agent's own behavior; the agent needs
    the actual content in context to follow the rules, so worker bundling is
    semantically wrong here.

    Deliberately NOT a blanket `.claude/**` exemption — state/log files can
    grow into hundreds of KB and the size protection still applies there.
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
        f"or `mcp__ai-worker__read_files(paths=[\"{file_path}\"], question=<...>)`. "
        "If ai-worker is absent this session, use the bounded Read — it always passes this gate."
    )


# --- Dispatch ----------------------------------------------------------

def process(input_data: dict) -> str | None:
    """Returns the block message or None."""
    tool_name = input_data.get("tool_name") or ""
    if tool_name != "Read":
        return None

    tool_input = input_data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    if not file_path:
        return None

    if _is_bounded_read(tool_input):
        return None

    if _is_exempt_extension(file_path):
        return None

    if _is_exempt_instruction_file(file_path):
        return None

    try:
        if not os.path.isfile(file_path):
            return None
        size_bytes = os.path.getsize(file_path)
    except OSError:
        return None

    if size_bytes <= LARGE_FILE_BYTE_THRESHOLD:
        return None

    # Audit-shape exemption — checked after the size gate so we don't pay the
    # state-file read on every Read call.
    session_id = input_data.get("session_id") or ""
    agent_id = input_data.get("agent_id") or ""
    last_prompt = _read_last_prompt(session_id, agent_id)
    if last_prompt and _is_audit_exempt(last_prompt):
        return None

    return _build_block_message(file_path, size_bytes)


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    block_msg = process(input_data)
    if block_msg:
        sys.stderr.write(block_msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
