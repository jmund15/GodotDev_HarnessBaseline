"""Proof: RuntimeSlots.ps1 names the runtime slots and maps a crash exit to an overlap signature

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: failure outcomes
(a deny or a non-zero exit) and success outcomes. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil
import sys
import tempfile

import _pwsh_drive

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "RuntimeSlots.ps1")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    quiet = "$k = Get-OverlapSignatureKind -ExitCode 0; \"kind=[$k]\"; if (-not $k) { exit 3 }"
    rc, out, _ = drive(quiet, tmp)
    check("a clean exit has no overlap signature, exit 3", rc == 3 and "kind=[]" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("\"kind=$(Get-OverlapSignatureKind -ExitCode -1073741795)\"", tmp)
    check("a native crash exit maps to its signature", rc == 0 and "kind=testhost-native-crash" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("$n = @(Get-RuntimeSlotNames); \"count=$($n.Count) unique=$(@($n | Sort-Object -Unique).Count)\"", tmp)
    check("slot names are distinct", rc == 0 and "count=8 unique=8" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="RuntimeSlots-proof-") as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)
