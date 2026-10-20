#!/usr/bin/env python3
"""Read one Anthropic Console credit plan's remaining credit into the shared capacity schema.

NO API RETURNS REMAINING CREDIT (C1: `/v1/organizations/balance` and `/v1/organizations/credit_grants`
404, `/v1/organizations/spend_limits` answers 400 for an Individual Org, the Spend Limits API is
Enterprise-only), so the number is computed from two local sources, the way D1 settles it:

    used = (grantUSD - anchor.remainingUSD) + sum(this plan's ledger rows' repriced cost after anchor.asOf)
    left = grantUSD - used

`grantUSD`, `cycleStart` and `cycleEnd` are the registry's `credit` block (D4: cycle facts the owner
enters once per cycle; a date names midnight UTC of that day). `asOf` and `remainingUSD` are the
machine-local re-anchor, written by `--anchor <account> <remainingUSD>` from an owner's Console
Billing reading. `costUSD` on a plan-quota row is the vendor's LIST price for the run, which C3
matched to the cent against the Console balance on one sample; the audit compares the two further.
Each row is repriced through `model_registry.reprice_record` from its transcript; a row with none keeps its
stored `costUSD`, and `quota.credits.storedCostRows` names those rows.

The credit cycle is the plan's ONE quota window: `resetsAt` is `cycleEnd`, `windowDurationMins` is
the cycle's length, `usedPercent` is the spend as a share of the grant, and the band and pressure
come from `quota_bands.compute_band` like every other plan-quota probe. The dollars left travel in
`quota.credits` beside them, with `hasCredits` stating the same fact: `quota_bands.is_exhausted`
reads `hasCredits` and no producer emitted it before this one, so a dollar balance alone would be
carried by `provider_bands` and the launcher and never consulted by either.

THE SUMMED LEDGER IS `~/.claude/sidecar_ledger.jsonl` and `-L`/`SIDECAR_LEDGER_PATH` are ignored
(D5): the Console launchers force that path, and a sum taken from a file other than the one the
launcher writes would understate the spend. `HOME` is honoured, because that is the variable the
launcher's own default resolves from.

FAIL POSTURE. Anything that leaves the spend unknowable prints the `malformed` status, which
carries no quota envelope at all, so every consumer reads it as unreadable and never as room: no
usable anchor for this plan, an anchor older than the cycle it is offsetting, a cycle whose
`cycleEnd` has passed (R3: a stale cycle is refused, never guessed), a ledger row of this plan that
carries no price or no usable timestamp (R2: unaccounted spend is not spent-unknown, it is unknown).
A line of the ledger that is not an object names no plan and is skipped, so one writer's torn line
does not blind the route.

R1: the provider can refuse while the ledger still shows dollars left, so `--child-result <path>`
records the credit-exhausted error (C7's "credit balance is too low") as a marker that outranks the
arithmetic for the rest of that cycle. The owner's next re-anchor starts the next cycle.

CLI:
    console_credit_probe.py                                     the reading, as one JSON line
    console_credit_probe.py --anchor <account> <remainingUSD>    record the owner's re-anchor
    console_credit_probe.py --child-result <path>                record a credit-exhausted marker
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import model_registry  # noqa: E402
from quota_bands import EXHAUSTED_BAND, compute_band, runout_ratio_for  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# provider_capacity sets this to the plan it is reading; a bare run reads the primary Console org.
DEFAULT_PLAN = "anthropic-console"
COST_MODEL = "plan-quota"
# The one window a credit plan has. Not a provider window name: the cycle is the plan's own period.
WINDOW_NAME = "credit_cycle"
ANCHOR_FILE = "console_credit_anchor.json"
MARKER_FILE = "console_credit_exhausted.json"
LEDGER_REL = ".claude/sidecar_ledger.jsonl"
# C7, verbatim: what the endpoint answers once the promotional credit is spent.
CHILD_EXHAUSTED_TEXT = "credit balance is too low"
# The Console accounts the owner reads a balance from, and the plans those balances belong to.
ACCOUNT_ALIASES = {"primary": "anthropic-console", "secondary": "anthropic-console-secondary"}


def plan_name():
    return os.environ.get("PROVIDER_CAPACITY_TRANSPORT") or DEFAULT_PLAN


def home():
    """The launcher's home: `$HOME` when bash set it (the Console launchers write their ledger
    there), else this OS's own user home."""
    return os.environ.get("HOME") or os.path.expanduser("~")


def anchor_path():
    return os.path.join(home(), ".claude", ANCHOR_FILE)


def marker_path():
    return os.path.join(home(), ".claude", MARKER_FILE)


def ledger_path():
    return os.path.join(home(), LEDGER_REL)


def _utc(epoch=None):
    when = datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc)
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch(value):
    """Epoch seconds of an ISO-8601 stamp or an epoch number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if _finite(value) else None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _finite(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    value = float(value)
    if not float("-inf") < value < float("inf"):
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def _day(value):
    """Midnight UTC of an ISO day (`2026-10-20`), else None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _json(path):
    """(value, error) for the JSON at `path`; (None, None) when the file does not exist."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "not JSON (%s)" % exc


def outcome(status, plan, *, quota=None, error=None):
    return {"schemaVersion": 1, "transport": plan, "costModel": COST_MODEL, "sourceKind": "live",
            "observedAt": _utc(), "status": status, "error": error, "liveFailure": None,
            "quota": quota, "balance": None}


def unknown(plan, message):
    """The reading for a spend this probe cannot establish: typed, and with no quota envelope.

    `malformed` is the status that already exists for "the source did not yield a usable number",
    and every consumer of the schema treats it as unreadable (the launcher refuses at it, the bands
    drop the plan with `why`), which is what an unaccounted credit deserves.
    """
    return outcome("malformed", plan, error={"kind": "malformed", "message": message})


def cycle_for(plan):
    """(cycle, error) for `plan`'s registry `credit` block: `{start, end, grant, cycleStart, cycleEnd}`."""
    try:
        meta = model_registry.transport_meta(plan)
    except Exception:  # noqa: BLE001 -- an unreadable registry is a reading fault, not a crash
        meta = None
    if not isinstance(meta, dict):
        return None, "the registry names no transport %s" % plan
    credit = meta.get("credit")
    if not isinstance(credit, dict):
        return None, "the registry's %s row declares no credit block" % plan
    start, end = _day(credit.get("cycleStart")), _day(credit.get("cycleEnd"))
    grant = credit.get("grantUSD")
    if start is None or end is None:
        return None, "the %s credit cycle is not two ISO days (cycleStart %r, cycleEnd %r)" % (
            plan, credit.get("cycleStart"), credit.get("cycleEnd"))
    if end <= start:
        return None, "the %s credit cycle ends (%s) at or before it starts (%s)" % (
            plan, credit.get("cycleEnd"), credit.get("cycleStart"))
    if not _finite(grant, 0.01):
        return None, "the %s credit grant is not a positive number of dollars (%r)" % (
            plan, credit.get("grantUSD"))
    return {"start": start, "end": end, "grant": float(grant),
            "cycleStart": credit["cycleStart"], "cycleEnd": credit["cycleEnd"]}, None


def read_anchor(plan, path=None):
    """(anchor, error) for `plan`'s re-anchor: `{asOf, remainingUSD}` from the machine-local file."""
    path = path or anchor_path()
    blob, err = _json(path)
    if err:
        return None, "the %s anchor file is unreadable: %s (%s)" % (plan, err, path)
    entry = blob.get(plan) if isinstance(blob, dict) else None
    if not isinstance(entry, dict):
        return None, ("no %s anchor in %s; an owner's Console balance reading is the only source "
                      "(run: console_credit_probe.py --anchor <account> <remainingUSD>)" % (plan, path))
    as_of, remaining = _epoch(entry.get("asOf")), entry.get("remainingUSD")
    if as_of is None or not _finite(remaining, 0):
        return None, "the %s anchor in %s is not {asOf, remainingUSD}" % (plan, path)
    return {"asOf": as_of, "remainingUSD": float(remaining)}, None


def read_marker(plan, path=None):
    """`plan`'s credit-exhausted marker, or None. A marker is only ever read, never trusted blind:
    `summarize` applies it only when it names the cycle now current."""
    blob, _err = _json(path or marker_path())
    entry = blob.get(plan) if isinstance(blob, dict) else None
    return entry if isinstance(entry, dict) else None


def row_cost(row, path, data=None):
    """(usd, repriced): the row's cost through `model_registry.reprice_record`, the path `orchestration_metrics`
    and `run_cost` price by. Rows written before subagents were priced at their own model store the driver-rate
    cost; the transcript fixes it. `repriced` is False where no final transcript exists (or it cannot be
    priced), and the stored `costUSD` stands."""
    stored = row.get("costUSD")
    priced = model_registry.reprice_record(row, path, data=data)
    cost = priced.get("costUSD")
    if priced.get("costUsageBasis") == "transcript-final" and _finite(cost, 0):
        return float(cost), True
    return (float(stored) if _finite(stored, 0) else None), False


def ledger_spend(path, transport, after, stored=None):
    """(spend, error): `transport`'s `costUSD` in `path`, for rows stamped after `after`, each repriced from its
    transcript. `stored`, when a list, collects the label of every row whose stored `costUSD` was summed as is."""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return 0.0, None
    spend = 0.0
    data = model_registry.load()
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
            stamp = _epoch(row.get("timestamp"))
            if stamp is None:
                return None, ("a %s ledger row carries no usable timestamp, so its spend cannot be "
                              "placed against the anchor" % transport)
            if stamp <= after:
                continue
            cost, repriced = row_cost(row, path, data)
            if cost is None:
                return None, ("a %s ledger row after the anchor (%s, %s) carries no priced costUSD, so the "
                              "credit's use is unaccounted; a stopped job leaves such a row. Read the Console "
                              "balance and re-anchor past it: console_credit_probe.py --anchor <account> "
                              "<remainingUSD>" % (transport, row.get("label") or "unlabelled", row.get("timestamp")))
            if not repriced and stored is not None:
                stored.append(row.get("label") or "unlabelled")
            spend += cost
    return spend, None


def summarize(plan, cycle, anchor, spend, marker=None, now=None, stored=()):
    """The plan-quota reading for a known spend, or `unknown` when the cycle cannot be banded."""
    now = time.time() if now is None else now
    used = max(0.0, cycle["grant"] - anchor["remainingUSD"]) + spend
    left = cycle["grant"] - used
    marked = (isinstance(marker, dict) and isinstance(marker.get("cycleEnd"), str)
              and marker["cycleEnd"] == cycle["cycleEnd"])
    if cycle["end"] <= now:
        return unknown(plan, "the %s credit cycle ended %s (cycleEnd %s); the owner's next re-anchor "
                             "sets the new cycle" % (plan, _utc(cycle["end"]), cycle["cycleEnd"]))
    # The window is the credit, so its percentage is spend against the grant and never past 100:
    # the schema bounds usedPercent there, and the exhausted status already carries the override.
    percent = min(100.0, max(0.0, (used / cycle["grant"]) * 100.0))
    seconds = cycle["end"] - cycle["start"]
    pressure, band = compute_band(percent, cycle["end"], seconds, now)
    if pressure is None:
        return unknown(plan, "the %s credit cycle %s..%s cannot be banded now (a reset at or before "
                             "now, or a clock before the cycle's own start)" % (
                                 plan, cycle["cycleStart"], cycle["cycleEnd"]))
    ratio = runout_ratio_for(percent, cycle["end"], seconds, now)
    exhausted = marked or used >= cycle["grant"]
    window = {"name": WINDOW_NAME, "usedPercent": round(percent, 4), "resetsAt": cycle["end"],
              "windowDurationMins": int(round(seconds / 60.0)), "pressure": round(pressure, 4),
              "band": band, "runoutRatio": round(ratio, 4) if ratio is not None else None}
    quota = {"windows": [window],
             "dispatchBand": EXHAUSTED_BAND if exhausted else band,
             "routingBand": EXHAUSTED_BAND if exhausted else band,
             "routingWindow": WINDOW_NAME, "bindingWindow": WINDOW_NAME,
             "credits": {"enabled": True, "available": left > 0, "hasCredits": left > 0,
                         "balance": round(left, 6), "markedExhausted": marked,
                         "storedCostRows": list(stored)}}
    return outcome("exhausted" if exhausted else "available", plan, quota=quota)


def read(plan=None, now=None):
    """The plan's capacity reading: the registry cycle, the owner's anchor and the route's ledger."""
    plan = plan or plan_name()
    cycle, why = cycle_for(plan)
    if cycle is None:
        return unknown(plan, why)
    anchor, why = read_anchor(plan)
    if anchor is None:
        return unknown(plan, why)
    if anchor["asOf"] < cycle["start"]:
        return unknown(plan, "the %s anchor (%s) predates the cycle's start %s, so its remaining "
                             "balance belongs to an earlier cycle" % (
                                 plan, _utc(anchor["asOf"]), cycle["cycleStart"]))
    stored = []
    spend, why = ledger_spend(ledger_path(), plan, anchor["asOf"], stored)
    if spend is None:
        return unknown(plan, why)
    return summarize(plan, cycle, anchor, spend, read_marker(plan), now, stored)


def _write_json(path, blob):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(blob, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_anchor(account, remaining, now=None):
    """(message, ok): record `account`'s Console balance reading as this plan's re-anchor.

    The other accounts' entries are preserved: one re-anchor at a time is the owner's whole
    workflow, and an unreadable file is refused rather than overwritten.
    """
    try:
        value = float(remaining)
    except (TypeError, ValueError):
        return "the remaining credit must be a number of US dollars, got %r" % (remaining,), False
    if not _finite(value, 0):
        return "the remaining credit must be a finite, non-negative number of US dollars", False
    plan = ACCOUNT_ALIASES.get(account, account)
    try:
        meta = model_registry.transport_meta(plan)
    except Exception:  # noqa: BLE001
        meta = None
    if not isinstance(meta, dict):
        return ("no Console account or transport named %r (accounts: %s)"
                % (account, ", ".join(sorted(ACCOUNT_ALIASES)))), False
    path = anchor_path()
    blob, err = _json(path)
    if err:
        return "the anchor file %s is unreadable (%s); refusing to overwrite it" % (path, err), False
    blob = blob if isinstance(blob, dict) else {}
    stamp = _utc(time.time() if now is None else now)
    blob[plan] = {"asOf": stamp, "remainingUSD": value}
    _write_json(path, blob)
    return "anchored %s at $%.2f remaining as of %s (%s)" % (plan, value, stamp, path), True


def note_child_result(target, now=None):
    """(message, ok): mark the plan exhausted when `target`'s text carries C7's credit error."""
    plan = plan_name()
    try:
        text = sys.stdin.read() if target == "-" else Path(target).read_text(
            encoding="utf-8", errors="replace")
    except OSError as exc:
        return "the child result %s is unreadable: %s" % (target, exc), False
    if CHILD_EXHAUSTED_TEXT not in text.lower():
        return "no credit-exhausted error in %s; nothing marked" % target, True
    cycle, why = cycle_for(plan)
    if cycle is None:
        return why, False
    path = marker_path()
    blob, err = _json(path)
    if err:
        return "the marker file %s is unreadable (%s); refusing to overwrite it" % (path, err), False
    blob = blob if isinstance(blob, dict) else {}
    blob[plan] = {"asOf": _utc(time.time() if now is None else now), "cycleEnd": cycle["cycleEnd"]}
    _write_json(path, blob)
    return "marked %s exhausted for the cycle ending %s (%s)" % (plan, cycle["cycleEnd"], path), True


def main(argv):
    if argv[:1] == ["--anchor"]:
        if len(argv) != 3:
            print("usage: console_credit_probe.py --anchor <account> <remainingUSD>", file=sys.stderr)
            return 2
        message, ok = write_anchor(argv[1], argv[2])
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 2
    if argv[:1] == ["--child-result"]:
        if len(argv) != 2:
            print("usage: console_credit_probe.py --child-result <path>", file=sys.stderr)
            return 2
        message, ok = note_child_result(argv[1])
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 2
    if argv:
        print("usage: console_credit_probe.py [--anchor <account> <remainingUSD> | "
              "--child-result <path>]", file=sys.stderr)
        return 2
    try:
        reading = read()
    except Exception as exc:  # noqa: BLE001 -- a probe fault is a reading, never a nonzero exit
        reading = unknown(plan_name(), "the %s credit reading failed: %s" % (
            plan_name(), str(exc)[:200] or type(exc).__name__))
    print(json.dumps(reading, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
