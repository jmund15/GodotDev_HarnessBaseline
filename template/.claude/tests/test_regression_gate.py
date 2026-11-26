"""Proof: regression_gate.ps1 refuses a missing repo, prints the route for an existing one, and launches its held
children holding no machine lane of its own

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. The lane case runs the gate's real `Invoke-GatePool` against a
planted one-lane pool with the real RuntimeSlots.ps1 and the real broker; its run pool is stubbed by a held child's
lane take, so nothing launches Godot, dotnet or a test run.
"""
import os

from _pwsh_drive import check, drive, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "regression_gate.ps1")
SLOTS = os.path.join(REPO, ".claude", "scripts", "RuntimeSlots.ps1")

# The gate's coordinator holds its checkout, then its pool launches a held child that takes a lane. With one lane in
# the pool, a coordinator that kept its own lane would leave the child waiting until its lock-wait expired; a
# whole-machine take queued between them waits on the coordinator's lane while the child waits behind it.
LANE_BODY = r"""
$gate = [System.Management.Automation.Language.Parser]::ParseFile('{tool}', [ref]$null, [ref]$null)
$fn = @($gate.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Invoke-GatePool' }}, $true))
if ($fn.Count -ne 1) {{ 'NO-POOL-FUNCTION'; exit 3 }}
. ([scriptblock]::Create($fn[0].Extent.Text))
function Emit {{ param([string] $Text) }}
function Get-RunPoolWidth {{ [pscustomobject]@{{ Count = 1; Source = 'proof' }} }}
function Invoke-RunDirReap {{ param([string] $Site) }}
function Assert-UnitsAdmitted {{ param($Results) }}
function Invoke-RunPool {{
    param($Units, $ResultsRoot, $Launch, $OnPoll)
    $c = Enter-RunLocks -Repo $repo -Label 'held child' -HeldCheckout -RecompilePass -RecordDir $rec -TimeoutMs 4000
    $script:child = "$($c.Acquired):$($c.Blocker)"
    Exit-RunLocks $c
    @()
}}
$repo = '{repo}'
$rec = '{rec}'
$script:RunDir = '{rec}'
$script:HeldLease = Enter-HeldCheckout -Repo $repo -Label 'gate proof' -TimeoutMs 8000 -CheckoutLockName 'gate-lane-{pid}-ck' -RecordDir $rec
"coordinator=$($script:HeldLease.Acquired):$($script:HeldLease.Blocker)"
if (-not $script:HeldLease.Acquired) {{ exit 4 }}
Set-CheckoutStamp -Repo $repo -Name built -RecordDir $rec
Invoke-GatePool -Units @(@{{ label = 'u'; timeoutMs = 1000 }}) -Phase 'proof'
"child=$script:child"
Exit-HeldCheckout -Lease $script:HeldLease
$after = Enter-RunLocks -Repo $repo -Label 'after' -CheckoutLockName 'gate-lane-{pid}-ck' -RecordDir $rec -TimeoutMs 4000
"after=$($after.Acquired):$($after.Blocker)"
Exit-RunLocks $after
"""


def coordinator_lane(tmp):
    rec = os.path.join(tmp, "lane-records")
    body = LANE_BODY.format(tool=TOOL.replace("'", "''"), repo=REPO.replace("'", "''"), rec=rec.replace("'", "''"),
                            pid=os.getpid())
    env = {"HARNESS_SLOT_DIR": os.path.join(tmp, "pool"), "HARNESS_MACHINE_SLOTS": "1", "HARNESS_SLOT_BUDGET": "",
           "HARNESS_MACHINE_ROOT": ""}
    rc, out, err = drive(body, tmp, SLOTS, env)
    check("the gate's coordinator takes its checkout and a lane in a free one-lane pool",
          "coordinator=True:" in out, f"rc={rc} {out!r} {err[-300:]!r}")
    check("a held child the gate's pool launches gets the pool's only lane",
          rc == 0 and "child=True:" in out, f"rc={rc} {out!r} {err[-300:]!r}")
    check("the checkout and the lane are free once the gate exits its held checkout",
          rc == 0 and "after=True:" in out, f"rc={rc} {out!r} {err[-300:]!r}")


def main(tmp):
    rc, _, _ = pwsh(["-File", TOOL, "-RouteOnly", "-IgnoreEditor", "-Repo", os.path.join(tmp, "missing")], tmp)
    check("a missing -Repo exits non-zero", rc != 0, f"rc={rc}")
    rc, out, err = pwsh(["-File", TOOL, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp], tmp)
    route = out.strip().split("ROUTE=", 1)[-1].strip()
    check("-RouteOnly prints the checkout and exits 0",
          rc == 0 and "ROUTE=" in out and os.path.samefile(route, tmp), f"rc={rc} {out!r} {err!r}")
    coordinator_lane(tmp)


if __name__ == "__main__":
    run_proof(main, "regression_gate-proof-")
