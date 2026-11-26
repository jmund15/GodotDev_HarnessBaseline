#!/usr/bin/env python3
"""Shared origin checks for the hooks that speak to an owner: UserPromptSubmit and SessionStart.

`in_delegate` is the one fact about the session: a native subagent (payload `agent_id`), a launcher child
(`CLAUDE_CODE_SIDECAR`, which the seat launchers set while forcing entrypoint `cli`, so the entrypoint alone misses
them) or any `claude -p` child (entrypoint `sdk-cli`). A delegate holds a brief's tool grant, not the owner's:
no person types to it and no owner action is open to it.
`is_user_intent_prompt` reads the prompt's own shape (an envelope or a relayed peer message is runtime transport).
`human_typed` is both: user-intent text outside every delegate. `prompt_dispatch` calls it once for every member it
declares owner-facing; `session_context_loader` and `session_model_rails` call `in_delegate` for the SessionStart
lines that steer to an owner action or a tool a delegate lacks. Every other "is this call from a delegate?" decision
(a dispatch deny, a background rewrite, an owner-only record, a wide-run admission) asks it too; a hook or tool with
no payload passes `{}`. A decision about something else keeps its own reading: a window's `agent_id` key, a job's
inbox, a sidecar's kind, whether a completion wakes the caller."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _owner_text import ENVELOPE_NOTICES, ENVELOPE_TAGS  # noqa: E402

_RUNTIME_ENVELOPE_RE = re.compile(
    r"^<(?P<tag>" + "|".join(ENVELOPE_TAGS) + r")(?:\s+[^>]*)?>.*</(?P=tag)>$",
    re.DOTALL,
)
_RUNTIME_NOTICE_RE = re.compile(r"^\[(?:" + "|".join(ENVELOPE_NOTICES) + r") \w[^\]]*\]", re.I)
_RELAYED_RE = re.compile(r"^another claude session sent a message\b", re.I)


def is_user_intent_prompt(prompt: str) -> bool:
    """True for a real request; false for an empty prompt, a runtime envelope or a relayed peer message."""
    if not isinstance(prompt, str):
        return False
    stripped = prompt.strip()
    return (bool(stripped) and _RUNTIME_ENVELOPE_RE.fullmatch(stripped) is None
            and _RUNTIME_NOTICE_RE.match(stripped) is None and _RELAYED_RE.match(stripped) is None)


def in_delegate(payload, environ) -> bool:
    """True when this hook call runs inside a delegate, by the payload's `agent_id` and the process environment."""
    return bool(str(payload.get("agent_id") or "").strip() or str(environ.get("CLAUDE_CODE_SIDECAR") or "").strip()
                or environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli")


def human_typed(payload, environ) -> bool:
    """True when `payload`'s prompt was typed by a person: user-intent text, outside every delegate."""
    return not in_delegate(payload, environ) and is_user_intent_prompt(payload.get("prompt"))
