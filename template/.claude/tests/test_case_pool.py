#!/usr/bin/env python3
"""Proof for tests/_case_pool.py: cases run in separate processes at once, a failing or
crashing case fails the run and is the only case named, and a case's os.environ change never
reaches another case."""
import contextlib
import io
import re
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _case_pool import check, checked_cases, emit_section, run_cases, run_sections, run_sharded, unittest_cases  # noqa: E402


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


def checks_one_failed():
    check("planted check", False, "its detail")
    check("a later check", True)


def checks_all_pass():
    check("a passing check", True)


def case_fails_with_log():
    raise AssertionError("roster did not end: the reason\nGRID START\nGRID COMPLETE")


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


def _run_only(only, runner=False):
    """Run three cases under `HARNESS_CASES_ONLY=only`, as a direct run or (`runner`) as a proof the runner launched.
    The slot store `main` planted stays set either way: a proof that plants one is still a direct run."""
    saved = {k: os.environ.pop(k, None) for k in ("HARNESS_CASES_ONLY", "HARNESS_PROOF_TIMEOUT_S")}
    os.environ["HARNESS_CASES_ONLY"] = only
    if runner:
        os.environ["HARNESS_PROOF_TIMEOUT_S"] = "120"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = run_cases([case_fails, case_reads_env, case_sets_env], jobs=2)
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
    return rc, out.getvalue()


def only_proof():
    """`HARNESS_CASES_ONLY` runs the cases it names and no other, says the run is partial, fails when it names none,
    and is ignored under the harness runner, whose pass must cover every case."""
    problems = []
    rc, text = _run_only("reads_env,sets_env")
    if rc != 0 or "ok case_reads_env" not in text or "ok case_sets_env" not in text or "case_fails" in text:
        problems.append("named cases alone: rc=%s %r" % (rc, text))
    elif "partial: 2 of 3 cases" not in text:
        problems.append("the count line does not say the run is partial: %r" % text)
    rc, text = _run_only("fails")
    if rc != 1 or "FAIL case_fails" not in text:
        problems.append("a named failing case fails the run: rc=%s %r" % (rc, text))
    rc, text = _run_only("no_such_case")
    if rc != 1 or "case_reads_env" not in text:
        problems.append("a filter matching no case fails and lists the names: rc=%s %r" % (rc, text))
    rc, text = _run_only("reads_env", runner=True)
    if rc != 1 or "FAIL case_fails" not in text or "partial" in text:
        problems.append("under the runner every case runs: rc=%s %r" % (rc, text))
    ok = not problems
    print("%-4s HARNESS_CASES_ONLY runs the named cases alone, outside the runner only %s" % ("ok" if ok else "FAIL", problems))
    return problems


def main():
    # This fixture proves available parallel capacity, independent of live machine occupancy.
    pool_root = tempfile.mkdtemp(prefix='case_pool_budget_')
    os.environ.update(HARNESS_SLOT_DIR=pool_root, HARNESS_MACHINE_ROOT=pool_root, HARNESS_MACHINE_SLOTS='6',
                      HARNESS_CASE_JOBS='6')
    os.environ.pop("POOL_SPANS", None)
    failures = sections_proof() + only_proof()
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
        rc = run_cases([case_fails_with_log], jobs=1)
    line = next((l for l in out.getvalue().splitlines() if l.startswith("FAIL ")), "")
    ok = rc == 1 and "the reason" in line and "GRID COMPLETE" not in line
    print("%-4s a multi-line failure names its reason line, never only the log tail that ends it: %r" % ("ok" if ok else "FAIL", line))
    if not ok:
        failures.append("multi-line failure line: %r" % line)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_cases(checked_cases({"case_x": checks_one_failed, "helper": case_fails, "case_y": checks_all_pass}), jobs=2)
    text = out.getvalue()
    named = [line.split()[1].rstrip(":") for line in text.splitlines() if line.startswith("FAIL ")]
    ok = (rc == 1 and named == ["checks_one_failed"] and "planted check: its detail" in text
          and "ok checks_all_pass" in text and "1/2 cases pass" in text)
    print("%-4s a checked case fails with the checks it failed, alone, and only case_* names run: %s" % ("ok" if ok else "FAIL", named))
    if not ok:
        failures.append("checked cases: rc=%s %r" % (rc, text[-400:]))
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
    # A deep checkout times many node ids passes the OS command-line limit if a shard's ids ride on argv.
    top = tempfile.mkdtemp(prefix="pool_deep_")
    with open(os.path.join(top, "pytest.ini"), "w", encoding="utf-8") as fh:
        fh.write("[pytest]\n")  # the planted tree is its own rootdir, never the machine's TEMP
    deep = os.path.join(top, *(["d" * 60] * 3))
    os.makedirs(deep)
    planted = os.path.join(deep, "test_many.py")
    with open(planted, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join("def test_case_%03d_%s():\n    pass\n\n" % (i, "x" * 60) for i in range(400)))
    saved = os.environ.get("HARNESS_CASE_JOBS")
    os.environ["HARNESS_CASE_JOBS"] = "1"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = run_sharded(planted)
    except OSError as exc:
        rc = repr(exc)
    finally:
        if saved is None:
            os.environ.pop("HARNESS_CASE_JOBS", None)
        else:
            os.environ["HARNESS_CASE_JOBS"] = saved
    passed = sum(int(n) for n in re.findall(r"(\d+) passed", out.getvalue()))
    ok = rc == 0 and passed == 400
    print("%-4s a shard whose node ids exceed the command-line limit still runs every case" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("long shard: rc=%s %s" % (rc, out.getvalue().strip()[-200:]))
    print("%d failure(s)" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
