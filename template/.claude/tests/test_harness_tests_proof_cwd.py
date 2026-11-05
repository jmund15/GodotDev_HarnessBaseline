#!/usr/bin/env python3
"""Proof that harness_tests.py runs each proof from the checkout that holds the runner.

Proofs resolve `.claude/...` paths against their working directory. A runner started from another
directory (the baseline publish starts its battery one level above `template/`) used to hand each
proof that directory, so every cwd-relative proof went red there and green everywhere else.

Run: python3 .claude/tests/test_harness_tests_proof_cwd.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "scripts", "harness_tests.py")
spec = importlib.util.spec_from_file_location("ht", MOD)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)


def main():
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
    ok = status == "pass"
    print(("ok   " if ok else "FAIL ") + "a proof started from another directory runs from the runner's checkout root")
    if not ok:
        print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
