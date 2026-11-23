#!/usr/bin/env python3
"""One login's usage windows, and whether it can take a launch.

    capacity.py <seat>      a seats.json seat; scripts/seat.sh runs this before each launch
    capacity.py session     the session's own login (~/.claude); /delegate prints it before a fan-out

Prints one line with the 5-hour and 7-day used percent and reset time. Exit codes:
    0   the login can launch
    3   a window is at its exhaust floor (FLOOR): seat.sh refuses the launch
    4   the read failed: the verdict is unknown, so seat.sh warns and launches
    2   no such seat (seat.sh also warns and launches on it)

The read follows the seat's adapter in seats.json. scripts/seat_claude.sh reads the login's OAuth usage
endpoint; scripts/seat_agy.sh runs `agy -p /usage`, whose rows carry the Antigravity buckets. scripts/seat_local.sh
has no usage window: its GPU slot is backend_hold's lock, which queues the launch. A seat is one seats.json row; a CLI
with a usage read is one READS entry and one parser.
"""
import http.client
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "tools"))
from agy_seat import agy_bin  # noqa: E402
import launch_check  # noqa: E402

OK, FLOOR_HIT, UNKNOWN_SEAT, UNREADABLE = 0, 3, 2, 4
# Used percent at which a window refuses a launch: the dispatch floor v1's anthropic_capacity_probe.py enforced.
# A job started there runs out of its window mid-step.
FLOOR = {"five_hour": 98.0, "seven_day": 99.0}
LABEL = {"five_hour": "5-hour", "seven_day": "7-day"}
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
AGY_BUCKET = "Gemini Models"  # the /usage row of the Antigravity seat's bucket
AGY_LABELS = (("Five Hour", "five_hour"), ("Weekly", "seven_day"))


class Unreadable(Exception):
    pass


def parse_anthropic(payload):
    """{window: {used, resets}} for each window the usage payload carries. Unreadable when none is valid."""
    if not isinstance(payload, dict):
        raise Unreadable("usage response is not an object")
    found = {}
    for name in FLOOR:
        window = payload.get(name)
        if window is None:
            continue
        used = window.get("utilization") if isinstance(window, dict) else None
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            raise Unreadable(f"{name} has no valid utilization")
        found[name] = {"used": float(used), "resets": window.get("resets_at")}
    if not found:
        raise Unreadable("usage response carries no window")
    return found


def parse_agy(text, bucket=AGY_BUCKET):
    """{window: {used, resets}} from `agy /usage` rows `<bucket>\\t<label>\\t<N>%\\t<reset>`, where N is the percent left.
    A row this reader does not know is unreadable, never full headroom."""
    found = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < 4 or cells[0] != bucket:
            continue
        name = next((key for word, key in AGY_LABELS if word in cells[1]), None)
        try:
            used = 100.0 - float(cells[2][:-1]) if cells[2].endswith("%") else None
        except ValueError:
            used = None
        if name is None or used is None or not 0 <= used <= 100:
            raise Unreadable("unknown usage row: " + line[:120])
        found[name] = {"used": used, "resets": cells[3]}
    if not found:
        raise Unreadable(f"no {bucket} rows in agy /usage")
    return found


def read_anthropic(row):
    cred = Path(os.path.expanduser(row["configDir"])) / ".credentials.json"
    try:
        token = json.loads(cred.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Unreadable(f"no OAuth token in {cred} ({type(exc).__name__})")
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json", "User-Agent": "capacity/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise Unreadable(f"usage endpoint: {exc}"[:200])
    return parse_anthropic(payload)


def read_agy(row):
    # MSYS_NO_PATHCONV keeps Git Bash from rewriting `/usage` into a file path.
    try:
        run = subprocess.run([agy_bin(), "-p", "/usage"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30, env=dict(os.environ, MSYS_NO_PATHCONV="1"))
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unreadable(f"agy /usage did not run: {exc}"[:200])
    if run.returncode != 0:
        raise Unreadable(f"agy /usage exit {run.returncode}")
    return parse_agy(run.stdout)


READS = {"scripts/seat_claude.sh": read_anthropic, "scripts/seat_agy.sh": read_agy, "scripts/seat_local.sh": lambda row: {}}


def seat_row(name):
    if name == "session":
        return {"adapter": "scripts/seat_claude.sh", "configDir": "~/.claude"}
    return launch_check.seat_rows(CLAUDE).get(name)


def describe(name, windows):
    parts = [f"{LABEL[key]} {windows[key]['used']:.1f}% used, resets {windows[key]['resets'] or 'unknown'}"
             for key in FLOOR if key in windows]
    return f"{name}: " + ("; ".join(parts) or "no usage window")


def last_weekly_reset():
    """Epoch seconds of the session login's last 7-day window reset (its next reset minus a week), or None when
    the read fails or the window carries no reset time. tools/usage_report.py starts its default window here."""
    try:
        resets = read_anthropic(seat_row("session"))["seven_day"]["resets"]
        return datetime.fromisoformat(resets).timestamp() - 7 * 86400
    except (Unreadable, OSError, ValueError, KeyError, TypeError):
        return None


def main(argv):
    if len(argv) != 2:
        print("usage: capacity.py <seat>|session", file=sys.stderr)
        return UNKNOWN_SEAT
    name = argv[1]
    row = seat_row(name)
    if row is None:
        print(f"capacity: no seat '{name}' in seats.json", file=sys.stderr)
        return UNKNOWN_SEAT
    try:
        read = READS.get(row.get("adapter"))
        if read is None:
            raise Unreadable(f"no usage read for adapter {row.get('adapter')}")
        windows = read(row)
    except (OSError, ValueError, KeyError) as exc:
        print(f"{name}: unreadable: {exc}", file=sys.stderr)
        return UNREADABLE
    except Unreadable as exc:
        print(f"{name}: unreadable: {exc}", file=sys.stderr)
        return UNREADABLE
    line = describe(name, windows)
    at_floor = [LABEL[key] for key in FLOOR if key in windows and windows[key]["used"] >= FLOOR[key]]
    if at_floor:
        print(f"{line}; at exhaust floor ({', '.join(at_floor)})")
        return FLOOR_HIT
    print(line)
    return OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
