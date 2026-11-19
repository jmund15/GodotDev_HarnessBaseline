"""Proof that a gate's time is not spent on work that cannot change its verdict or its environment.

- A run's alone work (SERIAL proofs and alone retries) takes the machine once: each separate take waits for
  every running proof on the machine to end, and no run there starts a proof meanwhile.
- Assertion failures retry before timeouts, shortest first; once one fails alone an unranged close cannot
  pass, so no later retry runs. A ranged close (the main check's `--range-start` mode) runs every retry, and a
  proof that times out again alone is a red the FIRST RED bisect skips. A close counts only a red that is not
  pre-existing at its base, and reruns its reds at that base side by side.
- `--also` adds named proofs to a ranged close's selection and runs each once: no timeout or alone retry.
- A proof runs on the PATH the land runner gives it: the folders Git Bash prepends are not on it.
- A proof that checks every tracked row is bound to every file under `.claude/`.

    python3 .claude/tests/test_harness_tests_gate_time.py
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_tests  # noqa: E402
from _harness_repo_case import RepoCase  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def make_repo(durations):
    repo = tempfile.mkdtemp(prefix="harness_gate_time_")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    os.makedirs(os.path.join(repo, ".claude", "tests"))
    harness_tests.STAMP_PATH = os.path.join(repo, "stamp.json")
    with open(os.path.join(repo, "harness_proof_durations.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(durations, fh)
    return repo


def write_proof(repo, name):
    path = os.path.join(repo, ".claude", "tests", name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nsys.exit(0)\n")
    return path


def run_stubbed(repo, paths, outcomes, options=None, serial=()):
    """_run_selected over `paths` with run_proof stubbed: each proof pops its next outcome. Returns
    (statuses, details, launches as (proof name, exclusive), exclusive slot takes, printed text)."""
    launches, takes = [], []
    real_run, real_take, real_launch, real_serial = (harness_tests.run_proof, harness_tests._take_slots,
                                                    harness_tests._launch, harness_tests.SERIAL)
    saved = dict(os.environ)

    def stub(proof, timeout=None, root=None, **_):
        return outcomes[os.path.basename(proof)].pop(0)

    def take(exclusive, count=1):
        if exclusive:
            takes.append(exclusive)
        return real_take(exclusive, count)

    def launch(path, timeout=None, exclusive=False, *args, **kwargs):
        launches.append((os.path.basename(path), exclusive))
        return real_launch(path, timeout, exclusive, *args, **kwargs)

    harness_tests.run_proof, harness_tests._take_slots, harness_tests._launch = stub, take, launch
    harness_tests.SERIAL = {name: "" for name in serial}
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(repo, "slots")
    os.environ["HARNESS_MACHINE_SLOTS"] = "2"
    statuses, details, buf = {}, {}, io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            harness_tests._run_selected(paths, False, statuses, details, options or harness_tests.RunOptions())
    finally:
        harness_tests.run_proof, harness_tests._take_slots, harness_tests._launch = real_run, real_take, real_launch
        harness_tests.SERIAL = real_serial
        os.environ.clear()
        os.environ.update(saved)
    names = {os.path.basename(p): s for p, s in statuses.items()}
    return names, {os.path.basename(p): d for p, d in details.items()}, launches, takes, buf.getvalue()


def case_alone_work_takes_the_machine_once():
    durations = {"test_a.py": 1.0, "test_b.py": 1.0, "test_serial.py": 1.0}
    repo = make_repo(durations)
    paths = [write_proof(repo, name) for name in sorted(durations)]
    flaky = [("fail", 1.0, "AssertionError: load"), ("pass", 1.0, "")]
    statuses, _, launches, takes, _ = run_stubbed(repo, paths, {
        "test_a.py": list(flaky), "test_b.py": list(flaky), "test_serial.py": [("pass", 1.0, "")]},
        serial=("test_serial.py",))
    check("a run's SERIAL proofs and alone retries take the machine once",
          statuses == {"test_a.py": "pass", "test_b.py": "pass", "test_serial.py": "pass"} and len(takes) == 1,
          "statuses=%s exclusive takes=%d" % (statuses, len(takes)))


def case_a_red_alone_skips_the_timeout_retries():
    durations = {"test_aslow.py": 1.0, "test_red.py": 1.0, "test_flaky.py": 1.0}
    repo = make_repo(durations)
    paths = [write_proof(repo, name) for name in sorted(durations)]
    timeout = ("fail", 3.0, "TIMEOUT after 3s")
    red = ("fail", 1.0, "FAIL: the claim\nAssertionError: real")
    outcomes = {"test_aslow.py": [timeout, ("pass", 1.0, "")],
                "test_red.py": [red, red],
                "test_flaky.py": [("fail", 1.0, "AssertionError: race"), ("pass", 1.0, "")]}
    statuses, details, launches, _, out = run_stubbed(repo, paths, outcomes)
    retries = [name for name, exclusive in launches if exclusive]
    check("assertion failures retry alone before timeouts",
          retries[:2] == ["test_flaky.py", "test_red.py"], "retries=%s" % retries)
    check("a proof that failed alone leaves the timeout retries unrun, reported skipped with the red named",
          statuses == {"test_aslow.py": "skipped", "test_red.py": "fail", "test_flaky.py": "pass"}
          and "test_red.py" in details.get("test_aslow.py", "") and "test_aslow.py" not in retries
          and "harness_tests: 1 timeout retry not run: the run is already red (test_red.py failed alone)" in out,
          "statuses=%s retries=%s detail=%r" % (statuses, retries, details.get("test_aslow.py")))

    # A fresh repo: the first run dropped test_red.py's recorded pass, and a cold assertion failure is not retried.
    repo = make_repo(durations)
    paths = [write_proof(repo, name) for name in sorted(durations)]
    outcomes = {"test_aslow.py": [timeout, ("pass", 1.0, "")], "test_red.py": [red, red],
                "test_flaky.py": [("pass", 1.0, "")]}
    seen = []

    def judge(reds):
        seen.append(sorted(os.path.basename(p) for p in reds))
        return False
    statuses, _, launches, _, _ = run_stubbed(repo, paths, outcomes, harness_tests.RunOptions(red=judge))
    check("a red the run's judge excuses (pre-existing) leaves the timeout retries running",
          statuses == {"test_aslow.py": "pass", "test_red.py": "fail", "test_flaky.py": "pass"}
          and seen == [["test_red.py"]], "statuses=%s judge saw %s" % (statuses, seen))


def case_a_close_judges_only_new_reds():
    reds = {"/x/test_old.py": "FAIL: same\n", "/x/test_new.py": "FAIL: mine\n"}
    calls, memo = [], {}
    real = harness_tests._reds_at

    def at_base(root, rev, failing):
        calls.append(sorted(os.path.basename(p) for p in failing))
        return {"/x/test_old.py": "FAIL: same\n"}
    harness_tests._reds_at = at_base
    try:
        new = harness_tests._new_reds("/x", "base", reds, memo)
        again = harness_tests._new_reds("/x", "base", {"/x/test_old.py": "FAIL: same\n"}, memo)
    finally:
        harness_tests._reds_at = real
    check("a close's judge counts a red pre-existing at its base as no reason to stop, and reruns base once per proof",
          new == ["/x/test_new.py"] and again == [] and calls == [["test_new.py", "test_old.py"]],
          "new=%s again=%s calls=%s" % (new, again, calls))


def case_a_red_alone_skips_the_other_assertion_retries():
    durations = {"test_along.py": 9.0, "test_zshort.py": 1.0}
    repo = make_repo(durations)
    paths = [write_proof(repo, name) for name in sorted(durations)]
    red = ("fail", 1.0, "FAIL: the claim\nAssertionError: real")
    statuses, details, launches, _, out = run_stubbed(
        repo, paths, {"test_along.py": [red, red], "test_zshort.py": [red, red]})
    retries = [name for name, exclusive in launches if exclusive]
    check("the shortest assertion failure retries alone first, and once it fails alone the rest stay red unretried",
          retries == ["test_zshort.py"] and statuses == {"test_along.py": "fail", "test_zshort.py": "fail"}
          and "not retried alone" in details.get("test_along.py", "") and "test_zshort.py" in details.get("test_along.py", "")
          and "1 assertion retry not run: the run is already red (test_zshort.py failed alone)" in out,
          "retries=%s statuses=%s detail=%r" % (retries, statuses, details.get("test_along.py")))


def case_reds_rerun_at_base_side_by_side():
    import contextlib as _cl
    import threading
    import time as _time
    root = tempfile.mkdtemp(prefix="harness_gate_time_base_")
    failing = []
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        path = os.path.join(root, ".claude", "tests", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        failing.append(path)
    live, peak, lock = [0], [0], threading.Lock()

    @_cl.contextmanager
    def checkout(repo_root, rev, proofs=()):
        yield root

    def launch(path, timeout=None, exclusive=False, root=None, *args, **kwargs):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        _time.sleep(0.3)
        with lock:
            live[0] -= 1
        return ("fail", 0.3, "FAIL: " + os.path.basename(path))
    real = harness_tests._revision_checkout, harness_tests._launch
    saved = dict(os.environ)
    os.environ["HARNESS_SLOT_DIR"] = os.path.join(root, "slots")
    os.environ["HARNESS_MACHINE_SLOTS"] = "3"
    os.environ["HARNESS_TEST_JOBS"] = "3"
    os.environ.pop("HARNESS_SLOT_BUDGET", None)  # a runner inside an inherited lane runs serially by design
    harness_tests._revision_checkout, harness_tests._launch = checkout, launch
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            reds = harness_tests._reds_at(root, "base", failing)
    finally:
        harness_tests._revision_checkout, harness_tests._launch = real
        os.environ.clear()
        os.environ.update(saved)
    check("a red's rerun at base runs beside the others, each start printed",
          peak[0] > 1 and sorted(os.path.basename(p) for p in reds) == ["test_a.py", "test_b.py", "test_c.py"]
          and buf.getvalue().count("classifying red") == 3,
          "peak=%d reds=%s out=%r" % (peak[0], sorted(reds), buf.getvalue()))


class _CloseRepo(RepoCase):
    """A planted repo whose range sets `.claude/hooks/unit.py` to `bad`, so a proof reading it fails at HEAD
    and passes at the range start."""

    def tmp_path(self, name):
        return os.path.join(self.tmp.name, name).replace("\\", "/")


READ_UNIT = ("from pathlib import Path\n"
             "unit = (Path(__file__).resolve().parents[1] / 'hooks/unit.py').read_text()\n")


def close_repo(proofs, durations):
    """(case, range start) with `proofs` ({file name: source}) committed at the range start and `unit.py` set
    to `bad` by the range's one commit. Each proof's source may hold `%(log)r` and `%(marker)r`, its start log
    and its once-marker, under the case's temp root."""
    case = _CloseRepo("runTest")
    case.setUp()
    case.write(".claude/hooks/unit.py", "VALUE = 'good'\n")
    for name, source in proofs.items():
        case.write(".claude/tests/" + name,
                   source % {"log": case.tmp_path(name), "marker": case.tmp_path(name + ".marker")})
    durations_path = case.tmp_path("proof_durations.json")
    with open(durations_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(durations, fh)
    patch.object(harness_tests, "durations_path", lambda: durations_path).start()
    patch.object(harness_tests, "_TIMEOUT_SEC", 1).start()
    case.base = case.commit("plant the proofs")
    case.git("update-ref", "refs/remotes/origin/main", case.base)
    case.write(".claude/hooks/unit.py", "VALUE = 'bad'\n")
    case.commit("break the unit")
    return case, case.base


def close_once(case, base=None, **options):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = harness_tests._close_once(str(case.repo), base=base, **options)
    return code, out.getvalue()


def close_record(case):
    return json.loads(Path(case.stamp).read_text(encoding="utf-8"))["close"]


def starts(case, name):
    try:
        return len(Path(case.tmp_path(name)).read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def check_close(label, ok, case, out, detail=""):
    lines = [line for line in out.splitlines() if "harness_tests:" in line or line.startswith(("FAIL", "FIRST RED"))]
    check(label, ok, "%s starts: %r; close lines: %s" % (detail, {
        name: starts(case, name) for name in ("test_red.py", "test_red2.py", "test_slow.py",
                                              "test_hang.py", "test_also_hang.py")}, lines[-6:]))


RED = READ_UNIT + ("import sys\n"
                   "with open(%(log)r, 'a') as fh:\n"
                   "    fh.write('x\\n')\n"
                   "if 'bad' in unit:\n"
                   "    print('FAIL: unit regressed')\n"
                   "    sys.exit(1)\n")
RED2 = READ_UNIT + ("import sys\n"
                    "with open(%(log)r, 'a') as fh:\n"
                    "    fh.write('x\\n')\n"
                    "if 'bad' in unit:\n"
                    "    print('FAIL: unit regressed again')\n"
                    "    sys.exit(1)\n")
FLAKY = READ_UNIT + ("import os, sys\n"
                     "with open(%(log)r, 'a') as fh:\n"
                     "    fh.write('x\\n')\n"
                     "if not os.path.exists(%(marker)r):\n"
                     "    open(%(marker)r, 'w').close()\n"
                     "    print('FAIL: shared temp race')\n"
                     "    sys.exit(1)\n")
SLOW_ONCE = READ_UNIT + ("import os, sys, time\n"
                         "with open(%(log)r, 'a') as fh:\n"
                         "    fh.write('x\\n')\n"
                         "if not os.path.exists(%(marker)r):\n"
                         "    open(%(marker)r, 'w').close()\n"
                         "    time.sleep(5)\n")
HANG = READ_UNIT + ("import time\n"
                    "with open(%(log)r, 'a') as fh:\n"
                    "    fh.write('x\\n')\n"
                    "time.sleep(30)\n")


def case_a_ranged_close_runs_the_timeout_retry_after_a_red_fails_alone():
    case, base = close_repo({"test_red.py": RED, "test_slow.py": SLOW_ONCE},
                            {"test_red.py": 1.0, "test_slow.py": 1.0})
    try:
        code, out = close_once(case, base, ranged=True)
        check_close("a ranged close runs the timeout retry after a red fails alone",
                    starts(case, "test_slow.py") == 2 and "not run: the run is already red" not in out
                    and [r["proof"] for r in close_record(case)["red"]] == ["test_red.py"], case, out)
    finally:
        case.doCleanups()

    case, base = close_repo({"test_red.py": RED, "test_slow.py": SLOW_ONCE},
                            {"test_red.py": 1.0, "test_slow.py": 1.0})
    try:
        code, out = close_once(case, ranged=False)
        check_close("an unranged close still skips the timeout retry after a red fails alone",
                    starts(case, "test_slow.py") == 1
                    and "1 timeout retry not run: the run is already red (test_red.py failed alone)" in out,
                    case, out)
    finally:
        case.doCleanups()


def case_a_timeout_red_skips_the_first_red_bisect():
    case, base = close_repo({"test_red.py": RED, "test_hang.py": HANG},
                            {"test_red.py": 1.0, "test_hang.py": 1.0})
    try:
        code, out = close_once(case, base, ranged=True)
        reds = {r["proof"]: r for r in close_record(case)["red"]}
        check_close("a proof that times out again alone is a red the FIRST RED bisect skips",
                    reds.get("test_hang.py", {}).get("commit", "missing") is None
                    and reds.get("test_hang.py", {}).get("cause") == "timeout"
                    and starts(case, "test_hang.py") == 2, case, "")
    finally:
        case.doCleanups()


# The traceback Python 3.14 prints when a proof's own subprocess call times out, captured from a live close's red
# detail and trimmed to its frames.
OWN_SUBPROCESS_TIMEOUT = (
    "Traceback (most recent call last):\n"
    "  File \"C:\\...\\tests\\test_x.py\", line 188, in run_x\n"
    "    subprocess.run(cmd, check=True, timeout=600)\n"
    "  File \"C:\\...\\Lib\\subprocess.py\", line 1607, in _wait\n"
    "    raise TimeoutExpired(self.args, timeout)\n"
    "subprocess.TimeoutExpired: Command '['C:\\\\...\\\\python.exe', '-I', '...']' timed out after 600 seconds\n")


def case_a_proof_whose_own_subprocess_timed_out_is_a_timeout():
    details = [OWN_SUBPROCESS_TIMEOUT, "TIMEOUT after 900s", "FAIL: unit regressed\n",
               "Traceback (most recent call last):\nsubprocess.TimeoutExpired raised, then caught\nAssertionError: x\n"]
    check("a red whose detail is the runner's TIMEOUT or ends in subprocess.TimeoutExpired is a timeout; others are not",
          [harness_tests._timeout_detail(d) for d in details] == [True, True, False, False], details)


def case_a_ranged_close_runs_every_alone_retry():
    case, base = close_repo({"test_flaky.py": FLAKY, "test_red.py": RED, "test_red2.py": RED2},
                            {"test_flaky.py": 1.0, "test_red.py": 1.0, "test_red2.py": 1.0})
    try:
        code, out = close_once(case, base, ranged=True)
        reds = {r["proof"]: r for r in close_record(case)["red"]}
        check_close("a ranged close runs the second alone retry a red alone would have skipped",
                    sorted(reds) == ["test_red.py", "test_red2.py"]
                    and not any("not retried alone" in (r.get("detail") or "") for r in reds.values())
                    and "alone retries 3," in out, case, out)
    finally:
        case.doCleanups()


def case_also_runs_a_named_proof_once_in_the_pool():
    also_hang = ("import time\n"
                 "with open(%(log)r, 'a') as fh:\n"
                 "    fh.write('x\\n')\n"
                 "time.sleep(30)\n")
    case, base = close_repo({"test_red.py": RED, "test_also_hang.py": also_hang},
                            {"test_red.py": 1.0, "test_also_hang.py": 1.0})
    try:
        code, out = close_once(case, base, ranged=True, also=[".claude/tests/test_also_hang.py"])
        record = close_record(case)
        check_close("--also adds its proof to the close's selection and runs it once, with no retry",
                    "test_also_hang.py" in record["proofs"] and starts(case, "test_also_hang.py") == 1,
                    case, out)
    finally:
        case.doCleanups()


def case_a_proof_runs_on_the_land_path():
    if os.name != "nt":
        print("ok   (the Git Bash PATH is a Windows condition; nothing to strip here)")
        return
    root = tempfile.mkdtemp(prefix="harness_gate_git_")
    shell_dirs = [os.path.join(root, *parts) for parts in (("usr", "bin"), ("mingw64", "bin"), ("usr", "local", "bin"))]
    kept = os.path.join(root, "cmd")
    home_bin = os.path.join(os.path.expanduser("~"), "bin")
    for folder in shell_dirs + [kept, os.path.join(root, "bin")]:
        os.makedirs(folder, exist_ok=True)
    probe = os.path.join(root, "test_path_probe.py")
    with open(probe, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nsys.stdout.write(os.environ['PATH'])\nsys.exit(1)\n")
    real_bash, saved = harness_tests._bash_exe, os.environ.get("PATH", "")
    harness_tests._bash_exe = lambda: os.path.join(root, "bin", "bash.exe")
    os.environ["PATH"] = os.pathsep.join(shell_dirs + [home_bin, kept, saved])
    try:
        _, _, detail = harness_tests.run_proof(probe, 30)
    finally:
        harness_tests._bash_exe = real_bash
        os.environ["PATH"] = saved
    seen = [os.path.normcase(p) for p in detail.strip().split(os.pathsep)]
    check("a proof's PATH drops the folders Git Bash prepends and keeps the rest",
          not any(os.path.normcase(d) in seen for d in shell_dirs + [home_bin]) and os.path.normcase(kept) in seen,
          "proof PATH head=%s" % seen[:5])


def case_a_whole_tree_closure_proof_binds_every_claude_file():
    proof = os.path.join(harness_tests.REPO_ROOT, ".claude", "tests", "test_layer_closure.py")
    bound = harness_tests.bindings(harness_tests.REPO_ROOT, [".claude/tests/fixtures/zz_new_fixture.txt"], proofs=[proof])
    check("test_layer_closure.py is bound to a new file under .claude/, since its live case checks every tracked row",
          bound.get(".claude/tests/fixtures/zz_new_fixture.txt") == [proof], bound)


def main():
    for case in (case_alone_work_takes_the_machine_once, case_a_red_alone_skips_the_timeout_retries,
                 case_a_close_judges_only_new_reds, case_a_red_alone_skips_the_other_assertion_retries,
                 case_reds_rerun_at_base_side_by_side, case_a_ranged_close_runs_the_timeout_retry_after_a_red_fails_alone,
                 case_a_timeout_red_skips_the_first_red_bisect, case_a_proof_whose_own_subprocess_timed_out_is_a_timeout,
                 case_a_ranged_close_runs_every_alone_retry,
                 case_also_runs_a_named_proof_once_in_the_pool, case_a_proof_runs_on_the_land_path,
                 case_a_whole_tree_closure_proof_binds_every_claude_file):
        try:
            case()
        except Exception as exc:  # noqa: BLE001 - a crashing case is a failed case
            check(case.__name__, False, "%s: %s" % (type(exc).__name__, exc))
    if FAILURES:
        print("\n%d failure(s)" % len(FAILURES))
        for failure in FAILURES:
            print("  FAIL " + failure)
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
