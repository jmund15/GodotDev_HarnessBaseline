"""A planted provider-capacity cache: one fresh `available` reading per probed transport, so a proof of a cache
reader (`for-role`, a guard, a prompt hook) never touches the machine's cache or starts a probe."""
import json
import time


def available_reading(transport, cfg, now):
    """A fresh `available` reading for `transport` (registry entry `cfg`) at epoch `now`."""
    entry = {"schemaVersion": 1, "transport": transport, "costModel": cfg.get("costModel"), "sourceKind": "live",
             "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "status": "available",
             "error": None, "liveFailure": None, "quota": None, "balance": None}
    if cfg.get("costModel") == "plan-quota":
        # What a real probe emits for 10% of a week used at half-week: pace 0.2, run-out 9.0.
        entry["quota"] = {"windows": [{"name": "seven_day", "usedPercent": 10.0, "resetsAt": now + 302400,
                                       "windowDurationMins": 10080, "pressure": 0.2, "band": "Surplus",
                                       "runoutRatio": 9.0}],
                          "dispatchBand": "Surplus", "routingBand": "Surplus", "routingWindow": "seven_day",
                          "bindingWindow": "seven_day", "credits": None}
    else:
        entry["balance"] = {"amount": 100.0, "currency": "USD"}
    return entry


def write_capacity_cache(path, data):
    """Write one `available_reading` per probed transport of registry `data` to `path`, and return `path`."""
    now = time.time()
    blob = {name: available_reading(name, cfg, now) for name, cfg in (data.get("transports") or {}).items()
            if isinstance(cfg, dict) and cfg.get("capacityProbe")}
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    return path


def anthropic_payload(five=None, seven=None):
    """A planted OAuth usage response (`/api/oauth/usage`): a window is present when its used percent is given."""
    payload = {}
    if five is not None:
        payload["five_hour"] = {"utilization": five, "resets_at": "2026-11-23T18:00:00Z"}
    if seven is not None:
        payload["seven_day"] = {"utilization": seven, "resets_at": "2026-11-29T18:00:00Z"}
    return payload


def agy_usage_text(five_left=50.0, weekly_left=50.0, other_bucket_only=False):
    """Planted `agy -p /usage` stdout: tab rows `<bucket>\\t<label>\\t<N>% left\\t<reset>` for the Antigravity bucket."""
    bucket = "Claude and GPT models" if other_bucket_only else "Gemini Models"
    return (f"{bucket}\tFive Hour\t{five_left:.1f}%\t2026-11-23T18:00:00Z\n"
            f"{bucket}\tWeekly\t{weekly_left:.1f}%\t2026-11-29T18:00:00Z\n")
