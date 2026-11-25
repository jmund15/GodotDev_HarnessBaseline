#!/usr/bin/env python3
"""Proof that harness_tests.py runs each proof from the checkout it is proving.

Proofs resolve `.claude/...` paths against their working directory. A runner started from another
directory (the baseline publish starts its battery one level above `template/`) used to hand each
proof that directory, so every cwd-relative proof went red there and green everywhere else.

A `--repo <checkout>` run proves that checkout, so its proofs run there too: both entry points that
take `--repo` without a stamp (`--all` and `--proofs-for`) bind the proof's cwd and its
`CLAUDE_PROJECT_DIR` to it.

Run: python3 .claude/tests/test_harness_tests_proof_cwd.py
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "scripts", "harness_tests.py")
spec = importlib.util.spec_from_file_location("ht", MOD)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)

FAILS = []

# The proof passes only in the checkout that holds it: its cwd and CLAUDE_PROJECT_DIR are that checkout.
PROBE = """import os, sys
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.normcase(os.path.normpath(os.path.dirname(os.path.dirname(here))))
cwd = os.path.normcase(os.path.normpath(os.getcwd()))
project = os.path.normcase(os.path.normpath(os.environ.get("CLAUDE_PROJECT_DIR", "")))
if not os.path.isfile(os.path.join(here, "fixtures", "root_sentinel.txt")) or cwd != root or project != root:
    print("WRONG CHECKOUT cwd=%s root=%s project=%s" % (cwd, root, project))
    sys.exit(1)
"""


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILS.append("%s: %s" % (label, detail))


def make_repo():
    """A checkout with one proof that must run in it, and a stamp/durations store outside the real repo."""
    repo = tempfile.mkdtemp(prefix="ht_cwd_repo_")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    tests = os.path.join(repo, ".claude", "tests")
    os.makedirs(os.path.join(tests, "fixtures"))
    with open(os.path.join(tests, "fixtures", "root_sentinel.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("here\n")
    proof = os.path.join(tests, "test_root_probe.py")
    with open(proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(PROBE)
    return repo, proof


def store_for(repo):
    return os.path.join(repo, "proof_durations.json")


def in_process(entry, repo, proof):
    """Run one entry point in process with its store and stamp redirected into `repo`."""
    saved = (ht.STAMP_PATH, ht.DURATIONS_PATH, os.environ.get("HARNESS_NO_PASS_CACHE"),
             os.environ.get("HARNESS_DEADLINE"), os.environ.get("HARNESS_SLOT_BUDGET"))
    ht.STAMP_PATH, ht.DURATIONS_PATH = os.path.join(repo, "stamp.json"), store_for(repo)
    os.environ["HARNESS_NO_PASS_CACHE"] = "1"
    os.environ.pop("HARNESS_DEADLINE", None)
    os.environ.pop("HARNESS_SLOT_BUDGET", None)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            if entry == "all":
                rc = ht.run_all(repo)
            else:
                rc = ht.main(["--proofs-for", ".claude/tests/test_root_probe.py", "--repo", repo])
    finally:
        ht.STAMP_PATH, ht.DURATIONS_PATH = saved[0], saved[1]
        for name, value in (("HARNESS_NO_PASS_CACHE", saved[2]), ("HARNESS_DEADLINE", saved[3]),
                            ("HARNESS_SLOT_BUDGET", saved[4])):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return rc, out.getvalue()


def spawned(entry, repo):
    argv = [sys.executable, MOD]
    argv += ["--all"] if entry == "all" else ["--proofs-for", ".claude/tests/test_root_probe.py"]
    argv += ["--repo", repo]
    env = dict(os.environ, HARNESS_NO_PASS_CACHE="1", STAMP_PATH=os.path.join(repo, "stamp.json"),
               DURATIONS_PATH=store_for(repo))
    env.pop("HARNESS_DEADLINE", None)
    env.pop("HARNESS_SLOT_BUDGET", None)
    done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=120)
    return done.returncode, done.stdout + done.stderr


def case_a_runner_from_another_directory_uses_its_own_checkout():
    tmp = tempfile.mkdtemp(prefix="ht_cwd_")
    proof = os.path.join(tmp, "test_cwd_probe.py")
    expected = os.path.normcase(os.path.abspath(ht.REPO_ROOT))
    with open(proof, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import os, sys\nsys.exit(0 if os.path.normcase(os.getcwd()) == %r else 1)\n" % expected)
    elsewhere = tempfile.mkdtemp(prefix="ht_cwd_elsewhere_")
    start = os.getcwd()
    os.chdir(elsewhere)
    try:
        status, _seconds, detail = ht.run_proof(proof)
    finally:
        os.chdir(start)
    check("a proof started from another directory runs from the runner's checkout root", status == "pass", detail)


def case_a_proof_for_another_checkout_runs_in_that_checkout():
    for entry in ("all", "proofs-for"):
        repo, _proof = make_repo()
        rc, out = in_process(entry, repo, _proof)
        check("--repo runs the proof in that checkout (%s, in process)" % entry, rc == 0, out[-600:])
    for entry in ("all", "proofs-for"):
        repo, _proof = make_repo()
        rc, out = spawned(entry, repo)
        check("--repo runs the proof in that checkout (%s, spawned)" % entry, rc == 0, out[-600:])


def main():
    case_a_runner_from_another_directory_uses_its_own_checkout()
    case_a_proof_for_another_checkout_runs_in_that_checkout()
    if FAILS:
        print("\n%d failure(s)" % len(FAILS))
        for failure in FAILS:
            print("  FAIL " + failure)
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
