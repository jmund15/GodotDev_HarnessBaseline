#!/usr/bin/env python3
"""Read OpenCode Go's account-wide quota into the shared capacity schema."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import calendar
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from provider_capacity import http_failure  # noqa: E402
from quota_bands import (  # noqa: E402
    EXHAUSTED_BAND, compute_band, dormant_band, most_constrained_window, runout_ratio_for,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEFAULT_URL = "https://opencode.ai/zen/go/v1/usage"
# (payload key, normalized name, window minutes). None is the monthly window, whose length is the
# calendar month that ends at its reset: 28 to 31 days.
WINDOWS = (("rolling", "five_hour", 300), ("weekly", "seven_day", 10080),
           ("monthly", "thirty_day", None))
USABLE_STATUS = "ok"


def outcome(status, *, quota=None, error=None):
    return {"schemaVersion": 1, "transport": "go", "costModel": "plan-quota",
            "sourceKind": "live", "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": status, "error": error, "liveFailure": None, "quota": quota, "balance": None}


def _epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def month_minutes(resets_at):
    """Length in minutes of the calendar month that ends at `resets_at`.

    The window starts on the same day and time one month earlier, clamped to that month's last
    day, so a reset on November 26th covers the 31 days from October 26th.
    """
    end = datetime.fromtimestamp(resets_at, timezone.utc)
    year, month = (end.year, end.month - 1) if end.month > 1 else (end.year - 1, 12)
    start = end.replace(year=year, month=month, day=min(end.day, calendar.monthrange(year, month)[1]))
    return int(round((end - start).total_seconds() / 60))


def summarize(payload, now=None):
    if not isinstance(payload, dict):
        return outcome("malformed", error={"kind": "malformed", "message": "usage response is not an object"})
    payload = payload.get("usage")
    if not isinstance(payload, dict):
        return outcome("malformed", error={"kind": "malformed", "message": "usage envelope missing"})
    now = time.time() if now is None else now
    windows = []
    stopped = None
    for source, name, duration in WINDOWS:
        value = payload.get(source)
        if not isinstance(value, dict):
            return outcome("malformed", error={"kind": "malformed", "message": source + " window missing; keys=" + ",".join(sorted(payload))[:160]})
        used = value.get("percent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            return outcome("malformed", error={"kind": "malformed", "message": source + " window invalid"})
        dormant = dormant_band(used, value.get("resetsAt"))
        resets = None if dormant else _epoch(value.get("resetsAt"))
        if dormant is None and (resets is None or resets <= now):
            return outcome("malformed", error={"kind": "malformed", "message": source + " window invalid"})
        if duration is None:
            duration = month_minutes(resets) if resets is not None else 30 * 24 * 60
        pressure, band = dormant or compute_band(used, resets, duration * 60, now)
        if pressure is None:
            return outcome("malformed", error={"kind": "malformed",
                                              "message": source + " reset lies beyond its own window"})
        ratio = None if dormant else runout_ratio_for(used, resets, duration * 60, now)
        row = {"name": name, "usedPercent": float(used), "resetsAt": resets,
               "windowDurationMins": duration, "pressure": round(pressure, 4), "band": band,
               "runoutRatio": round(ratio, 4) if ratio is not None else None}
        windows.append(row)
        # A spent window, or one the provider itself reports as not ok, stops the plan: Go has no
        # credits to fall back on.
        if stopped is None and (used >= 100 or value.get("status") != USABLE_STATUS):
            stopped = row
    # The BINDING window is the tightest of the three by effective band -- a monthly cap 22 hours
    # from reset outranks a week burning faster -- and the routing band is that window's.
    selected = most_constrained_window(windows)
    binding = stopped or selected
    dispatch_band = EXHAUSTED_BAND if stopped else selected["band"]
    quota = {"windows": windows, "dispatchBand": dispatch_band,
             "routingBand": dispatch_band,
             "routingWindow": binding["name"], "bindingWindow": binding["name"],
             "credits": {"enabled": False, "available": False, "balance": None}}
    return outcome("exhausted" if stopped else "available", quota=quota)


def credential():
    key = os.environ.get("OPENCODE_API_KEY", "")
    if key:
        return key
    path = Path(os.environ.get("OPENCODE_CREDENTIAL_FILE") or Path.home() / ".env.ai-worker.cmd")
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip().lower() == "set opencode_api_key":
                return value.strip().strip('"')
    except OSError:
        pass
    return None


def read_live(url, key, timeout=15):
    if not key:
        return outcome("auth-error", error={"kind": "auth", "message": "OpenCode Go credential is missing"})
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
                                                   "Accept": "application/json",
                                                   "User-Agent": "provider-capacity/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        status, error = http_failure(exc.code, "Go usage endpoint")
        return outcome(status, error=error)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return outcome("network-error", error={"kind": "network", "message": str(exc)[:300]})
    except (ValueError, UnicodeError):
        return outcome("malformed", error={"kind": "malformed", "message": "Go usage response is not JSON"})
    return summarize(payload)


def main():
    print(json.dumps(read_live(os.environ.get("OPENCODE_USAGE_URL") or DEFAULT_URL, credential()),
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
