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

function Set-AgentEngineUserData {
    param([Parameter(Mandatory)] [string] $Repo)
    $json = & python3 (Join-Path $PSScriptRoot 'engine_userdata.py') --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw 'Engine user data isolation failed; refusing engine launch' }
    $values = $json | ConvertFrom-Json -AsHashtable
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process') }
    [Console]::Error.WriteLine("USERDIR=$($values.USERDIR)")
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
    Start-Process -FilePath 'pwsh' -WindowStyle Hidden -PassThru `
        -ArgumentList @('-NoProfile', '-Command', $command)
}

# Native redirection inside an isolated pwsh keeps Start-Process's inherited handles away from the
# wrapper caller. Do not invoke the native command with `& ... > file`: PowerShell then waits on
# its native stdout reader after the native root exits, until a lingering grandchild closes it.
function Start-IsolatedNativeProcess {
    param([string] $FilePath, [string[]] $Arguments, [string] $OutPath, [string] $ErrPath)
    $q = { param($s) "'" + ($s -replace "'", "''") + "'" }
    $tokens = @($Arguments | ForEach-Object { & $q ('"' + ($_ -replace '"', '\"') + '"') }) -join ','
    $command = "`$p = Start-Process -FilePath $(& $q $FilePath) -ArgumentList @($tokens) -NoNewWindow -PassThru " +
        "-RedirectStandardOutput $(& $q $OutPath) -RedirectStandardError $(& $q $ErrPath); " +
        '$null = $p.Handle; while (-not $p.WaitForExit(500)) { }; exit $p.ExitCode'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process -FilePath 'pwsh' -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-EncodedCommand', $encoded)
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

# A project that pins a runner build able to skip the held recompile declares it in GateTargetPin.ps1, which
# defines Test-GdUnitRecompileSkipSupport -Repo and Get-GateTargetPinRequirement. Without that file no target is
# barred and every standalone run recompiles.
$script:GateTargetPinFile = Join-Path $PSScriptRoot 'GateTargetPin.ps1'
if (Test-Path -LiteralPath $script:GateTargetPinFile) {
    . $script:GateTargetPinFile
} else {
    function Test-GdUnitRecompileSkipSupport {
        param([Parameter(Mandatory)] [string] $Repo)
        return $false
    }
}

function Assert-GateTargetCompatible {
    param([Parameter(Mandatory)] [string] $Repo)
    if (-not (Test-Path -LiteralPath $script:GateTargetPinFile)) { return }
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
$script:SuiteDurationExtraMs = 90000
function Get-HeadroomMs {
    param([double] $ExpectedSec, [double] $ExtraMs)
    [int][math]::Ceiling([math]::Max($script:SuiteTimeoutFloorMs, ($ExpectedSec * $script:SuiteDurationHeadroomFactor * 1000 + $ExtraMs)))
}
function Get-SuiteTimeoutMs {
    param([string] $Repo, [string] $Filter, [string] $LastGreen = '')
    $expected = 0.0
    try {
        if ($LastGreen -and (Test-Path -LiteralPath $LastGreen)) {
            $green = Get-Content -LiteralPath $LastGreen -Raw | ConvertFrom-Json
            if ($green.filter -ceq $Filter -and [double] $green.elapsedSec -gt 0) {
                return Get-HeadroomMs -ExpectedSec ([double] $green.elapsedSec) -ExtraMs $script:SuiteLastGreenExtraMs
            }
        }
        # A tier sub-filter takes its whole tier's duration, an upper bound; a part naming no known tier or
        # unit adds nothing, so a mixed filter keeps the headroom of the parts it can estimate.
        # `&` joins a selection to its exclusions; a conjunction runs at most its widest part, so each part
        # estimates alone and a `!~` exclusion, which selects nothing, adds nothing.
        $parts = @($Filter -replace '[()]', '' -split '[|&]' | Where-Object { $_ -notmatch '!' })
        $tiers = @{}
        $chosen = @{}
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
                if (-not $file) { continue }
                $rel = $file.FullName.Substring($testsRoot.Length + 1) -replace '\\', '/'
                $part = 'FullyQualifiedName~Tests.' + (($rel -split '/' | Select-Object -SkipLast 1) -join '.')
            }
            if ($part -match '^FullyQualifiedName~Tests\.(Logic|Sanity)(?:\.[\w.]*)?$') { $tiers[$Matches[1]] = $true; continue }
            if ($part -notmatch '^FullyQualifiedName~Tests\.Integration(?:\.([\w]+)(?:\.[\w.]*)?)?$') { continue }
            $segment = $Matches[1]
            if (-not $man) { $man = Get-Content -LiteralPath (Join-Path $Repo 'Tests/integration_batch_durations.json') -Raw | ConvertFrom-Json }
            $names = @($man.units.PSObject.Properties.Name | Where-Object { -not $segment -or $_ -eq $segment })
            foreach ($name in $names) { $chosen[$name] = [double] $man.units.$name.seconds }
        }
        if (-not $tiers.Count -and -not $chosen.Count) { return $script:SuiteTimeoutFloorMs }
        if ($tiers.Count) {
            $baseline = Get-Content -LiteralPath (Join-Path $Repo 'Tests/regression_baseline.json') -Raw | ConvertFrom-Json
            foreach ($tier in $tiers.Keys) { $expected += [double] $baseline.suites.$tier.duration_sec }
        }
        foreach ($value in $chosen.Values) { $expected += $value }
    } catch { return $script:SuiteTimeoutFloorMs }
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

function Get-ProcSnapshot {
    $map = @{}
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { $map[[int] $p.ProcessId] = $p }
    return $map
}

function Test-UnderRoot {
    param([string] $Text, [string] $Root)
    if ([string]::IsNullOrEmpty($Text)) { return $false }
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
    # `dotnet ... test ...` -- the wrapper's own launch form.
    return ($name -match '^dotnet(\.exe)?$' -and $cl -match '\stest(\s|$)')
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
    #    invisible to Get-LiveEditor for those seconds. The queued path's continuous re-check
    #    converts that miss into an abort-and-requeue once the window appears.
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
    $out = @()
    foreach ($p in @(Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue)) {
        $ci = $Map[$p.Id]
        if ($ci) { $out += $ci }
    }
    return $out
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
        [string[]] $CandidateRoots
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
            if ((Get-ProcVerdict $ci $Map $script:GodotNoSuchRoot 1) -eq 'ORPHAN') { $out += $ci }
        }
    }
    return $out
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
