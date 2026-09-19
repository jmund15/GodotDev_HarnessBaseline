#!/usr/bin/env python3
"""Proof for tools/lf_normalize.py argument handling.

Live defect 2026-09-14: `lf_normalize.py --help` ignored the unknown flag and rewrote a file. The tool
now prints its usage for --help/-h and refuses any other unknown option with exit 2, before touching
git or a file. Every case points the tool at a directory with no tracked files, so a regression rewrites
nothing.

    python3 .claude/tests/test_lf_normalize.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, ".claude", "tools", "lf_normalize.py")
EMPTY = ".claude/.lf-normalize-proof-no-such-dir"


def run(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, cwd=REPO, timeout=60)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    cases = []
    rc, out = run("--help", EMPTY)
    cases.append(("--help prints usage and exits 0", rc == 0 and "--include-dirty" in out and "lf_normalize: rewrote" not in out
                  and "CRLF working copies" not in out))
    rc, out = run("-h", EMPTY)
    cases.append(("-h prints usage and exits 0", rc == 0 and "--check" in out and "CRLF working copies" not in out))
    rc, out = run("--bogus", EMPTY)
    cases.append(("an unknown option exits 2 and names it", rc == 2 and "--bogus" in out and "CRLF working copies" not in out))
    rc, out = run(EMPTY, "second-dir")
    cases.append(("a second directory operand exits 2", rc == 2 and "CRLF working copies" not in out))
    rc, out = run("--check", EMPTY)
    cases.append(("a known option still runs", rc == 0 and "0 CRLF working copies" in out))
    rc, out = run(EMPTY)
    cases.append(("a directory operand alone still runs", rc == 0 and "0 CRLF working copies" in out))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
