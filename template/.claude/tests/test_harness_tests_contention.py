#!/usr/bin/env python3
"""Proof that a red the machine had no room for is its own outcome, never FAIL, and never a pass.

The runner observes the machine's CPU share across each attempt (`machine_slots.Room`). A pool red that ran
crowded waits for room before its alone retry; a proof that fails again while crowded ends `contended` (exit 2),
and one that fails with room stays FAIL, so a proof that really hangs is still caught.

    python3 .claude/tests/test_harness_tests_contention.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.pop("HARNESS_REUSE_PASSES", None)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
import machine_slots as slots  # noqa: E402

spec = importlib.util.spec_from_file_location("harness_contention_subject", ROOT / ".claude/scripts/harness_tests.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

CROWDED, ROOMY = 0.97, 0.20
TIMEOUT = ("fail", 1.0, "TIMEOUT after 120s")
ASSERT = ("fail", 1.0, "AssertionError: planted")
PASS = ("pass", 1.0, "")


class PlantedRoom:
    """The machine's reading, planted: `level` is the CPU share the attempt in flight saw (the planted proof sets
    it as it runs, one at a time), `quiet` whether a wait finds room."""

    def __init__(self, quiet=True):
        self.level, self.quiet, self.waits = ROOMY, quiet, []

    def mark(self):
        return None

    def busy(self, _mark):
        return self.level

    def wait(self, limit_s):
        self.waits.append(limit_s)
        return 0.0, self.quiet


class Contention(unittest.TestCase):
    def run_all(self, script, durations=None, quiet=True):
        """`script` is {proof: [(outcome, machine share), ...]}, one entry per attempt. Returns the exit code,
        each proof's attempts, the planted room and stdout."""
        room, calls = PlantedRoom(quiet), {}
        real_context = runner._run_context

        def context(*args, **kwargs):
            ctx = real_context(*args, **kwargs)
            ctx.room = room
            return ctx

        def proof(path, _timeout=None, _root=None, **_):
            name = os.path.basename(path)
            calls[name] = calls.get(name, 0) + 1
            outcome, room.level = script[name][calls[name] - 1]
            return outcome

        with tempfile.TemporaryDirectory(prefix="harness-contention-") as tmp:
            timing = Path(tmp) / "d.json"
            timing.write_text(json.dumps(durations or {}), encoding="utf-8")
            with patch.object(runner, "EXCLUDED", {}), patch.object(runner, "HEAVY", {}), \
                    patch.object(runner, "SERIAL", {}), \
                    patch.object(runner, "STAMP_PATH", str(Path(tmp) / "stamp.json")), \
                    patch.object(runner, "FLAKY_LOG", str(Path(tmp) / "flaky.jsonl")), \
                    patch.object(runner, "DURATIONS_PATH", str(timing)), \
                    patch.object(runner, "discover", return_value=sorted(script)), \
                    patch.object(runner, "tree_entries", return_value={".claude/tools/subject.py": "v"}), \
                    patch.object(runner, "_git", return_value="fixture-head"), \
                    patch.object(runner, "run_proof", side_effect=proof), \
                    patch.object(runner, "_run_context", side_effect=context), \
                    patch.dict(os.environ, {"HARNESS_TEST_JOBS": "1", "HARNESS_SLOT_BUDGET": "",
                                            "HARNESS_SLOT_DIR": str(Path(tmp) / "slots")}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = runner.run_all(tmp)
        return code, calls, room, output.getvalue()

    def test_a_timeout_that_repeats_alone_on_a_saturated_machine_is_contended_not_fail(self):
        code, calls, room, out = self.run_all({"test_hang.py": [(TIMEOUT, CROWDED), (TIMEOUT, CROWDED)]})
        self.assertEqual(code, 2, out)
        self.assertIn("CONTENDED", out)
        self.assertIn("contended (exit 2", out, "the summary names the proof among those the run could not judge")
        self.assertNotIn("FAIL ", out)
        self.assertEqual(calls["test_hang.py"], 2)
        self.assertEqual(len(room.waits), 1, "the alone retry waits for room once, then runs whatever it finds")

    def test_the_same_timeout_with_room_is_fail(self):
        code, calls, room, out = self.run_all({"test_hang.py": [(TIMEOUT, ROOMY), (TIMEOUT, ROOMY)]})
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL ", out)
        self.assertNotIn("CONTENDED", out)
        self.assertEqual(room.waits, [], "a red that ran with room has nothing to wait out")

    def test_an_assertion_red_that_repeats_alone_while_crowded_is_contended(self):
        code, _, _, out = self.run_all({"test_budget.py": [(ASSERT, CROWDED), (ASSERT, CROWDED)]},
                                       durations={"test_budget.py": 2.0})
        self.assertEqual(code, 2, out)
        self.assertIn("CONTENDED", out)
        self.assertNotIn("FAIL ", out)

    def test_a_red_with_no_recorded_pass_that_ran_crowded_is_still_retried(self):
        code, calls, _, out = self.run_all({"test_new.py": [(ASSERT, CROWDED), (PASS, ROOMY)]})
        self.assertEqual(code, 0, out)
        self.assertEqual(calls["test_new.py"], 2)

    def test_a_pool_red_that_ran_crowded_and_fails_alone_with_room_is_fail(self):
        code, _, _, out = self.run_all({"test_real.py": [(TIMEOUT, CROWDED), (TIMEOUT, ROOMY)]})
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL ", out)

    def test_a_pool_red_with_room_stays_fail_when_its_alone_retry_runs_crowded(self):
        code, _, _, out = self.run_all({"test_real.py": [(ASSERT, ROOMY), (ASSERT, CROWDED)]},
                                       durations={"test_real.py": 2.0})
        self.assertEqual(code, 1, out)
        self.assertNotIn("CONTENDED", out)

    def test_a_crowded_red_that_passes_alone_is_flaky_not_red(self):
        code, _, _, out = self.run_all({"test_load.py": [(ASSERT, CROWDED), (PASS, ROOMY)]},
                                       durations={"test_load.py": 2.0})
        self.assertEqual(code, 0, out)
        self.assertIn("FLAKY", out)

    def test_a_real_red_does_not_stop_the_retries_of_the_reds_that_ran_crowded(self):
        code, calls, _, out = self.run_all({
            "test_a_real.py": [(ASSERT, ROOMY), (ASSERT, ROOMY)],
            "test_b_load.py": [(ASSERT, CROWDED), (ASSERT, CROWDED)],
            "test_c_unlucky.py": [(ASSERT, ROOMY)]},
            durations={"test_a_real.py": 1.0, "test_b_load.py": 2.0, "test_c_unlucky.py": 3.0})
        self.assertEqual(code, 1, out)
        self.assertEqual(calls["test_b_load.py"], 2, "a load-suspect red is retried after the run is already red")
        self.assertEqual(calls["test_c_unlucky.py"], 1, "a red with room is not retried once the run is red")
        self.assertRegex(out, r"CONTENDED [\d.]+s \S*test_b_load\.py")
        self.assertRegex(out, r"FAIL [\d.]+s \S*test_a_real\.py")
        self.assertRegex(out, r"FAIL [\d.]+s \S*test_c_unlucky\.py")


class RoomReading(unittest.TestCase):
    """`machine_slots.Room` over a planted reading and clock."""

    def room(self, ticks, step=10.0):
        """A Room whose reads walk `ticks` ((busy, total) cumulative) and whose clock advances `step` per read."""
        reads, clock = iter(ticks), iter(i * step for i in range(10 ** 4))
        slept = []
        return slots.Room(read=lambda: next(reads, None), clock=lambda: next(clock), sleep=slept.append), slept

    def test_busy_is_the_cpu_share_between_the_mark_and_now(self):
        room, _ = self.room([(0, 0), (97, 100)])
        self.assertAlmostEqual(room.busy(room.mark()), 0.97)

    def test_an_unreadable_machine_or_a_window_under_the_minimum_has_no_reading(self):
        room, _ = self.room([None, (5, 10)])
        self.assertIsNone(room.busy(room.mark()))
        room, _ = self.room([(0, 0), (9, 10)], step=slots.MIN_WINDOW_S / 2)
        self.assertIsNone(room.busy(room.mark()))

    def test_wait_returns_once_a_whole_quiet_window_has_run_below_saturation(self):
        room, slept = self.room([(0, 0), (99, 100), (99, 100), (120, 200)])
        waited, found = room.wait(limit_s=600)
        self.assertTrue(found)
        self.assertEqual(len(slept), 2, "one saturated window, then one quiet window")

    def test_wait_gives_up_at_its_limit_and_says_no_room_came(self):
        room, slept = self.room([(i * 99, i * 100) for i in range(40)], step=15.0)
        waited, found = room.wait(limit_s=60)
        self.assertFalse(found)
        self.assertLessEqual(sum(slept), 60 + 15)

    def test_wait_with_no_budget_does_not_sleep(self):
        room, slept = self.room([(0, 0), (99, 100)])
        self.assertEqual(room.wait(limit_s=0), (0.0, False))
        self.assertEqual(slept, [])

    def test_a_machine_that_cannot_be_read_never_holds_a_wait(self):
        room, _ = self.room([])
        self.assertTrue(room.wait(limit_s=600)[1])

    def test_the_live_reading_is_a_cumulative_busy_and_total_pair(self):
        """The first step across the OS boundary: this machine answers with busy <= total, both growing."""
        first = slots.cpu_read()
        if first is None:
            self.skipTest("this platform exposes no CPU counter; the runner treats it as no reading")
        self.assertLessEqual(first[0], first[1])
        sum(range(10 ** 6))
        second = slots.cpu_read()
        self.assertGreaterEqual(second[1], first[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
