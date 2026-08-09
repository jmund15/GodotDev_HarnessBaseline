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
    [Parameter(Mandatory)] [string] $Filter,
    [string] $Label = $Filter,
    # Hard wall-clock cap. Must stay UNDER the Bash tool's 600000ms ceiling so this script
    # returns before the tool would kill it. Healthy Logic ~90s, Integration ~116s.
    [int] $TimeoutMs = 480000,
    # Logic-domain tier: the filtered suite spawns NO headless Godot, so it needs neither the
    # machine-global pipe lock nor the drain. Default off = today's runtime-safe behavior.
    [switch] $NoGodotRuntime
)

$ErrorActionPreference = 'Stop'
$repo = (Get-Item $PSScriptRoot).Parent.Parent.FullName   # .../.claude/scripts -> repo root
Set-Location $repo

$logDir = Join-Path $repo '.claude\scratch\test_runs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$safe   = ($Label -replace '[^\w.-]', '_')
$log    = Join-Path $logDir "$safe.log"
$errLog = Join-Path $logDir "$safe.err.log"

# --- Worktree attribution --------------------------------------------------------------
# Every worktree builds its OWN testhost.exe under <worktree>\.godot\mono\temp\bin\, so
# testhost/vstest carry the worktree root in ExecutablePath/CommandLine. The headless Godot
# does NOT (shared machine-global GODOT_BIN, launched with a relative `--path .`), so it is
# attributed by walking its PARENT chain up to the owning testhost.
function Get-WorktreeId {
    param([string] $Path)
    $norm  = $Path.TrimEnd('\', '/').ToLowerInvariant()
    $bytes = [System.Security.Cryptography.SHA1]::HashData([System.Text.Encoding]::UTF8.GetBytes($norm))
    return [System.Convert]::ToHexString($bytes).ToLowerInvariant().Substring(0, 8)
}

function Get-ProcSnapshot {
    $map = @{}
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { $map[[int] $p.ProcessId] = $p }
    return $map
}

function Test-UnderRoot {
    param([string] $Text, [string] $Root)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    if ($Text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    # Nested-worktree guard: worktrees live UNDER the main repo root (<main>\.claude\worktrees\<x>),
    # so a peer worktree's path also CONTAINS the main root. A reference into the worktrees dir
    # belongs to that worktree, not to Root. (When Root itself is a worktree, this infix never
    # occurs in its own paths, so the guard is inert.)
    $nested = $Root.TrimEnd('\') + '\.claude\worktrees\'
    return $Text.IndexOf($nested, [System.StringComparison]::OrdinalIgnoreCase) -lt 0
}

# 'MINE' = attributable to $Root | 'PEER' = another worktree's live process (SPARE it)
# | 'ORPHAN' = image path AND command line unreadable, or the parent chain is dead/unresolvable
# (dead weight for every worktree -- safe to reap). $MaxHops 0 = direct attribution only.
function Get-ProcVerdict {
    param([object] $Proc, [hashtable] $Map, [string] $Root, [int] $MaxHops = 0)
    $cur = $Proc
    for ($hop = 0; $hop -le $MaxHops; $hop++) {
        if ((Test-UnderRoot $cur.ExecutablePath $Root) -or (Test-UnderRoot $cur.CommandLine $Root)) { return 'MINE' }
        # Wedged zombie: neither field readable (observed 2026-05-31 -- a 1.2GB headless Godot
        # + its testhost survived a CommandLine-only sweep and held the named pipe).
        if (-not $cur.ExecutablePath -and -not $cur.CommandLine) { return 'ORPHAN' }
        if ($hop -eq $MaxHops) { break }
        $parentId = [int] $cur.ParentProcessId
        $parent   = if ($parentId -gt 0) { $Map[$parentId] } else { $null }
        # PID-reuse guard: a "parent" younger than its child is an impostor -> real parent is gone.
        if (-not $parent) { return 'ORPHAN' }
        if ($parent.CreationDate -and $cur.CreationDate -and $parent.CreationDate -gt $cur.CreationDate) { return 'ORPHAN' }
        $cur = $parent
    }
    return 'PEER'
}

# --- Clear stale test-runner trees BEFORE launching (never racing the launch). ---------
# Tree-kill any surviving dotnet-test / vstest / testhost wrapper (its /T takes the child
# Godot with it); then an editor-safe headless-Godot backstop. This wrapper's own name
# (run_test_suite.ps1) matches none of these patterns, so it can't kill itself. Scoped to
# $Repo: a peer worktree's in-flight run is SPARED, orphans are not.
function Clear-TestRunners {
    param([string] $Repo)
    $hosts  = @(Get-Process -Name 'testhost', 'vstest.console' -ErrorAction SilentlyContinue)
    $godots = @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue |
                Where-Object { $_.MainWindowTitle -notlike '*Godot Engine*' })   # editor-safe: headless only
    $map    = Get-ProcSnapshot
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
    foreach ($id in ($kill | Sort-Object -Unique)) { & taskkill.exe '/F' '/T' '/PID' $id 2>$null | Out-Null }
}

# Block until no headless-Godot/testhost of OURS (or orphaned) remains, THEN settle so the OS
# releases the machine-global named pipe. Back-to-back runtime suites that skip this drain step
# fail to bind the pipe -> Mode-A silent-skip/wedge on the 2nd suite (observed 2026-05-31).
# Peer worktrees' live processes are ignored -- a healthy long peer run must not stall us.
function Wait-PipeDrained {
    param([string] $Repo, [int] $TimeoutSec = 25, [int] $SettleMs = 2500)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $live = @(Get-Process -Name 'testhost', 'Godot*' -ErrorAction SilentlyContinue |
                  Where-Object { $_.MainWindowTitle -notlike '*Godot Engine*' })
        if ($live.Count -eq 0) { break }
        $map  = Get-ProcSnapshot
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
# NOTE: a Logic filter may still spawn Godot (only a subset of its tests are runtime-free)
# — pass -NoGodotRuntime ONLY for a filter that provably contains zero [RequireGodotRuntime]
# tests; it takes the per-worktree lock instead and skips the drain.
# Acquired BEFORE Clear-TestRunners, so a waiting run never tree-kills a peer's in-flight Godot.
# Mutex is process-owned: if a holder dies without releasing, the next WaitOne throws
# AbandonedMutexException but still grants the lock.
$mutexName  = if ($NoGodotRuntime) { "Global\gdunit4-runlock-$(Get-WorktreeId $repo)" } else { 'Global\gdunit4-{{PROJECT_NAME}}-runlock' }
$mutex      = [System.Threading.Mutex]::new($false, $mutexName)
$haveLock   = $false
$lockWaitMs = $TimeoutMs + 120000   # wait up to one suite-cap + buffer for a peer worktree to finish

try {
    try {
        $haveLock = $mutex.WaitOne($lockWaitMs)
    }
    catch [System.Threading.AbandonedMutexException] {
        $haveLock = $true   # prior holder died mid-run; we own the lock now -> proceed
    }
    if (-not $haveLock) {
        Write-Output "STATUS=LOCK_TIMEOUT  label=$Label  (a peer runtime suite held $mutexName > ${lockWaitMs}ms)"
        exit 125
    }

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
        Clear-TestRunners -Repo $repo
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
            Clear-TestRunners -Repo $repo
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
}
