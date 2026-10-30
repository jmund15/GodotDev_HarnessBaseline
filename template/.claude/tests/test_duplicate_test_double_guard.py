#!/usr/bin/env python3
"""Proof of the standalone test-double scan used by the regression gate."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import duplicate_test_double_guard as guard


class StandaloneTests(unittest.TestCase):
    def run_scan(self, count, baseline=None, shared=False, kind="interface", abstract=False,
                 mode=None, bypass=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modifier = "abstract " if abstract else ""
            (root / "Contract.cs").write_text(
                f"public {modifier}{kind} IPlug {{}}\n", encoding="utf-8")
            tests = root / "Tests" / ("Framework" if shared else "Logic")
            tests.mkdir(parents=True)
            for index in range(count):
                (tests / f"Fake{index}.cs").write_text(
                    f"private sealed class Fake{index} : IPlug {{}}\n", encoding="utf-8")
            baseline_path = root / "baseline.json"
            baseline_path.write_text(
                json.dumps({} if baseline is None else {"IPlug": baseline}), encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            args = ["guard"] + ([mode] if mode else [])
            env = {"HARNESS_ALLOW_DUPLICATE_TEST_DOUBLES": "1"} if bypass else {}
            with patch.object(guard, "REPO", root), patch.object(guard, "TESTS_ROOT", root / "Tests"), \
                    patch.object(guard, "BASELINE_PATH", baseline_path), patch.object(sys, "argv", args), \
                    patch.dict(os.environ, env, clear=True), redirect_stdout(output), redirect_stderr(errors):
                result = guard.main()
            return result, output.getvalue(), errors.getvalue(), json.loads(baseline_path.read_text())

    def run_mixed(self, class_bases):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Contract.cs").write_text(
                "public interface IPlug {}\npublic abstract class TaskBase {}\n"
                "public abstract class StateBase {}\n", encoding="utf-8")
            tests = root / "Tests" / "Logic"
            tests.mkdir(parents=True)
            for index, base in enumerate(class_bases):
                (tests / f"Fake{index}.cs").write_text(
                    f"private sealed partial class Fake{index} : {base}, IPlug {{}}\n", encoding="utf-8")
            baseline_path = root / "baseline.json"
            baseline_path.write_text("{}", encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(guard, "REPO", root), patch.object(guard, "TESTS_ROOT", root / "Tests"), \
                    patch.object(guard, "BASELINE_PATH", baseline_path), patch.object(sys, "argv", ["guard"]), \
                    patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(errors):
                result = guard.main()
            return result, output.getvalue(), errors.getvalue()

    def test_interface_doubles_on_distinct_project_class_bases_are_advisory(self):
        result, output, errors = self.run_mixed(["TaskBase", "StateBase"])
        self.assertEqual(result, 0)
        self.assertIn("advisory", output)
        self.assertEqual(errors, "")

    def test_interface_doubles_sharing_a_project_class_base_block(self):
        result, output, errors = self.run_mixed(["TaskBase", "TaskBase"])
        self.assertEqual(result, 1)
        self.assertIn("IPlug  (2 doubles)", errors)

    def test_flags_family_growth(self):
        result, output, errors, _ = self.run_scan(3, baseline=2)
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("IPlug  (3 doubles)", errors)
        self.assertIn("Tests/Logic/Fake0.cs:1", errors)
        self.assertIn("Tests/Framework/Mocks", errors)

    def test_second_interface_double_blocks_without_baseline_row(self):
        result, output, errors, _ = self.run_scan(2)
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("IPlug  (2 doubles)", errors)

    def test_passes_grandfathered_family(self):
        result, output, errors, _ = self.run_scan(2, baseline=2)
        self.assertEqual(result, 0)
        self.assertIn("1 grandfathered", output)
        self.assertEqual(errors, "")

    def test_subclasses_are_advisory(self):
        for kind, abstract in (("class", False), ("class", True), ("record", False), ("struct", False)):
            with self.subTest(kind=kind, abstract=abstract):
                result, output, errors, _ = self.run_scan(3, kind=kind, abstract=abstract)
                self.assertEqual(result, 0)
                self.assertIn("1 advisory", output)
                self.assertEqual(errors, "")

    def test_json_rows_carry_base_kind(self):
        for kind in ("interface", "class", "record", "struct"):
            with self.subTest(kind=kind):
                result, output, errors, _ = self.run_scan(2, kind=kind, mode="--json")
                self.assertEqual(result, 0)
                self.assertEqual(errors, "")
                data = json.loads(output)
                bucket = "blocking" if kind == "interface" else "advisory"
                self.assertEqual(len(data[bucket]), 1)
                self.assertEqual(data[bucket][0]["kind"], kind)
                self.assertEqual(data[bucket][0]["base"], "IPlug")
                self.assertEqual(data[bucket][0]["count"], 2)
                self.assertEqual(len(data[bucket][0]["sites"]), 2)
                self.assertEqual(data["interface_fail_at"], 2)

    def test_json_grandfathered_row_carries_kind(self):
        result, output, errors, _ = self.run_scan(2, baseline=2, mode="--json")
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")
        data = json.loads(output)
        self.assertEqual(data["blocking"], [])
        self.assertEqual(data["grandfathered"][0]["kind"], "interface")

    def test_baseline_snapshots_two_copy_families(self):
        result, output, errors, baseline = self.run_scan(2, mode="--write-baseline")
        self.assertEqual(result, 0)
        self.assertIn("1 grandfathered families", output)
        self.assertEqual(errors, "")
        self.assertEqual(baseline, {"IPlug": 2})

    def test_passes_shared_doubles(self):
        result, output, errors, _ = self.run_scan(3, shared=True)
        self.assertEqual(result, 0)
        self.assertIn("OK", output)
        self.assertEqual(errors, "")

    def test_single_double_is_not_reported(self):
        result, output, errors, _ = self.run_scan(1, mode="--json")
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")
        data = json.loads(output)
        for bucket in ("blocking", "grandfathered", "advisory"):
            self.assertEqual(data[bucket], [])

    def test_env_bypass(self):
        result, output, errors, _ = self.run_scan(2, bypass=True)
        self.assertEqual(result, 0)
        self.assertIn("SKIPPED", output)
        self.assertEqual(errors, "")


if __name__ == "__main__":
    unittest.main()
