#!/usr/bin/env python3
"""Shared prompt-origin check for UserPromptSubmit hooks."""
import re


_RUNTIME_ENVELOPE_RE = re.compile(
    r"^<(?P<tag>task-notification|local-command-stdout|cross-session-message|agent-message)"
    r"(?:\s+[^>]*)?>.*</(?P=tag)>$",
    re.DOTALL,
)


def is_user_intent_prompt(prompt: str) -> bool:
    """True for a real request; false for an empty or full runtime envelope."""
    if not isinstance(prompt, str):
        return False
    stripped = prompt.strip()
    return bool(stripped) and _RUNTIME_ENVELOPE_RE.fullmatch(stripped) is None
