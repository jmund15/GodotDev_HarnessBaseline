#!/usr/bin/env python3
"""Proof for harness_tests.py's exit-code semantics.

Exit 2 means CANNOT RUN — the proof could not bind its target. Counting it as red let one peer's
in-flight refactor block every harness commit on the machine, so the runner reported a defect
nobody had introduced. But an indeterminate must never become a silent pass either: the cases below
plant all three exit codes and assert both halves.

Run: python3 .claude/tests/test_harness_tests_cannot_run.py
"""
import importlib.util
import os
import stat
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "scripts", "harness_tests.py")
spec = importlib.util.spec_from_file_location("ht", MOD)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)

TMP = tempfile.mkdtemp(prefix="ht_exit_")


def proof(name, code, message="planted"):
    """A throwaway python proof that prints `message` and exits `code`."""
    p = os.path.join(TMP, "test_%s.py" % name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\nprint(%r)\nsys.exit(%d)\n" % (message, code))
    os.chmod(p, os.stat(p).st_mode | stat.S_IRUSR)
    return p


GREEN = ht.run_proof(proof("green", 0))
CANNOT = ht.run_proof(proof("cannot", 2, "CANNOT RUN: target not found"))
RED = ht.run_proof(proof("red", 1, "assertion failed"))
CRASH = ht.run_proof(proof("crash", 3, "unexpected"))


def _unlaunchable():
    """A proof whose interpreter is not on the shell's PATH never ran, so it asserted nothing."""
    real = ht._runner_cmds
    ht._runner_cmds = lambda path: [[os.path.join(TMP, "no-such-interpreter.exe"), path]]
    try:
        return ht.run_proof(proof("unlaunchable", 0))
    finally:
        ht._runner_cmds = real


UNLAUNCHABLE = _unlaunchable()


def pytest_proof(name, body):
    """A throwaway pytest-style proof: test functions and no `__main__` block, so `python <file>` runs none."""
    p = os.path.join(TMP, "test_%s.py" % name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return p


PYTEST_RED = ht.run_proof(pytest_proof("pytest_red", "def test_planted():\n    assert 1 == 2, 'planted pytest red'\n"))
PYTEST_GREEN = ht.run_proof(pytest_proof("pytest_green", "def test_planted():\n    assert 1 == 1\n"))
PYTEST_BROKEN = ht.run_proof(pytest_proof("pytest_broken", "import no_such_module_planted\n\ndef test_planted():\n    pass\n"))

CASES = [
    ("exit 0 is a pass", lambda: GREEN[0] == "pass"),

    # The whole point: a peer refactoring the target is not this commit's regression.
    ("exit 2 is cannot-run, NOT a failure", lambda: CANNOT[0] == "cannot-run"),

    # ...and the other half: it is not a pass either, or the runner would launder it into green.
    ("...and it is not a pass either", lambda: CANNOT[0] != "pass"),

    ("an indeterminate still carries its output, so the reason is visible",
     lambda: "CANNOT RUN: target not found" in CANNOT[2]),

    ("exit 1 is a failure", lambda: RED[0] == "fail"),

    # Anything outside {0, 1, 2} is a crash, and a crash is red — never quietly indeterminate
    # (`rules/harness_tooling.md`: a proof classifies an exit code outside {0,2} as CRASH).
    ("an unexpected exit code is a failure, not an indeterminate",
     lambda: CRASH[0] == "fail"),

    ("a failure carries its output", lambda: "assertion failed" in RED[2]),

    ("a green proof carries no detail", lambda: GREEN[2] == ""),

    # A shell without the interpreter turned 221 proofs into 0.0s reds that an executor then chased.
    ("an interpreter that cannot launch is cannot-run, not a failure",
     lambda: UNLAUNCHABLE[0] == "cannot-run"),

    ("...and its reason names the launch failure", lambda: "runner unavailable" in UNLAUNCHABLE[2]),

    # `python <file>` on a pytest-style proof defines its tests and exits 0: ten proof files passed
    # for weeks without running a case, one of them red (2026-10-14).
    ("a pytest-style proof with a failing test is a failure", lambda: PYTEST_RED[0] == "fail"),
    ("...and its output names the failing assertion", lambda: "planted pytest red" in PYTEST_RED[2]),
    ("a pytest-style proof whose tests pass is a pass", lambda: PYTEST_GREEN[0] == "pass"),
    ("a pytest-style proof that cannot import is a failure, not cannot-run", lambda: PYTEST_BROKEN[0] == "fail"),

    # The stamp must not claim coverage it did not have.
    ("_write_stamp records the cannot-run list", lambda: _stamp_records()),
]


def _stamp_records():
    import json
    dest = os.path.join(TMP, "stamp.json")
    prev = ht.STAMP_PATH
    ht.STAMP_PATH = dest
    try:
        ht._write_stamp(ht.REPO_ROOT, 3, 2, [], [os.path.join(TMP, "test_cannot.py")])
    finally:
        ht.STAMP_PATH = prev
    with open(dest, encoding="utf-8") as fh:
        return json.load(fh).get("cannotRun") == ["test_cannot.py"]


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
