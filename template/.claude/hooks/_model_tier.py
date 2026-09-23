#!/usr/bin/env python3
"""Session tier resolution: which rail depth and which `## detailed` sections a session reads.

One tier per session, resolved at SessionStart from the payload's model id and cached into the
shared routing state so every later hook reads the same answer without re-parsing a model id.

The tier is registry data (`railTier` on the model's row in reference/external_models.json), so a
per-model rail depth is an edit there, never here. Fails toward `detailed`: an unrecognized or absent
model, a row with no `railTier` and an unreadable registry all get the fully-spelled-out rails,
because an over-explicit rail costs bytes while an under-explicit one costs adherence. `/clear`
sends a payload with no `model`, which is exactly the case that must not silently promote a session
out of `detailed`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hook_state import read_json_salvage, state_path, update_json_locked  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
try:
    import model_registry
except Exception:  # no registry module: every session reads detailed
    model_registry = None

TIER_KEY = "session_tier"
VALID_TIERS = ("minimal", "condensed", "detailed")
# A state file written under an earlier vocabulary keeps its tier.
_LEGACY_TIERS = {"opus": "condensed", "terse": "condensed", "strict": "detailed", "fable": "minimal"}


def tier_of(model):
    """`minimal`|`condensed`|`detailed` for a model id or alias. Unrecognized or absent -> detailed."""
    if model_registry is None or not (model or "").strip():
        return "detailed"
    try:
        return model_registry.rail_tier(model)
    except Exception:
        return "detailed"


def write_session_tier(session_id, model):
    """Resolve and cache this session's tier. Returns the tier."""
    tier = tier_of(model)
    update_json_locked(state_path(session_id), lambda state: state.__setitem__(TIER_KEY, tier))
    return tier


def session_tier(session_id):
    """The cached tier for `session_id`, or `detailed` when nothing was cached."""
    tier = read_json_salvage(state_path(session_id)).get(TIER_KEY)
    tier = _LEGACY_TIERS.get(tier, tier)
    return tier if tier in VALID_TIERS else "detailed"
