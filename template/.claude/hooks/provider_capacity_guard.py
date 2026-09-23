#!/usr/bin/env python3
"""Provider-capacity decision shared by native Workflow/Agent dispatch hooks."""
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import provider_capacity  # noqa: E402


def refusal(transport, *, data=None, reader=None):
    """Return a denial reason, or None when normalized capacity permits native work."""
    reader = reader or provider_capacity.reading
    try:
        value = reader(transport, data=data)
    except Exception as exc:
        return "Provider capacity unavailable for %s: %s" % (transport, str(exc)[:300])
    if not isinstance(value, dict):
        return "Provider capacity unknown for %s: normalized result is missing" % transport
    status = value.get("status")
    source = value.get("sourceKind")
    if status == "available":
        return None
    if status == "unsupported" and source == "unsupported":
        return None
    if status == "exhausted":
        return "Provider capacity exhausted for %s; native Workflow/Agent dispatch is blocked" % transport
    if status == "insufficient":
        return "Provider balance insufficient for %s; native Workflow/Agent dispatch is blocked" % transport
    if status == "auth-error":
        return "Provider authentication failed for %s; re-login before native dispatch" % transport
    if status == "network-error":
        return "Provider network capacity check failed for %s with no valid fallback" % transport
    if status == "malformed":
        return "Provider capacity response malformed for %s; native dispatch is blocked" % transport
    return "Provider capacity status unknown for %s: %r" % (transport, status)
