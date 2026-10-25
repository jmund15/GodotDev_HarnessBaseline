#requires -Version 7
<#
.SYNOPSIS
  One reader of the machine-global activity registry and one own-family predicate, shared by the gate
  (regression_gate.ps1) and the suite wrapper (run_test_suite.ps1).

.OWNS
  Get-LivePeerActivity: the registry reader with its PID-reuse guard; it sweeps dead records.
  Test-OwnFamily: a process is own family when it is one of the caller's -OwnRoots or its parent chain reaches one.
    Exclusion is by process ancestry only. Checkout and session never make a record own family: a same-session run that
    does not descend from the caller is real runtime load, and a .claude/worktrees/<x> record is a peer.
  Get-OwnerRoot: the Claude Code process (node/claude) that owns this process's subtree, walked once and cached. It
    reads no environment; the wrapper alone honours the owner root a gate passes its children.
  Get-CheckoutTestRunners: live test-runner Godot processes on a checkout that are not own family.

.NOTES
  Dot-sourced flat .ps1 (house pattern). Depends one way on GodotProcess.ps1. The registry's writers (the activity
  records of the gate and the wrapper) stay with their writers.
#>
. (Join-Path $PSScriptRoot 'GodotProcess.ps1')

# 12 hops, the depth Get-OwnerRoot walks; the traced chain from a test Godot to the gate is 5.
function Test-OwnFamily {
    param([Parameter(Mandatory)] [int] $ProcessId, [int[]] $OwnRoots = @(), [hashtable] $Map, [int] $MaxHops = 12)
    if ($ProcessId -in $OwnRoots) { return $true }
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $proc = $Map[$ProcessId]
    if ($null -eq $proc) { return $false }
    foreach ($root in $OwnRoots) {
        if (Test-ChainReachesPid -Proc $proc -Map $Map -RootPid $root -MaxHops $MaxHops) { return $true }
    }
    $false
}

# Live gate/suite records whose writer is not own family. A record whose PID is gone, or whose process start differs
# from its procStart (PID reuse), is garbage: it is deleted here, so a crashed run cannot wedge a phantom peer forever.
function Get-LivePeerActivity {
    param([int[]] $OwnRoots = @(), [string] $ActivityDir = (Join-Path (Get-HarnessTempRoot) 'harness-activity'), [hashtable] $Map)
    if (-not (Test-Path -LiteralPath $ActivityDir)) { return }
    $files = @(Get-ChildItem -LiteralPath $ActivityDir -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) { return }
    # Taken after the listing, so every listed record's writer existed when the snapshot was taken.
    if (-not $Map) { $Map = Get-ProcSnapshot }
    foreach ($f in $files) {
        $rec = $null
        try { $rec = Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        if ($null -eq $rec -or -not $rec.PSObject.Properties['pid'] -or -not $rec.PSObject.Properties['kind']) { continue }
        if ([string] $rec.kind -notin @('gate', 'suite')) { continue }
        $rpid = 0
        try { $rpid = [int] $rec.pid } catch { continue }
        if ($rpid -le 0) { continue }
        $proc = Get-Process -Id $rpid -ErrorAction SilentlyContinue
        if (-not $proc) { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue; continue }
        # A ConvertFrom-Json DateTime is compared as is: a [string] cast drops its Z, and the re-parse then shifts it by
        # the machine offset (measured +5h), which swept every live record as stale.
        $ps = if ($rec.PSObject.Properties['procStart']) { $rec.procStart } else { $null }
        if ($ps) {
            try {
                $psDt = if ($ps -is [datetime]) { $ps } else { [datetime] $ps }
                $live = Get-ProcessStartStamp -ProcessId $rpid
                if ($live -and [math]::Abs(($psDt.ToUniversalTime() - $live).TotalSeconds) -gt 5) {
                    Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue; continue
                }
            } catch { continue }
        }
        if (Test-OwnFamily -ProcessId $rpid -OwnRoots $OwnRoots -Map $Map) { continue }
        $rec
    }
}

$script:OwnerRootComputed = $false
$script:OwnerRootCache = $null
# Captured once, early: a detached run outlives its launcher, and its chain then reaches no session at all.
function Get-OwnerRoot {
    if ($script:OwnerRootComputed) { return $script:OwnerRootCache }
    $script:OwnerRootComputed = $true
    try {
        $map = Get-ProcSnapshot
        $self = $map[[int] $PID]
        if ($self) {
            foreach ($p in @(Get-ParentChain -Proc $self -Map $map -MaxHops 12)) {
                if ([string] $p.Name -match '^(node|claude)(\.exe)?$') {
                    $script:OwnerRootCache = [pscustomobject] @{
                        RootPid   = [int] $p.ProcessId
                        RootStart = ([datetime] $p.CreationDate).ToUniversalTime().ToString('o')
                    }
                    break
                }
            }
        }
    } catch { }
    $script:OwnerRootCache
}

function Get-CheckoutTestRunners {
    param([Parameter(Mandatory)] [string] $Repo, [hashtable] $Map, [int[]] $OwnRoots = @())
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $root = Resolve-RootPath $Repo
    @($Map.Values | Where-Object {
        [string] $_.Name -like 'Godot*' -and
        (Get-GodotRole -Proc $_ -Map $Map) -eq 'TestRunner' -and
        (Test-RunnerOnCheckout -Proc $_ -Map $Map -Root $root) -and
        -not (Test-OwnFamily -ProcessId ([int] $_.ProcessId) -OwnRoots $OwnRoots -Map $Map)
    })
}

# A runner launches with a relative `--path .`, so its parent chain (the testhost under its checkout) places it.
# A chain under another checkout is that checkout's run; only a runner whose chain is gone falls back to
# Resolve-GodotCheckout, whose .godot-mtime guess would otherwise claim every runner started before this
# checkout's last import.
function Test-RunnerOnCheckout {
    param([Parameter(Mandatory)] [object] $Proc, [hashtable] $Map, [Parameter(Mandatory)] [string] $Root)
    $verdict = Get-ProcVerdict $Proc $Map $Root 3
    if ($verdict -eq 'MINE') { return $true }
    if ($verdict -ne 'ORPHAN') { return $false }
    (Resolve-GodotCheckout -Proc $Proc -Map $Map -CandidateRoots @($Root)) -eq $Root
}
