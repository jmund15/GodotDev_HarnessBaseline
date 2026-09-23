#!/usr/bin/env python3
"""Run a transport's live capacity probe and validate the shared result schema."""
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
LIVE_TRIES = 3
RETRY_SLEEP_SECONDS = 2.0
# One preflight stays inside the dispatch hook's 45 s --check window, retries included.
LIVE_BUDGET_SECONDS = 40.0
STATUSES = {
    "available", "exhausted", "insufficient", "auth-error",
    "network-error", "malformed", "unsupported",
}
SOURCE_KINDS = {"live", "cache", "unsupported"}
CACHEABLE = {"available", "exhausted", "insufficient"}
FAILURE_STATUSES = {"auth-error", "network-error", "malformed"}


class CapacityError(RuntimeError):
    pass


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
            if not isinstance(windows, list) or not windows:
                errors.append("quota windows missing")
            else:
                for win in windows:
                    if not isinstance(win, dict) or not isinstance(win.get("name"), str):
                        errors.append("quota window invalid")
                        continue
                    if not _finite(win.get("usedPercent"), 0, 100):
                        errors.append("quota usedPercent invalid")
            for key in ("dispatchBand", "routingBand", "bindingWindow"):
                if not isinstance(quota.get(key), str) or not quota.get(key):
                    errors.append("quota %s missing" % key)
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
        if not isinstance(failure, dict) or failure.get("kind") not in ("network", "server"):
            errors.append("cache result needs network/server liveFailure")
    elif value.get("liveFailure") is not None:
        errors.append("liveFailure is cache-only")
    return errors


def _cache_path():
    override = os.environ.get("PROVIDER_CAPACITY_CACHE")
    return override or str(ROOT / ".claude" / ".cache" / "provider_capacity.json")


def _load_lock():
    path = ROOT / ".claude" / "hooks" / "_file_lock.py"
    try:
        spec = importlib.util.spec_from_file_location("provider_capacity_file_lock", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _read_cache(path, transport, cost_model, now):
    try:
        entry = json.loads(Path(path).read_text(encoding="utf-8")).get(transport)
    except Exception:
        return None
    if validate_reading(entry, transport, cost_model):
        return None
    observed = _epoch(entry.get("observedAt"))
    if observed is None or now - observed < -300 or now - observed > CACHE_TTL_SECONDS:
        return None
    return entry


def _write_cache(path, transport, reading):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lock = _load_lock()
    lock_path = path + ".lock"
    try:
        context = lock.locked(lock_path, 2.0) if lock else _null_context()
        with context:
            try:
                blob = json.loads(Path(path).read_text(encoding="utf-8"))
                if not isinstance(blob, dict):
                    blob = {}
            except Exception:
                blob = {}
            blob[transport] = reading
            fd, temp_path = tempfile.mkstemp(prefix=".provider-capacity-", dir=str(Path(path).parent))
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(blob, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_path, path)
    except Exception:
        return False
    return True


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


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


def _run_probe(spec, transport, token=None):
    command = spec.get("command")
    args = list(spec.get("args") or [])
    if not isinstance(command, str) or not command:
        raise CapacityError("capacity probe command is missing")
    if command in ("python", "python3"):
        command = sys.executable
    timeout = spec.get("timeoutSeconds", 30)
    if not _finite(timeout, 1, 36):
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
    and never retries. Another attempt runs only while it still fits LIVE_BUDGET_SECONDS, so a
    slow-timeout transport spends fewer tries rather than overrunning the preflight window.
    """
    attempt_cost = float((spec or {}).get("timeoutSeconds") or 30)
    started = time.monotonic()
    for attempt in range(1, LIVE_TRIES + 1):
        live = runner(spec, transport) if runner else _run_probe(spec, transport, _credential(cfg))
        if live.get("status") != "network-error" or attempt == LIVE_TRIES:
            return live
        if time.monotonic() - started + RETRY_SLEEP_SECONDS + attempt_cost > LIVE_BUDGET_SECONDS:
            return live
        time.sleep(RETRY_SLEEP_SECONDS)
    return live


def cached(transport, data=None, now=None, cache_path=None):
    """Return a fresh normalized cached reading without starting a live probe."""
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    cfg = (data.get("transports") or {}).get(transport)
    if not isinstance(cfg, dict):
        return None
    return _read_cache(cache_path or _cache_path(), transport, cfg.get("costModel"), now)


def reading(transport, data=None, now=None, cache_path=None, runner=None):
    data = data if data is not None else model_registry.load()
    now = time.time() if now is None else now
    cfg = (data.get("transports") or {}).get(transport)
    if not isinstance(cfg, dict):
        raise CapacityError("unknown transport: " + transport)
    cost_model = cfg.get("costModel")
    cache_path = cache_path or _cache_path()
    if os.environ.get("PROVIDER_CAPACITY_ADVISORY") == "1":
        advisory = _read_cache(cache_path, transport, cost_model, now)
        if advisory is not None:
            return advisory
    if cfg.get("capacityUnsupported") is not None:
        return {
            "schemaVersion": 1, "transport": transport, "costModel": cost_model,
            "sourceKind": "unsupported", "observedAt": _utc(now), "status": "unsupported",
            "error": None, "liveFailure": None, "quota": None, "balance": None,
        }
    spec = cfg.get("capacityProbe")
    if not isinstance(spec, dict):
        raise CapacityError("transport has no capacity source: " + transport)
    live = live_reading(spec, transport, cfg, runner)
    if live.get("costModel") == "unknown":
        live["costModel"] = cost_model
    errors = validate_reading(live, transport, cost_model)
    if errors:
        raise CapacityError("invalid %s capacity result: %s" % (transport, "; ".join(errors)))
    if live.get("status") in CACHEABLE and live.get("sourceKind") == "live":
        _write_cache(cache_path, transport, copy.deepcopy(live))
        return live
    if live.get("status") == "network-error":
        cached = _read_cache(cache_path, transport, cost_model, now)
        if cached is not None:
            fallback = copy.deepcopy(cached)
            fallback["sourceKind"] = "cache"
            kind = (live.get("error") or {}).get("kind")
            fallback["liveFailure"] = {
                "kind": "server" if kind == "server" else "network",
                "message": str((live.get("error") or {}).get("message") or "live probe failed"),
            }
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
