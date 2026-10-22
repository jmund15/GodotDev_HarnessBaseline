"""Proof: PeerActivity.ps1 rejects a process outside the own family and accepts an own root

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "PeerActivity.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    stranger = "if (-not (Test-OwnFamily -ProcessId 77 -OwnRoots @(1) -Map @{ 1 = 'root' })) { 'own=False'; exit 3 }"
    rc, out, _ = drive(stranger, tmp)
    check("an unknown pid is not own family, exit 3", rc == 3 and "own=False" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("'own=' + (Test-OwnFamily -ProcessId 1 -OwnRoots @(1) -Map @{ 1 = 'root' })", tmp)
    check("a listed root is own family", rc == 0 and "own=True" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "PeerActivity-proof-")
