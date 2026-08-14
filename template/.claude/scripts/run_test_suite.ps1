#requires -Version 7
<#
.SYNOPSIS
  Hang-proof GdUnit4 suite runner. Guarantees the calling Bash tool ALWAYS regains
  control within -TimeoutMs, even when the GdUnit4 GodotRuntimeExecutor wedges.

.WHY
  Bare `dotnet test` run through the Bash tool can hang the caller FOREVER, past its
  own timeout: a wedged run has already spawned testhost -> headless Godot, and those
  grandchildren INHERIT the tool's stdout/stderr pipe. Killing the direct child leaves
  Godot alive holding the pipe's write-end open, so the tool blocks on a read that never
  hits EOF. This wrapper breaks that two ways:
    1. dotnet's output is redirected to a FILE (Start-Process -RedirectStandard*), so the
       caller's pipe is never inherited by any test grandchild -> EOF is immediate on exit.
    2. A hard wall-clock WaitForExit(TimeoutMs); on expiry the WHOLE process tree is killed
       via `taskkill /F /T` (name-based Stop-Process misses the respawning wrapper -- see
       memory GdUnit4_Process_Management). The script then returns a small summary.

.CONTENTION
  gdUnit4's connect pipe is gdunit4-<AssemblyName>, shared by every worktree (all build
  {{PROJECT_NAME}}.dll). Cold boot >10s OR a concurrent run from another worktree/session both
  trip the hardcoded 10s ConnectAsync -> a silent-skip (green-looking low count + the
  GodotRuntimeExecutor/Connection-timeout WARN). Locking is TIER-SPLIT:
    - Runtime suites (default): machine-global run-lock (named mutex) serializes them across
      ALL worktrees, plus a pipe drain before each launch.
    - Logic suites (-NoGodotRuntime): no pipe is involved, so a PER-WORKTREE mutex only
      (serializes same-worktree runs, which share TestResults/ + build output) and no drain
      -- peer worktrees run Logic in parallel.
  Both tiers: a single auto-retry-on-silent-skip absorbs cold-boot/orphan cases (the failed
  attempt warms the cache + frees the pipe), and the pre-flight kill-sweep is WORKTREE-SCOPED
  -- it reaps only processes attributable to this repo root (testhost/vstest by image path or
  command line; headless Godot by parent chain) plus unattributable orphans, never a peer
  worktree's in-flight run.

.NOTES
  Windows-only (uses Win32_Process + taskkill). Cloud/Linux test runs go through
  cloud_test_enforcer.py + xvfb-run, a separate path.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, ParameterSetName = 'Run')] [string] $Filter,
    # Classify what the reaper WOULD do against the live process table, print the REAP line, kill
    # nothing, exit 0. The verification surface for the kill/spare policy: a spare path can be proven
    # against a synthetic peer instead of by risking a real one. Takes no locks, runs read-only.
    [Parameter(Mandatory, ParameterSetName = 'ReapReport')] [switch] $ReapReport,
    [string] $Label = $Filter,
    # Hard wall-clock cap. Must stay UNDER the Bash tool's 600000ms ceiling so this script
    # returns before the tool would kill it. Healthy Logic ~90s, Integration ~116s.
    [int] $TimeoutMs = 480000,
    # Logic-domain tier: the filtered suite spawns NO headless Godot, so it needs neither the
    # machine-global pipe lock nor the drain. Default off = today's runtime-safe behavior.
    [switch] $NoGodotRuntime,
    # Escape hatch for the entry editor guard below. The gate's -FromQueue path forwards it, so a
    # watcher-fired run is never self-blocked by the editor race it just re-checked.
    [switch] $IgnoreEditor
)

# Godot process identity + checkout attribution live in ONE home; this wrapper consumes them.
. (Join-Path $PSScriptRoot 'GodotProcess.ps1')

$ErrorActionPreference = 'Stop'
$repo = (Get-Item $PSScriptRoot).Parent.Parent.FullName   # .../.claude/scripts -> repo root
Set-Location $repo

# --- Entry editor guard ------------------------------------------------------------------
# `dotnet test` builds into .godot\mono\temp\bin\Debug\, a SINGLE output shared with the Godot
# editor (the Godot SDK pins OutputPath; {{PROJECT_NAME}}.csproj cannot override it). A run and a live
# editor on this checkout are therefore mutually destructive -- we hold {{PROJECT_NAME}}.dll and its
# build fails, or it rebuilds under us and strips .tres scripts. This wrapper is the low-level tool
# and does not queue (the gate owns queueing); it refuses.
if (-not $IgnoreEditor -and -not $ReapReport) {
    $liveEditors = @(Get-LiveEditor -Checkout $repo)
    if ($liveEditors.Count -gt 0) {
        Write-Output "STATUS=EDITOR_OPEN  label=$Label  (editor/playtest live on this checkout; refuse rather than race its build -- pass -IgnoreEditor to override)"
        exit 126
    }
}

$logDir = Join-Path $repo '.claude\scratch\test_runs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$safe   = ($Label -replace '[^\w.-]', '_')
$log    = Join-Path $logDir "$safe.log"
$errLog = Join-Path $logDir "$safe.err.log"

# --- Activity registry (machine-global, advisory) -----------------------------------------
# Lets a peer session NAME what is blocking it instead of guessing -- a contention kill is not a
# regression. Advisory only: every touch is best-effort and can never fail the run.
# The Claude Code process that owns this script's subtree. This is the axis that tells "my
# session's run" apart from a concurrent session's when both share ONE checkout, where `checkout`
# cannot distinguish them and `sessionId` is null (CLAUDE_SESSION_ID is not exported into this
# shell). Captured HERE, at record-creation time, and never re-derived by a reader later: a
# detached run outlives its launcher — Claude Code kills a backgrounded wrapper at its ~10-minute
# ceiling — and the orphaned process's parent chain then reaches no session at all.
function Get-OwnerRoot {
    if ($script:OwnerRootComputed) { return $script:OwnerRootCache }
    $script:OwnerRootComputed = $true
    $script:OwnerRootCache = $null
    try {
        $seen = @{}
        $cur = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
        for ($i = 0; $i -lt 12 -and $cur; $i++) {
            $ppid = [int] $cur.ParentProcessId
            if ($ppid -le 0 -or $seen.ContainsKey($ppid)) { break }
            $seen[$ppid] = $true
            $par = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid" -ErrorAction SilentlyContinue
            if (-not $par) { break }
            if ($par.Name -match '^(node|claude)(\.exe)?$') {
                $script:OwnerRootCache = [pscustomobject] @{
                    RootPid   = [int] $par.ProcessId
                    RootStart = ([datetime] $par.CreationDate).ToUniversalTime().ToString('o')
                }
                break
            }
            $cur = $par
        }
    } catch { }
    return $script:OwnerRootCache
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
    # would queue behind a process that is doing nothing.
    if ($ReapReport) { throw 'skip' }
    $actDir = Join-Path $env:TEMP 'pp-activity'
    New-Item -ItemType Directory -Force -Path $actDir | Out-Null
    $script:ActivityFile = Join-Path $actDir "suite-$PID.json"
    $script:ActivityRec  = [ordered] @{
        pid         = $PID
        procStart   = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
        kind        = 'suite'
        checkout    = $repo
        sessionId   = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $null })
        ownerRootPid   = $(if ($o = Get-OwnerRoot) { $o.RootPid }   else { $null })
        ownerRootStart = $(if ($o = Get-OwnerRoot) { $o.RootStart } else { $null })
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
# mid-run). The activity registry already carries the missing axis — `sessionId` — and
# regression_gate.ps1's Get-LivePeerActivity already reads it, so identity comes from there and
# the reaper only has to honour it.
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
    param([hashtable] $Map)
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
    $stack.Push($PID)
    while ($stack.Count -gt 0) {
        $id = $stack.Pop()
        if (-not $mine.Add($id)) { continue }
        if ($children.ContainsKey($id)) { foreach ($c in $children[$id]) { $stack.Push($c) } }
    }
    # Unary comma: `return` writes to the output stream, which UNROLLS a collection. A one-element
    # set would arrive at the caller as a bare [int], and `.Contains()` on it throws — aborting the
    # reaper mid-classification. Measured 2026-08-13 against a live peer plus one other candidate.
    return , $mine
}

function Get-PeerProtectedPids {
    param([hashtable] $Map, [string] $Repo)
    $protected = [System.Collections.Generic.HashSet[int]]::new()
    # pid -> "label='x' session=y", so a spare can NAME what it declined to kill instead of
    # printing a bare number the reader has to go identify themselves.
    $script:PeerLabels = @{}
    $actDir = Join-Path $env:TEMP 'pp-activity'
    if (-not (Test-Path $actDir)) { return $protected }

    $roots = [System.Collections.Generic.List[int]]::new()
    $rootLabel = @{}
    foreach ($f in @(Get-ChildItem -Path $actDir -Filter '*.json' -ErrorAction SilentlyContinue)) {
        $rec = $null
        try { $rec = Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        $rpid = 0; try { $rpid = [int] $rec.pid } catch { continue }
        if ($rpid -le 0 -or $rpid -eq $PID) { continue }
        if ($rec.kind -notin @('gate', 'suite')) { continue }

        # PID-reuse guard: the record is only about the process that actually wrote it.
        $proc = Get-Process -Id $rpid -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        if ($rec.procStart) {
            try {
                if ([math]::Abs((([datetime] $rec.procStart).ToUniversalTime() - $proc.StartTime.ToUniversalTime()).TotalSeconds) -gt 5) { continue }
            } catch { continue }
        }

        # Our own family is same checkout AND same session. Anything else is a peer to spare —
        # including a different session in THIS checkout, which is the case Get-ProcVerdict misses.
        if ($rec.checkout -eq $Repo -and $env:CLAUDE_SESSION_ID -and $rec.sessionId -eq $env:CLAUDE_SESSION_ID) { continue }
        $roots.Add($rpid)
        $sid = if ($rec.sessionId) { ([string] $rec.sessionId).Substring(0, [math]::Min(8, ([string] $rec.sessionId).Length)) } else { 'n/a' }
        $rootLabel[$rpid] = "label='$($rec.label)' session=$sid"
    }
    if ($roots.Count -eq 0) { return $protected }

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
    if (-not $DryRun) { foreach ($k in $kills) { & taskkill.exe '/F' '/T' '/PID' $k.pid 2>$null | Out-Null } }
}

# Block until no headless-Godot/testhost of OURS (or orphaned) remains, THEN settle so the OS
# releases the machine-global named pipe. Back-to-back runtime suites that skip this drain step
# fail to bind the pipe -> Mode-A silent-skip/wedge on the 2nd suite (observed 2026-05-31).
# Peer worktrees' live processes are ignored -- a healthy long peer run must not stall us.
function Wait-PipeDrained {
    param([string] $Repo, [int] $TimeoutSec = 25, [int] $SettleMs = 2500)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $map  = Get-ProcSnapshot
        $live = @(Get-Process -Name 'testhost' -ErrorAction SilentlyContinue)
        $live += @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue | Where-Object {
                       $ci = $map[$_.Id]
                       $ci -and (Get-GodotRole -Proc $ci -Map $map) -in @('TestRunner', 'Orphan')
                   })
        if ($live.Count -eq 0) { break }
        $ours = @($live | Where-Object {
            $ci = $map[$_.Id]
            $ci -and (Get-ProcVerdict $ci $map $Repo $(if ($_.ProcessName -like 'Godot*') { 3 } else { 0 })) -ne 'PEER'
        })
        if ($ours.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds $SettleMs
}

# --- Run-lock: machine-global for anything that spawns Godot. -----------------------------
# The pipe is salted per worktree (GDUNIT4_PIPE_SUFFIX + forked gdUnit4.api), which removes
# PIPE collisions — but concurrent Godot test instances on one machine crash with CLR
# 0xc000001d (STATUS_ILLEGAL_INSTRUCTION; engine-adjacent shared user://+cache contention,
# godotengine/godot#16679 class; --headless is no escape — the GdUnit4 pipe server never
# starts without a display). Verified 2026-07-24: 4/4 cross-worktree concurrent runs crashed;
# solo/serialized runs green. So ALL Godot-spawning runs serialize on the global mutex.
# NOTE: this project's Logic suite DOES spawn Godot (only ~388 of its tests are runtime-free)
# — pass -NoGodotRuntime ONLY for a filter that provably contains zero [RequireGodotRuntime]
# tests; it takes the per-worktree lock instead and skips the drain.
if ($ReapReport) {
    Clear-TestRunners -Repo $repo -Site 'dry-run' -DryRun
    exit 0
}

# Acquired BEFORE Clear-TestRunners, so a waiting run never tree-kills a peer's in-flight Godot.
# Mutex is process-owned: if a holder dies without releasing, the next WaitOne throws
# AbandonedMutexException but still grants the lock.
$mutexName  = if ($NoGodotRuntime) { "Global\gdunit4-runlock-$(Get-WorktreeId $repo)" } else { 'Global\gdunit4-{{PROJECT_NAME}}-runlock' }
$mutex      = [System.Threading.Mutex]::new($false, $mutexName)
$haveLock   = $false
$lockWaitMs = $TimeoutMs + 120000   # wait up to one suite-cap + buffer for a peer worktree to finish

try {
    $lockSw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $haveLock = $mutex.WaitOne($lockWaitMs)
    }
    catch [System.Threading.AbandonedMutexException] {
        $haveLock = $true   # prior holder died mid-run; we own the lock now -> proceed
    }
    $lockSw.Stop()
    if (-not $haveLock) {
        # The full wait is emitted before exit — the batched runner sums LOCKWAIT_MS across
        # a batch's output and excludes it from the work budget.
        Write-Output "LOCKWAIT_MS=$($lockSw.ElapsedMilliseconds)"
        Write-Output "STATUS=LOCK_TIMEOUT  label=$Label  (a peer runtime suite held $mutexName > ${lockWaitMs}ms)"
        exit 125
    }
    # Every path emits its lock-wait, 0 included (same discipline as COMPLETENESS): the batched
    # runner excludes it from the work budget, so contention makes the gate SLOW, never a false
    # count regression. An ABSENT line means this path was never reached.
    Write-Output "LOCKWAIT_MS=$($lockSw.ElapsedMilliseconds)"

    # --- Per-worktree isolation: pipe salt (consumed by the forked gdUnit4.api — appends
    #     to the pipe name so worktrees stop sharing one pipe) + ensure the --log-file
    #     target dir exists before Godot spawns (Godot does not create missing dirs). ----
    $env:GDUNIT4_PIPE_SUFFIX = Get-WorktreeId $repo
    New-Item -ItemType Directory -Force -Path (Join-Path $repo 'TestResults') | Out-Null

    # --- Up to 2 attempts: a cold-boot / pipe-collision / orphan silent-skip self-heals on re-run
    #     (the failed attempt warms the .NET JIT + OS file cache and frees the pipe). ----
    $maxAttempts = 2
    $attempt = 0
    while ($true) {
        $attempt++
        Write-ActivityRecord
        Clear-TestRunners -Repo $repo -Site "suite:$Label"
        if (-not $NoGodotRuntime) { Wait-PipeDrained -Repo $repo }

        # File-redirected output so no grandchild inherits the caller's pipe (immediate EOF on exit).
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $p = Start-Process -FilePath 'dotnet' `
                -ArgumentList @('test', '--settings', '.runsettings', '--verbosity', 'quiet', '--filter', $Filter) `
                -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $errLog
        $exited = $p.WaitForExit($TimeoutMs)
        $sw.Stop()
        $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

        if (-not $exited) {
            # Wedged past the wall-clock cap. Tree-kill so nothing keeps the pipe / temp DLLs locked.
            & taskkill.exe '/F' '/T' '/PID' $p.Id 2>$null | Out-Null
            Start-Sleep -Milliseconds 750
            Clear-TestRunners -Repo $repo -Site "suite:$Label:hang"
            if (-not $NoGodotRuntime) { Wait-PipeDrained -Repo $repo }
            if ($attempt -lt $maxAttempts) {
                Write-Output "RETRY=HANG  label=$Label  attempt=$attempt  elapsed=${secs}s  (tree-killed PID $($p.Id); re-running once)"
                continue
            }
            Write-Output "STATUS=HANG  label=$Label  elapsed=${secs}s  cap=${TimeoutMs}ms  attempts=$attempt  (tree-killed PID $($p.Id))"
            Write-Output '---- last log lines ----'
            if (Test-Path $log) { Get-Content $log -Tail 8 -ErrorAction SilentlyContinue }
            exit 124   # conventional timeout exit code
        }

        $code = $p.ExitCode
        $skip = Select-String -Path $log, $errLog -Pattern 'GodotRuntimeExecutor failed|Connection timeout|Test Run Aborted|Failed to bind socket|The server returned an unexpected status code|gchandle\.is_released' -ErrorAction SilentlyContinue
        # Native test-host crash (0xC000001D) under IDE test-runner contention — environmental, retry-worthy.
        if (-not $skip -and $code -eq -1073741795) { $skip = $true }

        if ($skip -and $attempt -lt $maxAttempts) {
            Write-Output "RETRY=SILENT_SKIP  label=$Label  attempt=$attempt  elapsed=${secs}s  exit=$code  (runtime executor connect failed; re-running once)"
            continue
        }

        # Surface the GdUnit4 result line(s) + any persisting silent-skip / abort signature.
        $summary = Select-String -Path $log -Pattern 'Passed!|Failed!|Passed:\s*\d+|Total:\s*\d+|Aborted' -ErrorAction SilentlyContinue |
            Select-Object -Last 2 | ForEach-Object { $_.Line.Trim() }
        Write-Output "STATUS=DONE  label=$Label  exit=$code  elapsed=${secs}s  attempts=$attempt"
        if ($summary) { $summary | ForEach-Object { Write-Output $_ } }
        # On failure, surface WHICH tests failed (from the freshest TRX) so callers don't have to
        # parse TestResults\*.trx themselves for every red run.
        if ($code -ne 0) {
            $trx = Get-ChildItem -Path (Join-Path $repo 'TestResults') -Filter '*.trx' -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($trx) {
                Select-String -Path $trx.FullName -Pattern 'testName="([^"]+)"[^>]*outcome="Failed"' -AllMatches -ErrorAction SilentlyContinue |
                    ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } |
                    Select-Object -First 15 | ForEach-Object { Write-Output "FAILED_TEST=$_" }
            }
        }
        if ($skip) {
            Write-Output 'WARN=SILENT_SKIP_SIGNATURE  (persisted after retry -- runtime executor connection failed, results INVALID)'
        }
        Write-Output "LOG=$log"
        exit $code
    }
}
finally {
    # finally runs even on `exit`; release so peers don't wait the full lock cap. Belt-and-braces:
    # an unreleased mutex is abandoned at process death and the next waiter still acquires it.
    if ($haveLock) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    try { if ($script:ActivityFile) { Remove-Item -LiteralPath $script:ActivityFile -Force -ErrorAction SilentlyContinue } } catch { }
}
