"""Proof for sync_plan_config.py, the file stage only (--files-only). A planted registry copy points one plan's login dir and
its route's login dir at temp dirs, so the proof never reads or writes a real login. The planted source holds the one
whole-file config; check reports it as drift (exit 1), a sync writes it, and a second check is clean (exit 0). A
missing plan and a bad flag are refused (exit 2). The plugins stage is not run: it calls the claude CLI."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "sync_plan_config.py"
REGISTRY = ROOT / ".claude" / "reference" / "external_models.json"
GUIDE = "ai_worker_model_guide.md"


def run(env, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=str(ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def contract(temp_root):
    temp_root = Path(temp_root)
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    plan, route = next((name, cfg["planOf"]) for name, cfg in sorted(data["transports"].items()) if cfg.get("planOf"))
    source, dest = temp_root / "route-login", temp_root / "plan-login"
    source.mkdir()
    dest.mkdir()
    (source / GUIDE).write_text("planted guide\n", encoding="utf-8", newline="\n")
    data["transports"][route]["configDir"] = source.as_posix()
    data["transports"][plan]["configDir"] = dest.as_posix()
    registry = temp_root / "registry.json"
    registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    env = launch_env(temp_root, HARNESS_MODEL_REGISTRY=str(registry))

    drift = run(env, "--check", "--files-only", "--plan", plan)
    assert drift.returncode == 1 and GUIDE in drift.stdout, (drift.returncode, drift.stdout[-300:], drift.stderr[-300:])
    assert not (dest / GUIDE).exists(), "--check wrote the destination"

    synced = run(env, "--files-only", "--plan", plan)
    assert synced.returncode == 0 and (dest / GUIDE).read_text(encoding="utf-8") == "planted guide\n", (
        synced.returncode, synced.stdout[-300:], synced.stderr[-300:])

    clean = run(env, "--check", "--files-only", "--plan", plan)
    assert clean.returncode == 0 and GUIDE not in clean.stdout, (clean.returncode, clean.stdout[-300:])

    missing = run(env, "--check", "--files-only", "--plan", "no-such-plan")
    assert missing.returncode == 2 and "no transport" in missing.stderr, (missing.returncode, missing.stderr[-300:])
    bad = run(env, "--bogus")
    assert bad.returncode == 2, bad.returncode


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="sync_plan_config_proof_") as tmp:
        contract(tmp)
    print("ok sync_plan_config.py")
