#!/usr/bin/env python3
"""Contract proof for .claude/hooks/tool_cascade_audit.py.

Plants a one-file project with a [GlobalClass] Resource that lacks [Tool]: the audit exits 1 (blanket-on-Resources gap).
The same class with [Tool] exits 0. Runs with --root on its temp root and writes only there.
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


class ToolCascadeAuditContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="tool-cascade-audit-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("tool_cascade_audit.py", "_cs_files.py", "_checkout_root.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, hooks / name)
        (self.temp / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        scripts = self.temp / "Scripts"
        scripts.mkdir()
        self.resource = scripts / "Thing.cs"

    def audit(self):
        return subprocess.run([sys.executable, str(self.temp / ".claude" / "hooks" / "tool_cascade_audit.py"),
                               "--root", str(self.temp), "--quiet"],
                              cwd=self.temp, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)

    def test_global_class_resource_without_tool_fails_the_audit(self):
        self.resource.write_text(RESOURCE % "", encoding="utf-8")
        done = self.audit()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_global_class_resource_with_tool_passes_the_audit(self):
        self.resource.write_text(RESOURCE % ", Tool", encoding="utf-8")
        done = self.audit()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue((self.temp / ".claude" / "hooks" / "tool_resource_classes.txt").is_file())


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
