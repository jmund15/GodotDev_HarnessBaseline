#!/usr/bin/env python3
"""Contract proof for .claude/hooks/test_suite_gate_coverage_guard.py.

Plants the two gate filter sources naming every GATED prefix, then a runnable [TestSuite] under a gated
prefix (exit 0) and one under an un-gated top-level directory (exit 1). Writes only under its temp root.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILTERS = "\n".join("-Filter \"FullyQualifiedName~Tests.%s\"" % name for name in ("Logic", "Integration", "Sanity"))
SUITE = """using NUnit.Framework;

namespace Tests.%(namespace)s
{
    [TestSuite]
    public partial class %(name)s
    {
    }
}
"""


class TestSuiteGateCoverageContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="test-suite-gate-coverage-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(ROOT / ".claude" / "hooks" / "test_suite_gate_coverage_guard.py",
                    hooks / "test_suite_gate_coverage_guard.py")
        shutil.copy(ROOT / ".claude" / "hooks" / "_checkout_root.py", hooks / "_checkout_root.py")
        self.guard = hooks / "test_suite_gate_coverage_guard.py"
        scripts = self.temp / ".claude" / "scripts"
        scripts.mkdir(parents=True)
        for name in ("regression_gate.ps1", "IntegrationBatches.ps1"):
            (scripts / name).write_text(FILTERS + "\n", encoding="utf-8")
        self.tests = self.temp / "Tests"

    def plant_suite(self, directory, namespace, name):
        folder = self.tests / directory
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (name + ".cs")).write_text(SUITE % {"namespace": namespace, "name": name}, encoding="utf-8")

    def run_guard(self):
        return subprocess.run([sys.executable, str(self.guard)], cwd=self.temp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_suite_under_a_gated_prefix_passes(self):
        self.plant_suite("Logic", "Logic.Widgets", "WidgetTests")
        done = self.run_guard()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("every runnable [TestSuite] is under a gated prefix", done.stdout)

    def test_suite_under_an_ungated_directory_fails(self):
        self.plant_suite("Stray", "Stray", "StrayTests")
        done = self.run_guard()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("ungated  Tests/Stray", done.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
