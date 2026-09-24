#!/usr/bin/env python3
"""Tests the no-stamp `harness_tests.py --proofs-for` runner mode."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "harness_tests.py"


def _run(root: Path, row: str):
    return subprocess.run(
        [sys.executable, str(root / ".claude/scripts/harness_tests.py"), "--proofs-for", row],
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _case(label, body, row, code, needle=None):
    with tempfile.TemporaryDirectory(prefix="harness_proofs_for_") as tmp:
        root = Path(tmp)
        scripts = root / ".claude/scripts"
        tests = root / ".claude/tests"
        scripts.mkdir(parents=True)
        tests.mkdir(parents=True)
        (scripts / "harness_tests.py").write_bytes(RUNNER.read_bytes())
        (root / ".claude/hooks").mkdir(parents=True)
        (root / ".claude/hooks/_hook_state.py").write_bytes(
            (RUNNER.parents[1] / "hooks/_hook_state.py").read_bytes()
        )
        (root / ".claude/tools").mkdir(parents=True)
        (root / ".claude/tools/adaptation.py").write_bytes(
            (RUNNER.parents[1] / "tools/adaptation.py").read_bytes()
        )
        (root / ".claude/skills/project_subsystems").mkdir(parents=True)
        proof = tests / "test_bound.py"
        proof.write_text(body, encoding="utf-8")
        result = _run(root, row)
        assert result.returncode == code, "%s: exit %d\n%s" % (label, result.returncode, result.stdout)
        if needle:
            assert needle in result.stdout, "%s: missing %r\n%s" % (label, needle, result.stdout)
        assert not (root / ".claude/logs/harness_tests_stamp.json").exists(), "proofs-for wrote stamp"


def main():
    _case("failing proof", "# fail.md\nraise SystemExit(1)\n", ".claude/commands/fail.md", 1, "test_bound.py")
    _case("cannot-run proof", "# missing.md\nraise SystemExit(2)\n", ".claude/commands/missing.md", 2, "test_bound.py")
    _case("empty selection", "raise SystemExit(0)\n", ".claude/commands/unbound.md", 1, "no proof selected for")
    _case("mentioned proof", "# .claude/commands/mentioned.md\nraise SystemExit(0)\n",
          ".claude/commands/mentioned.md", 0, "scoped")
    print("4/4 cases pass")


if __name__ == "__main__":
    main()
