#!/usr/bin/env python3
"""Proof for X2 D10: one six-rung effort ladder and one workflow terminal-status tuple.

Every caller that orders efforts reads `model_registry.EFFORT_ORDER`; each caller keeps its own
`none` rule (review_ledger: none weakest). Every caller that reads a
workflow host record's status reads `model_registry.WORKFLOW_TERMINAL_STATES`. The status half is
proved in test_dispatch_result.py (a stopped run is finished) and the ultra candidate in
test_orchestration_metrics_candidates.py; this file proves the shared values and the `none` rules.

Run: python3 .claude/tests/test_effort_status_one_constant.py
"""
import importlib.util
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.abspath(os.path.join(HERE, ".."))
for path in (os.path.join(CLAUDE, "tools"), os.path.join(CLAUDE, "hooks")):
    if path not in sys.path:
        sys.path.insert(0, path)

import model_registry  # noqa: E402

LADDER = ("low", "medium", "high", "xhigh", "max", "ultra")


def load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, os.path.join(CLAUDE, relpath))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OneLadderTests(unittest.TestCase):
    def test_registry_owns_the_six_rung_ladder(self):
        self.assertEqual(tuple(model_registry.EFFORT_ORDER), LADDER)

    def test_orchestration_metrics_ranks_ultra_above_max(self):
        om = load("orchestration_metrics_one_ladder", os.path.join("tools", "orchestration_metrics.py"))
        ranked = sorted(LADDER, key=om._effort_rank)
        self.assertEqual(ranked, list(LADDER))
        self.assertLess(om._effort_rank("ultra")[0], om._effort_rank("?")[0])


class NoneRulesStayPerCaller(unittest.TestCase):
    """`none` is not a rung. review_ledger ranks it weakest."""

    def setUp(self):
        self.rl = load("review_ledger_one_ladder", os.path.join("tools", "review_ledger.py"))

    def _depth(self, seat_effort, row):
        cover = types.SimpleNamespace(tiers=lambda: ["seat-tier"])
        mode = self.rl.Mode(seat={"effort": seat_effort, "tier": None, "load": None})
        return self.rl._clause_depth(cover, row, None, None, mode=mode)

    def _row(self, effort):
        return {"reviewer": "panel", "tier": "seat-tier", "effort": effort, "seatMandates": []}

    def test_none_mandate_cannot_cover_a_low_seat(self):
        self.assertFalse(self._depth("low", self._row("none")))

    def test_low_mandate_covers_a_none_seat(self):
        self.assertTrue(self._depth("none", self._row("low")))

    def test_ultra_mandate_covers_a_max_seat(self):
        self.assertTrue(self._depth("max", self._row("ultra")))


class OneTerminalStatusTuple(unittest.TestCase):
    def test_registry_owns_the_terminal_status_tuple(self):
        self.assertIn("stopped", model_registry.WORKFLOW_TERMINAL_STATES)

    def test_dispatch_table_and_dispatch_result_agree(self):
        dt = load("dispatch_table_one_status", os.path.join("hooks", "dispatch_table.py"))
        for status in model_registry.WORKFLOW_TERMINAL_STATES:
            self.assertTrue(dt.workflow_finished(status), status)
        self.assertEqual(set(dt.DONE_STATES), set(model_registry.WORKFLOW_TERMINAL_STATES))


class SafeEffortsIsANamedSubset(unittest.TestCase):
    """SAFE_EFFORTS gates auto-approval of a fan-out job: ultra is outside it on purpose (it is the
    costliest rung, and a job outside the set falls through to owner approval)."""

    def test_safe_efforts_is_the_ladder_without_ultra(self):
        sidecar = load("sidecar_dispatch_context_one_ladder",
                       os.path.join("hooks", "sidecar_dispatch_context.py"))
        self.assertEqual(sidecar.SAFE_EFFORTS, frozenset(LADDER[:-1]))


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
