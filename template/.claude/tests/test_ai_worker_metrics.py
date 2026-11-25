#!/usr/bin/env python3
"""Minimal proof for tools/ai_worker_metrics.py and its worker_rate.py error column.

A bad --days value is refused, a planted ledger is reported, `--by` buckets rows by
outcome and caller with skipped calls apart from failures, a skipped row is never
counted as an error by either reducer, and worker_rate counts it neither as a call nor as unrated.

    python3 .claude/tests/test_ai_worker_metrics.py
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
TOOL = TOOLS / "ai_worker_metrics.py"
sys.path.insert(0, str(TOOLS))


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def row(index, **fields):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=index)).isoformat()
    base = {"ts": stamp, "tool": "read_files", "model": "planted-model", "tokens": 100,
            "latency_ms": 1000 + index, "cost": 0.0, "under_floor": False, "truncated": False,
            "degenerate": False, "chars": 400, "error": None}
    base.update(fields)
    return base


def served(index, caller, tool="read_files"):
    return row(index, tool=tool, caller_session=caller, caller_source="env@start",
               outcome="served", skip_reason=None, backend="ollama")


def failed(index, caller):
    return row(index, caller_session=caller, caller_source="env@start", outcome="failed",
               skip_reason=None, error="boom", backend="ollama")


def skipped(index, caller, reason="under_floor"):
    return row(index, caller_session=caller, caller_source="env@start", outcome="skipped",
               skip_reason=reason, under_floor=reason == "under_floor", backend=None)


def planted_ledger(directory, rows=None):
    if rows is None:
        rows = [row(index) for index in range(3)]
    path = Path(directory) / "ai_worker_calls.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def by_groups(stdout):
    """(group, calls) pairs from the `--by` table, in printed order."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("== Calls by "))
    out = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        if line.startswith(("group", "-")):
            continue
        key, count = line.rsplit(None, 1)
        out.append((key.strip(), int(count)))
    return out


def case_bad_days_value_exits_nonzero(directory):
    done = run("--ledger", str(planted_ledger(directory)), "--days", "not-a-number")
    assert done.returncode != 0, "a non-integer --days must exit non-zero"
    assert "--days" in done.stderr, done.stderr


def case_planted_ledger_reports_its_model(directory):
    done = run("--ledger", str(planted_ledger(directory)))
    assert done.returncode == 0, done.stderr
    assert "ai-worker call ledger -- all history" in done.stdout, done.stdout
    assert "planted-model" in done.stdout, done.stdout


def case_by_outcome_keeps_skipped_apart_and_unlabelled_legacy(directory):
    rows = [served(0, "sess-a"), served(1, "sess-b", tool="write_doc"), failed(2, "sess-a"),
            skipped(3, "sess-b"), skipped(4, "sess-b", reason="window_overflow"),
            row(5)]
    done = run("--ledger", str(planted_ledger(directory, rows)), "--by", "outcome")
    assert done.returncode == 0, done.stderr
    groups = dict(by_groups(done.stdout))
    assert groups == {"served": 2, "failed": 1, "skipped": 2, "unlabelled": 1}, groups
    full = run("--ledger", str(planted_ledger(directory, rows)))
    assert full.returncode == 0, full.stderr
    assert "failed 1 of 6 rows" in full.stdout, full.stdout


def case_by_caller_session_puts_unknown_last(directory):
    rows = [served(0, "sess-a"), served(1, "sess-a"), served(2, "sess-b"),
            served(3, None)]
    done = run("--ledger", str(planted_ledger(directory, rows)), "--by", "caller_session")
    assert done.returncode == 0, done.stderr
    groups = by_groups(done.stdout)
    assert groups == [("sess-a", 2), ("sess-b", 1), ("unknown", 1)], groups


def case_every_by_axis_is_accepted(directory):
    path = planted_ledger(directory, [served(0, "sess-a")])
    for axis in ("caller_session", "tool", "outcome", "backend"):
        done = run("--ledger", str(path), "--by", axis)
        assert done.returncode == 0, (axis, done.stderr)
        assert by_groups(done.stdout), (axis, done.stdout)


def case_skipped_row_is_not_an_error_in_worker_rate(directory):
    import worker_rate as wr

    ledger = planted_ledger(directory, [failed(0, "sess-a"), skipped(1, "sess-a")])
    saved = wr.LEDGER, wr.RATINGS
    wr.LEDGER, wr.RATINGS = ledger, Path(directory) / "ratings.jsonl"
    real_out = sys.stdout
    sys.stdout = captured = io.StringIO()
    try:
        rc = wr.cmd_report(types.SimpleNamespace(since=None, server_sha=None))
    finally:
        sys.stdout = real_out
        wr.LEDGER, wr.RATINGS = saved
    assert rc == 0, rc
    body = [line for line in captured.getvalue().splitlines() if line.startswith("read_files / ")]
    assert len(body) == 1, captured.getvalue()
    columns = body[0][52:].split()
    assert columns[0] == "1", f"a skipped row sent nothing, so it is not a call: {columns}"
    assert columns[4] == "1", f"only the failed row is an error: {columns}"
    assert columns[-1] == "1", f"a skipped row has no output to rate: {columns}"


def main():
    cases = [case_bad_days_value_exits_nonzero, case_planted_ledger_reports_its_model,
             case_by_outcome_keeps_skipped_apart_and_unlabelled_legacy,
             case_by_caller_session_puts_unknown_last, case_every_by_axis_is_accepted,
             case_skipped_row_is_not_an_error_in_worker_rate]
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
