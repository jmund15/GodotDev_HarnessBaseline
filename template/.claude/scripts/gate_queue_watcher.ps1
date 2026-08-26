<#
.SYNOPSIS
  Detached, per-checkout watcher that fires queued /regression_gate runs once the
  machine is quiet, and aborts a run the moment a Godot editor reclaims the
  checkout.

.WHY THIS EXISTS
  A gate run and an open Godot editor are mutually destructive: they share
  .godot/mono/temp/bin/Debug/{{PROJECT_NAME}}.dll (the Godot SDK pins OutputPath, so
  there is no per-consumer output). regression_gate.ps1 therefore refuses to run
  inline while an editor holds the checkout — it writes a request here and exits
  7 = QUEUED in under a second, instead of blocking the caller for minutes.

  This process is what makes that handoff honest. It outlives the Claude Code
  session that spawned it (no redirected handles back to the parent), so a run
  queued at 16:00 still fires at 18:00 when the editor closes.

.THE EDITOR ALWAYS WINS
  Discarded compute is recoverable; a blocked editor build and a stripped .tres
  set are not. If an editor appears mid-run, the gate child's whole process tree
  is killed and the request returns to pending.

  The abort is by OWNERSHIP, not by role: the gate child (pwsh) and its
  `dotnet build` classify as role Unknown and are permanently unkillable by
  Get-ReapableGodot. We launched the child, so we hold its PID and taskkill /T it
  directly; the Get-ReapableGodot sweep afterwards is belt-and-braces for
  anything that detached.

.OUTPUT
  Everything lands under .claude/scratch/gate_queue/:
    <id>.request.json   written by regression_gate.ps1; pending until a result exists
    <id>.log            the fired run's stdout   <id>.log.err  its stderr
    <id>.result.json    written by the gate (Complete-Gate) for every completed run; this file writes only the watcher-error GIVE-UP record
    watcher.log         this process's own trace (capped)

.EXIT
  0 always — including "another watcher already owns this checkout".
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo        = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$scripts     = Join-Path $repo '.claude\scripts'
$queueDir    = Join-Path $repo '.claude\scratch\gate_queue'
$activityDir = Join-Path $env:TEMP 'pp-activity'
$logP        = Join-Path $queueDir 'watcher.log'
$gateP       = Join-Path $scripts 'regression_gate.ps1'

$TickSec     = 5
$IdleExitMin = 10
$KeepRuns    = 20
$MaxAttempts = 3
$LogCapBytes = 256KB

New-Item -ItemType Directory -Force -Path $queueDir | Out-Null

# A detached process has no console anyone reads, so the log IS the only surface.
function Write-Log {
    param([string] $Text)
    try {
        if ((Test-Path $logP) -and ((Get-Item $logP).Length -gt $LogCapBytes)) {
            $tail = @(Get-Content -Path $logP -Tail 200 -ErrorAction SilentlyContinue)
            Set-Content -Path $logP -Value (@("--- truncated at $(Get-Date -Format 'u') ---") + $tail) -Encoding utf8
        }
        Add-Content -Path $logP -Value ("{0} {1}" -f (Get-Date -Format 'u'), $Text) -Encoding utf8
    } catch { }
}

function Get-Prop {
    param($Obj, [string] $Name, $Default = $null)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] } else { return $Default }
    }
    if ($Obj.PSObject.Properties[$Name]) { return $Obj.$Name }
    $Default
}

$godotProcP = Join-Path $scripts 'GodotProcess.ps1'
if (-not (Test-Path $godotProcP)) {
    Write-Log 'ABORT GodotProcess.ps1 missing — cannot identify processes, refusing to run.'
    exit 0
}
. $godotProcP

# The queue's file protocol — pending-set and digest comparison — shared with the gate. The
# prefix compare below used to be a second copy living in Invoke-QueuedGate.
$gateQueueP = Join-Path $scripts 'GateQueue.ps1'
if (-not (Test-Path $gateQueueP)) {
    Write-Log 'ABORT GateQueue.ps1 missing — cannot read the queue protocol, refusing to run.'
    exit 0
}
. $gateQueueP

# Field probes only; identity itself comes from GodotProcess.ps1.
function Get-ProcPid {
    param($P)
    foreach ($n in @('ProcessId', 'Id')) {
        if ($null -ne $P -and $P.PSObject.Properties[$n]) { return [int]$P.$n }
    }
    0
}
function Get-CheckoutTestRunners {
    param($Map)
    $vals = if ($Map -is [System.Collections.IDictionary]) { @($Map.Values) } else { @($Map) }
    @($vals | Where-Object {
        $n = $null
        foreach ($k in @('Name', 'ProcessName', 'Caption')) { if ($_.PSObject.Properties[$k]) { $n = [string]$_.$k; break } }
        $n -and $n -like 'Godot*' -and
        (Get-GodotRole -Proc $_ -Map $Map) -eq 'TestRunner' -and
        # -CandidateRoots is load-bearing: runners launch with a relative `--path .`, so only
        # the parent-chain attribution (which needs a candidate root) can place them.
        (Resolve-GodotCheckout -Proc $_ -Map $Map -CandidateRoots @($repo)) -eq $repo
    })
}

# ---------------------------------------------------------------- activity registry
$script:SpawnedGatePids   = [System.Collections.Generic.HashSet[int]]::new()
$script:ActivityPath      = $null
$script:ActivityStart     = $null
$script:ActivityProcStart = $null

function Update-ActivityRecord {
    param([string] $Label)
    if (-not $script:ActivityPath) { return }
    try {
        $rec = [ordered]@{
            pid         = $PID
            procStart   = $script:ActivityProcStart
            kind        = 'watcher'
            checkout    = $repo
            sessionId   = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $null })
            label       = $Label
            startedAt   = $script:ActivityStart
            heartbeatAt = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
            expectedSec = 600
        }
        Set-Content -Path $script:ActivityPath -Value ($rec | ConvertTo-Json -Depth 4) -Encoding utf8
    } catch { }
}
function Start-ActivityRecord {
    try {
        New-Item -ItemType Directory -Force -Path $activityDir | Out-Null
        $script:ActivityPath      = Join-Path $activityDir "watcher-$PID.json"
        $script:ActivityStart     = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
        $script:ActivityProcStart = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        Update-ActivityRecord -Label 'idle'
    } catch { $script:ActivityPath = $null }
}
function Stop-ActivityRecord {
    try { if ($script:ActivityPath) { Remove-Item -Path $script:ActivityPath -Force -ErrorAction SilentlyContinue } } catch { }
}

# ---------------------------------------------------------------- queue files
function Get-PendingRequests { @(Get-PendingQueueRequests -QueueDir $queueDir) }

function Set-RequestAttempt {
    param($Req, [int] $Attempt)
    try {
        $id = [string](Get-Prop $Req 'id' '')
        $p  = Join-Path $queueDir "$id.request.json"
        if (-not (Test-Path $p)) { return }
        $obj = Get-Content -Path $p -Raw | ConvertFrom-Json
        $obj.attempt = $Attempt
        Set-Content -Path $p -Value ($obj | ConvertTo-Json -Depth 5) -Encoding utf8
    } catch { Write-Log "WARN could not bump attempt: $_" }
}

function Write-Result {
    param([hashtable] $Result)
    try {
        Set-Content -Path (Join-Path $queueDir "$($Result.id).result.json") `
                    -Value (([ordered]@{
                        id         = $Result.id
                        status     = $Result.status
                        exitCode   = $Result.exitCode
                        head       = $Result.head
                        treeDigest = $Result.treeDigest
                        treeDelta  = $Result.treeDelta
                        startedAt  = $Result.startedAt
                        finishedAt = $Result.finishedAt
                        attempt    = $Result.attempt
                        detail     = $Result.detail
                    } | ConvertTo-Json -Depth 5)) -Encoding utf8
    } catch { Write-Log "ERROR writing result: $_" }
}

# Keep the newest $KeepRuns id-triplets; ids sort chronologically by construction
# (yyyyMMdd-HHmmss-pid), so name order is age order.
function Invoke-QueueSweep {
    try {
        $ids = @(Get-ChildItem -Path $queueDir -Filter '*.request.json' -ErrorAction SilentlyContinue |
                 Sort-Object Name | ForEach-Object { $_.Name -replace '\.request\.json$', '' })
        if ($ids.Count -le $KeepRuns) { return }
        foreach ($old in $ids[0..($ids.Count - $KeepRuns - 1)]) {
            foreach ($suffix in @('.request.json', '.result.json', '.log', '.log.err')) {
                Remove-Item -Path (Join-Path $queueDir "$old$suffix") -Force -ErrorAction SilentlyContinue
            }
        }
    } catch { Write-Log "WARN sweep: $_" }
}

# ---------------------------------------------------------------- readiness
# A peer GATE is invisible to Get-CheckoutTestRunners until it reaches its suites (in
# guards/build it has spawned no runner) and holds no mutex yet — but firing a queued run into
# a peer gate's tail is exactly the collision the queue exists to prevent. The gate's own
# kind=gate activity record exists from its first moment, so liveness is read from the
# registry (PID alive + procStart match), same semantics as the gate's Get-LivePeerActivity.
# The gate check is MACHINE-WIDE (any checkout, any session): every gate on this machine shares
# the machine-global runtime mutex, so quiet for firing purposes means no live gate anywhere,
# not just none on this checkout.
function Test-PeerGateLive {
    if (-not (Test-Path $activityDir)) { return $false }
    foreach ($f in @(Get-ChildItem -Path $activityDir -Filter '*.json' -ErrorAction SilentlyContinue)) {
        $rec = $null
        try { $rec = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        if ((Get-Prop $rec 'kind' '') -ne 'gate') { continue }
        # Machine-wide on purpose: a gate on ANY checkout occupies the same machine-global
        # runtime mutex for its suite phases. Firing this checkout's queued run into a peer
        # checkout's gate tail is exactly the collision the queue exists to prevent
        # (2026-08-14: two cross-checkout gates produced four INVALID verdicts).
        $rpid = [int](Get-Prop $rec 'pid' -Default 0)
        # Own-child exclusion: the gate children THIS watcher spawned are not peers — the
        # serial fire loop makes the timing safe today, but ownership must be explicit, not
        # incidental (mirrors the reaper's own-tree set).
        if ($rpid -le 0 -or $rpid -eq $PID -or $script:SpawnedGatePids.Contains($rpid)) { continue }
        $proc = Get-Process -Id $rpid -ErrorAction SilentlyContinue
        if (-not $proc) { continue }   # record outlived its process — not live
        # PID-reuse guard: the record is only about the process that actually wrote it.
        # ConvertFrom-Json materializes the ISO timestamp as a DateTime already; a [string]
        # cast would strip the offset marker and the re-parse lands Kind=Unspecified, whose
        # ToUniversalTime() adds the machine offset — measured 2026-08-14: a +5h skew that
        # made every live peer record read as stale. Compare DateTimes directly.
        $ps = Get-Prop $rec 'procStart' ''
        if ($ps) {
            try {
                $psDt = if ($ps -is [datetime]) { $ps } else { [datetime]$ps }
                if ([math]::Abs(($psDt.ToUniversalTime() - $proc.StartTime.ToUniversalTime()).TotalSeconds) -gt 5) { continue }
            } catch { continue }
        }
        return $true
    }
    $false
}

# Which of the four readiness conditions is false, or '' when all hold. Named rather than
# boolean because a bare $false left the log silent for the entire block: 9m34s between START
# and FIRE on 2026-08-23 with not one line explaining it, so a busy machine and a wedged
# watcher were indistinguishable from the outside. $script:LastBlocker rate-limits the log to
# transitions — a blocker that persists for ten minutes is one line, not 120.
$script:LastBlocker = $null
function Get-QuietBlocker {
    $map = Get-ProcSnapshot
    if (@(Get-LiveEditor -Checkout $repo -Map $map).Count -gt 0) { return 'editor' }
    if (@(Get-CheckoutTestRunners -Map $map).Count -gt 0) { return 'test-runner' }
    if (Test-PeerGateLive) { return 'peer-gate' }
    $m = $null
    try {
        $m = New-Object System.Threading.Mutex($false, 'Global\gdunit4-{{PROJECT_NAME}}-runlock')
        $got = $false
        try { $got = $m.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
        if ($got) { $m.ReleaseMutex(); return '' }
        return 'runlock'
    } catch { return 'runlock-error' } finally { if ($m) { $m.Dispose() } }
}

function Test-CheckoutQuiet {
    $b = Get-QuietBlocker
    if ($b -ne $script:LastBlocker) {
        if ($b) { Write-Log "BLOCKED blocker=$b" } else { Write-Log 'QUIET all readiness checks clear' }
        $script:LastBlocker = $b
    }
    return (-not $b)
}

# ---------------------------------------------------------------- fire a run
function Invoke-QueuedGate {
    param($Req)

    $id       = [string](Get-Prop $Req 'id' '')
    $attempt  = [int](Get-Prop $Req 'attempt' -Default 0) + 1
    $reqDig   = [string](Get-Prop $Req 'treeDigest' '')
    # Absolute ceiling, independent of the 6/8 re-queue cap: a fire that THROWS (bad args, dead
    # gate script) bypasses the completion-path cap and would otherwise hot-loop every tick,
    # burning attempts forever with no result file (observed live 2026-08-13, attempt=6+).
    if ($attempt -gt (2 * $MaxAttempts)) {
        Write-Log "GIVE-UP id=$id attempt=$attempt (fire ceiling — repeated launch failures)"
        Write-Result @{
            id = $id; status = 'watcher-error'; exitCode = -1
            head = [string](Get-Prop $Req 'head' ''); treeDigest = $reqDig; treeDelta = $false
            startedAt = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
            finishedAt = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
            attempt = $attempt; detail = 'watcher could not launch the gate; see watcher.log'
        }
        return
    }
    $logFile  = Join-Path $queueDir "$id.log"
    $errFile  = "$logFile.err"
    $started  = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
    Set-RequestAttempt -Req $Req -Attempt $attempt

    # Pipeline flatten + string-cast: a request written by an older gate build may carry the
    # gateArgs array nested one level (JSON round-trip of a comma-wrapped return), and
    # Start-Process hard-fails on any non-string ArgumentList element.
    $gateArgs = @(@(Get-Prop $Req 'gateArgs' -Default @()) |
                  ForEach-Object { $_ } | ForEach-Object { [string]$_ } | Where-Object { $_ })
    # -QueueId: the gate uses the request id as its runId, so the ledger record it writes at
    # Complete-Gate carries THIS request's id — one record per request, never a stray, and
    # treeDelta is computed against the request-time digest.
    $argList  = @('-NoProfile', '-File', $gateP, '-IgnoreEditor', '-FromQueue', '-QueueId', $id) + $gateArgs

    # Fire stagger: two checkouts' watchers waking on the same quiet tick would launch in the
    # same second, and each gate's machine-wide preflight would see no peer record — the gate
    # writes its kind=gate record seconds after launch (Start-ActivityRecord sits before its
    # own preflight), so the race produced mutual CONTENTION instead of a polite queue
    # (measured 2026-08-15 01:06). Hold the machine-global fire mutex across the launch window:
    # the second watcher blocks ~15s, its gate then preflights against a REAL live record and
    # queues (exit 7, re-queued). Bounded wait — a wedged holder degrades to the unstaggered
    # race rather than stalling the queue; the abandoned path recovers like every other
    # process-owned mutex (a watcher killed mid-hold).
    $fireMutex = $null; $fireHeld = $false
    try {
        $fireMutex = New-Object System.Threading.Mutex($false, 'Global\{{PROJECT_NAME}}-gatequeue-fire')
        try { $fireHeld = $fireMutex.WaitOne(30000) } catch [System.Threading.AbandonedMutexException] { $fireHeld = $true }
    } catch { Write-Log "STAGGER-OPEN failed id=$id — firing unstaggered: $_" }
    if (-not $fireHeld) { Write-Log "STAGGER wait timed out id=$id — firing unstaggered" }

    Write-Log "FIRE id=$id attempt=$attempt args=$($gateArgs -join ' ')"
    Update-ActivityRecord -Label "gate $id"

    try {
        $child = Start-Process -FilePath 'pwsh' -ArgumentList $argList -WindowStyle Hidden -PassThru `
                               -RedirectStandardOutput $logFile -RedirectStandardError $errFile
        $script:SpawnedGatePids.Add([int]$child.Id)
    } finally {
        if ($fireHeld) {
            Start-Sleep -Seconds 15
            try { $fireMutex.ReleaseMutex() } catch { }
            Write-Log "STAGGER released id=$id"
        }
        if ($fireMutex) { $fireMutex.Dispose() }
    }

    while (-not $child.HasExited) {
        Start-Sleep -Seconds $TickSec
        Update-ActivityRecord -Label "gate $id"
        $eds = @()
        try { $eds = @(Get-LiveEditor -Checkout $repo -Map (Get-ProcSnapshot)) } catch { }
        if ($eds.Count -eq 0) { continue }

        Write-Log "ABORT id=$id reason=editor-reopened pid=$($child.Id)"
        & taskkill /F /T /PID $child.Id *> $null
        try {
            foreach ($g in @(Get-ReapableGodot -Checkout $repo -Map (Get-ProcSnapshot) -OwnRootPid $child.Id)) {
                $gp = Get-ProcPid $g
                if ($gp -gt 0) { & taskkill /F /T /PID $gp *> $null }
            }
        } catch { Write-Log "WARN post-abort sweep: $_" }
        # No result file: an aborted child's exit code is not a verdict. The
        # request stays pending and re-fires on the next quiet tick.
        return
    }

    $child.WaitForExit()
    $script:SpawnedGatePids.Remove([int]$child.Id)
    $exit    = $child.ExitCode
    $logTxt  = ''
    try { $logTxt = Get-Content -Path $logFile -Raw -ErrorAction SilentlyContinue } catch { }
    if ($null -eq $logTxt) { $logTxt = '' }

    $runHead = ''; $runDig = ''
    $tm = [regex]::Match($logTxt, 'TREE head=(\S+) digest=(\S+)')
    if ($tm.Success) { $runHead = $tm.Groups[1].Value; $runDig = $tm.Groups[2].Value }

    $verdict = 'UNKNOWN'
    $vm = [regex]::Matches($logTxt, 'VERDICT=(\S+)')
    if ($vm.Count -gt 0) { $verdict = $vm[$vm.Count - 1].Groups[1].Value }

    # Digests cross the process boundary abbreviated in the TREE line, so parity is a prefix
    # test — Test-DigestMatch (GateQueue.ps1) is that test, shared with the gate.
    $delta = -not (Test-DigestMatch $runDig $reqDig)

    Write-Log "DONE id=$id exit=$exit verdict=$verdict treeDelta=$delta"

    # 6 = INCOMPLETE, 7 = QUEUED and 8 = CONTENTION all mean precisely "the
    # machine was busy" — 7 is the fired run's own preflight seeing a live peer
    # gate (likelier now that the preflight check is machine-wide). Re-queue
    # rather than hand the user a contention artifact dressed as a regression —
    # capped, so a permanently busy machine reports rather than loops.
    if ($exit -in @(6, 7, 8) -and $attempt -lt $MaxAttempts) {
        Write-Log "REQUEUE id=$id exit=$exit attempt=$attempt"
        # The gate's Complete-Gate has already written <id>.result.json (single writer), which
        # makes Get-PendingRequests skip this request — delete the record so it returns to
        # pending and re-fires on the next quiet tick. Capped by $MaxAttempts above.
        #
        # Folded siblings go with it. Complete-Gate satisfies every pending request on the same
        # tree from one run, so a contention exit (6/7/8) propagated a "machine was busy"
        # non-verdict to sessions that never fired anything; leaving those behind would strand
        # them on an artifact this very branch is declaring untrustworthy.
        $foldP = Join-Path $queueDir "$id.result.json"
        try {
            $prev = Get-Content -Path $foldP -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
            foreach ($fid in @(Get-Prop $prev 'foldedIds' -Default @())) {
                if (-not $fid) { continue }
                Remove-Item -Path (Join-Path $queueDir "$fid.result.json") -Force -ErrorAction SilentlyContinue
                Write-Log "REQUEUE-FOLD id=$fid (released with $id)"
            }
        } catch { }
        Remove-Item -Path $foldP -Force -ErrorAction SilentlyContinue
        return
    }

    # Completed-run records are written by the GATE itself (Complete-Gate — the single writer,
    # via -QueueId): full digests, per-run detail path, mode/source/baselineAction, and
    # treeDelta computed against the request file. The watcher writes ONLY the watcher-error
    # path (GIVE-UP above) that no gate can produce.
}

# ---------------------------------------------------------------- main
# One watcher per checkout. Held for this process's whole lifetime, so a gate
# probing it with WaitOne(0) correctly reads "a watcher is alive".
$guard = $null
$held  = $false
try {
    $guard = New-Object System.Threading.Mutex($false, "Global\{{PROJECT_NAME}}-gatequeue-$(Get-WorktreeId $repo)")
    try { $held = $guard.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
} catch {
    Write-Log "ABORT could not open guard mutex: $_"
    exit 0
}
if (-not $held) { if ($guard) { $guard.Dispose() }; exit 0 }

try {
    Start-ActivityRecord
    Write-Log "START pid=$PID repo=$repo"
    $idleSince = Get-Date
    while ($true) {
        Start-Sleep -Seconds $TickSec
        try {
            Update-ActivityRecord -Label 'idle'
            $pending = @(Get-PendingRequests)
            if ($pending.Count -eq 0) {
                if (((Get-Date) - $idleSince).TotalMinutes -ge $IdleExitMin) {
                    Write-Log 'EXIT reason=idle'
                    break
                }
                continue
            }
            $idleSince = Get-Date
            if (-not (Test-CheckoutQuiet)) { continue }
            # Serially, oldest first — the machine can host exactly one run.
            Invoke-QueuedGate -Req $pending[0]
            Invoke-QueueSweep
        } catch {
            Write-Log "ERROR tick: $_"
            Start-Sleep -Seconds $TickSec
        }
    }
} finally {
    Stop-ActivityRecord
    try { $guard.ReleaseMutex() } catch { }
    try { $guard.Dispose() }     catch { }
}
exit 0
