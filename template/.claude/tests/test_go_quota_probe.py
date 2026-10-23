#!/usr/bin/env python3
"""Exercise the Go usage adapter through a planted HTTP endpoint."""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "go_quota_probe.py"
sys.path.insert(0, str(ROOT / "tools"))
from provider_capacity import validate_reading  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {}
    headers_seen = {}

    def do_GET(self):
        type(self).headers_seen = dict(self.headers)
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def run(url, key="test-go-key"):
    env = dict(os.environ, OPENCODE_USAGE_URL=url, OPENCODE_API_KEY=key)
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=20)
    try:
        reading = json.loads(result.stdout)
    except ValueError:
        reading = None
    return result, reading


def headroom_cases():
    import importlib.util
    spec = importlib.util.spec_from_file_location("go_quota_probe_headroom", SCRIPT)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    now = 1_800_000_000

    def reading(five, weekly, monthly):
        return probe.summarize({"usage": {
            source: {"status": "ok", "percent": used, "resetsAt": now + remaining}
            for source, (used, remaining) in zip(
                ("rolling", "weekly", "monthly"), (five, weekly, monthly))
        }}, now=now)

    near = reading((10, 4 * 3600), (99, 22 * 3600), (10, 15 * 86400))
    quota = near.get("quota") or {}
    week = next((w for w in quota.get("windows") or [] if w.get("name") == "seven_day"), {})
    ratio = week.get("runoutRatio")
    monthly = reading((10, 4 * 3600), (80, 2 * 86400), (99, 22 * 3600))
    month_quota = monthly.get("quota") or {}
    month = next((w for w in month_quota.get("windows") or [] if w.get("name") == "thirty_day"), {})
    def summarize_at(at, windows):
        return probe.summarize({"usage": {
            source: dict({"status": "ok"}, **fields) for source, fields in windows.items()
        }}, now=at)

    def iso(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    # A monthly window ending on the 26th of a 31-day month started 31 days earlier.
    oct_now = 1_793_044_800  # 2026-10-26T20:00:00Z
    long_month = summarize_at(oct_now, {
        "rolling": {"percent": 5, "resetsAt": iso(oct_now + 3600)},
        "weekly": {"percent": 5, "resetsAt": iso(oct_now + 3 * 86400)},
        "monthly": {"percent": 5, "resetsAt": "2026-11-26T19:26:57Z"},
    })
    long_rows = {w.get("name"): w for w in (long_month.get("quota") or {}).get("windows") or []}
    live = {"rolling": {"percent": 10, "resetsAt": iso(now + 4 * 3600)},
            "weekly": {"percent": 20, "resetsAt": iso(now + 3 * 86400)},
            "monthly": {"percent": 30, "resetsAt": iso(now + 15 * 86400)}}
    limited = summarize_at(now, dict(live, weekly=dict(live["weekly"], status="rate_limited")))
    unstated_usage = {source: dict({"status": "ok"}, **fields) for source, fields in live.items()}
    del unstated_usage["weekly"]["status"]
    unstated = probe.summarize({"usage": unstated_usage}, now=now)
    dormant = {"percent": 0, "resetsAt": None}
    beside = summarize_at(now, dict(live, rolling=dormant))
    beside_five = next((w for w in (beside.get("quota") or {}).get("windows") or []
                        if w.get("name") == "five_hour"), {})
    fresh = summarize_at(now, {"rolling": dormant, "weekly": dormant, "monthly": dormant})
    fresh_quota = fresh.get("quota") or {}
    active_no_reset = summarize_at(now, dict(live, rolling={"percent": 3, "resetsAt": None}))
    expired = summarize_at(now, dict(live, rolling={"percent": 3, "resetsAt": iso(now - 60)}))
    return [
        ("a monthly reset closing a 31-day month is a valid 31-day window, not malformed",
         long_month.get("status") == "available"
         and long_rows.get("thirty_day", {}).get("windowDurationMins") == 31 * 24 * 60
         and not validate_reading(long_month, "go", "plan-quota")),
        ("a window whose own status is not ok is not available",
         limited.get("status") not in ("available", None)
         and not validate_reading(limited, "go", "plan-quota")),
        ("a window without a status is not read as ok",
         unstated.get("status") not in ("available", None)),
        ("a dormant Go window is a Surplus row at pressure 0 beside live windows",
         beside.get("status") == "available" and beside_five.get("band") == "Surplus"
         and beside_five.get("pressure") == 0.0
         and not validate_reading(beside, "go", "plan-quota")),
        ("a fresh Go plan with every window dormant is available at Surplus",
         fresh.get("status") == "available" and fresh_quota.get("dispatchBand") == "Surplus"
         and fresh_quota.get("routingBand") == "Surplus"
         and not validate_reading(fresh, "go", "plan-quota")),
        ("positive Go use without a reset stays malformed",
         active_no_reset.get("status") == "malformed"),
        ("an expired Go reset yields no band",
         expired.get("status") == "malformed"),
        ("Go weekly headroom binds a 99%-used week 22 hours before reset",
         quota.get("routingBand") == "Hot" and quota.get("routingWindow") == "seven_day"
         and quota.get("bindingWindow") == "seven_day"
         and isinstance(ratio, (int, float)) and 0.065 <= ratio <= 0.070),
        ("Go monthly headroom binds despite a lower raw pace than the week",
         month_quota.get("routingBand") == "Hot"
         and month_quota.get("routingWindow") == "thirty_day"
         and month_quota.get("bindingWindow") == "thirty_day"
         and month.get("pressure", 2) < next((w.get("pressure") for w in month_quota.get("windows") or []
                                              if w.get("name") == "seven_day"), 0)),
    ]


def cap_states(rows):
    """The monthly caps the rows state, `"unlimited"` for an explicitly uncapped row.

    `None` when a row states no cap at all, which is the defect this case exists for: the refresh
    (`scripts/opencode_refresh_models.py:470`) writes `{monthly: <n>}` or
    `{monthly: None, unlimited: True}` for EVERY Go id and refuses a caps file that omits one, so a
    row without a cap means the roster was hand-edited or written by an older generator.
    """
    states = set()
    for row in rows:
        cap = row.get("planCapUSD")
        if not isinstance(cap, dict):
            return None
        monthly = cap.get("monthly")
        if isinstance(monthly, (int, float)) and not isinstance(monthly, bool) and monthly > 0:
            states.add(monthly)
        elif monthly is None and cap.get("unlimited") is True:
            states.add("unlimited")
        else:
            return None
    return states


def main():
    cases = []
    registry = json.loads((ROOT / "reference" / "external_models.json").read_text(encoding="utf-8"))
    go_rows = [row for row in registry["models"] if row["transport"] == "go"]
    probe = registry["transports"]["go"].get("capacityProbe") or {}
    cases.append(("Go quota probe is registered within the 45-second check budget",
                  probe.get("kind") == "quota" and probe.get("args") == [".claude/scripts/go_quota_probe.py"]
                  and probe.get("timeoutSeconds", 100) <= 20))
    caps = cap_states(go_rows)
    cases.append(("every Go row carries its monthly cap as data, never an absent or malformed one",
                  bool(go_rows) and caps is not None))
    cases.append(("the caps are the documented Go plan's tiers: $15, $30, $60, or marked unlimited",
                  caps is not None and {15, 30, 60, "unlimited"} >= caps >= {15, 30, 60}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/zen/go/v1/usage" % server.server_port
    reset = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    try:
        Handler.payload = {"usage": {name: {"status": "ok", "percent": percent, "resetsAt": reset}
                                     for name, percent in (("rolling", 20), ("weekly", 10), ("monthly", 95))}}
        healthy, reading = run(url)
        cases.append(("200 is a valid quota reading", healthy.returncode == 0 and reading is not None
                      and reading.get("status") == "available"
                      and not validate_reading(reading, "go", "plan-quota")))
        if reading:
            quota = reading.get("quota") or {}
            windows = {w["name"]: w for w in quota.get("windows", [])}
            cases.append(("all Go windows map to their durations and used percentages",
                          {name: (windows[name]["windowDurationMins"], windows[name]["usedPercent"])
                           for name in windows if name != "thirty_day"}
                          == {"five_hour": (300, 20.0), "seven_day": (10080, 10.0)}
                          and windows["thirty_day"]["usedPercent"] == 95.0
                          and windows["thirty_day"]["windowDurationMins"] in (40320, 41760,
                                                                               43200, 44640)))
            cases.append(("monthly pressure binds routing", quota.get("bindingWindow") == "thirty_day"
                          and quota.get("routingBand") == windows["thirty_day"].get("band")))
        headers = {name.lower(): value for name, value in Handler.headers_seen.items()}
        cases.append(("usage call uses Go's account endpoint credential",
                      headers.get("authorization") == "Bearer test-go-key"))
        Handler.payload["usage"]["monthly"]["percent"] = 100
        _, spent = run(url)
        cases.append(("a spent window exhausts the transport", spent is not None
                      and spent.get("status") == "exhausted"
                      and spent.get("quota", {}).get("dispatchBand") == "Exhausted"
                      and not validate_reading(spent, "go", "plan-quota")))
        Handler.payload = {"rolling": {"status": "ok", "percent": "bad", "resetsAt": reset}}
        _, malformed = run(url)
        cases.append(("malformed usage is typed, never zero", malformed is not None
                      and malformed.get("status") == "malformed"
                      and malformed.get("error", {}).get("kind") == "malformed"
                      and not validate_reading(malformed, "go", "plan-quota")))
        Handler.status = 401
        _, denied = run(url)
        cases.append(("401 is a typed auth error", denied is not None
                      and denied.get("status") == "auth-error"
                      and not validate_reading(denied, "go", "plan-quota")))
        Handler.status = 429
        _, limited = run(url)
        cases.append(("429 is a typed rate-limit outcome", limited is not None
                      and limited.get("status") == "network-error"
                      and limited.get("error", {}).get("kind") == "rate-limit"
                      and not validate_reading(limited, "go", "plan-quota")))
    finally:
        server.shutdown()
        server.server_close()
    cases.extend(headroom_cases())
    for label, passed in cases:
        print(("ok   " if passed else "FAIL ") + label)
    print("%d/%d passed" % (sum(p for _, p in cases), len(cases)))
    return int(not all(p for _, p in cases))


if __name__ == "__main__":
    raise SystemExit(main())
