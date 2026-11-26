"""Proof for anthropic_sidecar.sh, the `anthropic` plan's launcher: `--check` and `--check --deep` run through a stub claude with the
plan's credential planted, a bad flag is refused, and the child's prompt-cache TTL follows the kind and `SIDECAR_CACHE_TTL`.
No network, no paid call."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import (anthropic_plan_contract, launch_env, plan_of, planted_reading, run_bash,  # noqa: E402
                             transport_cost_model, write_cache, write_stub)

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ".claude/scripts/anthropic_sidecar.sh"
NO_MCP = "-mcp:semantic-search,-mcp:ai-worker"  # an author launch in the proof's config, which registers no MCP server


def child_ttls(tmp):
    """The CLAUDE_CODE_PROMPT_CACHE_TTL each launch's child saw: a stub claude logs it, launches go through the real launcher."""
    tmp = Path(tmp)
    stub = write_stub(tmp / "bin" / "claude", 'echo "${CLAUDE_CODE_PROMPT_CACHE_TTL:-unset}" >> "$STUB_LOG"\n'
                      'echo \'{"type":"result","subtype":"success","is_error":false,"result":"OK",'
                      '"modelUsage":{"stub-model":{"contextWindow":200000}}}\'\n')
    # The preflight `--check` the launch's dispatch hook would run is test_sidecar_dispatch_context's subject.
    env = launch_env(tmp, [tmp / "bin"], CLAUDE_BIN=str(stub), STUB_LOG=str(tmp / "ttl.log"), HARNESS_SIDECAR_PREFLIGHT="0")
    plan, config_dir, *_ = plan_of(ROOT, LAUNCHER, env)
    env["PROVIDER_CAPACITY_CACHE"] = str(write_cache(tmp / "capacity.json", plan,
                                                     planted_reading(plan, transport_cost_model(ROOT, plan))))
    Path(config_dir, ".credentials.json").parent.mkdir(parents=True, exist_ok=True)
    Path(config_dir, ".credentials.json").write_text("{}", encoding="utf-8", newline="\n")
    (tmp / "brief.md").write_text("proof brief", encoding="utf-8")
    seen = {}
    for name, kind, extra, more in (("gate", "gate", {}, []),
                                    ("author-1h", "author", {"SIDECAR_CACHE_TTL": "1h"}, ["-t", NO_MCP])):
        log = tmp / "ttl.log"
        log.unlink(missing_ok=True)
        r = run_bash(ROOT / LAUNCHER, ROOT, dict(env, **extra),
                     ["-m", "sonnet", "-G", kind, "-f", str(tmp / "brief.md"), "-R", str(tmp / (name + ".json")), "-l", name] + more)
        assert r.returncode == 0, (name, r.returncode, r.stderr[-400:])
        seen[name] = log.read_text(encoding="utf-8").split()
    return seen


def kind_row_ttl(kind):
    out = subprocess.run([sys.executable, str(ROOT / ".claude" / "tools" / "tool_profiles.py"), "kind-row",
                          f"--kind={kind}", "--field=cacheTtl"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    return out.stdout.strip()


if __name__ == "__main__":
    assert (kind_row_ttl("gate"), kind_row_ttl("author")) == ("1h", "5m"), (kind_row_ttl("gate"), kind_row_ttl("author"))
    with tempfile.TemporaryDirectory(prefix="anthropic_sidecar_ttl_") as tmp:
        seen = child_ttls(tmp)
    assert seen == {"gate": ["1h"], "author-1h": ["1h"]}, seen
    with tempfile.TemporaryDirectory(prefix="anthropic_sidecar_proof_") as tmp:
        anthropic_plan_contract(ROOT, tmp, LAUNCHER)
    print("ok anthropic_sidecar.sh")
