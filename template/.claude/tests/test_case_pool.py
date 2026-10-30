#!/usr/bin/env python3
"""Proof for tests/_case_pool.py: cases run in separate processes at once, a failing or
crashing case fails the run and is the only case named, and a case's os.environ change never
reaches another case."""
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _case_pool import emit_section, run_cases, run_sections, unittest_cases  # noqa: E402


def _timed(name):
    """Sleep, recording this case's start and end so the proof can see overlap under any load."""
    start = time.time()
    time.sleep(1)
    with open(os.path.join(os.environ["POOL_SPANS"], name), "w") as fh:
        fh.write("%f %f" % (start, time.time()))


def _max_overlap(spans_dir):
    spans = [tuple(map(float, open(os.path.join(spans_dir, n)).read().split())) for n in os.listdir(spans_dir)]
    return max((sum(1 for s, e in spans if s <= start < e) for start, _ in spans), default=0)


def case_slow_a():
    _timed("a")


def case_slow_b():
    _timed("b")


def case_slow_c():
    _timed("c")


def case_slow_d():
    _timed("d")


def case_sets_env():
    os.environ["CASE_POOL_LEAK"] = "1"


def case_reads_env():
    assert "CASE_POOL_LEAK" not in os.environ, "an env change from another case leaked in"


def case_fails():
    raise AssertionError("planted")


def case_exits():
    os._exit(3)


class PlantedUnit(unittest.TestCase):
    def setUp(self):
        self.ready = True

    def test_passes(self):
        self.assertTrue(self.ready)

    def test_fails(self):
        self.fail("planted")


class FixtureUnit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = "planted"

    @classmethod
    def tearDownClass(cls):
        with open(os.path.join(os.environ["POOL_SPANS"], "teardown_class"), "a", encoding="utf-8") as fh:
            fh.write("x")

    def test_sees_the_class_fixture(self):
        self.assertEqual(self.shared, "planted")


SECTION_CHILD = """
import sys
sys.path.insert(0, %r)
from _case_pool import emit_section
k = int(sys.argv[2])
print("noise from section", k)
if k == 1:
    raise SystemExit("planted crash")
if k == 2:
    print("half-done output")
    raise SystemExit(3)
emit_section([("s%%d first" %% k, True, ""), ("s%%d second" %% k, k != 3, "" if k != 3 else "planted failure")])
"""


def sections_proof():
    """`run_sections` runs one child per section, keeps section order, drops the marker line from the passthrough
    text, and turns a child with no marker line or a nonzero exit into one failed case naming the section."""
    here = os.path.dirname(os.path.abspath(__file__))
    child = os.path.join(tempfile.mkdtemp(), "child.py")
    with open(child, "w", encoding="utf-8") as fh:
        fh.write(SECTION_CHILD % here)
    found = run_sections(child, "--section", 4, names=["a", "b", "c", "d"], jobs=2)
    problems = []
    if [c for _, cases in found for c, _, _ in cases] != ["s0 first", "s0 second", "b crashed", "c crashed", "s3 first", "s3 second"]:
        problems.append("order or crash entries: %r" % [cases for _, cases in found])
    elif found[0][0].strip() != "noise from section 0" or "SECTION-CASES" in found[0][0]:
        problems.append("passthrough text: %r" % found[0][0])
    elif found[1][1][0][1] is not False or "planted crash" not in found[1][1][0][2] or "half-done output" not in found[2][1][0][2]:
        problems.append("crash detail: %r" % found[1:3])
    elif found[3][1][1][1] is not False or found[3][1][1][2] != "planted failure":
        problems.append("a reported failure stays failed: %r" % found[3])
    ok = not problems
    print("%-4s run_sections merges sections in order and reports a crashed child as a failed case %s" % ("ok" if ok else "FAIL", problems))
    return problems


def main():
    failures = sections_proof()
    spans = tempfile.mkdtemp()
    os.environ["POOL_SPANS"] = spans
    rc = run_cases([case_slow_a, case_slow_b, case_slow_c, case_slow_d, case_sets_env, case_reads_env], jobs=6)
    overlap = _max_overlap(spans)
    ok = rc == 0 and overlap >= 2
    print("%-4s independent cases run at once and pass (%d overlapping)" % ("ok" if ok else "FAIL", overlap))
    if not ok:
        failures.append("parallel pass: rc=%s overlap=%d" % (rc, overlap))
    for bad in (case_fails, case_exits):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run_cases([bad, case_sets_env, case_reads_env, case_slow_a], jobs=2)
        named = [line.split()[1].rstrip(":") for line in out.getvalue().splitlines() if line.startswith("FAIL ")]
        ok = rc == 1 and named == [bad.__name__]
        print("%-4s %s fails the run and is the only case named FAIL: %s" % ("ok" if ok else "FAIL", bad.__name__, named))
        if not ok:
            failures.append("%s: rc=%s named=%s" % (bad.__name__, rc, named))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_cases(unittest_cases(PlantedUnit), jobs=2)
    named = [line.split()[1].rstrip(":") for line in out.getvalue().splitlines() if line.startswith("FAIL ")]
    ok = rc == 1 and named == ["PlantedUnit.test_fails"] and "ok PlantedUnit.test_passes" in out.getvalue()
    print("%-4s a unittest method runs as a case with setUp; its failure is the only one named: %s" % ("ok" if ok else "FAIL", named))
    if not ok:
        failures.append("unittest cases: rc=%s named=%s" % (rc, named))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_cases(unittest_cases(FixtureUnit), jobs=1)
    torn = os.path.exists(os.path.join(spans, "teardown_class"))
    ok = rc == 0 and torn
    print("%-4s a unittest method runs inside its class's setUpClass and tearDownClass" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("class fixtures: rc=%s torn=%s %s" % (rc, torn, out.getvalue().strip()[-200:]))
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
