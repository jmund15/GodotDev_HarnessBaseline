"""Run a proof's independent cases in separate processes at once.

A case that builds git fixtures spends its time spawning processes, so running them one at a
time is the proof's whole cost. Each case runs in its own process: an `os.environ` or cwd change
in one case cannot reach another, and a case that crashes the interpreter fails alone. Cases must
not depend on each other's order or leftovers, and must not assert wall-clock deadlines.

    from _case_pool import run_cases
    sys.exit(run_cases(cases))

`run_sections` is the same for a proof whose cases share one script's setup: the proof re-runs itself once
per section (`--section K`), each child ends with `emit_section(cases)`, and the parent merges the results.

`HARNESS_CASE_JOBS` requests a width (default 4; the runner requests 2). The pool
uses its parent's lane and borrows free machine slots, or takes a base lane when run directly.

Under the harness runner's read recorder, each `run_cases` case logs its reads to its own folder (`case_log_dir`),
so the runner records a pass per case and later skips a case whose recorded pass still holds
(`HARNESS_CASES_REUSED`)."""
import json
import os
import subprocess
import sys
import traceback
import unittest
import urllib.parse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool


def _budget(jobs):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
    from machine_slots import parallel_budget
    return parallel_budget(jobs)


def _run_one(case):
    try:
        case()
        return None
    except BaseException as exc:  # noqa: BLE001 -- every case failure is reported, never raised into the pool
        # A message's first line is its reason; a case that appends a log to it ends on the log's tail.
        return traceback.format_exception_only(type(exc), exc)[0].strip().splitlines()[0]


def case_log_dir(base, name):
    """The folder a case's reads go to under the proof's read log; the runner reads it back by this name."""
    return os.path.join(base, "cases", urllib.parse.quote(name, safe=""))


def _case_reads(name):
    """In the case's worker, before the case runs: its reads, and its children's, go to its own folder."""
    base = os.environ.get("HARNESS_READ_LOG")
    if base:
        import sitecustomize
        retarget = getattr(sitecustomize, "retarget", None)
        if retarget:
            retarget(case_log_dir(base, name))


def _isolated(case):
    """The case's error text, or None. A fresh one-process executor per case, so a crash breaks
    only this case's pool."""
    with ProcessPoolExecutor(max_workers=1, initializer=_case_reads, initargs=(case.__name__,)) as pool:
        try:
            return pool.submit(_run_one, case).result()
        except BrokenProcessPool as exc:
            return "case process died: %s" % exc


# Characters of node ids per pytest call: under the 32767-character Windows command line with room for the interpreter.
_ARGV_BUDGET = 24000


def _jobs(jobs):
    if jobs:
        return jobs
    try:
        value = int(os.environ.get("HARNESS_CASE_JOBS", ""))
    except ValueError:
        value = 0
    return value if value > 0 else 4


def _reused():
    """The case names whose recorded passes the harness runner reuses (`HARNESS_CASES_REUSED`, a JSON list)."""
    try:
        names = json.loads(os.environ.get("HARNESS_CASES_REUSED") or "[]")
    except ValueError:
        return set()
    return {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()


def _only(cases):
    """The cases `HARNESS_CASES_ONLY` names (comma-separated name parts), or None for every case. The harness runner
    sets `HARNESS_SLOT_DIR` for each proof it runs, and there the filter is ignored: its pass covers every case."""
    parts = [p.strip() for p in os.environ.get("HARNESS_CASES_ONLY", "").split(",") if p.strip()]
    if not parts or os.environ.get("HARNESS_SLOT_DIR"):
        return None
    return [case for case in cases if any(part in case.__name__ for part in parts)]


def run_cases(cases, jobs=None):
    """0 when every case passes, else 1. Prints `ok <case>` / `FAIL <case>: <error>` / `reused <case>` and a
    count. A case the runner reuses (`_reused`) does not run: its recorded pass still holds.
    `HARNESS_CASES_ONLY=<name part>[,<name part>]` runs the matching cases alone, for a fix loop on one case."""
    only = _only(cases)
    if only is not None:
        if not only:
            print("HARNESS_CASES_ONLY names no case of: " + ", ".join(case.__name__ for case in cases))
            return 1
        total, cases = len(cases), only
        print("partial: %d of %d cases (HARNESS_CASES_ONLY); not a pass of this proof" % (len(cases), total), flush=True)
    failures, reused = [], _reused()
    # The list names this proof's cases; a proof a case starts has its own.
    os.environ.pop("HARNESS_CASES_REUSED", None)
    running = [case for case in cases if case.__name__ not in reused]
    for case in cases:
        if case.__name__ in reused:
            print("reused " + case.__name__, flush=True)
    with _budget(_jobs(jobs)) as width, ThreadPoolExecutor(max_workers=width) as pool:
        for case, error in zip(running, pool.map(_isolated, running)):
            if error is None:
                print("ok " + case.__name__, flush=True)
            else:
                failures.append(case.__name__)
                print("FAIL %s: %s" % (case.__name__, error), flush=True)
    print("%d/%d cases pass%s" % (len(cases) - len(failures), len(cases),
                                  " (%d reused)" % (len(cases) - len(running)) if len(running) < len(cases) else ""))
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

    with _budget(_jobs(jobs)) as width, ThreadPoolExecutor(max_workers=width) as pool:
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
        # A deep checkout times many ids passes the Windows command-line limit (32767), so a shard runs in batches.
        batches, batch, size = [], [], 0
        for node in shard:
            if batch and size + len(node) + 3 > _ARGV_BUDGET:
                batches.append(batch)
                batch, size = [], 0
            batch.append(node)
            size += len(node) + 3
        batches.append(batch)
        code, text = 0, ""
        for batch in batches:
            proc = subprocess.run(base + ["-q", "-s"] + batch, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
            code = code or proc.returncode
            text += proc.stdout + proc.stderr
        return code, text

    code = 0
    with _budget(len(shards)) as width, ThreadPoolExecutor(max_workers=width) as pool:
        for rc, text in pool.map(run, shards):
            sys.stdout.write(text)
            code = code or rc
    print("%d cases in %d shards" % (len(ids), len(shards)))
    return code
