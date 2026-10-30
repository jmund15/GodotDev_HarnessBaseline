#!/usr/bin/env python3
"""Proof of the standalone suite-coverage scan used by the regression gate."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import test_suite_gate_coverage_guard as guard


class StandaloneTests(unittest.TestCase):
    def run_scan(self, namespace, suite_dir="Logic", filters=("Logic", "Integration", "Sanity")):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "Tests" / suite_dir
            tests.mkdir(parents=True)
            (tests / "Example.cs").write_text(
                f"namespace {namespace};\n[TestSuite] public class Example {{}}\n", encoding="utf-8")
            for rel in guard.FILTER_SOURCES:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(f'Run -Filter "FullyQualifiedName~Tests.{name}"' for name in filters),
                                encoding="utf-8", newline="\n")
            output, errors = io.StringIO(), io.StringIO()
            with patch.object(guard, "REPO", root), patch.object(guard, "TESTS_ROOT", root / "Tests"), \
                    patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(errors):
                result = guard.main()
            return result, output.getvalue(), errors.getvalue()

    def test_flags_ungated_suite(self):
        result, output, errors = self.run_scan("Tests.Encounter", "Encounter")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("ungated  Tests/Encounter", errors)
        self.assertIn("Tests/Encounter/Example.cs", errors)

    def test_flags_wrong_namespace_in_gated_directory(self):
        result, output, errors = self.run_scan("Tests.Encounter")
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("ungated  Tests/Logic", errors)

    def test_flags_filter_drift(self):
        result, output, errors = self.run_scan("Tests.Logic", filters=("Logic", "Integration"))
        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertIn("drift  Tests/Sanity", errors)

    def test_passes_gated_suite(self):
        result, output, errors = self.run_scan("Tests.Logic")
        self.assertEqual(result, 0)
        self.assertIn("OK", output)
        self.assertEqual(errors, "")

    def test_passes_excluded_measurement_suite(self):
        result, output, errors = self.run_scan("Tests.Stress", "Stress")
        self.assertEqual(result, 0)
        self.assertIn("OK", output)
        self.assertEqual(errors, "")


if __name__ == "__main__":
    unittest.main()
