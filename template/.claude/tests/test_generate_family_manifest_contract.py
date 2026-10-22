#!/usr/bin/env python3
"""Contract proof for .claude/hooks/generate_family_manifest.py.

Plants a project with one abstract base and one implementor. A run writes the manifest under the temp root
and lists the implementor. When `.claude/generated` is a file, the output directory cannot be made and the
tool exits non-zero. Writes only under its temp root.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class GenerateFamilyManifestContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="generate-family-manifest-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        hooks = self.temp / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("generate_family_manifest.py", "_cs_files.py"):
            shutil.copy(ROOT / ".claude" / "hooks" / name, hooks / name)
        self.tool = hooks / "generate_family_manifest.py"
        scripts = self.temp / "Scripts"
        scripts.mkdir()
        (scripts / "Base.cs").write_text("public abstract class WidgetBase\n{\n}\n", encoding="utf-8")
        (scripts / "Impl.cs").write_text("public class WidgetImpl : WidgetBase\n{\n}\n", encoding="utf-8")

    def run_tool(self):
        return subprocess.run([sys.executable, str(self.tool)], cwd=self.temp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_manifest_lists_the_implementor(self):
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        manifest = self.temp / ".claude" / "generated" / "abstraction_families.md"
        self.assertTrue(manifest.is_file())
        self.assertIn("WidgetImpl", manifest.read_text(encoding="utf-8"))
        self.assertIn("1 families, 1 implementors", done.stdout)

    def test_output_directory_blocked_by_a_file_fails(self):
        (self.temp / ".claude" / "generated").write_text("not a directory\n", encoding="utf-8")
        done = self.run_tool()
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("FileExistsError", done.stderr)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
