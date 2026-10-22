"""Proof: WideRunGuard.ps1 denies a wide run when the guard says exit 2 and admits it when the guard passes

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "WideRunGuard.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    scripts = os.path.join(tmp, "scripts")
    hooks = os.path.join(tmp, "hooks")
    os.makedirs(scripts)
    os.makedirs(hooks)
    tool = os.path.join(scripts, "WideRunGuard.ps1")
    shutil.copy(TOOL, tool)
    body = "Assert-WideRunAllowed -Entry 'x.ps1'; \"admitted=$env:HARNESS_WIDE_RUN_ADMITTED\""
    env = {"CLAUDE_CODE_SIDECAR": "1"}
    stub = os.path.join(hooks, "wide_test_guard.py")
    with open(stub, "w", encoding="utf-8", newline="\n") as f:
        f.write("import sys\nprint('DENIED by stub')\nsys.exit(2)\n")
    rc, out, _ = drive(body, tmp, env, tool)
    check("a guard exit 2 ends the run with 2", rc == 2 and "DENIED by stub" in out and "admitted=" not in out, f"rc={rc} {out!r}")
    with open(stub, "w", encoding="utf-8", newline="\n") as f:
        f.write("import sys\nsys.exit(0)\n")
    rc, out, _ = drive(body, tmp, env, tool)
    check("a guard exit 0 admits the run", rc == 0 and "admitted=1" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive(body, tmp, None, tool)
    check("outside a delegate nothing is checked", rc == 0 and "admitted=" in out and "admitted=1" not in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "WideRunGuard-proof-")
