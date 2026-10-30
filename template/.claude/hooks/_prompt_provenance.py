#!/usr/bin/env python3
"""Shared prompt-origin check for UserPromptSubmit hooks."""
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


def is_user_intent_prompt(prompt: str) -> bool:
    """True for a real request; false for an empty or full runtime envelope."""
    if not isinstance(prompt, str):
        return False
    stripped = prompt.strip()
    return (bool(stripped) and _RUNTIME_ENVELOPE_RE.fullmatch(stripped) is None
            and _RUNTIME_NOTICE_RE.match(stripped) is None)
