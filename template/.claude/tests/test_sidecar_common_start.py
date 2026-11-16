#!/usr/bin/env python3
"""Proof of the two launcher start-cost cuts in `scripts/lib/sidecar_common.sh`: `sc_to_native` converts a path in
bash with the answer `cygpath -w` gives, and `sc_ensure_launch_id` builds a canonical, unique launch id without a
Python start. A launch calls the first about thirty times and the second once; each cost an exec.

Run: python3 .claude/tests/test_sidecar_common_start.py
"""
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
import sidecar_launch  # noqa: E402

BASH = sidecar_launch.git_bash()
LIB = ".claude/scripts/lib/sidecar_common.sh"

# Shapes a launch converts, then the ones that must still reach cygpath for its normalizing.
PATHS = ["/tmp/tmp.abc", "/tmp/", "/tmp/a/b.c.ratelimit", "C:/Users/dev/x y/z", "c:/x/y", "/c/Users/dev/a", "/C/x",
         "/c/", "C:/", "C:/trail/", "'C:\\already\\native'", "C:/a/../b", "/tmp/a/./b", "/tmp/..", "//server/share/x",
         "/usr/bin/x", "relative/p", "/d/x.y", "C:\\mix/ed"]


def run(script):
    return subprocess.run([BASH, "-c", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


class StartCost(unittest.TestCase):
    def test_sc_to_native_answers_as_cygpath_does(self):
        probe = "; ".join('printf "%%s\\t%%s\\n" "$(sc_to_native %s)" "$(cygpath -w %s)"' % (p if p.startswith("'") else "'%s'" % p,
                                                                                         p if p.startswith("'") else "'%s'" % p)
                          for p in PATHS)
        done = run(". %s; %s" % (LIB, probe))
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = [line.split("\t") for line in done.stdout.splitlines()]
        self.assertEqual(len(rows), len(PATHS), done.stdout)
        for path, (got, want) in zip(PATHS, rows):
            self.assertEqual(got, want, path)

    def test_sc_to_native_spawns_no_cygpath_for_the_common_shapes(self):
        # `cygpath` shadowed by a function that fails the call: a common shape must not reach it.
        done = run(". %s; cygpath() { echo SPAWNED; }; SC_TMP_NATIVE='C:\\T'; "
                   "for p in /tmp/x /c/Users/a C:/Users/a 'C:\\a\\b'; do sc_to_native \"$p\"; echo; done" % LIB)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("SPAWNED", done.stdout)
        self.assertIn("C:\\T\\x", done.stdout)

    def test_sc_to_native_empty_and_odd_shapes_still_go_through_cygpath(self):
        done = run(". %s; cygpath() { echo VIA_CYGPATH; }; sc_to_native 'C:/a/../b'; sc_to_native 'rel/p'; sc_to_native ''" % LIB)
        self.assertEqual(done.stdout.split(), ["VIA_CYGPATH", "VIA_CYGPATH"], done.stdout)

    def test_launch_id_is_a_canonical_unique_version_4_uuid_made_in_bash(self):
        done = run(". %s; python3() { echo PYTHON_STARTED >&2; return 1; }; "
                   "for i in $(seq 1 40); do SC_LAUNCH_ID=''; sc_ensure_launch_id || exit 3; echo \"$SC_LAUNCH_ID\"; done" % LIB)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("PYTHON_STARTED", done.stderr)
        ids = done.stdout.split()
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40, "launch ids must never repeat")
        for value in ids:
            parsed = uuid.UUID(value)
            self.assertEqual(str(parsed), value)
            self.assertEqual(parsed.version, 4)

    def test_an_id_already_set_is_kept(self):
        done = run(". %s; SC_LAUNCH_ID=fixed-id; sc_ensure_launch_id; echo \"$SC_LAUNCH_ID $SIDECAR_LAUNCH_ID\"" % LIB)
        self.assertEqual(done.stdout.split(), ["fixed-id", "fixed-id"])


if __name__ == "__main__":
    unittest.main()
