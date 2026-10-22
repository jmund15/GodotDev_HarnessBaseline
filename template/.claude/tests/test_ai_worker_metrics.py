#!/usr/bin/env python3
"""Minimal proof for tools/ai_worker_metrics.py: a bad --days value refused, a planted ledger reported.

    python3 .claude/tests/test_ai_worker_metrics.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "ai_worker_metrics.py"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def planted_ledger(directory):
    now = datetime.now(timezone.utc)
    rows = []
    for index in range(3):
        stamp = (now - timedelta(minutes=index)).isoformat()
        rows.append({"ts": stamp, "tool": "read_files", "model": "planted-model", "tokens": 100,
                     "latency_ms": 1000 + index, "cost": 0.0, "under_floor": False, "truncated": False,
                     "degenerate": False, "chars": 400})
    path = Path(directory) / "ai_worker_calls.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def case_bad_days_value_exits_nonzero(directory):
    done = run("--ledger", str(planted_ledger(directory)), "--days", "not-a-number")
    assert done.returncode != 0, "a non-integer --days must exit non-zero"
    assert "--days" in done.stderr, done.stderr


def case_planted_ledger_reports_its_model(directory):
    done = run("--ledger", str(planted_ledger(directory)))
    assert done.returncode == 0, done.stderr
    assert "ai-worker call ledger -- all history" in done.stdout, done.stdout
    assert "planted-model" in done.stdout, done.stdout


def main():
    cases = [case_bad_days_value_exits_nonzero, case_planted_ledger_reports_its_model]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="ai-worker-metrics-proof-")
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
