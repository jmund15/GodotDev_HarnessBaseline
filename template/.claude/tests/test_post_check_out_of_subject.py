#!/usr/bin/env python3
"""Re-runnable proof that post_check.py reads a folded run's original findings without failing on a count.

A folded run's routing is checked per original finding by fold_check (each one no fold job read is held by id), so
reading the source records no fold-routing error even when the engine's consolidated count differs from the views.

    python3 .claude/tests/test_post_check_out_of_subject.py
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))
import post_check  # noqa: E402


class ReadSourceTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(run_label=None, result=None, findings=None, sidecar_dir=None, seats_file=None,
                               workflow_dir="run", expect_total=4, expect_sources=None, spill_dir=None)

    def test_a_short_count_waits_for_the_fold_report_instead_of_failing_at_read(self):
        rows = [{"id": f"F{n}"} for n in range(1, 5)]
        job = {"rows": rows[:2], "subject": object()}
        archive = {"status": "completed", "idOrder": "engine"}
        with mock.patch.object(post_check.fold_check, "workflow_jobs", return_value=[job]), \
                mock.patch.object(post_check, "_archive_rows", return_value=(rows, archive)):
            src = post_check.read_source(self.args())
        self.assertEqual([e for e in src.errors if e["kind"] == "fold-routing"], [])
        self.assertEqual(src.rows, rows)


if __name__ == "__main__":
    unittest.main()
