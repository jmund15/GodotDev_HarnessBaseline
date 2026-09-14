#!/usr/bin/env python3
"""Session tier resolution: which rail depth and which `## strict` sections a session reads.

One tier per session, resolved at SessionStart from the payload's model id and cached into the
shared routing state so every later hook reads the same answer without re-parsing a model id.

Fails toward `strict`: an unrecognized or absent model gets the fully-spelled-out rails, because an
over-explicit rail costs bytes while an under-explicit one costs adherence. `/clear` sends a payload
with no `model`, which is exactly the case that must not silently promote a session out of `strict`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_state import read_json_salvage, state_path, write_json_atomic  # noqa: E402

TIER_KEY = "session_tier"
VALID_TIERS = ("fable", "opus", "strict")


def tier_of(model):
    """`fable`|`opus`|`strict` for a model id or alias. Anything unrecognized or absent -> strict."""
    name = (model or "").strip().lower()
    if not name:
        return "strict"
    if "fable" in name or "mythos" in name:
        return "fable"
    if "opus" in name:
        return "opus"
    return "strict"


def write_session_tier(session_id, model):
    """Resolve and cache this session's tier. Returns the tier."""
    tier = tier_of(model)
    path = state_path(session_id)
    state = read_json_salvage(path)
    state[TIER_KEY] = tier
    write_json_atomic(path, state)
    return tier


def session_tier(session_id):
    """The cached tier for `session_id`, or `strict` when nothing was cached."""
    tier = read_json_salvage(state_path(session_id)).get(TIER_KEY)
    return tier if tier in VALID_TIERS else "strict"
