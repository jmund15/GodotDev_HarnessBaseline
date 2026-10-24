#!/usr/bin/env python3
"""Read one Anthropic plan's live OAuth usage into the shared provider-capacity schema.

The plan is PROVIDER_CAPACITY_TRANSPORT (default `anthropic`), which tools/provider_capacity.py sets.
Its credential is `<configDir>/.credentials.json`; ANTHROPIC_CREDENTIALS_PATH overrides the file for
tests. CLAUDE_CODE_OAUTH_TOKEN is a fallback for the `~/.claude` plan only: that variable belongs to
the host login, and under another plan's name it would read the wrong account's usage.

An expired access token on a plan whose `capacityProbe.refresh` names a pin is refreshed once through
the CLI that owns the file: one print-mode turn at that pin, run in the plan's configDir with a
scrubbed environment. A per-configDir lock makes concurrent probes refresh once. A failed refresh is
recorded and not retried for provider_capacity.CACHE_TTL_SECONDS. HARNESS_PLAN_REFRESH=1 marks the
refresh child, so a probe running inside it reports auth-error instead of recursing.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.append(str(Path(__file__).resolve().parents[1] / "hooks"))
import model_registry  # noqa: E402
from _claude_scope import is_cloud  # noqa: E402
from provider_capacity import http_failure  # noqa: E402
from quota_bands import (  # noqa: E402
    EXHAUSTED_BAND, compute_band, dormant_band, most_constrained_window, runout_ratio_for,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DEFAULT_PLAN = "anthropic"
DEFAULT_CONFIG_DIR = "~/.claude"
COST_MODEL = "plan-quota"
REFRESH_SENTINEL = "HARNESS_PLAN_REFRESH"
REFRESH_PROMPT = "Reply with the single word OK."
DEFAULT_URL = "https://api.anthropic.com/api/oauth/usage"
# A window this close to spent refuses dispatch: a job started there runs out mid-step and leaves
# half-made edits behind.
DISPATCH_FLOOR_PERCENT = {"five_hour": 98.0, "seven_day": 99.0}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def plan_name():
    return os.environ.get("PROVIDER_CAPACITY_TRANSPORT") or DEFAULT_PLAN


def outcome(status, *, quota=None, error=None):
    return {
        "schemaVersion": 1,
        "transport": plan_name(),
        "costModel": COST_MODEL,
        "sourceKind": "live",
        "observedAt": _utc(),
        "status": status,
        "error": error,
        "liveFailure": None,
        "quota": quota,
        "balance": None,
    }


def _window(name, payload, duration_minutes, now):
    if not isinstance(payload, dict):
        return None
    used = payload.get("utilization")
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
        return None
    raw_reset = payload.get("resets_at")
    dormant = dormant_band(used, raw_reset)
    if dormant is not None:
        return {"name": name, "usedPercent": 0.0, "resetsAt": None,
                "windowDurationMins": duration_minutes, "pressure": dormant[0],
                "band": dormant[1], "runoutRatio": None}
    resets = _epoch(raw_reset)
    seconds = duration_minutes * 60
    pressure, band = compute_band(used, resets, seconds, now)
    if pressure is None:
        return None
    ratio = runout_ratio_for(used, resets, seconds, now)
    return {
        "name": name,
        "usedPercent": float(used),
        "resetsAt": resets,
        "windowDurationMins": duration_minutes,
        "pressure": round(pressure, 4) if pressure is not None else None,
        "band": band,
        "runoutRatio": round(ratio, 4) if ratio is not None else None,
    }


def summarize(payload, now=None):
    if not isinstance(payload, dict):
        return outcome("malformed", error={"kind": "malformed", "message": "response is not an object"})
    now = time.time() if now is None else now
    windows = []
    for name, duration in (("five_hour", 300), ("seven_day", 10080)):
        if name not in payload:
            continue
        window = _window(name, payload[name], duration, now)
        if window is None:
            return outcome("malformed", error={"kind": "malformed",
                                               "message": name + " window is invalid"})
        window["dispatchFloorPercent"] = DISPATCH_FLOOR_PERCENT[name]
        windows.append(window)
    if not windows:
        return outcome("malformed", error={"kind": "malformed", "message": "usage windows are missing"})
    selected = most_constrained_window(windows)
    if selected is None:
        return outcome("malformed", error={"kind": "malformed",
                                           "message": "no computable usage window"})
    extra = payload.get("extra_usage") or {}
    credit_available = bool(extra.get("is_enabled") and not extra.get("user_disabled")
                            and not extra.get("spend_limit_reached"))
    active_limits = []
    for row in payload.get("limits") or []:
        if not isinstance(row, dict):
            continue
        active_limits.append({
            "kind": row.get("kind"), "percent": row.get("percent"),
            "resetsAt": _epoch(row.get("resets_at")), "active": bool(row.get("is_active")),
            "scope": row.get("scope"),
        })
    spent_window = next((w for w in windows if w["usedPercent"] >= 100), None)
    floor_window = next((w for w in windows
                         if w["usedPercent"] >= w["dispatchFloorPercent"]), None)
    spent_limit = next((row for row in active_limits
                        if row["active"] and isinstance(row.get("percent"), (int, float))
                        and row["percent"] >= 100), None)
    exhausted = bool((spent_window or spent_limit) and not credit_available)
    at_floor = bool(floor_window and not exhausted and not credit_available)
    status = "exhausted" if exhausted else "dispatch-floor" if at_floor else "available"
    binding = spent_window if exhausted and spent_window else floor_window if at_floor else selected
    dispatch_band = EXHAUSTED_BAND if exhausted else "Dispatch floor" if at_floor else selected["band"]
    # The risk band the SESSION currency is chosen from stays the weekly one whenever the weekly
    # slot is reported and running; an absent or dormant week falls back to the binding window.
    routing_row = next((w for w in windows
                        if w["name"] == "seven_day" and w["resetsAt"] is not None), None) or binding
    routing_band = routing_row["band"]
    quota = {
        "windows": windows,
        "dispatchBand": dispatch_band,
        "routingBand": routing_band,
        "routingWindow": routing_row["name"],
        "bindingWindow": binding["name"],
        "credits": {"enabled": bool(extra.get("is_enabled")),
                    "available": credit_available, "balance": None},
        "scopes": active_limits,
    }
    return outcome(status, quota=quota)


def _load(directory, filename, name):
    path = Path(__file__).resolve().parents[1] / directory / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan_config(plan=None):
    """{configDir, default, refresh, budget} for `plan`, or None when a non-default plan has no
    registered `configDir`: only the default plan may fall back to the default login, or another
    plan's reading would carry the default login's quota under its own name. `budget` is the
    registered probe timeout this process runs under."""
    plan = plan or plan_name()
    try:
        meta = model_registry.transport_meta(plan) or {}
    except Exception:
        meta = {}
    if not meta.get("configDir") and plan != DEFAULT_PLAN:
        return None
    config_dir = meta.get("configDir") or DEFAULT_CONFIG_DIR
    probe = meta.get("capacityProbe") or {}
    refresh = probe.get("refresh")
    capacity = _load("tools", "provider_capacity.py", "probe_provider_capacity")
    return {"configDir": os.path.normpath(model_registry.expand_login_dir(config_dir)),
            "default": model_registry.is_default_login(config_dir),
            "refresh": refresh if isinstance(refresh, dict) else None,
            "budget": float(capacity.probe_timeout(probe))}


def credential_path(cfg):
    return os.environ.get("ANTHROPIC_CREDENTIALS_PATH") or os.path.join(cfg["configDir"], ".credentials.json")


def _oauth(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    return oauth if isinstance(oauth, dict) else {}


def plan_type(oauth):
    """The plan size from `rateLimitTier` (`default_claude_max_5x` -> `max_5x`), or None."""
    tier = oauth.get("rateLimitTier")
    if not isinstance(tier, str) or not tier:
        return None
    return tier[len("default_claude_"):] if tier.startswith("default_claude_") else tier


def expired(oauth, now=None):
    expires = oauth.get("expiresAt")
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return False
    return expires / 1000.0 <= (time.time() if now is None else now)


def credential(path=None, default_login=True):
    """The credentials-file token, else CLAUDE_CODE_OAUTH_TOKEN when `default_login`, else None."""
    path = path or os.environ.get("ANTHROPIC_CREDENTIALS_PATH") or str(Path.home() / ".claude" / ".credentials.json")
    token = _oauth(path).get("accessToken")
    if isinstance(token, str) and token:
        return token
    return (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None) if default_login else None


def _refresh_env(cfg):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env.update(CLAUDE_CODE_ENTRYPOINT="cli", ANTHROPIC_API_KEY="")
    env[REFRESH_SENTINEL] = "1"
    if not cfg["default"]:
        env["CLAUDE_CONFIG_DIR"] = cfg["configDir"]
    return env


def _claude_bin():
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_refresh(cfg, timeout):
    """One print-mode turn at the registry's refresh pin: no tools, no MCP, no saved session."""
    spec = cfg["refresh"]
    binary = _claude_bin()
    if not binary:
        return False
    command = [binary, "-p", REFRESH_PROMPT, "--model", str(spec.get("model"))]
    if spec.get("effort"):
        command += ["--effort", str(spec["effort"])]
    command += ["--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence", "--output-format", "json"]
    try:
        result = subprocess.run(command, cwd=cfg["configDir"], env=_refresh_env(cfg),
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def refresh_if_expired(cfg, cred_path, deadline):
    """False when an expired token on a plan with a refresh pin stays expired after one refresh.
    The lock wait and the refresh end PROBE_GET_FLOOR_SECONDS before `deadline` (time.monotonic).

    A plan without `capacityProbe.refresh` returns True: its usage GET decides, as before."""
    if cfg["refresh"] is None or not expired(_oauth(cred_path)):
        return True
    if os.environ.get(REFRESH_SENTINEL) == "1":
        return False
    state = _load("hooks", "_hook_state.py", "probe_hook_state")
    locks = _load("hooks", "_file_lock.py", "probe_file_lock")
    ttl = float(_load("tools", "provider_capacity.py", "probe_provider_capacity").CACHE_TTL_SECONDS)
    key = hashlib.sha1(model_registry.login_dir(cfg["configDir"]).encode("utf-8")).hexdigest()[:12]
    base = os.path.join(state.state_dir(), "plan_refresh-" + key)
    record = base + ".json"

    def recently_failed():
        failed = state.read_json_salvage(record).get("failedAt")
        return isinstance(failed, (int, float)) and time.time() - failed < ttl

    if recently_failed():
        return False
    refresh_timeout = float(cfg["refresh"].get("timeoutSeconds") or 15)

    def left():
        return deadline - model_registry.PROBE_GET_FLOOR_SECONDS - time.monotonic()

    if left() <= 0:
        return False
    os.makedirs(os.path.dirname(base), exist_ok=True)
    handle = locks.acquire(base + ".refresh.lock", min(refresh_timeout + 5, left()))
    if handle is None:
        return False
    try:
        # A peer holding the lock may have refreshed, or failed, while this probe waited.
        if not expired(_oauth(cred_path)):
            return True
        if recently_failed():
            return False
        if left() <= 0:
            return False
        ok = _run_refresh(cfg, min(refresh_timeout, left())) and not expired(_oauth(cred_path))
        if not ok:
            state.write_json_atomic(record, {"failedAt": time.time(), "configDir": cfg["configDir"]})
        return ok
    finally:
        locks.release(handle)


def read_live(url, token, timeout=15):
    if not token:
        return outcome("auth-error", error={"kind": "auth", "message": "Anthropic OAuth credential is missing"})
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "Accept": "application/json",
        "User-Agent": "provider-capacity/1",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status, error = http_failure(exc.code, "Anthropic usage endpoint")
        return outcome(status, error=error)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return outcome("network-error", error={"kind": "network", "message": str(exc)[:300]})
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return outcome("malformed", error={"kind": "malformed", "message": "Anthropic usage response is not JSON"})
    return summarize(payload)


def main():
    started = time.monotonic()
    url = os.environ.get("ANTHROPIC_USAGE_URL") or DEFAULT_URL
    cfg = plan_config()
    if cfg is None:
        print(json.dumps(outcome("auth-error", error={
            "kind": "auth", "message": "the registry names no configDir for plan %s" % plan_name()}),
            separators=(",", ":")))
        return 0
    # provider_capacity kills this process at budget = min(35, timeoutSeconds + refresh.timeoutSeconds)
    # s after spawn. Up to PROBE_STARTUP_SECONDS of it pass before main() runs, so the deadline is
    # budget - startup; a lock wait and a refresh stop PROBE_GET_FLOOR_SECONDS short of it, and the GET
    # gets what is left. The registry refuses a refresh timeout for which budget - refresh < startup + floor.
    deadline = started + cfg["budget"] - model_registry.PROBE_STARTUP_SECONDS
    cred_path = credential_path(cfg)
    if refresh_if_expired(cfg, cred_path, deadline):
        result = read_live(url, credential(cred_path, default_login=cfg["default"]),
                           timeout=max(1.0, min(15.0, deadline - time.monotonic())))
    else:
        result = outcome("auth-error", error={
            "kind": "auth", "message": "the %s OAuth token has expired and was not refreshed" % plan_name()})
    size = plan_type(_oauth(cred_path))
    if size:
        result["planType"] = size
    cloud = is_cloud()
    if cloud and result["status"] in ("auth-error", "network-error", "malformed"):
        # The cloud sandbox is authenticated but need not expose a usage-readable token, and a fresh
        # VM has no cached reading to fall back on. Anthropic enforces its quota server-side there,
        # so capacity is unreadable, not denied.
        result.update(sourceKind="unsupported", status="unsupported", error=None)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
