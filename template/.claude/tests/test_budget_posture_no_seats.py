"""Proof that hooks/budget_posture.py is the Anthropic pace line alone on a machine with no external seats.

A workstation with no model registry has no other plan to route to: the line states the host plan's pace
and nothing about provider rows, an unidentified seat, routing "to another plan", a spend-lean offer or a
warm. With a registry that names external transports the same hook keeps all of them.

    python3 -m pytest -q .claude/tests/test_budget_posture_no_seats.py
"""
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "budget_posture.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

SHIPPED_REGISTRY = os.path.join(HERE, "..", "reference", "external_models.json")


def run_hook(tmp_path, used, registry):
    tmp = tmp_path / "tmp"
    state = tmp_path / "state"
    tmp.mkdir()
    state.mkdir()
    now = time.time()
    (tmp / "cc-cachestat-bps00001.json").write_text(json.dumps({"rate_limits": {
        "captured_at": now,
        "seven_day": {"used_percentage": used, "resets_at": now + 7 * 24 * 3600 / 2},
        "five_hour": {"used_percentage": used, "resets_at": now + 5 * 3600 / 2}}}), encoding="utf-8")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state), CLAUDE_PROJECT_DIR=str(tmp_path), TMP=str(tmp),
               TEMP=str(tmp), TMPDIR=str(tmp), HARNESS_CAPACITY_WARM="0", PYTHONIOENCODING="utf-8",
               PROVIDER_CAPACITY_CACHE=str(tmp / "pc.json"), HARNESS_USAGE_SAMPLES=str(tmp / "s.jsonl"),
               HARNESS_MODEL_REGISTRY=str(registry))
    for key in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_TRANSPORT", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR"):
        env.pop(key, None)
    done = subprocess.run([sys.executable, HOOK], input=json.dumps({"session_id": "bps00001", "prompt": "next"}),
                          capture_output=True, text=True, env=env, cwd=str(tmp_path), encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.mark.parametrize("used", [40, 100])
def test_no_registry_states_only_the_host_plan(tmp_path, used):
    out = run_hook(tmp_path, used, tmp_path / "absent.json")
    assert "[budget-posture] anthropic (this session)" in out
    for provider_text in ("UNIDENTIFIED", "another plan", "spend: lean available", "free pool", "warming"):
        assert provider_text not in out, out


def test_a_registry_with_external_seats_keeps_the_provider_rows(tmp_path):
    out = run_hook(tmp_path, 100, SHIPPED_REGISTRY)
    assert "another plan" in out or "UNIDENTIFIED" in out, out


def test_no_registry_starts_no_warm(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL_REGISTRY", str(tmp_path / "absent.json"))
    monkeypatch.delenv("HARNESS_CAPACITY_WARM", raising=False)
    import budget_posture
    assert budget_posture.has_external_seats() is False
    assert budget_posture.cold_plans() == []
