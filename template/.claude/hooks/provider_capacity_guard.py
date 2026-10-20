#!/usr/bin/env python3
"""Provider-capacity decision shared by native Workflow/Agent dispatch hooks."""
import os
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import provider_capacity  # noqa: E402


def _binding_window(quota):
    """' (<window> at N% used, resets HH:MM local)' for the window that refused, or '' when unnamed."""
    if not isinstance(quota, dict):
        return ""
    name = quota.get("bindingWindow")
    window = next((w for w in quota.get("windows") or [] if isinstance(w, dict) and w.get("name") == name), None)
    if not window:
        return ""
    resets = window.get("resetsAt")
    when = time.strftime("%H:%M", time.localtime(resets)) if isinstance(resets, (int, float)) else "unknown"
    return " (%s at %s%% used, resets %s local)" % (name, window.get("usedPercent"), when)


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
    if status == "dispatch-floor":
        return provider_capacity.floor_refusal(value) + "; native dispatch is blocked until reset"
    if status == "exhausted":
        return "Provider capacity exhausted for %s%s; native Workflow/Agent dispatch is blocked" % (
            transport, _binding_window(value.get("quota")))
    if status == "insufficient":
        return "Provider balance insufficient for %s; native Workflow/Agent dispatch is blocked" % transport
    if status == "auth-error":
        return "Provider authentication failed for %s; re-login before native dispatch" % transport
    if status == "network-error":
        failure = value.get("error") or value.get("liveFailure") or {}
        if failure.get("kind") == "rate-limit":
            return "Provider usage endpoint for %s is rate limited: %s; native dispatch is blocked" % (
                transport, str(failure.get("message"))[:200])
        return "Provider network capacity check failed for %s with no valid fallback" % transport
    if status == "malformed":
        return "Provider capacity response malformed for %s; native dispatch is blocked" % transport
    return "Provider capacity status unknown for %s: %r" % (transport, status)
