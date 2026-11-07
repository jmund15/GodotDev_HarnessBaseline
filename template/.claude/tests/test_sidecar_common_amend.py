#!/usr/bin/env python3
"""An author job's amend line names its brief by absolute path, so a job whose cwd is a worktree still finds it."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

CLAUDE = Path(__file__).resolve().parents[1]
BASH = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")
             if os.path.exists(c)), shutil.which("bash"))


def amend_prompt(cwd, prompt_file, shape="author"):
    proc = subprocess.run([BASH, "-c", 'source "$1"; cd "$2"; SC_SHAPE="$3" SC_PROMPT_FILE="$4"; sc_read_prompt; printf "%s" "$SC_PROMPT"',
                           "_", (CLAUDE / "scripts/lib/sidecar_common.sh").as_posix(), cwd, shape, prompt_file],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def amend_path(cwd, prompt_file, shape="author"):
    found = re.search(r"delivers (.+?)\.amend\.md to you", amend_prompt(cwd, prompt_file, shape))
    return found.group(1) if found else None


class AmendLine(unittest.TestCase):
    def test_relative_brief_is_named_absolutely(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_amend_") as tmp:
            brief = Path(tmp) / "scratch" / "job" / "brief.md"
            brief.parent.mkdir(parents=True)
            brief.write_text("task\n", encoding="utf-8")
            named = amend_path(tmp, "scratch/job/brief.md")
            self.assertIsNotNone(named)
            self.assertTrue(Path(named).is_absolute(), named)
            self.assertEqual(os.path.normcase(os.path.realpath(named)), os.path.normcase(os.path.realpath(brief)))

    def test_amend_line_orders_a_read_without_an_existence_probe(self):
        """A Glob existence probe hides a gitignored amend file, so the line orders a Read."""
        with tempfile.TemporaryDirectory(prefix="sidecar_amend_") as tmp:
            Path(tmp, "brief.md").write_text("task\n", encoding="utf-8")
            self.assertNotIn("amend.md exists", amend_prompt(tmp, "brief.md"))

    def test_read_only_shape_gets_no_amend_line(self):
        with tempfile.TemporaryDirectory(prefix="sidecar_amend_") as tmp:
            Path(tmp, "brief.md").write_text("task\n", encoding="utf-8")
            self.assertIsNone(amend_path(tmp, "brief.md", shape="survey"))


if __name__ == "__main__":
    unittest.main()
