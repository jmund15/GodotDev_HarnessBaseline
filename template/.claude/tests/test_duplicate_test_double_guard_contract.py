#!/usr/bin/env python3
"""Contract proof for .claude/hooks/duplicate_test_double_guard.py.

Plants a project with one interface declared in production code. Two file-local test doubles of that
interface exit 1 (a blocking family with no committed baseline). One double is within the threshold and
exits 0. Writes only under its temp root.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERFACE = "public interface IWidget\n{\n    void Run();\n}\n"
DOUBLE = """using Godot;

public partial class Feature_%(n)s_Tests
{
    private sealed partial class FakeWidget%(n)s : Node, IWidget
    {
        public void Run() { }
    }
}
"""


class DuplicateTestDoubleGuardContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="duplicate-test-double-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("duplicate_test_double_guard.py", "_cs_files.py", "_checkout_root.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, hooks / name)
        self.guard = hooks / "duplicate_test_double_guard.py"
        (self.temp / "Scripts").mkdir()
        (self.temp / "Scripts" / "IWidget.cs").write_text(INTERFACE, encoding="utf-8")
        self.tests = self.temp / "Tests" / "Feature"
        self.tests.mkdir(parents=True)

    def plant_double(self, n):
        (self.tests / ("Double%s.cs" % n)).write_text(DOUBLE % {"n": n}, encoding="utf-8")

    def run_guard(self, *args):
        return subprocess.run([sys.executable, str(self.guard), *args], cwd=self.temp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_two_file_local_doubles_of_one_interface_block(self):
        self.plant_double("A")
        self.plant_double("B")
        done = self.run_guard()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("IWidget  (2 doubles)", done.stderr)

    def test_one_double_passes(self):
        self.plant_double("A")
        done = self.run_guard()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK - no family grew past the baseline", done.stdout)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
