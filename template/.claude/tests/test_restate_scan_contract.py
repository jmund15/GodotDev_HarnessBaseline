#!/usr/bin/env python3
"""Minimal proof for tools/restate_scan.py: a repo without an instruction scope is refused, and a configured
scope proves the scan finds a planted near-copy through its own selftest.

    python3 .claude/tests/test_restate_scan_contract.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "restate_scan.py"

SCOPE = {"instruction_scope": {"paths": [".claude/**/*.md"], "rows_dir": ".claude/scratch/restate-rows"}}


def run(repo, *args):
    return subprocess.run([sys.executable, str(TOOL), "--repo", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def planted_repo(directory, scope):
    repo = Path(directory) / "repo"
    (repo / ".claude" / "skills" / "project_subsystems").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    if scope is not None:
        adaptation = repo / ".claude" / "skills" / "project_subsystems" / "adaptation.json"
        adaptation.write_text(json.dumps(scope), encoding="utf-8")
    return repo


def case_repo_without_scope_is_refused(directory):
    repo = planted_repo(directory, None)
    done = run(repo, "pairs")
    assert done.returncode == 2, "no instruction_scope must exit 2, never report zero candidates"
    assert "no instruction_scope" in done.stdout, done.stdout


def case_selftest_finds_planted_near_copy(directory):
    repo = planted_repo(directory, SCOPE)
    done = run(repo, "selftest")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "the planted near-copy is a group" in done.stdout, done.stdout


def main():
    cases = [case_repo_without_scope_is_refused, case_selftest_finds_planted_near_copy]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="restate-scan-proof-")
        try:
            case(directory)
            print("ok " + case.__name__)
        except Exception as exc:
            failures.append(case.__name__)
            print("FAIL %s: %s" % (case.__name__, exc))
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
