"""Proof for anthropic_sidecar.sh, the `anthropic` plan's launcher: `--check` and `--check --deep` run through a stub claude with the
plan's credential planted, and a bad flag is refused. No network, no paid call."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import anthropic_plan_contract  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="anthropic_sidecar_proof_") as tmp:
        anthropic_plan_contract(ROOT, tmp, ".claude/scripts/anthropic_sidecar.sh")
    print("ok anthropic_sidecar.sh")
