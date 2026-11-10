"""Proof that a gate's time is not spent on work that cannot change its verdict or its environment.

- A run's alone work (SERIAL proofs and alone retries) takes the machine once: each separate take waits for
  every running proof on the machine to end, and no run there starts a proof meanwhile.
- Assertion failures retry before timeouts, and once one fails alone the run cannot pass, so its timeout
  retries do not run. A close counts only a red that is not pre-existing at its base.
- A proof runs on the PATH the land runner gives it: the folders Git Bash prepends are not on it.
- A proof that checks every tracked row is bound to every file under `.claude/`.

    python3 .claude/tests/test_harness_tests_gate_time.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402

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

    def stub(proof, timeout=None, root=None):
        return outcomes[os.path.basename(proof)].pop(0)

    def take(exclusive):
        if exclusive:
            takes.append(exclusive)
        return real_take(exclusive)

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
                 case_a_close_judges_only_new_reds, case_a_proof_runs_on_the_land_path,
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
