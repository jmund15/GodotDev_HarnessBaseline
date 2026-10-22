#!/usr/bin/env python3
"""Contract proof for .claude/scripts/godot_docs_cache.sh: the argument and skip paths only.

Never reaches the clone: no network. Refuses a missing version source; `--check` reports an absent cache
and exits 2; `ensure` skips a cache already at the pin. Writes only under its temp root.
Requires a POSIX `bash` on PATH (Git Bash on Windows).
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))
from sidecar_launch import git_bash  # noqa: E402

BASH = git_bash()
PIN = "4.7.1"
STACK_LINE = "- Godot engine: **%s** stable mono\n"


class GodotDocsCacheContract(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="godot-docs-cache-contract-"))
        self.addCleanup(shutil.rmtree, self.temp, True)
        scripts = self.temp / ".claude" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / ".claude" / "scripts" / "godot_docs_cache.sh", scripts / "godot_docs_cache.sh")
        self.script = scripts / "godot_docs_cache.sh"

    def write_stack(self):
        reference = self.temp / ".claude" / "reference"
        reference.mkdir(parents=True, exist_ok=True)
        (reference / "project_stack.md").write_text(STACK_LINE % PIN, encoding="utf-8")

    def plant_cache(self):
        classes = self.temp / ".claude" / "cache" / "godot-docs" / "doc" / "classes"
        classes.mkdir(parents=True)
        (classes / "Node.xml").write_text("<class name=\"Node\"/>\n", encoding="utf-8")
        (classes.parent.parent / ".stamp").write_text(PIN + "\n", encoding="utf-8")

    def run_script(self, *args):
        return subprocess.run([BASH, self.script.as_posix(), *args], cwd=self.temp,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_missing_version_source_is_refused(self):
        done = self.run_script("--check")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("missing version SSOT", done.stderr)

    def test_check_reports_absent_cache_and_writes_nothing(self):
        self.write_stack()
        done = self.run_script("--check")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("STALE cache='absent' pin='%s'" % PIN, done.stdout)
        self.assertFalse((self.temp / ".claude" / "cache").exists())

    def test_ensure_skips_cache_already_at_pin(self):
        self.write_stack()
        self.plant_cache()
        done = self.run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("already at %s (1 classes) — nothing to do" % PIN, done.stdout)
        check = self.run_script("--check")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn("in-sync %s (1 classes)" % PIN, check.stdout)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
