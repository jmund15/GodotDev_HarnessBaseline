"""Fixtures for the launcher and sidecar proofs: Git Bash, stub CLIs on PATH, a planted capacity cache.

Every path a proof writes lives under its own temp root; no call reaches the network or a paid provider.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

GIT_BASH = (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")


def bash_exe():
    if os.name == "nt":
        for candidate in GIT_BASH:
            if os.path.isfile(candidate):
                return candidate
    return "bash"


def write_stub(path, body):
    """An executable shell stub that runs `body` after its shebang."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    os.chmod(path, 0o755)
    return path


def planted_reading(transport, cost_model, now=None):
    """A fresh, valid capacity reading the launcher gate accepts without a live probe."""
    now = now or datetime.now(timezone.utc)
    reading = {
        "schemaVersion": 1, "transport": transport, "costModel": cost_model, "sourceKind": "live",
        "observedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "available", "error": None,
        "liveFailure": None,
    }
    if cost_model == "plan-quota":
        reset = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        reading["quota"] = {
            "windows": [{"name": "five-hour", "usedPercent": 10, "band": "On pace", "pressure": 0.2,
                         "runoutRatio": None, "resetsAt": reset}],
            "dispatchBand": "On pace", "routingBand": "On pace", "bindingWindow": "five-hour",
            "routingWindow": "five-hour", "credits": None,
        }
        reading["balance"] = None
    else:
        reading["quota"] = None
        reading["balance"] = {"amount": 50.0, "currency": "USD"}
    return reading


def run_bash(script, cwd, env, args=(), stdin=None):
    """Run a shell script under Git Bash with the proof's environment; text output."""
    return subprocess.run([bash_exe(), str(script), *args], cwd=str(cwd), env=env, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


HOST_PREFIXES = ("CLAUDE", "ANTHROPIC", "SC_", "SIDECAR", "DEEPSEEK", "OPENCODE", "CODEX", "PROVIDER_CAPACITY")


def launch_env(temp_root, path_dirs=(), **extra):
    """The environment for a launcher: HOME, temp and cache all under `temp_root`, stubs first on PATH.
    The host's launcher variables are dropped, so a session's own plan cannot steer the launch."""
    home = Path(temp_root) / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(HOST_PREFIXES)}
    env.update({
        "HOME": home.as_posix(), "USERPROFILE": str(home), "TEMP": str(temp_root), "TMPDIR": str(temp_root),
        "PATH": os.pathsep.join([str(p) for p in path_dirs] + [os.environ.get("PATH", "")]),
    })
    env.update(extra)
    return env


def write_cache(path, transport, reading):
    """The capacity cache: {transport: reading}, the shape provider_capacity.py reads."""
    Path(path).write_text(json.dumps({transport: reading}), encoding="utf-8", newline="\n")
    return path


def plan_of(root, launcher, env):
    """The registry's plan for `launcher`: plan, configDir, default-login flag, credentialVar, credentialFile."""
    r = subprocess.run([sys.executable, str(Path(root) / ".claude" / "tools" / "model_registry.py"),
                        "plan-for-launcher", launcher], cwd=str(root), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise AssertionError(f"plan-for-launcher {launcher} exit {r.returncode}: {r.stderr[-300:]}")
    return r.stdout.rstrip("\n").split("\t")


def transport_cost_model(root, plan):
    with open(Path(root) / ".claude" / "reference" / "external_models.json", encoding="utf-8") as fh:
        return json.load(fh)["transports"][plan]["costModel"]


def anthropic_plan_contract(root, temp_root, launcher):
    """A claude-plan launcher's contract, with a stub `claude` and no network: `--check` refuses a plan with
    no credential (exit 3) and answers OK once one is planted; `--check --deep` runs the stub child and
    the argv it would spawn names the one-turn probe; a bad flag is refused (exit 2)."""
    temp_root = Path(temp_root)
    stub = write_stub(temp_root / "bin" / "claude", 'echo "$@" >> "$STUB_LOG"\n'
                      'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                      '"modelUsage":{"stub-model":{"contextWindow":200000}}}\'\n')
    env = launch_env(temp_root, [temp_root / "bin"], CLAUDE_BIN=str(stub), STUB_LOG=str(temp_root / "argv.log"))
    plan, config_dir, _default, cred_var, cred_file = plan_of(root, launcher, env)
    cache = write_cache(temp_root / "capacity.json", plan,
                        planted_reading(plan, transport_cost_model(root, plan)))
    env["PROVIDER_CAPACITY_CACHE"] = str(cache)
    script = Path(root) / launcher
    missing = run_bash(script, root, env, ["--check"])
    assert missing.returncode == 3, (missing.returncode, missing.stdout[-300:])
    assert "UNAVAILABLE" in missing.stdout, missing.stdout[-300:]
    if cred_var and not cred_file:
        # A consumer whose external_models.local.json names no credentialFile has nowhere to plant the key.
        bad = run_bash(script, root, env, ["--bogus"])
        assert bad.returncode == 2, bad.returncode
        return plan
    if cred_var:
        Path(cred_file).parent.mkdir(parents=True, exist_ok=True)
        Path(cred_file).write_text(f"set {cred_var}=sk-planted-proof-key\n", encoding="utf-8", newline="\n")
    else:
        Path(config_dir, ".credentials.json").parent.mkdir(parents=True, exist_ok=True)
        Path(config_dir, ".credentials.json").write_text("{}", encoding="utf-8", newline="\n")
    ok = run_bash(script, root, env, ["--check"])
    assert ok.returncode == 0 and ok.stdout.rstrip().splitlines()[-1].startswith("OK"), (ok.returncode, ok.stdout[-300:])
    deep = run_bash(script, root, env, ["--check", "--deep"])
    assert deep.returncode == 0, (deep.returncode, deep.stdout[-300:], deep.stderr[-300:])
    argv = (temp_root / "argv.log").read_text(encoding="utf-8").splitlines()
    assert len(argv) == 1 and argv[0].startswith("-p Reply with the single word OK.") and " --model " in argv[0], argv
    bad = run_bash(script, root, env, ["--bogus"])
    assert bad.returncode == 2, bad.returncode
    return plan
