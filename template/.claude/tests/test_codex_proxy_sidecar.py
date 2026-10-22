"""Proof for codex_proxy_sidecar.sh, `--check` only: the proxy binary is a stub that lists the model the registry resolves,
so the launcher's availability answer runs end to end. A missing login and a model the proxy does not list are refused;
a bad flag is refused. The dispatch path starts a proxy and a shim that outlive the launcher, so this proof does not launch
it. No network, no paid call."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import (launch_env, planted_reading, run_bash, transport_cost_model, write_cache,  # noqa: E402
                             write_stub)

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / ".claude" / "scripts" / "codex_proxy_sidecar.sh"


def resolved_model(env):
    """The id the registry gives the launcher's model alias, read from the same CLI the launcher calls."""
    r = subprocess.run([sys.executable, str(ROOT / ".claude" / "tools" / "model_registry.py"),
                        "sidecar-fields", "luna"], cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, (r.returncode, r.stderr[-300:])
    return r.stdout.split("|")[0].strip()


def proxy_stub(bin_dir, listed):
    """A proxy whose `models` prints `listed`. The probe runs it from Python, which cannot exec a bash
    script on Windows, so the stub is a batch file there and a shell script elsewhere."""
    if os.name == "nt":
        return write_bytes(bin_dir / "claude-code-proxy.cmd",
                           "@echo off\r\nif \"%~1\"==\"models\" (\r\necho " + listed + "\r\nexit /b 0\r\n)\r\n"
                           "echo stub proxy: unexpected argv %* 1>&2\r\nexit /b 9\r\n")
    return write_stub(bin_dir / "claude-code-proxy",
                      'if [ "$1" = models ]; then echo "' + listed + '"; exit 0; fi\n'
                      'echo "stub proxy: unexpected argv $*" >&2; exit 9\n')


def write_bytes(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    return path


def contract(temp_root):
    temp_root = Path(temp_root)
    bin_dir = temp_root / "bin"
    claude = write_stub(bin_dir / "claude", "exit 0\n")
    env = launch_env(temp_root, [bin_dir], CLAUDE_BIN=str(claude), CCP_BIN_SCOPE="process")
    model = resolved_model(env)
    proxy = proxy_stub(bin_dir, model + " other-model")
    cache = write_cache(temp_root / "capacity.json", "codex",
                        planted_reading("codex", transport_cost_model(ROOT, "codex")))
    env.update(CCP_BIN=str(proxy), PROVIDER_CAPACITY_CACHE=str(cache))

    # No codex login: refused, exit 3.
    missing = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert missing.returncode == 3 and "UNAVAILABLE" in missing.stdout, (missing.returncode, missing.stdout[-300:])

    auth = Path(env["HOME"]) / ".config" / "claude-code-proxy" / "codex" / "auth.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text(json.dumps({"planted": True}), encoding="utf-8", newline="\n")
    ok = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert ok.returncode == 0 and ok.stdout.rstrip().splitlines()[-1].startswith("OK"), (ok.returncode, ok.stdout[-300:])

    # The proxy lists other models only: refused, exit 4 (the probe's not-served answer).
    proxy_stub(bin_dir, "other-model")
    unserved = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert unserved.returncode == 4 and "does not serve" in unserved.stdout, (unserved.returncode, unserved.stdout[-300:])

    bad_model = run_bash(LAUNCHER, ROOT, env, ["-m", "nope", "--check"])
    assert bad_model.returncode == 2, (bad_model.returncode, bad_model.stdout[-300:])
    bad_flag = run_bash(LAUNCHER, ROOT, env, ["--bogus"])
    assert bad_flag.returncode == 2, bad_flag.returncode


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codex_proxy_sidecar_proof_") as tmp:
        contract(tmp)
    print("ok codex_proxy_sidecar.sh")
