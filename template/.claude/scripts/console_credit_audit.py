#!/usr/bin/env python3
"""Audit one UTC day at a time: the sidecar ledger's list price against the Console organization's
own `cost_report`.

WHY THIS EXISTS. D1 makes the credit-used number the ledger's `costUSD` for the route after the
owner's anchor, and C3 matched that list price to the Console balance to the cent on ONE sample of
tool-free, single-model calls. The routes dispatch tool-bearing, multi-turn and long-context runs
whose ledger arithmetic nothing has checked against the vendor's own accounting. This prints, for
each finished UTC day, the ledger sum, the `cost_report` sum and their difference (ledger MINUS
report, so a positive number is list price above what the organization was billed). NO threshold and
NO verdict: the difference is the owner's to read, and the plan's Admin key rules retire this key
once every audited day sits within the two sources' rounding.

SECONDARY ONLY (D2). The primary organization is an Individual Org and holds no Admin key, so
`--plan` accepts this plan and nothing else. The key `ANTHROPIC_CONSOLE_ADMIN_KEY_SECONDARY` is read
from `~/.env.console-audit.cmd` and from NOWHERE ELSE -- not from the process environment, which is
the blast radius C9 keeps the key out of -- and it is scrubbed from every printed line, error bodies
from the endpoint included. The only call this program makes is `GET /v1/organizations/cost_report`
for FINISHED days: C2 measured that the endpoint refuses a range ending today's date, so the window
ends at today's midnight and no request ever asks for the current day.

Cached once per UTC day, under `~/.claude/`: the endpoint owns these numbers, they cannot change for
a finished range, and the read is the one call the key makes.

CLI:
    console_credit_audit.py [--days N] [--json] [--refresh] [--plan <plan>]
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The ledger, the home rule and the timestamp parse are the probe's: one reader of the one file the
# Console launchers write (D5), so the audit cannot drift from the number the plan routes on.
import console_credit_probe as probe  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PLAN = "anthropic-console-secondary"
PLAN_ALIASES = {"secondary": PLAN}
KEY_VAR = "ANTHROPIC_CONSOLE_ADMIN_KEY_SECONDARY"
AUDIT_ENV = ".env.console-audit.cmd"
CACHE_FILE = "console_credit_audit_cache.json"
DEFAULT_URL = "https://api.anthropic.com"
COST_REPORT = "/v1/organizations/cost_report"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_DAYS = 7
# One cost_report page holds `limit` daily buckets, and a truncated answer cannot be summed per day,
# so a wider window than this is refused rather than paged.
MAX_DAYS = 31
# `amount` is a decimal string in the currency's smallest unit; for USD that is cents, which is how
# the owner's own panel-reading script (`console_cost_today.py`) divided the total.
USD_PER_UNIT = 0.01
PLACEHOLDERS = ("<redacted>", "your-key-here")


def audit_env_path():
    return os.path.join(probe.home(), AUDIT_ENV)


def cache_path():
    return os.path.join(probe.home(), ".claude", CACHE_FILE)


def admin_key(path=None):
    """(key, error): the Admin key, from the audit file alone.

    The process environment is deliberately not consulted: D3 puts this key in its own file so its
    blast radius is one script (C9 measures what a file sourced into every server process leaks),
    and a program that also honoured an exported copy would reopen exactly that.
    """
    path = path or audit_env_path()
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, ("the audit credential file %s is unreadable: %s (it holds %s alone; the Console "
                      "Admin key is never read from the environment)" % (path, exc, KEY_VAR))
    pattern = re.compile(r"^\s*(?:set\s+)?%s=(.*)$" % re.escape(KEY_VAR), re.I | re.M)
    match = pattern.search(text)
    value = match.group(1).strip().strip('"') if match else ""
    if not value:
        return None, "%s is not populated in %s" % (KEY_VAR, path)
    if value in PLACEHOLDERS or value.startswith("sk-xxx"):
        return None, "%s is still a placeholder in %s" % (KEY_VAR, path)
    return value, None


def scrub(text, key):
    """`text` with the key removed, for anything that may be printed."""
    return text.replace(key, "<KEY>") if key else text


def fetch_report(key, start, end, url=None, timeout=30):
    """(payload, error): one cost_report page for the finished range `start`..`end` (ISO instants)."""
    base = url or os.environ.get("ANTHROPIC_CONSOLE_COST_REPORT_URL") or DEFAULT_URL
    query = urllib.parse.urlencode({"starting_at": start, "ending_at": end, "limit": MAX_DAYS})
    request = urllib.request.Request(
        base.rstrip("/") + COST_REPORT + "?" + query,
        headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                 "accept": "application/json", "User-Agent": "harness-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        detail = re.sub(r"\s+", " ", scrub(body, key)).strip()[:160]
        return None, "HTTP %d%s" % (exc.code, (" (%s)" % detail) if detail else "")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, "unreachable: %s" % scrub(str(exc), key)[:160]
    except (ValueError, UnicodeError):
        return None, "the cost report is not JSON"
    if not isinstance(payload, dict):
        return None, "the cost report is not an object"
    return payload, None


def report_by_day(payload, window):
    """({day: usd}, {day: reason}, error): the report's own numbers, per day inside `window`.

    A day is refused rather than summed when one of its rows is not USD or when the answer is
    truncated (`has_more`): a partial sum would read as a difference the ledger did not cause.
    """
    if payload.get("has_more"):
        return {}, {}, "the cost report returned more pages than one call fetched; narrow --days"
    data = payload.get("data")
    if not isinstance(data, list):
        return {}, {}, "the cost report carries no data array"
    totals, refused = {}, {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        day = str(entry.get("starting_at") or "")[:10]
        if day not in window:
            continue
        results = entry.get("results")
        if not isinstance(results, list):
            continue
        for row in results:
            if not isinstance(row, dict):
                continue
            currency = row.get("currency")
            if isinstance(currency, str) and currency and currency.upper() != "USD":
                refused[day] = "a row is billed in %s" % currency
                continue
            try:
                amount = float(row.get("amount"))
            except (TypeError, ValueError):
                refused[day] = "a row carries no readable amount"
                continue
            totals[day] = totals.get(day, 0.0) + amount * USD_PER_UNIT
    for day in refused:
        totals.pop(day, None)
    return totals, refused, None


def ledger_by_day(path, transport, window):
    """({day: usd}, error): `transport`'s priced rows whose timestamp falls on a day in `window`."""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {}, None
    totals = {}
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                      # a torn line names no plan, so it belongs to nobody
            if not isinstance(row, dict) or row.get("transport") != transport:
                continue
            stamp = probe._epoch(row.get("timestamp"))
            if stamp is None:
                return {}, "a %s ledger row carries no usable timestamp" % transport
            day = time.strftime("%Y-%m-%d", time.gmtime(stamp))
            if day not in window:
                continue
            cost = row.get("costUSD")
            if not probe._finite(cost, 0):
                return {}, "a %s ledger row of %s carries no priced costUSD" % (transport, day)
            totals[day] = totals.get(day, 0.0) + float(cost)
    return totals, None


def load_cache(now=None):
    """(payload, fetchedOn): today's recorded report, or (None, None)."""
    blob, _err = probe._json(cache_path())
    entry = blob.get(PLAN) if isinstance(blob, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return None, None
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))
    if entry.get("fetchedOn") != today:
        return None, None
    return entry["payload"], entry["fetchedOn"]


def store_cache(payload, now=None):
    stamp = time.time() if now is None else now
    blob, _err = probe._json(cache_path())
    blob = blob if isinstance(blob, dict) else {}
    blob[PLAN] = {"fetchedOn": time.strftime("%Y-%m-%d", time.gmtime(stamp)), "payload": payload}
    path = cache_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(blob, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rows_for(ledger, report, refused, error=None):
    """One row per day either source has something for, in day order.

    An unavailable report prints NO difference at all: a 401 or a half-fetched page is not a zero
    difference, and printing 0.0000 for it would read as "the two sources agreed".
    """
    days = sorted(set(ledger) | set(report) | set(refused))
    rows = []
    for day in days:
        ledger_usd = round(ledger.get(day, 0.0), 6)
        reason = refused.get(day) or error
        if reason:
            rows.append({"day": day, "ledgerUSD": ledger_usd, "reportUSD": None, "reason": reason})
        else:
            report_usd = round(report.get(day, 0.0), 6)
            rows.append({"day": day, "ledgerUSD": ledger_usd, "reportUSD": report_usd,
                         "differenceUSD": round(ledger_usd - report_usd, 6)})
    return rows


def main(argv):
    days, as_json, refresh, plan = DEFAULT_DAYS, False, False, PLAN
    rest = list(argv)
    while rest:
        flag = rest.pop(0)
        if flag == "--json":
            as_json = True
        elif flag == "--refresh":
            refresh = True
        elif flag == "--days" and rest:
            try:
                days = int(rest.pop(0))
            except ValueError:
                days = 0
        elif flag == "--plan" and rest:
            plan = rest.pop(0)
        else:
            print("usage: console_credit_audit.py [--days 1..%d] [--json] [--refresh] [--plan %s]"
                  % (MAX_DAYS, PLAN), file=sys.stderr)
            return 2
    plan = PLAN_ALIASES.get(plan, plan)
    if plan != PLAN:
        print("usage: console_credit_audit.py audits %s only: %s has no Console Admin key (D2 leaves "
              "the primary an Individual Org)" % (PLAN, plan), file=sys.stderr)
        return 2
    if not 1 <= days <= MAX_DAYS:
        print("usage: console_credit_audit.py --days takes 1..%d" % MAX_DAYS, file=sys.stderr)
        return 2

    key, why = admin_key()
    if key is None:
        print(why, file=sys.stderr)
        return 3

    today = datetime.now(timezone.utc).date()
    window = {(today - timedelta(days=n)).isoformat() for n in range(1, days + 1)}
    start = (today - timedelta(days=days)).isoformat() + "T00:00:00Z"
    end = today.isoformat() + "T00:00:00Z"

    payload, fetched_on = (None, None) if refresh else load_cache()
    source = "cache"
    if payload is None:
        source = "live"
        payload, why = fetch_report(key, start, end)
        if payload is not None:
            store_cache(payload)
            fetched_on = time.strftime("%Y-%m-%d", time.gmtime())
    if payload is None:
        report, refused, error = {}, {}, why
    else:
        report, refused, error = report_by_day(payload, window)
    ledger, ledger_why = ledger_by_day(probe.ledger_path(), PLAN, window)
    if ledger_why:
        print(ledger_why, file=sys.stderr)
        return 3

    rows = rows_for(ledger, report, refused, error)
    if as_json:
        print(json.dumps({"plan": PLAN, "transport": PLAN, "from": start, "to": end,
                          "source": source, "days": rows}, indent=2))
    else:
        print("%s: sidecar ledger %s vs cost_report %s..%s (recorded %s)"
              % (PLAN, probe.ledger_path(), start, end, fetched_on or "unreadable"))
        for row in rows:
            if row["reportUSD"] is None:
                print("%s\tledger %.4f\treport unavailable (%s)"
                      % (row["day"], row["ledgerUSD"], row["reason"]))
            else:
                print("%s\tledger %.4f\treport %.4f\tdifference %+.4f"
                      % (row["day"], row["ledgerUSD"], row["reportUSD"], row["differenceUSD"]))
    return 3 if error or refused else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
