#!/usr/bin/env python3
"""Re-runnable proof that post_check.py counts a finding held out-of-subject as accounted for.

A review's findings can cite files outside every fold subject. fold_check disposes them `out-of-subject`
("held for the session, never a failure"), so the consolidated count the engine reports is the fold-job
views plus those held findings. A count that still falls short is a real routing failure.

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


def report(*outside):
    return {"dispositions": [{"id": i, "disposition": "out-of-subject"} for i in outside]
            + [{"id": "F1", "disposition": "applied"}]}


class RoutingGapTests(unittest.TestCase):
    def test_findings_held_out_of_subject_account_for_the_consolidated_count(self):
        rows = [{"id": f"F{n}"} for n in range(1, 5)]
        self.assertIsNone(post_check.routing_failure((4, 2, rows), report("F3", "F4")))

    def test_a_merged_group_held_out_of_subject_counts_once(self):
        rows = [{"id": "F1"}, {"id": "G1", "merged_from": ["F2", "F3"]}, {"id": "F4"}]
        self.assertIsNone(post_check.routing_failure((3, 1, rows), report("F2", "F3", "F4")))

    def test_a_group_with_a_member_the_fold_could_take_is_not_held(self):
        rows = [{"id": "F1"}, {"id": "G1", "merged_from": ["F2", "F3"]}]
        failure = post_check.routing_failure((2, 0, rows), report("F2"))
        self.assertEqual(failure["kind"], "fold-routing")
        self.assertIn("expected 2 consolidated findings", failure["detail"])

    def test_a_finding_that_reached_neither_a_fold_job_nor_the_held_set_fails(self):
        rows = [{"id": f"F{n}"} for n in range(1, 5)]
        failure = post_check.routing_failure((4, 2, rows), report("F3"))
        self.assertEqual(failure["kind"], "fold-routing")
        self.assertIn("1 held out-of-subject", failure["detail"])


class ReadSourceTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(run_label=None, result=None, findings=None, sidecar_dir=None, seats_file=None,
                               workflow_dir="run", expect_total=4, expect_sources=None, spill_dir=None)

    def test_a_short_count_waits_for_the_fold_report_instead_of_failing_at_read(self):
        rows = [{"id": f"F{n}"} for n in range(1, 5)]
        job = {"rows": rows[:2], "subject": object()}
        archive = {"status": "completed", "idOrder": "engine"}
        with mock.patch.object(post_check.fold_check, "workflow_jobs", return_value=[job]), \
                mock.patch.object(post_check.fold_check, "_validated", side_effect=lambda r, _w: r), \
                mock.patch.object(post_check, "_archive_rows", return_value=(rows, archive)):
            src = post_check.read_source(self.args())
        self.assertEqual([e for e in src.errors if e["kind"] == "fold-routing"], [])
        self.assertEqual(src.routing_gap[:2], (4, 2))


if __name__ == "__main__":
    unittest.main()
