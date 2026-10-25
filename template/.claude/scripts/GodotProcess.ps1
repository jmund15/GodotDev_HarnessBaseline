#requires -Version 7
<#
.SYNOPSIS
  Single home for "what is this Godot process, and whose checkout is it".

.OWNS
  Godot process IDENTITY (role classification) and CHECKOUT ATTRIBUTION, plus the worktree
  attribution engine every caller shares (Get-WorktreeId / Get-ProcSnapshot / Test-UnderRoot /
  Get-ProcVerdict, relocated here from run_test_suite.ps1). No caller re-derives any of this;
  run_test_suite.ps1 and regression_gate.ps1 dot-source this file.

.KILL POLICY -- two axes, BOTH required before anything dies.
  ROLE:      only TestRunner and Orphan are ever killable. Editor, Playtest and Unknown are
             never killed. Unknown is SPARED, not reaped -- a surviving orphan costs one retry
             that Wait-PipeDrained already absorbs; a killed editor costs unsaved work.
  OWNERSHIP: a live TestRunner on this checkout may belong to a CONCURRENT SESSION sharing the
             checkout. Preflight (before we launch anything) reaps Orphan ONLY -- a peer's live
             runner has a live parent, so it is not an Orphan and is spared. Only once we own a
             `dotnet test` root may TestRunners under that root (-OwnRootPid) be reaped.

.WHY NOT MainWindowTitle
  The retired idiom (`MainWindowTitle -notlike '*Godot Engine*'`) identifies by NEGATION, so every
  process the harness has no model of defaults into the kill set. Measured on 4.7.1 mono: an editor
  in its first seconds of startup has an EMPTY title, and a playtest window is titled with the
  PROJECT name -- both were killed. Positive identification (command line, parent chain, window
  handle) is unambiguous and is what this file implements.

.NOTES
  Dot-sourced flat .ps1 (house pattern -- no .psm1 anywhere in this harness). Windows-only
  (Win32_Process). `. (Join-Path $PSScriptRoot 'GodotProcess.ps1')`
#>

# The host seams: every script asks these for the platform and its scratch and data roots, and none reads
# $env:TEMP or $env:LOCALAPPDATA itself. -Platform lets a proof drive either branch from planted environment.
function Get-HostPlatform {
    if ($IsWindows) { return 'windows' }
    if ($IsLinux) { return 'linux' }
    throw 'unsupported host platform: the harness runs on Windows and Linux'
}

function Get-HarnessTempRoot {
    param([string] $Platform = (Get-HostPlatform))
    switch ($Platform) {
        'windows' { return $env:TEMP }
        'linux' {
            $root = "$env:TMPDIR".TrimEnd('/')
            return $(if ($root) { $root } else { '/tmp' })
        }
        default { throw "unsupported platform '$Platform': the harness runs on windows and linux" }
    }
}

function Get-HarnessDataRoot {
    param([string] $Platform = (Get-HostPlatform))
    switch ($Platform) {
        'windows' { return $env:LOCALAPPDATA }
        'linux' {
            if ($env:XDG_DATA_HOME) { return $env:XDG_DATA_HOME }
            return "$("$env:HOME".TrimEnd('/'))/.local/share"
        }
        default { throw "unsupported platform '$Platform': the harness runs on windows and linux" }
    }
}

function Set-AgentEngineUserData {
    param([Parameter(Mandatory)] [string] $Repo)
    $json = & python3 (Join-Path $PSScriptRoot 'engine_userdata.py') --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw 'Engine user data isolation failed; refusing engine launch' }
    $values = $json | ConvertFrom-Json -AsHashtable
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process') }
    [Console]::Error.WriteLine("USERDIR=$($values.USERDIR)")
}

# The pwsh argv for a child tool run against the same target checkout: every runner forwards -Repo this way.
function Get-RunnerArgv {
    param([Parameter(Mandatory)] [string] $Script, [Parameter(Mandatory)] [string] $Repo, [string[]] $ScriptArgs = @())
    @('-NoProfile', '-File', $Script, '-Repo', $Repo) + $ScriptArgs
}

# The flags a detached gate child re-receives from its launcher.
function Get-GateArgs {
    param([Parameter(Mandatory)] [string] $Repo, [switch] $StaticOnly, [switch] $NoBaselineUpdate, [switch] $RetryOnly, [switch] $NoReuse)
    $a = @('-Repo', $Repo)
    if ($StaticOnly)       { $a += '-StaticOnly' }
    if ($NoBaselineUpdate) { $a += '-NoBaselineUpdate' }
    if ($RetryOnly)        { $a += '-RetryOnly' }
    if ($NoReuse)          { $a += '-NoReuse' }
    $a
}

# The gate's -Detach hand-off: spawn the child detached, leave its liveness sidecar beside OUT, print the lines a caller polls.
function Invoke-GateDetach {
    param([Parameter(Mandatory)] [string] $ScriptPath, [Parameter(Mandatory)] [string[]] $ChildArgs,
          [Parameter(Mandatory)] [string] $OutPath, [Parameter(Mandatory)] [string] $ErrPath, [string] $Mode = '')
    $child = Start-DetachedPwshScript -ScriptPath $ScriptPath -Arguments $ChildArgs -OutPath $OutPath -ErrPath $ErrPath
    try { "pid=$($child.Id) start=$((Get-ProcessStartStamp -ProcessId $child.Id).ToString('yyyy-MM-ddTHH:mm:ssZ'))" | Set-Content -LiteralPath ([System.IO.Path]::ChangeExtension($OutPath, '.pid')) -Encoding ascii } catch { }
    Write-Output "DETACHED pid=$($child.Id) mode=$Mode"
    Write-Output "OUT=$OutPath"
    Write-Output "ERR=$ErrPath"
    Write-Output 'WAIT=poll OUT until a line begins VERDICT= (the run is over only then)'
}

# --- Detached launch -------------------------------------------------------------------
# Start-Process with -RedirectStandard* spawns with handle inheritance on, so the child also holds
# the CALLER's stdout: `regression_gate.ps1 -Detach | grep` blocked until the gate finished. Here the
# child redirects its own streams, and Start-Process (no redirection) hands it no handle.
# -Arguments: a token like `-Name` stays a parameter name; every other token is quoted as a value.
function Start-DetachedPwshScript {
    param(
        [Parameter(Mandatory)] [string] $ScriptPath,
        [string[]] $Arguments = @(),
        [Parameter(Mandatory)] [string] $OutPath,
        [Parameter(Mandatory)] [string] $ErrPath
    )
    $q = { param($s) "'" + ($s -replace "'", "''") + "'" }
    $tokens = foreach ($a in $Arguments) { if ($a -match '^-[A-Za-z][A-Za-z0-9]*$') { $a } else { & $q $a } }
    $command = "& $(& $q $ScriptPath) $($tokens -join ' ') 1> $(& $q $OutPath) 2> $(& $q $ErrPath); exit `$LASTEXITCODE"
    Start-HiddenPwsh -PwshArgs @('-NoProfile', '-Command', $command)
}

# Godot's runtime tests need a display. A Linux host with xvfb-run starts the launch under a virtual one on a
# free server number; Windows and a host without xvfb-run launch unchanged.
# -XvfbRun names the binary (empty: none); unset, it is looked up on PATH.
function Get-HeadlessDisplayArgv {
    param([Parameter(Mandatory)] [string] $FilePath, [string[]] $Arguments = @(),
          [string] $Platform = (Get-HostPlatform), [string] $XvfbRun)
    $xvfb = if ($PSBoundParameters.ContainsKey('XvfbRun')) { $XvfbRun }
            elseif ($Platform -eq 'linux') { [string] (Get-Command xvfb-run -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
            else { '' }
    if ($Platform -ne 'linux' -or -not $xvfb) { return [pscustomobject]@{ FilePath = $FilePath; Arguments = $Arguments } }
    [pscustomobject]@{ FilePath = $xvfb; Arguments = @('--auto-servernum', $FilePath) + $Arguments }
}

# The bash that runs a repo's .sh launchers. A bare `bash` from PowerShell or a program-spawned shell can resolve
# to WSL's System32\bash.exe on Windows, which cannot read a Windows path (this launch exited 127,
# "godot_bin.sh: No such file"), so Windows asks for Git for Windows' own bash.exe.
function Get-GitBash {
    param([string] $Platform = (Get-HostPlatform))
    if ($Platform -eq 'linux') {
        $b = Get-Command bash -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($b) { return $b.Source }
        throw 'Get-GitBash: no bash on PATH'
    }
    $exec = & git --exec-path 2>$null
    if ($exec) {
        $bash = Join-Path ([IO.Path]::GetFullPath((Join-Path $exec '../../..'))) 'bin\bash.exe'
        if (Test-Path -LiteralPath $bash) { return $bash }
    }
    $c = Get-Command bash -All -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\(system32|WindowsApps)\\' } | Select-Object -First 1
    if ($c) { return $c.Source }
    throw 'Get-GitBash: no Git for Windows bash.exe found (git --exec-path, PATH); WSL bash cannot run a Windows path'
}

# A pwsh child that outlives its launcher without a window and without the launcher's stdio. Windows hides the
# window (-WindowStyle is a hard error elsewhere). Linux starts it through `setsid -w` with its stdio on /dev/null,
# so it holds none of the caller's pipes and survives the launcher's session; the returned process is that
# setsid, which waits and passes the child's exit code on.
function Start-HiddenPwsh {
    param([Parameter(Mandatory)] [string[]] $PwshArgs, [string] $Platform = (Get-HostPlatform))
    if ($Platform -eq 'windows') { return (Start-Process -FilePath 'pwsh' -WindowStyle Hidden -PassThru -ArgumentList $PwshArgs) }
    # ProcessStartInfo.ArgumentList hands each argument over whole; Start-Process -ArgumentList re-splits them on spaces.
    $psi = [System.Diagnostics.ProcessStartInfo]::new('sh')
    foreach ($a in (@('-c', 'exec setsid -w "$@" </dev/null >/dev/null 2>&1', 'sh', 'pwsh') + $PwshArgs)) { $psi.ArgumentList.Add($a) }
    $psi.UseShellExecute = $false
    [System.Diagnostics.Process]::Start($psi)
}

# Native redirection inside an isolated pwsh keeps Start-Process's inherited handles away from the
# wrapper caller. Do not invoke the native command with `& ... > file`: PowerShell then waits on
# its native stdout reader after the native root exits, until a lingering grandchild closes it.
function Start-IsolatedNativeProcess {
    # -Display: the launch runs Godot's runtime, so it takes the virtual display a Linux host needs.
    param([string] $FilePath, [string[]] $Arguments, [string] $OutPath, [string] $ErrPath, [switch] $Display)
    if ($Display) {
        $wrapped = Get-HeadlessDisplayArgv -FilePath $FilePath -Arguments $Arguments
        $FilePath, $Arguments = $wrapped.FilePath, $wrapped.Arguments
    }
    $q = { param($s) "'" + ($s -replace "'", "''") + "'" }
    $tokens = @($Arguments | ForEach-Object { & $q ('"' + ($_ -replace '"', '\"') + '"') }) -join ','
    $command = "`$p = Start-Process -FilePath $(& $q $FilePath) -ArgumentList @($tokens) -NoNewWindow -PassThru " +
        "-RedirectStandardOutput $(& $q $OutPath) -RedirectStandardError $(& $q $ErrPath); " +
        '$null = $p.Handle; while (-not $p.WaitForExit(500)) { }; exit $p.ExitCode'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-HiddenPwsh -PwshArgs @('-NoProfile', '-EncodedCommand', $encoded)
}

function Resolve-TestRepo {
    param([string] $Repo = '', [Parameter(Mandatory)] [string] $ScriptsRoot)
    if ($Repo) { return (Resolve-Path -LiteralPath $Repo -ErrorAction Stop).Path }
    $root = & git -C (Get-Location).Path rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $root -and (Test-Path -LiteralPath (Join-Path ([string] $root) 'project.godot'))) {
        return [IO.Path]::GetFullPath([string] $root)
    }
    (Get-Item -LiteralPath $ScriptsRoot).Parent.Parent.FullName
}

# A project that pins a runner build able to skip the held recompile declares it in a `*.pin.ps1` beside this
# script, which defines Test-GdUnitRecompileSkipSupport -Repo and Get-GateTargetPinRequirement. Without one no
# target is barred and every standalone run recompiles.
$script:GatePinFiles = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.pin.ps1' -File -ErrorAction SilentlyContinue)
if ($script:GatePinFiles.Count -gt 0) {
    foreach ($pinFile in $script:GatePinFiles) { . $pinFile.FullName }
} else {
    function Test-GdUnitRecompileSkipSupport {
        param([Parameter(Mandatory)] [string] $Repo)
        return $false
    }
}

# A locked desktop slows a windowed engine about 40x; the lock screen is a LogonUI.exe in this session.
function Test-InteractiveSessionLocked {
    try {
        $sid = (Get-Process -Id $PID).SessionId
        return (@(Get-Process -Name LogonUI -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $sid }).Count -gt 0)
    } catch { return $false }
}

function Assert-GateTargetCompatible {
    param([Parameter(Mandatory)] [string] $Repo)
    if ($script:GatePinFiles.Count -eq 0) { return }
    if (-not (Test-GdUnitRecompileSkipSupport -Repo $Repo)) {
        Write-Output "VERDICT=BLOCKED reason=target requires $(Get-GateTargetPinRequirement) (held recompile skip); merge main first  repo=$Repo"
        exit 4
    }
}

# Healthy first-test observations span 7–66 s (cadence_godot2_report F4/F6). A 90 s runtime-log
# boot window leaves 24 s beyond that cold-reimport maximum; the attempt still retries once.
$script:SuiteBootTimeoutMs = 90000
# A short healthy run costs up to 66 s before its first test; the 150 s floor leaves build/reimport
# room. Known duration bodies get 1.5x headroom plus 90 s boot/build; a last-green wall gets 1.5x + 30 s.
$script:SuiteTimeoutFloorMs = 150000
$script:SuiteDurationHeadroomFactor = 1.5
$script:SuiteLastGreenExtraMs = 30000
# A cold checkout's first GdUnit4 recompile pass; the 150 s floor plus this is the 400 s cap that cleared cold runs.
$script:SuiteColdRecompileExtraMs = 250000
$script:SuiteDurationExtraMs = 90000
function Get-HeadroomMs {
    param([double] $ExpectedSec, [double] $ExtraMs)
    [int][math]::Ceiling([math]::Max($script:SuiteTimeoutFloorMs, ($ExpectedSec * $script:SuiteDurationHeadroomFactor * 1000 + $ExtraMs)))
}
function Read-SuiteManifest {
    param([Parameter(Mandatory)] [string] $Path)
    try { Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "Unreadable suite manifest $Path : $($_.Exception.Message)" }
}
function Get-SuiteTimeoutMs {
    param([string] $Repo, [string] $Filter, [string] $LastGreen = '')
    $expected = 0.0
    if ($LastGreen -and (Test-Path -LiteralPath $LastGreen)) {
        $green = Read-SuiteManifest -Path $LastGreen
        if ($green.filter -ceq $Filter -and [double] $green.elapsedSec -gt 0) {
            return Get-HeadroomMs -ExpectedSec ([double] $green.elapsedSec) -ExtraMs $script:SuiteLastGreenExtraMs
        }
    }
    # A tier sub-filter takes its whole tier's duration, an upper bound. A part naming no known tier,
    # unit or test file could select anything, so it adds the widest known duration: an unknown part
    # reads as the longest run, never as zero (a name prefix such as `EngagementLadder` hung at the floor).
    # `&` joins a selection to its exclusions; a conjunction runs at most its widest part, so each part
    # estimates alone and a `!~` exclusion, which selects nothing, adds nothing.
    $parts = @($Filter -replace '[()]', '' -split '[|&]' | Where-Object { $_ -notmatch '!' })
    $tiers = @{}
    $chosen = @{}
    $unknown = 0
    $man = $null
    foreach ($part in $parts) {
        # vstest reads an operator-less part as FullyQualifiedName~<part>.
        if ($part -match '^[\w.]+$') { $part = "FullyQualifiedName~$part" }
        # A class name, bare or dotted (Folder.Class, Class.Test), stands for the tier or unit its test
        # file lives in: the rightmost segment that names a test file is the class.
        if ($part -match '^FullyQualifiedName~([\w.]+)$' -and $Matches[1] -notmatch '^Tests(\.|$)') {
            $testsRoot = (Get-Item -LiteralPath (Join-Path $Repo 'Tests')).FullName.TrimEnd('\', '/')
            $segments = @($Matches[1].Trim('.') -split '\.')
            [array]::Reverse($segments)
            $file = $null
            foreach ($segment in $segments) {
                $file = Get-ChildItem -LiteralPath $testsRoot -Recurse -File -Filter "$segment.cs" -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($file) { break }
            }
            if (-not $file) { $unknown++; continue }
            $rel = $file.FullName.Substring($testsRoot.Length + 1) -replace '\\', '/'
            $part = 'FullyQualifiedName~Tests.' + (($rel -split '/' | Select-Object -SkipLast 1) -join '.')
        }
        if ($part -match '^FullyQualifiedName~Tests\.(Logic|Sanity)(?:\.[\w.]*)?$') { $tiers[$Matches[1]] = $true; continue }
        if ($part -notmatch '^FullyQualifiedName~Tests\.Integration(?:\.([\w]+)(?:\.[\w.]*)?)?$') { $unknown++; continue }
        $segment = $Matches[1]
        if (-not $man) { $man = Read-SuiteManifest -Path (Join-Path $Repo 'Tests/integration_batch_durations.json') }
        $names = @($man.units.PSObject.Properties.Name | Where-Object { -not $segment -or $_ -eq $segment -or $_.StartsWith($segment + '.') })
        if (-not $names.Count) { $unknown++ }
        foreach ($name in $names) { $chosen[$name] = [double] $man.units.$name.seconds }
    }
    if (-not $tiers.Count -and -not $chosen.Count -and -not $unknown) { return $script:SuiteTimeoutFloorMs }
    $baselinePath = Join-Path $Repo 'Tests/regression_baseline.json'
    $baseline = $null
    if ($tiers.Count) {
        $baseline = Read-SuiteManifest -Path $baselinePath
        foreach ($tier in $tiers.Keys) { $expected += [double] $baseline.suites.$tier.duration_sec }
    }
    foreach ($value in $chosen.Values) { $expected += $value }
    if ($unknown) {
        # Only the manifests that exist inform the widest duration; with neither, no estimate exists.
        $unitsPath = Join-Path $Repo 'Tests/integration_batch_durations.json'
        if (-not $man -and (Test-Path -LiteralPath $unitsPath)) { $man = Read-SuiteManifest -Path $unitsPath }
        if (-not $baseline -and (Test-Path -LiteralPath $baselinePath)) { $baseline = Read-SuiteManifest -Path $baselinePath }
        $known = @()
        if ($man) { $known += @($man.units.PSObject.Properties | ForEach-Object { [double] $_.Value.seconds }) }
        if ($baseline) { $known += @($baseline.suites.PSObject.Properties | ForEach-Object { [double] $_.Value.duration_sec }) }
        # No filter selects more than every known unit and tier together, so that sum bounds the estimate.
        if ($known.Count) {
            $widest = ($known | Measure-Object -Maximum).Maximum
            $expected = [math]::Min($expected + $unknown * $widest, ($known | Measure-Object -Sum).Sum)
        }
    }
    if ($expected -le 0) { return $script:SuiteTimeoutFloorMs }
    Get-HeadroomMs -ExpectedSec $expected -ExtraMs $script:SuiteDurationExtraMs
}

function Wait-SuiteAttempt {
    param($Process, [int] $TimeoutMs, [string] $GodotLog, [switch] $NoGodotRuntime,
          [int] $BootTimeoutMs = $script:SuiteBootTimeoutMs, [int] $PollMs = 500)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $bootAt = -1L
    $connected = $false
    while ($true) {
        $remaining = $TimeoutMs - $clock.ElapsedMilliseconds
        if ($remaining -le 0) { return 'cap-hang' }
        if ($Process.WaitForExit([int][math]::Min($PollMs, $remaining))) { return 'exited' }
        if (-not $NoGodotRuntime -and -not $connected -and (Test-Path -LiteralPath $GodotLog)) {
            if ($bootAt -lt 0) { $bootAt = $clock.ElapsedMilliseconds }
            $connected = [bool](Select-String -LiteralPath $GodotLog -SimpleMatch 'Waiting for client connecting' -Quiet -ErrorAction SilentlyContinue)
            if (-not $connected -and $clock.ElapsedMilliseconds - $bootAt -ge $BootTimeoutMs) { return 'boot-hang' }
        }
    }
}

function Save-HungGodotLog {
    param([string] $GodotLog, [int] $Attempt)
    $path = Join-Path (Split-Path $GodotLog) "godot_test.hang$Attempt.log"
    if (Test-Path -LiteralPath $GodotLog) { Copy-Item -LiteralPath $GodotLog -Destination $path -Force }
    else { Set-Content -LiteralPath $path -Value 'No runtime Godot log was created by this attempt.' }
    Write-Host "HANG_LOG=$path"
}

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

# The forked gdUnit4.api truncates GDUNIT4_PIPE_SUFFIX past this length:
# GodotRuntimeTestRunner.cs:42 `PIPE_SUFFIX_MAX_LENGTH = 16`.
$PipeSuffixMax = 16

# Leading hex of the label's SHA-256. The default length fills the pipe suffix `<worktreeId>-<tag>` exactly.
function Get-RunTag {
    param([Parameter(Mandatory)] [string] $Label, [ValidateRange(1, 64)] [int] $Chars = ($PipeSuffixMax - (Get-WorktreeId 'x').Length - 1))
    $hash = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($Label)))
    return $hash.Substring(0, $Chars)
}

# Where run_test_suite.ps1 writes its per-label .log and .err.log.
function Get-SuiteLogDir {
    param([Parameter(Mandatory)] [string] $Repo)
    Join-Path $Repo '.claude\scratch\test_runs'
}

# The one label-to-dir rule: a run's dir (and TRX and log file) name. The label, sanitized, capped to the room the
# longest names written from it leave under MAX_PATH (`<safe>.err.log` in the log dir; `<safe>\<safe>.trx` under
# -ResultsRoot), keeping a prefix plus a 12-hex Get-RunTag of the whole label. The wrapper names its run dir with it and
# the run pool its wrapper.out, so the two always share one dir.
function Get-SafeRunLabel {
    param([Parameter(Mandatory)] [string] $Label, [Parameter(Mandatory)] [string] $Repo, [string] $ResultsRoot = '')
    $pathLimit = 259      # Windows' MAX_PATH is 260 characters, the last of them the terminating NUL
    $hashChars = 12       # 48 bits of the label's SHA-256, so truncated labels do not collide
    $cap = $pathLimit - (Get-SuiteLogDir -Repo $Repo).Length - 1 - '.err.log'.Length
    if ($ResultsRoot) {
        $runRoot = [System.IO.Path]::GetFullPath($ResultsRoot, $Repo)
        $cap = [math]::Min($cap, [int][math]::Floor(($pathLimit - $runRoot.Length - 6) / 2))
    }
    $safe = $Label -replace '[^\w.-]', '_'
    if ($safe.Length -gt $cap) { $safe = $safe.Substring(0, $cap - $hashChars - 1) + '_' + (Get-RunTag -Label $Label -Chars $hashChars) }
    $safe
}

# A run_test_suite.ps1 run's bounds from its -TimeoutMs: the lock wait and the attempts (one retry on a hang or a
# silent skip). The lock wait outlasts the longest hold of the holder the run can queue behind (every attempt at the
# longest suite cap, plus a buffer for that holder to finish), whatever this run's own cap: a short unit bounded by its
# own cap starved behind longer ones. LifetimeMs is the longest a healthy wrapper lives before pre-work and teardown,
# which the run pool's backstop sits above.
$script:SuiteLockWaitExtraMs = 120000
# The gate's longest fixed unit (Logic). Dynamic standalone caps can be larger; their own bound
# still widens the wait through Max below.
$script:SuiteLockWaitFloorMs = 480000
function Get-SuiteRunBounds {
    param([Parameter(Mandatory)] [int64] $TimeoutMs)
    $attempts = 2
    $lockWait = $attempts * [math]::Max($TimeoutMs, $script:SuiteLockWaitFloorMs) + $script:SuiteLockWaitExtraMs
    [pscustomobject] @{ LockWaitMs = $lockWait; MaxAttempts = $attempts; LifetimeMs = $lockWait + $attempts * $TimeoutMs }
}

# The process table as pid -> an object with the six members every classifier below reads: ProcessId,
# ParentProcessId, Name, CommandLine, ExecutablePath and CreationDate (local time).
function Get-ProcSnapshot {
    if ((Get-HostPlatform) -eq 'linux') { return Get-LinuxProcSnapshot }
    $map = @{}
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { $map[[int] $p.ProcessId] = $p }
    return $map
}

# The exit a test host that died of an illegal instruction reports: 0xC000001D on Windows, 128 + SIGILL on Linux.
function Test-NativeCrashExit {
    param([int] $ExitCode, [string] $Platform = (Get-HostPlatform))
    if ($Platform -eq 'windows') { return $ExitCode -eq -1073741795 }
    $ExitCode -eq 132
}

function Get-ClockTicksPerSecond {
    $tck = 0
    if ([int]::TryParse((& getconf CLK_TCK 2>$null), [ref] $tck) -and $tck -gt 0) { return $tck }
    100
}

# The identity stamp of a running process, as a UTC time, or $null when it is gone. A recorded stamp is compared
# with a live one minutes later, from another process, so it must not move with the wall clock. Windows has
# StartTime for that. Linux's StartTime is the wall clock minus uptime, cached per process, so two processes read one
# process's start up to seconds apart; the stamp there is the kernel's start
# tick since boot, as a time since the Unix epoch.
function Get-ProcessStartStamp {
    param([Parameter(Mandatory)] [int] $ProcessId, [string] $Platform = (Get-HostPlatform), [string] $ProcRoot = '/proc', [int] $ClkTck = 0)
    try {
        if ($Platform -eq 'windows') { return (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime() }
        $stat = [System.IO.File]::ReadAllText((Join-Path $ProcRoot "$ProcessId/stat"))
        $rest = $stat.Substring($stat.LastIndexOf(')') + 1).Split(' ', [StringSplitOptions]::RemoveEmptyEntries)
        if ($ClkTck -le 0) { $ClkTck = Get-ClockTicksPerSecond }
        return [datetime]::SpecifyKind([datetime]::UnixEpoch.AddTicks([long] $rest[19] * [TimeSpan]::TicksPerSecond / $ClkTck), [DateTimeKind]::Utc)
    }
    catch { return $null }
}

# /proc, shaped like the Win32_Process rows. Name is the stat comm, which the kernel cuts to 15 characters, so
# a Godot reads `Godot_v4.7.1-st`; the comm sits between the first '(' and the LAST ')' because it may hold
# either. A process that exits between the listing and its reads is skipped. -ProcRoot and -ClkTck let a proof
# plant a captured tree.
function Get-LinuxProcSnapshot {
    param([string] $ProcRoot = '/proc', [int] $ClkTck = 0)
    if ($ClkTck -le 0) { $ClkTck = Get-ClockTicksPerSecond }
    $btime = 0
    foreach ($line in [System.IO.File]::ReadLines((Join-Path $ProcRoot 'stat'))) {
        if ($line.StartsWith('btime ')) { $btime = [long] $line.Substring(6).Trim(); break }
    }
    $boot = [DateTimeOffset]::FromUnixTimeSeconds($btime)
    $map = @{}
    foreach ($dir in [System.IO.Directory]::EnumerateDirectories($ProcRoot)) {
        $id = 0
        if (-not [int]::TryParse([System.IO.Path]::GetFileName($dir), [ref] $id)) { continue }
        try {
            $stat = [System.IO.File]::ReadAllText((Join-Path $dir 'stat'))
            $open = $stat.IndexOf('('); $close = $stat.LastIndexOf(')')
            $rest = $stat.Substring($close + 1).Split(' ', [StringSplitOptions]::RemoveEmptyEntries)
            $cmd = ''
            try { $cmd = [System.IO.File]::ReadAllBytes((Join-Path $dir 'cmdline')) } catch { $cmd = [byte[]]@() }
            $exe = $null
            try { $exe = [System.IO.File]::ResolveLinkTarget((Join-Path $dir 'exe'), $false).FullName } catch { }
            $map[$id] = [pscustomobject]@{
                ProcessId       = $id
                ParentProcessId = [int] $rest[1]
                Name            = $stat.Substring($open + 1, $close - $open - 1)
                CommandLine     = ([System.Text.Encoding]::UTF8.GetString($cmd) -replace "`0", ' ').Trim()
                ExecutablePath  = $exe
                CreationDate    = $boot.AddTicks([long] $rest[19] * [TimeSpan]::TicksPerSecond / $ClkTck).LocalDateTime
            }
        }
        catch { continue }
    }
    return $map
}

# Processes in $Map whose parent is in $Known and that are not themselves known. $Since drops a pid-reuse
# impostor: a child cannot be older than the root it descends from.
function Get-TreePids {
    param([hashtable] $Map, [System.Collections.Generic.HashSet[int]] $Known, $Since = $null)
    @($Map.Values | Where-Object {
        $Known.Contains([int] $_.ParentProcessId) -and -not $Known.Contains([int] $_.ProcessId) -and
        (-not $Since -or -not $_.CreationDate -or $_.CreationDate -ge $Since)
    } | ForEach-Object { [int] $_.ProcessId })
}

# Kill a process and everything under it, and emit nothing (callers sit inside value-returning functions).
# A parent that is killed first orphans its children and one that is killed last keeps spawning, so neither
# a single tree snapshot nor taskkill /T is enough. Windows runs taskkill, then sweeps the pids born under any
# pid of the tree since. Linux has no tree kill: it freezes the tree (SIGSTOP the root, then each descendant
# found by repeated /proc passes until a pass finds none new) and SIGKILLs it frozen.
function Stop-ProcessTree {
    param([Parameter(Mandatory)] [int] $ProcessId, [string] $Platform = (Get-HostPlatform))
    $tree = [System.Collections.Generic.HashSet[int]]::new()
    [void] $tree.Add($ProcessId)
    if ($Platform -eq 'windows') {
        $map = Get-ProcSnapshot
        $since = if ($map[$ProcessId]) { $map[$ProcessId].CreationDate } else { $null }
        do { $new = @(Get-TreePids -Map $map -Known $tree -Since $since); foreach ($p in $new) { [void] $tree.Add($p) } } while ($new.Count -gt 0)
        & taskkill.exe '/F' '/T' '/PID' $ProcessId 2>$null | Out-Null
        if (-not $since) { return }
        for ($pass = 0; $pass -lt 5; $pass++) {
            $stragglers = @(Get-TreePids -Map (Get-ProcSnapshot) -Known $tree -Since $since)
            if ($stragglers.Count -eq 0) { break }
            foreach ($p in $stragglers) { [void] $tree.Add($p); & taskkill.exe '/F' '/T' '/PID' $p 2>$null | Out-Null }
        }
        return
    }
    $kill = (Get-Command kill -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $order = [System.Collections.Generic.List[int]]::new()
    & $kill -STOP $ProcessId 2>$null | Out-Null
    $order.Add($ProcessId)
    do {
        $found = @(Get-TreePids -Map (Get-LinuxProcSnapshot) -Known $tree)
        foreach ($p in $found) {
            & $kill -STOP $p 2>$null | Out-Null
            [void] $tree.Add($p)
            $order.Add($p)
        }
    } while ($found.Count -gt 0)
    for ($i = $order.Count - 1; $i -ge 0; $i--) {
        & $kill -KILL $order[$i] 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { & $kill -CONT $order[$i] 2>$null | Out-Null }
    }
}

function Test-UnderRoot {
    param([string] $Text, [string] $Root)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
    # A POSIX root compares with its case and its slashes; the Windows rules below stay as they were.
    if ($Root.StartsWith('/')) {
        if ($Text.IndexOf($Root, [System.StringComparison]::Ordinal) -lt 0) { return $false }
        $posixNested = [regex]::Escape($Root.TrimEnd('/')) + '/\.claude/(?:worktrees|\.cache/[\w.-]*worktrees)/'
        return -not [regex]::IsMatch($Text, $posixNested)
    }
    if ($Text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    # Nested-worktree guard: checkouts live UNDER the main repo root (<main>\.claude\worktrees\<x>
    # or <main>\.claude\.cache\<name>-worktrees\<x>, e.g. baseline-worktrees), so a
    # peer checkout's path also CONTAINS the main root. A reference into any of those dirs belongs
    # to that checkout, not to Root. (When Root itself is a checkout, these infixes never occur in
    # its own paths, so the guard is inert.)
    $nested = [regex]::Escape($Root.TrimEnd('\')) + '\\\.claude\\(?:worktrees|\.cache\\[\w.-]*worktrees)\\'
    return -not [regex]::IsMatch($Text, $nested, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
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

# --- Internals -------------------------------------------------------------------------

# A root string that can never appear in any real path, so Get-ProcVerdict's MINE branch can
# never fire and its return collapses to exactly its ORPHAN test. This is how the orphan question
# is asked WITHOUT a checkout to attribute against -- reusing the one engine rather than forking it.
$script:GodotNoSuchRoot = '\\?\harness-godotprocess-no-such-root\'

function Resolve-RootPath {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try { return ([System.IO.Path]::GetFullPath($Path)).TrimEnd('\', '/') }
    catch { return $Path.TrimEnd('\', '/') }
}

# Walk up to $MaxHops parents, yielding each parent CIM object. Stops at a missing parent or a
# PID-reuse impostor (parent younger than child), matching Get-ProcVerdict's guard.
function Get-ParentChain {
    param([object] $Proc, [hashtable] $Map, [int] $MaxHops = 3)
    $chain = @()
    $cur = $Proc
    for ($hop = 0; $hop -lt $MaxHops; $hop++) {
        $parentId = [int] $cur.ParentProcessId
        $parent   = if ($parentId -gt 0) { $Map[$parentId] } else { $null }
        if (-not $parent) { break }
        if ($parent.CreationDate -and $cur.CreationDate -and $parent.CreationDate -gt $cur.CreationDate) { break }
        $chain += $parent
        $cur = $parent
    }
    return $chain
}

function Test-IsTestHostProc {
    param([object] $Proc)
    if (-not $Proc) { return $false }
    $name = [string] $Proc.Name
    if ($name -match '^(testhost|vstest\.console)(\.exe)?$') { return $true }
    $cl = [string] $Proc.CommandLine
    if ($name -notmatch '^dotnet(\.exe)?$') { return $false }
    # `dotnet ... test ...` -- the wrapper's own launch form; on Linux the test host and vstest console are
    # `dotnet exec .../testhost.dll` and `dotnet exec .../vstest.console.dll`, so their image name is dotnet.
    return ($cl -match '\stest(\s|$)' -or $cl -match '[\\/](testhost|vstest\.console)\.dll(\s|$)')
}

# The two SPARE signatures that also identify an editor when seen on a PARENT (rule 4's target).
function Test-IsEditorProc {
    param([object] $Proc)
    if (-not $Proc) { return $false }
    if ([string] $Proc.Name -notlike 'Godot*') { return $false }
    if (([string] $Proc.CommandLine) -match '(^|\s)(-e|--editor)(\s|$)') { return $true }
    return (Test-HasMainWindow -ProcessId ([int] $Proc.ProcessId))
}

# MainWindowHandle is a Process property, not a Win32_Process one. A process that vanished
# between the snapshot and here has no window as far as we are concerned.
function Test-HasMainWindow {
    param([int] $ProcessId)
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.MainWindowHandle -ne [System.IntPtr]::Zero)
    }
    catch { return $false }
}

# --- Role classification ----------------------------------------------------------------
<#
  ORDERING INVARIANT: every SPARE rule precedes the ORPHAN test. The Orphan test includes
  "dead parent", and interactive processes routinely HAVE dead parents -- a project-manager
  relaunch (the PM exits once the editor launches), a terminal-launched editor whose shell
  closed, an editor surviving an Explorer restart. Evaluating Orphan before the window-handle
  spare would classify exactly those editors killable: the original bug in new clothes.
#>
function Get-GodotRole {
    param(
        [Parameter(Mandatory)] [object] $Proc,
        [hashtable] $Map
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $cl = [string] $Proc.CommandLine

    # 1. Positive test-runner signature on our own command line.
    if ($cl -match 'GdUnit4TestRunnerScene' -or $cl -match '--pipe-name\s+gdunit4-') { return 'TestRunner' }

    $chain = Get-ParentChain -Proc $Proc -Map $Map -MaxHops 3

    # 2. Launched by the test host (the runner's `--path .` is unresolvable; its parent is not).
    foreach ($p in $chain) { if (Test-IsTestHostProc -Proc $p) { return 'TestRunner' } }

    # 3. Explicit editor token.
    if ($cl -match '(^|\s)(-e|--editor)(\s|$)') { return 'Editor' }

    # 4. Child of an editor -> a playtest / run-project window. Interactive: never killed.
    foreach ($p in $chain) { if (Test-IsEditorProc -Proc $p) { return 'Playtest' } }

    # 5. Has a main window -> interactive, so Editor. The window handle is used ONLY to SPARE,
    #    never to kill -- the inverse of the retired title filter. Covers editors launched
    #    without -e (double-clicked project.godot, project-manager relaunch) and playtests whose
    #    owning editor already exited.
    if (Test-HasMainWindow -ProcessId ([int] $Proc.ProcessId)) { return 'Editor' }

    # 6. The existing engine's ORPHAN test, as an OR (both fields unreadable at hop 0, OR a
    #    dead / PID-reuse-impostor DIRECT parent). Narrowing this to an AND would spare a
    #    dead-parent headless Godot -- always ExecutablePath-readable -- leaving it holding the
    #    machine-global pipe, which costs every subsequent run, not one retry.
    #    MaxHops is 1, NOT 3: a zombie's dead link is its DIRECT parent (its testhost died),
    #    while an interactive process's dead ancestor sits deeper -- explorer.exe's own parent
    #    (userinit) is ALWAYS dead, so a 3-hop walk classifies every explorer-launched process
    #    Orphan, including a booting double-clicked editor before its window exists.
    if ((Get-ProcVerdict $Proc $Map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') { return 'Orphan' }

    # 7. A booting editor with no window yet and no -e token lands here: spared from killing,
    #    invisible to Get-LiveEditor for those seconds.
    return 'Unknown'
}

# --- Checkout attribution ---------------------------------------------------------------
function Resolve-GodotCheckout {
    param(
        [Parameter(Mandatory)] [object] $Proc,
        [hashtable] $Map,
        # Roots to test parent-chain / .godot-mtime attribution against. Neither of those paths
        # can INVENT a root, so a caller that cares must supply the checkouts it knows about.
        [string[]] $CandidateRoots = @()
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $cl = [string] $Proc.CommandLine

    # 1. Absolute `--path <dir>` token (present on editors and playtests launched with an
    #    explicit project path). A relative `--path .` -- what the test runner uses -- is
    #    unresolvable from here and falls through to step 2.
    $m = [regex]::Match($cl, '--path\s+(?:"([^"]+)"|(\S+))')
    if ($m.Success) {
        $raw = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
        if ([System.IO.Path]::IsPathRooted($raw)) { return (Resolve-RootPath $raw) }
    }

    $norm = @($CandidateRoots | ForEach-Object { Resolve-RootPath $_ } | Where-Object { $_ })

    # 2. TestRunners: attribute via the parent chain to the owning testhost/vstest, whose
    #    ExecutablePath/CommandLine carries the worktree root (existing Test-UnderRoot logic,
    #    nested-worktree guard included).
    foreach ($root in $norm) {
        if ((Get-ProcVerdict $Proc $Map $root 3) -eq 'MINE') { return $root }
    }

    # 2b. An editor-launcher parent carries no --path while its child window names one explicitly.
    foreach ($kid in @($Map.Values | Where-Object { [int] $_.ParentProcessId -eq [int] $Proc.ProcessId -and [int] $_.ProcessId -ne [int] $Proc.ProcessId })) {
        if ($kid.CreationDate -and $Proc.CreationDate -and $kid.CreationDate -lt $Proc.CreationDate) { continue }
        $km = [regex]::Match([string] $kid.CommandLine, '--path\s+(?:"([^"]+)"|(\S+))')
        if (-not $km.Success) { continue }
        $kraw = if ($km.Groups[1].Success) { $km.Groups[1].Value } else { $km.Groups[2].Value }
        if ([System.IO.Path]::IsPathRooted($kraw)) { return (Resolve-RootPath $kraw) }
    }

    # 3. Editor with no resolvable --path (project-manager or shortcut launch): the checkout whose
    #    .godot/ was touched within this process's lifetime is the one it is actually attached to.
    $start = $Proc.CreationDate
    if ($start) {
        foreach ($root in $norm) {
            $dotGodot = Join-Path $root '.godot'
            try {
                $item = Get-Item -LiteralPath $dotGodot -ErrorAction Stop
                if ($item.LastWriteTime -ge $start) { return $root }
            }
            catch { }
        }
    }

    # 4. Unresolved. Callers handle this conservatively in BOTH directions: never killed, and
    #    treated as MATCHING for the queue decision. Both errors then favour the user.
    return $null
}

# --- Kill / liveness queries -------------------------------------------------------------

function Get-GodotCandidate {
    param([hashtable] $Map)
    return @($Map.Values | Where-Object { [string] $_.Name -like 'Godot*' })
}

function Test-ChainReachesPid {
    param([object] $Proc, [hashtable] $Map, [int] $RootPid, [int] $MaxHops = 6)
    if ([int] $Proc.ProcessId -eq $RootPid) { return $true }
    foreach ($p in (Get-ParentChain -Proc $Proc -Map $Map -MaxHops $MaxHops)) {
        if ([int] $p.ProcessId -eq $RootPid) { return $true }
    }
    return $false
}

# Godot processes whose command line names a path under $RunDir (a pool child's --log-file points into its run dir),
# whatever their parent chain: the one attribution that still finds a run's Godot after its test host exited. The
# trailing separator keeps a runId that shares this one's prefix, and a nested worktree's run dir, out.
function Get-RunDirGodot {
    param([Parameter(Mandatory)] [string] $RunDir, [hashtable] $Map)
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $needle = ([System.IO.Path]::GetFullPath($RunDir).TrimEnd('\', '/') -replace '\\', '/') + '/'
    @($Map.Values | Where-Object {
        [string] $_.Name -like 'Godot*' -and
        ([string] $_.CommandLine -replace '\\', '/').IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

<#
  Processes safe to kill, per the two-axis policy in the file header.
    WITHOUT -OwnRootPid (PREFLIGHT scope): role Orphan only, attributed to $Checkout or
      unattributable. A peer session's live runner has a live parent -> not Orphan -> spared.
    WITH -OwnRootPid: additionally any TestRunner whose parent chain reaches that PID -- the
      `dotnet test` root THIS invocation launched, so it is ours by construction.
  Never returns Editor, Playtest or Unknown.
#>
function Get-ReapableGodot {
    param(
        [Parameter(Mandatory)] [string] $Checkout,
        [hashtable] $Map,
        [int] $OwnRootPid = 0,
        [string[]] $CandidateRoots,
        [string] $Platform = (Get-HostPlatform)
    )
    if (-not $Map) { $Map = Get-ProcSnapshot }
    if (-not $CandidateRoots) { $CandidateRoots = @($Checkout) }
    $target = Resolve-RootPath $Checkout

    $out = @()
    foreach ($ci in (Get-GodotCandidate -Map $Map)) {
        $role = Get-GodotRole -Proc $ci -Map $Map
        if ($role -eq 'Orphan') {
            $own = Resolve-GodotCheckout -Proc $ci -Map $Map -CandidateRoots $CandidateRoots
            if ($null -eq $own -or $own -ieq $target) { $out += $ci }
            continue
        }
        if ($role -eq 'TestRunner') {
            if ($OwnRootPid -gt 0 -and (Test-ChainReachesPid -Proc $ci -Map $Map -RootPid $OwnRootPid)) {
                $out += $ci
                continue
            }
            # Zombie runner: a readable runner signature wins rule 1 before the Orphan test, so
            # role alone never flags a runner whose owning test host is GONE -- yet that is
            # exactly the wedged-pipe holder the orphan sweep exists for. Reapable at every
            # scope: a live peer's runner has a live parent and never matches.
            if (Test-RunnerOrphaned -Proc $ci -Map $Map -Platform $Platform) { $out += $ci }
        }
    }
    return $out
}

# A test runner whose owning test host is gone. Windows drops the dead link from the table, so the engine's
# ORPHAN verdict finds it. Linux reparents the runner to init, a subreaper or a session relay, which stays in
# the table: there the dead link shows as a direct parent that is no test host, because a runner's real parent
# is always its test host.
function Test-RunnerOrphaned {
    param([Parameter(Mandatory)] [object] $Proc, [Parameter(Mandatory)] [hashtable] $Map, [string] $Platform = (Get-HostPlatform))
    if ((Get-ProcVerdict $Proc $Map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') { return $true }
    if ($Platform -ne 'linux') { return $false }
    return -not (Test-IsTestHostProc -Proc $Map[[int] $Proc.ParentProcessId])
}

<#
  Interactive Godot processes (Editor or Playtest) on $Checkout -- the readiness clause the gate
  queue and the suite wrapper's entry guard both test. A process whose checkout resolves to $null
  IS included: unknown checkout is treated as matching, so the ambiguity blocks a run rather than
  risking the user's editor.
#>
function Get-LiveEditor {
    param(
        [string] $Checkout,
        [hashtable] $Map,
        [string[]] $CandidateRoots
    )
    # HARNESS_LIVE_EDITOR_CHECKOUTS (path-separated roots) plants the answer for proofs and unattended routing checks.
    if ($null -ne $env:HARNESS_LIVE_EDITOR_CHECKOUTS) {
        $planted = @($env:HARNESS_LIVE_EDITOR_CHECKOUTS -split [IO.Path]::PathSeparator | Where-Object { $_ } |
            ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\', '/') })
        $want = if ($Checkout) { [IO.Path]::GetFullPath($Checkout).TrimEnd('\', '/') } else { $null }
        return @($planted | Where-Object { -not $want -or $_ -ieq $want } |
            ForEach-Object { [pscustomobject]@{ ProcessId = 0; Name = 'planted'; CommandLine = $_ } })
    }
    if (-not $Map) { $Map = Get-ProcSnapshot }
    if (-not $CandidateRoots -and $Checkout) { $CandidateRoots = @($Checkout) }
    $target = if ($Checkout) { Resolve-RootPath $Checkout } else { $null }

    $out = @()
    foreach ($ci in (Get-GodotCandidate -Map $Map)) {
        $role = Get-GodotRole -Proc $ci -Map $Map
        if ($role -ne 'Editor' -and $role -ne 'Playtest') { continue }
        if (-not $target) { $out += $ci; continue }
        $own = Resolve-GodotCheckout -Proc $ci -Map $Map -CandidateRoots $CandidateRoots
        if ($null -eq $own -or $own -ieq $target) { $out += $ci }
    }
    return $out
}
