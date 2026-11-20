"""The one Python reader of the sidecar usage-limit marker files.

`sidecar_common.sh` (`sc_exhausted_marker`) writes them and resolves the same directory in shell, which cannot
import this: keep its default path in step. Layout: <dir>/<transport>.json, and for one pool member
<dir>/members/<transport>/<member>.json."""
import json
import os
from pathlib import Path


def exhausted_dir():
    return Path(os.environ.get("SIDECAR_EXHAUSTED_DIR") or Path.home() / ".claude" / "sidecar-exhausted")


def marker_path(transport, member=None):
    base = exhausted_dir()
    return base / "members" / transport / f"{member}.json" if member else base / f"{transport}.json"


def superseded(marker, capacity):
    """True when a provider reading observed after the marker was written shows every window under 100% used:
    the limit lifted early (an owner's banked reset), so the marker's 30-minute default no longer holds.
    An auth marker never lifts this way: quota room says nothing about a failed login."""
    from datetime import datetime
    if not isinstance(marker, dict) or not isinstance(capacity, dict) or marker.get("reason") == "auth":
        return False
    try:
        written = float(marker["writtenAt"])
        observed = datetime.fromisoformat(str(capacity["observedAt"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError):
        return False
    windows = (capacity.get("quota") or {}).get("windows") or []
    used = [w.get("usedPercent") for w in windows if isinstance(w, dict)]
    return (observed > written and bool(used)
            and all(isinstance(u, (int, float)) and not isinstance(u, bool) and u < 100 for u in used))


def read(transport, member=None):
    """The marker's dict, or None when it is missing, unreadable or not an object."""
    try:
        with open(marker_path(transport, member), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
