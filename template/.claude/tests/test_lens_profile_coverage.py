"""Every plc-*, sa-* and exp-* lens brief fits its engine's shell-free profile or names its uncovered line.

plan_check and session_audit seats run through review_fanout.js (shape review); explore seats run
through explore_fanout.js (shape survey). A brief that needs Bash must be refused with the brief
line that needs it, and the set of such lenses is pinned so a new one is a visible diff.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
LENS = ROOT / ".claude/tools/lens.py"
PROFILES = ROOT / ".claude/tools/tool_profiles.py"
SHAPE = {"plc-": "review", "sa-": "review", "exp-": "survey"}
# exp-external-truth fetches P1 sources through fetch_source.sh; its dispatch owns that grant.
NAMED_UNCOVERED = {"exp-external-truth"}


def call(*args):
    return subprocess.run([sys.executable, *map(str, args)], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


def keys():
    index = call(LENS, "index")
    if index.returncode != 0:
        raise AssertionError(index.stderr)
    return [line.split()[0] for line in index.stdout.splitlines()
            if line.split() and line.split()[0].startswith(tuple(SHAPE))]


def resolve(key, brief_dir, extra=""):
    shape = next(value for prefix, value in SHAPE.items() if key.startswith(prefix))
    fetched = call(LENS, "get", "--shared", key)
    if fetched.returncode != 0:
        raise AssertionError(fetched.stderr)
    brief = Path(brief_dir) / f"{key}.md"
    brief.write_text(fetched.stdout + extra, encoding="utf-8", newline="\n")
    return brief, call(PROFILES, "resolve", "--shape", shape, "--brief", brief, "--field", "profile")


class LensProfileCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.keys = keys()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_index_population_covers_every_prefix(self):
        for prefix in SHAPE:
            self.assertTrue(any(key.startswith(prefix) for key in self.keys), prefix)
        self.assertEqual(len(self.keys), len(set(self.keys)))

    def test_every_brief_fits_or_names_its_uncovered_line(self):
        uncovered = set()
        for key in self.keys:
            with self.subTest(key=key):
                brief, proc = resolve(key, self.tmp.name)
                if proc.returncode == 0:
                    self.assertNotIn("Bash", json.loads(proc.stdout)["builtins"])
                    continue
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("needs Bash", proc.stderr)
                self.assertRegex(proc.stderr, re.escape(brief.name) + r":\d+:")
                uncovered.add(key)
        self.assertEqual(uncovered, NAMED_UNCOVERED)

    def test_a_planted_shell_line_is_refused_with_its_location(self):
        key = next(key for key in self.keys if key not in NAMED_UNCOVERED)
        brief, proc = resolve(key, self.tmp.name, "\nRun `git log --oneline -5` first.\n")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertRegex(proc.stderr, re.escape(brief.name) + r":\d+:")


if __name__ == "__main__":
    unittest.main()
