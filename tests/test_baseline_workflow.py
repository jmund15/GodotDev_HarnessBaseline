#!/usr/bin/env python3
"""Re-runnable proof (baseline-repo root): baseline.yml bootstraps every consumer profile once.

    python3 tests/test_baseline_workflow.py

Each profile gets exactly one step that bootstraps a scratch project at that layer list and runs
the project's own harness battery. The workflow is read as text: the proof needs no YAML library,
and the steps it inspects are the `- name:` / `run:` shape the file already uses.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "baseline.yml"
RULESET = ROOT / ".github" / "ruleset-main.json"
PROFILES = ("pure", "pure,coding", "pure,coding,godot")
LANES = ("root", "template", "pure", "pure-coding", "pure-coding-godot")

sys.path.insert(0, str(ROOT / "template" / ".claude" / "tests"))

if not WORKFLOW.is_file():
    print("CANNOT-RUN: %s is missing" % WORKFLOW, file=sys.stderr)
    sys.exit(2)

STEP_START = re.compile(r"^      - ", re.MULTILINE)
LAYERS_FLAG = re.compile(r"bootstrap\.sh\s+--target\s+\S+\s+--project-name\s+\S+\s+--layers\s+(\S+)")


def _steps(text: str) -> list[str]:
    starts = [m.start() for m in STEP_START.finditer(text)]
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def _bootstrap_layers(text: str) -> list[str]:
    layers: list[str] = []
    for step in _steps(text):
        match = LAYERS_FLAG.search(step)
        if match:
            layers.append(match.group(1))
    return layers


def _job(text: str, job_id: str) -> str:
    """The lines of one job under `jobs:`; two-space-indent keys delimit jobs."""
    match = re.search(r"^  %s:\s*\n(.*?)(?=^  [A-Za-z0-9_-]+:\s*$|\Z)" % re.escape(job_id), text,
                      re.MULTILINE | re.DOTALL)
    assert match, "no job `%s` in the workflow" % job_id
    return match.group(1)


def test_each_lane_is_its_own_job() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for lane in LANES:
        _job(text, lane)
    ids = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text.split("\njobs:\n", 1)[1], re.MULTILINE)
    assert sorted(ids) == sorted(LANES + ("baseline",)), "jobs are %s" % ids


def test_gate_job_needs_every_lane_and_carries_the_ruleset_context() -> None:
    gate = _job(WORKFLOW.read_text(encoding="utf-8"), "baseline")
    needs = re.search(r"^    needs: \[([^\]]*)\]\s*$", gate, re.MULTILINE)
    assert needs, "gate needs: must be one flow-list line"
    assert sorted(x.strip() for x in needs.group(1).split(",")) == sorted(LANES), needs.group(1)
    assert re.search(r"^    if: always\(\)\s*$", gate, re.MULTILINE), "gate must run when a lane fails"
    contexts = [c["context"] for rule in json.loads(RULESET.read_text(encoding="utf-8"))["rules"]
                if rule["type"] == "required_status_checks"
                for c in rule["parameters"]["required_status_checks"]]
    assert contexts, "ruleset requires no status context"
    name = re.search(r"^    name: (\S+)\s*$", gate, re.MULTILINE)
    assert name and all(name.group(1) == c for c in contexts), (name and name.group(1), contexts)


def _gate_run(results: str) -> int:
    from _sidecar_launch import bash_exe
    gate = _job(WORKFLOW.read_text(encoding="utf-8"), "baseline")
    lines = gate.splitlines()
    at = next(n for n, line in enumerate(lines) if line.strip() == "run: |")
    indent = len(lines[at]) - len(lines[at].lstrip())
    body = []
    for line in lines[at + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2:])
    env = dict(os.environ, RESULTS=results)
    return subprocess.run([bash_exe(), "--noprofile", "--norc", "-eo", "pipefail", "-c", "\n".join(body)],
                          env=env, capture_output=True).returncode


def test_gate_step_passes_only_on_all_success() -> None:
    assert _gate_run(" ".join(["success"] * 5)) == 0
    for bad in ("failure", "cancelled", "skipped"):
        rc = _gate_run(" ".join(["success"] * 4 + [bad]))
        assert rc == 1, "%s must fail the gate, got rc=%d" % (bad, rc)


def test_one_bootstrap_step_per_profile() -> None:
    layers = _bootstrap_layers(WORKFLOW.read_text(encoding="utf-8"))
    for profile in PROFILES:
        count = layers.count(profile)
        assert count == 1, "expected one bootstrap step for --layers %s, found %d (all: %s)" % (
            profile, count, layers)


def test_each_profile_step_runs_the_consumer_battery() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for step in _steps(text):
        match = LAYERS_FLAG.search(step)
        if not match or match.group(1) not in PROFILES:
            continue
        assert "python3 .claude/scripts/harness_tests.py" in step, (
            "--layers %s step does not run the consumer's harness battery:\n%s" % (match.group(1), step))


if __name__ == "__main__":
    test_each_lane_is_its_own_job()
    test_gate_job_needs_every_lane_and_carries_the_ruleset_context()
    test_gate_step_passes_only_on_all_success()
    test_one_bootstrap_step_per_profile()
    test_each_profile_step_runs_the_consumer_battery()
    print("PASS: test_baseline_workflow")
