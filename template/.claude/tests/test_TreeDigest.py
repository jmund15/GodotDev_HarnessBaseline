"""Proof: TreeDigest.ps1 never matches an empty digest and excludes harness paths

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "TreeDigest.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    rc, out, _ = drive("$m = Test-DigestMatch -A '' -B 'abc'; \"match=$m\"; if (-not $m) { exit 3 }", tmp)
    check("an empty digest never matches, exit 3", rc == 3 and "match=False" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("\"match=$(Test-DigestMatch -A 'abc' -B 'abc') other=$(Test-DigestMatch -A 'abc' -B 'abd')\"", tmp)
    check("equal digests match and unequal ones do not", rc == 0 and "match=True other=False" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("\"claude=$(Test-DigestExcluded '.claude/x.md') src=$(Test-DigestExcluded 'src/a.cs')\"", tmp)
    check("harness paths are excluded, source is not", rc == 0 and "claude=True src=False" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "TreeDigest-proof-")
