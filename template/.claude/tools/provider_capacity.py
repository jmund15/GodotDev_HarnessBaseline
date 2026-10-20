#!/usr/bin/env python3
"""Read a transport's capacity, cache first, and validate the shared result schema.

`reading()` returns the cached reading when it is no older than `CACHE_TTL_SECONDS` and none of its
quota windows has passed its `resetsAt` (its numbers no longer describe that window, so an
`exhausted` reading never outlives its reset). Only a miss probes live, and only one process per
transport probes at a time: the probe runs under a lock beside the cache file, and a caller that
waited on it re-reads the cache first, so a fan-out of launchers costs one usage read. A caller
that cannot take the lock within `LIVE_BUDGET_SECONDS` falls back as a failed live read does.

A usage endpoint that answers HTTP 429 is rate limiting the reader, and a second read during that
window only extends the lockout, so a `rate-limit` failure puts its transport in a cooldown: no
probe starts until `_rateLimitedUntil[transport]`, and until then the last reading is served as a
cache fallback up to `STALE_FALLBACK_SECONDS` old. A read that answers clears the cooldown."""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent.parent
sys.path.insert(0, str(TOOLS))
import model_registry  # noqa: E402

SCHEMA_VERSION = 1
CACHE_TTL_SECONDS = 600
# Measured 2026-09-29 against the Anthropic usage endpoint: two reads a few seconds apart succeed,
# then it answers HTTP 429 with `Retry-After: 0` and keeps answering 429 for about seven minutes.
RATE_LIMIT_COOLDOWN_SECONDS = 420
STALE_FALLBACK_SECONDS = CACHE_TTL_SECONDS + RATE_LIMIT_COOLDOWN_SECONDS
# Reserved top-level cache key: {transport: epoch}, never a reading.
RATE_LIMIT_KEY = "_rateLimitedUntil"
LIVE_TRIES = 3
RETRY_SLEEP_SECONDS = 2.0
# One preflight stays inside the dispatch hook's 45 s --check window, retries included.
LIVE_BUDGET_SECONDS = 40.0
STATUSES = {
    "available", "exhausted", "dispatch-floor", "insufficient", "auth-error",
    "network-error", "malformed", "unsupported",
}
SOURCE_KINDS = {"live", "cache", "unsupported"}
CACHEABLE = {"available", "exhausted", "dispatch-floor", "insufficient"}
FAILURE_STATUSES = {"auth-error", "network-error", "malformed"}
# The failure kinds a cache fallback may carry: the live probe's fault, or the endpoint's own
# rate limit.
FALLBACK_KINDS = ("network", "server", "rate-limit")


class CapacityError(RuntimeError):
    pass


def http_failure(code, endpoint):
    """The (status, error) every HTTP capacity probe returns for an HTTP error from `endpoint`.

    A 429 is the endpoint rate limiting the reader, so `reading()` starts the transport's cooldown
    instead of re-probing into it; one mapping keeps that true for every plan.
    """
    if code in (401, 403):
        return "auth-error", {"kind": "auth", "message": "%s rejected the credential (HTTP %d)" % (endpoint, code)}
    if code == 429:
        return "network-error", {"kind": "rate-limit", "message": "%s returned HTTP 429 (rate limited)" % endpoint}
    return "network-error", {"kind": "server" if code >= 500 else "network",
                             "message": "%s returned HTTP %d" % (endpoint, code)}


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if float("-inf") < float(value) < float("inf") else None
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


def validate_reading(value, transport, cost_model):
    errors = []
    if not isinstance(value, dict):
        return ["reading is not an object"]
    if value.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion must be 1")
    if value.get("transport") != transport:
        errors.append("transport mismatch")
    if value.get("costModel") != cost_model:
        errors.append("costModel mismatch")
    source = value.get("sourceKind")
    status = value.get("status")
    if source not in SOURCE_KINDS:
        errors.append("unknown sourceKind")
    if status not in STATUSES:
        errors.append("unknown status")
    if status == "dispatch-floor" and cost_model != "plan-quota":
        errors.append("dispatch-floor requires plan-quota")
    if _epoch(value.get("observedAt")) is None:
        errors.append("observedAt must be ISO-8601 or epoch")
    quota = value.get("quota")
    balance = value.get("balance")
    if quota is not None and balance is not None:
        errors.append("quota and balance are mutually exclusive")
    if status == "unsupported":
        if source != "unsupported" or quota is not None or balance is not None:
            errors.append("unsupported result shape is invalid")
    elif status in FAILURE_STATUSES:
        error = value.get("error")
        if source != "live" or quota is not None or balance is not None:
            errors.append("failure result shape is invalid")
        if not isinstance(error, dict) or not isinstance(error.get("kind"), str):
            errors.append("failure needs typed error")
    elif cost_model == "plan-quota":
        if not isinstance(quota, dict) or balance is not None:
            errors.append("plan quota needs quota envelope")
        else:
            windows = quota.get("windows")
            names = set()
            computable = 0
            if not isinstance(windows, list) or not windows:
                errors.append("quota windows missing")
            else:
                for win in windows:
                    if not isinstance(win, dict) or not isinstance(win.get("name"), str):
                        errors.append("quota window invalid")
                        continue
                    names.add(win["name"])
                    used = win.get("usedPercent")
                    if not _finite(used, 0, 100):
                        errors.append("quota usedPercent invalid")
                    ratio = win.get("runoutRatio")
                    if ratio is not None and not _finite(ratio, 0):
                        errors.append("quota runoutRatio invalid")
                    if win.get("band") is None:
                        errors.append("quota window lacks a computable band")
                    elif not isinstance(win.get("band"), str) or not _finite(win.get("pressure"), 0):
                        errors.append("quota window band or pressure invalid")
                    elif win.get("resetsAt") is None and used != 0:
                        # Only a dormant window (0% used, no reset) is banded without a reset.
                        errors.append("quota window with use lacks a reset time")
                    else:
                        computable += 1
            if windows and computable == 0:
                errors.append("quota has no computable window")
            for key in ("dispatchBand", "routingBand", "bindingWindow"):
                if not isinstance(quota.get(key), str) or not quota.get(key):
                    errors.append("quota %s missing" % key)
            # Which window supplied `routingBand` has to be readable off the reading: a
            # consumer comparing two transports must not re-derive it from window lengths.
            if status in ("available", "exhausted", "dispatch-floor"):
                routing_window = quota.get("routingWindow")
                if not isinstance(routing_window, str) or not routing_window:
                    errors.append("quota routingWindow missing")
                elif routing_window not in names:
                    errors.append("quota routingWindow names no reported window")
                elif status == "available":
                    # Exhausted is a status-level override, so a spent capture may report a
                    # routing band no row carries; while usable, the band must be that row's.
                    row = next((w for w in windows
                                if isinstance(w, dict) and w.get("name") == routing_window), None)
                    if isinstance(row, dict) and row.get("band") != quota.get("routingBand"):
                        errors.append("quota routingBand disagrees with its routing window")
            if status == "dispatch-floor":
                row = next((w for w in (windows or []) if isinstance(w, dict)
                            and w.get("name") == quota.get("bindingWindow")), {})
                floor = row.get("dispatchFloorPercent")
                used = row.get("usedPercent")
                if (not _finite(floor, 0, 100) or floor == 100 or not _finite(used, 0, 100)
                        or not floor <= used < 100 or _epoch(row.get("resetsAt")) is None):
                    errors.append("dispatch-floor needs binding window use, floor below 100 and reset")
                if isinstance(quota.get("credits"), dict) and quota["credits"].get("available"):
                    errors.append("dispatch-floor cannot carry usable extra usage")
            credits = quota.get("credits")
            if credits is not None and not isinstance(credits, dict):
                errors.append("credits must be object or null")
    elif cost_model == "marginal-usd":
        if not isinstance(balance, dict) or quota is not None:
            errors.append("marginal USD needs balance envelope")
        elif not _finite(balance.get("amount"), 0) or not isinstance(balance.get("currency"), str):
            errors.append("balance envelope invalid")
    if source == "cache":
        failure = value.get("liveFailure")
        if not isinstance(failure, dict) or failure.get("kind") not in FALLBACK_KINDS:
            errors.append("cache result needs a %s liveFailure" % "/".join(FALLBACK_KINDS))
    elif value.get("liveFailure") is not None:
        errors.append("liveFailure is cache-only")
    return errors


def _cache_path():
    override = os.environ.get("PROVIDER_CAPACITY_CACHE")
    return override or str(ROOT / ".claude" / ".cache" / "provider_capacity.json")


def _load_hook_state():
    """`hooks/_hook_state.py`, the house owner of the locked read-modify-write the cache needs.

    Several probes can finish at once (a fan-out, a gate preflight, two sessions), and the cache
    holds one entry per transport: an unlocked read-modify-write drops the entries a sibling saved
    in between.
    """
    path = ROOT / ".claude" / "hooks" / "_hook_state.py"
    try:
        spec = importlib.util.spec_from_file_location("provider_capacity_hook_state", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _read_cache(path, transport, cost_model, now, max_age=CACHE_TTL_SECONDS):
    """One transport's reading from the cache, or None when it is unreadable or older than `max_age`.

    The reserved `_rateLimitedUntil` key is not a reading: a transport's entry is looked up by the
    transport's own name, so a cooldown table is never returned as one.
    """
    try:
        entry = json.loads(Path(path).read_text(encoding="utf-8")).get(transport)
    except Exception:
        return None
    if validate_reading(entry, transport, cost_model):
        return None
    # Old Anthropic captures called a floor crossing exhausted. Re-probe rather than serve that lie.
    if (cost_model == "plan-quota" and entry.get("status") == "exhausted"
            and model_registry.route_of(transport) == "anthropic"
            and any("dispatchFloorPercent" not in w for w in entry["quota"]["windows"])):
        return None
    if entry.get("status") == "dispatch-floor" and _reset_passed(entry, now):
        return None
    observed = _epoch(entry.get("observedAt"))
    if observed is None or now - observed < -300 or now - observed > max_age:
        return None
    return entry


def _reset_passed(entry, now):
    """Whether any quota window of `entry` has passed its `resetsAt`."""
    windows = ((entry.get("quota") or {}).get("windows")) or []
    for win in windows:
        reset = _epoch(win.get("resetsAt")) if isinstance(win, dict) else None
        if reset is not None and reset <= now:
            return True
    return False


def _fresh(path, transport, cost_model, now):
    """`_read_cache` within the TTL, minus a reading whose window has reset since."""
    entry = _read_cache(path, transport, cost_model, now)
    if entry is None or _reset_passed(entry, now):
        return None
    return entry


def _live_lock_path(transport, cache_path):
    """The single-reader lock file for `transport`, beside the cache file."""
    return "%s.%s.lock" % (cache_path, transport)


def _load_file_lock():
    path = ROOT / ".claude" / "hooks" / "_file_lock.py"
    try:
        spec = importlib.util.spec_from_file_location("provider_capacity_file_lock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _rate_limited_until(path, transport):
    """The epoch `transport` may probe again after a rate-limit failure, or None."""
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8")).get(RATE_LIMIT_KEY)
    except Exception:
        return None
    if not isinstance(table, dict):
        return None
    until = table.get(transport)
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return None
    if not _finite(until, 1):
        return None
    return float(until)


def _write_cache(path, transport, reading):
    """Store one transport's reading and clear its rate-limit cooldown. False when the lock cannot
    be taken — never a racy write.

    The advisory cache is not worth a lost sibling entry, and `update_json_locked` writes nothing
    when it cannot take the lock, so this returns False and the caller keeps its live reading. Only
    a successful live read reaches here, which is why the cooldown clear rides the same locked
    read-modify-write: a transport that just answered is not rate limited.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    state = _load_hook_state()
    if state is None:
        return False

    def store(blob):
        blob[transport] = reading
        table = blob.get(RATE_LIMIT_KEY)
        if isinstance(table, dict) and transport in table:
            del table[transport]
            if not table:
                del blob[RATE_LIMIT_KEY]
        return blob

    written, _ = state.update_json_locked(str(path), store)
    return written


def _record_rate_limit(path, transport, until):
    """Start `transport`'s cooldown through the same locked read-modify-write the cache uses."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    state = _load_hook_state()
    if state is None:
        return False

    def store(blob):
        table = blob.get(RATE_LIMIT_KEY)
        if not isinstance(table, dict):
            table = {}
        table[transport] = until
        blob[RATE_LIMIT_KEY] = table
        return blob

    written, _ = state.update_json_locked(str(path), store)
    return written


def _credential(cfg):
    name = cfg.get("credentialVar")
    if isinstance(name, str) and os.environ.get(name):
        return os.environ[name]
    path = cfg.get("credentialFile")
    if not isinstance(name, str) or not isinstance(path, str):
        return None
    try:
        text = Path(os.path.expanduser(path)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    pattern = re.compile(r"^\s*(?:set\s+)?%s=(.*)$" % re.escape(name), re.I | re.M)
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip().strip('"')


PROBE_TIMEOUT_CAP_SECONDS = 35


def probe_timeout(spec):
    """The probe's `timeoutSeconds`, plus its `refresh.timeoutSeconds` when it may refresh a token
    first, capped at PROBE_TIMEOUT_CAP_SECONDS."""
    timeout = spec.get("timeoutSeconds", 30)
    refresh = spec.get("refresh")
    extra = refresh.get("timeoutSeconds") if isinstance(refresh, dict) else None
    if _finite(timeout) and _finite(extra, 0):
        timeout = min(PROBE_TIMEOUT_CAP_SECONDS, timeout + extra)
    return timeout


def _run_probe(spec, transport, token=None):
    command = spec.get("command")
    args = list(spec.get("args") or [])
    if not isinstance(command, str) or not command:
        raise CapacityError("capacity probe command is missing")
    if command in ("python", "python3"):
        command = sys.executable
    timeout = probe_timeout(spec)
    if not _finite(timeout, 1, PROBE_TIMEOUT_CAP_SECONDS + 1):
        raise CapacityError("capacity probe timeout must be 1..35 seconds")
    try:
        probe_env = dict(os.environ, PROVIDER_CAPACITY_TRANSPORT=transport)
        if token:
            probe_env["PROVIDER_CAPACITY_TOKEN"] = token
        result = subprocess.run(
            [command] + args, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=float(timeout),
            env=probe_env,
        )
    except subprocess.TimeoutExpired:
        return {
            "schemaVersion": 1, "transport": transport,
            "costModel": "unknown", "sourceKind": "live", "observedAt": _utc(time.time()),
            "status": "network-error", "error": {"kind": "network", "message": "probe timed out"},
            "liveFailure": None, "quota": None, "balance": None,
        }
    if result.returncode != 0:
        raise CapacityError("capacity probe exited %d: %s" % (result.returncode, result.stderr[-500:]))
    try:
        parsed = json.loads(result.stdout)
    except Exception as exc:
        raise CapacityError("capacity probe returned invalid JSON: %s" % exc)
    if not isinstance(parsed, dict):
        raise CapacityError("capacity probe returned a non-object")
    return parsed


def live_reading(spec, transport, cfg, runner=None):
    """Read live capacity once, retrying only a transient network failure.

    A single probe timeout is usually transient: measured 2026-09-15, one 15 s timeout voided
    benchmark cell ARM-T7-FLASH-r1 at $0 while the endpoint answered in 0.5 s a minute later.
    Any parsed answer - available, insufficient, exhausted, auth-error, malformed - is an answer
    and never retries, and neither does a `rate-limit` failure: the endpoint asked for less
    traffic, so another attempt would extend the lockout the caller is about to wait out. Another
    attempt runs only while it still fits LIVE_BUDGET_SECONDS, so a slow-timeout transport spends
    fewer tries rather than overrunning the preflight window.
    """
    attempt_cost = float((spec or {}).get("timeoutSeconds") or 30)
    started = time.monotonic()
    for attempt in range(1, LIVE_TRIES + 1):
        live = runner(spec, transport) if runner else _run_probe(spec, transport, _credential(cfg))
        rate_limited = (live.get("error") or {}).get("kind") == "rate-limit"
        if live.get("status") != "network-error" or rate_limited or attempt == LIVE_TRIES:
            return live
        if time.monotonic() - started + RETRY_SLEEP_SECONDS + attempt_cost > LIVE_BUDGET_SECONDS:
            return live
        time.sleep(RETRY_SLEEP_SECONDS)
    return live


def routing_view(reading):
    """Reduce a normalized reading to its routing band and the window rows behind it, or None.

    One reduction over two fetchers: `provider_bands.probe` reads live-first capacity and
    `budget_posture.provider_band` reads the cache only, under the prompt hook's deadline. Both
    then ask the same question — which band routes, and which window's pace that band came from —
    so the rule lives here, beside the reading shape it reads.

    Returns {"band", "binding", "routing"}: `binding` is the window that will actually stop work,
    `routing` the one whose band a routing comparison may weigh. A window the reading does not
    name is {}.
    """
    if not isinstance(reading, dict):
        return None
    quota = reading.get("quota")
    if not isinstance(quota, dict):
        quota = {}
    band = {"exhausted": "Exhausted", "dispatch-floor": "Dispatch floor"}.get(
        reading.get("status"), quota.get("routingBand"))
    if not band:
        return None
    windows = [w for w in (quota.get("windows") or []) if isinstance(w, dict)]

    def row(key):
        return next((w for w in windows if w.get("name") == quota.get(key)), None) or {}

    return {"band": band, "binding": row("bindingWindow"), "routing": row("routingWindow")}


def floor_refusal(reading, now=None):
    """The measured admission cutoff, shared by native, launcher and plan-room output."""
    quota = reading.get("quota") or {}
    name = quota.get("bindingWindow")
    row = next(w for w in quota.get("windows") or [] if w.get("name") == name)
    reset = _epoch(row["resetsAt"])
    now = time.time() if now is None else now
    when = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(reset))
    minutes = max(0, int((reset - now) / 60))
    return ("%s %s window %g%% used, at its %g%% dispatch floor "
            "(a job started here can run out mid-step); resets %s (in %d min)" %
            (reading["transport"], name, row["usedPercent"], row["dispatchFloorPercent"], when, minutes))


def _cache_fallback(cached, kind, now, message=None):
    """The cached reading served where a live probe failed, typed with that failure."""
    fallback = copy.deepcopy(cached)
    fallback["sourceKind"] = "cache"
    observed = _epoch(cached.get("observedAt"))
    age = int(max(0.0, now - observed)) if observed is not None else 0
    fallback["liveFailure"] = {
        "kind": kind if kind in FALLBACK_KINDS else "network",
        "message": "%s; reading is %d s old" % (message or "live capacity probe failed", age),
    }
    return fallback


def _cooldown_reading(transport, cost_model, until, now):
    """The rate-limit refusal for a transport in cooldown with no usable reading behind it."""
    return {
        "schemaVersion": 1, "transport": transport, "costModel": cost_model,
        "sourceKind": "live", "observedAt": _utc(now), "status": "network-error",
        "error": {"kind": "rate-limit",
                  "message": "the %s usage endpoint is rate limited until %s" % (transport, _utc(until))},
        "liveFailure": None, "quota": None, "balance": None,
    }


def cached(transport, data=None, now=None, cache_path=None):
    """Return a fresh normalized cached reading without starting a live probe."""
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    cfg = (data.get("transports") or {}).get(transport)
    if not isinstance(cfg, dict):
        return None
    return _fresh(cache_path or _cache_path(), transport, cfg.get("costModel"), now)


def reading(transport, data=None, now=None, cache_path=None, runner=None):
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    cfg = (data.get("transports") or {}).get(transport)
    if not isinstance(cfg, dict):
        raise CapacityError("unknown transport: " + transport)
    cost_model = cfg.get("costModel")
    cache_path = cache_path or _cache_path()
    fresh = _fresh(cache_path, transport, cost_model, now)
    if fresh is not None:
        return fresh
    if cfg.get("capacityUnsupported") is not None:
        return {
            "schemaVersion": 1, "transport": transport, "costModel": cost_model,
            "sourceKind": "unsupported", "observedAt": _utc(now), "status": "unsupported",
            "error": None, "liveFailure": None, "quota": None, "balance": None,
        }
    spec = cfg.get("capacityProbe")
    if not isinstance(spec, dict):
        raise CapacityError("transport has no capacity source: " + transport)
    cooling = _cooldown_answer(cache_path, transport, cost_model, now)
    if cooling is not None:
        return cooling
    # The lock covers the recheck, the probe and its store, so a waiter that takes it next finds
    # the stored reading or cooldown. An unloadable lock module probes unlocked: more reads, never none.
    locks = _load_file_lock()
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    lock_path = _live_lock_path(transport, cache_path)
    handle = locks.acquire(lock_path, 0) if locks else None
    waited = locks is not None and handle is None
    if waited:
        handle = locks.acquire(lock_path, LIVE_BUDGET_SECONDS)
    if locks is not None and handle is None:
        return _failed_read(transport, cost_model, cache_path, now, "another process is reading %s capacity" % transport)
    try:
        fresh = _fresh(cache_path, transport, cost_model, now)
        if fresh is not None:
            return fresh
        cooling = _cooldown_answer(cache_path, transport, cost_model, now)
        if cooling is not None:
            return cooling
        if waited:
            return _failed_read(transport, cost_model, cache_path, now,
                                "another process just read %s capacity and stored no result" % transport)
        return _settle(live_reading(spec, transport, cfg, runner), transport, cost_model, cache_path, now)
    finally:
        if handle is not None:
            locks.release(handle)


def _failed_read(transport, cost_model, cache_path, now, message):
    return _settle({
        "schemaVersion": 1, "transport": transport, "costModel": cost_model,
        "sourceKind": "live", "observedAt": _utc(now), "status": "network-error",
        "error": {"kind": "network", "message": message},
        "liveFailure": None, "quota": None, "balance": None,
    }, transport, cost_model, cache_path, now)


def _cooldown_answer(cache_path, transport, cost_model, now):
    """The reading to return while `transport` is in a rate-limit cooldown, else None.

    A rate-limited endpoint is not asked again before its cooldown ends: another read only extends
    the lockout, and every session and hook sharing this plan would pay it.
    """
    until = _rate_limited_until(cache_path, transport)
    if until is None or until <= now:
        return None
    cached = _read_cache(cache_path, transport, cost_model, now, STALE_FALLBACK_SECONDS)
    if cached is not None:
        return _cache_fallback(cached, "rate-limit", now, "rate limited until " + _utc(until))
    return _cooldown_reading(transport, cost_model, until, now)


def _settle(live, transport, cost_model, cache_path, now):
    """Validate a live result, store it or its cooldown, and fall back to a stale cache on failure."""
    if live.get("costModel") == "unknown":
        live["costModel"] = cost_model
    errors = validate_reading(live, transport, cost_model)
    if errors:
        raise CapacityError("invalid %s capacity result: %s" % (transport, "; ".join(errors)))
    if live.get("status") in CACHEABLE and live.get("sourceKind") == "live":
        _write_cache(cache_path, transport, copy.deepcopy(live))
        return live
    if live.get("status") == "network-error":
        kind = (live.get("error") or {}).get("kind")
        if kind == "rate-limit":
            _record_rate_limit(cache_path, transport, now + RATE_LIMIT_COOLDOWN_SECONDS)
        cached = _read_cache(cache_path, transport, cost_model, now, STALE_FALLBACK_SECONDS)
        if cached is not None:
            fallback = _cache_fallback(cached, kind, now, (live.get("error") or {}).get("message"))
            if validate_reading(fallback, transport, cost_model):
                raise CapacityError("generated cache fallback is invalid")
            return fallback
    return live


def main(argv):
    if len(argv) != 1:
        print("usage: provider_capacity.py <transport>", file=sys.stderr)
        return 2
    try:
        value = reading(argv[0])
    except CapacityError as exc:
        print("provider capacity failed: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(value, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
