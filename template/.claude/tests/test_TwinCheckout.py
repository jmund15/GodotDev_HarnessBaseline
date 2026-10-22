"""Proof: TwinCheckout.ps1 refuses a non-git source and names the twin of a git checkout

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os
import subprocess

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "TwinCheckout.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    plain = os.path.join(tmp, "plain")
    os.makedirs(plain)
    rc, _, _ = drive("Get-TwinPath -Source (Join-Path $env:PROOF_TMP 'plain')", tmp)
    check("a non-git source throws", rc != 0, f"rc={rc}")
    src = os.path.join(tmp, "work", "src")
    os.makedirs(src)
    git = subprocess.run(["git", "init", "-q", src], capture_output=True, text=True)
    check("planted git repo", git.returncode == 0, git.stderr)
    rc, out, _ = drive("'TWIN=' + (Get-TwinPath -Source (Join-Path (Join-Path $env:PROOF_TMP 'work') 'src'))", tmp)
    twin = out.strip().split("TWIN=", 1)[-1].replace("\\", "/")
    check("the twin sits under <checkout>/.claude/worktrees as <leaf>--twin",
          rc == 0 and twin.endswith("/work/src/.claude/worktrees/src--twin"), f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "TwinCheckout-proof-")
