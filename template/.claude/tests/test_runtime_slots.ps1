#requires -Version 7
<#
.SYNOPSIS
  Proof for .claude/scripts/RuntimeSlots.ps1: the checkout lock, the N runtime slots, count
  resolution, slot records, the orphan sweep, the probes, overlap detection, the trigger log and
  the overlap-signature classifier.

.HOW
  Kernel objects are exercised with real processes: each holder is a separate pwsh child that
  dot-sources the module, calls Enter-RunLocks with an explicit -SlotCount and fixture names
  (Global\slots-test-<pid>-<guid>-{s0..s<max-1>,ck-<checkout>}, one name per module slot), writes its lease to a ready file,
  holds until a release file appears or this proof dies, calls Exit-RunLocks, writes a released
  file carrying any exception text, and stays alive until an exit file appears. Every release
  assertion probes while the holder is still alive, because process exit frees or abandons a
  mutex by itself. No case reads the machine's override file or the committed default.

.RUN
  pwsh -NoProfile -File .claude/tests/test_runtime_slots.ps1
  pwsh -NoProfile -File .claude/tests/test_runtime_slots.ps1 -Case T3,T8 -ModulePath <plant.ps1>
#>
[CmdletBinding()]
param(
    # A planted copy of the module, to show a case detects the defect the plant carries.
    [string] $ModulePath = '',
    # Comma- or space-separated case ids (T0..T37). Default: every case, run as parallel shards of this proof.
    [string] $Case = '',
    # Shards for the default run; each is a child of this script given its own -Case list.
    [int] $Shards = 4
)

# Start-Process -WindowStyle exists on Windows only; a Linux child has no window to hide.
$HiddenWindow = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$scriptsDir = Join-Path $repoRoot '.claude\scripts'
if (-not $ModulePath) { $ModulePath = Join-Path $scriptsDir 'RuntimeSlots.ps1' }
$ModulePath = [System.IO.Path]::GetFullPath($ModulePath)
$allCases = @(0..37 | ForEach-Object { "T$_" })
$selected = if ($Case) { @($Case -split '[,\s]+' | Where-Object { $_ }) } else { $allCases }
. (Join-Path $scriptsDir 'GodotProcess.ps1')
$Pwsh = Get-PwshStart

if (-not $Case -and $Shards -gt 1) {
    # Cases share nothing (each has its own temp root and PID-keyed mutex names), so shards run side by side.
    # Seconds a case takes alone (rounded); the rest weigh 1. Deals the heavy cases out first.
    $cost = @{ T8 = 23; T27 = 16; T7 = 13; T3 = 10; T16 = 10; T14 = 8; T2 = 8; T5 = 8; T4 = 7; T32 = 6; T29 = 5; T15 = 5; T10 = 4; T34 = 4; T22 = 4; T21 = 3; T20 = 3 }
    $load = @(0) * $Shards
    $lists = @(for ($k = 0; $k -lt $Shards; $k++) { , [System.Collections.Generic.List[string]]::new() })
    foreach ($id in ($allCases | Sort-Object { -[int]($cost[$_] ?? 1) }, { [int]$_.Substring(1) })) {
        $i = 0; for ($k = 1; $k -lt $Shards; $k++) { if ($load[$k] -lt $load[$i]) { $i = $k } }
        $lists[$i].Add($id); $load[$i] += [int]($cost[$id] ?? 1)
    }
    $outDir = Join-Path ([System.IO.Path]::GetTempPath()) "slots-shards-$PID-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $procs = @(0..($Shards - 1) | ForEach-Object {
        $ids = @($lists[$_] | Sort-Object { [int]$_.Substring(1) }) -join ','
        Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-File', $PSCommandPath, '-ModulePath', $ModulePath, '-Case', $ids)) -PassThru @HiddenWindow `
            -RedirectStandardOutput (Join-Path $outDir "out$_.txt") -RedirectStandardError (Join-Path $outDir "err$_.txt")
    })
    $bad = 0
    for ($i = 0; $i -lt $Shards; $i++) {
        $procs[$i].WaitForExit()
        $lines = @(Get-Content (Join-Path $outDir "out$i.txt"))
        $lines | Where-Object { $_ -ne 'ALL PASS' -and $_ -notmatch '^FAILED \(\d+\)$' } | Write-Host
        Get-Content (Join-Path $outDir "err$i.txt") -ErrorAction SilentlyContinue | Write-Host
        if ('ALL PASS' -notin $lines) { $bad++ }
    }
    Remove-Item -Path $outDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ''
    if ($bad) { Write-Host "FAILED ($bad shard(s))"; exit 1 }
    Write-Host 'ALL PASS'
    exit 0
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) "slots-test-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $work | Out-Null
# Linux StartTime differs between two readers of one process, so every recorded identity goes through the product's stamp.
function Get-ProofStamp { param([int] $ProcessId) (Get-ProcessStartStamp -ProcessId $ProcessId).ToString('o') }
$ownerStart = Get-ProofStamp $PID
$script:Fails = 0
$script:Children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$script:Runners = [System.Collections.Generic.List[int]]::new()
$script:OpenHandles = [System.Collections.Generic.List[object]]::new()

function Assert-True {
    param([string] $Name, $Condition, [string] $Detail = '')
    if ($Condition) { Write-Host "  PASS  $Name" }
    else {
        Write-Host "  FAIL  $Name$(if ($Detail) { "`n        $Detail" })"
        $script:Fails++
    }
}

function Get-ModuleMax {
    if (Get-Command Get-RuntimeSlotNames -ErrorAction SilentlyContinue) { return @(Get-RuntimeSlotNames).Count }
    2
}

function New-Names {
    $g = [guid]::NewGuid().ToString('N').Substring(0, 12)
    $rec = Join-Path $work "rec-$g"
    New-Item -ItemType Directory -Force -Path $rec | Out-Null
    $names = @(0..((Get-ModuleMax) - 1) | ForEach-Object { "Global\slots-test-$PID-$g-s$_" })
    [pscustomobject]@{
        names    = $names
        s0       = $names[0]
        s1       = $names[1]
        ckPrefix = "Global\slots-test-$PID-$g-ck-"
        rec      = $rec
    }
}

function Get-AbandonedException {
    param($Exception)
    $ex = $Exception
    while ($ex -and -not ($ex -is [System.Threading.AbandonedMutexException])) { $ex = $ex.InnerException }
    $ex
}

# True when the named mutex can be taken right now. Takes and releases at once; an abandoned
# mutex counts as free, because the wait that reports abandonment also grants ownership.
function Test-MutexFree {
    param([string] $Name)
    $m = New-RunLock $Name
    try {
        $got = $false
        try { $got = $m.WaitOne(0) }
        catch { if (Get-AbandonedException $_.Exception) { $got = $true } else { throw } }
        if ($got) { $m.ReleaseMutex() }
        return $got
    } finally { $m.Dispose() }
}

function Open-Handle {
    param([string] $Name)
    $m = New-RunLock $Name
    $script:OpenHandles.Add($m)
    $m
}

$holderPath = Join-Path $work 'holder.ps1'
$holderBody = @'
[CmdletBinding()]
param(
    [string] $ModulePath, [string] $Dir, [string] $Mode = 'lease',
    [string] $Repo = '', [string] $Label = 'holder', [int] $SlotCount = 2, [int] $TimeoutMs = 3000,
    [string] $Names = '', [string] $Ck = '', [string] $RecordDir = '',
    [string] $Runtime = '1', [switch] $Runner, [string] $RawName = '', [string] $Tag = '',
    [int] $OwnerPid, [string] $OwnerStart,
    [switch] $Held, [switch] $RecompilePass, [string] $Stamps = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Test-OwnerLive {
    Test-SlotProcessLive $OwnerPid $OwnerStart
}
function Write-JsonFile {
    param([string] $Path, $Object)
    $tmp = "$Path.tmp"
    [System.IO.File]::WriteAllText($tmp, ($Object | ConvertTo-Json -Depth 5 -Compress))
    [System.IO.File]::Move($tmp, $Path, $true)
}
function Wait-File {
    param([string] $Path)
    while (-not (Test-Path $Path) -and (Test-OwnerLive)) { Start-Sleep -Milliseconds 50 }
}
$ready = Join-Path $Dir 'ready.json'
$released = Join-Path $Dir 'released.json'

if ($Mode -eq 'raw') {
    . $ModulePath
    $m = New-RunLock $RawName
    $got = $false
    try { $got = $m.WaitOne(5000) } catch [System.Threading.AbandonedMutexException] { $got = $true }
    Write-JsonFile $ready ([ordered]@{ Acquired = $got; Pid = $PID })
    Wait-File (Join-Path $Dir 'release')
    $err = ''
    try { if ($got) { $m.ReleaseMutex() } } catch { $err = "$_" }
    Write-JsonFile $released ([ordered]@{ Error = $err })
    Wait-File (Join-Path $Dir 'exit')
    exit 0
}

. $ModulePath

if ($Mode -eq 'append') {
    $err = ''
    Wait-File (Join-Path $Dir 'go')
    try {
        for ($i = 0; $i -lt 50; $i++) {
            Add-RuntimeSlotTrigger -Repo $Repo -Label $Label -Line "OVERLAP_SIGNATURE=run-aborted tag=$Tag n=$i" -RecordDir $RecordDir
        }
    } catch { $err = "$_" }
    Write-JsonFile $released ([ordered]@{ Error = $err })
    exit 0
}

if ($Mode -eq 'heldowner') {
    $hl = $null
    try {
        $hl = Enter-HeldCheckout -Repo $Repo -Label $Label -TimeoutMs $TimeoutMs -CheckoutLockName $Ck -RecordDir $RecordDir
        foreach ($s in @($Stamps -split ',' | Where-Object { $_ })) { Set-CheckoutStamp -Repo $Repo -Name $s -RecordDir $RecordDir }
    } catch {
        Write-JsonFile $ready ([ordered]@{ Acquired = $false; Error = "$_"; Pid = $PID })
        Wait-File (Join-Path $Dir 'exit')
        exit 3
    }
    Write-JsonFile $ready ([ordered]@{ Acquired = [bool] $hl.Acquired; Error = ''; Pid = $PID })
    Wait-File (Join-Path $Dir 'release')
    $err = ''
    try { Exit-HeldCheckout -Lease $hl } catch { $err = "$_" }
    Write-JsonFile $released ([ordered]@{ Error = $err })
    Wait-File (Join-Path $Dir 'exit')
    exit 0
}

# Process start and the Linux lock-type compile take seconds on a loaded host; a case that must know this
# holder is inside its lock wait reads 'entering', written once only the wait itself remains.
if ((Get-HostPlatform) -eq 'linux') { Import-HarnessFileLock }
Set-Content -Path (Join-Path $Dir 'entering') -Value 'x' -NoNewline
$lease = $null
try {
    $heldArgs = @{}
    if ($Held) { $heldArgs['HeldCheckout'] = $true }
    if ($RecompilePass) { $heldArgs['RecompilePass'] = $true }
    $lease = Enter-RunLocks -Repo $Repo -Label $Label -Runtime:($Runtime -eq '1') -SlotCount $SlotCount `
        -TimeoutMs $TimeoutMs -SlotNames @($Names -split ',') -CheckoutLockName $Ck -RecordDir $RecordDir @heldArgs
} catch {
    Write-JsonFile $ready ([ordered]@{ Acquired = $false; Error = "$_"; Pid = $PID })
    Wait-File (Join-Path $Dir 'exit')
    exit 3
}
$runnerPid = 0
if ($Runner -and $lease.Acquired) {
    $hw = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }  # this body runs in its own pwsh child, which has no $HiddenWindow
    $pw = Get-PwshStart
    $r = Start-Process $pw.FilePath -ArgumentList (@($pw.Lead) + @('-NoProfile', '-Command', 'Start-Sleep -Seconds 600')) -PassThru @hw
    Set-RuntimeSlotRunner -Lease $lease -Process $r
    $runnerPid = $r.Id
}
Write-JsonFile $ready ([ordered]@{
    Acquired     = [bool] $lease.Acquired
    Blocker      = $lease.Blocker
    WaitMs       = [int64] $lease.WaitMs
    Slots        = @($lease.Slots)
    AcquiredAt   = $lease.AcquiredAt
    Abandoned    = [bool] $lease.Abandoned
    PeerSlotHeld = [bool] $lease.PeerSlotHeld
    Reaped       = @($lease.Reaped)
    HeldCount    = @($lease.Held).Count
    RunnerPid    = $runnerPid
    Pid          = $PID
    Error        = ''
})
Wait-File (Join-Path $Dir 'release')
$err = ''
try { Exit-RunLocks -Lease $lease } catch { $err = "$_" }
Write-JsonFile $released ([ordered]@{ Error = $err })
Wait-File (Join-Path $Dir 'exit')
exit 0
'@
[System.IO.File]::WriteAllText($holderPath, $holderBody, [System.Text.UTF8Encoding]::new($false))

function Start-Holder {
    param($N, [string] $Checkout = 'X', [int] $SlotCount = 2, [int] $TimeoutMs = 3000,
          [switch] $Runner, [switch] $Logic, [string] $Mode = 'lease', [string] $RawName = '',
          [string] $Tag = '', [switch] $Held, [switch] $RecompilePass, [string] $Stamps = '')
    $dir = Join-Path $work ("h-" + [guid]::NewGuid().ToString('N').Substring(0, 12))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $a = @('-NoProfile', '-File', $holderPath, '-ModulePath', $ModulePath, '-Dir', $dir, '-Mode', $Mode,
           '-OwnerPid', "$PID", '-OwnerStart', $ownerStart)
    if ($Mode -eq 'raw') { $a += @('-RawName', $RawName) }
    else {
        $a += @('-Repo', "C:\slots-fixture\$Checkout", '-Label', $Checkout, '-SlotCount', "$SlotCount",
                '-TimeoutMs', "$TimeoutMs", '-Names', ($N.names -join ','), '-Ck', ($N.ckPrefix + $Checkout),
                '-RecordDir', $N.rec, '-Runtime', $(if ($Logic) { '0' } else { '1' }))
        if ($Runner) { $a += '-Runner' }
        if ($Tag) { $a += @('-Tag', $Tag) }
        if ($Held) { $a += '-Held' }
        if ($RecompilePass) { $a += '-RecompilePass' }
        if ($Stamps) { $a += @('-Stamps', $Stamps) }
    }
    $p = Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + $a) -PassThru @HiddenWindow `
        -RedirectStandardOutput (Join-Path $dir 'out.txt') -RedirectStandardError (Join-Path $dir 'err.txt')
    $script:Children.Add($p)
    [pscustomobject]@{ Proc = $p; Dir = $dir; Name = $Checkout; Lease = $null; LaunchedAt = Get-Date }
}

function Read-SlotRecordFile {
    param([string] $RecordDir, [int] $Slot)
    $p = Join-Path $RecordDir "slot-$Slot.json"
    if (Test-Path -LiteralPath $p) { Read-JsonFile $p } else { $null }
}

function Read-JsonFile {
    param([string] $Path)
    for ($i = 0; $i -lt 20; $i++) {
        try { return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) } catch { Start-Sleep -Milliseconds 25 }
    }
    $null
}

function Wait-Entering {
    param($H, [int] $Seconds = 60)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while (-not (Test-Path (Join-Path $H.Dir 'entering'))) {
        if ($H.Proc.HasExited -or (Get-Date) -gt $deadline) {
            throw "holder $($H.Name) never reached its lock wait in ${Seconds}s: $(Get-Content (Join-Path $H.Dir 'err.txt') -Raw -ErrorAction SilentlyContinue)"
        }
        Start-Sleep -Milliseconds 25
    }
}

# The deadline only catches a hung holder; every timing claim reads the lease's own WaitMs.
function Wait-Ready {
    param($H, [int] $Seconds = 60)
    $p = Join-Path $H.Dir 'ready.json'
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $p) {
            $H.Lease = Read-JsonFile $p
            if ($H.Lease -and $H.Lease.PSObject.Properties['Pid']) {
                Assert-True "holder $($H.Name) is the process this proof launched (a wrapper would take every Kill)" ([int]$H.Lease.Pid -eq $H.Proc.Id) "Proc.Id=$($H.Proc.Id) holder `$PID=$($H.Lease.Pid) parent=$((Get-Content "/proc/$($H.Lease.Pid)/stat" -ErrorAction SilentlyContinue) -replace '^\d+ \(.*?\) \S (\d+).*','$1')"
            }
            if ($H.Lease -and $H.Lease.PSObject.Properties['RunnerPid'] -and [int]$H.Lease.RunnerPid -gt 0) {
                $script:Runners.Add([int]$H.Lease.RunnerPid)
            }
            return $H.Lease
        }
        if ($H.Proc.HasExited) { break }
        Start-Sleep -Milliseconds 25
    }
    $err = Get-Content (Join-Path $H.Dir 'err.txt') -Raw -ErrorAction SilentlyContinue
    throw "holder $($H.Name) not ready in ${Seconds}s: $err"
}

function Invoke-HolderRelease {
    param($H, [int] $Seconds = 10)
    Set-Content -Path (Join-Path $H.Dir 'release') -Value 'x' -NoNewline
    $p = Join-Path $H.Dir 'released.json'
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $p) { return (Read-JsonFile $p) }
        if ($H.Proc.HasExited) { break }
        Start-Sleep -Milliseconds 25
    }
    throw "holder $($H.Name) wrote no released file"
}

function Stop-Holder {
    param($H)
    if (-not $H) { return }
    try { Set-Content -Path (Join-Path $H.Dir 'exit') -Value 'x' -NoNewline } catch { }
    try { if (-not $H.Proc.WaitForExit(5000)) { $H.Proc.Kill($true) } } catch { }
}

function Stop-All {
    foreach ($p in $script:Children) { try { if (-not $p.HasExited) { $p.Kill($true); [void]$p.WaitForExit(5000) } } catch { } }
    $script:Children.Clear()
    foreach ($rp in $script:Runners) {
        $r = Get-Process -Id $rp -ErrorAction SilentlyContinue
        if ($r -and $r.ProcessName -in @('pwsh', 'dotnet')) { try { $r.Kill($true) } catch { } }
    }
    $script:Runners.Clear()
    foreach ($m in $script:OpenHandles) { try { $m.Dispose() } catch { } }
    $script:OpenHandles.Clear()
}

function Test-ProcessGone {
    param([int] $ProcessId, [int] $Ms = 3000)
    $deadline = (Get-Date).AddMilliseconds($Ms)
    while ((Get-Date) -lt $deadline) {
        # A killed process whose parent never reaps it (a container's PID 1) keeps its /proc entry as a zombie, which has no stamp.
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) -or $null -eq (Get-ProcessStartStamp -ProcessId $ProcessId)) { return $true }
        Start-Sleep -Milliseconds 50
    }
    $false
}

function Invoke-Case {
    param([string] $Id, [scriptblock] $Body)
    if ($Id -notin $selected) { return }
    Write-Host "`n$Id"
    try { & $Body }
    catch {
        Write-Host "  FAIL  $Id threw: $($_.Exception.Message)"
        $script:Fails++
    }
    finally { Stop-All }
}

$needsModule = @($selected | Where-Object { $_ -ne 'T0' -and $_ -ne 'T9' }).Count -gt 0
if ($needsModule) {
    if (-not (Test-Path $ModulePath)) { throw "module not found: $ModulePath" }
    . $ModulePath
}

try {
    Write-Host "Module: $ModulePath"

    # ------------------------------------------------------------------ T0 (.NET contract)
    Invoke-Case 'T0' {
        if (-not $IsWindows) { Write-Host '  SKIP  T0 pins the Windows kernel-mutex WaitAny contract'; return }
        $n = New-Names
        $s0 = Open-Handle $n.s0
        $s1 = Open-Handle $n.s1
        $a = Start-Holder -N $n -Mode raw -RawName $n.s0
        $b = Start-Holder -N $n -Mode raw -RawName $n.s1
        [void](Wait-Ready $a); [void](Wait-Ready $b)
        $a.Proc.Kill()
        [void]$a.Proc.WaitForExit(5000)
        $rel = Invoke-HolderRelease $b
        Assert-True 'T0 raw holder B released s1 cleanly' ($rel.Error -eq '') $rel.Error
        $index = $null; $abandoned = $null
        try { $index = [System.Threading.WaitHandle]::WaitAny([System.Threading.WaitHandle[]]@($s0, $s1), 5000) }
        catch { $abandoned = Get-AbandonedException $_.Exception; if (-not $abandoned) { throw } }
        Assert-True 'T0 WaitAny throws AbandonedMutexException for the killed holder' ($null -ne $abandoned) "index=$index"
        if ($abandoned) {
            Assert-True 'T0 MutexIndex is 0 (smallest signaled index)' ($abandoned.MutexIndex -eq 0) "MutexIndex=$($abandoned.MutexIndex)"
            $relOk = $true
            try { $abandoned.Mutex.ReleaseMutex() } catch { $relOk = $false }
            Assert-True 'T0 the waiter owns the abandoned mutex (ReleaseMutex succeeds)' $relOk
        }
        Stop-Holder $b
    }

    # ------------------------------------------------------------------ T1
    Invoke-Case 'T1' {
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X -SlotCount 2
        $lx = Wait-Ready $x
        Assert-True 'T1 X acquired' ($lx.Acquired -eq $true) ($lx | ConvertTo-Json -Compress)
        Assert-True 'T1 X reports slot 1 (the preferred slot)' ((@($lx.Slots) -join ',') -eq '1') "slots=$(@($lx.Slots) -join ',')"
        Assert-True 'T1 X holds s1 and leaves s0 free' ((-not (Test-MutexFree $n.s1)) -and (Test-MutexFree $n.s0))
        $y = Start-Holder -N $n -Checkout Y -SlotCount 2
        $ly = Wait-Ready $y
        Assert-True 'T1 Y acquired within 3s' ($ly.Acquired -eq $true -and [int64]$ly.WaitMs -le 3000) ($ly | ConvertTo-Json -Compress)
        Assert-True 'T1 Y reports slot 0' ((@($ly.Slots) -join ',') -eq '0') "slots=$(@($ly.Slots) -join ',')"
        Assert-True 'T1 X acquired within 3s' ([int64]$lx.WaitMs -le 3000) "waitMs=$($lx.WaitMs)"
    }

    # ------------------------------------------------------------------ T2
    Invoke-Case 'T2' {
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)
        $y = Start-Holder -N $n -Checkout Y; [void](Wait-Ready $y)
        $z = Start-Holder -N $n -Checkout Z -TimeoutMs 3000
        $lz = Wait-Ready $z
        Assert-True 'T2 Z times out' ($lz.Acquired -eq $false) ($lz | ConvertTo-Json -Compress)
        Assert-True "T2 Z blocker is runtime-slots" ($lz.Blocker -eq 'runtime-slots') "blocker=$($lz.Blocker)"
        Assert-True 'T2 Z holds nothing' ([int]$lz.HeldCount -eq 0) "held=$($lz.HeldCount)"
        Assert-True 'T2 Z waited the full window' ([int64]$lz.WaitMs -ge 2900) "waitMs=$($lz.WaitMs)"
        $rel = Invoke-HolderRelease $x
        Assert-True 'T2 X released with no exception' ($rel.Error -eq '') $rel.Error
        Assert-True 'T2 X is still alive at the probe' (-not $x.Proc.HasExited)
        Assert-True 'T2 slot 1 is free after X released' (Test-MutexFree $n.s1)
        Assert-True 'T2 ck-X is free after X released' (Test-MutexFree ($n.ckPrefix + 'X'))
        $z2 = Start-Holder -N $n -Checkout Z -TimeoutMs 3000
        $lz2 = Wait-Ready $z2
        Assert-True 'T2 a new Z acquires slot 1 within 3s' ($lz2.Acquired -eq $true -and (@($lz2.Slots) -join ',') -eq '1' -and [int64]$lz2.WaitMs -le 3000) ($lz2 | ConvertTo-Json -Compress)
    }

    # ------------------------------------------------------------------ T3
    Invoke-Case 'T3' {
        # a. waiter path: proof handles open, W waits before the kill.
        $n = New-Names
        $s0 = Open-Handle $n.s0; $s1 = Open-Handle $n.s1
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)
        $y = Start-Holder -N $n -Checkout Y -Runner; $ly = Wait-Ready $y
        $runner = [int]$ly.RunnerPid
        Assert-True 'T3a Y holds slot 0 with a runner' ((@($ly.Slots) -join ',') -eq '0' -and $runner -gt 0) ($ly | ConvertTo-Json -Compress)
        $w = Start-Holder -N $n -Checkout W -TimeoutMs 30000
        Wait-Entering $w
        Start-Sleep -Milliseconds 500
        Assert-True 'T3a W is waiting before the kill' (-not (Test-Path (Join-Path $w.Dir 'ready.json')))
        $killAt = Get-Date
        $y.Proc.Kill()
        $lw = Wait-Ready $w -Seconds 20
        $latency = ((Get-Date) - $killAt).TotalMilliseconds
        # The bound only has to sit well under W's 30s timeout, which separates taking the abandoned slot
        # from timing out. The reap inside the acquisition ran 3.5s on a loaded machine, so a tighter bound flakes.
        Assert-True 'T3a W acquired within 10s of the kill' ($lw.Acquired -eq $true -and $latency -le 10000) "latencyMs=$([int]$latency) lease=$($lw | ConvertTo-Json -Compress)"
        Assert-True 'T3a W holds slot 0 (MutexIndex 1 path)' ((@($lw.Slots) -join ',') -eq '0') "slots=$(@($lw.Slots) -join ',')"
        Assert-True 'T3a W reports Abandoned' ($lw.Abandoned -eq $true)
        Assert-True 'T3a W reaped the orphan runner before writing its ready file' ((@($lw.Reaped) -contains $runner)) "reaped=$(@($lw.Reaped) -join ',') runner=$runner"
        Assert-True 'T3a the orphan runner is gone' (Test-ProcessGone $runner)
        Assert-True 'T3a ck-Y is acquirable' (Test-MutexFree ($n.ckPrefix + 'Y'))
        Stop-All

        # b. fresh path: no proof handle, W starts after the kill.
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)
        $y = Start-Holder -N $n -Checkout Y -Runner; $ly = Wait-Ready $y
        $runner = [int]$ly.RunnerPid
        $y.Proc.Kill(); [void]$y.Proc.WaitForExit(5000)
        $w = Start-Holder -N $n -Checkout W -TimeoutMs 30000
        $lw = Wait-Ready $w
        Assert-True 'T3b W acquires slot 0 within 10s' ($lw.Acquired -eq $true -and (@($lw.Slots) -join ',') -eq '0' -and [int64]$lw.WaitMs -le 10000) ($lw | ConvertTo-Json -Compress)
        Assert-True 'T3b Reaped names the orphan runner' ((@($lw.Reaped) -contains $runner)) "reaped=$(@($lw.Reaped) -join ',') runner=$runner"
        Assert-True 'T3b the orphan runner is gone' (Test-ProcessGone $runner)
        Stop-All

        # c. sweep by a non-holder: an old-commit stand-in takes s0 and writes no record.
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)
        $y = Start-Holder -N $n -Checkout Y -Runner; $ly = Wait-Ready $y
        $runner = [int]$ly.RunnerPid
        $y.Proc.Kill(); [void]$y.Proc.WaitForExit(5000)
        $old = Start-Holder -N $n -Mode raw -RawName $n.s0
        $lo = Wait-Ready $old
        Assert-True 'T3c the raw stand-in holds s0' ($lo.Acquired -eq $true)
        $recPath = Join-Path $n.rec 'slot-0.json'
        $before = [System.IO.File]::ReadAllBytes($recPath)
        $killed = @(Invoke-RuntimeSlotSweep -RecordDir $n.rec)
        $after = [System.IO.File]::ReadAllBytes($recPath)
        Assert-True 'T3c the sweep returns the orphan runner' ($killed -contains $runner) "killed=$($killed -join ',') runner=$runner"
        Assert-True 'T3c the orphan runner is gone' (Test-ProcessGone $runner)
        Assert-True 'T3c the slot-0 record is byte-identical' ([System.Linq.Enumerable]::SequenceEqual($before, $after))
    }

    # ------------------------------------------------------------------ T4
    Invoke-Case 'T4' {
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X; $lx = Wait-Ready $x
        Assert-True 'T4 X holds slot 1, slot 0 free' ((@($lx.Slots) -join ',') -eq '1' -and (Test-MutexFree $n.s0))
        $x2 = Start-Holder -N $n -Checkout X -TimeoutMs 4000
        Wait-Entering $x2
        Start-Sleep -Milliseconds 1500
        Assert-True 'T4 the free slot stays unowned while the second X run waits' ((-not (Test-Path (Join-Path $x2.Dir 'ready.json'))) -and (Test-MutexFree $n.s0))
        $l2 = Wait-Ready $x2
        Assert-True "T4 the second X run times out on the checkout lock" ($l2.Acquired -eq $false -and $l2.Blocker -eq 'checkout') ($l2 | ConvertTo-Json -Compress)
    }

    # ------------------------------------------------------------------ T5
    Invoke-Case 'T5' {
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X -SlotCount 1; $lx = Wait-Ready $x
        $allSlots = (0..((Get-ModuleMax) - 1)) -join ','
        Assert-True 'T5a C=1 holds every slot' ((@($lx.Slots) -join ',') -eq $allSlots) "slots=$(@($lx.Slots) -join ',') want=$allSlots"
        Assert-True 'T5a C=1 holds the checkout lock and every slot' ([int]$lx.HeldCount -eq (1 + (Get-ModuleMax))) "held=$($lx.HeldCount)"
        $y = Start-Holder -N $n -Checkout Y -SlotCount 1 -TimeoutMs 2000
        $y2 = Start-Holder -N $n -Checkout Y2 -SlotCount 2 -TimeoutMs 2000
        $ly = Wait-Ready $y; $ly2 = Wait-Ready $y2
        Assert-True 'T5a a C=1 peer waits on the slots' ($ly.Acquired -eq $false -and $ly.Blocker -eq 'runtime-slots') ($ly | ConvertTo-Json -Compress)
        Assert-True 'T5a a C=2 peer waits on the slots' ($ly2.Acquired -eq $false -and $ly2.Blocker -eq 'runtime-slots') ($ly2 | ConvertTo-Json -Compress)
        Stop-All

        $n = New-Names
        $h = Start-Holder -N $n -Checkout H -SlotCount 2; $lh = Wait-Ready $h
        Assert-True 'T5b setup: a C=2 holder has s1' ((@($lh.Slots) -join ',') -eq '1')
        $p = Start-Holder -N $n -Checkout P -SlotCount 1 -TimeoutMs 2500
        $lp = Wait-Ready $p
        Assert-True 'T5b the C=1 run times out on slot 1' ($lp.Acquired -eq $false -and $lp.Blocker -eq 'runtime-slots' -and [int]$lp.HeldCount -eq 0) ($lp | ConvertTo-Json -Compress)
        $rel = Invoke-HolderRelease $p
        Assert-True 'T5b its released file has no exception' ($rel.Error -eq '') $rel.Error
        Assert-True 'T5b s0 is unowned while it is still alive' ((-not $p.Proc.HasExited) -and (Test-MutexFree $n.s0))
    }

    # ------------------------------------------------------------------ T6
    Invoke-Case 'T6' {
        Assert-True 'T6 static: the committed default is within 1..max' ($script:RuntimeSlotDefault -in 1..(Get-ModuleMax)) "default=$script:RuntimeSlotDefault"
        $committed = $script:RuntimeSlotDefault
        $repoDir = Join-Path $work 'not-a-repo'
        New-Item -ItemType Directory -Force -Path $repoDir | Out-Null
        $ov = Join-Path $work 'override-runtime-slots'
        try {
            foreach ($d in 1, 2) {
                $script:RuntimeSlotDefault = $d
                Remove-Item $ov -Force -ErrorAction SilentlyContinue
                $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                Assert-True "T6 [default=$d] absent file -> the default" ($r.Count -eq $d -and $r.Source -eq 'default') ($r | ConvertTo-Json -Compress)
                Set-Content -Path $ov -Value '1' -NoNewline
                $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                Assert-True "T6 [default=$d] '1' -> 1 (override)" ($r.Count -eq 1 -and $r.Source -eq $(if ($d -eq 1) { 'default' } else { 'override' })) ($r | ConvertTo-Json -Compress)
                Set-Content -Path $ov -Value "2`r`n" -NoNewline
                $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                Assert-True "T6 [default=$d] '2' -> the default" ($r.Count -eq $d) ($r | ConvertTo-Json -Compress)
                foreach ($bad in @('0', "$((Get-ModuleMax) + 1)", 'abc', '')) {
                    Set-Content -Path $ov -Value $bad -NoNewline
                    $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                    Assert-True "T6 [default=$d] '$bad' -> 1 (override-invalid)" ($r.Count -eq 1 -and $r.Source -eq 'override-invalid') ($r | ConvertTo-Json -Compress)
                }
                $r = Get-RuntimeSlotCount -Repo $repoDir
                Assert-True "T6 [default=$d] a non-git repo -> 1 (override-unreadable)" ($r.Count -eq 1 -and $r.Source -eq 'override-unreadable') ($r | ConvertTo-Json -Compress)
            }
            $script:RuntimeSlotDefault = 2
            Remove-Item $ov -Force -ErrorAction SilentlyContinue
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov -Ceiling 1
            Assert-True 'T6 -Ceiling 1 -> 1 (ceiling)' ($r.Count -eq 1 -and $r.Source -eq 'ceiling') ($r | ConvertTo-Json -Compress)
        } finally { $script:RuntimeSlotDefault = $committed }
    }

    # ------------------------------------------------------------------ T7
    Invoke-Case 'T7' {
        $n = New-Names
        $names = $n.names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)
        $r = Test-RuntimeSlotAvailable -Repo 'C:\slots-fixture\P' -SlotCount 2 -SlotNames $names
        Assert-True 'T7 C=2 with one slot held -> true' ($r -eq $true) "got=$r"
        $f = Start-Holder -N $n -Checkout F -TimeoutMs 1000; $lf = Wait-Ready $f
        Assert-True 'T7 a fresh holder then acquires the free slot at once' ($lf.Acquired -eq $true) ($lf | ConvertTo-Json -Compress)
        $r = Test-RuntimeSlotAvailable -Repo 'C:\slots-fixture\P' -SlotCount 2 -SlotNames $names
        Assert-True 'T7 C=2 with both held -> false' ($r -eq $false) "got=$r"
        Stop-All

        $n = New-Names
        $names = $n.names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)   # takes s1
        $r = Test-RuntimeSlotAvailable -Repo 'C:\slots-fixture\P' -SlotCount 1 -SlotNames $names
        Assert-True 'T7 C=1 with only s1 held -> false' ($r -eq $false) "got=$r"
        Assert-True 'T7 s0 is unowned after the C=1 probe' (Test-MutexFree $n.s0)
        $f = Start-Holder -N $n -Checkout F -TimeoutMs 1000; $lf = Wait-Ready $f
        Assert-True 'T7 a fresh holder then acquires s0 at once' ($lf.Acquired -eq $true -and (@($lf.Slots) -join ',') -eq '0') ($lf | ConvertTo-Json -Compress)
        Stop-All

        $n = New-Names
        $names = $n.names
        $h0 = Open-Handle $n.s0; $h1 = Open-Handle $n.s1
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)   # s1
        $y = Start-Holder -N $n -Checkout Y; [void](Wait-Ready $y)   # s0
        $y.Proc.Kill(); [void]$y.Proc.WaitForExit(5000)
        $r = Test-RuntimeSlotAvailable -Repo 'C:\slots-fixture\P' -SlotCount 2 -SlotNames $names
        Assert-True "T7 a killed holder's abandoned slot probes free" ($r -eq $true) "got=$r"
        $f = Start-Holder -N $n -Checkout F -TimeoutMs 1000; $lf = Wait-Ready $f
        Assert-True 'T7 the probe kept nothing: a fresh holder takes it at once' ($lf.Acquired -eq $true -and (@($lf.Slots) -join ',') -eq '0') ($lf | ConvertTo-Json -Compress)
        Stop-All

        $n = New-Names
        $ck = $n.ckPrefix + 'X'
        $x = Start-Holder -N $n -Checkout X -Logic; [void](Wait-Ready $x)
        Assert-True 'T7 Test-CheckoutLockFree is false while ck-X is held' ((Test-CheckoutLockFree -Repo 'C:\slots-fixture\X' -CheckoutLockName $ck) -eq $false)
        [void](Invoke-HolderRelease $x)
        Assert-True 'T7 Test-CheckoutLockFree is true afterwards' ((Test-CheckoutLockFree -Repo 'C:\slots-fixture\X' -CheckoutLockName $ck) -eq $true)
        $f = Start-Holder -N $n -Checkout X -Logic -TimeoutMs 1000; $lf = Wait-Ready $f
        Assert-True 'T7 the lock probe kept nothing: a fresh holder takes ck-X at once' ($lf.Acquired -eq $true)
    }

    # ------------------------------------------------------------------ T8
    Invoke-Case 'T8' {
        function New-ProofLease {
            param($N, [string] $Checkout = 'L', [int] $SlotCount = 2)
            Enter-RunLocks -Repo "C:\slots-fixture\$Checkout" -Label $Checkout -Runtime -SlotCount $SlotCount `
                -TimeoutMs 3000 -SlotNames $N.names -CheckoutLockName ($N.ckPrefix + $Checkout) -RecordDir $N.rec
        }
        # Acquired while the other slot was held.
        $n = New-Names
        $y = Start-Holder -N $n -Checkout Y; [void](Wait-Ready $y)   # s1
        $l = New-ProofLease $n
        try {
            Assert-True 'T8 setup: the lease is on slot 0 beside a live peer' ($l.Acquired -and (@($l.Slots) -join ',') -eq '0' -and $l.PeerSlotHeld)
            [void](Invoke-HolderRelease $y)
            Assert-True 'T8 true when acquired while the other slot was held' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $true)
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # A peer acquired and released the other slot inside this lease's window.
        $n = New-Names
        $l = New-ProofLease $n
        try {
            Start-Sleep -Milliseconds 50
            $y = Start-Holder -N $n -Checkout Y; $ly = Wait-Ready $y   # s0
            [void](Invoke-HolderRelease $y); Stop-Holder $y
            Assert-True 'T8 setup: the lease is solo on slot 1 and the peer took slot 0' ((@($l.Slots) -join ',') -eq '1' -and -not $l.PeerSlotHeld -and (@($ly.Slots) -join ',') -eq '0')
            Assert-True 'T8 true when a peer acquired and released inside the window' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $true)
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # Unreadable other-slot record.
        $n = New-Names
        $l = New-ProofLease $n
        try {
            $other = Join-Path $n.rec 'slot-0.json'
            foreach ($torn in @('', '{"holderPid": 12')) {
                [System.IO.File]::WriteAllText($other, $torn)
                $res = $null; $threw = $null
                try { $res = Test-RuntimeOverlap -Lease $l -RecordDir $n.rec } catch { $threw = $_.Exception.Message }
                Assert-True "T8 an unreadable record ('$torn') counts as overlap without throwing" ($null -eq $threw -and $res -eq $true) "threw=$threw result=$res"
            }
            Remove-Item $other -Force
            Assert-True 'T8 false for a solo lease' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $false)
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # The other slot's holder was killed, and its runner reaped, before this lease acquired.
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X; [void](Wait-Ready $x)               # s1
        $y = Start-Holder -N $n -Checkout Y -Runner; $ly = Wait-Ready $y        # s0 + runner
        [void](Invoke-HolderRelease $x); Stop-Holder $x
        $y.Proc.Kill(); [void]$y.Proc.WaitForExit(5000)
        $l = New-ProofLease $n
        try {
            Assert-True 'T8 setup: the lease took slot 1 and reaped the dead peer runner' ((@($l.Slots) -join ',') -eq '1' -and (@($l.Reaped) -contains [int]$ly.RunnerPid))
            $over = @(Get-RuntimeOverlapSlots -Lease $l -RecordDir $n.rec)
            Assert-True "T8 false for a peer killed (runner reaped) before this lease" ($over.Count -eq 0) "overlap=$($over -join ',') peerSlots=$(@($l.PeerSlots) -join ',')"
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # A C=1 run parked on slot 0 in state=waiting while this lease holds slot 1.
        $n = New-Names
        $l = New-ProofLease $n
        try {
            Assert-True 'T8 setup: the lease holds slot 1' ($l.Acquired -and (@($l.Slots) -join ',') -eq '1')
            $p = Start-Holder -N $n -Checkout P -SlotCount 1 -TimeoutMs 20000
            $rec0 = Join-Path $n.rec 'slot-0.json'
            $r0 = $null
            $deadline = (Get-Date).AddSeconds(10)
            while ((Get-Date) -lt $deadline) {
                $r0 = if (Test-Path $rec0) { Read-JsonFile $rec0 } else { $null }
                if ($r0 -and $r0.state -eq 'waiting') { break }
                Start-Sleep -Milliseconds 50
            }
            Assert-True 'T8 setup: the C=1 run is parked on slot 0' ($r0 -and $r0.state -eq 'waiting')
            Assert-True 'T8 false while a C=1 run is parked on slot 0' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $false)
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # A waiter on the checkout lock, and a Logic holder, never count.
        $n = New-Names
        $logic = Start-Holder -N $n -Checkout X -Logic; [void](Wait-Ready $logic)
        $waiter = Start-Holder -N $n -Checkout X -TimeoutMs 15000
        $l = New-ProofLease $n
        try {
            Wait-Entering $waiter
            Start-Sleep -Milliseconds 500
            Assert-True 'T8 false with a live checkout-lock waiter and a live Logic holder' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $false)
        } finally { Exit-RunLocks -Lease $l }
        Stop-All

        # A C=1 lease.
        $n = New-Names
        $l = New-ProofLease $n -SlotCount 1
        try {
            Assert-True 'T8 false for a C=1 lease' ($l.Acquired -and (Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $false)
        } finally { Exit-RunLocks -Lease $l }
    }

    # ------------------------------------------------------------------ T9 (static)
    Invoke-Case 'T9' {
        foreach ($name in @('run_test_suite.ps1', 'regression_gate.ps1')) {
            $text = [System.IO.File]::ReadAllText((Join-Path $scriptsDir $name))
            Assert-True "T9 $name holds no Global\gdunit4 literal" (-not $text.Contains('Global\gdunit4'))
            Assert-True "T9 $name dot-sources RuntimeSlots.ps1" ($text -match "(?m)^\s*\.\s+(\(Join-Path \`$PSScriptRoot 'RuntimeSlots\.ps1'\)|\`$runtimeSlotsP)\s*$")
        }
        $batched = [System.IO.File]::ReadAllText((Join-Path $scriptsDir 'run_integration_batched.ps1'))
        $defined = $batched -match [regex]::Escape("`$passThroughPattern = '^(FAILED_TEST|FAILED_MSG|OVERLAP_SIGNATURE|SLOT)='")
        $hits = if ($defined) { [regex]::Matches($batched, [regex]::Escape('-match $passThroughPattern')).Count } else { 0 }
        $calls = [regex]::Matches($batched, '(?m)^\s+Write-PassThrough \$res\[0\]').Count
        Assert-True 'T9 run_integration_batched.ps1 passes SLOT/OVERLAP_SIGNATURE through in both loops (one filter, called from the batch and retry loops)' ($hits -eq 1 -and $calls -eq 2) "hits=$hits calls=$calls"
    }

    # ------------------------------------------------------------------ T10
    Invoke-Case 'T10' {
        $n = New-Names
        $saved = $env:HARNESS_GATE_RUN
        try {
            $env:HARNESS_GATE_RUN = 'g1'
            Add-RuntimeSlotTrigger -Repo 'C:\slots-fixture\X' -Label 'a' -Line 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=2 filter=A' -RecordDir $n.rec
            Add-RuntimeSlotTrigger -Repo 'C:\slots-fixture\X' -Label 'b' -Line 'OVERLAP_SIGNATURE=executor-connect attempt=2 exit=1 slots=2 filter=B' -RecordDir $n.rec
            $env:HARNESS_GATE_RUN = 'g2'
            Add-RuntimeSlotTrigger -Repo 'C:\slots-fixture\X' -Label 'c' -Line 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=2 filter=C' -RecordDir $n.rec
            Remove-Item Env:\HARNESS_GATE_RUN -ErrorAction SilentlyContinue
            Add-RuntimeSlotTrigger -Repo 'C:\slots-fixture\X' -Label 'd' -Line 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=2 filter=D' -RecordDir $n.rec
        } finally {
            if ($null -eq $saved) { Remove-Item Env:\HARNESS_GATE_RUN -ErrorAction SilentlyContinue } else { $env:HARNESS_GATE_RUN = $saved }
        }
        $g1 = @(Get-RuntimeSlotTriggers -GateRun 'g1' -RecordDir $n.rec)
        Assert-True 'T10 -GateRun g1 returns exactly the two g1 lines' ($g1.Count -eq 2 -and @($g1 | Where-Object { $_ -match ' gateRun=g1 ' }).Count -eq 2) ($g1 -join "`n")
        $all = @(Get-Content -LiteralPath (Join-Path $n.rec 'triggers.log'))
        Assert-True 'T10 a direct run logs gateRun=-' (@($all | Where-Object { $_ -match ' gateRun=- .*filter=D$' }).Count -eq 1) ($all -join "`n")

        $a = Start-Holder -N $n -Mode append -Checkout A -Tag A
        $b = Start-Holder -N $n -Mode append -Checkout B -Tag B
        Start-Sleep -Milliseconds 800
        Set-Content -Path (Join-Path $a.Dir 'go') -Value 'x' -NoNewline
        Set-Content -Path (Join-Path $b.Dir 'go') -Value 'x' -NoNewline
        $ra = $null; $rb = $null
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline -and -not ($ra -and $rb)) {
            if (-not $ra -and (Test-Path (Join-Path $a.Dir 'released.json'))) { $ra = Read-JsonFile (Join-Path $a.Dir 'released.json') }
            if (-not $rb -and (Test-Path (Join-Path $b.Dir 'released.json'))) { $rb = Read-JsonFile (Join-Path $b.Dir 'released.json') }
            Start-Sleep -Milliseconds 50
        }
        Assert-True 'T10 concurrent appenders report no exception' ($ra -and $rb -and $ra.Error -eq '' -and $rb.Error -eq '') "a=$($ra | ConvertTo-Json -Compress) b=$($rb | ConvertTo-Json -Compress)"
        $all = @(Get-Content -LiteralPath (Join-Path $n.rec 'triggers.log'))
        $na = @($all | Where-Object { $_ -match ' tag=A n=\d+$' }).Count
        $nb = @($all | Where-Object { $_ -match ' tag=B n=\d+$' }).Count
        Assert-True 'T10 all 100 concurrent lines are present' ($na -eq 50 -and $nb -eq 50) "A=$na B=$nb"

        $running = Join-Path $repoRoot '.claude\skills\testing\reference\running.md'
        $kindLines = @(Get-Content -LiteralPath $running | Where-Object { $_ -match '^Trigger kinds:' })
        $docKinds = @()
        if ($kindLines.Count -eq 1) { $docKinds = @([regex]::Matches($kindLines[0], '`([^`]+)`') | ForEach-Object { $_.Groups[1].Value }) }
        $diff = Compare-Object @($docKinds | Sort-Object) @($script:OverlapSignatureKinds | Sort-Object)
        Assert-True "T10 running.md's one 'Trigger kinds:' line equals the module's kinds" ($kindLines.Count -eq 1 -and $docKinds.Count -gt 0 -and -not $diff) "lines=$($kindLines.Count) doc=$($docKinds -join ',') module=$($script:OverlapSignatureKinds -join ',')"
    }

    # ------------------------------------------------------------------ T11
    Invoke-Case 'T11' {
        $clean = Join-Path $work 'godot_clean.log'
        [System.IO.File]::WriteAllText($clean, "Godot Engine v4.7.1`nall good`n")
        foreach ($k in @('testhost-native-crash', 'executor-connect', 'run-aborted')) {
            $got = Get-OverlapSignatureKind -SkipKind $k -ExitCode 1 -GodotLog $clean
            Assert-True "T11 $k maps to itself" ($got -eq $k) "got=$got"
        }
        $got = Get-OverlapSignatureKind -SkipKind '' -ExitCode -1073741795 -GodotLog $clean -Platform windows
        Assert-True 'T11 the native-crash exit code alone maps to testhost-native-crash' ($got -eq 'testhost-native-crash') "got=$got"
        $got = Get-OverlapSignatureKind -SkipKind 'refcounted-free-fatal' -ExitCode 1 -GodotLog $clean
        Assert-True 'T11 refcounted-free-fatal is never a kind' ($null -eq $got) "got=$got"
        $bad = Join-Path $work 'godot_cache.log'
        [System.IO.File]::WriteAllText($bad, "ERROR: Unable to open file: user://cache/x.bin`n")
        $got = Get-OverlapSignatureKind -SkipKind '' -ExitCode 1 -GodotLog $bad
        Assert-True "T11 'Unable to open' in godot_test.log -> cache-file-error" ($got -eq 'cache-file-error') "got=$got"
        $got = Get-OverlapSignatureKind -SkipKind '' -ExitCode 0 -GodotLog $clean
        Assert-True 'T11 a clean log with exit 0 -> none' ($null -eq $got) "got=$got"
        $got = Get-OverlapSignatureKind -SkipKind '' -ExitCode 0 -GodotLog (Join-Path $work 'absent.log')
        Assert-True 'T11 an absent log with exit 0 -> none' ($null -eq $got) "got=$got"
    }

    # ------------------------------------------------------------------ T12
    Invoke-Case 'T12' {
        $root = 'C:\slots-fixture\Root'
        Assert-True 'T12 a nested worktree is a different checkout' ((Get-WorktreeId $root) -ne (Get-WorktreeId "$root\.claude\worktrees\peer"))
        Assert-True 'T12 trailing-slash and case variants are one checkout' (
            (Get-WorktreeId $root) -eq (Get-WorktreeId "$root\") -and
            (Get-WorktreeId $root) -eq (Get-WorktreeId $root.ToUpperInvariant()))
        Assert-True 'T12 the checkout lock is named by that identity' (
            (Get-CheckoutLockName -Repo $root) -eq (Get-CheckoutLockName -Repo "$root\") -and
            (Get-CheckoutLockName -Repo $root) -ne (Get-CheckoutLockName -Repo "$root\.claude\worktrees\peer"))
    }

    # ------------------------------------------------------------------ T13 (names)
    Invoke-Case 'T13' {
        $max = Get-ModuleMax
        $names = @(Get-RuntimeSlotNames)
        $declared = Get-Variable -Name RuntimeSlotMax -Scope Script -ValueOnly -ErrorAction SilentlyContinue
        Assert-True 'T13 RuntimeSlotMax is declared and equals the name count' ($null -ne $declared -and [int]$declared -eq $names.Count) "declared=$declared names=$($names.Count)"
        Assert-True 'T13 there are more names than the old two' ($names.Count -gt 2) "names=$($names.Count)"
        Assert-True 'T13 slot 0 keeps the legacy literal' ($names[0] -ceq 'Global\gdunit4-{{PROJECT_NAME}}-runlock') "slot0=$($names[0])"
        Assert-True 'T13 slot 1 keeps the legacy literal' ($names[1] -ceq 'Global\gdunit4-{{PROJECT_NAME}}-runlock-slot1') "slot1=$($names[1])"
        $bad = @(2..($max - 1) | Where-Object { $names.Count -le $_ -or $names[$_] -cne "Global\gdunit4-{{PROJECT_NAME}}-runlock-slot$_" })
        Assert-True 'T13 slot k >= 2 is the legacy prefix plus -slot<k>' ($names.Count -gt 2 -and $bad.Count -eq 0) "bad=$($bad -join ',')"
        Assert-True 'T13 names are unique' (@($names | Select-Object -Unique).Count -eq $names.Count)
    }

    # ------------------------------------------------------------------ T14 (count 3, highest free slot first)
    Invoke-Case 'T14' {
        $n = New-Names
        $a = Start-Holder -N $n -Checkout A -SlotCount 3; $la = Wait-Ready $a
        Assert-True 'T14 the first C=3 holder acquires slot 2' ($la.Acquired -eq $true -and (@($la.Slots) -join ',') -eq '2') ($la | ConvertTo-Json -Compress)
        $b = Start-Holder -N $n -Checkout B -SlotCount 3; $lb = Wait-Ready $b
        Assert-True 'T14 the second C=3 holder acquires slot 1' ($lb.Acquired -eq $true -and (@($lb.Slots) -join ',') -eq '1') ($lb | ConvertTo-Json -Compress)
        $c = Start-Holder -N $n -Checkout C -SlotCount 3; $lc = Wait-Ready $c
        Assert-True 'T14 the third C=3 holder acquires slot 0' ($lc.Acquired -eq $true -and (@($lc.Slots) -join ',') -eq '0') ($lc | ConvertTo-Json -Compress)
        Assert-True 'T14 three holders overlap: every one is still alive' (-not $a.Proc.HasExited -and -not $b.Proc.HasExited -and -not $c.Proc.HasExited)
        $d = Start-Holder -N $n -Checkout D -SlotCount 3 -TimeoutMs 2000; $ld = Wait-Ready $d
        Assert-True 'T14 a fourth C=3 holder times out on the slots' ($ld.Acquired -eq $false -and $ld.Blocker -eq 'runtime-slots' -and [int]$ld.HeldCount -eq 0) ($ld | ConvertTo-Json -Compress)
        if ($n.names.Count -gt 3) {
            Assert-True 'T14 slots above the count stay unowned' (@(3..($n.names.Count - 1) | Where-Object { -not (Test-MutexFree $n.names[$_]) }).Count -eq 0)
        }
    }

    # ------------------------------------------------------------------ T15 (count 1 is exclusive against every slot)
    Invoke-Case 'T15' {
        $max = Get-ModuleMax
        $n = New-Names
        $x = Start-Holder -N $n -Checkout X -SlotCount 1; $lx = Wait-Ready $x
        $all = (0..($max - 1)) -join ','
        Assert-True 'T15 a C=1 holder reports every slot' ($lx.Acquired -eq $true -and (@($lx.Slots) -join ',') -eq $all) "slots=$(@($lx.Slots) -join ',') want=$all"
        $held = @(0..($max - 1) | Where-Object { -not (Test-MutexFree $n.names[$_]) })
        Assert-True 'T15 every slot name is held' ($held.Count -eq $max) "held=$($held -join ',')"
        $running = @(0..($max - 1) | Where-Object { $r = Read-SlotRecordFile $n.rec $_; $r -and $r.state -eq 'running' })
        Assert-True 'T15 every held slot record says running' ($running.Count -eq $max) "running=$($running -join ',')"
        $y = Start-Holder -N $n -Checkout Y -SlotCount 1 -TimeoutMs 1500
        $z = Start-Holder -N $n -Checkout Z -SlotCount 3 -TimeoutMs 1500
        $w = Start-Holder -N $n -Checkout W -SlotCount $max -TimeoutMs 1500
        foreach ($h in $y, $z, $w) {
            $l = Wait-Ready $h
            Assert-True "T15 holder $($h.Name) is excluded by the C=1 run" ($l.Acquired -eq $false -and $l.Blocker -eq 'runtime-slots') ($l | ConvertTo-Json -Compress)
        }
        $rel = Invoke-HolderRelease $x
        Assert-True 'T15 the C=1 run releases with no exception' ($rel.Error -eq '') $rel.Error
        Assert-True 'T15 every slot is free after the release' (@(0..($max - 1) | Where-Object { -not (Test-MutexFree $n.names[$_]) }).Count -eq 0)
    }

    # ------------------------------------------------------------------ T16 (lowering the count mid-flight)
    Invoke-Case 'T16' {
        $n = New-Names
        $repoDir = Join-Path $work 'not-a-repo'
        New-Item -ItemType Directory -Force -Path $repoDir | Out-Null
        $ov = Join-Path $work 'override-lowering'
        $committed = $script:RuntimeSlotDefault
        try {
            $script:RuntimeSlotDefault = 3
            Remove-Item $ov -Force -ErrorAction SilentlyContinue
            $before = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            $h = Start-Holder -N $n -Checkout H -SlotCount $before.Count; $lh = Wait-Ready $h
            Assert-True 'T16 setup: a holder sits on slot 2 at count 3' ($before.Count -eq 3 -and (@($lh.Slots) -join ',') -eq '2') "count=$($before.Count) $($lh | ConvertTo-Json -Compress)"
            Set-Content -Path $ov -Value '2' -NoNewline
            $after = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            Assert-True 'T16 the override lowers the count to 2' ($after.Count -eq 2 -and $after.Source -eq 'override') ($after | ConvertTo-Json -Compress)
            $a = Start-Holder -N $n -Checkout A -SlotCount $after.Count; $la = Wait-Ready $a
            $b = Start-Holder -N $n -Checkout B -SlotCount $after.Count; $lb = Wait-Ready $b
            Assert-True 'T16 new runs take slots 1 and 0, never slot 2' ((@($la.Slots) -join ',') -eq '1' -and (@($lb.Slots) -join ',') -eq '0') "a=$(@($la.Slots) -join ',') b=$(@($lb.Slots) -join ',')"
            $c = Start-Holder -N $n -Checkout C -SlotCount $after.Count -TimeoutMs 1500; $lc = Wait-Ready $c
            Assert-True 'T16 a third new run waits although slots 3 and up are free' ($lc.Acquired -eq $false -and $lc.Blocker -eq 'runtime-slots') ($lc | ConvertTo-Json -Compress)
            Assert-True 'T16 the drained holder still owns slot 2' (-not (Test-MutexFree $n.names[2]))
            [void](Invoke-HolderRelease $h)
            $d = Start-Holder -N $n -Checkout D -SlotCount $after.Count -TimeoutMs 1500; $ld = Wait-Ready $d
            Assert-True 'T16 a freed slot 2 is not used at count 2' ($ld.Acquired -eq $false -and (Test-MutexFree $n.names[2])) ($ld | ConvertTo-Json -Compress)
        } finally { $script:RuntimeSlotDefault = $committed }
    }

    # ------------------------------------------------------------------ T17 (count resolution over 1..max)
    Invoke-Case 'T17' {
        $max = Get-ModuleMax
        $repoDir = Join-Path $work 'not-a-repo'
        New-Item -ItemType Directory -Force -Path $repoDir | Out-Null
        $ov = Join-Path $work 'override-values'
        $committed = $script:RuntimeSlotDefault
        try {
            $script:RuntimeSlotDefault = $max
            Remove-Item $ov -Force -ErrorAction SilentlyContinue
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            Assert-True 'T17 [default=max] absent file -> max (default)' ($r.Count -eq $max -and $r.Source -eq 'default') ($r | ConvertTo-Json -Compress)
            Set-Content -Path $ov -Value "3`r`n" -NoNewline
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            Assert-True 'T17 [default=max] override 3 -> 3 (override)' ($r.Count -eq 3 -and $r.Source -eq 'override') ($r | ConvertTo-Json -Compress)
            Set-Content -Path $ov -Value "$max" -NoNewline
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            Assert-True 'T17 [default=max] override max -> max (default)' ($r.Count -eq $max -and $r.Source -eq 'default') ($r | ConvertTo-Json -Compress)
            foreach ($bad in @("$($max + 1)", '0', 'x', '-1', '2.5', '')) {
                Set-Content -Path $ov -Value $bad -NoNewline
                $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                Assert-True "T17 override '$bad' -> 1 (override-invalid)" ($r.Count -eq 1 -and $r.Source -eq 'override-invalid') ($r | ConvertTo-Json -Compress)
            }
            Remove-Item $ov -Force -ErrorAction SilentlyContinue
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov -Ceiling 0
            Assert-True 'T17 -Ceiling 0 is no ceiling' ($r.Count -eq $max) ($r | ConvertTo-Json -Compress)
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov -Ceiling ($max + 5)
            Assert-True 'T17 a ceiling above max clamps to max' ($r.Count -eq $max) ($r | ConvertTo-Json -Compress)
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov -Ceiling 3
            Assert-True 'T17 -Ceiling 3 -> 3 (ceiling)' ($r.Count -eq 3 -and $r.Source -eq 'ceiling') ($r | ConvertTo-Json -Compress)
            foreach ($d in 0, ($max + 1), -1) {
                $script:RuntimeSlotDefault = $d
                $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
                Assert-True "T17 a default of $d fails closed to 1" ($r.Count -eq 1) ($r | ConvertTo-Json -Compress)
            }
            $script:RuntimeSlotDefault = 2
            Set-Content -Path $ov -Value '3' -NoNewline
            $r = Get-RuntimeSlotCount -Repo $repoDir -OverridePath $ov
            Assert-True 'T17 the override cannot raise the count above the default' ($r.Count -eq 2) ($r | ConvertTo-Json -Compress)
        } finally { $script:RuntimeSlotDefault = $committed }
    }

    # ------------------------------------------------------------------ T18 (the highest slot, and the caller-error throw)
    Invoke-Case 'T18' {
        $max = Get-ModuleMax
        $top = $max - 1
        function New-ProofLease {
            param($N, [string] $Checkout = 'L', [int] $SlotCount = 2)
            Enter-RunLocks -Repo "C:\slots-fixture\$Checkout" -Label $Checkout -Runtime -SlotCount $SlotCount `
                -TimeoutMs 3000 -SlotNames $N.names -CheckoutLockName ($N.ckPrefix + $Checkout) -RecordDir $N.rec
        }
        $n = New-Names
        $p = Start-Holder -N $n -Checkout P -SlotCount $max -Runner; $lp = Wait-Ready $p
        $runner = [int]$lp.RunnerPid
        Assert-True 'T18 a count-max holder takes the highest slot' ((@($lp.Slots) -join ',') -eq "$top" -and $runner -gt 0) ($lp | ConvertTo-Json -Compress)
        $l = New-ProofLease $n
        try {
            Assert-True 'T18 a lease beside a peer on the highest slot sees PeerSlotHeld' ($l.Acquired -and $l.PeerSlotHeld) "slots=$(@($l.Slots) -join ',') peer=$($l.PeerSlotHeld)"
            Assert-True 'T18 overlap is detected against the highest slot' ((Test-RuntimeOverlap -Lease $l -RecordDir $n.rec) -eq $true)
        } finally { Exit-RunLocks -Lease $l }
        $p.Proc.Kill(); [void]$p.Proc.WaitForExit(5000)
        $killed = @(Invoke-RuntimeSlotSweep -RecordDir $n.rec)
        Assert-True 'T18 the sweep kills the orphan runner of the highest slot' ($killed -contains $runner) "killed=$($killed -join ',') runner=$runner"
        Assert-True 'T18 the orphan runner is gone' (Test-ProcessGone $runner)
        Stop-All

        $n = New-Names
        $thrown = $null
        try { $lease = Enter-RunLocks -Repo 'C:\slots-fixture\Over' -Runtime -SlotCount ($max + 1) -TimeoutMs 500 -SlotNames $n.names -CheckoutLockName ($n.ckPrefix + 'Over') -RecordDir $n.rec }
        catch { $thrown = $_.Exception }
        Assert-True 'T18 a SlotCount above the name count throws a caller error' ($null -ne $thrown -and $thrown -isnot [System.Management.Automation.ParameterBindingException] -and $thrown.Message -match 'SlotCount') "thrown=$($thrown.GetType().Name): $($thrown.Message)"
        Assert-True 'T18 the caller error left the checkout lock free' (Test-MutexFree ($n.ckPrefix + 'Over'))
    }
    # ------------------------------------------------------------------ T19 (a parked C=1 run marks every slot it holds)
    Invoke-Case 'T19' {
        $n = New-Names
        $raw = Start-Holder -N $n -Mode raw -RawName $n.names[2]; [void](Wait-Ready $raw)
        $p = Start-Holder -N $n -Checkout P -SlotCount 1 -TimeoutMs 20000
        $recs = @{}
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline) {
            foreach ($k in 0, 1) { $recs[$k] = Read-SlotRecordFile -RecordDir $n.rec -Slot $k }
            if ($recs[0] -and $recs[1]) { break }
            Start-Sleep -Milliseconds 50
        }
        Assert-True 'T19 the parked C=1 run wrote a record for slot 0 and slot 1' ($recs[0] -and $recs[1])
        foreach ($k in 0, 1) {
            Assert-True "T19 held slot $k is waiting while the run parks on slot 2" ($recs[$k] -and $recs[$k].state -eq 'waiting') ($recs[$k] | ConvertTo-Json -Compress)
        }
        $probe = [pscustomobject]@{ SlotNames = @($n.names); RecordDir = $n.rec }
        Assert-True 'T19 the parked slots 0 and 1 do not count as busy' ((Test-OtherSlotBusy -Lease $probe -Slot 0) -eq $false -and (Test-OtherSlotBusy -Lease $probe -Slot 1) -eq $false)
    }
    # ------------------------------------------------------------------ T20..T26 (held checkout)
    function Get-CkRepo { param([string] $Checkout) "C:\slots-fixture\$Checkout" }
    function Write-CheckoutRecordFile {
        param($N, [string] $Checkout, $HolderPid, $Start, [bool] $Built, [bool] $Recompiled)
        $name = Get-CheckoutRecordName -Repo (Get-CkRepo $Checkout)
        $rec = [ordered]@{ holderPid = $HolderPid; procStart = $Start; checkout = (Get-CkRepo $Checkout); built = $Built; recompiled = $Recompiled }
        [System.IO.File]::WriteAllText((Join-Path $N.rec "$name.json"), ($rec | ConvertTo-Json))
        Join-Path $N.rec "$name.json"
    }
    function Get-DeadPid {
        $g = Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-Command', 'exit 0')) -PassThru @HiddenWindow
        $st = Get-ProofStamp $g.Id
        [void]$g.WaitForExit(10000)
        [pscustomobject]@{ Pid = $g.Id; Start = $st }
    }
    function Get-HeldDenyText {
        param($N, [string] $Checkout, [switch] $RecompilePass)
        try {
            $l = Enter-RunLocks -Repo (Get-CkRepo $Checkout) -Label $Checkout -Runtime -SlotCount 2 -TimeoutMs 2000 -SlotNames $N.names `
                -CheckoutLockName ($N.ckPrefix + $Checkout) -RecordDir $N.rec -HeldCheckout -RecompilePass:$RecompilePass
            Exit-RunLocks -Lease $l
            return 'NO THROW'
        } catch { return $_.Exception.Message }
    }
    function Get-Unmet { param([string] $Text) if ($Text -match 'unmet=(\S+)') { @($Matches[1] -split ',') } else { @() } }

    # T20: a held child under a live, built, recompiled holder acquires a slot only and never waits on the checkout lock.
    Invoke-Case 'T20' {
        $n = New-Names
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'H') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'H') -RecordDir $n.rec
        try {
            Assert-True 'T20 Enter-HeldCheckout acquires the checkout lock' ($hl.Acquired -eq $true)
            Set-CheckoutStamp -Repo (Get-CkRepo 'H') -Name built -RecordDir $n.rec
            Set-CheckoutStamp -Repo (Get-CkRepo 'H') -Name recompiled -RecordDir $n.rec
            $c = Start-Holder -N $n -Checkout H -Held -TimeoutMs 3000; $lc = Wait-Ready $c
            Assert-True 'T20 the held child acquires' ($lc.Acquired -eq $true -and -not $lc.Error) ($lc | ConvertTo-Json -Compress)
            Assert-True 'T20 the held child holds exactly one runtime slot and no checkout lock' ($lc.Acquired -eq $true -and $lc.HeldCount -eq 1 -and @($lc.Slots).Count -eq 1) ($lc | ConvertTo-Json -Compress)
            Assert-True 'T20 the held child did not wait on the holder''s checkout lock' ($lc.Acquired -eq $true -and [int64]$lc.WaitMs -lt 2500) ($lc | ConvertTo-Json -Compress)
            [void](Invoke-HolderRelease $c)
        } finally { Exit-HeldCheckout -Lease $hl }
        $recPath = Join-Path $n.rec ((Get-CheckoutRecordName -Repo (Get-CkRepo 'H')) + '.json')
        Assert-True 'T20 Exit-HeldCheckout removes the checkout record' (-not (Test-Path -LiteralPath $recPath))
        $raw = Start-Holder -N $n -Mode raw -RawName ($n.ckPrefix + 'H'); $lr = Wait-Ready $raw
        Assert-True 'T20 Exit-HeldCheckout releases the checkout lock' ($lr.Acquired -eq $true)
    }

    # T21: a live holder that is not an ancestor of the caller is refused; the deny names the holder and the caller's
    # chain. Set-CheckoutStamp and Exit-HeldCheckout from that non-holder throw.
    Invoke-Case 'T21' {
        $n = New-Names
        $o = Start-Holder -N $n -Mode heldowner -Checkout F -Stamps 'built,recompiled'; $lo = Wait-Ready $o
        Assert-True 'T21 setup: a foreign process holds the checkout, built and recompiled' ($lo.Acquired -eq $true) ($lo | ConvertTo-Json -Compress)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'F') -RecordDir $n.rec)
        Assert-True 'T21 the denial list names holder-not-ancestor only' (($d -join ',') -eq 'holder-not-ancestor') "denials=$($d -join ',')"
        $msg = Get-HeldDenyText -N $n -Checkout F
        Assert-True 'T21 Enter-RunLocks -HeldCheckout throws naming the holder' ($msg -match "holder=$($o.Proc.Id)\b") $msg
        Assert-True 'T21 the deny names the caller''s chain, starting at the caller' ($msg -match "chain=$PID(<\d+)+") $msg
        Assert-True 'T21 the deny names the unmet rule' ((Get-Unmet $msg) -contains 'holder-not-ancestor') $msg
        $thrown = $null
        try { Set-CheckoutStamp -Repo (Get-CkRepo 'F') -Name built -RecordDir $n.rec } catch { $thrown = $_.Exception.Message }
        Assert-True 'T21 Set-CheckoutStamp from a non-holder throws' ($null -ne $thrown) "thrown=$thrown"
        $fake = [pscustomobject]@{ Repo = (Get-CkRepo 'F'); RecordDir = $n.rec; Acquired = $true; Held = @(); Opened = @(); Records = @{} }
        $thrown = $null
        try { Exit-HeldCheckout -Lease $fake } catch { $thrown = $_.Exception.Message }
        Assert-True 'T21 Exit-HeldCheckout from a non-holder throws' ($null -ne $thrown) "thrown=$thrown"
        $recPath = Join-Path $n.rec ((Get-CheckoutRecordName -Repo (Get-CkRepo 'F')) + '.json')
        Assert-True 'T21 the refused release left the holder''s record in place' (Test-Path -LiteralPath $recPath)
        $rel = Invoke-HolderRelease $o
        Assert-True 'T21 the real holder releases cleanly' ($rel.Error -eq '') $rel.Error
    }

    # T22: missing record, dead holder, reused pid and built=false all refuse; recompiled=false refuses unless -RecompilePass.
    Invoke-Case 'T22' {
        $n = New-Names
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'M') -RecordDir $n.rec)
        Assert-True 'T22 a missing record is denied' ($d -contains 'no-checkout-record') "denials=$($d -join ',')"
        $msg = Get-HeldDenyText -N $n -Checkout M
        Assert-True 'T22 Enter-RunLocks -HeldCheckout throws on a missing record' ((Get-Unmet $msg) -contains 'no-checkout-record') $msg

        $dead = Get-DeadPid
        [void](Write-CheckoutRecordFile -N $n -Checkout D -HolderPid $dead.Pid -Start $dead.Start -Built $true -Recompiled $true)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'D') -RecordDir $n.rec)
        Assert-True 'T22 a dead holder is denied' ($d -contains 'holder-dead') "denials=$($d -join ',')"
        $msg = Get-HeldDenyText -N $n -Checkout D
        Assert-True 'T22 Enter-RunLocks -HeldCheckout throws on a dead holder' ((Get-Unmet $msg) -contains 'holder-dead') $msg

        [void](Write-CheckoutRecordFile -N $n -Checkout R -HolderPid $PID -Start '2001-01-01T00:00:00.0000000Z' -Built $true -Recompiled $true)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'R') -RecordDir $n.rec)
        Assert-True 'T22 a record whose pid was reused is rejected as a dead holder' ($d -contains 'holder-dead') "denials=$($d -join ',')"

        [void](Write-CheckoutRecordFile -N $n -Checkout B -HolderPid $PID -Start $ownerStart -Built $false -Recompiled $false)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'B') -RecordDir $n.rec)
        Assert-True 'T22 built=false is denied, and so is recompiled=false' (($d -join ',') -eq 'not-built,not-recompiled') "denials=$($d -join ',')"
        $msg = Get-HeldDenyText -N $n -Checkout B
        Assert-True 'T22 Enter-RunLocks -HeldCheckout throws on built=false' ((Get-Unmet $msg) -contains 'not-built') $msg

        [void](Write-CheckoutRecordFile -N $n -Checkout P -HolderPid $PID -Start $ownerStart -Built $true -Recompiled $false)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'P') -RecordDir $n.rec)
        Assert-True 'T22 recompiled=false without -RecompilePass is denied through the same list' (($d -join ',') -eq 'not-recompiled') "denials=$($d -join ',')"
        $msg = Get-HeldDenyText -N $n -Checkout P
        Assert-True 'T22 Enter-RunLocks -HeldCheckout without -RecompilePass throws' ((Get-Unmet $msg) -contains 'not-recompiled') $msg
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'P') -RecordDir $n.rec -RecompilePass)
        Assert-True 'T22 recompiled=false with -RecompilePass has no denial' ($d.Count -eq 0) "denials=$($d -join ',')"
        $l = Enter-RunLocks -Repo (Get-CkRepo 'P') -Label P -Runtime -SlotCount 2 -TimeoutMs 2000 -SlotNames $n.names `
            -CheckoutLockName ($n.ckPrefix + 'P') -RecordDir $n.rec -HeldCheckout -RecompilePass
        try {
            Assert-True 'T22 a -RecompilePass held child acquires one slot and no checkout lock' ($l.Acquired -and @($l.Slots).Count -eq 1 -and @($l.Held).Count -eq 1) "slots=$(@($l.Slots) -join ',') held=$(@($l.Held).Count)"
        } finally { Exit-RunLocks -Lease $l }
    }

    # T23: a leftover record from a dead holder with both stamps true is replaced by a fresh record with both false.
    Invoke-Case 'T23' {
        $n = New-Names
        $dead = Get-DeadPid
        $recPath = Write-CheckoutRecordFile -N $n -Checkout L -HolderPid $dead.Pid -Start $dead.Start -Built $true -Recompiled $true
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'L') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'L') -RecordDir $n.rec
        try {
            $r = Read-JsonFile $recPath
            Assert-True 'T23 the new holder owns the record' ($r -and [int]$r.holderPid -eq $PID) ($r | ConvertTo-Json -Compress)
            Assert-True 'T23 no successor inherits built or recompiled' ($r -and $r.built -eq $false -and $r.recompiled -eq $false) ($r | ConvertTo-Json -Compress)
            $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'L') -RecordDir $n.rec)
            Assert-True 'T23 the fresh record denies a held child until stamped' (($d -join ',') -eq 'not-built,not-recompiled') "denials=$($d -join ',')"
        } finally { Exit-HeldCheckout -Lease $hl }
    }

    # T24: for one planted record, the wrapper pre-check and Enter-RunLocks -HeldCheckout report the same deny list.
    Invoke-Case 'T24' {
        $n = New-Names
        [void](Write-CheckoutRecordFile -N $n -Checkout W -HolderPid $PID -Start $ownerStart -Built $false -Recompiled $true)
        $wrapperPath = Join-Path $scriptsDir 'run_test_suite.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($wrapperPath, [ref]$null, [ref]$null)
        $fn = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq 'Invoke-HeldPreCheck' }, $true))
        Assert-True 'T24 the wrapper defines Invoke-HeldPreCheck' ($fn.Count -eq 1)
        if ($fn.Count -ne 1) { return }
        $child = Join-Path $work 'precheck.ps1'
        $body = @"
. '$ModulePath'
`$script:RuntimeSlotRecordRoot = '$($n.rec)'
$($fn[0].Extent.Text)
Invoke-HeldPreCheck -Repo '$(Get-CkRepo 'W')' -Label 'W' -ResultsRoot 'r' -Held `$true -RecompilePass `$false
exit 0
"@
        [System.IO.File]::WriteAllText($child, $body, [System.Text.UTF8Encoding]::new($false))
        $out = (& pwsh -NoProfile -File $child 2>&1) -join "`n"
        $code = $LASTEXITCODE
        $wrapperUnmet = @(Get-Unmet $out)
        $locksUnmet = @(Get-Unmet (Get-HeldDenyText -N $n -Checkout W))
        Assert-True 'T24 the wrapper pre-check refuses with a non-zero exit and a STATUS=HELD_REFUSED line' ($code -ne 0 -and $out -match 'STATUS=HELD_REFUSED') "exit=$code out=$out"
        Assert-True 'T24 the wrapper pre-check deny list names not-built' ($wrapperUnmet -contains 'not-built') "out=$out"
        Assert-True 'T24 Enter-RunLocks -HeldCheckout names not-built' ($locksUnmet -contains 'not-built') "locks=$($locksUnmet -join ',')"
        Assert-True 'T24 the two deny lists are equal' (($wrapperUnmet -join ',') -eq ($locksUnmet -join ',') -and $wrapperUnmet.Count -gt 0) "wrapper=$($wrapperUnmet -join ',') locks=$($locksUnmet -join ',')"
    }

    # T25: a checkout record (no state field) reads through Read-SlotRecord; a slot record without state is unreadable.
    Invoke-Case 'T25' {
        $n = New-Names
        [System.IO.File]::WriteAllText((Join-Path $n.rec 'checkout-abc.json'), '{"holderPid":1,"procStart":null,"checkout":"x","built":false,"recompiled":false}')
        [System.IO.File]::WriteAllText((Join-Path $n.rec 'slot-3.json'), '{"holderPid":1}')
        $c = Read-SlotRecord -RecordDir $n.rec -Name 'checkout-abc'
        $s = Read-SlotRecord -RecordDir $n.rec -Name 'slot-3'
        Assert-True 'T25 a checkout record reads without unreadable' ($null -ne $c -and $c -isnot [string] -and [int]$c.holderPid -eq 1) "got=$c"
        Assert-True 'T25 a slot record missing state still reads unreadable' ($s -is [string] -and $s -eq 'unreadable') "got=$s"
        Write-SlotRecord -RecordDir $n.rec -Name 'slot-5' -Record ([ordered]@{ holderPid = 7; state = 'running' })
        $back = Read-SlotRecord -RecordDir $n.rec -Name 'slot-5'
        Assert-True 'T25 Write-SlotRecord and Read-SlotRecord round-trip by record name' ($null -ne $back -and $back -isnot [string] -and [int]$back.holderPid -eq 7) "got=$back"
    }

    # T26: the checkout record name is per checkout, so gates in two checkouts never share one record.
    Invoke-Case 'T26' {
        $a = Get-CheckoutRecordName -Repo 'C:\slots-fixture\A'
        $b = Get-CheckoutRecordName -Repo 'C:\slots-fixture\B'
        Assert-True 'T26 checkout record names are checkout-<worktreeId>' ($a -eq ('checkout-' + (Get-WorktreeId 'C:\slots-fixture\A')) -and $a -ne $b) "a=$a b=$b"
    }

    # T27: the overlap judgement beside Test-RuntimeOverlap. A held child that overlapped only its own gate's held
    # siblings is 'self': a plain RED, no trigger. An overlap with a foreign peer's slot is 'foreign' and logs its trigger.
    Invoke-Case 'T27' {
        Assert-True 'T27 RuntimeSlots.ps1 defines Register-OverlapSignature' ($null -ne (Get-Command Register-OverlapSignature -ErrorAction SilentlyContinue))
        if (-not (Get-Command Register-OverlapSignature -ErrorAction SilentlyContinue)) { return }
        $line = 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=2 filter=T27'
        function Enter-ProofHeld { param($N, [string] $Checkout)
            $h = Enter-HeldCheckout -Repo (Get-CkRepo $Checkout) -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($N.ckPrefix + $Checkout) -RecordDir $N.rec
            Set-CheckoutStamp -Repo (Get-CkRepo $Checkout) -Name built -RecordDir $N.rec
            Set-CheckoutStamp -Repo (Get-CkRepo $Checkout) -Name recompiled -RecordDir $N.rec
            $h }
        function Enter-ProofLease { param($N, [string] $Checkout, [switch] $Held)
            Enter-RunLocks -Repo (Get-CkRepo $Checkout) -Label 'me' -Runtime -SlotCount 3 -TimeoutMs 3000 -SlotNames $N.names `
                -CheckoutLockName ($N.ckPrefix + $Checkout) -RecordDir $N.rec -HeldCheckout:$Held }
        $trig = { param($N) $p = Join-Path $N.rec 'triggers.log'; if (Test-Path -LiteralPath $p) { @(Get-Content -LiteralPath $p) } else { @() } }

        # A live sibling held child of this gate.
        $n = New-Names
        $hl = Enter-ProofHeld $n 'S'
        try {
            $sib = Start-Holder -N $n -Checkout S -Held -SlotCount 3; $ls = Wait-Ready $sib
            $l = Enter-ProofLease $n 'S' -Held
            try {
                Assert-True 'T27 setup: this held lease acquired beside a live sibling held child' ($ls.Acquired -and $l.Acquired -and $l.PeerSlotHeld) "sib=$($ls | ConvertTo-Json -Compress)"
                $scope = Register-OverlapSignature -Lease $l -Repo (Get-CkRepo 'S') -Label 'me' -Line $line -RecordDir $n.rec
                Assert-True 'T27 an overlap with a live sibling held child is self' ($scope -eq 'self') "scope=$scope"
                Assert-True 'T27 a self overlap writes no triggers.log entry' (@(& $trig $n).Count -eq 0)
                Assert-True 'T27 Test-RuntimeOverlap still reports the overlap' (Test-RuntimeOverlap -Lease $l -RecordDir $n.rec)
            } finally { Exit-RunLocks -Lease $l }
        } finally { Stop-All; Exit-HeldCheckout -Lease $hl }

        # A sibling that took and released its slot inside this lease's window, and has exited.
        $n = New-Names
        $hl = Enter-ProofHeld $n 'S'
        try {
            $l = Enter-ProofLease $n 'S' -Held
            try {
                Start-Sleep -Milliseconds 50
                $sib = Start-Holder -N $n -Checkout S -Held -SlotCount 3; [void](Wait-Ready $sib)
                [void](Invoke-HolderRelease $sib); Stop-Holder $sib; [void]$sib.Proc.WaitForExit(5000)
                $scope = Register-OverlapSignature -Lease $l -Repo (Get-CkRepo 'S') -Label 'me' -Line $line -RecordDir $n.rec
                Assert-True 'T27 an overlap with an exited sibling from inside the held window is self' ($scope -eq 'self') "scope=$scope"
                Assert-True 'T27 no trigger for it' (@(& $trig $n).Count -eq 0)
            } finally { Exit-RunLocks -Lease $l }
        } finally { Stop-All; Exit-HeldCheckout -Lease $hl }

        # A foreign peer (another checkout's ordinary run) beside a held child, alone and with a sibling.
        foreach ($withSibling in $false, $true) {
            $n = New-Names
            $hl = Enter-ProofHeld $n 'S'
            try {
                $y = Start-Holder -N $n -Checkout Y -SlotCount 3; [void](Wait-Ready $y)
                if ($withSibling) { $sib = Start-Holder -N $n -Checkout S -Held -SlotCount 3; [void](Wait-Ready $sib) }
                $l = Enter-ProofLease $n 'S' -Held
                try {
                    $scope = Register-OverlapSignature -Lease $l -Repo (Get-CkRepo 'S') -Label 'me' -Line $line -RecordDir $n.rec
                    $what = if ($withSibling) { 'a foreign peer plus a sibling' } else { 'a foreign peer' }
                    Assert-True "T27 an overlap with $what is foreign" ($scope -eq 'foreign') "scope=$scope"
                    $t = @(& $trig $n)
                    Assert-True "T27 a foreign overlap ($what) logs one trigger naming the run" ($t.Count -eq 1 -and $t[0] -match ' label=me ' -and $t[0] -match 'filter=T27') ($t -join ' | ')
                } finally { Exit-RunLocks -Lease $l }
            } finally { Stop-All; Exit-HeldCheckout -Lease $hl }
        }

        # An ordinary (non-held) lease has no own pool: any overlap is foreign.
        $n = New-Names
        $y = Start-Holder -N $n -Checkout Y -SlotCount 3; [void](Wait-Ready $y)
        $l = Enter-ProofLease $n 'Z'
        try {
            Assert-True 'T27 an ordinary lease''s overlap is foreign' ((Get-RuntimeOverlapScope -Lease $l -RecordDir $n.rec) -eq 'foreign')
        } finally { Exit-RunLocks -Lease $l }
        $l = $null
        Stop-All
        $n = New-Names
        $l = Enter-ProofLease $n 'Z'
        try { Assert-True 'T27 a solo lease is none' ((Get-RuntimeOverlapScope -Lease $l -RecordDir $n.rec) -eq 'none') } finally { Exit-RunLocks -Lease $l }
    }

    # T28: a refused Exit-HeldCheckout still releases the caller's checkout lock; it leaves the record alone and throws.
    Invoke-Case 'T28' {
        $n = New-Names
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'E') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'E') -RecordDir $n.rec
        try {
            Assert-True 'T28 setup: the checkout is held' ($hl.Acquired -eq $true)
            $recPath = Join-Path $n.rec ((Get-CheckoutRecordName -Repo (Get-CkRepo 'E')) + '.json')
            Remove-Item -LiteralPath $recPath -Force
            $thrown = $null
            try { Exit-HeldCheckout -Lease $hl } catch { $thrown = $_.Exception.Message }
            Assert-True 'T28 Exit-HeldCheckout without the caller''s record throws' ($null -ne $thrown) "thrown=$thrown"
            $raw = Start-Holder -N $n -Mode raw -RawName ($n.ckPrefix + 'E'); $lr = Wait-Ready $raw
            Assert-True 'T28 the refused release still freed the checkout lock' ($lr.Acquired -eq $true) ($lr | ConvertTo-Json -Compress)
        } finally { Exit-RunLocks -Lease $hl }
    }

    # T29: a foreign holder busy at acquisition stays foreign after its slot passes to one of this gate's own siblings.
    Invoke-Case 'T29' {
        $n = New-Names
        $line = 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=3 filter=T29'
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'S') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'S') -RecordDir $n.rec
        try {
            Set-CheckoutStamp -Repo (Get-CkRepo 'S') -Name built -RecordDir $n.rec
            Set-CheckoutStamp -Repo (Get-CkRepo 'S') -Name recompiled -RecordDir $n.rec
            $y = Start-Holder -N $n -Checkout Y -SlotCount 3; $ly = Wait-Ready $y
            $l = Enter-RunLocks -Repo (Get-CkRepo 'S') -Label 'me' -Runtime -SlotCount 3 -TimeoutMs 3000 -SlotNames $n.names `
                -CheckoutLockName ($n.ckPrefix + 'S') -RecordDir $n.rec -HeldCheckout
            try {
                [void](Invoke-HolderRelease $y)
                $sib = Start-Holder -N $n -Checkout S -Held -SlotCount 3; $ls = Wait-Ready $sib
                Assert-True 'T29 setup: the sibling took the slot the foreign holder held at acquisition' (
                    $ly.Acquired -and $ls.Acquired -and @($ls.Slots)[0] -eq @($ly.Slots)[0] -and @($l.PeerSlots) -contains [int] @($ly.Slots)[0]) "y=$($ly | ConvertTo-Json -Compress) sib=$($ls | ConvertTo-Json -Compress) peers=$(@($l.PeerSlots) -join ',')"
                $scope = Register-OverlapSignature -Lease $l -Repo (Get-CkRepo 'S') -Label 'me' -Line $line -RecordDir $n.rec
                Assert-True 'T29 the overlap is foreign' ($scope -eq 'foreign') "scope=$scope"
            } finally { Exit-RunLocks -Lease $l }
        } finally { Stop-All; Exit-HeldCheckout -Lease $hl }
    }

    # T30: a process snapshot that lacks the caller (a transient CIM failure) is retaken, not read as holder-not-ancestor;
    # a snapshot that keeps failing is its own denial.
    Invoke-Case 'T30' {
        $n = New-Names
        $me = (Get-ProcSnapshot)[[int] $PID]
        $parent = Get-Process -Id ([int] $me.ParentProcessId)
        [void](Write-CheckoutRecordFile -N $n -Checkout N -HolderPid $parent.Id -Start (Get-ProofStamp $parent.Id) -Built $true -Recompiled $true)
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'N') -RecordDir $n.rec -Map @{})
        Assert-True 'T30 a snapshot missing the caller is retaken and the live ancestor holder admits' ($d.Count -eq 0) "denials=$($d -join ',')"
        function Get-ProcSnapshot { @{} }
        $d = @(Get-HeldCheckoutDenials -Repo (Get-CkRepo 'N') -RecordDir $n.rec -Map @{})
        Assert-True 'T30 a snapshot that keeps failing is denied as snapshot-failed' (($d -join ',') -eq 'snapshot-failed') "denials=$($d -join ',')"
    }

    # T31: a checkout stamp that cannot land throws instead of reporting success; no temp file is left behind.
    Invoke-Case 'T31' {
        if (-not $IsWindows) { Write-Host '  SKIP  T31 blocks the write with a Windows share lock, which a Linux rename ignores'; return }
        $n = New-Names
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'K') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'K') -RecordDir $n.rec
        try {
            $recPath = Join-Path $n.rec ((Get-CheckoutRecordName -Repo (Get-CkRepo 'K')) + '.json')
            $fs = [System.IO.File]::Open($recPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
            $thrown = $null
            try { Set-CheckoutStamp -Repo (Get-CkRepo 'K') -Name built -RecordDir $n.rec } catch { $thrown = $_.Exception.Message } finally { $fs.Dispose() }
            Assert-True 'T31 a stamp whose write fails throws' ($null -ne $thrown) "thrown=$thrown"
            $r = Read-JsonFile $recPath
            Assert-True 'T31 the record still reads built=false' ($r -and $r.built -eq $false) ($r | ConvertTo-Json -Compress)
            Assert-True 'T31 the failed write left no temp file' (@(Get-ChildItem -LiteralPath $n.rec -Filter '*.tmp').Count -eq 0)
            Set-CheckoutStamp -Repo (Get-CkRepo 'K') -Name built -RecordDir $n.rec
            Assert-True 'T31 an unblocked stamp lands' ((Read-JsonFile $recPath).built -eq $true)
        } finally { Exit-HeldCheckout -Lease $hl }
    }

    # T32: a held child re-checks its denials after the slot wait; a holder that left while it waited refuses it.
    Invoke-Case 'T32' {
        $n = New-Names
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'G') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'G') -RecordDir $n.rec
        $released = $false
        try {
            Set-CheckoutStamp -Repo (Get-CkRepo 'G') -Name built -RecordDir $n.rec
            Set-CheckoutStamp -Repo (Get-CkRepo 'G') -Name recompiled -RecordDir $n.rec
            $r0 = Start-Holder -N $n -Mode raw -RawName $n.s0; [void](Wait-Ready $r0)
            $r1 = Start-Holder -N $n -Mode raw -RawName $n.s1; [void](Wait-Ready $r1)
            $c = Start-Holder -N $n -Checkout G -Held -SlotCount 2 -TimeoutMs 30000
            Wait-Entering $c
            Start-Sleep -Milliseconds 1500
            Exit-HeldCheckout -Lease $hl; $released = $true
            [void](Invoke-HolderRelease $r0)
            $lc = Wait-Ready $c -Seconds 20
            Assert-True 'T32 the held child is refused after its slot wait' (-not $lc.Acquired -and $lc.Error -match 'no-checkout-record' -and $lc.Error -match 'slot wait') ($lc | ConvertTo-Json -Compress)
            Assert-True 'T32 the refused child released the slot it took' (Test-MutexFree $n.s0)
        } finally { Stop-All; if (-not $released) { Exit-HeldCheckout -Lease $hl } }
    }

    # T33: a record write retries while another process holds the record open; Windows reports that as access denied,
    # not as an IOException.
    Invoke-Case 'T33' {
        $n = New-Names
        Write-SlotRecord -RecordDir $n.rec -Name 'slot-0' -Record ([ordered]@{ holderPid = 1; state = 'running' }) -Required
        $recPath = Join-Path $n.rec 'slot-0.json'
        $ready = Join-Path $n.rec 'reader.ready'
        $cmd = "`$fs = [System.IO.File]::Open('$recPath', 'Open', 'Read', 'Read'); Set-Content -LiteralPath '$ready' -Value x; Start-Sleep -Milliseconds 150; `$fs.Dispose()"
        $p = Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-Command', $cmd)) -PassThru @HiddenWindow
        $script:Children.Add($p)
        $deadline = (Get-Date).AddSeconds(15)
        while (-not (Test-Path -LiteralPath $ready) -and (Get-Date) -lt $deadline -and -not $p.HasExited) { Start-Sleep -Milliseconds 10 }
        Assert-True 'T33 setup: a reader holds the record open' ((Test-Path -LiteralPath $ready) -and -not $p.HasExited)
        $thrown = $null
        try { Write-SlotRecord -RecordDir $n.rec -Name 'slot-0' -Record ([ordered]@{ holderPid = 2; state = 'running' }) -Required }
        catch { $thrown = $_.Exception.Message }
        Assert-True 'T33 a write blocked by a short-lived reader retries and lands' ($null -eq $thrown -and [int] (Read-JsonFile $recPath).holderPid -eq 2) "thrown=$thrown"
    }

    # T34: a slot busy at acquisition whose holder left no readable record stays foreign after a sibling takes it.
    Invoke-Case 'T34' {
        $n = New-Names
        $line = 'OVERLAP_SIGNATURE=run-aborted attempt=1 exit=1 slots=2 filter=T34'
        $hl = Enter-HeldCheckout -Repo (Get-CkRepo 'U') -Label 'gate' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'U') -RecordDir $n.rec
        try {
            Set-CheckoutStamp -Repo (Get-CkRepo 'U') -Name built -RecordDir $n.rec
            Set-CheckoutStamp -Repo (Get-CkRepo 'U') -Name recompiled -RecordDir $n.rec
            $raw = Start-Holder -N $n -Mode raw -RawName $n.s1; [void](Wait-Ready $raw)
            $l = Enter-RunLocks -Repo (Get-CkRepo 'U') -Label 'me' -Runtime -SlotCount 2 -TimeoutMs 3000 -SlotNames $n.names `
                -CheckoutLockName ($n.ckPrefix + 'U') -RecordDir $n.rec -HeldCheckout
            try {
                [void](Invoke-HolderRelease $raw)
                $sib = Start-Holder -N $n -Checkout U -Held -SlotCount 2; $ls = Wait-Ready $sib
                Assert-True 'T34 setup: the sibling took the slot a recordless holder held at acquisition' (
                    $l.Acquired -and $ls.Acquired -and @($l.Slots)[0] -eq 0 -and @($ls.Slots)[0] -eq 1 -and @($l.PeerSlots) -contains 1) "me=$(@($l.Slots) -join ',') sib=$($ls | ConvertTo-Json -Compress) peers=$(@($l.PeerSlots) -join ',')"
                $scope = Register-OverlapSignature -Lease $l -Repo (Get-CkRepo 'U') -Label 'me' -Line $line -RecordDir $n.rec
                Assert-True 'T34 the overlap is foreign' ($scope -eq 'foreign') "scope=$scope"
            } finally { Exit-RunLocks -Lease $l }
        } finally { Stop-All; Exit-HeldCheckout -Lease $hl }
    }
    # T35: the checkout lock records its holder, so a waiter can name who holds it. Every lock taker writes the record
    # and removes only the one it wrote; a record whose process died reads as absent.
    Invoke-Case 'T35' {
        $n = New-Names
        $repo = Get-CkRepo 'H'
        $savedSid = @{ a = $env:CLAUDE_SESSION_ID; b = $env:CLAUDE_CODE_SESSION_ID }
        $env:CLAUDE_SESSION_ID = $null; $env:CLAUDE_CODE_SESSION_ID = 'sess-t35'
        try {
            $recPath = Join-Path $n.rec ((Get-CheckoutHolderName -Repo $repo) + '.json')
            Assert-True 'T35 no holder reads as null before anyone takes the lock' ($null -eq (Get-CheckoutHolder -Repo $repo -RecordDir $n.rec))
            $h = Start-Holder -N $n -Checkout H -Logic -SlotCount 1
            $lh = Wait-Ready $h
            $rec = Get-CheckoutHolder -Repo $repo -RecordDir $n.rec
            Assert-True 'T35 a held lock names its holder pid, label, session and time' (
                $rec -and [int] $rec.holderPid -eq [int] $lh.Pid -and $rec.label -eq 'H' -and $rec.sessionId -eq 'sess-t35' -and $rec.acquiredAt -and $rec.checkout -eq $repo) ($rec | ConvertTo-Json -Compress)
            [void](Invoke-HolderRelease $h)
            Assert-True 'T35 the record is gone after Exit-RunLocks' (-not (Test-Path -LiteralPath $recPath))
            Stop-Holder $h

            $outer = Enter-RunLocks -Repo $repo -Label 'outer' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'H') -RecordDir $n.rec
            try {
                $before = [System.IO.File]::ReadAllText($recPath)
                $inner = Enter-RunLocks -Repo $repo -Label 'inner' -TimeoutMs 3000 -CheckoutLockName ($n.ckPrefix + 'H') -RecordDir $n.rec
                Assert-True 'T35 re-entry on the same thread leaves the record unchanged' ($inner.Acquired -and [System.IO.File]::ReadAllText($recPath) -eq $before)
                Exit-RunLocks -Lease $inner
                Assert-True 'T35 the re-entrant release does not remove the outer holder record' ((Test-Path -LiteralPath $recPath) -and (Get-CheckoutHolder -Repo $repo -RecordDir $n.rec).label -eq 'outer')
            } finally { Exit-RunLocks -Lease $outer }
            Assert-True 'T35 the outer release removes it' (-not (Test-Path -LiteralPath $recPath))

            $d = Start-Holder -N $n -Checkout H -Logic -SlotCount 1
            [void](Wait-Ready $d)
            $d.Proc.Kill(); [void] $d.Proc.WaitForExit(5000)
            Assert-True 'T35 a killed holder leaves its record file behind' (Test-Path -LiteralPath $recPath)
            Assert-True 'T35 a dead holder reads as null' ($null -eq (Get-CheckoutHolder -Repo $repo -RecordDir $n.rec))
        } finally { $env:CLAUDE_SESSION_ID = $savedSid.a; $env:CLAUDE_CODE_SESSION_ID = $savedSid.b }
    }
    # T36: the .NET global tool runs pwsh as `dotnet pwsh.dll` behind a shim, so `pwsh` by name returns the shim's Id.
    # From that host, Get-PwshStart launches the pwsh that runs the command.
    Invoke-Case 'T36' {
        $out = Join-Path $work 't36.txt'
        $hw = if ($IsWindows) { '-WindowStyle Hidden' } else { '' }
        $cmd = ". '$(Join-Path $scriptsDir 'GodotProcess.ps1')'; `$s = Get-PwshStart; `$o = '$out.child'; " +
               "`$p = Start-Process `$s.FilePath -ArgumentList (@(`$s.Lead) + @('-NoProfile', '-Command', '`$PID')) -PassThru $hw -RedirectStandardOutput `$o; `$p.WaitForExit(); " +
               "Set-Content -LiteralPath '$out' -Value ('{0} {1} {2}' -f [System.IO.Path]::GetFileNameWithoutExtension([Environment]::ProcessPath), `$p.Id, (Get-Content -LiteralPath `$o -Raw))"
        & dotnet ([System.Reflection.Assembly]::GetEntryAssembly().Location) -NoProfile -Command $cmd
        $host_, $launched, $child = if (Test-Path -LiteralPath $out) { @((Get-Content -LiteralPath $out -Raw).Trim() -split '\s+') } else { @('', '', '') }
        Assert-True 'T36 setup: the probe runs in a dotnet-hosted pwsh' ($host_ -eq 'dotnet') "host=$host_"
        Assert-True 'T36 the launched Id is the pwsh that runs the command' ($launched -and $launched -eq $child) "launched=$launched child=$child"
        Assert-True 'T36 Lead is an array on any host, so a splat adds no null argument' ($null -ne $Pwsh.Lead -and @($Pwsh.Lead | Where-Object { $null -eq $_ }).Count -eq 0) "lead=$($Pwsh.Lead | ConvertTo-Json -Compress)"
    }
    # T37: the Linux file-lock type compiles once per process, seconds on a loaded host. That compile is not a wait, so a
    # fresh process's uncontended WaitMs matches a pre-warmed one's. Each child stubs the host as Linux to take that path.
    Invoke-Case 'T37' {
        $probe = Join-Path $work 't37.ps1'
        [System.IO.File]::WriteAllText($probe, @'
param([string] $Module, [switch] $Warm)
. $Module
$tmp = [System.IO.Path]::GetTempPath()
function Get-HostPlatform { 'linux' }
function Get-HarnessTempRoot { param([string] $Platform) $tmp }
$compileMs = -1
if ($Warm) { $c = [System.Diagnostics.Stopwatch]::StartNew(); Import-HarnessFileLock; $compileMs = $c.ElapsedMilliseconds }
$id = [guid]::NewGuid().ToString('N').Substring(0, 8)
$rec = Join-Path $tmp "t37-$id"; New-Item -ItemType Directory -Force -Path $rec | Out-Null
$l = Enter-RunLocks -Repo "C:\slots-fixture\T37-$id" -Label T37 -Runtime -SlotCount 2 -TimeoutMs 10000 -SlotNames @("t37-s0-$id", "t37-s1-$id") -CheckoutLockName "t37-ck-$id" -RecordDir $rec
"$([int][bool]$l.Acquired) $($l.WaitMs) $compileMs"
Exit-RunLocks -Lease $l
Remove-Item -LiteralPath $rec -Recurse -Force -ErrorAction SilentlyContinue
'@, [System.Text.UTF8Encoding]::new($false))
        $cold = @((& $Pwsh.FilePath @($Pwsh.Lead | ForEach-Object { $_.Trim('"') }) -NoProfile -File $probe -Module $ModulePath | Select-Object -Last 1) -split ' ')
        $warm = @((& $Pwsh.FilePath @($Pwsh.Lead | ForEach-Object { $_.Trim('"') }) -NoProfile -File $probe -Module $ModulePath -Warm | Select-Object -Last 1) -split ' ')
        Assert-True 'T37 setup: both probes acquire and the warm one timed a compile' ($cold[0] -eq '1' -and $warm[0] -eq '1' -and [int]$warm[2] -gt 0) "cold=$($cold -join ' ') warm=$($warm -join ' ')"
        Assert-True 'T37 a fresh process''s uncontended WaitMs holds no type compile' (([int64]$cold[1] - [int64]$warm[1]) -lt ([int64]$warm[2] / 2)) "coldWaitMs=$($cold[1]) warmWaitMs=$($warm[1]) compileMs=$($warm[2])"
    }
} finally {
    Stop-All
    Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Fails -gt 0) { Write-Host "FAILED ($script:Fails)"; exit 1 }
Write-Host 'ALL PASS'
exit 0
