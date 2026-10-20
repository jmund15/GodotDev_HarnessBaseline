#requires -Version 7
<#
.SYNOPSIS
  Single home for the test-run locks: the per-checkout lock, the machine's N runtime slots, the
  slot count, slot records, the orphan sweep, the probes, overlap detection, the own-pool overlap
  judgement, the overlap-signature classifier and the trigger log.

.LOCK MODEL
  Every run (runtime or Logic) takes its checkout lock ($script:CheckoutLockPrefix + Get-WorktreeId),
  so runs from one checkout serialize: they share .godot\mono\temp\bin\Debug\ and TestResults\.
  A runtime (Godot-spawning) run then takes runtime slots. There are $script:RuntimeSlotMax slot
  names. Slot 0 is the legacy machine-global name and slot 1 the legacy '-slot1' name, so an
  old-commit wrapper (which knows only those two) still excludes and is excluded on them.
    C>=2 WaitAny(slot C-1, ..., slot 0): holds one slot. WaitAny returns the smallest signaled
         index, so a run takes the highest free slot of 0..C-1 and leaves slot 0 to old commits.
    C=1  slots 0..max-1 in index order: holds every slot, so it excludes every other runtime run.
         Each held slot's record says 'waiting' until the last slot is held, then every record 'running'.
  The checkout lock always comes first and the slot order is fixed, so waits cannot cycle. An
  AbandonedMutexException is an acquisition: the waiter owns the mutex (.NET contract).
  Lowering the count mid-flight: runs already on a slot at or above the new count finish; new runs
  wait only on slots 0..C-1, and a C=1 run waits for all of them.

.COUNT
  min(committed default, <git-common-dir>\runtime-slots, caller ceiling). A default outside
  1..max gives 1. The override file can only lower the count: an integer in 1..max lowers it (a
  value at or above the default leaves it), anything else gives 1, and an unresolvable common dir
  gives 1. A ceiling of 0 means no ceiling; a ceiling above max clamps to max. Read at every
  acquisition and probe; no environment variable.
  Triggers and rollback: .claude/skills/testing/reference/running.md §Runtime slots and parallel dev.

.CONTRACT
  Functions return values on the output stream and print nothing. Each function runs under
  Set-StrictMode -Version Latest (set inside it, so a non-strict caller stays non-strict).
  Dot-sourced flat .ps1, like GodotProcess.ps1, which it dot-sources for Get-WorktreeId.

.HELD CHECKOUT
  A gate holds its checkout lock for its whole run (Enter-HeldCheckout ... Exit-HeldCheckout) and records itself in
  the checkout record checkout-<worktreeId>.json: holderPid, procStart, checkout, built, recompiled. Set-CheckoutStamp
  is the record's one stamp writer. A held child (Enter-RunLocks -HeldCheckout) takes a runtime slot only, and only
  while Get-HeldCheckoutDenials is empty: the holder is live, is the caller or its ancestor (Test-HeldByCaller),
  built is true, and recompiled is true unless the child is the pool's one -RecompilePass run.

.HOLDER RECORD
  Every taker of a checkout lock (Enter-RunLocks, TwinCheckout.ps1's Enter-TwinLock) also writes holder-<worktreeId>.json:
  holderPid, procStart, checkout, label, sessionId, acquiredAt. It exists so a gate that waits on the lock can name who
  holds it. It is separate from the checkout record, whose presence is the admission contract for held children. The write
  is best-effort and skipped when a live record already names the caller (re-entry on the same thread); a taker removes
  only a record it wrote, before it releases the mutex. Get-CheckoutHolder returns the record only while its process lives.
#>

. (Join-Path $PSScriptRoot 'GodotProcess.ps1')
. (Join-Path $PSScriptRoot 'CSharpSource.ps1')

# A name count, not a capacity claim: the default below is the measured count; the measurement and its re-measure
# triggers: auto-memory/archive/gotcha_runtime_suite_pipe_contention.md (running.md §Runtime slots owns the rollback).
$script:RuntimeSlotMax = 8
$script:RuntimeSlotDefault = 6
$script:RuntimeSlotNamePrefix = 'Global\gdunit4-{{PROJECT_NAME}}-runlock'
$script:CheckoutLockPrefix = 'Global\gdunit4-runlock-'
$script:RuntimeSlotRecordRoot = ''     # '' = $env:TEMP\harness-activity\runtime-slots
$script:RuntimeSlotOverrideFile = ''   # '' = <git-common-dir>\runtime-slots
$script:OverlapSignatureKinds = @('testhost-native-crash', 'executor-connect', 'run-aborted', 'cache-file-error')

function Get-RuntimeSlotNames {
    Set-StrictMode -Version Latest
    @(0..($script:RuntimeSlotMax - 1) | ForEach-Object { if ($_ -eq 0) { $script:RuntimeSlotNamePrefix } else { "$($script:RuntimeSlotNamePrefix)-slot$_" } })
}

function Get-CheckoutLockName {
    param([string] $Repo)
    Set-StrictMode -Version Latest
    $script:CheckoutLockPrefix + (Get-WorktreeId $Repo)
}

# The record every checkout-lock taker writes (see .HOLDER RECORD).
function Get-CheckoutHolderName {
    param([string] $Repo)
    Set-StrictMode -Version Latest
    'holder-' + (Get-WorktreeId $Repo)
}

function Get-TestIsolation {
    # The per-checkout isolation every runtime test run takes (run_test_suite.ps1 and the shared runner): the
    # forked gdUnit4.api pipe suffix, and a user dir handed to testhost as an inline VSTest APPDATA with forward
    # slashes, because VSTest doubles a backslash and Godot's DirAccess rejects the result
    # (gotcha_vstest_inline_env_backslash_breaks_godot_diraccess).
    # -Tag (Get-RunTag) makes it a per-run isolation: suffix and user dir leaf become <worktreeId>-<tag>.
    param([Parameter(Mandatory)] [string] $Repo, [string] $Tag = '')
    Set-StrictMode -Version Latest
    $id = Get-WorktreeId $Repo
    $leaf = if ($Tag) { "$id-$Tag" } else { $id }
    if ($leaf.Length -gt $PipeSuffixMax) {
        throw "pipe suffix '$leaf' is $($leaf.Length) chars, over the cap $PipeSuffixMax (worktreeId '$id' + '-' + tag '$Tag')"
    }
    $dir = Join-Path $env:LOCALAPPDATA "{{PROJECT_NAME}}\test-appdata\$leaf"
    [pscustomobject]@{ PipeSuffix = $leaf; AppData = $dir; AppDataArg = "RunConfiguration.EnvironmentVariables.APPDATA=$($dir -replace '\\', '/')" }
}

function Get-RuntimeSlotRecordDir {
    param([string] $RecordDir = '')
    Set-StrictMode -Version Latest
    if ($RecordDir) { return $RecordDir }
    if ($script:RuntimeSlotRecordRoot) { return $script:RuntimeSlotRecordRoot }
    Join-Path $env:TEMP 'harness-activity\runtime-slots'
}

# <git-common-dir>\runtime-slots, or $null when the common dir cannot be resolved.
function Get-RuntimeSlotOverridePath {
    param([string] $Repo)
    Set-StrictMode -Version Latest
    if ($script:RuntimeSlotOverrideFile) { return $script:RuntimeSlotOverrideFile }
    try {
        if (-not $Repo -or -not (Test-Path -LiteralPath $Repo -PathType Container)) { return $null }
        $out = @(& git -C $Repo rev-parse --git-common-dir 2>$null)
        if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0 -or -not ([string]$out[0]).Trim()) { return $null }
        $dir = ([string]$out[0]).Trim()
        if (-not [System.IO.Path]::IsPathRooted($dir)) { $dir = Join-Path $Repo $dir }
        return (Join-Path ([System.IO.Path]::GetFullPath($dir)) 'runtime-slots')
    } catch { return $null }
}

function Get-RuntimeSlotCount {
    param([string] $Repo, [ValidateRange(0, [int]::MaxValue)] [int] $Ceiling = 0, [string] $OverridePath = '')
    Set-StrictMode -Version Latest
    $max = [int] $script:RuntimeSlotMax
    $count = [int] $script:RuntimeSlotDefault
    $source = 'default'
    $raw = $null
    if ($count -lt 1 -or $count -gt $max) { $count = 1 }
    $path = if ($OverridePath) { $OverridePath } else { Get-RuntimeSlotOverridePath -Repo $Repo }
    if (-not $path) {
        $count = 1; $source = 'override-unreadable'
    } elseif (Test-Path -LiteralPath $path -PathType Leaf) {
        try {
            $raw = [System.IO.File]::ReadAllText($path)
            $t = $raw.Trim()
            $v = 0
            if ($t -match '^[0-9]{1,9}$' -and ($v = [int] $t) -ge 1 -and $v -le $max) {
                if ($v -lt $count) { $count = $v; $source = 'override' }
            } else {
                $count = 1; $source = 'override-invalid'
            }
        } catch { $count = 1; $source = 'override-unreadable' }
    } elseif (Test-Path -LiteralPath $path) {
        $count = 1; $source = 'override-unreadable'
    }
    if ($Ceiling -gt 0 -and $Ceiling -lt $count) { $count = $Ceiling; $source = 'ceiling' }
    [pscustomobject]@{ Count = $count; Source = $source; Raw = $raw }
}

# ------------------------------------------------------------------ records and process identity
function Get-ProcStartUtc {
    param([System.Diagnostics.Process] $Process)
    Set-StrictMode -Version Latest
    try { $Process.StartTime.ToUniversalTime().ToString('o') } catch { $null }
}

function ConvertTo-SlotUtc {
    param($Value)
    Set-StrictMode -Version Latest
    if ($null -eq $Value -or '' -eq $Value) { return $null }
    try {
        if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
        return ([datetimeoffset]::Parse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture)).UtcDateTime
    } catch { return $null }
}

# Alive AND the same process that wrote the record (PID reuse guard).
function Test-SlotProcessLive {
    param($ProcessId, $StartUtc)
    Set-StrictMode -Version Latest
    try {
        $id = [int] $ProcessId
        if ($id -le 0) { return $false }
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if (-not $p) { return $false }
        $want = ConvertTo-SlotUtc $StartUtc
        if ($null -eq $want) { return $false }
        return ([math]::Abs(($p.StartTime.ToUniversalTime() - $want).TotalSeconds) -le 2)
    } catch { return $false }
}

# Record names: slot-<n> for a runtime slot, checkout-<worktreeId> for a held checkout. A slot record carries a state;
# a checkout record does not. Per checkout, so gates in two checkouts never share one record.
function Get-CheckoutRecordName {
    param([string] $Repo)
    Set-StrictMode -Version Latest
    'checkout-' + (Get-WorktreeId $Repo)
}

# The record, or $null when absent, or the string 'unreadable' when present but not a record of its kind.
function Read-SlotRecord {
    param([string] $RecordDir, [Parameter(Mandatory)] [string] $Name)
    Set-StrictMode -Version Latest
    $path = Join-Path $RecordDir "$Name.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $rec = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json
        if ($null -eq $rec -or -not $rec.PSObject.Properties['holderPid']) { return 'unreadable' }
        if ($Name -like 'slot-*' -and -not $rec.PSObject.Properties['state']) { return 'unreadable' }
        return $rec
    } catch { return 'unreadable' }
}

# Replaces the record atomically, retrying while a reader holds it. A write that still fails is dropped, or throws with
# -Required: a record a later check depends on must not report success unwritten.
function Write-SlotRecord {
    param([string] $RecordDir, [Parameter(Mandatory)] [string] $Name, $Record, [switch] $Required)
    Set-StrictMode -Version Latest
    $tmp = $null
    try {
        New-Item -ItemType Directory -Force -Path $RecordDir | Out-Null
        $path = Join-Path $RecordDir "$Name.json"
        $tmp = Join-Path $RecordDir ("$Name.$PID.$([guid]::NewGuid().ToString('N')).tmp")
        [System.IO.File]::WriteAllText($tmp, ($Record | ConvertTo-Json -Depth 4))
        $deadline = (Get-Date).AddMilliseconds(500)
        while ($true) {
            try { [System.IO.File]::Move($tmp, $path, $true); return }
            catch [System.IO.IOException], [System.UnauthorizedAccessException] {
                if ((Get-Date) -gt $deadline) { throw }
                Start-Sleep -Milliseconds 10
            }
        }
    } catch {
        if ($tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        if ($Required) { throw "Write-SlotRecord $Name failed: $($_.Exception.Message)" }
    }
}

# The holder record, or $null when absent, unreadable, or its process is gone (a leftover from a dead holder).
function Get-CheckoutHolder {
    param([Parameter(Mandatory)] [string] $Repo, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    $rec = Read-SlotRecord -RecordDir (Get-RuntimeSlotRecordDir -RecordDir $RecordDir) -Name (Get-CheckoutHolderName -Repo $Repo)
    if ($null -eq $rec -or $rec -is [string]) { return $null }
    if (-not $rec.PSObject.Properties['procStart'] -or -not (Test-SlotProcessLive $rec.holderPid $rec.procStart)) { return $null }
    $rec
}

# Writes the caller's holder record unless a live one already names the caller. True only when this call wrote it.
function Write-CheckoutHolder {
    param([Parameter(Mandatory)] [string] $Repo, [string] $Label = '', [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    try {
        $dir = Get-RuntimeSlotRecordDir -RecordDir $RecordDir
        $cur = Get-CheckoutHolder -Repo $Repo -RecordDir $dir
        if ($null -ne $cur -and [int] $cur.holderPid -eq $PID) { return $false }
        $sid = if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } elseif ($env:CLAUDE_CODE_SESSION_ID) { $env:CLAUDE_CODE_SESSION_ID } else { $null }
        Write-SlotRecord -RecordDir $dir -Name (Get-CheckoutHolderName -Repo $Repo) -Record ([ordered]@{
            holderPid  = $PID
            procStart  = (Get-ProcStartUtc (Get-Process -Id $PID))
            checkout   = $Repo
            label      = $Label
            sessionId  = $sid
            acquiredAt = (Get-Date).ToUniversalTime().ToString('o')
        })
        $now = Get-CheckoutHolder -Repo $Repo -RecordDir $dir
        return ($null -ne $now -and [int] $now.holderPid -eq $PID)
    } catch { return $false }
}

# Removes the holder record only when it names the caller. Never throws.
function Remove-CheckoutHolder {
    param([Parameter(Mandatory)] [string] $Repo, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    try {
        $dir = Get-RuntimeSlotRecordDir -RecordDir $RecordDir
        $name = Get-CheckoutHolderName -Repo $Repo
        $rec = Read-SlotRecord -RecordDir $dir -Name $name
        if ($null -ne $rec -and $rec -isnot [string] -and [int] $rec.holderPid -eq $PID) {
            Remove-Item -LiteralPath (Join-Path $dir "$name.json") -Force -ErrorAction SilentlyContinue
        }
    } catch { }
}

function New-SlotRecord {
    param($Lease, [string] $State, $AcquiredAt, [ValidateSet('slot', 'checkout')] [string] $Kind = 'slot')
    Set-StrictMode -Version Latest
    if ($Kind -eq 'checkout') {
        return [ordered]@{
            holderPid  = $PID
            procStart  = $Lease.ProcStart
            checkout   = $Lease.Repo
            label      = $Lease.Label
            acquiredAt = $AcquiredAt
            built      = $false
            recompiled = $false
        }
    }
    [ordered]@{
        holderPid   = $PID
        procStart   = $Lease.ProcStart
        checkout    = $Lease.Repo
        label       = $Lease.Label
        state       = $State
        acquiredAt  = $AcquiredAt
        releasedAt  = $null
        runnerPid   = $null
        runnerStart = $null
    }
}

# The persistent editor-pass stamp is separate from the live gate record, which is removed at release.
function Get-RecompileSnapshot {
    param([Parameter(Mandatory)] [string] $Repo)
    Set-StrictMode -Version Latest
    $root = [IO.Path]::GetFullPath($Repo).TrimEnd('\', '/')
    $paths = [Collections.Generic.List[string]]::new()
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $dir = $pending.Pop()
        foreach ($child in [IO.Directory]::EnumerateDirectories($dir)) {
            if ([IO.Path]::GetFileName($child) -in @('.git', '.godot', '.claude', 'bin', 'obj', 'TestResults')) { continue }
            if ([IO.File]::GetAttributes($child) -band [IO.FileAttributes]::ReparsePoint) { throw "recompile snapshot cannot follow linked directory $child" }
            $pending.Push($child)
        }
        foreach ($file in [IO.Directory]::EnumerateFiles($dir)) {
            if ([IO.Path]::GetExtension($file).ToLowerInvariant() -in @('.cs', '.gd', '.tscn', '.tres', '.import')) { $paths.Add($file) }
        }
    }
    $cs = [Collections.Generic.List[string]]::new()
    $globals = [Collections.Generic.List[string]]::new()
    $names = [Collections.Generic.List[string]]::new()
    $resources = [Collections.Generic.List[string]]::new()
    $hash = { param([string] $text) [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($text))) }
    foreach ($file in @($paths | Sort-Object)) {
        $relative = [IO.Path]::GetRelativePath($root, $file) -replace '\\', '/'
        $ext = [IO.Path]::GetExtension($file).ToLowerInvariant()
        if ($ext -eq '.cs') {
            $cs.Add($relative)
            $text = [IO.File]::ReadAllText($file)
            $code = Remove-CSharpLiterals -Text $text
            $declarations = @([regex]::Matches($code, '(?s)(?:\[[^\]]*\]\s*)+(?:(?:public|internal|private|protected|abstract|sealed|partial|static|new|file)\s+)*class\s+[^\{;]+') |
                Where-Object { $_.Value -match '\b(?:Godot\s*\.\s*)?GlobalClass(?:Attribute)?\b' } | ForEach-Object { $_.Value -replace '\s+', ' ' })
            if ($declarations.Count -gt 0) {
                $namespaces = @([regex]::Matches($code, '\bnamespace\s+[\w.]+') | ForEach-Object { $_.Value -replace '\s+', ' ' })
                $globals.Add("$relative=$(& $hash (($namespaces + $declarations) -join "`n"))")
            } elseif ($code -match '\bGlobalClass(?:Attribute)?\b') {
                # Unrecognized declaration syntax cannot safely be reduced to a class header.
                $globals.Add("$relative=$(& $hash $code)")
            }
        } elseif ($ext -eq '.gd') {
            $declarations = @([regex]::Matches([IO.File]::ReadAllText($file), '(?m)^\s*class_name\s+[^\r\n#]+') | ForEach-Object { ($_.Value -replace '\s+', ' ').Trim() })
            if ($declarations.Count -gt 0) { $names.Add("$relative=$(& $hash ($declarations -join "`n"))") }
        } else {
            $resources.Add("$relative=$([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([IO.File]::ReadAllBytes($file))))")
        }
    }
    [pscustomobject] @{ CsPaths = @($cs.ToArray()); GlobalClasses = @($globals.ToArray()); ClassNames = @($names.ToArray()); Resources = @($resources.ToArray()) }
}

function Get-RecompileDecision {
    param([Parameter(Mandatory)] [string] $Repo, [switch] $Force)
    Set-StrictMode -Version Latest
    $decision = [pscustomobject] @{ Skip = $false; Reason = 'stamp-unreadable'; Snapshot = $null }
    try { $decision.Snapshot = Get-RecompileSnapshot -Repo $Repo }
    catch { $decision.Reason = 'inputs-unreadable'; return $decision }
    if ($Force) { $decision.Reason = 'forced'; return $decision }
    if (-not (Test-GdUnitRecompileSkipSupport -Repo $Repo)) {
        $decision.Reason = 'pin-without-skip-support'; return $decision
    }
    $path = Join-Path $Repo '.godot/gdunit4-recompiled.json'
    try {
        if (-not (Test-Path -LiteralPath $path)) { $decision.Reason = 'stamp-missing'; return $decision }
        $stamp = [IO.File]::ReadAllText($path) | ConvertFrom-Json
        if ($stamp.schema -ne 1 -or -not (ConvertTo-SlotUtc $stamp.completedUtc)) { return $decision }
        $root = [IO.Path]::GetFullPath($Repo).TrimEnd('\', '/')
        if ($stamp.checkout -ne $root) { $decision.Reason = 'stamp-foreign'; return $decision }
        foreach ($field in @('CsPaths', 'GlobalClasses', 'ClassNames', 'Resources')) {
            if (-not $stamp.snapshot.PSObject.Properties[$field] -or $null -eq $stamp.snapshot.$field -or
                $stamp.snapshot.$field -isnot [array] -or @($stamp.snapshot.$field | Where-Object { $_ -isnot [string] }).Count -gt 0) { return $decision }
        }
        $reasons = @{ CsPaths = 'cs-paths-changed'; GlobalClasses = 'globalclass-declarations-changed'; ClassNames = 'class-name-declarations-changed'; Resources = 'resources-changed' }
        foreach ($field in @('CsPaths', 'GlobalClasses', 'ClassNames', 'Resources')) {
            if (($stamp.snapshot.$field | ConvertTo-Json -Compress) -cne ($decision.Snapshot.$field | ConvertTo-Json -Compress)) {
                $decision.Reason = $reasons[$field]; return $decision
            }
        }
        $decision.Skip = $true; $decision.Reason = 'unchanged'
    } catch { $decision.Reason = 'stamp-unreadable' }
    $decision
}

function Set-RecompileStamp {
    param([Parameter(Mandatory)] [string] $Repo, $Snapshot, [string] $Trx)
    Set-StrictMode -Version Latest
    try {
        if ($null -eq $Snapshot -or -not $Trx -or -not (Test-Path -LiteralPath $Trx)) { return }
        [xml] $xml = [IO.File]::ReadAllText($Trx)
        $summary = $xml.SelectSingleNode("/*[local-name()='TestRun']/*[local-name()='ResultSummary']")
        $stdout = $summary.SelectSingleNode("./*[local-name()='Output']/*[local-name()='StdOut']")
        $counters = $summary.SelectSingleNode("./*[local-name()='Counters']")
        if (-not $stdout -or -not $counters -or [int] $counters.executed -lt 1 -or
            $stdout.InnerText -notmatch 'Rebuilding Godot Project ends with exit code:\s*0\b' -or
            $stdout.InnerText -match 'Skipping Godot project recompile|Rebuilding Godot Project fails|Godot compilation TIMEOUT') { return }
        $current = Get-RecompileSnapshot -Repo $Repo
        if (($Snapshot | ConvertTo-Json -Compress) -cne ($current | ConvertTo-Json -Compress)) { return }
        Write-SlotRecord -RecordDir (Join-Path $Repo '.godot') -Name 'gdunit4-recompiled' -Record ([ordered] @{
            schema = 1
            checkout = [IO.Path]::GetFullPath($Repo).TrimEnd('\', '/')
            completedUtc = (Get-Date).ToUniversalTime().ToString('o')
            snapshot = $Snapshot
        })
    } catch { }
}

# ------------------------------------------------------------------ held checkout
# The caller's pid, then its ancestors (Get-ParentChain's PID-reuse guard), 12 hops as Get-OwnerRoot walks.
function Get-CallerChain {
    param([hashtable] $Map)
    Set-StrictMode -Version Latest
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $self = $Map[[int]$PID]
    if ($null -eq $self) { return , [int[]] @($PID) }
    return , [int[]] (@($PID) + @(Get-ParentChain -Proc $self -Map $Map -MaxHops 12 | ForEach-Object { [int] $_.ProcessId }))
}

# True when the record's holder is live (the process that wrote it) and is the caller or one of its ancestors.
function Test-HeldByCaller {
    param($Record, [hashtable] $Map)
    Set-StrictMode -Version Latest
    if ($null -eq $Record -or $Record -is [string]) { return $false }
    if (-not (Test-SlotProcessLive $Record.holderPid $Record.procStart)) { return $false }
    $holder = [int] $Record.holderPid
    if ($holder -eq $PID) { return $true }
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $self = $Map[[int]$PID]
    if ($null -eq $self) { return $false }
    Test-ChainReachesPid -Proc $self -Map $Map -RootPid $holder -MaxHops 12
}

# A process snapshot that contains the caller. A transient CIM failure returns a map without it, which would read as a
# broken ancestry; such a map is retaken. Returns the last map when every try lacks the caller.
function Get-CallerSnapshot {
    param([hashtable] $Map, [int] $Tries = 3, [int] $DelayMs = 250)
    Set-StrictMode -Version Latest
    for ($i = 0; $i -lt $Tries; $i++) {
        if ($null -ne $Map -and $Map.ContainsKey([int]$PID)) { return $Map }
        if ($i -gt 0) { Start-Sleep -Milliseconds $DelayMs }
        $Map = Get-ProcSnapshot
    }
    if ($null -eq $Map) { return @{} }
    $Map
}

# Every unmet held-mode rule in a fixed order; empty when a held child may run. The one predicate behind the wrapper's
# pre-check and Enter-RunLocks -HeldCheckout.
function Get-HeldCheckoutDenials {
    param([string] $Repo, [string] $RecordDir = '', [switch] $RecompilePass, [hashtable] $Map)
    Set-StrictMode -Version Latest
    $rec = Read-SlotRecord -RecordDir (Get-RuntimeSlotRecordDir -RecordDir $RecordDir) -Name (Get-CheckoutRecordName -Repo $Repo)
    if ($null -eq $rec -or $rec -is [string]) { return 'no-checkout-record' }
    $out = [System.Collections.Generic.List[string]]::new()
    if (-not (Test-SlotProcessLive $rec.holderPid $rec.procStart)) { $out.Add('holder-dead') }
    elseif ([int] $rec.holderPid -ne $PID -and -not ($Map = Get-CallerSnapshot -Map $Map).ContainsKey([int]$PID)) { $out.Add('snapshot-failed') }
    elseif (-not (Test-HeldByCaller -Record $rec -Map $Map)) { $out.Add('holder-not-ancestor') }
    if (-not ($rec.PSObject.Properties['built'] -and $rec.built -eq $true)) { $out.Add('not-built') }
    if (-not $RecompilePass -and -not ($rec.PSObject.Properties['recompiled'] -and $rec.recompiled -eq $true)) { $out.Add('not-recompiled') }
    $out.ToArray()
}

# One line for a refused held child: the holder, the caller's chain and every unmet rule.
function Format-HeldCheckoutDenial {
    param([string] $Repo, [string] $RecordDir = '', [string[]] $Denials, [hashtable] $Map)
    Set-StrictMode -Version Latest
    if (-not $Map) { $Map = Get-ProcSnapshot }
    $rec = Read-SlotRecord -RecordDir (Get-RuntimeSlotRecordDir -RecordDir $RecordDir) -Name (Get-CheckoutRecordName -Repo $Repo)
    $holder = if ($null -ne $rec -and $rec -isnot [string]) { [string] $rec.holderPid } else { 'none' }
    "held checkout refused: checkout=$Repo holder=$holder chain=$((Get-CallerChain -Map $Map) -join '<') unmet=$($Denials -join ',')"
}

# The gate's one entry to a held checkout: the checkout lock through Enter-RunLocks (its wait, abandoned flag and
# fenced cleanup), then a fresh record with built and recompiled false over any leftover, then one sweep.
function Enter-HeldCheckout {
    param([string] $Repo, [string] $Label = 'gate', [int] $TimeoutMs = 600000, [string] $CheckoutLockName = '', [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    $lease = Enter-RunLocks -Repo $Repo -Label $Label -TimeoutMs $TimeoutMs -CheckoutLockName $CheckoutLockName -RecordDir $RecordDir
    if (-not $lease.Acquired) { return $lease }
    try {
        Write-SlotRecord -RecordDir $lease.RecordDir -Name (Get-CheckoutRecordName -Repo $Repo) -Required `
            -Record (New-SlotRecord -Lease $lease -Kind checkout -AcquiredAt $lease.AcquiredAt)
    } catch { Exit-RunLocks -Lease $lease; throw }
    $lease.Reaped = [int[]] @(Invoke-RuntimeSlotSweep -RecordDir $lease.RecordDir)
    $lease
}

function Set-CheckoutStamp {
    param([string] $Repo, [Parameter(Mandatory)] [ValidateSet('built', 'recompiled')] [string] $Name, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    $dir = Get-RuntimeSlotRecordDir -RecordDir $RecordDir
    $recName = Get-CheckoutRecordName -Repo $Repo
    $rec = Read-SlotRecord -RecordDir $dir -Name $recName
    if (-not (Test-HeldByCaller -Record $rec)) { throw "Set-CheckoutStamp $Name refused: the caller (pid $PID) does not hold checkout $Repo" }
    $out = [ordered]@{}
    foreach ($p in $rec.PSObject.Properties) { $out[$p.Name] = $p.Value }
    $out[$Name] = $true
    Write-SlotRecord -RecordDir $dir -Name $recName -Record $out -Required
}

# Sweeps once, removes the record and releases the checkout lock. A caller whose record is gone or not its own leaves
# the record alone and throws, but still releases the locks its lease holds: only the acquiring thread can.
function Exit-HeldCheckout {
    param($Lease)
    Set-StrictMode -Version Latest
    if ($null -eq $Lease) { return }
    $dir = Get-RuntimeSlotRecordDir -RecordDir $Lease.RecordDir
    $recName = Get-CheckoutRecordName -Repo $Lease.Repo
    $rec = Read-SlotRecord -RecordDir $dir -Name $recName
    if (-not (Test-HeldByCaller -Record $rec)) {
        Exit-RunLocks -Lease $Lease
        throw "Exit-HeldCheckout refused: the caller (pid $PID) does not hold checkout $($Lease.Repo)"
    }
    try { [void] @(Invoke-RuntimeSlotSweep -RecordDir $dir) } catch { }
    try { Remove-Item -LiteralPath (Join-Path $dir "$recName.json") -Force -ErrorAction SilentlyContinue } catch { }
    Exit-RunLocks -Lease $Lease
}

# ------------------------------------------------------------------ sweep
# Kills the orphaned test tree of every slot record whose holder died without releasing. Never
# writes a record: a sweep racing a fresh holder must not overwrite that holder's record.
function Invoke-RuntimeSlotSweep {
    param([string] $RecordDir = '')
    Set-StrictMode -Version Latest
    $dir = Get-RuntimeSlotRecordDir -RecordDir $RecordDir
    $killed = [System.Collections.Generic.List[int]]::new()
    foreach ($slot in 0..($script:RuntimeSlotMax - 1)) {
        try {
            $rec = Read-SlotRecord -RecordDir $dir -Name "slot-$slot"
            if ($null -eq $rec -or $rec -is [string]) { continue }
            if ($null -ne $rec.releasedAt) { continue }
            if (Test-SlotProcessLive $rec.holderPid $rec.procStart) { continue }
            if ($null -eq $rec.runnerPid) { continue }
            if (-not (Test-SlotProcessLive $rec.runnerPid $rec.runnerStart)) { continue }
            & taskkill.exe '/F' '/T' '/PID' ([int]$rec.runnerPid) 2>$null | Out-Null
            $killed.Add([int]$rec.runnerPid)
        } catch { }
    }
    $killed.ToArray()
}

# ------------------------------------------------------------------ waits
# One wait for every lock: WaitAny over the given handles. Returns the signaled index (-1 on
# timeout) and whether the wait completed through an abandoned mutex, which still grants it.
function Wait-RunLock {
    param([System.Threading.Mutex[]] $Handles, [int] $TimeoutMs)
    Set-StrictMode -Version Latest
    try {
        $i = [System.Threading.WaitHandle]::WaitAny([System.Threading.WaitHandle[]]$Handles, [math]::Max(0, $TimeoutMs))
        if ($i -eq [System.Threading.WaitHandle]::WaitTimeout) { return [pscustomobject]@{ Index = -1; Abandoned = $false } }
        return [pscustomobject]@{ Index = $i; Abandoned = $false }
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [System.Threading.AbandonedMutexException])) { $ex = $ex.InnerException }
        if (-not $ex) { throw }
        return [pscustomobject]@{ Index = $ex.MutexIndex; Abandoned = $true }
    }
}

# True when the given slot is busy with a run that counts: held by someone, unless its record
# is a live holder parked in state=waiting (a C=1 run that has started no Godot).
function Test-OtherSlotBusy {
    param($Lease, [int] $Slot)
    Set-StrictMode -Version Latest
    $m = $null
    try {
        $m = [System.Threading.Mutex]::new($false, $Lease.SlotNames[$Slot])
        $r = Wait-RunLock -Handles @($m) -TimeoutMs 0
        if ($r.Index -ge 0) { $m.ReleaseMutex(); return $false }
    } catch { return $true } finally { if ($m) { $m.Dispose() } }
    $rec = Read-SlotRecord -RecordDir $Lease.RecordDir -Name "slot-$Slot"
    if ($null -ne $rec -and $rec -isnot [string] -and $rec.state -eq 'waiting' -and
        (Test-SlotProcessLive $rec.holderPid $rec.procStart)) { return $false }
    $true
}

function Enter-RunLocks {
    param(
        [string] $Repo,
        [string] $Label = '',
        [switch] $Runtime,
        [ValidateRange(1, [int]::MaxValue)] [int] $SlotCount = 1,
        [int] $TimeoutMs = 600000,
        [string[]] $SlotNames = @(),
        [string] $CheckoutLockName = '',
        [string] $RecordDir = '',
        # A gate's held child: no checkout lock (its ancestor holds it), a runtime slot only, and only with no denial.
        [switch] $HeldCheckout,
        [switch] $RecompilePass
    )
    Set-StrictMode -Version Latest
    if ($RecompilePass -and -not $HeldCheckout) { throw 'Enter-RunLocks: -RecompilePass is valid only with -HeldCheckout' }
    if ($HeldCheckout) {
        $map = Get-ProcSnapshot
        $denials = @(Get-HeldCheckoutDenials -Repo $Repo -RecordDir $RecordDir -RecompilePass:$RecompilePass -Map $map)
        if ($denials.Count -gt 0) { throw (Format-HeldCheckoutDenial -Repo $Repo -RecordDir $RecordDir -Denials $denials -Map $map) }
    }
    $names = @(if ($SlotNames.Count -gt 0) { $SlotNames } else { Get-RuntimeSlotNames })
    if ($SlotCount -gt $names.Count) { throw "SlotCount $SlotCount exceeds the $($names.Count) runtime slot names" }
    $held = [System.Collections.Generic.List[System.Threading.Mutex]]::new()
    $opened = [System.Collections.Generic.List[System.Threading.Mutex]]::new()
    $lease = [pscustomobject]@{
        Acquired     = $false
        Blocker      = $null
        WaitMs       = [int64] 0
        SweepMs      = [int64] 0
        Slots        = [int[]] @()
        AcquiredAt   = $null
        Abandoned    = $false
        PeerSlotHeld = $false
        PeerSlots    = [int[]] @()
        PeerRecords  = @{}
        HeldCheckout = [bool] $HeldCheckout
        Reaped       = [int[]] @()
        HolderRecord = $false
        Held         = @()
        Repo         = $Repo
        Label        = $Label
        Runtime      = [bool] $Runtime
        SlotNames    = @($names)
        RecordDir    = (Get-RuntimeSlotRecordDir -RecordDir $RecordDir)
        ProcStart    = (Get-ProcStartUtc (Get-Process -Id $PID))
        Records      = @{}
        Opened       = $opened
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $reaped = [System.Collections.Generic.List[int]]::new()
    $sweep = {
        $t = [System.Diagnostics.Stopwatch]::StartNew()
        foreach ($k in @(Invoke-RuntimeSlotSweep -RecordDir $lease.RecordDir)) { $reaped.Add($k) }
        $lease.SweepMs += $t.ElapsedMilliseconds
    }
    $fail = {
        param([string] $Blocker)
        foreach ($k in @($lease.Records.Keys)) {
            $lease.Records[$k].releasedAt = (Get-Date).ToUniversalTime().ToString('o')
            Write-SlotRecord -RecordDir $lease.RecordDir -Name "slot-$k" -Record $lease.Records[$k]
        }
        if ($lease.HolderRecord) { Remove-CheckoutHolder -Repo $Repo -RecordDir $lease.RecordDir; $lease.HolderRecord = $false }
        for ($i = $held.Count - 1; $i -ge 0; $i--) { try { $held[$i].ReleaseMutex() } catch { } }
        foreach ($m in $opened) { try { $m.Dispose() } catch { } }
        $held.Clear(); $opened.Clear()
        $lease.Records = @{}
        $lease.Blocker = $Blocker
        $lease.Held = @()
        $lease.Slots = [int[]] @()
        $lease.WaitMs = $sw.ElapsedMilliseconds
    }
    try {
        # Checkout lock first, always, unless this is a held child: its ancestor holds that lock.
        if (-not $HeldCheckout) {
            $ckName = if ($CheckoutLockName) { $CheckoutLockName } else { Get-CheckoutLockName -Repo $Repo }
            $ck = [System.Threading.Mutex]::new($false, $ckName); $opened.Add($ck)
            $r = Wait-RunLock -Handles @($ck) -TimeoutMs ($TimeoutMs - $sw.ElapsedMilliseconds)
            if ($r.Index -lt 0) { & $fail 'checkout'; return $lease }
            $held.Add($ck)
            if ($r.Abandoned) { $lease.Abandoned = $true }
            $lease.HolderRecord = Write-CheckoutHolder -Repo $Repo -Label $Label -RecordDir $lease.RecordDir
        }

        if ($Runtime) {
            $slotHandles = @(foreach ($nm in $names) { $m = [System.Threading.Mutex]::new($false, $nm); $opened.Add($m); $m })
            if ($SlotCount -ge 2) {
                $order = @(($SlotCount - 1)..0)
                $r = Wait-RunLock -Handles @($order | ForEach-Object { $slotHandles[$_] }) -TimeoutMs ($TimeoutMs - $sw.ElapsedMilliseconds)
                if ($r.Index -lt 0) { & $fail 'runtime-slots'; return $lease }
                $slot = $order[$r.Index]
                $held.Add($slotHandles[$slot])
                if ($r.Abandoned) { $lease.Abandoned = $true }
                $lease.Slots = [int[]] @($slot)
                $lease.PeerSlots = [int[]] @(foreach ($other in 0..($names.Count - 1)) {
                    if ($other -ne $slot -and (Test-OtherSlotBusy -Lease $lease -Slot $other)) { $other }
                })
                $lease.PeerSlotHeld = $lease.PeerSlots.Count -gt 0
                foreach ($other in $lease.PeerSlots) {
                    $pr = Read-SlotRecord -RecordDir $lease.RecordDir -Name "slot-$other"
                    $lease.PeerRecords[$other] = if ($null -ne $pr -and $pr -isnot [string] -and $null -eq $pr.releasedAt) { $pr } else { 'unknown' }
                }
                & $sweep
                $now = (Get-Date).ToUniversalTime().ToString('o')
                $lease.Records[$slot] = New-SlotRecord -Lease $lease -State 'running' -AcquiredAt $now
                Write-SlotRecord -RecordDir $lease.RecordDir -Name "slot-$slot" -Record $lease.Records[$slot]
                $lease.AcquiredAt = $now
            } else {
                foreach ($slot in 0..($names.Count - 1)) {
                    $r = Wait-RunLock -Handles @($slotHandles[$slot]) -TimeoutMs ($TimeoutMs - $sw.ElapsedMilliseconds)
                    if ($r.Index -lt 0) { & $fail 'runtime-slots'; return $lease }
                    $held.Add($slotHandles[$slot])
                    if ($r.Abandoned) { $lease.Abandoned = $true }
                    & $sweep
                    $lease.Records[$slot] = New-SlotRecord -Lease $lease -State 'waiting' -AcquiredAt $null
                    Write-SlotRecord -RecordDir $lease.RecordDir -Name "slot-$slot" -Record $lease.Records[$slot]
                }
                $now = (Get-Date).ToUniversalTime().ToString('o')
                foreach ($slot in 0..($names.Count - 1)) {
                    $lease.Records[$slot] = New-SlotRecord -Lease $lease -State 'running' -AcquiredAt $now
                    Write-SlotRecord -RecordDir $lease.RecordDir -Name "slot-$slot" -Record $lease.Records[$slot]
                }
                $lease.Slots = [int[]] @(0..($names.Count - 1))
                $lease.AcquiredAt = $now
            }
        } else {
            $lease.AcquiredAt = (Get-Date).ToUniversalTime().ToString('o')
        }
        # The holder can leave or die during the slot wait; a held child admitted on the pre-wait check would run
        # --no-build on a tree nothing guards.
        if ($HeldCheckout) {
            $map = Get-ProcSnapshot
            $denials = @(Get-HeldCheckoutDenials -Repo $Repo -RecordDir $RecordDir -RecompilePass:$RecompilePass -Map $map)
            if ($denials.Count -gt 0) {
                $msg = "$(Format-HeldCheckoutDenial -Repo $Repo -RecordDir $RecordDir -Denials $denials -Map $map) after a $($sw.ElapsedMilliseconds) ms slot wait"
                & $fail 'held'
                throw $msg
            }
        }
        $lease.Acquired = $true
        $lease.Held = @($held)
        $lease.Reaped = [int[]] @($reaped)
        $lease.WaitMs = $sw.ElapsedMilliseconds
        return $lease
    } catch {
        & $fail 'error'
        throw
    }
}

# Stamps releasedAt, then releases in reverse order and disposes. Never throws; accepts $null,
# an empty lease and a non-acquired lease. Must run on the acquiring thread.
function Exit-RunLocks {
    param($Lease)
    Set-StrictMode -Version Latest
    if ($null -eq $Lease) { return }
    try {
        if ($Lease.PSObject.Properties['Records'] -and $Lease.Records) {
            foreach ($k in @($Lease.Records.Keys)) {
                try {
                    $Lease.Records[$k].releasedAt = (Get-Date).ToUniversalTime().ToString('o')
                    Write-SlotRecord -RecordDir $Lease.RecordDir -Name "slot-$k" -Record $Lease.Records[$k]
                } catch { }
            }
            $Lease.Records = @{}
        }
        if ($Lease.PSObject.Properties['HolderRecord'] -and $Lease.HolderRecord) {
            Remove-CheckoutHolder -Repo $Lease.Repo -RecordDir $Lease.RecordDir
            $Lease.HolderRecord = $false
        }
        $held = @(if ($Lease.PSObject.Properties['Held']) { $Lease.Held })
        for ($i = $held.Count - 1; $i -ge 0; $i--) {
            try { $held[$i].ReleaseMutex() } catch { }
        }
        if ($Lease.PSObject.Properties['Opened'] -and $Lease.Opened) {
            foreach ($m in @($Lease.Opened)) { try { $m.Dispose() } catch { } }
        }
        $Lease.Held = @()
    } catch { }
}

function Set-RuntimeSlotRunner {
    param($Lease, [System.Diagnostics.Process] $Process)
    Set-StrictMode -Version Latest
    try {
        if ($null -eq $Lease -or -not $Lease.Acquired -or -not $Lease.Records -or $null -eq $Process) { return }
        $start = Get-ProcStartUtc $Process
        foreach ($k in @($Lease.Records.Keys)) {
            $Lease.Records[$k].runnerPid = [int] $Process.Id
            $Lease.Records[$k].runnerStart = $start
            Write-SlotRecord -RecordDir $Lease.RecordDir -Name "slot-$k" -Record $Lease.Records[$k]
        }
    } catch { }
}

# ------------------------------------------------------------------ probes (hold nothing)
function Test-RuntimeSlotAvailable {
    param([string] $Repo, [int] $SlotCount = 0, [string[]] $SlotNames = @(), [string] $OverridePath = '')
    Set-StrictMode -Version Latest
    $names = @(if ($SlotNames.Count -gt 0) { $SlotNames } else { Get-RuntimeSlotNames })
    $count = if ($SlotCount -ge 1) { $SlotCount } else { (Get-RuntimeSlotCount -Repo $Repo -OverridePath $OverridePath).Count }
    $count = [math]::Min($count, $names.Count)
    $taken = [System.Collections.Generic.List[System.Threading.Mutex]]::new()
    $opened = [System.Collections.Generic.List[System.Threading.Mutex]]::new()
    try {
        $order = if ($count -ge 2) { @(($count - 1)..0) } else { @(0..($names.Count - 1)) }
        foreach ($slot in $order) {
            $m = [System.Threading.Mutex]::new($false, $names[$slot]); $opened.Add($m)
            $r = Wait-RunLock -Handles @($m) -TimeoutMs 0
            if ($r.Index -ge 0) { $taken.Add($m); if ($count -ge 2) { return $true } }
            elseif ($count -eq 1) { return $false }
        }
        return ($count -eq 1)
    } finally {
        for ($i = $taken.Count - 1; $i -ge 0; $i--) { try { $taken[$i].ReleaseMutex() } catch { } }
        foreach ($m in $opened) { try { $m.Dispose() } catch { } }
    }
}

function Test-CheckoutLockFree {
    param([string] $Repo, [string] $CheckoutLockName = '')
    Set-StrictMode -Version Latest
    $name = if ($CheckoutLockName) { $CheckoutLockName } else { Get-CheckoutLockName -Repo $Repo }
    $m = [System.Threading.Mutex]::new($false, $name)
    $got = $false
    try {
        $r = Wait-RunLock -Handles @($m) -TimeoutMs 0
        $got = $r.Index -ge 0
        return $got
    } finally {
        if ($got) { try { $m.ReleaseMutex() } catch { } }
        $m.Dispose()
    }
}

# The other slots whose runs overlapped this lease: busy at acquisition or now, or a record acquired or released inside
# its window. -1 stands for an overlap no slot can be named for (an unreadable record, a probe error).
function Get-RuntimeOverlapSlots {
    param($Lease, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    try {
        if ($null -eq $Lease -or -not $Lease.Acquired -or -not $Lease.Runtime) { return }
        $slots = @($Lease.Slots)
        if ($slots.Count -ne 1) { return }
        $out = [System.Collections.Generic.List[int]]::new()
        $atStart = @(if ($Lease.PSObject.Properties['PeerSlots']) { $Lease.PeerSlots })
        foreach ($s in $atStart) { $out.Add([int] $s) }
        if ($Lease.PeerSlotHeld -and $atStart.Count -eq 0) { $out.Add(-1) }
        $dir = if ($RecordDir) { $RecordDir } else { $Lease.RecordDir }
        $mine = ConvertTo-SlotUtc $Lease.AcquiredAt
        foreach ($other in 0..(@($Lease.SlotNames).Count - 1)) {
            if ($other -eq [int]$slots[0] -or $out.Contains($other)) { continue }
            if (Test-OtherSlotBusy -Lease $Lease -Slot $other) { $out.Add($other); continue }
            $rec = Read-SlotRecord -RecordDir $dir -Name "slot-$other"
            if ($null -eq $rec) { continue }
            if ($rec -is [string]) { $out.Add($other); continue }
            if ($rec.state -ne 'running') { continue }
            if ([int]$rec.holderPid -eq $PID -and (ConvertTo-SlotUtc $rec.procStart) -eq (ConvertTo-SlotUtc $Lease.ProcStart)) { continue }
            $theirAcq = ConvertTo-SlotUtc $rec.acquiredAt
            $theirRel = ConvertTo-SlotUtc $rec.releasedAt
            if ($null -ne $theirAcq -and $null -ne $mine -and $theirAcq -gt $mine) { $out.Add($other); continue }
            if ($null -ne $theirRel) {
                if ($null -ne $mine -and $theirRel -gt $mine) { $out.Add($other) }
                continue
            }
            if ((Test-SlotProcessLive $rec.holderPid $rec.procStart) -or (Test-SlotProcessLive $rec.runnerPid $rec.runnerStart)) { $out.Add($other) }
        }
        $out.ToArray()
    } catch { -1 }
}

# Was any other runtime run live during this lease? A true result is a rollback CANDIDATE only.
function Test-RuntimeOverlap {
    param($Lease, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    @(Get-RuntimeOverlapSlots -Lease $Lease -RecordDir $RecordDir).Count -gt 0
}

# Who overlapped this lease. 'self': it is a held child and every overlapping slot belongs to its own gate's pool: the
# slot's holder is live and descends from the checkout holder, or has exited after taking its slot inside the holder's
# held window, when this checkout's lock admitted only held children. A slot busy at acquisition is judged by its
# holder then (the lease's PeerRecords) as well as its record now, so a foreign holder that passed the slot to a
# sibling stays foreign. 'foreign': anything else, a rollback candidate. 'none': no overlap.
function Get-RuntimeOverlapScope {
    param($Lease, [string] $RecordDir = '', [hashtable] $Map)
    Set-StrictMode -Version Latest
    $over = @(Get-RuntimeOverlapSlots -Lease $Lease -RecordDir $RecordDir)
    if ($over.Count -eq 0) { return 'none' }
    if (-not ($Lease.PSObject.Properties['HeldCheckout'] -and $Lease.HeldCheckout)) { return 'foreign' }
    try {
        $dir = if ($RecordDir) { $RecordDir } else { $Lease.RecordDir }
        $ck = Read-SlotRecord -RecordDir $dir -Name (Get-CheckoutRecordName -Repo $Lease.Repo)
        if ($null -eq $ck -or $ck -is [string]) { return 'foreign' }
        $holder = [int] $ck.holderPid
        $window = ConvertTo-SlotUtc $ck.acquiredAt
        if (-not $Map) { $Map = Get-ProcSnapshot }
        $atStart = if ($Lease.PSObject.Properties['PeerRecords'] -and $Lease.PeerRecords) { $Lease.PeerRecords } else { @{} }
        foreach ($s in $over) {
            if ($s -lt 0) { return 'foreign' }
            $recs = [System.Collections.Generic.List[object]]::new()
            $recs.Add((Read-SlotRecord -RecordDir $dir -Name "slot-$s"))
            if ($atStart.ContainsKey($s)) { $recs.Add($atStart[$s]) }
            foreach ($rec in $recs) {
                if ($null -eq $rec -or $rec -is [string] -or [string] $rec.checkout -ne [string] $Lease.Repo) { return 'foreign' }
                if (Test-SlotProcessLive $rec.holderPid $rec.procStart) {
                    $p = $Map[[int] $rec.holderPid]
                    if ($null -eq $p -or -not (Test-ChainReachesPid -Proc $p -Map $Map -RootPid $holder -MaxHops 12)) { return 'foreign' }
                } else {
                    $acq = ConvertTo-SlotUtc $rec.acquiredAt
                    if ($null -eq $window -or $null -eq $acq -or $acq -lt $window) { return 'foreign' }
                }
            }
        }
        'self'
    } catch { 'foreign' }
}

# The wrapper's post-run overlap step: the trigger is logged for a foreign overlap only; an own-pool overlap is a plain
# RED. Returns the scope.
function Register-OverlapSignature {
    param($Lease, [string] $Repo, [string] $Label, [string] $Line, [string] $RecordDir = '', [hashtable] $Map)
    Set-StrictMode -Version Latest
    $scope = Get-RuntimeOverlapScope -Lease $Lease -RecordDir $RecordDir -Map $Map
    if ($scope -eq 'foreign') { Add-RuntimeSlotTrigger -Repo $Repo -Label $Label -Line $Line -RecordDir $RecordDir }
    $scope
}

# ------------------------------------------------------------------ signatures and triggers
function Get-OverlapSignatureKind {
    param([string] $SkipKind = '', [int] $ExitCode = 0, [string] $GodotLog = '')
    Set-StrictMode -Version Latest
    if ($SkipKind -eq 'refcounted-free-fatal') { return $null }
    if ($SkipKind -and $SkipKind -in $script:OverlapSignatureKinds) { return $SkipKind }
    if ($ExitCode -eq -1073741795) { return 'testhost-native-crash' }
    try {
        if ($GodotLog -and (Test-Path -LiteralPath $GodotLog) -and
            (Select-String -LiteralPath $GodotLog -Pattern 'Unable to open|Cannot create' -Quiet -ErrorAction SilentlyContinue)) {
            return 'cache-file-error'
        }
    } catch { }
    $null
}

function Add-RuntimeSlotTrigger {
    param([string] $Repo, [string] $Label, [string] $Line, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    try {
        $dir = Get-RuntimeSlotRecordDir -RecordDir $RecordDir
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $gate = if ($env:HARNESS_GATE_RUN) { $env:HARNESS_GATE_RUN } else { '-' }
        $text = "{0} checkout={1} label={2} gateRun={3} {4}`n" -f (Get-Date).ToUniversalTime().ToString('o'), $Repo, $Label, $gate, $Line
        $path = Join-Path $dir 'triggers.log'
        $deadline = (Get-Date).AddMilliseconds(500)
        while ($true) {
            try { [System.IO.File]::AppendAllText($path, $text); return }
            catch [System.IO.IOException] {
                if ((Get-Date) -gt $deadline) { return }
                Start-Sleep -Milliseconds 10
            }
        }
    } catch { }
}

function Get-RuntimeSlotTriggers {
    param([string] $GateRun, [string] $RecordDir = '')
    Set-StrictMode -Version Latest
    if (-not $GateRun) { return }
    try {
        $path = Join-Path (Get-RuntimeSlotRecordDir -RecordDir $RecordDir) 'triggers.log'
        if (-not (Test-Path -LiteralPath $path)) { return }
        $pattern = ' gateRun=' + [regex]::Escape($GateRun) + ' '
        return @([System.IO.File]::ReadAllLines($path) | Where-Object { $_ -match $pattern })
    } catch { return }
}
