"""Proof: IntegrationBatches.ps1 falls back on malformed adaptation.json and reads a valid key

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "IntegrationBatches.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    cfg = os.path.join(tmp, ".claude", "skills", "project_subsystems")
    os.makedirs(cfg)
    path = os.path.join(cfg, "adaptation.json")
    body = "$v = Get-AdaptationValue -Repo $env:PROOF_TMP -Key k -Default dflt; \"value=$v\"; if ($v -eq 'dflt') { exit 3 }"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("{bad")
    rc, out, err = drive(body, tmp)
    check("malformed json yields the default and names the file", rc == 3 and "value=dflt" in out and "unparseable" in err, f"rc={rc} {out!r} {err!r}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"k": "val"}')
    rc, out, _ = drive(body, tmp)
    check("a valid string key is returned", rc == 0 and "value=val" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "IntegrationBatches-proof-")
