#!/usr/bin/env python3
"""Re-runnable proof (baseline-repo root): baseline.yml bootstraps every consumer profile once.

    python3 tests/test_baseline_workflow.py

Each profile gets exactly one step that bootstraps a scratch project at that layer list and runs
the project's own harness battery. The workflow is read as text: the proof needs no YAML library,
and the steps it inspects are the `- name:` / `run:` shape the file already uses.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "baseline.yml"
PROFILES = ("pure", "pure,coding", "pure,coding,godot")

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
    test_one_bootstrap_step_per_profile()
    test_each_profile_step_runs_the_consumer_battery()
    print("PASS: test_baseline_workflow")
