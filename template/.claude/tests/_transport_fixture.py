"""Pin the transport a guard proof runs under.

Every hook that reads `_session_transport` resolves from the ENVIRONMENT. A proof that inherits the
developer's environment therefore asserts against whatever seat they happen to be on: the same file
passes on an Anthropic seat and fails on a codex one, where `sonnet` is denied by the VOCABULARY
rule before the rule under test is ever reached. Measured 2026-09-08 — a codex session running
`test_workflow_provider_guard_effort.py` failed `sonnet·medium ... != deny`, which is the guard
behaving correctly and the proof asserting a host assumption it never declared.

Not named `test_*.py` / `*_test.py` on purpose: `harness_tests.py` discovers by those patterns, and
a helper that runs as a proof reports a pass for having no cases.

    from _transport_fixture import hook_env
    subprocess.run([sys.executable, HOOK], env=hook_env(), ...)          # anthropic seat
    subprocess.run([sys.executable, HOOK], env=hook_env("codex"), ...)   # provider seat
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_CAPACITY_CACHE = None


def _available_capacity_cache():
    """Plant one fresh `available` reading per probed transport, once per process.

    Native-dispatch guards consult live provider capacity. A proof that reaches that probe
    asserts the host's network, quota and credentials, so a fresh checkout or CI runner denies
    with "network capacity check failed" before the rule under test runs.
    """
    global _CAPACITY_CACHE
    if _CAPACITY_CACHE:
        return _CAPACITY_CACHE
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
    import model_registry
    now = time.time()
    blob = {}
    for name, cfg in (model_registry.load().get("transports") or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("capacityProbe"):
            continue
        entry = {"schemaVersion": 1, "transport": name, "costModel": cfg.get("costModel"),
                 "sourceKind": "live", "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                 "status": "available", "error": None, "liveFailure": None, "quota": None, "balance": None}
        if cfg.get("costModel") == "plan-quota":
            entry["quota"] = {"windows": [{"name": "seven_day", "usedPercent": 10}],
                              "dispatchBand": "Normal", "routingBand": "Normal",
                              "bindingWindow": "seven_day", "credits": None}
        else:
            entry["balance"] = {"amount": 100.0, "currency": "USD"}
        blob[name] = entry
    folder = tempfile.mkdtemp(prefix="transport_fixture_")
    atexit.register(shutil.rmtree, folder, True)
    _CAPACITY_CACHE = os.path.join(folder, "provider_capacity.json")
    with open(_CAPACITY_CACHE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(blob, fh)
    return _CAPACITY_CACHE


def hook_env(transport="anthropic", **extra):
    """A child environment whose seat is `transport`, whatever seat the caller runs on.

    Clears BOTH signals first. `resolve()` prefers `CLAUDE_CODE_TRANSPORT` and falls back to
    matching `ANTHROPIC_BASE_URL`'s host, so leaving the URL behind lets the developer's proxy
    decide the seat on any run where the explicit name is absent. Provider capacity reads a
    planted `available` cache unless the caller passes its own `PROVIDER_CAPACITY_CACHE`.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_TRANSPORT", None)
    env.pop("ANTHROPIC_BASE_URL", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if transport:
        env["CLAUDE_CODE_TRANSPORT"] = transport
    if "PROVIDER_CAPACITY_CACHE" not in extra:
        env["PROVIDER_CAPACITY_CACHE"] = _available_capacity_cache()
        env["PROVIDER_CAPACITY_ADVISORY"] = "1"
    env.update(extra)
    return env
