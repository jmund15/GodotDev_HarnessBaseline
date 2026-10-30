#requires -Version 7
<#
.SYNOPSIS
  The run pool: one launcher (Start-SuiteChild) and one scheduler (Invoke-RunPool) for isolated wrapper runs, and the
  run-dir layout they write: `TestResults/runs/<runId>/`, one dir per gate or standalone batcher run.

.OWNS
  The layout functions New-RunDir and Remove-OldRunDirs. The owner that mints a runId (the gate: `gate-<runId>`,
  the standalone batcher: `verify-<timestamp>`) creates its dir, passes it as every child's `-ResultsRoot`, and
  runs retention. Retention keeps the newest $Keep dirs per owner kind, so the newest gate's per-test profile and
  the previous gate's failures stay comparable.
  The pool's width ($IntraCheckoutWidthMax caps it) and backstop, and the one reader of the wrapper's stdout contract
  (ConvertFrom-WrapperOutput). The pool is policy-free: -Launch decides held or ordinary children, -OnPoll carries
  the owner's per-poll checks, and every state write stays with the owner.

.NOTES
  Dot-sourced flat .ps1 (house pattern). Depends one way: RuntimeSlots.ps1 (owner stamp, slot count) and, through it,
  GodotProcess.ps1's Get-RunTag.
#>
. (Join-Path $PSScriptRoot 'RuntimeSlots.ps1')

# One checkout's pool width cap; $null leaves the width at the machine's runtime slot count. Set only from a measured
# same-checkout limit (A4a no-go or A9), cited beside it.
$IntraCheckoutWidthMax = $null
# The pool's backstop above a healthy wrapper's longest life (Get-SuiteRunBounds: slot wait plus every attempt): the
# wrapper's pre-work, hang kill and report take this long beyond it.
# basis: A4a tail probe 2026-09-29, a non-held Logic run at -TimeoutMs 20000 hung twice: wall 60952 ms minus lock wait
# 123 ms minus 2 x 20000 ms = 20829 ms of pre-work and kill tail; 2x margin.
$script:PoolBackstopTailMs = 42000
$script:PoolPollMs = 500
$script:SuiteWrapperPath = Join-Path $PSScriptRoot 'run_test_suite.ps1'
$script:PoolRepo = Resolve-TestRepo -ScriptsRoot $PSScriptRoot

# Creates <RunsRoot>/<RunId> with owner.json (the owner's pid and procStart) and returns its path. A dir whose stamp
# names a live process keeps it and its contents: a child handed the owner's runId must not take the dir over. A dir
# with no live owner is emptied and restamped: a dead gate's dir with the same runId is reused, and the previous attempt's stamp
# and files would otherwise expose the dir to retention and mix that attempt's TRX into this run.
function New-RunDir {
    param([Parameter(Mandatory)] [string] $RunsRoot, [Parameter(Mandatory)] [string] $RunId)
    Set-StrictMode -Version Latest
    $dir = Join-Path $RunsRoot $RunId
    $stampPath = Join-Path $dir 'owner.json'
    if (Test-Path -LiteralPath $stampPath) {
        $live = $false
        try {
            $stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
            $live = Test-SlotProcessLive $stamp.pid $stamp.procStart
        } catch { }
        if ($live) { return $dir }
    }
    if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [pscustomobject]@{ pid = $PID; procStart = (Get-ProcStartUtc (Get-Process -Id $PID)) } |
        ConvertTo-Json | Set-Content -LiteralPath $stampPath -Encoding utf8
    return $dir
}

# Deletes all but the newest $Keep dirs of each owner kind (the name's prefix before the first '-'), newest by
# creation time. A dir whose owner stamp names a live process is never deleted; a dir with no readable stamp has no
# live owner. Names outside $Kinds are left alone.
function Remove-OldRunDirs {
    param([Parameter(Mandatory)] [string] $RunsRoot, [string[]] $Kinds = @('gate', 'verify'), [int] $Keep = 2)
    Set-StrictMode -Version Latest
    if (-not (Test-Path -LiteralPath $RunsRoot)) { return }
    $dirs = @(Get-ChildItem -LiteralPath $RunsRoot -Directory)
    foreach ($kind in $Kinds) {
        $ofKind = @($dirs | Where-Object { $_.Name -like "$kind-*" } | Sort-Object CreationTimeUtc -Descending)
        foreach ($old in ($ofKind | Select-Object -Skip $Keep)) {
            $live = $false
            try {
                $stamp = Get-Content -LiteralPath (Join-Path $old.FullName 'owner.json') -Raw | ConvertFrom-Json
                $live = Test-SlotProcessLive $stamp.pid $stamp.procStart
            } catch { }
            if ($live) { continue }
            Remove-Item -LiteralPath $old.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# ------------------------------------------------------------------ pool
# The width a pool runs at when its caller passes no -MaxParallel: the runtime slot count of this checkout, capped by
# $IntraCheckoutWidthMax ($null casts to -Ceiling 0, no cap). .Source says which bound won.
function Get-RunPoolWidth {
    Get-RuntimeSlotCount -Repo $script:PoolRepo -Ceiling $IntraCheckoutWidthMax
}

# A child's stdout file: <ResultsRoot>/<the wrapper's run dir name>/wrapper.out, beside the child's TRX.
function Get-SuiteChildOutPath {
    param([Parameter(Mandatory)] [string] $ResultsRoot, [Parameter(Mandatory)] [string] $Label)
    Join-Path (Join-Path $ResultsRoot (Get-SafeRunLabel -Label $Label -Repo $script:PoolRepo -ResultsRoot $ResultsRoot)) 'wrapper.out'
}

function Get-SuiteChildArgv {
    param([Parameter(Mandatory)] $Unit, [Parameter(Mandatory)] [string] $ResultsRoot, [switch] $Held, [switch] $RecompilePass,
          [string] $Verbosity = 'quiet')
    $childArgs = @('-Label', $Unit.label, '-Filter', $Unit.filter, '-TimeoutMs', [string] $Unit.timeoutMs, '-ResultsRoot', $ResultsRoot,
              '-IgnoreEditor', '-Verbosity', $Verbosity, '-RunMode', $(if ($Unit.windowed) { 'windowed' } else { 'headless' })) + @(if ($Held) { '-HeldCheckout' }) + @(if ($RecompilePass) { '-RecompilePass' })
    Get-RunnerArgv -Script $script:SuiteWrapperPath -Repo $script:PoolRepo -ScriptArgs $childArgs
}

# The one child launcher: run_test_suite.ps1 for one unit under -ResultsRoot, always -IgnoreEditor (the pool's owner
# owns editor policy through -OnPoll). -Held makes it a gate's held child; -RecompilePass is the single held child
# admitted before the checkout is stamped recompiled. Without -Held it is an ordinary lock-taking run.
function Start-SuiteChild {
    param([Parameter(Mandatory)] $Unit, [Parameter(Mandatory)] [string] $ResultsRoot, [switch] $Held, [switch] $RecompilePass,
          [string] $Verbosity = 'quiet')
    if ($RecompilePass -and -not $Held) { throw 'Start-SuiteChild: -RecompilePass is valid only with -Held' }
    $argv = Get-SuiteChildArgv -Unit $Unit -ResultsRoot $ResultsRoot -Held:$Held -RecompilePass:$RecompilePass -Verbosity $Verbosity
    $out = Get-SuiteChildOutPath -ResultsRoot $ResultsRoot -Label $Unit.label
    New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
    # Start-Process joins its arguments with spaces, so a spaced value is quoted to stay one argument.
    $pw = Get-PwshStart
    $quoted = @($pw.Lead) + @($argv | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } })
    $p = Start-Process -FilePath $pw.FilePath -ArgumentList $quoted -PassThru -NoNewWindow `
        -RedirectStandardOutput $out -RedirectStandardError ([System.IO.Path]::ChangeExtension($out, '.err'))
    $null = $p.Handle   # caches the handle so ExitCode survives the process
    $p
}

# The one reader of the wrapper's stdout contract. A child whose evidence is incomplete gets its own status, never
# GREEN or RED: NO_OUTPUT (no wrapper result line), NO_TRX, BAD_TRX, SILENT_SKIP; the wrapper's refusals keep theirs.
function ConvertFrom-WrapperOutput {
    param($Unit, [string[]] $Lines, $ExitCode, [double] $ElapsedSec, [switch] $BackstopKilled, [string] $OutPath = '')
    $text = @($Lines) -join "`n"
    $passed = 0; $failed = 0; $counted = $false
    if ($text -match 'Failed:\s*(\d+),\s*Passed:\s*(\d+)') { $failed = [int] $Matches[1]; $passed = [int] $Matches[2]; $counted = $true }
    $trx = ''
    foreach ($m in [regex]::Matches($text, '(?m)^TRX=(.+)$')) { $trx = $m.Groups[1].Value.Trim() }
    $lockWaitMs = 0
    foreach ($m in [regex]::Matches($text, '(?m)^LOCKWAIT_MS=(\d+)')) { $lockWaitMs += [int] $m.Groups[1].Value }
    $status =
        if ($BackstopKilled) { 'HANG' }
        elseif (-not $text.Trim()) { 'NO_OUTPUT' }
        elseif ($ExitCode -eq 124 -or $text -match '(?m)^STATUS=HANG') { 'HANG' }
        elseif ($text -match '(?m)^STATUS=LOCK_TIMEOUT') { 'LOCKED' }
        elseif ($text -match '(?m)^STATUS=HELD_REFUSED') { 'HELD_REFUSED' }
        # A runner emits it only when a live checkout's twin could not be synced (TwinCheckout.ps1), or from older revisions.
        elseif ($text -match '(?m)^STATUS=EDITOR_OPEN') { 'EDITOR_OPEN' }
        elseif ($text -match '(?m)^STATUS=SKIP_RECOMPILE_IGNORED') { 'SKIP_RECOMPILE_IGNORED' }
        elseif ($text -match '(?m)^WARN=SILENT_SKIP_SIGNATURE') { 'SILENT_SKIP' }
        elseif ($text -notmatch '(?m)^STATUS=DONE') { 'NO_OUTPUT' }
        elseif (-not $trx -or -not (Test-Path -LiteralPath $trx)) { 'NO_TRX' }
        elseif (-not (Test-TrxReadable -Path $trx)) { 'BAD_TRX' }
        elseif ($ExitCode -ne 0 -or $failed -gt 0) { 'RED' }
        else { 'GREEN' }
    # GdUnit can report a [Before]/[After] hook as its own result row; vstest's summary counts it as a passed test,
    # and it appears in some runs only, so it moves the count against the baseline with no test added or removed.
    if ($counted -and $status -in @('GREEN', 'RED')) { $passed = [math]::Max(0, $passed - (Get-TrxHookPassCount -Path $trx)) }
    [pscustomobject] @{
        tag            = Get-RunTag -Label $Unit.label
        label          = $Unit.label
        exit           = $ExitCode
        passed         = $passed
        failed         = $failed
        counted        = $counted
        status         = $status
        trx            = $trx
        failedTests    = @([regex]::Matches($text, '(?m)^FAILED_TEST=(.+)$') | ForEach-Object { $_.Groups[1].Value.Trim() })
        failedMessages = @([regex]::Matches($text, '(?m)^FAILED_MSG=(.+)$') | ForEach-Object { $_.Groups[1].Value.Trim() })
        elapsedSec     = [math]::Round($ElapsedSec, 1)
        lockWaitMs     = $lockWaitMs
        outPath        = $OutPath
    }
}

function Get-TrxHookPassCount {
    param([string] $Path)
    try { [xml] $x = Get-Content -LiteralPath $Path -Raw } catch { return 0 }
    @($x.SelectNodes("//*[local-name()='UnitTestResult'][@outcome='Passed'][starts-with(@testName, '[')]")).Count
}

function Test-TrxReadable {
    param([string] $Path)
    try { [xml] $x = Get-Content -LiteralPath $Path -Raw; return ($null -ne $x.DocumentElement) } catch { return $false }
}

function Stop-PoolChild {
    param([System.Diagnostics.Process] $Process)
    try { if (-not $Process.HasExited) { Stop-ProcessTree -ProcessId $Process.Id } } catch { }
    try { [void] $Process.WaitForExit(5000) } catch { }
}

# Runs every unit through -Launch, longest expectSec first, at most -MaxParallel at once, and returns one result per
# unit in completion order, always as an array. A child still running its Get-SuiteRunBounds lifetime plus
# $script:PoolBackstopTailMs after its launch is tree-killed and reported HANG; the wrapper's own -TimeoutMs fires first
# in the normal case, so a slot wait or the wrapper's own retry never reads as a hang. -OnPoll runs
# once per poll; whatever ends the pool, a throw included, tree-kills every live child first.
function Invoke-RunPool {
    param(
        [Parameter(Mandatory)] [object[]] $Units,
        [Parameter(Mandatory)] [string] $ResultsRoot,
        [Parameter(Mandatory)] [scriptblock] $Launch,
        [ValidateRange(1, [int]::MaxValue)] [int] $MaxParallel = 1,
        [scriptblock] $OnPoll = $null
    )
    if (-not $PSBoundParameters.ContainsKey('MaxParallel')) { $MaxParallel = [math]::Max(1, (Get-RunPoolWidth).Count) }
    $tags = @{}
    # Case-insensitive, like the file system the run dirs live on.
    $dirs = @{}
    foreach ($u in $Units) {
        if (-not $u.label -or $null -eq $u.timeoutMs) { throw 'Invoke-RunPool: every unit needs a label and a timeoutMs' }
        $t = Get-RunTag -Label $u.label
        if ($tags.ContainsKey($t)) { throw "Invoke-RunPool: units '$($tags[$t])' and '$($u.label)' derive the same run tag $t" }
        $tags[$t] = $u.label
        $d = Get-SafeRunLabel -Label $u.label -Repo $script:PoolRepo -ResultsRoot $ResultsRoot
        if ($dirs.ContainsKey($d)) { throw "Invoke-RunPool: units '$($dirs[$d])' and '$($u.label)' share the run dir $d" }
        $dirs[$d] = $u.label
    }
    $pending = [System.Collections.Generic.Queue[object]]::new()
    foreach ($u in @($Units | Sort-Object -Property @{ Expression = { [double] $_.expectSec }; Descending = $true } -Stable)) { $pending.Enqueue($u) }
    $running = [System.Collections.Generic.List[object]]::new()
    $results = [System.Collections.Generic.List[object]]::new()
    try {
        while ($pending.Count -gt 0 -or $running.Count -gt 0) {
            while ($pending.Count -gt 0 -and $running.Count -lt $MaxParallel) {
                $u = $pending.Dequeue()
                $p = & $Launch $u
                if ($p -isnot [System.Diagnostics.Process]) { throw "Invoke-RunPool: -Launch returned no process for '$($u.label)'" }
                $running.Add([pscustomobject] @{ Unit = $u; Proc = $p; Sw = [System.Diagnostics.Stopwatch]::StartNew()
                                                 BackstopMs = (Get-SuiteRunBounds -TimeoutMs $u.timeoutMs).LifetimeMs + $script:PoolBackstopTailMs })
            }
            Start-Sleep -Milliseconds $script:PoolPollMs
            if ($OnPoll) { & $OnPoll }
            foreach ($c in @($running)) {
                $killed = $false
                if (-not $c.Proc.HasExited) {
                    if ($c.Sw.ElapsedMilliseconds -lt $c.BackstopMs) { continue }
                    Stop-PoolChild -Process $c.Proc
                    $killed = $true
                }
                [void] $c.Proc.WaitForExit(5000)
                $out = Get-SuiteChildOutPath -ResultsRoot $ResultsRoot -Label $c.Unit.label
                $lines = if (Test-Path -LiteralPath $out) { @(Get-Content -LiteralPath $out -ErrorAction SilentlyContinue) } else { @() }
                $code = if ($killed) { $null } else { $c.Proc.ExitCode }
                $results.Add((ConvertFrom-WrapperOutput -Unit $c.Unit -Lines $lines -ExitCode $code -ElapsedSec $c.Sw.Elapsed.TotalSeconds `
                    -BackstopKilled:$killed -OutPath $out))
                [void] $running.Remove($c)
            }
        }
    } finally {
        foreach ($c in $running) { Stop-PoolChild -Process $c.Proc }
    }
    return , $results.ToArray()
}
