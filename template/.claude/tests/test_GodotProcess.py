"""Proof: GodotProcess.ps1 refuses a missing repo and resolves an existing one

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "GodotProcess.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    rc, _, err = drive("Resolve-TestRepo -Repo (Join-Path $env:PROOF_TMP 'missing') -ScriptsRoot $env:PROOF_TMP", tmp)
    check("a missing -Repo throws", rc != 0, f"rc={rc}")
    rc, out, _ = drive("'RESOLVED=' + (Resolve-TestRepo -Repo $env:PROOF_TMP -ScriptsRoot $env:PROOF_TMP)", tmp)
    check("an existing -Repo resolves to itself",
          rc == 0 and os.path.samefile(out.strip().split("RESOLVED=", 1)[-1], tmp), f"rc={rc} {out!r}")
    rc, out, _ = drive("'LABEL=' + (Get-SafeRunLabel -Label 'a b/c' -Repo $env:PROOF_TMP)", tmp)
    check("a label is made path-safe", rc == 0 and "LABEL=a_b_c" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "GodotProcess-proof-")
