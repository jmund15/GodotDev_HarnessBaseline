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


def _unreadable_warning(transport, value):
    failure = value.get("error") or value.get("liveFailure") or {}
    reason = " ".join(str(failure.get("message") or "").split()) if isinstance(failure, dict) else ""
    return "Provider capacity for %s is unreadable (%s): %s; native dispatch proceeds on a warning" % (
        transport, value.get("status"),
        reason or provider_capacity.reason_text(value) or "the reader gave no reason")


def assess(transport, *, data=None, reader=None):
    """Return `(refusal, warning)`: a denial reason when the reading shows no room, else None, beside
    the warning an unreadable meter contributes to the launch that proceeds anyway (orchestration §5b).
    """
    reader = reader or provider_capacity.reading
    try:
        value = reader(transport, data=data)
    except Exception as exc:
        return "Provider capacity unavailable for %s: %s" % (transport, str(exc)[:300]), None
    if not isinstance(value, dict):
        return "Provider capacity unknown for %s: normalized result is missing" % transport, None
    status = value.get("status")
    source = value.get("sourceKind")
    if status == "available":
        return None, None
    if status == "unsupported" and source == "unsupported":
        return None, None
    if status == "dispatch-floor":
        return provider_capacity.floor_refusal(value) + "; native dispatch is blocked until reset", None
    if status == "exhausted":
        return "Provider capacity exhausted for %s%s; native Workflow/Agent dispatch is blocked" % (
            transport, _binding_window(value.get("quota"))), None
    if status == "insufficient":
        return "Provider balance insufficient for %s; native Workflow/Agent dispatch is blocked" % transport, None
    if status == "auth-error":
        return "Provider authentication failed for %s; re-login before native dispatch" % transport, None
    if status in provider_capacity.UNREADABLE:
        return None, _unreadable_warning(transport, value)
    return "Provider capacity status unknown for %s: %r" % (transport, status), None
