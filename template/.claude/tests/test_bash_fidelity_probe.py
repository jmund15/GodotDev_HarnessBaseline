#!/usr/bin/env python3
r"""Proof for tools/bash_fidelity_probe.py: each argv shape and its exit code.

The probe takes two literals as issued through the Bash tool: `x\\y` and `x\\"y`. It records
`<client-version>|<sys.platform>` into the fidelity record with one bool per backslash-run class:
`collapses` (argv 1 arrived as `x\y`) and `collapses_before_quote` (argv 2 arrived as `x\"y`).
`--client-version` is mandatory (only the hook supplies it); every malformed shape exits 2.
Exit codes outside {0, 2} or a traceback are CRASH.

    python3 .claude/tests/test_bash_fidelity_probe.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
PROBE = os.path.join(REPO, ".claude", "tools", "bash_fidelity_probe.py")
BS = "\\"
DQ = '"'
ONE = "x" + BS + "y"
TWO = "x" + BS + BS + "y"
QONE = "x" + BS + DQ + "y"
QTWO = "x" + BS + BS + DQ + "y"
VERSION = "9.9.9-probe"
KEY = "%s|%s" % (VERSION, sys.platform)

failures = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append(label + (" :: " + str(detail)[:400] if detail else ""))


def run(args, env):
    p = subprocess.run([sys.executable, PROBE] + list(args), capture_output=True, text=True,
                       encoding="utf-8", timeout=60, env=env, cwd=REPO)
    # A missing script also exits 2 ("can't open file"); that is a CRASH, not a rejection.
    crashed = p.returncode not in (0, 2) or "Traceback" in p.stderr or "can't open file" in p.stderr
    return p, crashed


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def main():
    tmp = tempfile.mkdtemp(prefix="bfp_")
    record = os.path.join(tmp, "cache", "record.json")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", HARNESS_BASH_FIDELITY_RECORD=record)

    check("probe file exists", os.path.isfile(PROBE), PROBE)

    for first, second, collapses, before_quote in (
        (ONE, QTWO, True, False),
        (TWO, QTWO, False, False),
        (ONE, QONE, True, True),
        (TWO, QONE, False, True),
    ):
        label = "argv %r %r" % (first, second)
        p, crashed = run([first, second, "--client-version", VERSION], env)
        row = (load(record) or {}).get(KEY) or {}
        check(label + " exits 0", not crashed and p.returncode == 0, p.stdout + p.stderr)
        check(label + " records collapses=%s collapses_before_quote=%s" % (collapses, before_quote),
              row.get("collapses") is collapses and row.get("collapses_before_quote") is before_quote
              and len(row.get("observed", "")) == 10, row)
    check("the probe prints the record it wrote", KEY in p.stdout and "collapses_before_quote" in p.stdout, p.stdout)

    os.makedirs(os.path.dirname(record), exist_ok=True)
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"keep|me": {"collapses": False, "observed": "2026-01-01"}}, fh)
    p, crashed = run([ONE, QTWO, "--client-version=" + VERSION], env)
    rec = load(record) or {}
    check("--client-version=<v> form exits 0", not crashed and p.returncode == 0, p.stdout + p.stderr)
    check("an existing key in the record is preserved", (rec.get("keep|me") or {}).get("observed") == "2026-01-01", rec)
    leftovers = [n for n in os.listdir(os.path.dirname(record)) if n != os.path.basename(record) and not n.endswith(".lock")]
    check("the write leaves no temp file behind", not leftovers, leftovers)

    before = load(record)
    for label, args in (
        ("no --client-version", [ONE, QTWO]),
        ("--client-version without a value", [ONE, QTWO, "--client-version"]),
        ("the retired one-argument form", [ONE, "--client-version", VERSION]),
        ("unexpected first argument xy", ["xy", QTWO, "--client-version", VERSION]),
        ("three backslashes", ["x" + BS * 3 + "y", QTWO, "--client-version", VERSION]),
        ("second argument without a quote", [ONE, ONE, "--client-version", VERSION]),
        ("three backslashes before the quote", [ONE, "x" + BS * 3 + DQ + "y", "--client-version", VERSION]),
        ("no argument", ["--client-version", VERSION]),
        ("three arguments", [ONE, QTWO, QTWO, "--client-version", VERSION]),
        ("unknown flag", [ONE, QTWO, "--client-version", VERSION, "--bogus"]),
        ("empty client version", [ONE, QTWO, "--client-version", ""]),
    ):
        p, crashed = run(args, env)
        check("%s exits 2" % label, not crashed and p.returncode == 2, "rc=%s %s" % (p.returncode, p.stderr))
    check("rejected invocations leave the record unchanged", load(record) == before, load(record))

    p, crashed = run(["--help"], env)
    check("--help exits 0 and prints usage", not crashed and p.returncode == 0 and "usage" in (p.stdout + p.stderr).lower(),
          p.stdout + p.stderr)

    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
