"""Shared pytest setup for the harness tests: a child process never inherits the process's stale standard handles, and
every proof `scripts/harness_tests.py` discovers here runs under `python -m pytest`.

Under pytest's capture (and any runner with no console) Windows can hand a child an invalid standard handle: an
unnamed stdin fails with `[WinError 6]`, and an unnamed stdout or stderr intermittently fails with `[WinError 50]`
when `subprocess` duplicates the Win32 standard handle. Here, in one place, an unnamed stdin becomes DEVNULL (a test
that needs a payload passes `input=`), and an unnamed stdout or stderr becomes fd 1 or 2, the descriptors pytest's
capture already points at its own files, so the child's output still lands in the captured output.

The proof bridge. A pytest file in this folder (top-level `def test_` or `class Test`, no `__main__` block, and a
module body that only defines) is collected by pytest. Every other proof the runner discovers here (a script-style or
hybrid `test_*.py`, a pytest file with a script body, each `.js`, `.mjs`, `.sh` and `.ps1` proof) is one `proof` case
that runs the file the way `harness_tests.py` does
(`_proof_runner.attempt.run_proof`: its interpreter, isolated environment, temp root and timeout), so one
`pytest -n auto` run covers the battery. A pytest file collected in process gets the same environment and the same
cap per case (`_proof_environment`). adaptation.json's `proof_timeouts` caps a proof's seconds (default 120);
`proof_excluded` and `proof_heavy` skip with their reason unless `--harness-all` (the runner's `--all`);
`proof_quarantine` (file name: its cause) runs a seen-flaky proof as `xfail(strict=False)`, so it still runs and reports
and its failure is no red; a
`proof_serial` proof skips while xdist workers share the machine and runs under `-p no:xdist`; the pass that runs
them alone is `-p no:xdist --harness-serial`, which selects only those proofs. A proof whose interpreter is missing
fails as cannot-run (v1's runner: unavailable coverage is never green). A file named with a
`::case` selector, and every pytest run inside a proof (the runner sets `HARNESS_PROOF_TIMEOUT_S` there), collects
as plain pytest.

A `test_*.py` (or `*_test.py`) that pytest collects and that holds no test fails its collection, naming the file:
pytest otherwise passes it silently, so a proof would read green without running.
"""
import faulthandler
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

if os.name == "nt":
    _popen_init = subprocess.Popen.__init__

    def _popen_with_handles(self, *args, **kwargs):
        if len(args) < 4 and kwargs.get("stdin") is None and kwargs.get("input") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        if len(args) < 5 and kwargs.get("stdout") is None:
            kwargs["stdout"] = 1
        if len(args) < 6 and kwargs.get("stderr") is None:
            kwargs["stderr"] = 2
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_with_handles

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
_THIS = sys.modules[__name__]
DEFAULT_TIMEOUT_S = 120
_FAILURE_LINE = re.compile(r"^\s*(FAIL|not ok)\b|^\w*(Error|Exception)\b:")


def _runner_modules():
    """The runner's own modules, so the bridge discovers, classifies and runs a proof exactly as it does. The path
    entries go again after the import, so the bridge adds no folder to a test's import path; the modules it
    imported (`_proof_runner`, `adaptation`, `_git_run`, `_hook_state`, `_bounded_run`) stay loaded."""
    saved = list(sys.path)
    sys.path[:0] = [str(CLAUDE / "scripts"), str(CLAUDE / "tools"), str(CLAUDE / "hooks")]
    try:
        from _proof_runner import attempt, catalog
        import adaptation
    finally:
        sys.path[:] = saved
    return attempt, catalog, adaptation


_attempt, _catalog, _adaptation = _runner_modules()
# Fixture repos are copied while their template's git may still be writing: `git commit` detaches `maintenance run --auto`
# (an `objects/maintenance.lock`, then packs), and a `copytree` racing it raises `shutil.Error`. No test wants background
# git work, so every git a test spawns (also the floor's own, and `git init` templates) starts with it off. The entries
# join the caller's `GIT_CONFIG_COUNT` list; they never replace it.
_attempt.add_git_config(os.environ, [("gc.auto", "0"), ("maintenance.auto", "false")])
_POLICY = _catalog.Policy.read_worktree(str(CLAUDE))
_TIMEOUTS = _adaptation.get(str(CLAUDE), "proof_timeouts")
_QUARANTINE = _adaptation.get(str(CLAUDE), "proof_quarantine")


def _here(path):
    return Path(path).resolve().parent == HERE


def _names_a_case(config, path):
    for arg in config.invocation_params.args:
        head, sep, _ = str(arg).partition("::")
        if sep and (Path(config.invocation_params.dir) / head).resolve() == Path(path).resolve():
            return True
    return False


def _bridged(path, config):
    if not _here(path) or not _catalog.proof_name(path.name) or os.environ.get("HARNESS_PROOF_TIMEOUT_S"):
        return False
    if path.suffix == ".py":
        return not _attempt.pytest_native(str(path)) and not _names_a_case(config, path)
    return True


def _timeout(path):
    for name in (path.name, os.path.basename(_catalog.part_main(str(path)) or "")):
        seconds = _TIMEOUTS.get(name)
        if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0:
            return seconds
    return DEFAULT_TIMEOUT_S


def _excerpt(detail, tail=60):
    lines = (detail or "").splitlines()
    marked = [line for line in lines[:-tail] if _FAILURE_LINE.search(line)][:20]
    return "\n".join(marked + (["..."] if marked else []) + lines[-tail:])


def _hang(item, seconds):
    """The watchdog of a case that outran its cap. A hung test has no one to end it, so this names the case on the real
    stderr with every thread's stack and ends the process: under `-p no:xdist` the run, under xdist one worker, which the
    controller reports as crashed while running the case."""
    try:
        capture = item.config.pluginmanager.getplugin("capturemanager")
        if capture:
            capture.suspend_global_capture()
        sys.__stderr__.write("\nHANG: %s ran longer than %gs (its file's proof_timeouts cap, else %gs); ending the process\n"
                             % (item.nodeid, seconds, DEFAULT_TIMEOUT_S))
        sys.__stderr__.flush()
        faulthandler.dump_traceback(file=sys.__stderr__, all_threads=True)
    finally:
        os._exit(1)


@pytest.fixture(autouse=True)
def _proof_environment(request):
    """A pytest file ran under `run_proof`: a clean runner's environment, a temp root and home of its own, and its file's
    `proof_timeouts` cap. Collected in process it gets the same for each case: the environment `proof_env` builds is
    applied around the case and the caller's put back, and a watchdog ends a hang at the cap. Inside a proof the runner
    already did both, so nothing is applied twice."""
    if os.environ.get("HARNESS_PROOF_TIMEOUT_S"):
        yield
        return
    timeout = _timeout(Path(str(request.node.path)))
    temp_root = tempfile.mkdtemp(prefix="harness_proof_")
    caller = dict(os.environ)

    def become(env):
        for name in set(os.environ) - set(env):
            del os.environ[name]
        os.environ.update(env)

    env = _attempt.proof_env(temp_root, timeout, root=str(CLAUDE.parent), pytest_lane=True)
    # The machine's temp folder and system git config stay: pytest's tmp_path holds a case's own folders, and the floor's
    # delete rows judge the real temp. A module-scoped fixture builds its repositories before the first case, so a case
    # that saw another system config (Git for Windows sets `core.autocrlf=true`) would read them as dirty, which is how
    # the floor's clone rows failed. The session's identity, index, global config and home are what leak.
    for name in (*_attempt._TEMP_ENV, "GIT_CONFIG_NOSYSTEM"):
        if name in caller:
            env[name] = caller[name]
        else:
            env.pop(name, None)
    become(env)
    watchdog = threading.Timer(timeout, _hang, (request.node, timeout))
    watchdog.daemon = True
    watchdog.start()
    try:
        yield
    finally:
        watchdog.cancel()
        become(caller)
        _attempt._remove_temp_root(temp_root)


@pytest.fixture(scope="session")
def bridge():
    """This module. `import conftest` names whichever conftest pytest loaded last (the floor's one beside this folder's)."""
    return _THIS


class ProofFailed(Exception):
    pass


class ProofItem(pytest.Item):
    def runtest(self):
        status, _seconds, detail = _attempt.run_proof(str(self.path), timeout=_timeout(self.path),
                                                      root=str(CLAUDE.parent), pytest_lane=True)
        if status == "platform-skip":
            found = re.search(r"^(?:# )?PLATFORM-SKIP-PROOF (.*)$", detail, re.M)
            pytest.skip(found.group(1) if found else "platform skip")
        if status != "pass":
            raise ProofFailed(f"{self.path.name}: {status}\n{_excerpt(detail)}")

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ProofFailed):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        # The failure header names the file (every case is `proof`); a line number, since pytest's skip report
        # asserts one for every item a skip mark reaches.
        return self.path, 0, self.path.name


class ProofFile(pytest.File):
    def collect(self):
        yield ProofItem.from_parent(self, name="proof")


def pytest_addoption(parser):
    parser.addoption("--harness-all", action="store_true",
                     help="also run adaptation.json's proof_excluded and proof_heavy proofs (harness_tests.py --all)")
    parser.addoption("--harness-serial", action="store_true",
                     help="run only adaptation.json's proof_serial proofs, alone (-p no:xdist); none is a pass")


def pytest_pycollect_makemodule(module_path, parent):
    if _bridged(module_path, parent.config):
        return ProofFile.from_parent(parent, path=module_path)
    return None


def pytest_collect_file(file_path, parent):
    if file_path.suffix != ".py" and _bridged(file_path, parent.config):
        return ProofFile.from_parent(parent, path=file_path)
    return None


def _shared(config):
    """True inside an xdist worker that shares the machine with others."""
    return getattr(config, "workerinput", {}).get("workercount", 1) > 1


def pytest_configure(config):
    if config.getoption("harness_serial") and config.getoption("numprocesses", default=0):
        raise pytest.UsageError("--harness-serial requires -p no:xdist (or -n 0)")
    # `run_proof` gives a proof two case jobs (HARNESS_CASE_JOBS). Under `-n` each worker already holds a CPU, so those
    # jobs would oversubscribe the machine (b3 measured five proofs red only at 24 workers); a worker's proofs run one
    # case at a time. A value the caller set stays.
    if _shared(config):
        os.environ.setdefault("HARNESS_CASE_JOBS", "1")
    # After the opening pair every worker holds (xdist's floor, `_spread_opening_pairs`), `load` tops a worker up by this
    # many cases as it frees, so the next long proof goes to the first worker to finish. A chunk the caller set stays.
    if getattr(config.option, "maxschedchunk", 0) is None:
        config.option.maxschedchunk = 1


def _spread_opening_pairs(items, workers):
    """`items` (longest first) reordered so each worker's opening pair holds one long proof and one from the short end.
    xdist's `load` schedule sends every worker two consecutive items before any case runs (its floor, whatever
    `maxschedchunk` says) and a sent case never moves, so with the longest proofs sorted first the two longest both went
    to worker 0 and ran in series. Fewer than two items per worker are sent one each, and the sort already spreads them."""
    if len(items) < 2 * workers:
        return items
    heads, fillers = items[:workers], items[-workers:]
    pairs = [case for pair in zip(heads, reversed(fillers)) for case in pair]
    return pairs + items[workers:-workers]


def pytest_collection_modifyitems(config, items):
    every = config.getoption("harness_all", default=False)
    shared = _shared(config)
    if config.getoption("harness_serial", default=False):
        kept = [item for item in items if _here(item.path) and Path(str(item.path)).name in _POLICY.serial]
        config.hook.pytest_deselected(items=[item for item in items if item not in kept])
        items[:] = kept
    # Workers take cases in collection order: the proofs with a named bound are the long ones, so they start first, and
    # within a bound a bridged proof (a whole file) starts before a single pytest case.
    items.sort(key=lambda item: (-_timeout(Path(str(item.path))), not isinstance(item, ProofItem)) if _here(item.path)
               else (0, True))
    if shared:
        items[:] = _spread_opening_pairs(items, config.workerinput["workercount"])
    for item in items:
        path = Path(str(item.path))
        if not _here(path):
            continue
        name, reason = path.name, None
        if not every and name in _POLICY.excluded:
            reason = f"proof_excluded: {_POLICY.excluded[name]} (--harness-all runs it)"
        elif not every and name in _POLICY.heavy:
            reason = f"proof_heavy: {_POLICY.heavy[name]} (--harness-all runs it)"
        elif shared and name in _POLICY.serial:
            reason = f"proof_serial: {_POLICY.serial[name]}; it runs alone: python -m pytest -p no:xdist {name}"
        if reason:
            item.add_marker(pytest.mark.skip(reason=reason))
        elif name in _QUARANTINE:
            item.add_marker(pytest.mark.xfail(strict=False, reason=f"quarantined: {_QUARANTINE[name]}"))


def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("harness_serial", default=False) and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    report = (yield).get_result()
    path = getattr(collector, "path", None)
    if (isinstance(collector, pytest.Module) and path is not None and report.outcome == "passed" and not report.result
            and (path.name.startswith("test_") or path.name.endswith("_test.py"))):
        report.outcome = "failed"
        report.longrepr = ("%s collects no test: a pytest file defines a `def test_` or a `class Test`; a script-style "
                           "proof runs from its `if __name__ == \"__main__\":` block, which the proof bridge runs as one "
                           "case." % path.name)
