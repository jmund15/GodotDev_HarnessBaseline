"""Proof: a case-pool proof reruns only the cases whose own reads changed.

`tests/_case_pool.run_cases` gives each case its own read log; a passing run records one pass row per case,
keyed on what that case and the proof's module-level code read. The next run of the proof reuses every case whose
row still holds and runs the rest. A file read at module level is in every case's key, so its change reruns the
whole proof. A partial run's proof-level pass keeps the reused cases' reads, so a later change only a reused case
reads still reruns that case. A failed case is never reused, and a partial run records no duration.

    python3 .claude/tests/test_harness_tests_case_reach.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

os.environ.pop("HARNESS_REUSE_PASSES", None)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import harness_tests  # noqa: E402

FAILS = []

PROOF = '''import json, os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
sys.path.insert(0, HERE)
from _case_pool import run_cases
MODULE = open(os.path.join(DATA, "m.json")).read()


def _mark(name):
    # One file per case: two cases appending to one file at once overwrite each other's line on Windows.
    with open(os.path.join(os.environ["CASE_MARKS"], name), "a") as fh:
        fh.write(name + "\\n")


def case_a():
    _mark("a")
    time.sleep(1.5)
    json.loads(open(os.path.join(DATA, "f.json")).read())


def case_b():
    _mark("b")
    data = json.loads(open(os.path.join(DATA, "g.json")).read())
    assert not data.get("fail"), "planted failure"


if __name__ == "__main__":
    sys.exit(run_cases([case_a, case_b]))
'''


def check(name, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def write(repo, rel, text):
    path = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True)


def main():
    parent = tempfile.mkdtemp(prefix="harnesscasereach_")
    repo = os.path.join(parent, "repo")
    marks = os.path.join(parent, "marks")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    write(repo, ".claude/settings.json", "{}\n")
    for name in ("f", "g", "m"):
        write(repo, ".claude/data/%s.json" % name, "{}\n")
    proof = write(repo, ".claude/tests/test_two.py", PROOF)
    # The case pool and the machine-slot admission it takes its width from (tools/machine_slots.py and its hooks).
    for rel in ("tests/_case_pool.py", "tools/machine_slots.py", "hooks/_file_lock.py", "hooks/_hook_state.py"):
        os.makedirs(os.path.join(repo, ".claude", os.path.dirname(rel)), exist_ok=True)
        shutil.copyfile(os.path.join(HERE, "..", rel), os.path.join(repo, ".claude", rel))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    harness_tests.EXCLUDED, harness_tests.SERIAL = {}, {}
    harness_tests.STAMP_PATH = os.path.join(parent, "stamp.json")

    def run():
        """Run the proof through the runner's pool and pass cache; (status, cases that ran)."""
        shutil.rmtree(marks, ignore_errors=True)
        os.makedirs(marks)
        saved = dict(os.environ)
        os.environ.update(CASE_MARKS=marks, HARNESS_SLOT_DIR=os.path.join(parent, "slots"))
        statuses = {}
        try:
            with redirect_stdout(io.StringIO()):
                harness_tests._run_selected([proof], False, statuses, None,
                                            harness_tests.RunOptions(harness_tests._pass_cache(repo, [proof])))
        finally:
            os.environ.clear()
            os.environ.update(saved)
        ran = sorted(w for name in os.listdir(marks) for w in open(os.path.join(marks, name)).read().split())
        return statuses.get(proof), ran

    def duration():
        with open(harness_tests.durations_path(), encoding="utf-8") as fh:
            return json.load(fh).get("test_two.py")

    try:
        first = run()
        check("a first run runs every case", first == ("pass", ["a", "b"]), first)
        write(repo, ".claude/data/f.json", '{"x": 1}\n')
        got = run()
        check("a change to a file case a reads reruns case a", "a" in got[1] and got[0] == "pass", got)
        write(repo, ".claude/data/m.json", '{"x": 1}\n')
        got = run()
        check("a change to a file the proof reads at module level reruns every case", got == ("pass", ["a", "b"]), got)
        whole = duration()
        write(repo, ".claude/data/f.json", '{"x": 2}\n')
        got = run()
        check("a change only case a reads runs case a alone", got == ("pass", ["a"]), got)
        check("a partial run records no duration", duration() == whole, "before=%s after=%s" % (whole, duration()))
        write(repo, ".claude/data/g.json", '{"x": 2}\n')
        got = run()
        check("after a partial run, a change only the reused case reads reruns that case", got == ("pass", ["b"]), got)
        got = run()
        check("an unchanged proof is reused whole", got == ("pass", []), got)
        write(repo, ".claude/data/g.json", '{"fail": true}\n')
        # A red proof with a recorded pass is retried alone, so its cases may run twice: compare which ran.
        got = run()
        check("a failing case fails the proof", (got[0], sorted(set(got[1]))) == ("fail", ["b"]), got)
        got = run()
        check("a failed case is never reused; the passing case still is",
              (got[0], sorted(set(got[1]))) == ("fail", ["b"]), got)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    print("%d failure(s)" % len(FAILS) if FAILS else "all cases pass")
    for line in FAILS:
        print("  " + line)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
