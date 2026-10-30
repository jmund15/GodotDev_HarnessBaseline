"""Proof for harness_tests.sweep_stale_proof_temp: a proof run outside the runner (RED-first work runs the
file directly) leaves its mkdtemp folders in the machine's TEMP; the close sweeps the stale ones.

Only top-level folders older than the age limit whose name starts with a prefix a tracked proof passes to
mkdtemp (4+ characters), or the runner's own roots, are removed. A fresh folder, a file, an unrelated
folder and a too-short prefix's folder stay.
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import harness_tests  # noqa: E402


def main():
    fails = 0

    def ck(name, cond, detail=""):
        nonlocal fails
        print(("ok   " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
        fails += not cond

    root = tempfile.mkdtemp(prefix="sweep_proof_")
    repo = os.path.join(root, "repo")
    tests = os.path.join(repo, ".claude", "tests")
    os.makedirs(tests)
    with open(os.path.join(tests, "test_fixture.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write('a = tempfile.mkdtemp(prefix="wpg_state_")\nb = tempfile.mkdtemp(prefix="us_")\n'
                 "c = tempfile.mkdtemp(dir=d, prefix='evaldash_cwd_')\ne = tempfile.mkdtemp('_suffixonly')\n")
    temp = os.path.join(root, "TEMP")
    os.makedirs(temp)
    old = time.time() - 3 * 86400
    names = {"wpg_state_old1": old, "evaldash_cwd_old2": old, "harness_proof_old3": old,
             "wpg_state_fresh": None, "unrelated_old": old, "us_old": old, "_suffixonly_old": old}
    for name, mtime in names.items():
        os.makedirs(os.path.join(temp, name, "inner"))
        if mtime:
            os.utime(os.path.join(temp, name), (mtime, mtime))
    with open(os.path.join(temp, "wpg_state_file"), "w") as fh:
        fh.write("x")
    os.utime(os.path.join(temp, "wpg_state_file"), (old, old))

    removed = harness_tests.sweep_stale_proof_temp(repo, temp_dir=temp)
    left = sorted(os.listdir(temp))
    ck("stale folders under tracked proof prefixes and the runner's roots are removed",
       removed == 3 and not {"wpg_state_old1", "evaldash_cwd_old2", "harness_proof_old3"} & set(left),
       "removed=%s left=%s" % (removed, left))
    ck("a fresh folder, a file, an unrelated folder, a short prefix's and a positional suffix's folder stay",
       {"wpg_state_fresh", "wpg_state_file", "unrelated_old", "us_old", "_suffixonly_old"} <= set(left), "left=%s" % left)
    ck("an unreadable temp dir sweeps nothing",
       harness_tests.sweep_stale_proof_temp(repo, temp_dir=os.path.join(root, "absent")) == 0)
    shutil.rmtree(root, ignore_errors=True)
    print("test_harness_tests_temp_sweep: %s" % ("FAIL" if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
