"""The one Python reader of the sidecar usage-limit marker files.

`sidecar_common.sh` (`sc_exhausted_marker`) writes them and resolves the same directory in shell, which cannot
import this: keep its default path in step. Layout: <dir>/<transport>.json, and for one pool member
<dir>/members/<transport>/<member>.json."""
import json
import sys
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


def lift_if_room(transport, data=None, now=None, runner=None):
    """Delete `transport`'s live usage-limit marker when a reading taken after it says the plan has room.

    The marker records a rejection at its `writtenAt`; an owner's manual quota reset or top-up leaves
    it standing until its expiry. A reading observed after that moment is the newer fact, so the marker
    yields to it: only a live (not cooldown-fallback) `available` reading counts, a failed or rate-limited
    read keeps the refusal, and a rate-limited endpoint is not probed again before its cooldown ends
    (`provider_capacity.reading` owns that). An auth marker, an expired one and a pool member's are not
    this question's. Returns True when the marker was deleted.
    """
    import time
    import provider_capacity
    now = time.time() if now is None else now
    marker = read(transport)
    if marker is None or marker.get("reason") == "auth":
        return False
    written, expires = marker.get("writtenAt"), marker.get("expiresAt")
    if (isinstance(written, bool) or not isinstance(written, (int, float))
            or isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires <= now):
        return False
    try:
        got = provider_capacity.reading(transport, data=data, now=now, runner=runner, newer_than=written)
    except provider_capacity.CapacityError:
        return False
    observed = provider_capacity._epoch(got.get("observedAt"))
    if (got.get("status") != "available" or got.get("liveFailure") is not None
            or observed is None or observed < written):
        return False
    again = read(transport)
    if again is None or again.get("writtenAt") != written:
        return False
    try:
        marker_path(transport).unlink()
    except OSError:
        return False
    return True


def main(argv):
    if len(argv) == 2 and argv[0] == "lift":
        return 0 if lift_if_room(argv[1]) else 1
    print("usage: exhausted_marker.py lift <transport>  (exit 0 when the marker was lifted)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
