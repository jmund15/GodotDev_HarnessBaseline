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
  GodotRuntimeExecutor/Connection-timeout WARN). Two guards: a machine-global run-lock
  (named mutex) serializes suites across worktrees, and a single auto-retry-on-silent-skip
  absorbs cold-boot/orphan cases (the failed attempt warms the cache + frees the pipe).

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
    [int] $TimeoutMs = 480000
)

$ErrorActionPreference = 'Stop'
$repo = (Get-Item $PSScriptRoot).Parent.Parent.FullName   # .../.claude/scripts -> repo root
Set-Location $repo

$logDir = Join-Path $repo '.claude\scratch\test_runs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$safe   = ($Label -replace '[^\w.-]', '_')
$log    = Join-Path $logDir "$safe.log"
$errLog = Join-Path $logDir "$safe.err.log"

# --- Clear stale test-runner trees BEFORE launching (never racing the launch). ---------
# Tree-kill any surviving dotnet-test / vstest / testhost wrapper (its /T takes the child
# Godot with it); then an editor-safe headless-Godot backstop. This wrapper's own name
# (run_test_suite.ps1) matches none of these patterns, so it can't kill itself.
function Clear-TestRunners {
    # 1) Kill by process NAME first. A wedged testhost/Godot can have a null/unreadable
    #    CommandLine (or re-parent to PID 1), which the CommandLine filter below misses --
    #    observed 2026-05-31: a 1.2GB headless Godot + its testhost survived a CommandLine-only
    #    sweep and held the named pipe, wedging the next runtime suite.
    Get-Process -Name 'testhost', 'vstest.console' -ErrorAction SilentlyContinue | ForEach-Object {
        & taskkill.exe '/F' '/T' '/PID' $_.Id 2>$null | Out-Null
    }
    # Editor-safe: headless test Godot only (empty/non-editor window title); never the editor.
    Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -notlike '*Godot Engine*' } | ForEach-Object {
            & taskkill.exe '/F' '/T' '/PID' $_.Id 2>$null | Out-Null
        }
    # 2) Also tree-kill detached dotnet-test wrappers by CommandLine (best-effort top-up).
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and ($_.CommandLine -match 'vstest\.console' -or $_.CommandLine -match 'test\s+--settings')
    } | ForEach-Object { & taskkill.exe '/F' '/T' '/PID' $_.ProcessId 2>$null | Out-Null }
}

# Block until no headless-Godot/testhost remains, THEN settle so the OS releases the
# machine-global named pipe. Back-to-back runtime suites that skip this drain step fail to
# bind the pipe -> Mode-A silent-skip/wedge on the 2nd suite (observed 2026-05-31).
function Wait-PipeDrained {
    param([int] $TimeoutSec = 25, [int] $SettleMs = 2500)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $live = @(Get-Process -Name 'testhost', 'Godot*' -ErrorAction SilentlyContinue |
                  Where-Object { $_.MainWindowTitle -notlike '*Godot Engine*' })
        if ($live.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds $SettleMs
}

# --- Machine-global run-lock: serialize runtime suites across ALL worktrees/sessions. ----
# gdUnit4's connect pipe is gdunit4-<AssemblyName>; every worktree builds {{PROJECT_NAME}}.dll, so
# all of them share ONE pipe (gdunit4-{{PROJECT_NAME}}). Two suites running at once collide on it ->
# one silent-skips (the hardcoded 10s NamedPipeClientStream.ConnectAsync in GodotRuntimeExecutor).
# A named mutex makes a second run WAIT instead of race. Acquired BEFORE Clear-TestRunners, so a
# waiting run never tree-kills a peer's in-flight Godot. Mutex is process-owned: if a holder dies
# without releasing, the next WaitOne throws AbandonedMutexException but still grants the lock.
$mutexName  = 'Global\gdunit4-{{PROJECT_NAME}}-runlock'
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

    # --- Up to 2 attempts: a cold-boot / pipe-collision / orphan silent-skip self-heals on re-run
    #     (the failed attempt warms the .NET JIT + OS file cache and frees the pipe). ----
    $maxAttempts = 2
    $attempt = 0
    while ($true) {
        $attempt++
        Clear-TestRunners
        Wait-PipeDrained

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
            Clear-TestRunners
            Wait-PipeDrained
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
        $skip = Select-String -Path $log, $errLog -Pattern 'GodotRuntimeExecutor failed|Connection timeout|Test Run Aborted|Failed to bind socket' -ErrorAction SilentlyContinue

        if ($skip -and $attempt -lt $maxAttempts) {
            Write-Output "RETRY=SILENT_SKIP  label=$Label  attempt=$attempt  elapsed=${secs}s  exit=$code  (runtime executor connect failed; re-running once)"
            continue
        }

        # Surface the GdUnit4 result line(s) + any persisting silent-skip / abort signature.
        $summary = Select-String -Path $log -Pattern 'Passed!|Failed!|Passed:\s*\d+|Total:\s*\d+|Aborted' -ErrorAction SilentlyContinue |
            Select-Object -Last 2 | ForEach-Object { $_.Line.Trim() }
        Write-Output "STATUS=DONE  label=$Label  exit=$code  elapsed=${secs}s  attempts=$attempt"
        if ($summary) { $summary | ForEach-Object { Write-Output $_ } }
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
