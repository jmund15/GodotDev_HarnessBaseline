#!/usr/bin/env python3
"""Contract proof for .claude/tools/test_profile.py.

A unit folder holding one well-formed TRX is profiled and exits 0. A unit folder whose TRX is malformed is
listed under "problems" and exits 1. Reads only planted TRX files under its temp root.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude" / "tools" / "test_profile.py"
TRX = """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testId="11111111-1111-1111-1111-111111111111" testName="Tests.Logic.Widgets.WidgetTests.Spins" duration="00:00:01.5000000" outcome="Passed" />
  </Results>
  <TestDefinitions>
    <UnitTest name="Spins" id="11111111-1111-1111-1111-111111111111">
      <TestMethod className="Tests.Logic.Widgets.WidgetTests" name="Spins" />
    </UnitTest>
  </TestDefinitions>
</TestRun>
"""


class TestProfileContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="test-profile-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.runs = self.temp / "runs"
        self.unit = self.runs / "unit"
        self.unit.mkdir(parents=True)

    def run_tool(self):
        return subprocess.run([sys.executable, str(TOOL), str(self.runs)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    def test_well_formed_trx_is_profiled(self):
        (self.unit / "results.trx").write_text(TRX, encoding="utf-8")
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Spins", done.stdout)

    def test_malformed_trx_is_listed_as_a_problem(self):
        (self.unit / "results.trx").write_text("<TestRun><Results>", encoding="utf-8")
        done = self.run_tool()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Problems (1):", done.stdout)
        self.assertIn("results.trx: ParseError", done.stdout)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
