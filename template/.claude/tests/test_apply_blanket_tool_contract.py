#!/usr/bin/env python3
"""Contract proof for .claude/hooks/apply_blanket_tool.py.

Plants a one-file project. `--dry-run` plans the [Tool] edit and writes nothing. A plain run adds [Tool]
to the [GlobalClass] line. A [GlobalClass] line the rewrite cannot parse is reported as an ANOMALY and its
file is left unchanged. The tool exits 0 in both cases, so the refusal is asserted on its report and bytes.
Writes only under its temp root.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = """using Godot;

[GlobalClass%s]
public partial class Thing : Resource
{
}
"""


class ApplyBlanketToolContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="apply-blanket-tool-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("apply_blanket_tool.py", "tool_cascade_audit.py", "_cs_files.py", "_checkout_root.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, hooks / name)
        self.tool = hooks / "apply_blanket_tool.py"
        (self.temp / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        scripts = self.temp / "Scripts"
        scripts.mkdir()
        self.resource = scripts / "Thing.cs"

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(self.tool), *args], cwd=self.temp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_dry_run_plans_the_edit_and_writes_nothing(self):
        self.resource.write_text(RESOURCE % "", encoding="utf-8")
        done = self.run_tool("--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Planned edits: 1", done.stdout)
        self.assertIn("[GlobalClass]  ->  [GlobalClass, Tool]", done.stdout)
        self.assertEqual(self.resource.read_text(encoding="utf-8"), RESOURCE % "")

    def test_apply_adds_tool_to_the_global_class_line(self):
        self.resource.write_text(RESOURCE % "", encoding="utf-8")
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Applied edits to 1 files", done.stdout)
        self.assertEqual(self.resource.read_text(encoding="utf-8"), RESOURCE % ", Tool")

    def test_unparseable_global_class_line_is_reported_and_left_unchanged(self):
        original = RESOURCE % '("thing")'
        self.resource.write_text(original, encoding="utf-8")
        done = self.run_tool()
        self.assertIn("ANOMALIES", done.stdout)
        self.assertIn("could not transform line", done.stdout)
        self.assertIn("NOT edited", done.stdout)
        self.assertEqual(self.resource.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
