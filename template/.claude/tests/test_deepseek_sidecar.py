"""Proof for deepseek_sidecar.sh: `--check` passes with a planted key and capacity reading and refuses without one;
a bad model and a bad flag are refused; a dispatch runs the stub claude with the flash model on its argv.
No network, no paid call."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, planted_reading, run_bash, write_stub  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / ".claude" / "scripts" / "deepseek_sidecar.sh"
OFF_PEAK = "2026-10-18T12:00:00Z"


def contract(temp_root):
    temp_root = Path(temp_root)
    home = temp_root / "home"
    env_file = home / ".env.ai-worker.cmd"
    result = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "OK",
                         "modelUsage": {"stub-model": {"contextWindow": 1000000}}})
    stub = write_stub(temp_root / "bin" / "claude", 'echo "$@" >> "$STUB_LOG"\necho \'' + result + "'\n")
    # The launcher's floor reads the host seat's band (anthropic), and its own balance (deepseek).
    cache = temp_root / "capacity.json"
    cache.write_text(json.dumps({
        "deepseek": planted_reading("deepseek", "marginal-usd"),
        "anthropic": planted_reading("anthropic", "plan-quota"),
    }), encoding="utf-8", newline="\n")
    env = launch_env(temp_root, [temp_root / "bin"], CLAUDE_BIN=str(stub), STUB_LOG=str(temp_root / "argv.log"),
                     PROVIDER_CAPACITY_CACHE=str(cache), SC_PRICE_AT=OFF_PEAK)

    # No credential file: refused before any spend, exit 3.
    missing = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert missing.returncode == 3 and "UNAVAILABLE" in missing.stdout, (missing.returncode, missing.stdout[-300:])

    # A placeholder key is not a credential: exit 3.
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("set DEEPSEEK_API_KEY=your-key-here\n", encoding="utf-8", newline="\n")
    placeholder = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert placeholder.returncode == 3, (placeholder.returncode, placeholder.stdout[-300:])

    env_file.write_text("set DEEPSEEK_API_KEY=sk-planted-proof-key\n", encoding="utf-8", newline="\n")
    ok = run_bash(LAUNCHER, ROOT, env, ["--check"])
    assert ok.returncode == 0 and ok.stdout.rstrip().splitlines()[-1].startswith("OK"), (ok.returncode, ok.stdout[-300:])

    # A dispatch runs the stub child with the flash model on its argv.
    run = run_bash(LAUNCHER, temp_root, env, ["-d", str(temp_root), "-t", "Read", "--", "Reply with the single word OK."])
    assert run.returncode == 0, (run.returncode, run.stdout[-300:], run.stderr[-300:])
    argv = (temp_root / "argv.log").read_text(encoding="utf-8").splitlines()
    assert len(argv) == 1 and argv[0].startswith("-p --model ") and " --allowedTools Read " in argv[0], argv

    # A model the registry does not know, and an unknown flag: refused, exit 2.
    bad_model = run_bash(LAUNCHER, ROOT, env, ["-m", "nope", "--check"])
    assert bad_model.returncode == 2, (bad_model.returncode, bad_model.stdout[-300:])
    bad_flag = run_bash(LAUNCHER, ROOT, env, ["--bogus"])
    assert bad_flag.returncode == 2, bad_flag.returncode


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="deepseek_sidecar_proof_") as tmp:
        contract(tmp)
    print("ok deepseek_sidecar.sh")
