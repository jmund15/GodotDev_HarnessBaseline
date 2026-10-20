#!/usr/bin/env python3
"""Proof for Anthropic's live OAuth usage capacity adapter."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "anthropic_capacity_probe.py"
PLANTED_KEY = "sk-" + "planted"
ENV_OAUTH = "env-oauth-" + "token"


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {}
    headers_seen = {}

    def do_GET(self):
        type(self).headers_seen = dict(self.headers)
        raw = type(self).payload
        body = json.dumps(raw).encode("utf-8") if not isinstance(raw, bytes) else raw
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def base_payload(extra_enabled=False):
    now = time.time()
    week_reset = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 2 * 86400))
    return {
        "five_hour": {"utilization": 0.0, "resets_at": None},
        "seven_day": {"utilization": 100.0, "resets_at": week_reset},
        "limits": [
            {"group": "weekly", "kind": "weekly_all", "percent": 100,
             "resets_at": week_reset, "is_active": True,
             "severity": "critical", "scope": None},
            {"group": "weekly", "kind": "weekly_scoped", "percent": 75,
             "resets_at": week_reset, "is_active": False,
             "severity": "warning", "scope": {"model": {"display_name": "Fable"}}},
        ],
        "extra_usage": {
            "is_enabled": extra_enabled, "user_disabled": not extra_enabled,
            "spend_limit_reached": False, "used_credits": 0,
        },
    }


def dispatch_floor_cases():
    """A window with too little left to finish a job refuses dispatch before it hits 100%."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("anthropic_capacity_probe", SCRIPT)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    def status(five, seven, extra_enabled=False):
        now = 1_790_000_000
        payload = {"five_hour": {"utilization": five, "resets_at": now + 4 * 3600},
                   "seven_day": {"utilization": seven, "resets_at": now + 2 * 86400},
                   "limits": [],
                   "extra_usage": {"is_enabled": extra_enabled, "user_disabled": not extra_enabled,
                                   "spend_limit_reached": False, "used_credits": 0}}
        out = probe.summarize(payload, now=now)
        return out.get("status"), (out.get("quota") or {}).get("bindingWindow")

    return [
        ("five-hour window at 98% used refuses dispatch", status(98.0, 50.0) == ("dispatch-floor", "five_hour")),
        ("five-hour window at 97.9% used still dispatches", status(97.9, 50.0)[0] == "available"),
        ("seven-day window at 99% used refuses dispatch", status(10.0, 99.0) == ("dispatch-floor", "seven_day")),
        ("seven-day window at 98.9% used still dispatches", status(10.0, 98.9)[0] == "available"),
        ("usable extra usage excuses the floor", status(98.0, 50.0, extra_enabled=True)[0] == "available"),
    ]


NOW = 1_800_000_000  # fixed clock: these cases must not read the wall clock


def valid(reading):
    """The shared validator's errors for an Anthropic reading."""
    import importlib.util
    path = Path(SCRIPT).resolve().parents[1] / "tools" / "provider_capacity.py"
    spec = importlib.util.spec_from_file_location("provider_capacity_for_probe", path)
    capacity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capacity)
    return capacity.validate_reading(reading, "anthropic", "plan-quota")


def headroom_cases():
    """A 99%-used weekly cap 22 hours before reset is Hot, though its raw pace is ~1.14.

    Run-out ratio: elapsed = 1 - 79200/604800 = 0.869, rate = 99/0.869 = 113.9 %-per-week,
    so the last 1% lasts 0.0088 week against 0.1310 week to reset -> 0.067.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("anthropic_capacity_probe_headroom", SCRIPT)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    def reading(extra_enabled):
        payload = {
            "five_hour": {"utilization": 10.0, "resets_at": NOW + 4 * 3600},
            "seven_day": {"utilization": 99.0, "resets_at": NOW + 22 * 3600},
            "limits": [],
            "extra_usage": {"is_enabled": extra_enabled, "user_disabled": not extra_enabled,
                            "spend_limit_reached": False, "used_credits": 0},
        }
        return probe.summarize(payload, now=NOW)

    credited = reading(True)
    quota = credited.get("quota") or {}
    weekly = next((w for w in quota.get("windows") or [] if w.get("name") == "seven_day"), {})
    ratio = weekly.get("runoutRatio")
    near_expected = (isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
                     and 0.065 <= ratio <= 0.070)
    spent_quota = (reading(False).get("quota") or {})
    credited_floor = probe.summarize({
        "five_hour": {"utilization": 98.0, "resets_at": NOW + 5 * 60},
        "seven_day": {"utilization": 99.0, "resets_at": NOW + 22 * 3600},
        "limits": [],
        "extra_usage": {"is_enabled": True, "user_disabled": False,
                        "spend_limit_reached": False},
    }, now=NOW)
    invalid_week = probe.summarize({
        "five_hour": {"utilization": 10.0, "resets_at": NOW + 4 * 3600},
        "seven_day": {"utilization": 5.0, "resets_at": NOW + 30 * 86400},
        "limits": [],
    }, now=NOW)
    dormant = probe.summarize({
        "five_hour": {"utilization": 0.0, "resets_at": None},
        "seven_day": {"utilization": 50.0, "resets_at": NOW + 2 * 86400},
        "limits": [],
    }, now=NOW)
    dormant_quota = dormant.get("quota") or {}
    dormant_five = next((w for w in dormant_quota.get("windows") or []
                        if w.get("name") == "five_hour"), {})
    invalid_active = probe.summarize({
        "five_hour": {"utilization": 1.0, "resets_at": None},
        "seven_day": {"utilization": 50.0, "resets_at": NOW + 2 * 86400},
    }, now=NOW)
    dormant_only = probe.summarize({
        "five_hour": {"utilization": 0.0, "resets_at": None},
    }, now=NOW)
    fresh = probe.summarize({
        "five_hour": {"utilization": 0.0, "resets_at": None},
        "seven_day": {"utilization": 0.0, "resets_at": None},
        "limits": [],
    }, now=NOW)
    fresh_quota = fresh.get("quota") or {}
    dormant_week = probe.summarize({
        "five_hour": {"utilization": 60.0, "resets_at": NOW + 2 * 3600},
        "seven_day": {"utilization": 0.0, "resets_at": None},
        "limits": [],
    }, now=NOW)
    dormant_week_quota = dormant_week.get("quota") or {}
    dormant_week_five = next((w for w in dormant_week_quota.get("windows") or []
                              if w.get("name") == "five_hour"), {})
    return [
        ("usable extra usage admits a 99%-weekly / 22h-to-reset capture",
         credited.get("status") == "available"),
        ("a 99%-used week 22h from reset routes Hot on the weekly risk band",
         quota.get("routingBand") == "Hot" and quota.get("routingWindow") == "seven_day"),
        ("the weekly row grades Hot and carries its projected run-out ratio (~0.067)",
         weekly.get("band") == "Hot" and near_expected),
        ("the headroom-Hot week is the dispatch and binding window",
         quota.get("dispatchBand") == "Hot" and quota.get("bindingWindow") == "seven_day"),
        ("without extra usage the cap hits its dispatch floor while weekly risk stays Hot",
         reading(False).get("status") == "dispatch-floor"
         and spent_quota.get("dispatchBand") == "Dispatch floor"
         and spent_quota.get("routingBand") == "Hot"
         and spent_quota.get("routingWindow") == "seven_day"),
        ("extra usage does not let a spent-but-safe short cap steal the Hot binding window",
         credited_floor.get("status") == "available"
         and (credited_floor.get("quota") or {}).get("bindingWindow") == "seven_day"),
        ("a reported weekly reset beyond its window is malformed, not a borrowed short band",
         invalid_week.get("status") == "malformed" and invalid_week.get("quota") is None),
        ("an unused short window without reset is a Surplus row at pressure 0 beside a valid week",
         dormant.get("status") == "available" and dormant_five.get("usedPercent") == 0.0
         and dormant_five.get("band") == "Surplus" and dormant_five.get("pressure") == 0.0
         and dormant_five.get("resetsAt") is None and dormant_five.get("runoutRatio") is None
         and dormant_quota.get("routingWindow") == "seven_day"
         and valid(dormant) == []),
        ("positive use without reset stays malformed despite a valid week",
         invalid_active.get("status") == "malformed" and invalid_active.get("quota") is None),
        ("a dormant window alone is available at Surplus, never malformed",
         dormant_only.get("status") == "available"
         and (dormant_only.get("quota") or {}).get("dispatchBand") == "Surplus"
         and (dormant_only.get("quota") or {}).get("routingBand") == "Surplus"
         and valid(dormant_only) == []),
        ("a fresh account with every window dormant is available at Surplus",
         fresh.get("status") == "available" and fresh_quota.get("dispatchBand") == "Surplus"
         and fresh_quota.get("routingBand") == "Surplus"
         and all(w.get("band") == "Surplus" for w in fresh_quota.get("windows") or [])
         and valid(fresh) == []),
        ("a dormant week routes on the live five-hour window, not a missing band",
         dormant_week.get("status") == "available"
         and dormant_week_quota.get("routingWindow") == "five_hour"
         and dormant_week_quota.get("routingBand") == dormant_week_five.get("band")
         and dormant_week_quota.get("routingBand") is not None
         and valid(dormant_week) == []),
    ]


def run(url, cred_path, **extra_env):
    env = dict(os.environ, ANTHROPIC_USAGE_URL=url,
               ANTHROPIC_CREDENTIALS_PATH=str(cred_path), PYTHONIOENCODING="utf-8")
    for key in ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(key, None)
    env.update(extra_env)
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=20)
    try:
        payload = json.loads(result.stdout)
    except Exception:
        payload = None
    return result, payload


ROOT = SCRIPT.parents[2]
CAPACITY = ROOT / ".claude" / "tools" / "provider_capacity.py"
SECONDARY = "anthropic-secondary"
REFRESH_MODEL = "claude-haiku-4-5-20251001"
STUB = r"""
import json, os, subprocess, sys, time
from pathlib import Path
rec = {"cwd": os.getcwd(), "argv": sys.argv[1:],
       "env": {k: os.environ.get(k) for k in ("HARNESS_PLAN_REFRESH", "CLAUDE_CODE_ENTRYPOINT",
                                              "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR")},
       "leaked": sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC"))
                        and k not in ("CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR"))}
mode = os.environ.get("STUB_MODE", "refresh")
if mode == "recurse":
    r = subprocess.run([sys.executable, os.environ["STUB_PROBE"]], capture_output=True, text=True,
                       env=dict(os.environ, PROVIDER_CAPACITY_TRANSPORT="anthropic-secondary"), timeout=30)
    try:
        rec["nested"] = json.loads(r.stdout).get("status")
    except Exception:
        rec["nested"] = "unparseable rc=%s" % r.returncode
if mode == "slow":
    time.sleep(2)
if mode in ("refresh", "slow"):
    cred = Path(os.getcwd()) / ".credentials.json"
    data = json.loads(cred.read_text(encoding="utf-8"))
    data["claudeAiOauth"]["accessToken"] = "refreshed-token"
    data["claudeAiOauth"]["expiresAt"] = int((time.time() + 8 * 3600) * 1000)
    cred.write_text(json.dumps(data), encoding="utf-8")
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec) + "\n")
if mode == "fail":
    sys.exit(1)
print('{"is_error": false, "result": "OK"}')
"""


def _cred(path, token, expires_ms, tier):
    oauth = {"accessToken": token, "rateLimitTier": tier}
    if expires_ms is not None:
        oauth["expiresAt"] = expires_ms
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")


def _plan_fixture(root):
    """A registry copy whose two Anthropic plans log in under planted dirs, the secondary live."""
    sys.path.insert(0, str(ROOT / ".claude" / "tools"))
    import model_registry
    data = json.loads(json.dumps(model_registry.load()))
    primary, secondary = root / "primary", root / "secondary"
    data["transports"]["anthropic"]["configDir"] = str(primary)
    plan = data["transports"][SECONDARY]
    plan["configDir"] = str(secondary)
    plan["status"] = {"state": "available"}
    plan["capacityProbe"]["refresh"] = {"model": REFRESH_MODEL, "effort": "low", "timeoutSeconds": 15}
    registry = root / "external_models.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    stub_py = root / "stub_claude.py"
    stub_py.write_text(STUB, encoding="utf-8")
    if os.name == "nt":
        stub = root / "stub_claude.cmd"
        stub.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, stub_py), encoding="utf-8")
    else:
        stub = root / "stub_claude"
        stub.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, stub_py), encoding="utf-8")
        stub.chmod(0o755)
    return registry, primary, secondary, stub


def _plan_env(root, registry, stub, url, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_CREDENTIALS_PATH", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
                        "HARNESS_PLAN_REFRESH", "CLAUDE_CODE_REMOTE")}
    env.update(HARNESS_MODEL_REGISTRY=str(registry), PROVIDER_CAPACITY_CACHE=str(root / "cache.json"),
               ANTHROPIC_USAGE_URL=url, HARNESS_HOOK_STATE_DIR=str(root / "state"),
               CLAUDE_BIN=str(stub), STUB_LOG=str(root / "stub.log"), STUB_PROBE=str(SCRIPT),
               PYTHONIOENCODING="utf-8",
               # Host variables the refresh child must not inherit.
               CLAUDE_CODE_SESSION_ID="planted-session", ANTHROPIC_BASE_URL="http://planted.invalid",
               CLAUDE_CODE_ENTRYPOINT="claude-desktop", ANTHROPIC_API_KEY=PLANTED_KEY)
    env.update(extra)
    return env


def _capacity(transport, env):
    result = subprocess.run([sys.executable, str(CAPACITY), transport], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=60)
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"_rc": result.returncode, "_stderr": result.stderr[-300:]}


def _stub_calls(root):
    log = root / "stub.log"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _same_dir(a, b):
    return os.path.normcase(os.path.realpath(a or "")) == os.path.normcase(os.path.realpath(str(b)))


def plan_cases(url):
    """A plan's probe reads its own login dir, stamps its own name and refreshes an expired token once."""
    cases = []
    future = int((time.time() + 3600) * 1000)
    past = int((time.time() - 3600) * 1000)

    def fresh(tag):
        root = Path(tempfile.mkdtemp(prefix="anthropic_plan_%s_" % tag))
        registry, primary, secondary, stub = _plan_fixture(root)
        _cred(primary / ".credentials.json", "primary-token", future, "default_claude_max_20x")
        return root, registry, primary, secondary, stub

    Handler.status = 200
    Handler.payload = base_payload(True)

    root, registry, primary, secondary, stub = fresh("live")
    _cred(secondary / ".credentials.json", "secondary-token", future, "default_claude_max_5x")
    env = _plan_env(root, registry, stub, url)
    got = _capacity(SECONDARY, env)
    seen = Handler.headers_seen.get("Authorization")
    cases.append(("a plan's reading is available under its own name from its configDir credential",
                  got.get("status") == "available" and got.get("transport") == SECONDARY
                  and seen == "Bearer secondary-token"))
    cases.append(("planType comes from the secondary rateLimitTier", got.get("planType") == "max_5x"))
    primary_got = _capacity("anthropic", env)
    cases.append(("the primary plan reads its own configDir and plan size",
                  primary_got.get("transport") == "anthropic" and primary_got.get("planType") == "max_20x"
                  and Handler.headers_seen.get("Authorization") == "Bearer primary-token"))
    cases.append(("a valid token runs no refresh", _stub_calls(root) == []))

    # A plan the registry does not name (planted registry, snapshot, unreadable file) has no login of
    # its own: reading the default login instead would file the primary's quota under its name.
    root, registry, primary, secondary, stub = fresh("unknown")
    data = json.loads(registry.read_text(encoding="utf-8"))
    del data["transports"][SECONDARY]
    registry.write_text(json.dumps(data), encoding="utf-8")
    Handler.headers_seen = {}
    env = _plan_env(root, registry, stub, url, PROVIDER_CAPACITY_TRANSPORT=SECONDARY)
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=60)
    try:
        got = json.loads(result.stdout)
    except Exception:
        got = {"_rc": result.returncode}
    cases.append(("a plan the registry does not name never reads the default login",
                  got.get("status") == "auth-error" and got.get("planType") is None
                  and "Authorization" not in Handler.headers_seen))

    root, registry, primary, secondary, stub = fresh("empty")
    secondary.mkdir(parents=True, exist_ok=True)
    (secondary / ".credentials.json").write_text("{}", encoding="utf-8")
    env = _plan_env(root, registry, stub, url, CLAUDE_CODE_OAUTH_TOKEN=ENV_OAUTH)
    got = _capacity(SECONDARY, env)
    cases.append(("an empty secondary credential ignores CLAUDE_CODE_OAUTH_TOKEN and reads auth-error",
                  got.get("status") == "auth-error" and got.get("transport") == SECONDARY))

    root, registry, primary, secondary, stub = fresh("refresh")
    _cred(secondary / ".credentials.json", "stale-token", past, "default_claude_max_5x")
    env = _plan_env(root, registry, stub, url, STUB_MODE="refresh")
    got = _capacity(SECONDARY, env)
    calls = _stub_calls(root)
    call = calls[0] if calls else {}
    call_env = call.get("env") or {}
    argv = call.get("argv") or []
    cases.append(("an expired token runs the CLI once and then reads available",
                  len(calls) == 1 and got.get("status") == "available"
                  and Handler.headers_seen.get("Authorization") == "Bearer refreshed-token"))
    cases.append(("the refresh runs in the plan's configDir with that dir as CLAUDE_CONFIG_DIR",
                  bool(call) and _same_dir(call.get("cwd"), secondary)
                  and _same_dir(call_env.get("CLAUDE_CONFIG_DIR"), secondary)))
    cases.append(("the refresh env carries the sentinel, ENTRYPOINT=cli, a blank API key and no host var",
                  bool(call) and call_env.get("HARNESS_PLAN_REFRESH") == "1"
                  and call_env.get("CLAUDE_CODE_ENTRYPOINT") == "cli"
                  and call_env.get("ANTHROPIC_API_KEY") == "" and call.get("leaked") == []))
    cases.append(("the refresh turn is pinned by the registry's capacityProbe.refresh",
                  "--model" in argv and argv[argv.index("--model") + 1] == REFRESH_MODEL
                  and "--effort" in argv and argv[argv.index("--effort") + 1] == "low"
                  and "--no-session-persistence" in argv))

    root, registry, primary, secondary, stub = fresh("race")
    _cred(secondary / ".credentials.json", "stale-token", past, "default_claude_max_5x")
    env = _plan_env(root, registry, stub, url, STUB_MODE="slow")
    procs = [subprocess.Popen([sys.executable, str(CAPACITY), SECONDARY], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
             for _ in range(2)]
    outs = []
    for proc in procs:
        out, _err = proc.communicate(timeout=60)
        try:
            outs.append(json.loads(out).get("status"))
        except Exception:
            outs.append("unparseable")
    cases.append(("two concurrent probes refresh once and both read available",
                  len(_stub_calls(root)) == 1 and outs == ["available", "available"]))

    root, registry, primary, secondary, stub = fresh("recurse")
    _cred(secondary / ".credentials.json", "stale-token", past, "default_claude_max_5x")
    env = _plan_env(root, registry, stub, url, STUB_MODE="recurse")
    got = _capacity(SECONDARY, env)
    calls = _stub_calls(root)
    cases.append(("a nested probe inside the refresh reads auth-error instead of recursing",
                  len(calls) == 1 and calls[0].get("nested") == "auth-error"
                  and got.get("status") == "auth-error"))

    root, registry, primary, secondary, stub = fresh("fail")
    _cred(secondary / ".credentials.json", "stale-token", past, "default_claude_max_5x")
    env = _plan_env(root, registry, stub, url, STUB_MODE="fail")
    first = _capacity(SECONDARY, env)
    second = _capacity(SECONDARY, env)
    cases.append(("a failed refresh is not retried within the cache TTL",
                  len(_stub_calls(root)) == 1 and first.get("status") == "auth-error"
                  and second.get("status") == "auth-error"))

    sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
    import model_name_guard
    verdict = model_name_guard.process({
        "tool_name": "Write",
        "tool_input": {"file_path": str(ROOT / ".claude" / "scripts" / "_unwritten_probe_copy.py"),
                       "content": SCRIPT.read_text(encoding="utf-8")}})
    cases.append(("model_name_guard passes the probe text", not (verdict or {}).get("deny")))
    return cases


def _probe_module():
    spec = importlib.util.spec_from_file_location("probe_deadline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deadline_cases():
    """The lock wait, the refresh and the usage GET all end inside the probe's registered timeout."""
    sys.path.insert(0, str(ROOT / ".claude" / "tools"))
    import model_registry
    floor = model_registry.PROBE_GET_FLOOR_SECONDS
    startup = model_registry.PROBE_STARTUP_SECONDS
    cases = []
    root = Path(tempfile.mkdtemp(prefix="anthropic_deadline_"))
    cred = root / ".credentials.json"
    _cred(cred, "stale-token", int((time.time() - 3600) * 1000), "default_claude_max_5x")
    probe = _probe_module()
    waits = []

    class Locks:
        @staticmethod
        def acquire(_path, timeout):
            waits.append(timeout)
            return None

        @staticmethod
        def release(_handle):
            pass

    real_load = probe._load
    probe._load = lambda d, f, n: Locks if f == "_file_lock.py" else real_load(d, f, n)
    saved = os.environ.get("HARNESS_HOOK_STATE_DIR")
    os.environ["HARNESS_HOOK_STATE_DIR"] = str(root / "state")
    cfg = {"configDir": str(root), "default": False, "budget": 35,
           "refresh": {"model": REFRESH_MODEL, "timeoutSeconds": 15}}
    try:
        late = probe.refresh_if_expired(cfg, str(cred), deadline=time.monotonic() + 10)
        cases.append(("a probe waiting on a peer's refresh stops waiting while the usage GET still fits",
                      late is False and len(waits) == 1 and 0 < waits[0] <= 10 - floor))
        del waits[:]
        spent = probe.refresh_if_expired(cfg, str(cred), deadline=time.monotonic() + floor - 1)
        cases.append(("a probe with no room left after the GET's share refreshes nothing",
                      spent is False and waits == []))
    except TypeError as exc:
        cases.append(("refresh_if_expired takes the probe's deadline", False))
        cases.append(("(deadline case skipped: %s)" % exc, False))
    finally:
        probe._load = real_load
        if saved is None:
            os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
        else:
            os.environ["HARNESS_HOOK_STATE_DIR"] = saved

    probe = _probe_module()
    asked = []
    probe.plan_config = lambda *_a: {"configDir": str(root), "default": False, "refresh": None, "budget": 9}
    probe.credential_path = lambda _cfg: str(cred)
    probe.read_live = lambda url, token, timeout=15: asked.append(timeout) or probe.outcome("available")
    with contextlib.redirect_stdout(io.StringIO()):
        probe.main()
    cases.append(("the usage GET's timeout is the time left before the probe's deadline",
                  len(asked) == 1 and 0 < asked[0] <= 9 - startup))

    registry = root / "external_models.json"
    data = json.loads(json.dumps(model_registry.load()))
    os.environ["HARNESS_PROBE_TEST_HOME"] = os.path.expanduser("~")
    data["transports"]["anthropic"]["configDir"] = "$HARNESS_PROBE_TEST_HOME/.claude/"
    registry.write_text(json.dumps(data), encoding="utf-8")
    saved = os.environ.get("HARNESS_MODEL_REGISTRY")
    os.environ["HARNESS_MODEL_REGISTRY"] = str(registry)
    try:
        cfg = _probe_module().plan_config("anthropic")
        second = _probe_module().plan_config(SECONDARY)
    finally:
        if saved is None:
            os.environ.pop("HARNESS_MODEL_REGISTRY", None)
        else:
            os.environ["HARNESS_MODEL_REGISTRY"] = saved
    cases.append(("a $VAR spelling of ~/.claude is the default login", (cfg or {}).get("default") is True))
    cases.append(("plan_config carries the registered probe timeout as the probe's budget",
                  (second or {}).get("budget") == 35 and (cfg or {}).get("budget") == 20))
    return cases


def main():
    if not SCRIPT.is_file():
        print("FAIL anthropic_capacity_probe.py exists")
        return 1
    root = Path(tempfile.mkdtemp(prefix="anthropic_capacity_"))
    cred = root / "credentials.json"
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-oauth-token"}}), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/api/oauth/usage" % server.server_port
    cases = []
    try:
        Handler.status = 200
        Handler.payload = base_payload(False)
        spent_run, spent = run(url, cred)
        spent_quota = (spent or {}).get("quota") or {}
        spent_week = next((w for w in spent_quota.get("windows") or []
                           if w.get("name") == "seven_day"), {})
        headers = {key.lower(): value for key, value in Handler.headers_seen.items()}
        cases.append(("live OAuth endpoint exits zero", spent_run.returncode == 0))
        cases.append(("OAuth and first-party beta headers reach the endpoint",
                      headers.get("authorization") == "Bearer secret-oauth-token"
                      and "oauth-2025-04-20" in headers.get("anthropic-beta", "")))
        cases.append(("spent weekly allowance is exhausted",
                      spent and spent.get("status") == "exhausted"
                      and spent_quota.get("dispatchBand") == "Exhausted"))
        cases.append(("five-hour and seven-day windows are preserved",
                      {w.get("name") for w in spent_quota.get("windows") or []}
                      == {"five_hour", "seven_day"}))
        cases.append(("seven-day window owns routing band",
                      spent_quota.get("routingBand") == spent_week.get("band")
                      and spent_quota.get("routingWindow") == "seven_day"))
        cases.append(("credential never appears in output",
                      "secret-oauth-token" not in (spent_run.stdout + spent_run.stderr)))

        Handler.payload = base_payload(True)
        credit_run, credited = run(url, cred)
        credited_quota = (credited or {}).get("quota") or {}
        cases.append(("usable extra usage excuses plan exhaustion", credit_run.returncode == 0
                      and credited.get("status") == "available"
                      and (credited_quota.get("credits") or {}).get("available") is True))

        Handler.status = 401
        Handler.payload = {"error": "unauthorized"}
        auth_run, auth = run(url, cred)
        cases.append(("401 is a typed auth outcome", auth_run.returncode == 0
                      and auth.get("status") == "auth-error"))

        # A cloud sandbox runs authenticated but may not expose a usage-readable OAuth token.
        _, cloud_denied = run(url, cred, CLAUDE_CODE_REMOTE="true")
        cases.append(("cloud 401 is unsupported, not a re-login demand",
                      cloud_denied and cloud_denied.get("status") == "unsupported"
                      and cloud_denied.get("sourceKind") == "unsupported"))
        missing = root / "absent.json"
        _, local_missing = run(url, missing)
        cases.append(("local missing credential stays an auth outcome",
                      local_missing and local_missing.get("status") == "auth-error"))
        _, cloud_missing = run(url, missing, CLAUDE_CODE_REMOTE="true")
        cases.append(("cloud missing credential is unsupported",
                      cloud_missing and cloud_missing.get("status") == "unsupported"
                      and cloud_missing.get("sourceKind") == "unsupported"
                      and cloud_missing.get("quota") is None))
        Handler.status = 503
        Handler.payload = {"error": "busy"}
        _, cloud_busy = run(url, cred, CLAUDE_CODE_REMOTE="TRUE")
        cases.append(("cloud 5xx is unsupported: a fresh VM has no cache fallback",
                      cloud_busy and cloud_busy.get("status") == "unsupported"))
        Handler.status = 200
        Handler.payload = b"not-json"
        _, cloud_bad = run(url, cred, CLAUDE_CODE_REMOTE="true")
        cases.append(("cloud malformed response is unsupported",
                      cloud_bad and cloud_bad.get("status") == "unsupported"))
        Handler.payload = base_payload(True)
        run(url, cred, CLAUDE_CODE_OAUTH_TOKEN=ENV_OAUTH)
        cases.append(("the credentials file wins over CLAUDE_CODE_OAUTH_TOKEN",
                      Handler.headers_seen.get("Authorization") == "Bearer secret-oauth-token"))
        env_run, env_token = run(url, missing, CLAUDE_CODE_OAUTH_TOKEN=ENV_OAUTH)
        cases.append(("CLAUDE_CODE_OAUTH_TOKEN is a credential source",
                      env_token and env_token.get("status") == "available"
                      and Handler.headers_seen.get("Authorization") == "Bearer env-oauth-token"
                      and "env-oauth-token" not in env_run.stdout + env_run.stderr))

        Handler.status = 500
        Handler.payload = {"error": "boom"}
        server_run, unavailable = run(url, cred)
        cases.append(("500 is a typed server outcome", server_run.returncode == 0
                      and unavailable.get("status") == "network-error"
                      and unavailable.get("error", {}).get("kind") == "server"))

        # A 429 is the endpoint's own rate limit, not a transport fault: the reading carries its own
        # kind so the caller can wait the lockout out instead of re-probing into it.
        Handler.status = 429
        Handler.payload = {"error": "rate limited"}
        limited_run, limited = run(url, cred)
        cases.append(("429 is a typed rate-limit outcome", limited_run.returncode == 0
                      and limited.get("status") == "network-error"
                      and limited.get("error", {}).get("kind") == "rate-limit"
                      and "429" in limited.get("error", {}).get("message", "")))

        Handler.status = 200
        Handler.payload = b"not-json"
        bad_run, malformed = run(url, cred)
        cases.append(("malformed response is typed", bad_run.returncode == 0
                      and malformed.get("status") == "malformed"))
        cases.extend(plan_cases(url))
    finally:
        server.shutdown()
        server.server_close()

    cases.extend(deadline_cases())
    cases.extend(dispatch_floor_cases())
    cases.extend(headroom_cases())
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
