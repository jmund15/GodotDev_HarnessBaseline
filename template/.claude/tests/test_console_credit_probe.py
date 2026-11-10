#!/usr/bin/env python3
"""Proof for `scripts/console_credit_probe.py`: the Console credit reading, computed from the owner's
re-anchor and the sidecar ledger, never from an API (C1 leaves no balance endpoint).

Every case plants a scratch `HOME` holding its own registry copy, anchor file and ledger, and runs
the probe as a process with `PROVIDER_CAPACITY_TRANSPORT` set. Nothing here reads this machine's
ledger, and no case makes a call or touches a credential: the scratch anchor is load-bearing in
every available reading, so a probe that resolved a different home would read unknown and fail.

Cycles are planted relative to the real clock (`day()`), and every band assertion is either the
whole-grant or the spent end of the scale, because a mid-cycle band follows the wall clock.

    python3 .claude/tests/test_console_credit_probe.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "console_credit_probe.py"
SHIPPED = ROOT / "reference" / "external_models.json"
sys.path.insert(0, str(ROOT / "tools"))
from provider_capacity import validate_reading  # noqa: E402
import quota_bands  # noqa: E402

PLAN = "anthropic-console"
PLAN_SECOND = "anthropic-console-secondary"
PROBE = ".claude/scripts/console_credit_probe.py"
WINDOW = "credit_cycle"
ANCHOR_FILE = "console_credit_anchor.json"
MARKER_FILE = "console_credit_exhausted.json"
LEDGER = ".claude/sidecar_ledger.jsonl"
# The error C7 records for a spent credit, in the shape a child's stream-json result carries it.
CHILD_SPENT = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                          "result": "API Error: Your credit balance is too low to access the "
                                    "Anthropic API. Please go to Plans & Billing to purchase credits."})

# Child 61a50469's capture, trimmed to the fields the reprice reads (type, session, timestamp,
# message id/model/usage); the run's paths and tool content are dropped. Stored $0.005904 priced
# at the driver's model; repriced from these transcripts, the two subagents on their own model
# bring it to $0.042558.
WFSPEND = {
    "record": '{"sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "transport": "anthropic-secondary", "requestedModel": "claude-haiku-5-5", "servedModel": ["claude-haiku-5-5", "claude-sonnet-5-5"], "costModel": "plan-quota", "costBasis": "list-price (plan-quota; not spend)", "priceMultiplier": 1.0, "inputTokens": 16, "cacheReadTokens": 81906, "cacheWriteTokens": 20226, "cacheWrite1hTokens": 0, "outputTokens": 3334, "costUSD": 0.005904}',
    "transcript": '''
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:18.840Z", "message": {"id": "msg_011Cfzvc4eFnFtY51mPQxztH", "model": "claude-haiku-5-5", "usage": {"input_tokens": 2, "output_tokens": 1587, "cache_read_input_tokens": 14599, "cache_creation_input_tokens": 3680, "cache_creation": {"ephemeral_1h_input_tokens": 3680, "ephemeral_5m_input_tokens": 0}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:20.131Z", "message": {"id": "msg_011CfzvcW7T1pwxxoLYdUow9", "model": "claude-haiku-5-5", "usage": {"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 18279, "cache_creation_input_tokens": 2225, "cache_creation": {"ephemeral_1h_input_tokens": 2225, "ephemeral_5m_input_tokens": 0}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:30.235Z", "message": {"id": "msg_011Cfzvd9vHbLAJWkbvmg5aM", "model": "claude-haiku-5-5", "usage": {"input_tokens": 4, "output_tokens": 252, "cache_read_input_tokens": 20504, "cache_creation_input_tokens": 5935, "cache_creation": {"ephemeral_1h_input_tokens": 5935, "ephemeral_5m_input_tokens": 0}}}}
''',
    "agents": {
        "agent-a062d08947bc597a4": '''
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:21.993Z", "message": {"id": "msg_011CfzvcaX7rJuaahPH1MARt", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 16, "cache_read_input_tokens": 6451, "cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:22.460Z", "message": {"id": "msg_011CfzvcaX7rJuaahPH1MARt", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 215, "cache_read_input_tokens": 6451, "cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:26.184Z", "message": {"id": "msg_011Cfzvcjy92pwC9NgQ6sFoD", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 534, "cache_read_input_tokens": 12004, "cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0}}}}
''',
        "agent-aa654b8b7a22d05e3": '''
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:20.684Z", "message": {"id": "msg_011CfzvcVoLtYsc5kfyS21ku", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 16, "cache_read_input_tokens": 3618, "cache_creation_input_tokens": 2833, "cache_creation": {"ephemeral_5m_input_tokens": 2833, "ephemeral_1h_input_tokens": 0}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:21.371Z", "message": {"id": "msg_011CfzvcVoLtYsc5kfyS21ku", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 215, "cache_read_input_tokens": 3618, "cache_creation_input_tokens": 2833, "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 2833}}}}
{"type": "assistant", "sessionId": "61a50469-e2df-4f40-a2dc-abaa08dc2480", "timestamp": "2026-10-13T21:12:24.862Z", "message": {"id": "msg_011CfzvcfR39xfbpJ8CTpm1A", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 2, "output_tokens": 526, "cache_read_input_tokens": 6451, "cache_creation_input_tokens": 5553, "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 5553}}}}
''',
    },
}


class _Absent:
    """A ledger row with the field left out entirely."""


_ABSENT = _Absent()


def day(delta):
    """A UTC ISO day `delta` days from today, so a planted cycle is live whenever this runs."""
    return (datetime.now(timezone.utc).date() + timedelta(days=delta)).isoformat()


def epoch(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def iso(ago_seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago_seconds))


def row(cost, ago_seconds, transport=PLAN, **extra):
    """A sidecar ledger row of the shape `sc_write_record` appends."""
    entry = {"timestamp": iso(ago_seconds), "transport": transport, "costModel": "plan-quota",
             "costBasis": "list-price (plan-quota; not spend)", "exitCode": 0,
             "outcome": "finished-with-record"}
    if not isinstance(cost, _Absent):
        entry["costUSD"] = cost
    entry.update(extra)
    return entry


def plant(home, *, cycle=(-2, 8), grant=200, ledger=None, anchor=None, marker=None, plan=PLAN,
          second_cycle=(-2, 19), second_grant=100, anchor_now=3600):
    """Write a registry copy, a scratch HOME and its files; return the probe's child environment."""
    data = json.loads(SHIPPED.read_text(encoding="utf-8"))
    for name, (cs, ce), g in ((PLAN, cycle, grant), (PLAN_SECOND, second_cycle, second_grant)):
        data["transports"][name]["credit"] = {"grantUSD": g, "cycleStart": day(cs), "cycleEnd": day(ce)}
        data["transports"][name]["status"] = {"state": "available", "reason": "planted"}
    registry = Path(home) / "external_models.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    (Path(home) / ".claude").mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        (Path(home) / LEDGER).write_text(
            "".join(item if isinstance(item, str) else json.dumps(item) + "\n" for item in ledger),
            encoding="utf-8")
    if anchor is not None:
        (Path(home) / ".claude" / ANCHOR_FILE).write_text(json.dumps(anchor), encoding="utf-8")
    if marker is not None:
        (Path(home) / ".claude" / MARKER_FILE).write_text(json.dumps(marker), encoding="utf-8")
    env = dict(os.environ, HOME=str(home), HARNESS_MODEL_REGISTRY=str(registry),
               PROVIDER_CAPACITY_TRANSPORT=plan)
    env.pop("SIDECAR_LEDGER_PATH", None)      # D5: the route's ledger is the default one, always
    env.pop("PROVIDER_CAPACITY_CACHE", None)
    return env


def run(env, *argv):
    result = subprocess.run([sys.executable, str(SCRIPT), *argv], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=60)
    try:
        reading = json.loads(result.stdout)
    except ValueError:
        reading = None
    return result, reading if isinstance(reading, dict) else {}


def planted_json(path):
    """A planted file's JSON, or None when the probe wrote nothing at all."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def show(result, reading):
    """The child's own words, so a failed case shows why rather than a bare assertion."""
    return (result.stdout + result.stderr + json.dumps(reading))[:300]


def anchored(plan=PLAN, remaining=200.0, ago=3600):
    return {plan: {"asOf": iso(ago), "remainingUSD": remaining}}


def quota_of(reading):
    return (reading or {}).get("quota") or {}


def credits_of(reading):
    return quota_of(reading).get("credits") or {}


def window_of(reading):
    return next((w for w in quota_of(reading).get("windows") or [] if w.get("name") == WINDOW), {})


def scratch():
    return tempfile.mkdtemp(prefix="console_credit_probe_")


def plant_wfspend(login):
    """Write the trimmed child capture under a scratch login's projects dir."""
    sid = json.loads(WFSPEND["record"])["sessionId"]
    projects = Path(login) / "projects" / "p"
    projects.mkdir(parents=True)
    (projects / (sid + ".jsonl")).write_text(WFSPEND["transcript"], encoding="utf-8")
    subagents = projects / sid / "subagents" / "workflows" / "wf_proof"
    subagents.mkdir(parents=True)
    for name, text in WFSPEND["agents"].items():
        (subagents / (name + ".jsonl")).write_text(text, encoding="utf-8")


def main():
    cases = []
    source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.is_file() else ""

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), "" if ok else str(detail)[:400]))

    # ---- registration ------------------------------------------------------------------------
    registry = json.loads(SHIPPED.read_text(encoding="utf-8"))
    specs = [(registry["transports"].get(n) or {}).get("capacityProbe") or {} for n in (PLAN, PLAN_SECOND)]
    case("both Console plans register this probe as their quota source, inside --check's time budget",
         all(s.get("kind") == "quota" and s.get("args") == [PROBE]
             and s.get("command") in ("python", "python3") and s.get("timeoutSeconds", 100) <= 20
             for s in specs), repr(specs))

    # ---- the reading -------------------------------------------------------------------------
    home = scratch()
    result, reading = run(plant(home))
    case("no anchor reads unknown: the Console balance is the only source and it is absent",
         result.returncode == 0 and isinstance(reading, dict) and reading.get("status") == "unknown"
         and reading.get("quota") is None and reading.get("transport") == PLAN
         and "anchor" in (reading.get("error") or {}).get("message", "")
         and validate_reading(reading, PLAN, "plan-quota") == [], result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored()))
    win = window_of(reading)
    case("an anchored plan with an empty ledger reads available at its whole grant",
         reading is not None and reading.get("status") == "available"
         and not validate_reading(reading, PLAN, "plan-quota") and win.get("usedPercent") == 0.0
         and credits_of(reading).get("balance") == 200.0 and win.get("band") == "Surplus"
         and win.get("runoutRatio") is None and not (Path(home) / LEDGER).exists(),
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99),
                                ledger=[row(0.44, 1800), row(0.01, 600), row(0.5, 10800),
                                        row(0.7, 1800, transport=PLAN_SECOND),
                                        row(9.0, 1800, transport="anthropic"),
                                        row(0.25, 1800, transport="anthropic-secondary")]))
    case("the ledger's priced rows for THIS plan after the anchor are the spend; other routes are not",
         reading.get("status") == "available" and abs(credits_of(reading).get("balance", 0) - 199.54) < 1e-6
         and credits_of(reading).get("hasCredits") is True and credits_of(reading).get("enabled") is True
         and window_of(reading).get("usedPercent") == 0.23
         and not validate_reading(reading, PLAN, "plan-quota"), show(result, reading))

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99, ago=3600),
                                ledger=[row(0.44, 3600)]))         # stamped exactly at the anchor
    case("a ledger row stamped exactly at the anchor's asOf is not counted",
         reading.get("status") == "available"
         and abs(credits_of(reading).get("balance", 0) - 199.99) < 1e-6, result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99, ago=60),
                                ledger=[row(0.44, 3600), row(0.01, 7200)]))
    case("an anchor newer than every ledger row is the baseline: those rows are outside the range",
         abs(credits_of(reading).get("balance", 0) - 199.99) < 1e-6, result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=0.0)))
    case("a spent credit reads exhausted with the Exhausted dispatch band and a zero balance",
         reading.get("status") == "exhausted"
         and quota_of(reading).get("dispatchBand") == "Exhausted"
         and credits_of(reading).get("balance") == 0.0 and credits_of(reading).get("hasCredits") is False
         and not validate_reading(reading, PLAN, "plan-quota"), show(result, reading))

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=0.5)))
    case("dollars still left are available, not exhausted, however little is left",
         reading.get("status") == "available" and credits_of(reading).get("balance") == 0.5
         and quotas_band(reading), show(result, reading))

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=0.1), ledger=[row(0.25, 1800)]))
    case("spending past the grant clamps the window at 100% and still reads exhausted, validly",
         reading.get("status") == "exhausted" and window_of(reading).get("usedPercent") == 100.0
         and validate_reading(reading, PLAN, "plan-quota") == [], show(result, reading))

    home = scratch()
    result, reading = run(plant(home, cycle=(-20, -11),
                                anchor={PLAN: {"asOf": iso(15 * 86400), "remainingUSD": 199.99}}))
    case("a cycle whose cycleEnd has passed reads unknown, never a stale percentage",
         reading.get("status") == "unknown" and reading.get("quota") is None
         and "cycle" in (reading.get("error") or {}).get("message", "")
         and validate_reading(reading, PLAN, "plan-quota") == [], result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, cycle=(-20, -11),
                                anchor={PLAN: {"asOf": iso(15 * 86400), "remainingUSD": 199.99}}))
    message = (reading.get("error") or {}).get("message", "")
    case("an ended cycle is a STATED unknown, not malformed: the reader names the date and the step",
         reading.get("status") == "unknown" and reading.get("quota") is None
         and (reading.get("error") or {}).get("kind") == "unknown"
         and "credit cycle ended %s" % day(-11) in message and "re-anchor" in message
         and validate_reading(reading, PLAN, "plan-quota") == [], result.stdout + result.stderr)

    home = scratch()
    env = plant(home, anchor=anchored())
    cache = Path(home) / "capacity.json"
    cache.write_text(json.dumps({PLAN: {
        "schemaVersion": 1, "transport": PLAN, "costModel": "plan-quota", "sourceKind": "live",
        "observedAt": iso(60), "status": "unknown",
        "error": {"kind": "unknown", "message": "credit cycle ended 2026-10-31"},
        "liveFailure": None, "quota": None, "balance": None}}), encoding="utf-8")
    env["PROVIDER_CAPACITY_CACHE"] = str(cache)
    result, _ = run(env, "--anchor", "primary", "199.99")
    case("the owner's re-anchor drops the plan's cached unknown, so the next read is live again",
         result.returncode == 0 and PLAN not in (planted_json(cache) or {}),
         result.stdout + result.stderr + repr(planted_json(cache)))

    home = scratch()
    env = plant(home, anchor=anchored())
    (Path(home) / ".claude" / ANCHOR_FILE).write_text("{not json", encoding="utf-8")
    result, reading = run(env)
    case("an unreadable anchor file reads unknown, never zero spend",
         reading.get("status") == "unknown" and reading.get("quota") is None,
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor={PLAN: {"asOf": "yesterday", "remainingUSD": 199.99}}))
    case("an anchor whose asOf is not a time reads unknown",
         reading.get("status") == "unknown" and reading.get("quota") is None,
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, cycle=(5, 15),
                                anchor={PLAN: {"asOf": iso(3600), "remainingUSD": 199.99}}))
    case("an anchor that predates the cycle's start is refused: its balance belongs to another cycle",
         reading.get("status") == "unknown" and reading.get("quota") is None,
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99), ledger=[row(_ABSENT, 1800)]))
    case("an unpriced ledger row after the anchor fails the reading: the spend is unaccounted",
         reading.get("status") == "unknown" and reading.get("quota") is None
         and "costUSD" in (reading.get("error") or {}).get("message", ""),
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99),
                                ledger=[{"transport": PLAN, "costUSD": 0.25}]))
    case("a ledger row naming the plan with no usable timestamp fails the reading",
         reading.get("status") == "unknown" and reading.get("quota") is None,
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=199.99),
                                ledger=[row(0.44, 1800), "{torn line from another writer"]))
    case("a torn ledger line that names no row is skipped, read as neither spend nor fault",
         reading.get("status") == "available"
         and abs(credits_of(reading).get("balance", 0) - 199.55) < 1e-6,
         result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored()))
    win = window_of(reading)
    quota = quota_of(reading)
    case("the credit cycle is the one window: resetsAt is cycleEnd and its length is the cycle's",
         win.get("resetsAt") == epoch(day(8)) and win.get("windowDurationMins") == 10 * 24 * 60
         and quota.get("routingWindow") == WINDOW and quota.get("bindingWindow") == WINDOW
         and quota.get("routingBand") == win.get("band")
         and quota.get("dispatchBand") == win.get("band")
         and isinstance(win.get("pressure"), (int, float)) and win.get("pressure") >= 0,
         show(result, reading))

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(remaining=100.0)))
    case("half the grant spent reads 50% of the cycle at a band compute_band gives",
         window_of(reading).get("usedPercent") == 50.0
         and window_of(reading).get("band") in quota_bands.BAND_NAMES
         and isinstance(window_of(reading).get("runoutRatio"), (int, float))
         and not validate_reading(reading, PLAN, "plan-quota"), show(result, window_of(reading)))

    home = scratch()
    result, reading = run(plant(home, plan=PLAN_SECOND, second_cycle=(-2, 19), second_grant=100,
                                anchor={PLAN_SECOND: {"asOf": iso(3600), "remainingUSD": 99.55}},
                                ledger=[row(0.25, 1800, transport=PLAN_SECOND),
                                        row(9.0, 1800, transport=PLAN)]))
    case("the secondary reads its own cycle, its own grant and only its own ledger rows",
         reading.get("transport") == PLAN_SECOND and reading.get("status") == "available"
         and abs(credits_of(reading).get("balance", 0) - 99.30) < 1e-6
         and window_of(reading).get("windowDurationMins") == 21 * 24 * 60
         and not validate_reading(reading, PLAN_SECOND, "plan-quota"), show(result, reading))

    # ---- the owner's re-anchor ----------------------------------------------------------------
    home = scratch()
    env = plant(home, anchor={PLAN_SECOND: {"asOf": iso(60), "remainingUSD": 99.55}})
    result, _ = run(env, "--anchor", "primary", "199.99")
    blob = planted_json(Path(home) / ".claude" / ANCHOR_FILE) or {}
    _, reading = run(env)
    case("--anchor <account> writes this plan's re-anchor and keeps the other account's",
         result.returncode == 0 and abs(blob.get(PLAN, {}).get("remainingUSD", 0) - 199.99) < 1e-9
         and blob.get(PLAN_SECOND, {}).get("remainingUSD") == 99.55
         and reading.get("status") == "available"
         and abs(credits_of(reading).get("balance", 0) - 199.99) < 1e-6,
         result.stdout + result.stderr + repr(blob))

    home = scratch()
    env = plant(home, anchor=anchored())
    result, _ = run(env, "--anchor", "no-such-account", "5")
    result2, _ = run(env, "--anchor", "primary", "not-a-number")
    blob = planted_json(Path(home) / ".claude" / ANCHOR_FILE) or {}
    case("--anchor refuses an unknown account and a non-numeric balance, writing nothing",
         result.returncode == 2 and result2.returncode == 2
         and "no Console account or transport named" in (result.stderr + result.stdout)
         and "must be a number of US dollars" in (result2.stderr + result2.stdout)
         and blob.get(PLAN, {}).get("remainingUSD") == 200.0,
         result.stdout + result.stderr + result2.stdout + result2.stderr)

    # ---- R1: the credit-exhausted child result ------------------------------------------------
    home = scratch()
    env = plant(home, anchor=anchored())
    child = Path(home) / "child.txt"
    child.write_text(CHILD_SPENT + "\n", encoding="utf-8")
    result, _ = run(env, "--child-result", str(child))
    marker = planted_json(Path(home) / ".claude" / MARKER_FILE) or {}
    _, reading = run(env)
    case("a child result carrying the credit-exhausted error marks the route exhausted for this cycle",
         result.returncode == 0 and marker.get(PLAN, {}).get("cycleEnd") == day(8)
         and reading.get("status") == "exhausted"
         and quota_of(reading).get("dispatchBand") == "Exhausted"
         and credits_of(reading).get("markedExhausted") is True
         and not validate_reading(reading, PLAN, "plan-quota"), result.stdout + result.stderr)

    home = scratch()
    result, reading = run(plant(home, anchor=anchored(),
                                marker={PLAN: {"asOf": iso(60), "cycleEnd": day(-1)}}))
    case("a marker left by an earlier cycle does not exhaust the current one",
         reading.get("status") == "available", show(result, reading))

    home = scratch()
    env = plant(home, anchor=anchored())
    child = Path(home) / "child.txt"
    child.write_text(json.dumps({"type": "result", "is_error": True, "result": "rate limited"}) + "\n",
                     encoding="utf-8")
    result, _ = run(env, "--child-result", str(child))
    _, reading = run(env)
    case("a child result without the credit-exhausted error writes no marker",
         result.returncode == 0 and not (Path(home) / ".claude" / MARKER_FILE).exists()
         and reading.get("status") == "available", result.stdout + result.stderr)

    home = scratch()
    env = plant(home, plan=PLAN_SECOND,
                anchor={PLAN_SECOND: {"asOf": iso(3600), "remainingUSD": 99.55}})
    child = Path(home) / "child.txt"
    child.write_text(CHILD_SPENT + "\n", encoding="utf-8")
    result, _ = run(env, "--child-result", str(child))
    marker = planted_json(Path(home) / ".claude" / MARKER_FILE) or {}
    case("the marker names the plan the probe ran under, not the account it came from",
         marker.get(PLAN_SECOND, {}).get("cycleEnd") == day(19) and PLAN not in marker,
         result.stdout + result.stderr + repr(marker))

    # ---- a row stored before the subagent-pricing fix is repriced from its transcript
    # The planted child 61a50469: stored $0.005904 (priced at the driver's model), repriced $0.042558.
    home = scratch()
    login = Path(home) / "login"
    stale = json.loads(WFSPEND["record"])
    sid = stale["sessionId"]

    def stale_row(label, session):
        return dict(stale, timestamp=iso(600), transport=PLAN, label=label, sessionId=session, costUSD=0.005904)

    env = plant(home, anchor=anchored(), ledger=[stale_row("repriced", sid),
                                                 stale_row("no-transcript", "absent-session")])
    blob = json.loads(Path(env["HARNESS_MODEL_REGISTRY"]).read_text(encoding="utf-8"))
    blob["transports"][PLAN]["configDir"] = str(login)
    Path(env["HARNESS_MODEL_REGISTRY"]).write_text(json.dumps(blob), encoding="utf-8")
    plant_wfspend(login)
    result, reading = run(env)
    credits = credits_of(reading)
    case("a ledger row with a transcript is repriced, not summed at its stored costUSD; one without keeps its stored cost and is named",
         reading.get("status") == "available"
         and abs(credits.get("balance", 0) - (200.0 - 0.042558 - 0.005904)) < 1e-5
         and credits.get("storedCostRows") == ["no-transcript"], show(result, reading) + repr(credits))

    # ---- surfaces the probe must not have -----------------------------------------------------
    case("the probe reads no credential and calls no API: the reading is entirely local",
         source != "" and "urllib" not in source and "requests" not in source
         and "credentialVar" not in source and "credentialFile" not in source
         and "ANTHROPIC_CONSOLE_KEY" not in source, source[:200])

    bad = [name for name, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % detail))
    print("\n%d/%d passed" % (len(cases) - len(bad), len(cases)))
    return 1 if bad else 0


def quotas_band(reading):
    """True when the reading's window band is a band `compute_band` can return."""
    return window_of(reading).get("band") in quota_bands.BAND_NAMES


if __name__ == "__main__":
    raise SystemExit(main())
