#!/usr/bin/env python3
"""Minimal proof for tools/guard_denials.py: a planted denied call counted under its category, a bad flag refused.

    python3 .claude/tests/test_guard_denials_contract.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "guard_denials.py"

TRANSCRIPT = [
    {"type": "assistant", "sessionId": "planted-session", "timestamp": "2026-10-20T12:00:00Z",
     "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]}},
    {"type": "user", "sessionId": "planted-session", "timestamp": "2026-10-20T12:00:01Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                              "content": "BLOCKED foreground wait loop: run the job with run_in_background."}]}},
    {"type": "assistant", "sessionId": "planted-session", "timestamp": "2026-10-20T12:00:02Z",
     "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash"}]}},
    {"type": "user", "sessionId": "planted-session", "timestamp": "2026-10-20T12:00:03Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": False,
                              "content": "ok"}]}},
]


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def planted_transcript_root(directory):
    root = Path(directory) / "projects"
    root.mkdir()
    (root / "planted-session.jsonl").write_text("".join(json.dumps(row) + "\n" for row in TRANSCRIPT),
                                                encoding="utf-8")
    return root


def case_unknown_flag_exits_nonzero(directory):
    done = run("--no-such-flag")
    assert done.returncode != 0, "an unknown flag must exit non-zero"


def case_planted_denial_counted_under_its_category(directory):
    done = run("--roots", str(planted_transcript_root(directory)))
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert "wait-loop 1" in lines, done.stdout
    assert "calls 2" in lines, done.stdout
    assert "sleep-wait 0" in lines, done.stdout


def case_missing_root_is_reported_as_a_gap(directory):
    missing = str(Path(directory) / "absent")
    done = run("--roots", missing)
    coverage = json.loads(done.stdout.splitlines()[-1])
    assert coverage["gaps"] and coverage["files"] == 0, done.stdout


def main():
    cases = [case_unknown_flag_exits_nonzero, case_planted_denial_counted_under_its_category,
             case_missing_root_is_reported_as_a_gap]
    failures = []
    for case in cases:
        directory = tempfile.mkdtemp(prefix="guard-denials-proof-")
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
