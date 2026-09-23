#!/usr/bin/env python3
"""Read Anthropic's live OAuth usage into the shared provider-capacity schema."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_bands import EXHAUSTED_BAND, compute_band, worst_of  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TRANSPORT = "anthropic"
COST_MODEL = "plan-quota"
DEFAULT_URL = "https://api.anthropic.com/api/oauth/usage"


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def outcome(status, *, quota=None, error=None):
    return {
        "schemaVersion": 1,
        "transport": TRANSPORT,
        "costModel": COST_MODEL,
        "sourceKind": "live",
        "observedAt": _utc(),
        "status": status,
        "error": error,
        "liveFailure": None,
        "quota": quota,
        "balance": None,
    }


def _window(name, payload, duration_minutes, now):
    if not isinstance(payload, dict):
        return None
    used = payload.get("utilization")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return None
    resets = _epoch(payload.get("resets_at"))
    pressure, band = compute_band(used, resets, duration_minutes * 60, now)
    return {
        "name": name,
        "usedPercent": float(used),
        "resetsAt": resets,
        "windowDurationMins": duration_minutes,
        "pressure": round(pressure, 4) if pressure is not None else None,
        "band": band,
    }


def summarize(payload, now=None):
    if not isinstance(payload, dict):
        return outcome("malformed", error={"kind": "malformed", "message": "response is not an object"})
    now = time.time() if now is None else now
    windows = [w for w in (
        _window("five_hour", payload.get("five_hour"), 300, now),
        _window("seven_day", payload.get("seven_day"), 10080, now),
    ) if w is not None]
    if not windows:
        return outcome("malformed", error={"kind": "malformed", "message": "usage windows are missing"})
    pressure, band = worst_of((w["pressure"], w["band"]) for w in windows)
    weekly = next((w for w in windows if w["name"] == "seven_day" and w["band"]), None)
    extra = payload.get("extra_usage") or {}
    credit_available = bool(extra.get("is_enabled") and not extra.get("user_disabled")
                            and not extra.get("spend_limit_reached"))
    active_limits = []
    for row in payload.get("limits") or []:
        if not isinstance(row, dict):
            continue
        active_limits.append({
            "kind": row.get("kind"), "percent": row.get("percent"),
            "resetsAt": _epoch(row.get("resets_at")), "active": bool(row.get("is_active")),
            "scope": row.get("scope"),
        })
    spent_window = next((w for w in windows if w["usedPercent"] >= 100), None)
    spent_limit = next((row for row in active_limits
                        if row["active"] and isinstance(row.get("percent"), (int, float))
                        and row["percent"] >= 100), None)
    exhausted = bool((spent_window or spent_limit) and not credit_available)
    binding = spent_window or next((w for w in windows if w["band"] == band
                                    and w["pressure"] == pressure), windows[0])
    dispatch_band = EXHAUSTED_BAND if exhausted else band
    routing_band = weekly["band"] if weekly else dispatch_band
    quota = {
        "windows": windows,
        "dispatchBand": dispatch_band,
        "routingBand": routing_band,
        "bindingWindow": binding["name"],
        "credits": {"enabled": bool(extra.get("is_enabled")),
                    "available": credit_available, "balance": None},
        "scopes": active_limits,
    }
    return outcome("exhausted" if exhausted else "available", quota=quota)


def credential(path=None):
    path = path or os.environ.get("ANTHROPIC_CREDENTIALS_PATH") or str(Path.home() / ".claude" / ".credentials.json")
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        token = (data.get("claudeAiOauth") or {}).get("accessToken")
        return token if isinstance(token, str) and token else None
    except Exception:
        return None


def read_live(url, token, timeout=15):
    if not token:
        return outcome("auth-error", error={"kind": "auth", "message": "Anthropic OAuth credential is missing"})
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
        "User-Agent": "provider-capacity/1",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return outcome("auth-error", error={"kind": "auth", "message": "Anthropic authentication failed"})
        return outcome("network-error", error={
            "kind": "server" if exc.code >= 500 else "network",
            "message": "Anthropic usage endpoint returned HTTP %d" % exc.code,
        })
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return outcome("network-error", error={"kind": "network", "message": str(exc)[:300]})
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return outcome("malformed", error={"kind": "malformed", "message": "Anthropic usage response is not JSON"})
    return summarize(payload)


def main():
    url = os.environ.get("ANTHROPIC_USAGE_URL") or DEFAULT_URL
    print(json.dumps(read_live(url, credential()), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
