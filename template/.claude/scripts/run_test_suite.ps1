#requires -Version 7
<#
.SYNOPSIS
  Hang-proof GdUnit4 suite runner. Bounds each attempt by -TimeoutMs and runtime boot progress,
  with one retry and a separately bounded lock wait.

.WHY
  Bare `dotnet test` run through the Bash tool can hang the caller FOREVER, past its
  own timeout: a wedged run has already spawned testhost -> headless Godot, and those
  grandchildren INHERIT the tool's stdout/stderr pipe. Killing the direct child leaves
  Godot alive holding the pipe's write-end open, so the tool blocks on a read that never
  hits EOF. This wrapper breaks that two ways:
    1. An isolated pwsh launches dotnet with file-redirection. Start-Process redirection alone
       inherits handles, so it is unsafe in the wrapper that holds the caller's stdout pipe.
    2. A boot watchdog and a hard wall-clock cap tree-kill a wedged attempt; each hang's
       Godot log survives the one retry. The script then returns a small summary.

.CONTENTION
  gdUnit4's connect pipe is salted per worktree (GDUNIT4_PIPE_SUFFIX + the forked gdUnit4.api).
  A cold boot >10s trips the hardcoded 10s ConnectAsync -> a silent-skip (green-looking low
  count + the GodotRuntimeExecutor/Connection-timeout WARN). Locking lives in RuntimeSlots.ps1
  (lock names, slot count, probes).
  Overlap signatures, triggers and rollback: .claude/skills/testing/reference/running.md
  §Runtime slots and parallel dev.
  Both tiers: a single auto-retry-on-silent-skip absorbs cold-boot/orphan cases (the failed
  attempt warms the cache + frees the pipe), and the pre-flight kill-sweep is WORKTREE-SCOPED
  -- it reaps only processes attributable to this repo root (testhost/vstest by image path or
  command line; headless Godot by parent chain) plus unattributable orphans, never a peer
  worktree's in-flight run.

  LOCK-EXPIRY CONTRACT: when the lock window expires, the wrapper emits LOCKWAIT_MS=<full
  wait> + STATUS=LOCK_TIMEOUT lock=<checkout|runtime-slots> and exits 125 -- a live holder owns
  the lock and the run never started (no counts line follows). The gate's LOCKED tier routes this to the queue (never
  INVALID); DIRECT callers read the status line and retry when the machine is quiet -- never
  as a test failure.

.NOTES
  Runs on Windows and Linux (Stop-ProcessTree, -Display for xvfb).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, ParameterSetName = 'Run')] [string] $Filter,
    # Classify what the reaper WOULD do against the live process table, print the REAP line, kill
    # nothing, exit 0. The verification surface for the kill/spare policy: a spare path can be proven
    # against a synthetic peer instead of by risking a real one. Takes no locks, runs read-only.
    [Parameter(Mandatory, ParameterSetName = 'ReapReport')] [switch] $ReapReport,
    [string] $Label = $Filter,
    [string] $Repo = '',
    # Omitted: target durations or last-green wall with headroom, at least 150 s.
    [ValidateRange(1, [int]::MaxValue)] [int] $TimeoutMs,
    # Logic-domain tier: the filtered suite spawns NO headless Godot, so it needs neither a
    # runtime slot nor the drain. Default off = today's runtime-safe behavior.
    [switch] $NoGodotRuntime,
    # A ceiling that can only LOWER the runtime slot count (RuntimeSlots.ps1); 0 means no ceiling
    # and a value above the slot maximum clamps. -SlotCount 1 runs exclusively: the rollback's
    # discrimination re-run.
    [ValidateRange(0, [int]::MaxValue)] [int] $SlotCount = 0,
    # Escape hatch for the entry editor guard below. The gate's held children pass it; the pool's -OnPoll owns the
    # mid-run editor check.
    [switch] $IgnoreEditor,
    # Print the checkout this run would build in (ROUTE=<path>) and exit 0 before any reap or build.
    # A live checkout's twin is synced first, which takes the twin's checkout lock.
    [switch] $RouteOnly,
    # Makes this run self-contained under <ResultsRoot>/<label>/: its own TRX (<label>.trx), Godot log
    # (godot_test.log), pipe suffix and APPDATA leaf (<worktreeId>-<Get-RunTag -Label>), and a failure listing that
    # reads that TRX. Prints TRX= and GODOT_LOG=. Absent: the fixed TestResults\ paths and the per-checkout isolation.
    [string] $ResultsRoot = '',
    # A gate's held child (RunPool.ps1 Start-SuiteChild -Held). Requires -ResultsRoot and a checkout record whose live
    # holder is this run's ancestor and has stamped built (RuntimeSlots.ps1 Get-HeldCheckoutDenials); refused with
    # STATUS=HELD_REFUSED and exit 122 otherwise. Takes a runtime slot only (the holder has the checkout lock), derives
    # --no-build from built and GDUNIT4_SKIP_RECOMPILE=1 from recompiled, skips the checkout-wide reap and pipe drain,
    # and reaps only its own process tree, plus its run dir's Godot before a retry. Agents never pass it: the flag exists
    # for the gate's pool.
    [switch] $HeldCheckout,
    # The pool's single held child admitted before the checkout is stamped recompiled: the gate's Sanity run, which
    # performs the one GdUnit4 editor pass. Valid only with -HeldCheckout.
    [switch] $RecompilePass,
    [switch] $ForceRecompile,
    # Run the engine windowed whatever the filter selects. Without it the run is headless unless every class the filter
    # selects is [RequiresRenderer]-tagged (IntegrationBatches.ps1 Resolve-RunMode).
    [switch] $Windowed,
    # The mode the caller decided (the pool forwards its planner's choice). Unset, the mode is derived from -Filter.
    [ValidateSet('headless', 'windowed')] [string] $RunMode,
    # dotnet test's console verbosity. normal prints a line per test, so a hang names its last test in the batch log.
    [ValidateSet('quiet', 'minimal', 'normal', 'detailed', 'diagnostic')] [string] $Verbosity = 'quiet'
)

. (Join-Path $PSScriptRoot 'WideRunGuard.ps1')
Assert-WideRunAllowed -Entry 'run_test_suite.ps1' -Repo $Repo -Filter $Filter -Flags @(@('ReapReport', 'RouteOnly') | Where-Object { $PSBoundParameters.ContainsKey($_) })
$moduleSw = [System.Diagnostics.Stopwatch]::StartNew()
$runStartedAt = Get-Date
# Godot process identity + checkout attribution live in ONE home; this wrapper consumes them.
. (Join-Path $PSScriptRoot 'GodotProcess.ps1')
# The checkout lock, runtime slots, overlap signatures and trigger log live in ONE home.
. (Join-Path $PSScriptRoot 'RuntimeSlots.ps1')
. (Join-Path $PSScriptRoot 'TwinCheckout.ps1')
. (Join-Path $PSScriptRoot 'IntegrationBatches.ps1')
# The activity-registry reader and the owner-root walk, shared with the gate and the watcher.
. (Join-Path $PSScriptRoot 'PeerActivity.ps1')

function Get-SuiteTreeDigest {
    param([string] $Repo, [string] $DigestScript)
    try {
        . $DigestScript
        (Get-TreeDigest -Repo $Repo).digest
    } catch { '' }
}

function Save-SuiteGreen {
    param([string] $Repo, [string] $Path, [string] $Trx, [string] $Filter, [double] $ElapsedSec,
          [datetime] $StartedAt, [string] $TreeDigest, [string] $DigestScript)
    $record = @{ filter = $Filter; elapsedSec = $ElapsedSec }
    if ($TreeDigest -and (Test-Path -LiteralPath $Trx) -and
        (Get-Item -LiteralPath $Trx).LastWriteTimeUtc -ge $StartedAt.ToUniversalTime()) {
        $endDigest = Get-SuiteTreeDigest -Repo $Repo -DigestScript $DigestScript
        if ($endDigest -and $TreeDigest -eq $endDigest) {
            $record.treeDigest = $TreeDigest
            $record.startedAt = $StartedAt.ToUniversalTime().ToString('o')
            $record.finishedAt = (Get-Date).ToUniversalTime().ToString('o')
            $record.trxPath = [IO.Path]::GetFullPath($Trx)
            $record.trxSha256 = (Get-FileHash -LiteralPath $Trx -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $temp = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $record | ConvertTo-Json | Set-Content -LiteralPath $temp -Encoding utf8
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}

# One line per pre-work phase, so a phase's share of the run's wall is measured, not guessed.
function Write-PhaseMs {
    param([string] $Name, [int64] $Ms)
    Write-Output ("PHASE_MS name={0} ms={1}" -f $Name, $Ms)
}
if (-not $ReapReport) { Write-PhaseMs -Name 'module-load' -Ms $moduleSw.ElapsedMilliseconds }

$ErrorActionPreference = 'Stop'
$repo = Resolve-TestRepo -Repo $Repo -ScriptsRoot $PSScriptRoot
Set-Location $repo

# --- Held-mode validity, before anything that locks, reaps or builds ---------------------------
# A refusal prints one STATUS line and exits 122; called as a statement so no capture swallows that line.
# Get-HeldCheckoutDenials is the one predicate, shared with Enter-RunLocks -HeldCheckout.
function Invoke-HeldPreCheck {
    param([string] $Repo, [string] $Label, [string] $ResultsRoot, [bool] $Held, [bool] $RecompilePass)
    if ($RecompilePass -and -not $Held) {
        Write-Output "STATUS=HELD_REFUSED  label=$Label  unmet=recompile-pass-without-held-checkout"
        exit 122
    }
    if (-not $Held) { return }
    if (-not $ResultsRoot) {
        Write-Output "STATUS=HELD_REFUSED  label=$Label  unmet=held-checkout-without-results-root"
        exit 122
    }
    $map = Get-ProcSnapshot
    $denials = @(Get-HeldCheckoutDenials -Repo $Repo -RecompilePass:$RecompilePass -Map $map)
    if ($denials.Count -gt 0) {
        Write-Output "STATUS=HELD_REFUSED  label=$Label  $(Format-HeldCheckoutDenial -Repo $Repo -Denials $denials -Map $map)"
        exit 122
    }
}
if (-not $ReapReport) {
    Invoke-HeldPreCheck -Repo $repo -Label $Label -ResultsRoot $ResultsRoot -Held $HeldCheckout.IsPresent -RecompilePass $RecompilePass.IsPresent
}
$heldRecord = if ($HeldCheckout -and -not $ReapReport) { Read-SlotRecord -RecordDir (Get-RuntimeSlotRecordDir) -Name (Get-CheckoutRecordName -Repo $repo) } else { $null }
# The holder built this tree and Assert-TreeStable guards it, so a held child derives --no-build from the record's
# built stamp; agents never pass --no-build. recompiled means the holder's one editor pass already ran.
$noBuild = [bool]($heldRecord -and $heldRecord.built -eq $true)
$skipRecompile = [bool]($heldRecord -and $heldRecord.recompiled -eq $true)

# --- Entry editor guard ------------------------------------------------------------------
# `dotnet test` builds into .godot\mono\temp\bin\Debug\, a SINGLE output shared with the Godot
# editor (the Godot SDK pins OutputPath; {{PROJECT_NAME}}.csproj cannot override it). A run and a live
# editor on this checkout are therefore mutually destructive -- we hold {{PROJECT_NAME}}.dll and its
# build fails, or it rebuilds under us and strips .tres scripts. So a live checkout's run builds in its twin
# (TwinCheckout.ps1); only a twin that cannot be synced leaves the old refusal.
if (-not $IgnoreEditor -and -not $ReapReport -and -not $HeldCheckout) {
    $route = Resolve-RunCheckout -Repo $repo
    if ($route.Failed) {
        Write-Output "STATUS=EDITOR_OPEN  label=$Label  (editor/playtest live on this checkout and its twin could not be synced; refuse rather than race its build -- wait for it to close)"
        exit 126
    }
    $repo = $route.Path
    Set-Location $repo
}
$modeChoice = if ($ReapReport) { $null } else { Resolve-RunMode -Repo $repo -Filter $Filter -Windowed:$Windowed -Declared "$RunMode" }
if ($modeChoice) {
    Write-Output "MODE=$($modeChoice.mode)"
    if (@($modeChoice.skipped).Count -gt 0) {
        Write-Output "RENDER_SKIPPED classes=$(@($modeChoice.skipped) -join ',')  (this filter mixes renderer-tagged and untagged classes, so it runs headless and these are not rendered; filter to only tagged classes or pass -Windowed)"
    }
}
if ($RouteOnly) {
    Write-Output "ROUTE=$repo"
    exit 0
}
# .runsettings is gitignored, so a fresh linked worktree has none; dotnet test would then exit 1 with
# the reason only in the err log. Initialize-Runsettings generates it from the template; the refusal stands when it cannot.
$runsettings = if ($ReapReport) { '' } else { Initialize-Runsettings -Repo $repo }
if ($runsettings -like 'FAILED*') {
    Write-Output "STATUS=NO_RUNSETTINGS  label=$Label  reason=$($runsettings -replace '^FAILED\s*', '')  (no .runsettings in $repo)"
    exit 126
}

$logDir = Get-SuiteLogDir -Repo $repo
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$runRoot = if ($ResultsRoot) { [System.IO.Path]::GetFullPath($ResultsRoot, $repo) } else { '' }
# A many-domain verify scope makes a label hundreds of characters long; Start-Process fails on a path past Windows'
# limit. GodotProcess.ps1 owns the cap, so the run pool names this run's wrapper.out dir the same way.
$safe = Get-SafeRunLabel -Label $Label -Repo $repo -ResultsRoot $ResultsRoot
$log    = Join-Path $logDir "$safe.log"
$errLog = Join-Path $logDir "$safe.err.log"
$runDir   = if ($runRoot) { Join-Path $runRoot $safe } else { '' }
$trxPath  = if ($runRoot) { Join-Path $runDir "$safe.trx" } else { '' }
$passTrx = if ($trxPath) { $trxPath } else { Join-Path $repo "TestResults\$safe.trx" }
$godotLog = if ($runRoot) { Join-Path $runDir 'godot_test.log' } else { Join-Path $repo 'TestResults\godot_test.log' }
$lastGreen = Join-Path $logDir "$safe.green"
if (-not $PSBoundParameters.ContainsKey('TimeoutMs')) {
    $TimeoutMs = Get-SuiteTimeoutMs -Repo $repo -Filter $Filter -LastGreen $lastGreen
}
if (-not $PSBoundParameters.ContainsKey('TimeoutMs') -and -not $HeldCheckout -and -not $ReapReport -and -not (Get-RecompileDecision -Repo $repo).Skip) {
    $TimeoutMs += $script:SuiteColdRecompileExtraMs
}
if (-not $ReapReport) { Write-Output "CAP_MS=$TimeoutMs" }

# --- Activity registry (machine-global, advisory) -----------------------------------------
# Lets a peer session NAME what is blocking it instead of guessing -- a contention kill is not a
# regression. Advisory only: every touch is best-effort and can never fail the run.
# The record's owner root (the Claude Code process owning the run) is what separates "my session's run" from a
# concurrent session's in one checkout. A gate passes its own root to every child (HARNESS_OWNER_ROOT_PID /
# HARNESS_OWNER_ROOT_START): a child of a detached gate has no session in its own chain. An absent or stale pair falls
# back to this process's own walk (PeerActivity.ps1 Get-OwnerRoot, which reads no environment).
function Get-RunOwnerRoot {
    $rootPid = 0
    if ([int]::TryParse([string] $env:HARNESS_OWNER_ROOT_PID, [ref] $rootPid) -and
        (Test-SlotProcessLive $rootPid $env:HARNESS_OWNER_ROOT_START)) {
        return [pscustomobject] @{ RootPid = $rootPid; RootStart = [string] $env:HARNESS_OWNER_ROOT_START }
    }
    Get-OwnerRoot
}
$script:ActivityFile = $null
$script:ActivityRec  = $null
function Write-ActivityRecord {
    try {
        if (-not $script:ActivityFile -or -not $script:ActivityRec) { return }
        $script:ActivityRec.heartbeatAt = (Get-Date).ToUniversalTime().ToString('o')
        $script:ActivityRec | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $script:ActivityFile -Encoding utf8
    }
    catch { }
}
try {
    # A dry run must not announce itself as a live suite — a peer's gate reads this registry and
    # would wait behind a process that is doing nothing.
    if ($ReapReport) { throw 'skip' }
    $actDir = Join-Path (Get-HarnessTempRoot) 'harness-activity'
    New-Item -ItemType Directory -Force -Path $actDir | Out-Null
    $script:ActivityFile = Join-Path $actDir "suite-$PID.json"
    $owner = Get-RunOwnerRoot
    $script:ActivityRec  = [ordered] @{
        pid         = $PID
        procStart   = (Get-ProcessStartStamp -ProcessId $PID).ToString('o')
        kind        = 'suite'
        checkout    = $repo
        sessionId   = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $null })
        ownerRootPid   = $(if ($owner) { $owner.RootPid }   else { $null })
        ownerRootStart = $(if ($owner) { $owner.RootStart } else { $null })
        label       = $Label
        startedAt   = (Get-Date).ToUniversalTime().ToString('o')
        heartbeatAt = $null
        expectedSec = [int] ($TimeoutMs / 1000)
    }
    Write-ActivityRecord
}
catch { $script:ActivityFile = $null }

# PIDs belonging to a live PEER SESSION's harness run, plus every descendant, which this
# script must never kill.
#
# Get-ProcVerdict answers "which CHECKOUT owns this process", so it returns MINE for a peer
# session sharing our checkout — two concurrent sessions in one working tree are invisible to
# it by construction. That made Clear-TestRunners reap a peer session's in-flight run as if it
# were our own orphan (observed 2026-08-13: a concurrent execution arm's suite was tree-killed
# mid-run). The activity registry already carries the missing axis: every live gate/suite record
# is a run with a live owner, read through PeerActivity.ps1 Get-LivePeerActivity, and the reaper
# only has to honour it.
# PIDs in THIS process's own tree (self + descendants). The reaper's licence is scoped to
# these plus true orphans: anything else has a live owner that is not us.
#
# This is the guard that needs no cooperation from the peer. An execution arm that runs
# `dotnet test` directly writes no activity record, so Get-PeerProtectedPids cannot see it —
# but its wrapper still has a live parent outside our tree, which is enough to spare it.
#
# This is the same two-axis policy Get-ReapableGodot documents for the Godot side
# ("ours by construction, or Orphan"); Clear-TestRunners had simply never applied it to the
# testhost/wrapper side, deciding on Get-ProcVerdict's checkout axis instead — which cannot
# separate two sessions sharing one working tree.
function Get-SelfTreePids {
    param([hashtable] $Map, [int] $RootPid = $PID)
    $mine = [System.Collections.Generic.HashSet[int]]::new()
    $children = @{}
    foreach ($ci in $Map.Values) {
        $ppid = 0; try { $ppid = [int] $ci.ParentProcessId } catch { }
        if ($ppid -gt 0) {
            if (-not $children.ContainsKey($ppid)) { $children[$ppid] = [System.Collections.Generic.List[int]]::new() }
            $children[$ppid].Add([int] $ci.ProcessId)
        }
    }
    $stack = [System.Collections.Generic.Stack[int]]::new()
    $stack.Push($RootPid)
    while ($stack.Count -gt 0) {
        $id = $stack.Pop()
        if (-not $mine.Add($id)) { continue }
        if ($children.ContainsKey($id)) { foreach ($c in $children[$id]) { $stack.Push($c) } }
    }
    # Unary comma: `return` writes to the output stream, which UNROLLS a collection. A one-element
    # set would arrive at the caller as a bare [int], and `.Contains()` on it throws — aborting the
    # reaper mid-classification.
    return , $mine
}

function Get-PeerProtectedPids {
    param([hashtable] $Map, [string] $Repo)
    $protected = [System.Collections.Generic.HashSet[int]]::new()
    # pid -> "label='x' session=y", so a spare can NAME what it declined to kill instead of
    # printing a bare number the reader has to go identify themselves.
    $script:PeerLabels = @{}
    $roots = [System.Collections.Generic.List[int]]::new()
    $rootLabel = @{}
    # No ownership axis, deliberately (activity_registry.py): the reaper spares every live record but its own.
    foreach ($rec in @(Get-LivePeerActivity -OwnRoots @($PID) -Map $Map)) {
        $rpid = [int] $rec.pid
        $roots.Add($rpid)
        $sidRaw = if ($rec.PSObject.Properties['sessionId']) { [string] $rec.sessionId } else { '' }
        $sid = if ($sidRaw) { $sidRaw.Substring(0, [math]::Min(8, $sidRaw.Length)) } else { 'n/a' }
        $label = if ($rec.PSObject.Properties['label']) { $rec.label } else { '' }
        $rootLabel[$rpid] = "label='$label' session=$sid"
    }
    if ($roots.Count -eq 0) { return , $protected }

    # Expand to descendants: the record names the runner, but /T would take its whole tree.
    $children = @{}
    foreach ($ci in $Map.Values) {
        $ppid = 0; try { $ppid = [int] $ci.ParentProcessId } catch { }
        if ($ppid -gt 0) {
            if (-not $children.ContainsKey($ppid)) { $children[$ppid] = [System.Collections.Generic.List[int]]::new() }
            $children[$ppid].Add([int] $ci.ProcessId)
        }
    }
    # Carry the owning record's label down the tree: a spared grandchild must name the peer run it
    # belongs to, not just the root that announced it.
    $stack = [System.Collections.Generic.Stack[object]]::new()
    foreach ($r in $roots) { $stack.Push(@($r, $rootLabel[$r])) }
    while ($stack.Count -gt 0) {
        $item = $stack.Pop()
        $id = [int] $item[0]
        if (-not $protected.Add($id)) { continue }
        $script:PeerLabels[$id] = [string] $item[1]
        if ($children.ContainsKey($id)) { foreach ($c in $children[$id]) { $stack.Push(@($c, $item[1])) } }
    }
    return , $protected
}

# --- Clear stale test-runner trees BEFORE launching (never racing the launch). ---------
# Tree-kill any surviving dotnet-test / vstest / testhost wrapper (its /T takes the child
# Godot with it); then an editor-safe headless-Godot backstop. This wrapper's own name
# (run_test_suite.ps1) matches none of these patterns, so it can't kill itself. Scoped to
# $Repo: a peer worktree's in-flight run is SPARED, orphans are not — and so is a peer
# SESSION's run in this same checkout, via Get-PeerProtectedPids.
# One line per reaping site, ALWAYS printed — including at 0/0. A site that reaped nothing and a site
# that never ran are different facts, and only the printed line separates them: an ABSENT REAP line
# means that site was never reached, never "nothing was killed". This is the whole reason the line
# exists; `PREFLIGHT orphans_killed=0` counted one site and read as a whole-run guarantee.
function Write-ReapLine {
    param([string] $Site, $Kills, $Spares)
    Write-Output ("REAP site={0} killed={1} spared={2}" -f $Site, $Kills.Count, $Spares.Count)
    foreach ($k in $Kills)  { Write-Output ("  kill  {0} {1} {2}" -f $k.pid, $k.name, $k.reason) }
    foreach ($s in $Spares) {
        Write-Output ("  spare {0} {1} {2}{3}" -f $s.pid, $s.name, $s.reason, $(if ($s.detail) { "  $($s.detail)" } else { '' }))
    }
}

function Clear-TestRunners {
    param([string] $Repo, [string] $Site = 'suite', [switch] $DryRun)
    $hosts  = @(Get-Process -Name 'testhost', 'vstest.console' -ErrorAction SilentlyContinue)
    $map    = Get-ProcSnapshot
    # The first drain pass reuses this snapshot (Get-DrainWaitSet refreshes it when a live candidate is missing).
    $script:ReapMap = $map
    $spare  = Get-PeerProtectedPids -Map $map -Repo $Repo
    # Positive identity, not a title negation: only roles the kill policy admits are candidates.
    $godots = @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue | Where-Object {
                    $ci = $map[$_.Id]
                    $ci -and (Get-GodotRole -Proc $ci -Map $map) -in @('TestRunner', 'Orphan')
                })
    $kill   = [System.Collections.Generic.List[int]]::new()

    foreach ($p in $hosts) {
        $ci = $map[$p.Id]
        if ($ci -and (Get-ProcVerdict $ci $map $Repo 0) -ne 'PEER') { $kill.Add($p.Id) }
    }
    foreach ($p in $godots) {
        $ci = $map[$p.Id]
        if ($ci -and (Get-ProcVerdict $ci $map $Repo 3) -ne 'PEER') { $kill.Add($p.Id) }
    }
    # Detached dotnet-test wrappers by CommandLine (best-effort top-up).
    foreach ($ci in $map.Values) {
        if ($ci.CommandLine -and ($ci.CommandLine -match 'vstest\.console' -or $ci.CommandLine -match 'test\s+--settings') -and
            (Get-ProcVerdict $ci $map $Repo 0) -ne 'PEER') { $kill.Add([int] $ci.ProcessId) }
    }
    # Two independent spares, both required. The record-based one covers a peer harness run that
    # announced itself; the tree-based one covers everything else, including a bare `dotnet test`
    # an execution arm launched directly. A candidate survives the reaper unless it is ours or
    # parentless — never merely because it sits in our checkout.
    $mine   = Get-SelfTreePids -Map $map
    $kills  = [System.Collections.Generic.List[object]]::new()
    $spares = [System.Collections.Generic.List[object]]::new()
    foreach ($id in ($kill | Sort-Object -Unique)) {
        $ci = $map[$id]
        $nm = if ($ci -and $ci.Name) { [string] $ci.Name -replace '\.exe$', '' } else { '?' }
        if ($spare.Contains($id)) {
            $spares.Add([pscustomobject] @{ pid = $id; name = $nm; reason = 'peer-session'; detail = $script:PeerLabels[$id] }); continue
        }
        if ($mine.Contains($id)) {
            $kills.Add([pscustomobject] @{ pid = $id; name = $nm; reason = 'own-tree' }); continue
        }
        if ($ci -and (Get-ProcVerdict $ci $map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') {
            $kills.Add([pscustomobject] @{ pid = $id; name = $nm; reason = 'orphan' }); continue
        }
        # Not ours, not parentless. Separate the two live-owner cases: a different CHECKOUT is a peer
        # worktree; anything else is an owner we cannot name (an unannounced arm, or the user's own run).
        $reason = if ($ci -and (Get-ProcVerdict $ci $map $Repo 1) -eq 'PEER') { 'peer-worktree' } else { 'live-owner' }
        $spares.Add([pscustomobject] @{ pid = $id; name = $nm; reason = $reason; detail = $null })
    }
    Write-ReapLine -Site $Site -Kills $kills -Spares $spares
    if (-not $DryRun) { foreach ($k in $kills) { Stop-ProcessTree -ProcessId $k.pid } }
}

# A held child's reaper: the test-runner candidates (testhost, vstest, Godot, dotnet-test wrappers) in $RootPid's own
# tree are killed; every other candidate, a sibling held child of the same gate included, is spared. No checkout axis:
# the gate's children share this checkout, so only the tree tells them apart.
function Get-OwnTreeReap {
    param([hashtable] $Map, [int] $RootPid)
    $mine = Get-SelfTreePids -Map $Map -RootPid $RootPid
    $kills = [System.Collections.Generic.List[object]]::new()
    $spares = [System.Collections.Generic.List[object]]::new()
    foreach ($ci in @($Map.Values | Sort-Object { [int] $_.ProcessId })) {
        $id = [int] $ci.ProcessId
        if ($id -eq $RootPid) { continue }
        $nm = [string] $ci.Name -replace '\.exe$', ''
        $cmd = [string] $ci.CommandLine
        if (-not ($nm -match '^(testhost|vstest\.console|Godot.*)$' -or $cmd -match 'vstest\.console|test\s+--settings')) { continue }
        if ($mine.Contains($id)) { $kills.Add([pscustomobject] @{ pid = $id; name = $nm; reason = 'own-tree' }) }
        else { $spares.Add([pscustomobject] @{ pid = $id; name = $nm; reason = 'not-own-tree'; detail = $null }) }
    }
    [pscustomobject] @{ Kills = $kills; Spares = $spares }
}

function Clear-OwnTree {
    param([string] $Site, [switch] $DryRun)
    $r = Get-OwnTreeReap -Map (Get-ProcSnapshot) -RootPid $PID
    Write-ReapLine -Site $Site -Kills $r.Kills -Spares $r.Spares
    if (-not $DryRun) { foreach ($k in $r.Kills) { Stop-ProcessTree -ProcessId $k.pid } }
}

# A held child's Godot can outlive its test host (a connect timeout leaves it serving the pipe), and once that host
# exited Clear-OwnTree cannot reach it. Its --log-file still names this run's dir, so a retry kills it there before it
# rebinds the same pipe.
function Clear-RunDirGodot {
    param([string] $RunDir, [string] $Site, [hashtable] $Map, [switch] $DryRun)
    $kills = @(Get-RunDirGodot -RunDir $RunDir -Map $Map | ForEach-Object {
        [pscustomobject] @{ pid = [int] $_.ProcessId; name = [string] $_.Name -replace '\.exe$', ''; reason = 'run-dir' } })
    Write-ReapLine -Site $Site -Kills $kills -Spares @()
    if (-not $DryRun) { foreach ($k in $kills) { Stop-ProcessTree -ProcessId $k.pid } }
}

# The consumed gdUnit4.api ignores GDUNIT4_SKIP_RECOMPILE silently unless it carries the fork patch, whose positive
# signal is this line in the run's own TRX run-level StdOut (it never reaches dotnet test stdout). Without it the run
# fails loud: an ignored opt-out brings back the concurrent editor-pass race. A missing or unparseable TRX, or one that
# executed no test, proves nothing either way and is left to the caller's TRX checks.
function Assert-RecompileSkipped {
    param([string] $Trx, [string] $Label)
    if (-not $Trx -or -not (Test-Path -LiteralPath $Trx)) { return }
    try { [xml] $x = Get-Content -LiteralPath $Trx -Raw } catch { return }
    $node = $x.SelectSingleNode("/*[local-name()='TestRun']/*[local-name()='ResultSummary']/*[local-name()='Output']/*[local-name()='StdOut']")
    if ($node -and $node.InnerText -match 'Skipping Godot project recompile') { return }
    # Only the Godot runtime runner has a recompile step; a run whose tests all took the direct runner never reaches it.
    if (-not ($node -and $node.InnerText -match 'Starting GodotRuntimeTestRunner')) { return }
    # A run that executed no test may have died before the runner reached its recompile step, so a missing line there
    # proves nothing; the caller's zero-result checks classify it.
    $counters = $x.SelectSingleNode("/*[local-name()='TestRun']/*[local-name()='ResultSummary']/*[local-name()='Counters']")
    if ($counters -and $counters.GetAttribute('executed') -eq '0') { return }
    Write-Output "STATUS=SKIP_RECOMPILE_IGNORED  label=$Label  trx=$Trx  (GDUNIT4_SKIP_RECOMPILE=1 was set but the run-level StdOut lacks 'Skipping Godot project recompile')"
    exit 127
}

# The test hosts and runner/orphan Godot processes the drain waits on: live candidates attributable to $Repo. $Map is
# reused while it covers every live candidate; a candidate it lacks (started after the snapshot) forces a fresh one,
# so reuse never drops a process from the wait.
function Get-DrainWaitSet {
    param([string] $Repo, [hashtable] $Map, [object[]] $Live, [scriptblock] $Snapshot = { Get-ProcSnapshot })
    if ($Live.Count -eq 0) { return }
    if (-not $Map -or @($Live | Where-Object { -not $Map.ContainsKey([int] $_.Id) }).Count -gt 0) { $Map = & $Snapshot }
    @($Live | Where-Object {
        $ci = $Map[[int] $_.Id]
        $isGodot = $_.ProcessName -like 'Godot*'
        $ci -and (-not $isGodot -or (Get-GodotRole -Proc $ci -Map $Map) -in @('TestRunner', 'Orphan')) -and
            (Get-ProcVerdict $ci $Map $Repo $(if ($isGodot) { 3 } else { 0 })) -ne 'PEER'
    })
}

# The settle lets the OS release a salted named pipe before the next run binds it. A unique-pipe run (-ResultsRoot:
# its suffix carries the run tag) binds a name no sibling holds, so its first attempt needs none; its retry rebinds its
# own pipe right after the failed attempt, and a shared-pipe run shares one name per checkout, so both still settle.
function Get-PipeSettleMs {
    param([bool] $UniquePipe, [int] $Attempt)
    if ($UniquePipe -and $Attempt -le 1) { 0 } else { 2500 }
}

# Block until no headless-Godot/testhost of OURS (or orphaned) remains, THEN settle so the OS
# releases this checkout's salted named pipe. Back-to-back runtime suites that skip this drain step
# fail to bind the pipe -> Mode-A silent-skip/wedge on the 2nd suite (observed 2026-05-31).
# Peer worktrees' live processes are ignored -- a healthy long peer run must not stall us.
function Wait-PipeDrained {
    param([string] $Repo, [Parameter(Mandatory)] [int] $SettleMs, [hashtable] $Map, [int] $TimeoutSec = 25)
    $phaseSw = [System.Diagnostics.Stopwatch]::StartNew()
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $live = @(Get-Process -Name 'testhost' -ErrorAction SilentlyContinue) + @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue)
        $ours = @(Get-DrainWaitSet -Repo $Repo -Map $Map -Live $live)
        $Map = $null
        if ($ours.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-PhaseMs -Name 'pipe-drain' -Ms $phaseSw.ElapsedMilliseconds
    $phaseSw.Restart()
    if ($SettleMs -gt 0) { Start-Sleep -Milliseconds $SettleMs }
    Write-PhaseMs -Name 'settle' -Ms $phaseSw.ElapsedMilliseconds
}

# FAILED_TEST=<fqn> and FAILED_MSG=<fqn> :: <first message line> for the first 15 failures of this run's own TRX.
# A TRX older than the run (a build or launch that failed before tests ran) belongs to an earlier run: NO_TRX instead.
function Write-FailureLines {
    param([string] $OwnTrx, [datetime] $Since)
    $trx = $OwnTrx
    if (-not $trx -or -not (Test-Path -LiteralPath $trx) -or (Get-Item -LiteralPath $trx).LastWriteTime -lt $Since) {
        Write-Output 'NO_TRX  this run wrote no test results (build or launch failed before tests ran): read LOG=, not an older TRX'
        return
    }
    try { [xml] $xml = Get-Content -LiteralPath $trx -Raw }
    catch { Write-Output "WARN=TRX_UNREADABLE path=$trx"; return }
    foreach ($r in @($xml.SelectNodes("//*[local-name()='UnitTestResult'][@outcome='Failed']") | Select-Object -First 15)) {
        Write-Output "FAILED_TEST=$($r.testName)"
        $msg = $r.SelectSingleNode(".//*[local-name()='Message']")
        $first = if ($msg) { ($msg.InnerText -split "`r?`n")[0].Trim() } else { '' }
        Write-Output "FAILED_MSG=$($r.testName) :: $first"
    }
}

# --- Locks: this checkout's lock, then a runtime slot for anything that spawns Godot. ------
# Lock model, slot count and override file: RuntimeSlots.ps1 (see .CONTENTION).
# NOTE: this project's Logic suite DOES spawn Godot (only ~388 of its tests are runtime-free)
# — pass -NoGodotRuntime ONLY for a filter that provably contains zero [RequireGodotRuntime]
# tests; it takes no runtime slot and skips the drain.
if ($ReapReport) {
    if ($HeldCheckout) { Clear-OwnTree -Site 'dry-run:own-tree' -DryRun }
    else { Clear-TestRunners -Repo $repo -Site 'dry-run' -DryRun }
    exit 0
}

# Acquired BEFORE Clear-TestRunners, so a waiting run never tree-kills a peer's in-flight Godot.
# An abandoned lock (holder died) is an acquisition; the slot handoff reaps its orphaned tree.
$bounds = Get-SuiteRunBounds -TimeoutMs $TimeoutMs   # GodotProcess.ps1: shared with the run pool's backstop
$lockWaitMs = $bounds.LockWaitMs
$slots = Get-RuntimeSlotCount -Repo $repo -Ceiling $SlotCount
$lease = $null

try {
    try {
        $lease = Enter-RunLocks -Repo $repo -Label $Label -Runtime:(-not $NoGodotRuntime) -SlotCount $slots.Count -TimeoutMs $lockWaitMs `
            -HeldCheckout:$HeldCheckout -RecompilePass:$RecompilePass
    } catch {
        if (-not $HeldCheckout) { throw }
        $msg = $_.Exception.Message
        Write-Output "STATUS=HELD_REFUSED  label=$Label  $(if ($msg -like 'held checkout refused:*') { $msg } else { "unmet=lock-error  error=$msg" })"
        exit 122
    }
    # Every path emits its lock-wait, 0 included (same discipline as COMPLETENESS): the batched
    # runner sums it across a batch's output and excludes it from the work budget, so contention
    # makes the gate SLOW, never a false count regression. An ABSENT line means this path was
    # never reached.
    Write-Output "LOCKWAIT_MS=$($lease.WaitMs)"
    Write-PhaseMs -Name 'lock' -Ms ($lease.WaitMs - $lease.SweepMs)
    Write-PhaseMs -Name 'slot-handoff-reap' -Ms $lease.SweepMs
    if (-not $lease.Acquired) {
        Write-Output "STATUS=LOCK_TIMEOUT  label=$Label  lock=$($lease.Blocker)  (a peer held $($lease.Blocker) > ${lockWaitMs}ms)"
        exit 125
    }
    Write-Output "SLOT=$(if ($lease.Slots) { $lease.Slots -join ',' } else { 'none' })/$($slots.Count) source=$($slots.Source) abandoned=$([int]$lease.Abandoned) peerAtStart=$([int]$lease.PeerSlotHeld)"
    if (-not $NoGodotRuntime) {
        $handoff = @($lease.Reaped | ForEach-Object { [pscustomobject]@{ pid = $_; name = 'dotnet'; reason = 'dead-slot-holder-runner' } })
        Write-ReapLine -Site 'slot-handoff' -Kills $handoff -Spares @()
    }

    $run = Resolve-RunRecompile -Repo $repo -Held:$HeldCheckout.IsPresent -HeldRecompiled:$skipRecompile -Force:$ForceRecompile.IsPresent -RecompilePass:$RecompilePass.IsPresent
    $recompileDecision = $run.Decision
    $skipRecompile = $run.Skip
    Write-Output $run.Line

    # --- Per-worktree isolation: pipe salt (consumed by the forked gdUnit4.api — appends
    #     to the pipe name so worktrees stop sharing one pipe) + ensure the --log-file
    #     target dir exists before Godot spawns (Godot does not create missing dirs). ----
    $iso = if ($ResultsRoot) { Get-TestIsolation -Repo $repo -Tag (Get-RunTag -Label $Label) } else { Get-TestIsolation -Repo $repo }
    $env:GDUNIT4_PIPE_SUFFIX = $iso.PipeSuffix
    $env:GDUNIT4_SKIP_RECOMPILE = if ($skipRecompile) { '1' } else { $null }
    New-Item -ItemType Directory -Force -Path $iso.AppData | Out-Null
    Write-Output "USERDIR=$($iso.AppData)"
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'TestResults') | Out-Null
    if ($ResultsRoot) {
        New-Item -ItemType Directory -Force -Path $runDir | Out-Null
        Write-Output "TRX=$trxPath"
        Write-Output "GODOT_LOG=$godotLog"
    }

    # --- Up to 2 attempts: a cold-boot / pipe-collision / orphan silent-skip self-heals on re-run
    #     (the failed attempt warms the .NET JIT + OS file cache and frees the pipe). ----
    $digestScript = Join-Path $PSScriptRoot 'TreeDigest.ps1'
    # Held gate children already have the gate's tree checks; only standalone narrow runs transfer here.
    $suiteTreeDigest = if (-not $HeldCheckout) { Get-SuiteTreeDigest -Repo $repo -DigestScript $digestScript } else { '' }
    $maxAttempts = $bounds.MaxAttempts
    $attempt = 0
    while ($true) {
        $attempt++
        $selfOverlap = $false
        Write-ActivityRecord
        $phaseSw = [System.Diagnostics.Stopwatch]::StartNew()
        # A held child's pipe is unique and its siblings are the gate's own runs: no checkout-wide reap, no drain.
        if (-not $HeldCheckout) { Clear-TestRunners -Repo $repo -Site "suite:$Label" }
        Write-PhaseMs -Name 'suite-reap' -Ms $phaseSw.ElapsedMilliseconds
        if ($HeldCheckout -and $attempt -gt 1) {
            Clear-RunDirGodot -RunDir $runDir -Site "run-dir:${Label}:retry"
            $settleMs = Get-PipeSettleMs -UniquePipe $true -Attempt $attempt
            Start-Sleep -Milliseconds $settleMs
            Write-PhaseMs -Name 'settle' -Ms $settleMs
        }
        if (-not $NoGodotRuntime -and -not $HeldCheckout) {
            Wait-PipeDrained -Repo $repo -SettleMs (Get-PipeSettleMs -UniquePipe ([bool]$ResultsRoot) -Attempt $attempt) -Map $script:ReapMap
        }
        Remove-Item -LiteralPath $passTrx -Force -ErrorAction SilentlyContinue

        Remove-Item -LiteralPath $godotLog -Force -ErrorAction SilentlyContinue
        # The native launcher redirects inside an isolated pwsh, never in this caller's process.
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $testArgs = @('test', '--settings', (Join-Path $repo '.runsettings'), '--verbosity', $Verbosity, '--filter', $Filter) +
            @(if ($noBuild) { '--no-build' }) +
            @('--results-directory', (Split-Path $passTrx), '--logger', "trx;LogFileName=$safe.trx") +
            @('--', $iso.AppDataArg, "GdUnit4.Parameters=--log-file $($godotLog -replace '\\', '/')$(if ($modeChoice.engineArgs) { ' ' + $modeChoice.engineArgs })",
                # .runsettings' own TestSessionTimeout would abort a run the cap allows; vstest must never stop first.
                "RunConfiguration.TestSessionTimeout=$([int64] $TimeoutMs * $script:SuiteProgressCeilingFactor + 60000)")
        $p = Start-IsolatedNativeProcess -FilePath 'dotnet' -Arguments $testArgs -OutPath $log -ErrPath $errLog -Display:(-not $NoGodotRuntime)
        $null = $p.Handle
        Set-RuntimeSlotRunner -Lease $lease -Process $p
        $attemptState = Wait-SuiteAttempt -Process $p -TimeoutMs $TimeoutMs -GodotLog $godotLog -NoGodotRuntime:$NoGodotRuntime
        $sw.Stop()
        $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

        if ($attemptState -ne 'exited') {
            # Both deadlines use the same retry and final HANG path; neither can become green.
            Stop-ProcessTree -ProcessId $p.Id
            Start-Sleep -Milliseconds 750
            if ($HeldCheckout) { Clear-OwnTree -Site "own-tree:${Label}:hang" }
            else { Clear-TestRunners -Repo $repo -Site "suite:$Label:hang" }
            if (-not $NoGodotRuntime -and -not $HeldCheckout) { Wait-PipeDrained -Repo $repo -SettleMs (Get-PipeSettleMs -UniquePipe $false -Attempt $attempt) }
            Save-HungGodotLog -GodotLog $godotLog -Attempt $attempt
            if ($attempt -lt $maxAttempts) {
                Write-Output "RETRY=HANG  label=$Label  attempt=$attempt  elapsed=${secs}s  reason=$attemptState  (tree-killed PID $($p.Id); re-running once)"
                continue
            }
            Write-Output "STATUS=HANG  label=$Label  elapsed=${secs}s  cap=${TimeoutMs}ms  attempts=$attempt  reason=$attemptState  (tree-killed PID $($p.Id))"
            Write-Output '---- last log lines ----'
            if (Test-Path $log) { Get-Content $log -Tail 8 -ErrorAction SilentlyContinue }
            exit 124   # conventional timeout exit code
        }

        $code = $p.ExitCode
        if ($HeldCheckout) { Clear-OwnTree -Site "own-tree:$Label" }
        # These are NOT one condition. Reporting them all as "connect failed" sends the reader after
        # the pipe when the cause is a RefCounted Free() in some suite's teardown, so name which
        # signature actually matched — ordered most-specific first.
        $skipSignatures = [ordered]@{
            'refcounted-free-fatal' = 'gchandle\.is_released'
            'executor-connect'      = 'GodotRuntimeExecutor failed|Connection timeout|Failed to bind socket|The server returned an unexpected status code'
            'run-aborted'           = 'Test Run Aborted'
        }
        $skipKind = $null
        foreach ($sig in $skipSignatures.Keys) {
            if (Select-String -Path $log, $errLog -Pattern $skipSignatures[$sig] -ErrorAction SilentlyContinue) { $skipKind = $sig; break }
        }
        # Native test-host crash (illegal instruction) under IDE test-runner contention — environmental, retry-worthy.
        if (-not $skipKind -and (Test-NativeCrashExit -ExitCode $code)) { $skipKind = 'testhost-native-crash' }
        $skip = [bool]$skipKind

        # A signature in a run that overlapped a peer runtime run is a rollback CANDIDATE, logged
        # durably so no retry or batch filter can erase it. Triggers and rollback:
        # running.md §Runtime slots and parallel dev. filter= is last: it runs to end of line.
        # An overlap with only this gate's own held siblings is a plain RED marked selfOverlap=1, never a trigger.
        $ovKind = Get-OverlapSignatureKind -SkipKind $skipKind -ExitCode $code -GodotLog $godotLog
        if ($ovKind) {
            $ovLine = "OVERLAP_SIGNATURE=$ovKind attempt=$attempt exit=$code slots=$($slots.Count) filter=$Filter"
            $ovScope = Register-OverlapSignature -Lease $lease -Repo $repo -Label $Label -Line $ovLine
            if ($ovScope -eq 'foreign') { Write-Output $ovLine }
            elseif ($ovScope -eq 'self') { $selfOverlap = $true }
        }

        if ($skip -and $attempt -lt $maxAttempts) {
            Write-Output "RETRY=SILENT_SKIP  label=$Label  attempt=$attempt  elapsed=${secs}s  exit=$code  signature=$skipKind  (re-running once)"
            continue
        }

        if ($skipRecompile) { Assert-RecompileSkipped -Trx $passTrx -Label $Label }
        Save-RecompilePassStamp -Repo $repo -Decision $recompileDecision -SkipRecompile:$skipRecompile -SilentSkip:$skip -Trx $passTrx

        # Surface the GdUnit4 result line(s) + any persisting silent-skip / abort signature.
        $summary = Select-String -Path $log -Pattern 'Passed!|Failed!|Passed:\s*\d+|Total:\s*\d+|Aborted' -ErrorAction SilentlyContinue |
            Select-Object -Last 2 | ForEach-Object { $_.Line.Trim() }
        Write-Output "STATUS=DONE  label=$Label  exit=$code  elapsed=${secs}s  attempts=$attempt$(if ($selfOverlap) { '  selfOverlap=1' })"
        if ($summary) { $summary | ForEach-Object { Write-Output $_ } }
        # On failure, surface WHICH tests failed from this run's own TRX, so callers don't parse TestResults\*.trx.
        if ($code -ne 0) { Write-FailureLines -OwnTrx $passTrx -Since $runStartedAt }
        if ($skip) {
            Write-Output "WARN=SILENT_SKIP_SIGNATURE  signature=$skipKind  (persisted after retry -- results INVALID, counts prove nothing)"
            if ($skipKind -eq 'refcounted-free-fatal') {
                Write-Output 'CAUSE=REFCOUNTED_FREE  A Free() on a RefCounted (every Resource is one) released its gchandle; the finalizer then killed the host.'
                Write-Output '  It is NOT a pipe/connect failure, and the faulting teardown is usually in a DIFFERENT suite than the one that died.'
                Write-Output '  Name it: grep the log for "Can.t free a RefCounted" -- those ERROR lines carry the faulting type.'
                Write-Output '  Sweep it: python .claude/hooks/refcounted_free_guard.py'
            }
            Write-Output '  Before bisecting the change under test: re-run this exact filter in the PRIMARY checkout. A fresh worktree is not a control'
            Write-Output '  (.runsettings is gitignored, .godot is cold). Bisect the FILTER, not the code.'
        }
        if ($code -eq 0 -and -not $skip) {
            Save-SuiteGreen -Repo $repo -Path $lastGreen -Trx $passTrx -Filter $Filter -ElapsedSec $secs `
                -StartedAt $runStartedAt -TreeDigest $suiteTreeDigest -DigestScript $digestScript
        }
        Write-Output "LOG=$log"
        exit $code
    }
}
finally {
    # finally runs even on `exit`; release so peers don't wait the full lock cap. Exit-RunLocks
    # never throws, so the activity cleanup below always runs and LOCK_TIMEOUT stays exit 125.
    Exit-RunLocks -Lease $lease
    try { if ($script:ActivityFile) { Remove-Item -LiteralPath $script:ActivityFile -Force -ErrorAction SilentlyContinue } } catch { }
}
