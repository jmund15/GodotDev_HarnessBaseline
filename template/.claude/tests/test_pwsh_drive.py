"""Proof: `drive_many` reports each case's exit code, output and environment as a `pwsh -File` child would

Every case shares one pwsh (`Cases`, a runspace each). A case reports its code only by how it ends: `exit N` is N
wherever it runs, a normal end is 0 even when a native command left a code in `$LASTEXITCODE`, and a terminating
error is 1.
"""
import os

from _pwsh_drive import ISOLATED, Cases, check, run_proof


def main(tmp):
    batch = Cases()

    def code(label, body, want):
        batch.add(body, lambda rc, out, err: check(label, rc == want and "before" in out, f"rc={rc} {out!r} {err!r}"))

    code("a bare exit 3 reports 3", "'before'; exit 3; 'after'", 3)
    code("exit inside a function reports its code", "function Stop-Case { exit 4 }; 'before'; Stop-Case; 'after'", 4)
    code("a normal end reports 0 though a native command left a code", "'before'; $global:LASTEXITCODE = 5", 0)
    code("exit 0 reports 0", "'before'; exit 0", 0)
    code("a terminating error reports 1", "'before'; throw 'boom'", 1)
    batch.add("[Console]::Error.WriteLine('console-err'); Write-Host 'host-line'; $env:PWSH_DRIVE_LEAK = 'x'",
              lambda rc, out, err: check("host text joins stdout and a [Console]::Error write joins stderr",
                                         rc == 0 and "host-line" in out and "console-err" in err, f"rc={rc} {out!r} {err!r}"))
    batch.add("\"leak=[$env:PWSH_DRIVE_LEAK] given=[$env:PWSH_DRIVE_GIVEN]\"",
              lambda rc, out, _: check("a case starts without an earlier case's environment and with its own",
                                       rc == 0 and "leak=[] given=[y]" in out, f"rc={rc} {out!r}"),
              {"PWSH_DRIVE_GIVEN": "y"})
    child = os.path.join(tmp, "child.ps1")
    with open(child, "w", encoding="utf-8", newline="\n") as f:
        f.write("\"args=$($args -join ',')\"; exit 6\n")
    nested = (". '" + ISOLATED.replace("'", "''") + "'; $r = Invoke-Isolated -File '" + child.replace("'", "''")
              + "' -ArgumentList @('a', 'b'); \"nested rc=$($r.rc) $($r.out)\"")
    batch.add(nested, lambda rc, out, _: check("a nested Invoke-Isolated passes its arguments and returns the child's code",
                                               rc == 0 and "nested rc=6 args=a,b" in out, f"rc={rc} {out!r}"))
    batch.run(tmp)


if __name__ == "__main__":
    run_proof(main, "pwsh-drive-")
