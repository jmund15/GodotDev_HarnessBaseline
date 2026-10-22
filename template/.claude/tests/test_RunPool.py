"""Proof: RunPool.ps1 classifies an empty wrapper output as NO_OUTPUT and a complete one as GREEN

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: failure outcomes
(a deny or a non-zero exit) and success outcomes. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil
import sys
import tempfile

import _pwsh_drive

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "RunPool.ps1")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    empty = "$r = ConvertFrom-WrapperOutput -Unit @{ label = 'u' } -Lines @() -ExitCode 0 -ElapsedSec 1; \"status=$($r.status)\"; if ($r.status -ne 'GREEN') { exit 3 }"
    rc, out, _ = drive(empty, tmp)
    check("no output is not green, exit 3", rc == 3 and "status=NO_OUTPUT" in out, f"rc={rc} {out!r}")
    trx = os.path.join(tmp, "u.trx")
    with open(trx, "w", encoding="utf-8", newline="\n") as f:
        f.write("<TestRun/>")
    done = ("$r = ConvertFrom-WrapperOutput -Unit @{ label = 'u' } -Lines @('STATUS=DONE', (\"TRX=\" + (Join-Path $env:PROOF_TMP 'u.trx')), 'Failed: 0, Passed: 3') "
            "-ExitCode 0 -ElapsedSec 1; \"status=$($r.status) passed=$($r.passed)\"")
    rc, out, _ = drive(done, tmp)
    check("a done run with a readable trx is GREEN", rc == 0 and "status=GREEN passed=3" in out, f"rc={rc} {out!r}")
    hang = "(ConvertFrom-WrapperOutput -Unit @{ label = 'u' } -Lines @('x') -ExitCode 124 -ElapsedSec 1).status"
    rc, out, _ = drive(hang, tmp)
    check("exit 124 is a HANG", rc == 0 and "HANG" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="RunPool-proof-") as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)
