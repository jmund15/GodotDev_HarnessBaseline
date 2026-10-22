"""Proof: CSharpSource.ps1 rejects an unreadable test patch and counts a removed test attribute

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: failure outcomes
(a deny or a non-zero exit) and success outcomes. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil
import sys
import tempfile

import _pwsh_drive

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "CSharpSource.ps1")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    bad = "$r = Get-TestAttributeDelta -Patch @('diff --git a/T.cs b/T.cs','Binary files a/T.cs and b/T.cs differ'); \"readable=$($r.readable) reason=$($r.reason)\"; if (-not $r.readable) { exit 3 }"
    rc, out, _ = drive(bad, tmp)
    check("binary patch is unreadable, exit 3", rc == 3 and "readable=False reason=binary-test-diff" in out, f"rc={rc} {out!r}")
    good = "$r = Get-TestAttributeDelta -Patch @('diff --git a/T.cs b/T.cs','@@ -1,3 +1,2 @@',' class T {','-    [Test]',' }'); \"readable=$($r.readable) removed=$($r.removed) added=$($r.added)\""
    rc, out, _ = drive(good, tmp)
    check("a removed [Test] counts once", rc == 0 and "readable=True removed=1 added=0" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("Remove-CSharpLiterals -Text 'a \"[Test]\" b' | ForEach-Object { \"masked=[$_]\" }", tmp)
    check("a literal is blanked at its own length", rc == 0 and "masked=[a" in out and "Test" not in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="CSharpSource-proof-") as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)
