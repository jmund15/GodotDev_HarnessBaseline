"""Proof: batch_diagnosis.ps1 triggers on a hang and on a long green batch, never on RED

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "batch_diagnosis.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    red = "$b = [pscustomobject]@{ status = 'RED'; workWall = 10 }; $t = Test-BatchNeedsDiagnosis -Batch $b -Counter 0 -ReservationSec 60; \"trigger=$t\"; if (-not $t) { exit 3 }"
    rc, out, _ = drive(red, tmp)
    check("RED never triggers, exit 3", rc == 3 and "trigger=False" in out, f"rc={rc} {out!r}")
    hang = ("\"hang=$(Test-BatchNeedsDiagnosis -Batch ([pscustomobject]@{ status = 'HANG'; workWall = 1 }) -Counter 0 -ReservationSec 60) "
            "slow=$(Test-BatchNeedsDiagnosis -Batch ([pscustomobject]@{ status = 'GREEN'; workWall = 200 }) -Counter 0 -ReservationSec 100) "
            "fast=$(Test-BatchNeedsDiagnosis -Batch ([pscustomobject]@{ status = 'GREEN'; workWall = 50 }) -Counter 0 -ReservationSec 100)\"")
    rc, out, _ = drive(hang, tmp)
    check("HANG and a 2x-over green trigger, an on-budget green does not",
          rc == 0 and "hang=True slow=True fast=False" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("'last=' + (Get-LastTestName -Output \"  Passed Foo.Bar`n  Failed Baz.Qux\")", tmp)
    check("the last per-test line names the test", rc == 0 and "last=Baz.Qux" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "batch_diagnosis-proof-")
