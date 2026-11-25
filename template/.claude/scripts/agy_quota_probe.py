#!/usr/bin/env python3
"""Read one Google AI Pro quota bucket from the Antigravity CLI into the shared capacity schema.

    agy_quota_probe.py <transport>

The installed `agy` prints its own `/usage` table headless with no model call: one tab-separated line per
window, `<bucket>\t<window label>\t<N>%\t<reset ISO>`. The CLI holds the login; this reads only its stdout.
`MSYS_NO_PATHCONV=1` keeps Git Bash from rewriting `/usage` into a file path. Each transport is one bucket
(BUCKETS): the plan's buckets are separate allowances, so each is its own paying plan. A bucket untouched
this window reports a 1970 reset; with nothing used that is a dormant window (`quota_bands.dormant_band`).
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from quota_bands import EXHAUSTED_BAND, compute_band, dormant_band, most_constrained_window, runout_ratio_for  # noqa: E402
from agy_seat import agy_bin  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BUCKETS = {"antigravity": "Gemini Models", "antigravity-claude": "Claude and GPT models",
           "antigravity-argon": "Gemini 4 Argon"}
# window label word -> (normalized name, window minutes)
WINDOWS = (("Five Hour", "five_hour", 300), ("Weekly", "seven_day", 10080))


def outcome(transport, status, *, quota=None, error=None):
    return {"schemaVersion": 1, "transport": transport, "costModel": "plan-quota", "sourceKind": "live",
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": status, "error": error,
            "liveFailure": None, "quota": quota, "balance": None}


def _epoch(text):
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return value if value > 0 else None


def summarize(transport, text, now=None):
    bucket = BUCKETS[transport]
    now = time.time() if now is None else now
    windows = []
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < 4 or cells[0] != bucket:
            continue
        if cells[2].lower() == "disabled":
            # agy prints `disabled` for a window the plan is not running (live 2026-11-24: the Claude
            # bucket's five-hour row). It constrains nothing, so it is skipped; any other value that
            # is not a percentage is still an unknown row and stays malformed.
            continue
        match = next(((name, mins) for word, name, mins in WINDOWS if word in cells[1]), None)
        try:
            used = 100.0 - float(cells[2].rstrip("%")) if cells[2].endswith("%") else None
        except ValueError:
            used = None
        if match is None or used is None:  # a window this reader does not know is never full headroom
            return outcome(transport, "malformed", error={"kind": "malformed", "message": "unknown row: " + line[:160]})
        resets = _epoch(cells[3])
        if resets is False or not 0 <= used <= 100:
            return outcome(transport, "malformed", error={"kind": "malformed", "message": "bad row: " + line[:160]})
        name, duration = match
        dormant = dormant_band(used, resets)
        if dormant is None and (resets is None or resets <= now):
            return outcome(transport, "malformed", error={"kind": "malformed", "message": "no live reset: " + line[:160]})
        pressure, band = dormant or compute_band(used, resets, duration * 60, now)
        if pressure is None:
            return outcome(transport, "malformed", error={"kind": "malformed", "message": "reset beyond window: " + line[:160]})
        ratio = None if dormant else runout_ratio_for(used, resets, duration * 60, now)
        windows.append({"name": name, "usedPercent": used, "resetsAt": resets, "windowDurationMins": duration,
                        "pressure": round(pressure, 4), "band": band,
                        "runoutRatio": round(ratio, 4) if ratio is not None else None})
    if not windows:
        return outcome(transport, "malformed", error={"kind": "malformed", "message": f"no {bucket} rows in agy /usage"})
    stopped = next((w for w in windows if w["usedPercent"] >= 100), None)
    selected = most_constrained_window(windows)
    binding = stopped or selected
    band = EXHAUSTED_BAND if stopped else selected["band"]
    quota = {"windows": windows, "dispatchBand": band, "routingBand": band, "routingWindow": binding["name"],
             "bindingWindow": binding["name"], "credits": {"enabled": False, "available": False, "balance": None}}
    return outcome(transport, "exhausted" if stopped else "available", quota=quota)


def read_live(transport, timeout=30):
    binary = agy_bin()
    if not os.path.isfile(binary):
        return outcome(transport, "unsupported", error={"kind": "missing", "message": "agy CLI missing: " + binary})
    try:
        run = subprocess.run([binary, "-p", "/usage"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, env=dict(os.environ, MSYS_NO_PATHCONV="1"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return outcome(transport, "network-error", error={"kind": "network", "message": str(exc)[:300]})
    if run.returncode != 0:
        first = ((run.stderr or run.stdout).strip().splitlines() or [""])[0][:200]
        return outcome(transport, "network-error", error={"kind": "cli", "message":
                       "agy /usage exit %d: %s" % (run.returncode, first)})
    return summarize(transport, run.stdout)


def main():
    transport = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SC_TRANSPORT", "antigravity")
    if transport not in BUCKETS:
        print(f"usage: agy_quota_probe.py {{{'|'.join(BUCKETS)}}}", file=sys.stderr)
        return 2
    print(json.dumps(read_live(transport), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
