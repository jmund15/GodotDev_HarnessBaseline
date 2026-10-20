#!/usr/bin/env python3
"""Proof for `scripts/console_credit_audit.py`: one day at a time, the sidecar ledger's list price
against the Console organization's own `cost_report`, with no threshold and no verdict.

The HTTP side is a planted local server (the Go probe's idiom); the credential is a planted
`~/.env.console-audit.cmd` under a scratch HOME. No case reaches the real Admin key, the real
ledger or the network.

    python3 .claude/tests/test_console_credit_audit.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "console_credit_audit.py"
PROBE = ".claude/scripts/console_credit_audit.py"
PLAN = "anthropic-console-secondary"
OTHER = "anthropic-console"
KEY_VAR = "ANTHROPIC_CONSOLE_ADMIN_KEY_SECONDARY"
AUDIT_ENV = ".env.console-audit.cmd"
LEDGER = ".claude/sidecar_ledger.jsonl"
FAKE_KEY = "sk-ant-admin01-PLANTED-FIXTURE-KEY"
DECOY_KEY = "sk-ant-admin01-DECOY-FROM-THE-PROCESS-ENV"


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {}
    calls = []

    def do_GET(self):
        type(self).calls.append({"path": self.path, "headers": dict(self.headers)})
        body = json.dumps(type(self).payload).encode("utf-8") if type(self).status == 200 \
            else json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                        "message": "invalid x-api-key"}}).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def day(delta):
    return (datetime.now(timezone.utc).date() + timedelta(days=delta)).isoformat()


def bucket(day_text, cents, currency="USD"):
    return {"starting_at": day_text + "T00:00:00Z", "ending_at": day_text + "T23:59:59Z",
            "results": [{"amount": cents, "currency": currency, "description": "Claude Code",
                         "cost_type": "tokens"}]}


def ledger_row(cost, at, transport=PLAN):
    return {"timestamp": at, "transport": transport, "costModel": "plan-quota",
            "costBasis": "list-price (plan-quota; not spend)", "costUSD": cost, "exitCode": 0,
            "outcome": "finished-with-record"}


def stamp(day_text, hour=12):
    return "%sT%02d:00:00Z" % (day_text, hour)


def plant(home, *, ledger=None, key=FAKE_KEY, env_file=True, cache=None, plan=PLAN):
    """A scratch HOME: the audit env file, this plan's ledger, and (optionally) a warm cache."""
    (Path(home) / ".claude").mkdir(parents=True, exist_ok=True)
    if env_file:
        (Path(home) / AUDIT_ENV).write_text(
            "set DEEPSEEK_API_KEY=decoy-neighbour\nset %s=%s\n" % (KEY_VAR, key), encoding="utf-8")
    if ledger is not None:
        (Path(home) / LEDGER).write_text(
            "".join(json.dumps(r) + "\n" for r in ledger), encoding="utf-8")
    if cache is not None:
        (Path(home) / ".claude" / "console_credit_audit_cache.json").write_text(
            json.dumps(cache), encoding="utf-8")
    env = dict(os.environ, HOME=str(home))
    env.pop("SIDECAR_LEDGER_PATH", None)
    return env


def run(env, url, *argv):
    result = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60,
                            env=dict(env, ANTHROPIC_CONSOLE_COST_REPORT_URL=url))
    return result


def tally(result):
    """(days, {day: (ledger, report, difference)}): numbers an audited row printed."""
    rows, values = [], {}
    for line in result.stdout.splitlines():
        match = re.match(r"^(\d{4}-\d{2}-\d{2})\tledger ([-\d.]+)\treport ([-\d.]+)"
                         r"\tdifference ([+\-][\d.]+)$", line)
        if match:
            rows.append(match.group(1))
            values[match.group(1)] = tuple(float(match.group(i)) for i in (2, 3, 4))
    return rows, values


def scratch():
    return tempfile.mkdtemp(prefix="console_credit_audit_")


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), "" if ok else str(detail)[:400]))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_port
    try:
        # ---- a matching day ------------------------------------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00")], "has_more": False, "next_page": None}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))]), url)
        rows, values = tally(result)
        case("a day whose ledger sum and cost_report sum agree prints a zero difference",
             result.returncode == 0 and day(-1) in values
             and values[day(-1)] == (0.44, 0.44, 0.0), result.stdout + result.stderr)

        # ---- a differing day ------------------------------------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "40.00"), bucket(day(-2), "100.00")],
                           "has_more": False, "next_page": None}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1))),
                                         ledger_row(1.25, stamp(day(-2))),
                                         ledger_row(9.99, stamp(day(-2)), transport=OTHER)]), url)
        rows, values = tally(result)
        case("each day prints its own ledger sum, report sum and their signed difference "
             "(ledger minus report)",
             result.returncode == 0 and values.get(day(-1)) == (0.44, 0.40, 0.04)
             and values.get(day(-2)) == (1.25, 1.00, 0.25), result.stdout + result.stderr)
        case("the ledger side is this plan's rows only: another Console plan's spend is not counted",
             values.get(day(-2), (0,))[0] == 1.25, result.stdout + result.stderr)
        case("no threshold and no verdict: the audit never grades the difference it prints",
             result.stdout.startswith(PLAN + ": sidecar ledger")
             and not re.search(r"\b(mismatch|within tolerance|FAIL|WARN|OK|verdict|too high|too low)\b",
                               result.stdout, re.I), result.stdout)

        # ---- the request itself ---------------------------------------------------------------
        sent = Handler.calls[-1] if Handler.calls else {"path": "", "headers": {}}
        params = ({k: v[0] for k, v in urllib.parse.parse_qs(sent["path"].split("?", 1)[1]).items()}
                  if "?" in sent["path"] else {})
        headers = {k.lower(): v for k, v in sent["headers"].items()}
        case("the report is asked for finished UTC days only, ending at today's midnight",
             params.get("starting_at") == day(-7) + "T00:00:00Z"
             and params.get("ending_at") == day(0) + "T00:00:00Z"
             and day(0) not in tally(result)[0], sent["path"] + repr(tally(result)[0]))
        case("the call carries the planted key as x-api-key and an Anthropic version header",
             headers.get("x-api-key") == FAKE_KEY and headers.get("anthropic-version")
             and sent["path"].startswith("/v1/organizations/cost_report"), sent["path"] + repr(headers))
        case("the key is never printed, in any run",
             bool(result.stdout.strip())
             and FAKE_KEY not in (result.stdout + result.stderr), result.stdout)

        # ---- the credential is the audit file, and only it -------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00")], "has_more": False, "next_page": None}
        env = plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))])
        env[KEY_VAR] = DECOY_KEY                      # a process-env key must not win
        result = run(env, url)
        headers = {k.lower(): v for k, v in (Handler.calls[-1]["headers"] if Handler.calls else {}).items()}
        case("the key comes from the audit file alone: a process-environment copy is not used",
             headers.get("x-api-key") == FAKE_KEY and DECOY_KEY not in (result.stdout + result.stderr),
             repr(headers.get("x-api-key")))
        missing = scratch()
        calls_before = len(Handler.calls)
        result = run(plant(missing, env_file=False, ledger=[ledger_row(0.44, stamp(day(-1)))]), url)
        case("a missing audit file is refused by name, with no call made and no key invented",
             result.returncode == 3 and AUDIT_ENV in (result.stdout + result.stderr)
             and KEY_VAR in (result.stdout + result.stderr)
             and len(Handler.calls) == calls_before, result.stdout + result.stderr)

        # ---- a 401 is unavailable, never a zero difference --------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 401, []
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))]), url)
        rows, values = tally(result)
        case("a 401 reports the day unavailable and prints no difference at all",
             result.returncode == 3 and not values and day(-1) in result.stdout
             and "unavailable" in result.stdout and "401" in result.stdout
             and "0.00" not in result.stdout.split("unavailable")[1], result.stdout + result.stderr)

        # ---- the once-per-UTC-day cache ---------------------------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00")], "has_more": False, "next_page": None}
        env = plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))])
        first = run(env, url)
        calls_after_first = len(Handler.calls)
        second = run(env, url)
        calls_after_second = len(Handler.calls)
        third = run(env, url, "--refresh")
        _, first_values = tally(first)
        _, second_values = tally(second)
        case("a second run inside the same UTC day reuses the recorded report",
             calls_after_first == 1 and calls_after_second == 1
             and first_values == second_values and second.stdout == first.stdout,
             "%d then %d calls, %s" % (calls_after_first, calls_after_second, second.stdout))
        case("--refresh asks again", len(Handler.calls) == 2 and third.stdout == first.stdout,
             "%d calls" % len(Handler.calls))
        cache_text = read_text(Path(home) / ".claude" / "console_credit_audit_cache.json")
        case("the cache records the report and the day it was taken, never the key",
             "fetchedOn" in cache_text and '"data"' in cache_text and FAKE_KEY not in cache_text,
             cache_text[:200])

        home = scratch()
        Handler.status, Handler.calls = 200, []
        stale = {"anthropic-console-secondary": {
            "fetchedOn": "2000-01-01", "from": day(-7), "to": day(0),
            "payload": {"data": [bucket(day(-1), "999.00")]}}}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))], cache=stale), url)
        _, values = tally(result)
        case("a cache recorded on an earlier UTC day is not reused",
             len(Handler.calls) == 1 and values.get(day(-1)) == (0.44, 0.44, 0.0),
             "%d calls %s" % (len(Handler.calls), result.stdout))

        # ---- the window and the ledger side ------------------------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00"), bucket(day(-5), "25.00"),
                                    bucket(day(-30), "700.00")], "has_more": False, "next_page": None}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1))),
                                         ledger_row(0.25, stamp(day(-5)),
                                                    transport=PLAN),
                                         ledger_row(0.01, stamp(day(-1), hour=23))]), url)
        rows, values = tally(result)
        case("a bucket outside the window is not audited; two rows on one day sum into that day",
             day(-30) not in values and values.get(day(-1)) == (0.45, 0.44, 0.01)
             and values.get(day(-5)) == (0.25, 0.25, 0.0), result.stdout + result.stderr)
        case("a --days window widens the query and the days it audits",
             run(plant(scratch(), ledger=[ledger_row(0.44, stamp(day(-20)))]), url, "--days", "30")
             .returncode == 0
             and day(-20) in tally(run(plant(scratch(), ledger=[ledger_row(0.44, stamp(day(-20)))]),
                                       url, "--days", "30"))[1], "")

        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00", currency="EUR")],
                           "has_more": False, "next_page": None}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))]), url)
        rows, values = tally(result)
        case("a bucket that is not in USD is refused as unavailable, never summed as dollars",
             result.returncode == 3 and not values and "EUR" in result.stdout,
             result.stdout + result.stderr)

        # ---- the plan scope, the CLI and the shape ----------------------------------------------
        home = scratch()
        Handler.status, Handler.calls = 200, []
        calls_before = len(Handler.calls)
        result = run(plant(home, ledger=[]), url, "--plan", OTHER)
        case("the audit is the secondary's: naming the primary plan refuses (D2 leaves it no Admin key)",
             result.returncode == 2 and OTHER in (result.stdout + result.stderr)
             and len(Handler.calls) == calls_before, result.stdout + result.stderr)
        result = run(plant(home, ledger=[]), url, "--nonsense")
        case("an unknown flag exits 2 with its usage",
             result.returncode == 2 and "usage" in (result.stdout + result.stderr).lower(),
             result.stdout + result.stderr)
        calls_before = len(Handler.calls)
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))]), url, "--days", "0")
        case("a zero-day window exits 2 rather than auditing nothing",
             result.returncode == 2 and "usage" in (result.stdout + result.stderr).lower()
             and len(Handler.calls) == calls_before, result.stdout + result.stderr)

        home = scratch()
        Handler.status, Handler.calls = 200, []
        Handler.payload = {"data": [bucket(day(-1), "44.00"), bucket(day(-2), "100.00")],
                           "has_more": False, "next_page": None}
        result = run(plant(home, ledger=[ledger_row(0.44, stamp(day(-1)))]), url, "--json")
        try:
            blob = json.loads(result.stdout)
        except ValueError:
            blob = {}
        days = {d.get("day"): d for d in blob.get("days") or []}
        case("--json carries the same three numbers per day, with no verdict field",
             result.returncode == 0 and days.get(day(-1), {}).get("ledgerUSD") == 0.44
             and days.get(day(-1), {}).get("reportUSD") == 0.44
             and days.get(day(-2), {}).get("ledgerUSD") == 0.0
             and set(blob) == {"plan", "transport", "from", "to", "source", "days"}
             and not any("verdict" in k.lower() for d in days.values() for k in d), result.stdout)

        # ---- the Admin key has exactly one reader ------------------------------------------------
        case("the audit script itself is the proof's positive control for the scan below",
             SCRIPT.is_file() and AUDIT_ENV in SCRIPT.read_text(encoding="utf-8"), PROBE)
        planted = Path(scratch()) / "planted_reference.txt"
        planted.write_text("reads " + AUDIT_ENV + "\n", encoding="utf-8")
        case("the scan finds a planted reference (the instrument is live)",
             str(planted) in scan_for_audit_file([str(planted)]), str(planted))
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=120)
        files = [str(ROOT / line) for line in tracked.stdout.splitlines() if line.strip()]
        # The script this slice adds is untracked until the dispatcher commits it, so the scan
        # covers it explicitly; every OTHER path it covers is tracked.
        hits = scan_for_audit_file([f for f in files + [str(SCRIPT)] if Path(f).is_file()])
        allowed = {str(SCRIPT), str(Path(__file__).resolve())}
        case("no tracked path but the audit script names the Admin key's file",
             tracked.returncode == 0 and hits and all(h in allowed for h in hits)
             and str(SCRIPT) in hits, repr(sorted(hits)) + " of %d tracked" % len(files))

        # ---- the auth surface --------------------------------------------------------------------
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.is_file() else ""
        case("the audit makes no /v1/messages call and never prints the key",
             "v1/messages" not in source and "scrub" in source.lower(), source[:200])
    finally:
        server.shutdown()
        server.server_close()

    bad = [name for name, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % detail))
    print("\n%d/%d passed" % (len(cases) - len(bad), len(cases)))
    return 1 if bad else 0


def read_text(path):
    """A planted file's text, or "" when the script wrote nothing at all."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan_for_audit_file(paths):
    """The paths among `paths` whose text names the audit env file."""
    hits = []
    for path in paths:
        try:
            if AUDIT_ENV in Path(path).read_text(encoding="utf-8", errors="replace"):
                hits.append(str(path))
        except OSError:
            continue
    return hits


if __name__ == "__main__":
    raise SystemExit(main())
