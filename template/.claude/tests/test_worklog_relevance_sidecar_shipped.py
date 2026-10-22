"""Proof for worklog_relevance_sidecar.sh, the wrapper over the deepseek launcher. `--check` reaches the launcher with a
planted key and capacity reading. A dispatch with an effort of none, and one whose notes vault is absent, are refused
before any spend. No network, no paid call."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, planted_reading, run_bash, write_stub  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / ".claude" / "scripts" / "worklog_relevance_sidecar.sh"


def contract(temp_root):
    temp_root = Path(temp_root)
    # The launcher's --check needs a claude CLI; a CI runner has none, and this proof calls no model.
    stub = write_stub(temp_root / "bin" / "claude", "exit 0\n")
    env = launch_env(temp_root, [temp_root / "bin"], CLAUDE_BIN=str(stub), SC_PRICE_AT="2026-10-18T12:00:00Z")
    (Path(env["HOME"]) / ".env.ai-worker.cmd").write_text("set DEEPSEEK_API_KEY=sk-planted-proof-key\n",
                                                          encoding="utf-8", newline="\n")
    cache = temp_root / "capacity.json"
    cache.write_text(json.dumps({
        "deepseek": planted_reading("deepseek", "marginal-usd"),
        "anthropic": planted_reading("anthropic", "plan-quota"),
    }), encoding="utf-8", newline="\n")
    env["PROVIDER_CAPACITY_CACHE"] = str(cache)

    ok = run_bash(WRAPPER, ROOT, env, ["--check"])
    assert ok.returncode == 0 and ok.stdout.rstrip().splitlines()[-1].startswith("OK"), (ok.returncode, ok.stdout[-300:])

    no_effort = run_bash(WRAPPER, ROOT, env, ["-e", "none", "--", "planted scope"])
    assert no_effort.returncode == 2 and "effort none refused" in no_effort.stderr, (no_effort.returncode, no_effort.stderr[-300:])

    # The HOME in this proof holds no notes vault, so the wrapper refuses before it dispatches.
    no_vault = run_bash(WRAPPER, ROOT, env, ["--", "planted scope"])
    assert no_vault.returncode == 2 and "vault dir missing" in no_vault.stderr, (no_vault.returncode, no_vault.stderr[-300:])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="worklog_relevance_sidecar_proof_") as tmp:
        contract(tmp)
    print("ok worklog_relevance_sidecar.sh")
