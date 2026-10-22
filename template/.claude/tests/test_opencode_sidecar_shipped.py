"""Proof for opencode_sidecar.sh, `--check` on a messages-mode row of the registry's `go` transport. The row is read from the
repo's own registry, so the proof names no model. A stub curl answers the live probe: a 200 passes, a 401 is refused,
and the missing key and an unknown model are refused before any call. No network, no paid call."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, planted_reading, run_bash, write_stub  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / ".claude" / "scripts" / "opencode_sidecar.sh"
REGISTRY = ROOT / ".claude" / "reference" / "external_models.json"


def messages_row():
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))["models"]
    return next(m for m in rows if m.get("transport") == "go" and m.get("apiMode") == "messages")


def write_text(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def contract(temp_root):
    temp_root = Path(temp_root)
    row = messages_row()
    bin_dir = temp_root / "bin"
    # Git Bash puts its own curl ahead of PATH, so the stub is a shell function that BASH_ENV loads into every
    # launcher shell; the launcher's probes run in command substitutions, which inherit the function.
    curl = write_text(temp_root / "stub_curl.sh",
                      'curl() {\n  echo "$*" >> "$STUB_LOG"\n  printf \'%s\\n\' \'{"ok":true}\'\n'
                      '  if [ "${STUB_HTTP:-200}" = 200 ]; then echo 200; else echo "$STUB_HTTP"; fi\n}\n')
    claude = write_stub(bin_dir / "claude", "exit 0\n")
    cache = temp_root / "capacity.json"
    cache.write_text(json.dumps({
        "go": planted_reading("go", "plan-quota"),
        "anthropic": planted_reading("anthropic", "plan-quota"),
    }), encoding="utf-8", newline="\n")
    env = launch_env(temp_root, [bin_dir], CLAUDE_BIN=str(claude), STUB_LOG=str(temp_root / "curl.log"),
                     PROVIDER_CAPACITY_CACHE=str(cache), SC_PRICE_AT="2026-10-18T12:00:00Z",
                     HARNESS_MODEL_REGISTRY=str(REGISTRY), BASH_ENV=str(curl))
    key_file = Path(env["HOME"]) / ".env.ai-worker.cmd"

    # No key: the go transport refuses before any call.
    nokey = run_bash(LAUNCHER, ROOT, env, ["--check", "-m", row["id"]])
    assert nokey.returncode == 3 and not (temp_root / "curl.log").exists(), (nokey.returncode, nokey.stdout[-300:])

    key_file.write_text("set OPENCODE_API_KEY=sk-planted-proof-key\n", encoding="utf-8", newline="\n")
    ok = run_bash(LAUNCHER, ROOT, env, ["--check", "-m", row["id"]])
    assert ok.returncode == 0 and ok.stdout.rstrip().splitlines()[-1].startswith("OK"), (ok.returncode, ok.stdout[-300:])
    assert "/v1/messages" in (temp_root / "curl.log").read_text(encoding="utf-8")

    # The provider answers 401 to the probe: refused, exit 3.
    refused = run_bash(LAUNCHER, ROOT, dict(env, STUB_HTTP="401"), ["--check", "-m", row["id"]])
    assert refused.returncode == 3 and "live probe" in refused.stdout, (refused.returncode, refused.stdout[-300:])

    unknown = run_bash(LAUNCHER, ROOT, env, ["--check", "-m", "no-such-model"])
    assert unknown.returncode == 2, (unknown.returncode, unknown.stdout[-300:])
    bogus = run_bash(LAUNCHER, ROOT, env, ["--bogus"])
    assert bogus.returncode == 2, bogus.returncode


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="opencode_sidecar_proof_") as tmp:
        contract(tmp)
    print("ok opencode_sidecar.sh")
