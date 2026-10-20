"""Run a proof's independent cases in separate processes at once.

A case that builds git fixtures spends its time spawning processes, so running them one at a
time is the proof's whole cost. Each case runs in its own process: an `os.environ` or cwd change
in one case cannot reach another, and a case that crashes the interpreter fails alone. Cases must
not depend on each other's order or leftovers, and must not assert wall-clock deadlines.

    from _case_pool import run_cases
    sys.exit(run_cases(cases))

`run_sections` is the same for a proof whose cases share one script's setup: the proof re-runs itself once
per section (`--section K`), each child ends with `emit_section(cases)`, and the parent merges the results.

`HARNESS_CASE_JOBS` overrides the width (default 4; the harness runner sets 2 for its proofs)."""
import json
import os
import subprocess
import sys
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool


def _run_one(case):
    try:
        case()
        return None
    except BaseException:  # noqa: BLE001 -- every case failure is reported, never raised into the pool
        return traceback.format_exc(limit=3).strip().splitlines()[-1]


def _isolated(case):
    """The case's error text, or None. A fresh one-process executor per case, so a crash breaks
    only this case's pool."""
    with ProcessPoolExecutor(max_workers=1) as pool:
        try:
            return pool.submit(_run_one, case).result()
        except BrokenProcessPool as exc:
            return "case process died: %s" % exc


def _jobs(jobs):
    if jobs:
        return jobs
    try:
        value = int(os.environ.get("HARNESS_CASE_JOBS", ""))
    except ValueError:
        value = 0
    return value if value > 0 else 4


def run_cases(cases, jobs=None):
    """0 when every case passes, else 1. Prints `ok <case>` / `FAIL <case>: <error>` and a count."""
    failures = []
    with ThreadPoolExecutor(max_workers=_jobs(jobs)) as pool:
        for case, error in zip(cases, pool.map(_isolated, cases)):
            if error is None:
                print("ok " + case.__name__, flush=True)
            else:
                failures.append(case.__name__)
                print("FAIL %s: %s" % (case.__name__, error), flush=True)
    print("%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


_SECTION_MARK = "SECTION-CASES "


def emit_section(cases):
    """A section child's last output: its `(label, ok, detail)` cases, for `run_sections` to merge."""
    print(_SECTION_MARK + json.dumps(cases))


def run_sections(script, flag, count, names=None, jobs=None):
    """Run `python script flag K` for each K in range(count), `_jobs` at once, as one child process each.
    Returns, in section order, `(passthrough text, cases)`; a child that exits nonzero or never calls
    `emit_section` yields one failed case `<name> crashed` carrying the tail of its output."""
    def run(k):
        proc = subprocess.run([sys.executable, script, flag, str(k)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        lines = proc.stdout.splitlines()
        marked = [ln for ln in lines if ln.startswith(_SECTION_MARK)]
        if proc.returncode != 0 or not marked:
            return proc.stdout, [("%s crashed" % (names[k] if names else "section %d" % k), False,
                                  (proc.stdout + proc.stderr)[-600:])]
        text = "".join(ln + "\n" for ln in lines if not ln.startswith(_SECTION_MARK))
        return text, [tuple(c) for c in json.loads(marked[-1][len(_SECTION_MARK):])]

    with ThreadPoolExecutor(max_workers=_jobs(jobs)) as pool:
        return list(pool.map(run, range(count)))


class _Method:
    """One unittest method as a pool case: it runs with its class's setUp/tearDown and raises its failure text."""

    def __init__(self, cls, name):
        self.cls, self.__name__ = cls, "%s.%s" % (cls.__name__, name)
        self.method = name

    def __call__(self):
        result = unittest.TestResult()
        self.cls.setUpClass()
        try:
            self.cls(self.method).run(result)
        finally:
            self.cls.tearDownClass()
        if not result.wasSuccessful():
            text = "\n".join(text for _, text in result.failures + result.errors)
            print(text, file=sys.stderr, flush=True)
            raise AssertionError(text.strip().splitlines()[-1])


def unittest_cases(*classes):
    """Every `test*` method of the given TestCase classes, as cases for `run_cases`. Methods must be independent."""
    return [_Method(cls, name) for cls in classes for name in unittest.TestLoader().getTestCaseNames(cls)]


def run_sharded(path):
    """Run a pytest file as `HARNESS_CASE_JOBS` shard processes of round-robin node ids. 0 when all pass."""
    base = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
    listing = subprocess.run(base + ["--collect-only", "-q", path], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, encoding="utf-8")
    file = os.path.abspath(path)
    ids = [file + "::" + line.split("::", 1)[1] for line in listing.stdout.splitlines() if "::" in line]
    if listing.returncode != 0 or not ids:
        sys.stdout.write(listing.stdout + listing.stderr)
        return listing.returncode or 1
    shards = [ids[i::_jobs(None)] for i in range(_jobs(None))]
    shards = [shard for shard in shards if shard]

    def run(shard):
        proc = subprocess.run(base + ["-q", "-s"] + shard, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout + proc.stderr

    code = 0
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        for rc, text in pool.map(run, shards):
            sys.stdout.write(text)
            code = code or rc
    print("%d cases in %d shards" % (len(ids), len(shards)))
    return code
