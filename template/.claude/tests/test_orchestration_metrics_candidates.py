#!/usr/bin/env python3
"""Re-runnable proof for compute_candidates() and its two reports in tools/orchestration_metrics.py.

Assessment 2026-09-10 F1: the candidate loop grouped by label family and effort but not model,
so two clean Opus/high rows beside two clean Sonnet/low rows produced "lower the effort" with a
3x cost ratio -- a comparison that changed both model and effort. A candidate must compare
ADJACENT effort rungs of the SAME recorded model inside a family: another model, an unrecorded
model, or a rung two steps down is a different population, not a cheaper rung. Candidates are
advisory (commands/orchestration_metrics.md "Over-pin candidates"), so neither report may tell the
next dispatch to run the lower rung.

    python3 .claude/tests/test_orchestration_metrics_candidates.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")


def load():
    spec = importlib.util.spec_from_file_location("orchestration_metrics_probe", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(model, effort, cost, n, turns=10, family="proof"):
    return [{"label": f"{family}:same-family", "model": model, "effort": effort,
             "cost": cost, "turns": turns, "outcome": "clean"} for _ in range(n)]


class CandidateGroupingTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.n = self.m.CANDIDATE_MIN_N

    def test_cross_model_rows_never_form_a_candidate(self):
        synthetic = rows("claude-opus-5", "high", 300, self.n) + rows("claude-sonnet-5", "medium", 100, self.n)
        self.assertEqual(self.m.compute_candidates(synthetic), [])

    def test_same_model_adjacent_rungs_still_form_a_candidate(self):
        synthetic = rows("claude-opus-5", "high", 300, self.n) + rows("claude-opus-5", "medium", 100, self.n)
        cands = self.m.compute_candidates(synthetic)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c["family"], c["effort"], c["suggested"]), ("proof", "high", "medium"))
        self.assertEqual(c["model"], "claude-opus-5")
        self.assertEqual(c["cost_ratio"], 3.0)

    def test_a_missing_middle_rung_is_not_skipped(self):
        # high vs low with no medium rows: the medium rung is untested, so there is no adjacent pair.
        synthetic = rows("claude-opus-5", "high", 300, self.n) + rows("claude-opus-5", "low", 100, self.n)
        self.assertEqual(self.m.compute_candidates(synthetic), [])

    def test_mixed_models_in_one_family_compare_only_within_each_model(self):
        # Opus high vs Opus medium: candidate. Sonnet high vs Sonnet medium at a 1.2x ratio: none.
        synthetic = (rows("claude-opus-5", "high", 300, self.n) + rows("claude-opus-5", "medium", 100, self.n)
                     + rows("claude-sonnet-5", "high", 120, self.n) + rows("claude-sonnet-5", "medium", 100, self.n))
        cands = self.m.compute_candidates(synthetic)
        self.assertEqual([c["model"] for c in cands], ["claude-opus-5"])

    def test_unknown_model_sentinel_is_not_a_population(self):
        # Native rows with no recorded model carry "?"; pooling them compares unknown models.
        for sentinel in ("?", "unknown", ""):
            synthetic = rows(sentinel, "high", 300, self.n) + rows(sentinel, "medium", 100, self.n)
            self.assertEqual(self.m.compute_candidates(synthetic), [], sentinel)

    def test_missing_model_is_its_own_population(self):
        # A row without a model cannot be compared with a modeled row; it must not become
        # the "cheaper rung" of one.
        synthetic = rows("claude-opus-5", "high", 300, self.n) + rows(None, "medium", 100, self.n)
        self.assertEqual(self.m.compute_candidates(synthetic), [])


class CandidateReportTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        n = self.m.CANDIDATE_MIN_N
        synthetic = rows("claude-opus-5", "high", 300, n) + rows("claude-opus-5", "medium", 100, n)
        with tempfile.TemporaryDirectory() as td:
            saved = self.m.CANDIDATES_FILE
            self.m.CANDIDATES_FILE = os.path.join(td, "candidates.json")
            try:
                aggregate, pending = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(aggregate):
                    self.m._report_candidates(synthetic)
                with contextlib.redirect_stdout(pending):
                    self.m._report_pending_candidates()
            finally:
                self.m.CANDIDATES_FILE = saved
        self.reports = {"aggregate": aggregate.getvalue(), "pending": pending.getvalue()}

    def test_both_reports_name_the_model(self):
        # A report without the model invites applying an Opus finding to a Sonnet dispatch.
        for name, text in self.reports.items():
            self.assertIn("claude-opus-5", text, name)

    def test_both_reports_stay_advisory(self):
        for name, text in self.reports.items():
            self.assertIn("advisory", text.lower(), name)
            self.assertNotIn("substituted downgrade", text.lower(), name)
            self.assertNotIn("runs at the suggested rung", text.lower(), name)

    def test_module_text_carries_no_downgrade_instruction(self):
        with open(TOOL, encoding="utf-8") as fh:
            source = fh.read().lower()
        self.assertNotIn("substituted downgrade", source)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
