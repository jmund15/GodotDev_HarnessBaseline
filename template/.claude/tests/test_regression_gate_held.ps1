#requires -Version 7
<#
.SYNOPSIS
  Proof for the gate's held pool (regression_gate.ps1, plan gate-speed-harness A7) and the shared peer reader it
  excludes its own children through (PeerActivity.ps1).

.HOW
  Part D decides the gate's row rules in process, on the shipped functions and planted facts: the Logic partition
  rows, the merged-Logic tiers, the Integration plan's quarantine and Render rows, the retry selection, the
  locked-suite verdict, and .runsettings generation. These are verdict tables, so no gate run buys them anything.
  Part U drives single functions against planted inputs: Test-OwnFamily on a planted process table (the production
  chain depth, an exited intermediate parent), the gate's own peer functions against live registry records whose
  writers are real processes, Get-CheckoutTestRunners on a planted table, the gate's run-dir reap (R3) on a planted
  table, the owner-root resolution in a child whose own chain reaches no session, and the project's gate_guards table
  through the real Invoke-StaticGuards.
  Part G runs a copy of the real gate in a fixture checkout with every external stubbed: git, dotnet and the Godot
  binary are in-process fixture scripts, the suite wrapper is a stub that runs the real held-mode admission
  (Get-HeldCheckoutDenials) and prints canned wrapper output per label, and RuntimeSlots.ps1 is patched to fixture
  mutex names and a fixture record dir. The fixture reads its live caller ancestry, not the machine's process table:
  no editor or test runner exists in the fixture. Every Part G case is a boundary the held gate really crosses: the checkout
  lock handshake (wait, acquire, abandon, release), a held child's admission and status, the detached launcher's
  owner root, the activity record, and the baseline's git reads. G20 also pauses before the checkout take to force
  the late-open schedule. A case that only differs from another in its planted data is decided in Part D instead.
  No real test run, build or Godot starts; nothing outside the fixture root is written.

.RUN
  pwsh -NoProfile -File .claude/tests/test_regression_gate_held.ps1
  pwsh -NoProfile -File .claude/tests/test_regression_gate_held.ps1 -Case U,G1
#>
[CmdletBinding()]
param([string] $Case = '', [ValidateRange(1, 8)] [int] $Shards = 0)
# The land runner caps in-proof width with HARNESS_CASE_JOBS (tests/_case_pool.py owns the rule: default 4).
if ($Shards -eq 0) {
    $Shards = 4
    if ($env:HARNESS_CASE_JOBS -match '^\d+$' -and [int]$env:HARNESS_CASE_JOBS -gt 0) { $Shards = [Math]::Min(8, [int]$env:HARNESS_CASE_JOBS) }
}

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Start-Process -WindowStyle exists on Windows only; a Linux child has no window to hide.
$HiddenWindow = if ($IsWindows) { @{ WindowStyle = 'Hidden' } } else { @{} }
$HiddenWindowArg = if ($IsWindows) { '-WindowStyle Hidden' } else { '' }

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$scriptsDir = Join-Path $repoRoot '.claude\scripts'
# The fixture csproj carries the project's runner pin so the fixture gate passes its pin floor; a project
# with no fork pin gets the stock version, which no pin bars.
$gatePin = & { . (Join-Path $scriptsDir 'GodotProcess.ps1'); if (Get-Command Get-GdUnitForkMarker -ErrorAction SilentlyContinue) { "5.1.0-rc4$(Get-GdUnitForkMarker).2" } else { '5.1.0' } }
$gatePath = Join-Path $scriptsDir 'regression_gate.ps1'
# Linux StartTime differs between two readers of one process, so every recorded identity goes through the product's stamp.
# Planted process tables carry the host's own path shape: the product compares them as paths.
$fixtureRoot = if ($IsWindows) { 'C:\held-fixture' } else { '/tmp/held-fixture' }
$fixtureFwd = $fixtureRoot -replace '\\', '/'
$sep = [System.IO.Path]::DirectorySeparatorChar
function Join-Native { param([string[]] $Parts) $Parts -join $sep }
function Get-ProofStamp { param([int] $ProcessId) . (Join-Path $scriptsDir 'GodotProcess.ps1'); (Get-ProcessStartStamp -ProcessId $ProcessId).ToString('o') }
# A child scope: GodotProcess.ps1 also defines Get-LiveEditor and Stop-ProcessTree, which this proof plants its own of.
$Pwsh = & { . (Join-Path $scriptsDir 'GodotProcess.ps1'); Get-PwshStart }
$PwshLeadArg = -join @($Pwsh.Lead | ForEach-Object { "'$_'," })
$env:HARNESS_WIDE_RUN_ADMITTED = '1'  # fixture gate runs start no test; a delegate running this proof must not meet WideRunGuard.ps1
$env:HARNESS_SESSION_LOCKED = '0'  # fixture gates must not depend on the desktop lock state (GodotProcess.ps1 Test-InteractiveSessionLocked)
$env:HARNESS_SLOT_BUDGET = '1'  # a fixture gate starts no engine work: it keeps the one machine lane this proof (or its runner) holds, never brokers a second
$allIds = @([regex]::Matches((Get-Content -Raw -LiteralPath $PSCommandPath), "Invoke-Case '([UGD]\d+)'") | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
$selected = @(if ($Case) { $Case -split '[,\s]+' | Where-Object { $_ } } else { $allIds })
$knownCases = @($allIds) + @('U', 'G', 'D')
$unknownCases = @($selected | Where-Object { $_ -notin $knownCases })
if ($unknownCases.Count -gt 0) {
    Write-Host "FAIL unknown -Case selector(s): $($unknownCases -join ', '). Use U, G or D, or one of: $($allIds -join ', ')."
    exit 2
}
if (-not $Case -and $Shards -gt 1) {
    # Cases share nothing (each shard builds its own fixture root and mutex scope), so shards run side by side.
    # Seconds a case takes alone (rounded, measured one shard at a time); the rest weigh 1. Deals the heavy cases out first.
    $cost = @{ G11 = 18; G31 = 18; G30 = 17; G21 = 17; G1 = 16; G8 = 15; G7 = 13; G20 = 13; G27 = 12; G2 = 12; G10 = 10; G4 = 10; G6 = 9; G5 = 9; G26 = 9; G25 = 7; G18 = 7; G29 = 6; U8 = 6; G3 = 6; G17 = 5; G23 = 4; U5 = 3; U2 = 2 }
    $ids = $selected
    $load = @(0) * $Shards
    $lists = @(for ($k = 0; $k -lt $Shards; $k++) { , [System.Collections.Generic.List[string]]::new() })
    foreach ($id in ($ids | Sort-Object { -[int]($cost[$_] ?? 1) }, { $_ })) {
        $i = 0; for ($k = 1; $k -lt $Shards; $k++) { if ($load[$k] -lt $load[$i]) { $i = $k } }
        $lists[$i].Add($id); $load[$i] += [int]($cost[$id] ?? 1)
    }
    $outDir = Join-Path ([System.IO.Path]::GetTempPath()) "gate-held-shards-$PID-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $procs = @(0..($Shards - 1) | ForEach-Object {
        Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-File', $PSCommandPath, '-Case', (@($lists[$_]) -join ','))) -PassThru @HiddenWindow `
            -RedirectStandardOutput (Join-Path $outDir "out$_.txt") -RedirectStandardError (Join-Path $outDir "err$_.txt")
    })
    # A failing shard prints last: a runner that keeps only an output's tail still shows its failed cases.
    $bad = 0
    $failedText = [System.Collections.Generic.List[string]]::new()
    for ($i = 0; $i -lt $Shards; $i++) {
        $procs[$i].WaitForExit()
        $lines = @(Get-Content (Join-Path $outDir "out$i.txt"))
        $errLines = @(Get-Content (Join-Path $outDir "err$i.txt") -ErrorAction SilentlyContinue)
        if ('ALL PASS' -in $lines) {
            $lines | Where-Object { $_ -ne 'ALL PASS' } | Write-Host
            $errLines | Write-Host
            continue
        }
        $bad++
        $failedText.AddRange([string[]](@($lines | Where-Object { $_ -match '^\s+FAIL |^\s{8}\S' }) + $errLines))
    }
    $failedText | Write-Host
    Remove-Item -Path $outDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host ''
    if ($bad) { Write-Host "FAILED ($bad shard(s))"; exit 1 }
    Write-Host 'ALL PASS'
    exit 0
}
$work = Join-Path ([System.IO.Path]::GetTempPath()) "gate-held-$PID-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$script:Fails = 0
$script:PartGSkipped = $false
$script:Owned = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Assert-True {
    param([string] $Name, $Condition, [string] $Detail = '')
    if ($Condition) { Write-Host "  PASS  $Name" }
    else { Write-Host "  FAIL  $Name$(if ($Detail) { "`n        $Detail" })"; $script:Fails++ }
}

function Invoke-Case {
    param([string] $Id, [scriptblock] $Body)
    if ($selected.Count -gt 0 -and $Id -notin $selected -and ($Id -replace '\d+$', '') -notin $selected) { return }
    if (-not $IsWindows -and $Id -match '^G\d+$') {
        if (-not $script:PartGSkipped) { Write-Host 'PLATFORM-SKIP-CASE Part G: fixture ancestry uses the Windows Get-Process parent chain'; $script:PartGSkipped = $true }
        return
    }
    Write-Host "`n$Id"
    try { & $Body } catch { Write-Host "  FAIL  $Id threw: $($_.Exception.Message)`n        $($_.ScriptStackTrace)"; $script:Fails++ }
}

function Start-Owned {
    param([string] $Command, [string] $Name)
    $p = Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-Command', $Command)) -PassThru @HiddenWindow `
        -RedirectStandardOutput (Join-Path $work "$Name.out") -RedirectStandardError (Join-Path $work "$Name.err")
    $script:Owned.Add($p)
    $p
}

function Get-FunctionText {
    param([string] $Path, [string[]] $Names)
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$null)
    $out = foreach ($n in $Names) {
        $f = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq $n }.GetNewClosure(), $true))
        if ($f.Count -ne 1) { throw "$([System.IO.Path]::GetFileName($Path)) defines $($f.Count) function(s) named $n" }
        $f[0].Extent.Text
    }
    $out -join "`n"
}

function P {
    param([int] $Id, [int] $ParentId, [string] $Name, [string] $Cmd = '', [string] $Exe = '', [int] $AgeMin = 60)
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $ParentId; Name = $Name; CommandLine = $Cmd; ExecutablePath = $Exe
                       CreationDate = [datetime]::Now.AddMinutes(-$AgeMin) }
}

function New-Map { param([object[]] $Procs) $m = @{}; foreach ($p in $Procs) { $m[[int]$p.ProcessId] = $p }; $m }

function Write-Record {
    param([string] $Dir, [string] $Kind, [System.Diagnostics.Process] $Proc, [string] $Checkout, [string] $Session = $null, [string] $Tag = '')
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $path = Join-Path $Dir "$Kind-$($Proc.Id)$Tag.json"
    [ordered]@{ pid = $Proc.Id; procStart = (Get-ProofStamp $Proc.Id); kind = $Kind; checkout = $Checkout
                sessionId = $Session; label = "planted $Kind"; startedAt = (Get-Date).ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $path -Encoding utf8
    $path
}

try {
    . (Join-Path $scriptsDir 'RuntimeSlots.ps1')
    $peerP = Join-Path $scriptsDir 'PeerActivity.ps1'
    if (Test-Path $peerP) { . $peerP }
    # The batch model owns the plan, retry, quarantine and Render-row rules the gate calls; Part D drives it directly.
    . (Join-Path $scriptsDir 'IntegrationBatches.ps1')

    # ================================================================== Part U: planted single-function cases

    # U1: the ancestry predicate. Production chain: Godot -> testhost -> vstest.console -> dotnet test -> wrapper -> gate.
    Invoke-Case 'U1' {
        Assert-True 'U1 PeerActivity.ps1 defines Test-OwnFamily' ($null -ne (Get-Command Test-OwnFamily -ErrorAction SilentlyContinue))
        if (-not (Get-Command Test-OwnFamily -ErrorAction SilentlyContinue)) { return }
        $gate = 900100
        $map = New-Map @((P $gate 900001 'pwsh.exe' 'pwsh -File regression_gate.ps1' -AgeMin 70),
                         (P 900101 $gate 'pwsh.exe' 'pwsh -File run_test_suite.ps1 -HeldCheckout' -AgeMin 60),
                         (P 900102 900101 'dotnet.exe' 'dotnet test --settings .runsettings' -AgeMin 50),
                         (P 900103 900102 'vstest.console.exe' '' -AgeMin 40),
                         (P 900104 900103 'testhost.exe' '' -AgeMin 30),
                         (P 900105 900104 'Godot_v4.7.1-stable_mono_win64.exe' 'godot --headless --pipe-name gdunit4-x' -AgeMin 20),
                         (P 900205 900299 'Godot_v4.7.1-stable_mono_win64.exe' 'godot --headless --pipe-name gdunit4-y' -AgeMin 20),
                         (P 900305 900199 'Godot_v4.7.1-stable_mono_win64.exe' 'godot --headless --pipe-name gdunit4-z' -AgeMin 20))
        Assert-True 'U1 a test Godot five hops below the gate is own family (production chain depth)' (Test-OwnFamily -ProcessId 900105 -OwnRoots @($gate) -Map $map)
        Assert-True 'U1 the gate itself is own family' (Test-OwnFamily -ProcessId $gate -OwnRoots @($gate) -Map $map)
        Assert-True 'U1 a Godot under another tree is not own family' (-not (Test-OwnFamily -ProcessId 900205 -OwnRoots @($gate) -Map $map))
        # 900199 (its testhost) has exited, so its chain ends there and cannot reach the gate. Expected verdict: NOT own
        # family (a peer wherever a consumer counts it); the gate's run-dir reap (R3) is what removes such a Godot.
        Assert-True 'U1 an own Godot whose intermediate parent exited is not provably own family (expected: peer)' (-not (Test-OwnFamily -ProcessId 900305 -OwnRoots @($gate) -Map $map))
        Assert-True 'U1 any of several own roots counts' (Test-OwnFamily -ProcessId 900105 -OwnRoots @(1, 2, 900102) -Map $map)
        $deep = @((P 910000 1 'pwsh.exe' 'gate' -AgeMin 200))
        for ($i = 1; $i -le 13; $i++) { $deep += P (910000 + $i) (910000 + $i - 1) 'x.exe' '' -AgeMin (100 - $i) }
        $dm = New-Map $deep
        Assert-True 'U1 the walk is bounded at 12 hops (13 hops down is not own family)' ((Test-OwnFamily -ProcessId 910012 -OwnRoots @(910000) -Map $dm) -and -not (Test-OwnFamily -ProcessId 910013 -OwnRoots @(910000) -Map $dm))
        $src = Get-Content -LiteralPath $peerP -Raw
        Assert-True 'U1 Test-OwnFamily walks through Test-ChainReachesPid with an explicit -MaxHops' ($src -match 'Test-ChainReachesPid[^\n]*-MaxHops \$MaxHops')
    }

    # U2: the gate's peer functions over live records. The asking gate is this proof process ($PID).
    Invoke-Case 'U2' {
        if (-not (Get-Command Get-LivePeerActivity -ErrorAction SilentlyContinue)) { Assert-True 'U2 PeerActivity.ps1 defines Get-LivePeerActivity' $false; return }
        $actDir = Join-Path $work 'activity-u2'
        $repo = Join-Path $work 'repo-u2'
        $script:PeersSeen = $false
        $script:PeersSeenList = @()
        $activityDir = $actDir
        . ([scriptblock]::Create((Get-FunctionText $gatePath @('Get-Prop', 'Get-SuitePeers'))))
        $own = Start-Owned 'Start-Sleep -Seconds 120' 'u2-own'
        $parent = Get-Process -Id ((Get-ProcSnapshot)[[int] $PID].ParentProcessId)
        $ownRec = Write-Record -Dir $actDir -Kind 'suite' -Proc $own -Checkout $repo
        $ownGate = Write-Record -Dir $actDir -Kind 'gate' -Proc $own -Checkout $repo
        $peers = @(Get-SuitePeers)
        Assert-True 'U2 a live suite record descended from the asking gate is not a peer' (@($peers | Where-Object { [int]$_.pid -eq $own.Id }).Count -eq 0) ($peers | ConvertTo-Json -Compress)
        Assert-True 'U2 own-only records leave PeersSeen false' (-not $script:PeersSeen)
        Remove-Item $ownRec, $ownGate -Force
        $env:CLAUDE_SESSION_ID = 'sess-u2'
        try {
            $foreign = Write-Record -Dir $actDir -Kind 'gate' -Proc $parent -Checkout $repo -Session 'sess-u2'
            Assert-True 'U2 a non-descended record sets PeersSeen and joins the peer list (session rule retired)' ((@(Get-SuitePeers).Count -eq 1) -and $script:PeersSeen -and
                @($script:PeersSeenList).Count -eq 1 -and [int]@($script:PeersSeenList)[0].pid -eq $parent.Id) (@($script:PeersSeenList) | ConvertTo-Json -Compress)
            Remove-Item $foreign -Force
            $wt = Write-Record -Dir $actDir -Kind 'gate' -Proc $parent -Checkout (Join-Path $repo '.claude\worktrees\x')
            $all = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $actDir)
            Assert-True 'U2 a record from a .claude/worktrees/<x> checkout is still a peer' ($all.Count -eq 1 -and [string]$all[0].checkout -like "*${sep}.claude${sep}worktrees${sep}x") ($all | ConvertTo-Json -Compress)
            Remove-Item $wt -Force
        } finally { Remove-Item Env:\CLAUDE_SESSION_ID -ErrorAction SilentlyContinue }
        $dead = Start-Owned 'exit 0' 'u2-dead'; [void]$dead.WaitForExit(10000)
        $deadRec = Join-Path $actDir "suite-$($dead.Id).json"
        [ordered]@{ pid = $dead.Id; procStart = '2001-01-01T00:00:00Z'; kind = 'suite'; checkout = $repo } | ConvertTo-Json | Set-Content -LiteralPath $deadRec
        $null = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $actDir)
        Assert-True 'U2 a dead record is swept by the shared reader' (-not (Test-Path -LiteralPath $deadRec))
        $gateSrc = Get-Content -LiteralPath $gatePath -Raw
        Assert-True 'U2 the gate defines no reader, owner walk or runner scan of its own' ($gateSrc -notmatch '(?m)^function (Get-LivePeerActivity|Get-OwnerRoot|Get-CheckoutTestRunners)\b')
        $gateAst = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
        $reads = @($gateAst.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] -and $x.GetCommandName() -in 'Get-LivePeerActivity', 'Get-CheckoutTestRunners' }, $true))
        Assert-True 'U2 every gate registry read and runner scan excludes by the gate''s own pid' ($reads.Count -gt 0 -and
            @($reads | Where-Object { $_.Extent.Text -notmatch '-OwnRoots @\(\$PID\)' }).Count -eq 0) (($reads | ForEach-Object { $_.Extent.Text }) -join ' | ')
    }

    # U3: the shared Get-CheckoutTestRunners excludes a runner descended from an own root, on a planted table.
    Invoke-Case 'U3' {
        if (-not (Get-Command Get-CheckoutTestRunners -ErrorAction SilentlyContinue)) { Assert-True 'U3 PeerActivity.ps1 defines Get-CheckoutTestRunners' $false; return }
        $repo = Join-Native $fixtureRoot, 'repo'
        $map = New-Map @((P 920000 1 'pwsh.exe' 'pwsh -File regression_gate.ps1' -AgeMin 70),
                         (P 920001 920000 'testhost.exe' '' (Join-Native $repo, '.godot', 'mono', 'temp', 'bin', 'Debug', 'testhost.exe') -AgeMin 30),
                         (P 920002 920001 'Godot_v4.exe' 'godot --headless --path . --pipe-name gdunit4-a' -AgeMin 20),
                         (P 920101 920100 'testhost.exe' '' (Join-Native $repo, '.godot', 'mono', 'temp', 'bin', 'Debug', 'testhost.exe') -AgeMin 30),
                         (P 920100 1 'pwsh.exe' 'pwsh -File run_test_suite.ps1' -AgeMin 70),
                         (P 920102 920101 'Godot_v4.exe' 'godot --headless --path . --pipe-name gdunit4-b' -AgeMin 20))
        $r = @(Get-CheckoutTestRunners -Repo $repo -Map $map -OwnRoots @(920000))
        Assert-True 'U3 a runner descended from the asking gate is not counted; a foreign runner on the checkout is' ((($r | ForEach-Object { [int]$_.ProcessId }) -join ',') -eq '920102') "got=$(($r | ForEach-Object ProcessId) -join ',')"
    }

    # U11: a runner whose testhost sits under ANOTHER checkout is that checkout's, even when this checkout's .godot/
    # was written after the runner started (the editor heuristic must not claim a runner its parent chain places).
    Invoke-Case 'U11' {
        $repo = Join-Path ([System.IO.Path]::GetTempPath()) ("held-u3b-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Path (Join-Path $repo '.godot') -Force | Out-Null
        try {
            $other = Join-Native $fixtureRoot, 'other-checkout'
            $map = New-Map @((P 921099 1 'node.exe' 'node claude' -AgeMin 90),
                             (P 921100 921099 'pwsh.exe' 'pwsh -File run_test_suite.ps1' -AgeMin 70),
                             (P 921101 921100 'testhost.exe' '' (Join-Native $other, '.godot', 'mono', 'temp', 'bin', 'Debug', 'testhost.exe') -AgeMin 30),
                             (P 921102 921101 'Godot_v4.exe' 'godot --headless --path . --pipe-name gdunit4-c' -AgeMin 20))
            $r = @(Get-CheckoutTestRunners -Repo $repo -Map $map -OwnRoots @())
            Assert-True 'U11 a runner placed under another checkout by its parent chain is not counted here' ($r.Count -eq 0) "got=$(($r | ForEach-Object ProcessId) -join ',')"
        } finally { Remove-Item -LiteralPath $repo -Recurse -Force -ErrorAction SilentlyContinue }
    }

    # U13: the gate's one wait holds nothing. Entering it while a lease or the twin's lock is held is a harness fault
    # (a loud throw), never a wait: the wait cycle this closes is a gate that holds one lock and queues for another.
    Invoke-Case 'U13' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Enter-GateHeld') } catch { }
        Assert-True 'U13 the gate defines Enter-GateHeld' ([bool]$fn)
        if (-not $fn) { return }
        $script:U13Taken = $false
        function Enter-HeldCheckout { $script:U13Taken = $true; [pscustomobject]@{ Acquired = $false; WaitMs = 0; Abandoned = $false; Reaped = @() } }
        function Emit { }
        function Add-Detail { }
        function Update-ActivityRecord { }
        function Get-CheckoutHolders { @() }
        $WaitSec = 0; $runId = 'u13'
        . ([scriptblock]::Create($fn))
        $name = 'Global\held-u13-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
        $script:HeldLease = $null
        $script:TwinLock = New-RunLock $name
        [void] $script:TwinLock.WaitOne(0)
        $msg = ''
        try { Enter-GateHeld -Checkout 'C:\held-fixture\repo' -Site entry } catch { $msg = $_.Exception.Message }
        Assert-True 'U13 entering while the twin lock is held throws, naming the site' ($msg -match 'Enter-GateHeld\(entry\) entered holding a lock') "msg=$msg"
        Assert-True 'U13 the throw takes no lock' (-not $script:U13Taken)
        try { $script:TwinLock.ReleaseMutex() } catch { }
        $script:TwinLock.Dispose(); $script:TwinLock = $null
        $script:HeldLease = [pscustomobject]@{ Acquired = $true }
        $msg = ''
        try { Enter-GateHeld -Checkout 'C:\held-fixture\repo' -Site twin } catch { $msg = $_.Exception.Message }
        Assert-True 'U13 entering while a checkout lease is held throws' ($msg -match 'Enter-GateHeld\(twin\) entered holding a lock') "msg=$msg"
        $script:HeldLease = $null
    }

    # U14: a verdict reaches the ledger before the lock is released, so a waiter that acquires next finds the producer's record.
    Invoke-Case 'U14' {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
        $cg = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -eq 'Complete-Gate' }, $true))
        Assert-True 'U14 the gate defines Complete-Gate' ($cg.Count -eq 1)
        if ($cg.Count -ne 1) { return }
        $cmds = @($cg[0].FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] }, $true))
        $ledger = @($cmds | Where-Object { $_.GetCommandName() -eq 'Set-Content' -and $_.Extent.Text -match 'result\.json' })
        $releases = @($cmds | Where-Object { $_.GetCommandName() -in 'Exit-GateHeld', 'Exit-TwinLock' })
        Assert-True 'U14 Complete-Gate writes the ledger record (checkout copy and shared copy)' ($ledger.Count -eq 2)
        Assert-True 'U14 Complete-Gate releases the checkout lease and the twin lock' ((((@($releases | ForEach-Object { $_.GetCommandName() }) | Sort-Object -Unique) -join ',')) -eq 'Exit-GateHeld,Exit-TwinLock')
        Assert-True 'U14 the ledger write precedes every release' ($ledger.Count -eq 2 -and $releases.Count -gt 0 -and
            @($releases | Where-Object { $_.Extent.StartOffset -lt $ledger[-1].Extent.StartOffset }).Count -eq 0) "ledger@$(if ($ledger.Count) { $ledger[-1].Extent.StartOffset }) releases@$(($releases | ForEach-Object { $_.Extent.StartOffset }) -join ',')"
    }

    # U15: the queue is gone from the gate: no queue parameter and no queue literal.
    Invoke-Case 'U15' {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
        $names = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
        $gone = @('FromQueue', 'QueueId', 'QueueStatus', 'WaitForQueue') | Where-Object { $_ -in $names }
        Assert-True 'U15 the gate takes none of -FromQueue, -QueueId, -QueueStatus, -WaitForQueue' (@($gone).Count -eq 0) "still present: $(@($gone) -join ',')"
        Assert-True 'U15 the gate takes -WaitSec' ('WaitSec' -in $names)
        $src = Get-Content -LiteralPath $gatePath -Raw
        Assert-True 'U15 the gate source has no gate_queue directory or harness-gatequeue mutex literal' ($src -notmatch 'gate_queue|harness-gatequeue')
        $gone = @('scripts\gate_queue_watcher.ps1', 'scripts\GateQueue.ps1', 'hooks\gate_queue_surface.py') | Where-Object { Test-Path -LiteralPath (Join-Path (Split-Path $scriptsDir) $_) }
        Assert-True 'U15 the watcher, the queue module and the queue-result surfacing hook are deleted' (@($gone).Count -eq 0) "still present: $(@($gone) -join ', ')"
    }

    # U16: a test runner on the checkout that holds no lock (an older wrapper, a raw dotnet test) is a holder the wait names.
    Invoke-Case 'U16' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Get-CheckoutHolders') } catch { }
        Assert-True 'U16 the gate defines Get-CheckoutHolders' ([bool]$fn)
        if (-not $fn) { return }
        function Test-CheckoutLockFree { param([string] $Repo) $true }
        function Get-LiveEditor { param([string] $Checkout, [hashtable] $Map) @() }
        $IgnoreEditor = $false; $activityDir = Join-Path $work 'activity-u16'
        . ([scriptblock]::Create($fn))
        $repo = Join-Native $fixtureRoot, 'repo'
        $map = New-Map @((P 920100 1 'pwsh.exe' 'pwsh -File run_test_suite.ps1' -AgeMin 70),
                         (P 920101 920100 'testhost.exe' '' (Join-Native $repo, '.godot', 'mono', 'temp', 'bin', 'Debug', 'testhost.exe') -AgeMin 30),
                         (P 920102 920101 'Godot_v4.exe' 'godot --headless --path . --pipe-name gdunit4-b' -AgeMin 20))
        $h = @(Get-CheckoutHolders -Checkout $repo -Map $map)
        Assert-True 'U16 one holder: the foreign runner, named by pid and start' ($h.Count -eq 1 -and $h[0].Kind -eq 'test runner' -and [int]$h[0].Pid -eq 920102 -and
            [math]::Abs((([datetime]$h[0].Since) - [datetime]::Now.AddMinutes(-20)).TotalSeconds) -lt 30) ($h | ConvertTo-Json -Compress)
        $late = @(Get-CheckoutHolders -Checkout $repo -Map $map -LateOnly)
        Assert-True 'U16 -LateOnly reports the runner too' ($late.Count -eq 1 -and [int]$late[0].Pid -eq 920102)
        $IgnoreEditor = $true
        function Get-LiveEditor { param([string] $Checkout, [hashtable] $Map) @([pscustomobject]@{ ProcessId = 0; Name = 'planted'; CommandLine = $Checkout }) }
        $h = @(Get-CheckoutHolders -Checkout $repo -Map $map)
        Assert-True 'U16 -IgnoreEditor leaves an editor out of the holders' (@($h | Where-Object { $_.Kind -eq 'editor' }).Count -eq 0)
        $IgnoreEditor = $false
        $h = @(Get-CheckoutHolders -Checkout $repo -Map $map)
        Assert-True 'U16 an editor is a holder, listed before the runner' ($h.Count -eq 2 -and $h[0].Kind -eq 'editor' -and $h[1].Kind -eq 'test runner') (($h | ForEach-Object Kind) -join ',')
    }

    # U4 (R3): after a pool and before release the gate reaps any Godot whose command line names its own run dir.
    Invoke-Case 'U4' {
        $script:Killed = [System.Collections.Generic.List[int]]::new()
        function Stop-ProcessTree { param([int] $ProcessId) $script:Killed.Add($ProcessId) }
        function Emit { param([string] $Text) Write-Output $Text }
        $script:RunDir = Join-Native $fixtureRoot, 'repo', 'TestResults', 'runs', 'gate-20260101-000000-1'
        . ([scriptblock]::Create((Get-FunctionText $gatePath @('Invoke-RunDirReap'))))
        $map = New-Map @((P 930001 1 'Godot_v4.exe' "godot --headless --log-file $fixtureFwd/repo/TestResults/runs/gate-20260101-000000-1/Logic/godot_test.log"),
                         (P 930002 1 'Godot_v4.exe' "godot --headless --log-file $fixtureFwd/repo/TestResults/runs/gate-20260101-000000-12/Logic/godot_test.log"),
                         (P 930003 1 'Godot_v4.exe' "godot --headless --log-file $fixtureFwd/repo/.claude/worktrees/x/TestResults/runs/gate-20260101-000000-1/Logic/godot_test.log"),
                         (P 930004 1 'testhost.exe' "testhost $fixtureFwd/repo/TestResults/runs/gate-20260101-000000-1/x"))
        $lines = @(Invoke-RunDirReap -Site 'pool:main' -Map $map)
        Assert-True 'U4 an orphaned Godot naming this gate''s run dir is reaped' ($script:Killed -contains 930001) "killed=$($script:Killed -join ',')"
        Assert-True 'U4 a Godot naming another run dir (a prefix-sharing runId, a nested worktree) is spared' (-not ($script:Killed -contains 930002) -and -not ($script:Killed -contains 930003))
        Assert-True 'U4 only Godot processes are candidates' (-not ($script:Killed -contains 930004))
        Assert-True 'U4 the reap prints its REAP line and one kill line' (($lines -join "`n") -match '(?m)^REAP site=pool:main killed=1 scope=run-dir' -and ($lines -join "`n") -match '(?m)^\s+kill\s+930001 ') ($lines -join ' | ')
    }

    # U5: the owner-root readers. A child whose own chain reaches no session (its launcher exited) still records the
    # owner root the gate passed in HARNESS_OWNER_ROOT_PID / HARNESS_OWNER_ROOT_START, through the wrapper's
    # Get-RunOwnerRoot and the gate's Resolve-GateOwnerRoot; a stale pair is ignored (the wrapper falls back to its own
    # walk, the gate resolves none).
    Invoke-Case 'U5' {
        $wrapperPath = Join-Path $scriptsDir 'run_test_suite.ps1'
        $wfn = ''
        try { $wfn = Get-FunctionText $wrapperPath @('Get-RunOwnerRoot') } catch { }
        $gfn = ''
        try { $gfn = Get-FunctionText $gatePath @('Resolve-GateOwnerRoot') } catch { }
        Assert-True 'U5 the wrapper defines Get-RunOwnerRoot' ([bool]$wfn)
        Assert-True 'U5 the gate defines Resolve-GateOwnerRoot' ([bool]$gfn)
        if (-not $wfn -or -not $gfn) { return }
        $childScript = Join-Path $work 'owner-child.ps1'
        $body = @"
. '$(Join-Path $scriptsDir 'RuntimeSlots.ps1')'
. '$(Join-Path $scriptsDir 'PeerActivity.ps1')'
$wfn
$gfn
Start-Sleep -Milliseconds 1500
`$walk = Get-OwnerRoot
`$r = Get-RunOwnerRoot
`$g = Resolve-GateOwnerRoot
[ordered]@{ walk = `$(if (`$walk) { `$walk.RootPid } else { 0 }); wrapper = `$(if (`$r) { `$r.RootPid } else { 0 }); gate = `$(if (`$g) { `$g.RootPid } else { 0 }); start = `$(if (`$r) { [string]`$r.RootStart } else { '' }) } | ConvertTo-Json -Compress | Set-Content -LiteralPath `$env:HELD_OWNER_OUT
"@
        [System.IO.File]::WriteAllText($childScript, $body, [System.Text.UTF8Encoding]::new($false))
        $stand = Start-Owned 'Start-Sleep -Seconds 120' 'u5-root'
        $standStart = Get-ProofStamp $stand.Id
        # Both launchers start before either is awaited: the two children share nothing, and each sleeps 1.5 s.
        $launched = foreach ($c in @(@{ n = 'live'; pid = "$($stand.Id)"; start = $standStart; want = $stand.Id },
                                     @{ n = 'stale'; pid = '4'; start = '2001-01-01T00:00:00.0000000Z'; want = 0 })) {
            $out = Join-Path $work "owner-$($c.n).json"
            # The launcher exits at once, so the child's own parent chain reaches no session: a detached gate's child.
            $launch = "`$env:HARNESS_OWNER_ROOT_PID='$($c.pid)'; `$env:HARNESS_OWNER_ROOT_START='$($c.start)'; `$env:HELD_OWNER_OUT='$out'; " +
                      "Start-Process '$($Pwsh.FilePath)' -ArgumentList @($PwshLeadArg'-NoProfile','-File','$childScript') $HiddenWindowArg -RedirectStandardOutput '$out.stdout' -RedirectStandardError '$out.stderr'"
            [pscustomobject]@{ c = $c; out = $out; l = (Start-Owned $launch "u5-launch-$($c.n)") }
        }
        foreach ($x in $launched) {
            $c = $x.c; $out = $x.out; $l = $x.l; [void]$l.WaitForExit(15000)
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $out)) { Start-Sleep -Milliseconds 200 }
            $res = if (Test-Path -LiteralPath $out) { Get-Content -LiteralPath $out -Raw | ConvertFrom-Json } else { $null }
            if ($c.n -eq 'live') {
                Assert-True 'U5 setup: the detached child''s own walk reaches no session' ($res -and [int]$res.walk -eq 0) "res=$($res | ConvertTo-Json -Compress) err=$(Get-Content "$out.stderr" -Raw -ErrorAction SilentlyContinue)"
                Assert-True 'U5 a child of a detached gate records the gate''s owner root from the env pair, through the wrapper and the gate' ($res -and [int]$res.wrapper -eq $stand.Id -and [int]$res.gate -eq $stand.Id -and ([datetime]$res.start).ToUniversalTime() -eq ([datetime]$standStart).ToUniversalTime()) ($res | ConvertTo-Json -Compress)
            } else {
                Assert-True 'U5 a stale env pair is ignored: the wrapper falls back to its walk and the gate resolves none' ($res -and [int]$res.wrapper -eq [int]$res.walk -and [int]$res.gate -eq 0) ($res | ConvertTo-Json -Compress)
            }
        }
        $peerSrc = Get-Content -LiteralPath $peerP -Raw
        Assert-True 'U5 the shared Get-OwnerRoot reads no environment' ($peerSrc -notmatch 'HARNESS_OWNER_ROOT')
    }

    # U7: the detached run's stage watcher (regression_gate.ps1 -Watch). Planted OUT + batch state; no gate, no lock.
    Invoke-Case 'U7' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Invoke-StageWatch') } catch { }
        Assert-True 'U7 the gate defines Invoke-StageWatch' ([bool]$fn)
        if (-not $fn) { return }
        . ([scriptblock]::Create($fn))
        $out = Join-Path $work 'gate_run_u7-live.out'
        $batches = Join-Path $work 'gate-watch-u7.batches.json'
        $stageLines = @('ENGINE=OK ver=4.7.1',
            'WAIT site=entry holder=gate pid=1 held=5s waited=0s left=60s',
            'WAIT site=entry acquired=1 waited=0s abandoned=0',
            'HELD site=enter acquired=1 waitMs=0 abandoned=0 reaped=0',
            'BUSY site=entry holder=gate pid=1 next="x"',
            'TRX_DIR path=C:\planted',
            'QUARANTINE filter=FullyQualifiedName!~Planted',
            'POOL phase=main width=3',
            'RETRY label=SegR prior=HANG',
            'UNIT label=SegA start',
            'SUITE Logic       passed=8814 failed=0 tier=PASS delta=+0 status=DONE dur=161s',
            'BATCH label=SegA status=GREEN',
            'EDITOR detected=1 phase=pool:main',
            'GATE_ERROR planted failure',
            'VERDICT=PASS')
        (@('noise line that is not a stage') + $stageLines) | Set-Content -LiteralPath $out -Encoding utf8
        '{"runId":"u7-live","batches":[{"label":"SegA","status":"GREEN","passed":10,"failed":0},{"label":"SegB","status":"PENDING"}]}' |
            Set-Content -LiteralPath $batches -Encoding utf8
        $got = @(Invoke-StageWatch -OutPath $out -BatchPath $batches -IntervalSec 1)
        Assert-True 'U7 each stage line streams once, in order' (
            (@($got | Where-Object { $_ -eq 'ENGINE=OK ver=4.7.1' }).Count -eq 1) -and
            ($got.IndexOf('ENGINE=OK ver=4.7.1') -lt $got.IndexOf('VERDICT=PASS'))) "got=$($got -join ' | ')"
        $missing = @($stageLines | Where-Object { $got -notcontains $_ })
        Assert-True 'U7 every stage prefix the gate emits (WAIT, BUSY, HELD, POOL, RETRY, UNIT, BATCH, TRX_DIR, QUARANTINE, EDITOR, GATE_ERROR) streams' ($missing.Count -eq 0) "missing=$($missing -join ' | ')"
        Assert-True 'U7 a non-stage line is never emitted' (-not ($got -contains 'noise line that is not a stage'))
        Assert-True 'U7 a finished batch streams and a PENDING one does not' (
            ($got -contains 'SegA GREEN passed=10 failed=0') -and -not ($got -match 'SegB')) "got=$($got -join ' | ')"
        '{"runId":"an-older-run","batches":[{"label":"SegOld","status":"GREEN","passed":3,"failed":0}]}' | Set-Content -LiteralPath $batches -Encoding utf8
        $got = @(Invoke-StageWatch -OutPath $out -BatchPath $batches -IntervalSec 1)
        Assert-True 'U7 batch state written by another run streams nothing' (-not ($got -match 'SegOld')) "got=$($got -join ' | ')"
        '[{"label":"SegLegacy","status":"GREEN","passed":3,"failed":0}]' | Set-Content -LiteralPath $batches -Encoding utf8
        $got = @(Invoke-StageWatch -OutPath $out -BatchPath $batches -IntervalSec 1)
        Assert-True 'U7 batch state with no run id streams nothing' (-not ($got -match 'SegLegacy')) "got=$($got -join ' | ')"
    }

    # U8: a detached run killed before its VERDICT= ends the watcher nonzero; a live one still streams to VERDICT= and ends 0.
    Invoke-Case 'U8' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Invoke-StageWatch') } catch { }
        if (-not $fn) { Assert-True 'U8 the gate defines Invoke-StageWatch' $false; return }
        $runner = Join-Path $work 'u8-runner.ps1'
        @('param([string] $O, [int] $I)', ". '$(Join-Path $scriptsDir 'GodotProcess.ps1')'", $fn, 'Invoke-StageWatch -OutPath $O -IntervalSec $I', 'exit $script:WatchExit') -join "`n" |
            Set-Content -LiteralPath $runner -Encoding utf8
        $watch = {
            param([string] $Out, [int] $LimitSec)
            $w = Start-Process $Pwsh.FilePath -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-File', $runner, '-O', $Out, '-I', '1')) -PassThru @HiddenWindow `
                -RedirectStandardOutput "$Out.watch" -RedirectStandardError "$Out.watcherr"
            $script:Owned.Add($w)
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $timedOut = -not $w.WaitForExit($LimitSec * 1000)
            if ($timedOut) { $w.Kill($true) }
            $sw.Stop()
            [pscustomobject]@{ TimedOut = $timedOut; Code = $(if ($timedOut) { -1 } else { $w.ExitCode }); Sec = $sw.Elapsed.TotalSeconds
                               Text = (Get-Content -LiteralPath "$Out.watch" -Raw -ErrorAction SilentlyContinue) }
        }
        $writePid = {
            param([string] $Out, [System.Diagnostics.Process] $P, [string] $Stamp = '')
            if (-not $Stamp) { $Stamp = Get-ProofStamp $P.Id }
            $t = [datetime]::Parse($Stamp, [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::RoundtripKind)
            "pid=$($P.Id) start=$($t.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
                Set-Content -LiteralPath ([System.IO.Path]::ChangeExtension($Out, '.pid')) -Encoding ascii
        }

        $outDead = Join-Path $work 'u8-dead.out'
        'ENGINE=OK ver=4.7.1' | Set-Content -LiteralPath $outDead -Encoding utf8
        $dead = Start-Owned 'Start-Sleep -Milliseconds 400' 'u8-dead-producer'
        $deadStamp = Get-ProofStamp $dead.Id  # an exited process has no stamp to read
        & $writePid $outDead $dead $deadStamp
        $dead.WaitForExit(10000) | Out-Null
        $r = & $watch $outDead 20
        Assert-True 'U8 a dead producer with no VERDICT ends the watcher nonzero within the poll interval plus margin' (
            -not $r.TimedOut -and $r.Code -ne 0 -and $r.Sec -lt 15) "timedOut=$($r.TimedOut) code=$($r.Code) sec=$($r.Sec)"
        Assert-True 'U8 the dead-run line names the OUT path, the ERR path and the rerun' (
            $r.Text -match [regex]::Escape($outDead) -and $r.Text -match [regex]::Escape([System.IO.Path]::ChangeExtension($outDead, '.err')) -and $r.Text -match 'rerun') "text=$($r.Text)"
        Assert-True 'U8 a stage line written before death still streams' ($r.Text -match 'ENGINE=OK')

        $outVerdict = Join-Path $work 'u8-verdict-after-death.out'
        'VERDICT=PASS' | Set-Content -LiteralPath $outVerdict -Encoding utf8
        & $writePid $outVerdict $dead $deadStamp
        $r = & $watch $outVerdict 20
        Assert-True 'U8 a VERDICT= already written when the producer exits still ends the watcher 0' (
            -not $r.TimedOut -and $r.Code -eq 0 -and $r.Text -match 'VERDICT=PASS') "timedOut=$($r.TimedOut) code=$($r.Code) text=$($r.Text)"

        $outLive = Join-Path $work 'u8-live.out'
        'ENGINE=OK ver=4.7.1' | Set-Content -LiteralPath $outLive -Encoding utf8
        $live = Start-Owned "Start-Sleep -Seconds 3; Add-Content -LiteralPath '$outLive' -Value 'VERDICT=PASS'; Start-Sleep -Seconds 60" 'u8-live-producer'
        & $writePid $outLive $live
        $r = & $watch $outLive 40
        Assert-True 'U8 a live producer that writes VERDICT=PASS ends the watcher 0' (
            -not $r.TimedOut -and $r.Code -eq 0 -and $r.Text -match 'VERDICT=PASS') "timedOut=$($r.TimedOut) code=$($r.Code) text=$($r.Text)"
        try { $live.Kill($true) } catch { }

        $outNoPid = Join-Path $work 'u8-nopid.out'
        'VERDICT=PASS' | Set-Content -LiteralPath $outNoPid -Encoding utf8
        $r = & $watch $outNoPid 20
        Assert-True 'U8 an OUT with no pid sidecar keeps the VERDICT= behavior' (-not $r.TimedOut -and $r.Code -eq 0) "code=$($r.Code)"
    }

    Invoke-Case 'U9' {
        . (Join-Path $scriptsDir 'CSharpSource.ps1')
        $patch = @'
diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs
--- a/Tests/Logic/Cases.cs
+++ b/Tests/Logic/Cases.cs
@@ -1,11 +1,8 @@
 class Cases {
-    [Test]
-    [TestCase(1), TestCase(2)]
-    [GdUnit4.TestCaseAttribute(
-        new int[] { 3, 4 })]
-    public void Gone() { }
+    [TestCase(Timeout = 100)]
+    public void New() { }
     /*
-    [TestCase]
+    [Test]
     */
-    string text = "[TestCase]";
+    string text = "[Test]";
 }
'@
        $d = Get-TestAttributeDelta -Patch ($patch -split "`n")
        Assert-True 'U9 counts Test, inline cases, grouped and multiline qualified attributes, not comments or strings' (
            $d.readable -and $d.removed -eq 4 -and $d.added -eq 1) ($d | ConvertTo-Json -Compress)
        $d = Get-TestAttributeDelta -Patch (($patch.Replace('@@ -1,11 +1,8 @@', '@@ -1,12 +1,9 @@').Replace(' class Cases {', " class Cases {`n     [DataPoint(nameof(Rows))]")) -split "`n")
        Assert-True 'U9 an unchanged DataPoint in a changed file makes the diff uncountable' (-not $d.readable -and $d.reason -eq 'data-driven-tests') ($d | ConvertTo-Json -Compress)
        $uncountable = @(
            @{ name = 'binary patch lines'; reason = 'binary-test-diff'; lines = @('diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs', 'Binary files a/Tests/Logic/Cases.cs and b/Tests/Logic/Cases.cs differ') },
            @{ name = 'a git binary patch'; reason = 'binary-test-diff'; lines = @('diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs', 'GIT binary patch') },
            @{ name = 'a raw string literal'; reason = 'uncountable-test-source'; lines = @($patch.Replace('    string text = "[Test]";', '    string text = """raw""";') -split "`n") },
            @{ name = 'a preprocessor branch'; reason = 'uncountable-test-source'; lines = @($patch.Replace(' class Cases {', " class Cases {`n #if DEBUG").Replace('@@ -1,11 +1,8 @@', '@@ -1,12 +1,9 @@') -split "`n") })
        foreach ($u in $uncountable) {
            $d = Get-TestAttributeDelta -Patch $u.lines
            Assert-True "U9 $($u.name) is uncountable ($($u.reason))" (-not $d.readable -and $d.reason -eq $u.reason -and $d.removed -eq 0 -and $d.added -eq 0) ($d | ConvertTo-Json -Compress)
        }
        $countable = @(
            @{ name = 'a verbatim string ending in an escaped quote'; lines = @($patch.Replace('    string text = "[Test]";', '    string text = @"a=""b""";') -split "`n") },
            @{ name = 'a preprocessor branch holding no test attribute'; lines = @($patch.Replace(' }' + "`n" + "'@", ' }').Replace('@@ -1,11 +1,8 @@', '@@ -1,14 +1,11 @@').Replace(' class Cases {', " #if TOOLS`n internal static int Hook;`n #endif`n class Cases {") -split "`n") })
        foreach ($c in $countable) {
            $d = Get-TestAttributeDelta -Patch $c.lines
            Assert-True "U9 $($c.name) stays countable" ($d.readable -and $d.removed -eq 4 -and $d.added -eq 1) ($d | ConvertTo-Json -Compress)
        }
        $d = Get-TestAttributeDelta -Patch @($patch.Replace(' class Cases {', " class Cases {`n #if DEBUG").Replace('    public void New() { }', "    public void New() { }`n #endif").Replace('@@ -1,11 +1,8 @@', '@@ -1,13 +1,10 @@') -split "`n")
        Assert-True 'U9 a preprocessor branch around a test attribute stays uncountable' (-not $d.readable -and $d.reason -eq 'uncountable-test-source') ($d | ConvertTo-Json -Compress)
    }

    # U10: the project's gate_guards table, through the gate's own Invoke-StaticGuards: a configured guard executes, a
    # configured guard with no file is UNAVAILABLE (which the gate ends INVALID at exit 2), absent or empty data keeps
    # only the generic set.
    Invoke-Case 'U10' {
        . (Join-Path $scriptsDir 'StaticGuards.ps1')
        $block = Get-Content -LiteralPath (Join-Path $scriptsDir 'StaticGuards.ps1') -Raw
        $generic = 'nullstrip=OK tool_cascade=OK script_strip=OK gate_coverage=OK dup_double=OK refcount_free=OK'
        $fixture = Join-Path $work 'adaptation-u10'
        $hooks = Join-Path $fixture 'hooks'
        $adapt = Join-Path $fixture '.claude/skills/project_subsystems/adaptation.json'
        New-Item -ItemType Directory -Force -Path $hooks, (Split-Path $adapt) | Out-Null
        foreach ($match in [regex]::Matches($block, "=\s*'([\w]+\.py)'")) {
            "def main():`n    return 0`n" | Set-Content -LiteralPath (Join-Path $hooks $match.Groups[1].Value) -Encoding utf8
        }
        foreach ($scenario in @('absent-file', 'absent-key', 'present', 'missing')) {
            Remove-Item -LiteralPath $adapt -Force -ErrorAction SilentlyContinue
            if ($scenario -eq 'absent-key') { '{}' | Set-Content -LiteralPath $adapt -Encoding utf8 }
            if ($scenario -in 'present', 'missing') {
                @{ gate_guards = (@{ project_extra = 'project_guard.py' } | ConvertTo-Json -Compress) } |
                    ConvertTo-Json | Set-Content -LiteralPath $adapt -Encoding utf8
            }
            $projectGuard = Join-Path $hooks 'project_guard.py'
            Remove-Item -LiteralPath $projectGuard -Force -ErrorAction SilentlyContinue
            if ($scenario -eq 'present') {
                "from pathlib import Path`ndef main():`n    Path('executed.txt').write_text('project guard ran')`n    return 0`n" |
                    Set-Content -LiteralPath $projectGuard -Encoding utf8
            }
            $marker = Join-Path $fixture 'executed.txt'
            Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
            $run = Invoke-StaticGuards -Repo $fixture -Hooks $hooks -Py 'python3'
            $text = $run.Results -join ' '
            if ($scenario -eq 'missing') {
                Assert-True 'U10 a configured missing guard is UNAVAILABLE and stops the run' (
                    $run.Unavailable -and $text -match 'project_extra=UNAVAILABLE' -and -not (Test-Path $marker)) $text
            } elseif ($scenario -eq 'present') {
                Assert-True 'U10 a configured project guard executes through the actual gate block' (
                    -not $run.Unavailable -and $text -eq "$generic project_extra=OK" -and (Test-Path $marker)) $text
            } else {
                Assert-True "U10 $scenario keeps only generic coverage" (-not $run.Unavailable -and $text -eq $generic) $text
            }
        }
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
        $ifs = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.IfStatementAst] -and $x.Clauses[0].Item1.Extent.Text -eq '$guardRun.Unavailable' }, $true))
        Assert-True 'U10 the gate ends an unavailable guard run INVALID at exit 2' ($ifs.Count -eq 1 -and
            @($ifs[0].Clauses[0].Item2.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] -and $x.GetCommandName() -eq 'Complete-Gate' -and $x.Extent.Text -match "'INVALID' 2" }, $true)).Count -eq 1)
    }

    # ================================================================== Part D: row decisions, in process
    # Each case here calls the shipped function on planted facts. A gate process would add PowerShell startup, git
    # stubs and suite children to a decision that reads none of them.

    # D1: the Logic partition rows (Get-LogicGroups/Get-LogicUnits) on planted project data: one unit per kept group
    # plus the complement, a folder that does not exist dropped, the listed order kept, the render exclusion on every
    # unit, and one `Logic` unit when the project names no groups.
    Invoke-Case 'D1' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Get-LogicGroups', 'Get-LogicUnits') } catch { }
        Assert-True 'D1 the gate defines Get-LogicGroups and Get-LogicUnits' ([bool]$fn)
        if (-not $fn) { return }
        . ([scriptblock]::Create($fn))
        $r = Join-Path $work 'logic-rows'
        foreach ($n in 'AI', 'Combat', 'Zeta', 'Alpha', 'Omega') { New-Item -ItemType Directory -Force -Path (Join-Path $r "Tests\Logic\$n") | Out-Null }
        $mapP = Join-Path $r '.claude\scripts\gate_domain_map.json'
        New-Item -ItemType Directory -Force -Path (Split-Path $mapP) | Out-Null
        $labels = { param($u) (@($u | ForEach-Object label) -join ',') }
        $filters = { param($u, $l) [string]@($u | Where-Object { $_.label -eq $l })[0].filter }
        try {
            [IO.File]::WriteAllText($mapP, '{ "schema_version": 1, "domains": {}, "logic_groups": [["AI"], ["Combat"]] }')
            $u = @(Get-LogicUnits -Repo $r -RenderExcl '' -TimeoutMs 1000)
            Assert-True 'D1 planted groups give one unit per kept group plus Logic_rest' ((& $labels $u) -eq 'Logic_1,Logic_2,Logic_rest') (& $labels $u)
            Assert-True 'D1 each group unit selects only its own folders' ((& $filters $u 'Logic_1') -eq '(FullyQualifiedName~Tests.Logic.AI.)' -and (& $filters $u 'Logic_2') -eq '(FullyQualifiedName~Tests.Logic.Combat.)') "$(& $filters $u 'Logic_1') | $(& $filters $u 'Logic_2')"
            $rest = & $filters $u 'Logic_rest'
            Assert-True 'D1 the complement selects all Logic and excludes every group folder' ($rest.Contains('FullyQualifiedName~Tests.Logic&') -and $rest.Contains('&FullyQualifiedName!~Tests.Logic.AI.') -and $rest.Contains('&FullyQualifiedName!~Tests.Logic.Combat.')) $rest
            $u = @(Get-LogicUnits -Repo $r -RenderExcl '&FullyQualifiedName!~X.' -TimeoutMs 1000)
            Assert-True 'D1 the render exclusion reaches every Logic unit' (@($u | Where-Object { -not ([string]$_.filter).EndsWith('&FullyQualifiedName!~X.') }).Count -eq 0) ((@($u | ForEach-Object filter)) -join ' | ')
            [IO.File]::WriteAllText($mapP, '{ "schema_version": 1, "domains": {}, "logic_groups": [["Zeta"], ["Omega", "Alpha", "Missing"]] }')
            $u = @(Get-LogicUnits -Repo $r -RenderExcl '' -TimeoutMs 1000)
            Assert-True 'D1 a group keeps its listed folder order and drops a folder that does not exist' ((& $labels $u) -eq 'Logic_1,Logic_2,Logic_rest' -and (& $filters $u 'Logic_1') -eq '(FullyQualifiedName~Tests.Logic.Zeta.)' -and
                (& $filters $u 'Logic_2') -eq '(FullyQualifiedName~Tests.Logic.Omega.|FullyQualifiedName~Tests.Logic.Alpha.)') "$(& $labels $u) $(& $filters $u 'Logic_2')"
            $rest = & $filters $u 'Logic_rest'
            Assert-True 'D1 the complement excludes every kept folder and no dropped one' ($rest.Contains('&FullyQualifiedName!~Tests.Logic.Zeta.') -and $rest.Contains('&FullyQualifiedName!~Tests.Logic.Omega.') -and
                $rest.Contains('&FullyQualifiedName!~Tests.Logic.Alpha.') -and -not $rest.Contains('Missing')) $rest
            [IO.File]::WriteAllText($mapP, '{ "schema_version": 1, "domains": {}, "logic_groups": [["Missing"], ["Zeta"]] }')
            $u = @(Get-LogicUnits -Repo $r -RenderExcl '' -TimeoutMs 1000)
            Assert-True 'D1 a group with no existing folder is dropped' ((& $labels $u) -eq 'Logic_1,Logic_rest' -and (& $filters $u 'Logic_1') -eq '(FullyQualifiedName~Tests.Logic.Zeta.)') (& $labels $u)
            Remove-Item -LiteralPath $mapP -Force
            $u = @(Get-LogicUnits -Repo $r -RenderExcl '' -TimeoutMs 1000)
            Assert-True 'D1 no logic_groups data gives the one Logic unit' ((& $labels $u) -eq 'Logic' -and (& $filters $u 'Logic') -eq 'FullyQualifiedName~Tests.Logic') (& $labels $u)
        } finally { Remove-Item -LiteralPath $mapP -Force -ErrorAction SilentlyContinue }
    }

    # D2: the Logic result rows (Merge-LogicResults + Test-SuiteTiers): the sum of the units is the suite's count, a
    # unit that ran no tests or hit the silent-skip signature makes the whole suite INVALID, an uncounted unit makes the
    # sum UNKNOWN, and a clean sum below the baseline is PARTIAL (a count drop, never PASS).
    Invoke-Case 'D2' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Merge-LogicResults', 'Test-SuiteTiers', 'Test-RecompileEvidence') } catch { }
        Assert-True 'D2 the gate defines Merge-LogicResults, Test-SuiteTiers and Test-RecompileEvidence' ([bool]$fn)
        if (-not $fn) { return }
        . ([scriptblock]::Create($fn))
        $baseline = [pscustomobject]@{
            suites = [pscustomobject]@{ Logic = [pscustomobject]@{ passed = 10 } }
            silent_skip_sentinels = [pscustomobject]@{ Logic_min = 1 }
            drift_thresholds = [pscustomobject]@{ warn_ratio = 0.9; hard_fail_ratio = 0.7 }
        }
        $script:IncompleteStatuses = @('NO_OUTPUT', 'NO_TRX', 'BAD_TRX', 'HELD_REFUSED', 'EDITOR_OPEN', 'SKIP_RECOMPILE_IGNORED')
        $res = { param([string] $Label, [int] $Passed, [int] $Failed, [string] $Status = 'DONE', [bool] $Silent = $false)
            [ordered]@{ label = $Label; passed = $Passed; failed = $Failed; status = $Status; silentSkip = $Silent; exit = $(if ($Failed) { 1 } else { 0 }); elapsed = 1.0; raw = ''; outPath = ''
                        failedTests = @(); failedMessages = @() } }
        $tier = { param($R) (Test-SuiteTiers -R $R -Label 'Logic').tier }
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' 4 0), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 the suite count is the sum of its units' ($merged.passed -eq 10 -and (& $tier $merged) -eq 'PASS') "passed=$($merged.passed) tier=$(& $tier $merged)"
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' 4 0 'SILENT_SKIP' $true), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 one silently skipping unit makes the suite INVALID, never PASS' ($merged.silentSkip -and (& $tier $merged) -eq 'INVALID') "tier=$(& $tier $merged)"
        Assert-True 'D2 the silent-signature unit carries no recompile evidence' (-not (Test-RecompileEvidence $merged))
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' 0 0), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 a unit that ran no tests makes the suite INVALID' ($merged.silentSkip -and (& $tier $merged) -eq 'INVALID') "tier=$(& $tier $merged)"
        Assert-True 'D2 a unit that ran no tests carries no recompile evidence' (-not (Test-RecompileEvidence $merged))
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' -1 -1 'DONE'), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 an uncounted unit that reports a status makes the sum UNPARSED' ($merged.passed -lt 0 -and (& $tier $merged) -eq 'UNPARSED') "passed=$($merged.passed) tier=$(& $tier $merged)"
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 1), (& $res 'Logic_2' 4 0)))
        Assert-True 'D2 a parsed failure outranks every count signal' ((& $tier $merged) -eq 'FAILING') "tier=$(& $tier $merged)"
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' 3 0), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 units that all pass but sum below the baseline are PARTIAL, never PASS' ($merged.passed -eq 9 -and (& $tier $merged) -eq 'PARTIAL') "passed=$($merged.passed) tier=$(& $tier $merged)"
        $merged = @(Merge-LogicResults @((& $res 'Logic_1' 4 0), (& $res 'Logic_2' 4 0 'NO_TRX'), (& $res 'Logic_rest' 2 0)))
        Assert-True 'D2 a unit with no readable result makes the suite INCOMPLETE' ((& $tier $merged) -eq 'INCOMPLETE') "tier=$(& $tier $merged)"
        Assert-True 'D2 a red suite with executed tests carries recompile evidence' (Test-RecompileEvidence (& $res 'Logic_1' 9 1))
    }

    # D3: the Integration plan rows: the project's quarantine reaches every batch and the Render row, and the Logic
    # units carry none; the gate reads the value from adaptation.json's test_quarantine_filter and hands it to the
    # planner only.
    Invoke-Case 'D3' {
        $r = Join-Path $work 'plan-rows'
        $vis = Join-Path $r 'Tests\Integration\Visual'
        $play = Join-Path $r 'Tests\Integration\Playtest'
        New-Item -ItemType Directory -Force -Path $vis, $play | Out-Null
        [IO.File]::WriteAllText((Join-Path $vis 'Tagged.cs'), "namespace P.Tests.Integration.Visual;`n[TestSuite]`n[RequiresRenderer]`npublic partial class TaggedTests { }`n")
        $render = @(Get-RendererTaggedClasses -Repo $r)
        $q = 'FullyQualifiedName!~Quarantined.Suite'
        $man = Read-BatchManifest -Path (Join-Path $r 'no-manifest.json')
        $plan = @(New-IntegrationPlan -Segments @('Visual', 'Playtest') -Manifest $man -TargetBatchSec 60 -MaxBatchTests 380 -Quarantine $q -RenderClasses $render)
        $ints = @($plan | Where-Object { $_.label -like 'Integration_B*' })
        $row = @($plan | Where-Object { $_.label -eq 'Render' })
        Assert-True 'D3 every Integration batch carries the quarantine' ($ints.Count -ge 1 -and @($ints | Where-Object { -not ([string]$_.filter).EndsWith("&$q") }).Count -eq 0) (($ints | ForEach-Object filter) -join ' | ')
        Assert-True 'D3 the Render row carries the quarantine and selects the tagged class' ($row.Count -eq 1 -and ([string]$row[0].filter).EndsWith("&$q") -and ([string]$row[0].filter).IndexOf("FullyQualifiedName~$($render[0]).") -ge 0) (($row | ForEach-Object filter) -join ' | ')
        $fn = Get-FunctionText $gatePath @('Get-LogicGroups', 'Get-LogicUnits')
        . ([scriptblock]::Create($fn))
        New-Item -ItemType Directory -Force -Path (Join-Path $r 'Tests\Logic\AI') | Out-Null
        $logic = @(Get-LogicUnits -Repo $r -RenderExcl '' -TimeoutMs 1000)
        Assert-True 'D3 the Logic units carry no quarantine' ($logic.Count -ge 1 -and @($logic | Where-Object { ([string]$_.filter).Contains($q) }).Count -eq 0) (($logic | ForEach-Object filter) -join ' | ')
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
        $adaptRead = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] -and $x.GetCommandName() -eq 'Get-AdaptationValue' -and $x.Extent.Text -match 'test_quarantine_filter' }, $true))
        $planner = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.CommandAst] -and $x.GetCommandName() -eq 'New-IntegrationPlan' }, $true))
        $sanity = @($ast.FindAll({ param($x) $x -is [System.Management.Automation.Language.AssignmentStatementAst] -and $x.Left.Extent.Text -eq '$sanityUnit' }, $true))
        Assert-True 'D3 the gate reads the quarantine from adaptation.json and passes it to the Integration planner only' (
            $adaptRead.Count -eq 1 -and $planner.Count -eq 1 -and $planner[0].Extent.Text -match '-Quarantine \$quarantine' -and
            $sanity.Count -eq 1 -and -not $sanity[0].Right.Extent.Text.Contains('quarantine')) "adapt=$($adaptRead.Count) planner=$($planner.Count) sanity=$($sanity.Count)"
    }

    # D4: the retry selection (Select-RetryUnits): only HANG, SILENT_SKIP and NO_TRX rows retry, once, as
    # <label>_retry with the original row's filter and windowed flag; RED and a finished row never retry.
    Invoke-Case 'D4' {
        $row = { param([string] $Label, [string] $Status, [bool] $Windowed = $false) [pscustomobject]@{ label = $Label; filter = "filter-$Label"; expectedSec = 10.0; unmeasured = $false; windowed = $Windowed; status = $Status } }
        $plan = @((& $row 'Integration_B1' 'HANG'), (& $row 'Integration_B2' 'SILENT_SKIP'), (& $row 'Integration_B3' 'NO_TRX'),
                  (& $row 'Integration_B4' 'RED'), (& $row 'Integration_B5' 'GREEN'), (& $row 'Render' 'HANG' $true))
        $retry = @(Select-RetryUnits -Plan $plan -CostCeilingMs 300000)
        Assert-True 'D4 a HANG, a SILENT_SKIP and a NO_TRX row retry once each; RED and GREEN never do' (
            (@($retry | ForEach-Object label) | Sort-Object) -join ',' -eq 'Integration_B1_retry,Integration_B2_retry,Integration_B3_retry,Render_retry') ((@($retry | ForEach-Object label)) -join ',')
        Assert-True 'D4 a retry keeps its row''s filter and windowed flag' (@($retry | Where-Object { $_.filter -ne "filter-$($_.label -replace '_retry$', '')" }).Count -eq 0 -and
            @($retry | Where-Object { $_.label -eq 'Render_retry' -and -not $_.windowed }).Count -eq 0) (($retry | ConvertTo-Json -Compress))
    }

    # D6: the locked-suite verdict (Complete-LockedGate): a suite whose child reported LOCK_TIMEOUT ends BUSY (exit 7)
    # at once, naming the live runtime-slot holder and the -RetryOnly step. The slot record is read from a planted dir.
    Invoke-Case 'D6' {
        $fn = ''
        try { $fn = Get-FunctionText $gatePath @('Test-SuiteTiers', 'Complete-LockedGate', 'Complete-Busy', 'Get-SlotHolders', 'Get-SlotNext', 'Format-HolderLine', 'Format-HolderSince') } catch { }
        Assert-True 'D6 the gate defines the locked-suite verdict path' ([bool]$fn)
        if (-not $fn) { return }
        $slotDir = Join-Path $work 'slots-d6'
        New-Item -ItemType Directory -Force -Path $slotDir | Out-Null
        $savedSlots = $script:RuntimeSlotRecordRoot
        $script:Emitted = [System.Collections.Generic.List[string]]::new()
        $script:GateVerdict = $null; $script:GateCode = $null
        function Emit { param([string] $Text) $script:Emitted.Add($Text) }
        function Add-Detail { param([string] $Text) }
        function Complete-Gate { param([string] $Verdict, [int] $Code) $script:GateVerdict = $Verdict; $script:GateCode = $Code }
        $WaitSec = 0
        . ([scriptblock]::Create($fn))
        $peer = Start-Owned 'Start-Sleep -Seconds 120' 'd6-slot-holder'
        $peerRec = [ordered]@{ holderPid = $peer.Id; procStart = (Get-ProofStamp $peer.Id); checkout = 'C:\held-fixture\other'
                               label = 'peer-run'; state = 'running'; acquiredAt = (Get-Date).ToUniversalTime().ToString('o'); releasedAt = $null; runnerPid = $null; runnerStart = $null }
        $peerRec | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $slotDir 'slot-3.json') -Encoding utf8
        try {
            $script:RuntimeSlotRecordRoot = $slotDir
            $baseline = [pscustomobject]@{
                suites = [pscustomobject]@{ Logic = [pscustomobject]@{ passed = 10 } }
                silent_skip_sentinels = [pscustomobject]@{ Logic_min = 1 }
                drift_thresholds = [pscustomobject]@{ warn_ratio = 0.9; hard_fail_ratio = 0.7 }
            }
            $script:IncompleteStatuses = @('NO_OUTPUT', 'NO_TRX', 'BAD_TRX', 'HELD_REFUSED', 'EDITOR_OPEN', 'SKIP_RECOMPILE_IGNORED')
            $suiteResults = [ordered]@{ Logic = @{ r = [ordered]@{ label = 'Logic'; passed = -1; failed = -1; status = 'LOCK_TIMEOUT'; silentSkip = $false; exit = 0; elapsed = 1.0; raw = ''; outPath = '' }
                                                  t = (Test-SuiteTiers -R ([ordered]@{ passed = -1; failed = -1; status = 'LOCK_TIMEOUT'; silentSkip = $false }) -Label 'Logic') } }
            Complete-LockedGate
            $busy = @($script:Emitted | Where-Object { $_ -match '^BUSY ' })
            Assert-True 'D6 the locked suite ends BUSY at exit 7' ($script:GateVerdict -eq 'BUSY' -and $script:GateCode -eq 7) "verdict=$($script:GateVerdict) code=$($script:GateCode)"
            Assert-True 'D6 exactly one BUSY line names the live slot holder' ($busy.Count -eq 1 -and $busy[0] -match "^BUSY site=locked holder=slot pid=$($peer.Id) ") ($busy -join ' | ')
            Assert-True 'D6 the BUSY line names the -RetryOnly step' ($busy.Count -eq 1 -and $busy[0] -match 'next="re-invoke with -RetryOnly') ($busy -join ' | ')
        } finally {
            $script:RuntimeSlotRecordRoot = $savedSlots
            if (-not $peer.HasExited) { $peer.Kill($true) }
        }
    }

    # D7: the .runsettings rows (Initialize-Runsettings/Get-EngineProbe): a missing file is generated from the tracked
    # template with the resolved GODOT_BIN, an existing file is never touched, a missing template is a FAILED status,
    # and the engine probe pairs the csproj SDK with the engine's version (or reports it inconclusive).
    Invoke-Case 'D7' {
        $fn = ''
        try { $fn = Get-FunctionText (Join-Path $scriptsDir 'TwinCheckout.ps1') @('Initialize-Runsettings') } catch { }
        # A function built from text has no file, so its $PSScriptRoot is empty; pin the loader's home (the scripts dir)
        # into the extracted body so Initialize-Runsettings resolves ..\hooks\session_context_loader.py.
        if ($fn) { $fn = $fn.Replace('$PSScriptRoot', "'" + ($scriptsDir -replace "'", "''") + "'") }
        $probeFn = ''
        try { $probeFn = Get-FunctionText $gatePath @('Get-EngineProbe', 'Format-Ver') } catch { }
        Assert-True 'D7 the modules define Initialize-Runsettings and Get-EngineProbe' ([bool]$fn -and [bool]$probeFn)
        if (-not $fn -or -not $probeFn) { return }
        . ([scriptblock]::Create($fn))
        . ([scriptblock]::Create($probeFn))
        # PowerShell runs a .cmd only on Windows; elsewhere the engine stub is an executable sh script.
        if ($IsWindows) {
            $stub = Join-Path $work 'd7-godot.cmd'
            [IO.File]::WriteAllText($stub, "@echo off`r`necho 4.7.1.stable.mono.official`r`nexit /b 0`r`n", [System.Text.ASCIIEncoding]::new())
        } else {
            $stub = Join-Path $work 'd7-godot'
            [IO.File]::WriteAllText($stub, "#!/bin/sh`necho 4.7.1.stable.mono.official`nexit 0`n", [System.Text.ASCIIEncoding]::new())
            [IO.File]::SetUnixFileMode($stub, [IO.UnixFileMode]'UserRead, UserWrite, UserExecute')
        }
        $savedBin = $env:GODOT_BIN
        try {
            $env:GODOT_BIN = $stub
            $r1 = Join-Path $work 'rs-generated'
            New-Item -ItemType Directory -Force -Path $r1 | Out-Null
            [IO.File]::WriteAllText((Join-Path $r1 '.runsettings.template'), "<RunSettings><RunConfiguration><EnvironmentVariables><GODOT_BIN>{{GODOT_BIN}}</GODOT_BIN></EnvironmentVariables></RunConfiguration></RunSettings>`n")
            Assert-True 'D7 a missing .runsettings is generated from the tracked template' ((Initialize-Runsettings -Repo $r1) -eq 'generated' -and
                [IO.File]::ReadAllText((Join-Path $r1 '.runsettings')).Contains($stub)) (Get-Content (Join-Path $r1 '.runsettings') -Raw -ErrorAction SilentlyContinue)
            $r2 = Join-Path $work 'rs-existing'
            New-Item -ItemType Directory -Force -Path $r2 | Out-Null
            $sentinel = '<RunSettings><!-- keep --></RunSettings>'
            [IO.File]::WriteAllText((Join-Path $r2 '.runsettings'), $sentinel)
            Assert-True 'D7 an existing .runsettings is untouched and reports no generation' ((Initialize-Runsettings -Repo $r2) -eq '' -and [IO.File]::ReadAllText((Join-Path $r2 '.runsettings')) -eq $sentinel)
            $r3 = Join-Path $work 'rs-failed'
            New-Item -ItemType Directory -Force -Path $r3 | Out-Null
            $status = Initialize-Runsettings -Repo $r3
            Assert-True 'D7 a generator with no template reports FAILED and the reason' ($status -like 'FAILED*' -and $status -match 'template') $status
            [IO.File]::WriteAllText((Join-Path $r1 '{{PROJECT_NAME}}.csproj'), '<Project Sdk="Godot.NET.Sdk/4.7.1"></Project>')
            $repo = $r1
            $probe = Get-EngineProbe
            Assert-True 'D7 the engine probe pairs the csproj SDK with the engine version' ($probe.ok -and $probe.sdk -eq '4.7.1' -and $probe.engine -eq '4.7.1') ($probe | ConvertTo-Json -Compress)
            Remove-Item -LiteralPath (Join-Path $r1 '.runsettings') -Force
            $probe = Get-EngineProbe
            Assert-True 'D7 no .runsettings leaves the engine inconclusive, never OK' (-not $probe.ok -and -not $probe.engine) ($probe | ConvertTo-Json -Compress)
        } finally { $env:GODOT_BIN = $savedBin }
    }

    # ================================================================== Part G: the real gate in a stubbed fixture
    if (@($selected | Where-Object { $_ -match '^G' }).Count -gt 0) {
    $fix = Join-Path $work 'fx'
    $fxRepo = Join-Path $fix 'checkout'
    $fxScripts = Join-Path $fxRepo '.claude\scripts'
    $fxBin = Join-Path $fix 'bin'
    $fxTemp = Join-Path $fix 'temp'
    $fxRec = Join-Path $fxTemp 'harness-activity\runtime-slots'
    $scope = "gh-$PID-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    $slotPrefix = "Global\$scope-slot"
    $ckPrefix = "Global\$scope-ck-"
    foreach ($d in @($fxScripts, $fxBin, $fxTemp, $fxRec, (Join-Path $fxRepo 'Tests\Integration\SegA'), (Join-Path $fxRepo 'Tests\Integration\SegB'),
                     (Join-Path $fxRepo 'Tests\Integration\SegC'))) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    # The gate's own sibling imports, followed from its text: every script the gate (or a script it loads) names through
    # `Join-Path $PSScriptRoot/$scripts '<name>'` is copied, so a newly dot-sourced module no longer strands this fixture.
    # The three entry points the fixture replaces with its own stubs are not modules and stay out; a referenced path
    # with a separator is a hook/tool copy owned elsewhere.
    function Get-FixtureScriptClosure {
        param([string] $Root)
        $stubbed = @('verify.ps1', 'run_test_suite.ps1', 'run_integration_batched.ps1')
        $seen = [System.Collections.Generic.List[string]]::new()
        $queue = [System.Collections.Generic.Queue[string]]::new()
        $queue.Enqueue('regression_gate.ps1')
        while ($queue.Count -gt 0) {
            $name = $queue.Dequeue()
            if ($seen.Contains($name) -or $stubbed -contains $name) { continue }
            $path = Join-Path $Root $name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
            $seen.Add($name) | Out-Null
            if ($name -notlike '*.ps1') { continue }
            $text = [System.IO.File]::ReadAllText($path)
            foreach ($m in [regex]::Matches($text, 'Join-Path \$(?:PSScriptRoot|scripts) ''([^'']+)''')) {
                $ref = $m.Groups[1].Value
                if (-not ($ref.Contains('\') -or $ref.Contains('/'))) { $queue.Enqueue($ref) }
            }
        }
        return $seen
    }
    foreach ($name in Get-FixtureScriptClosure -Root $scriptsDir) {
        Copy-Item -LiteralPath (Join-Path $scriptsDir $name) -Destination (Join-Path $fxScripts $name) -Force
    }
    $fxTools = Join-Path $fxRepo '.claude\tools'
    New-Item -ItemType Directory -Force -Path $fxTools | Out-Null
    Copy-Item -LiteralPath (Join-Path (Split-Path $scriptsDir -Parent) 'tools\cli_usage.py') -Destination $fxTools
    # V15 requires present mandatory coverage; successful stubs keep these held-pool cases isolated.
    $fxHooks = Join-Path $fxRepo '.claude\hooks'
    New-Item -ItemType Directory -Force -Path $fxHooks | Out-Null
    foreach ($name in @('tres_nullstrip_guard.py', 'tool_cascade_audit.py', 'tres_script_strip_guard.py',
                       'test_suite_gate_coverage_guard.py', 'duplicate_test_double_guard.py', 'refcounted_free_guard.py')) {
        [System.IO.File]::WriteAllText((Join-Path $fxHooks $name), "def main():`n    return 0`n", [System.Text.UTF8Encoding]::new($false))
    }
    $fxProc = Join-Path $fxScripts 'GodotProcess.ps1'
    $lockAnchor = "function Test-InteractiveSessionLocked {`n"
    $tp = [System.IO.File]::ReadAllText($fxProc)
    if (-not $tp.Contains($lockAnchor)) { throw 'fixture patch anchor missing in GodotProcess.ps1: Test-InteractiveSessionLocked' }
    $plant = $lockAnchor + "    if (`$env:HARNESS_SESSION_LOCKED -eq '1') { return `$true }`n    if (`$env:HARNESS_SESSION_LOCKED -eq '0') { return `$false }`n"
    $snapshot = Get-FunctionText $fxProc @('Get-ProcSnapshot')
    # This proof's own ancestry, read once from the Win32_Process rows production reads: .NET's Process.Parent can stop
    # short of an ancestor those rows still name (under the test runner G1/G8 then saw no owner root), and a filtered
    # CIM query per ancestor costs more than the whole-host scan it replaces.
    $hostRows = @{}
    if ($IsWindows) { foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) { $hostRows[[int] $p.ProcessId] = $p } }
    $callerChain = [System.Collections.Generic.List[object]]::new()
    for ($cur = $hostRows[[int] $PID]; $cur -and $callerChain.Count -lt 32; $cur = $hostRows[[int] $cur.ParentProcessId]) {
        if (@($callerChain | Where-Object { $_.ProcessId -eq [int] $cur.ProcessId }).Count) { break }
        $callerChain.Add([ordered]@{ ProcessId = [int] $cur.ProcessId; ParentProcessId = [int] $cur.ParentProcessId; Name = [string] $cur.Name
                                     CommandLine = [string] $cur.CommandLine; ExecutablePath = [string] $cur.ExecutablePath
                                     CreationTicks = $(if ($cur.CreationDate) { $cur.CreationDate.Ticks } else { 0 }) })
    }
    $callerChainPath = Join-Path $fix 'caller_chain.json'
    [System.IO.File]::WriteAllText($callerChainPath, (ConvertTo-Json -InputObject @($callerChain) -Depth 3), [System.Text.UTF8Encoding]::new($false))
    # The fixture's own processes are fresh descendants of this proof, so Process.Parent walks them; at this proof the
    # walk takes the planted rows. A launcher that already exited ends the walk, as its absent row ends production's.
    $fixtureSnapshot = @'
function Get-ProcSnapshot {
    $caller = @{}
    foreach ($r in @(Get-Content -LiteralPath '__CALLER_CHAIN__' -Raw | ConvertFrom-Json)) {
        $caller[[int] $r.ProcessId] = [pscustomobject]@{
            ProcessId = [int] $r.ProcessId; ParentProcessId = [int] $r.ParentProcessId; Name = $r.Name
            CommandLine = $r.CommandLine; ExecutablePath = $r.ExecutablePath
            CreationDate = $(if ([long] $r.CreationTicks) { [datetime]::new([long] $r.CreationTicks, 'Local') } else { $null })
        }
    }
    $map = @{}
    for ($p = Get-Process -Id $PID; $p -and -not $map.ContainsKey($p.Id); $p = $parent) {
        if ($caller.ContainsKey($p.Id)) { foreach ($k in $caller.Keys) { $map[$k] = $caller[$k] }; break }
        $parent = $null
        try { $parent = $p.Parent } catch { }
        try {
            $map[$p.Id] = [pscustomobject]@{
                ProcessId = $p.Id; ParentProcessId = $(if ($parent) { $parent.Id } else { 0 })
                Name = $p.Name; CommandLine = ''; ExecutablePath = ''; CreationDate = $p.StartTime
            }
        } catch { break }
    }
    $map
}
'@.Replace('__CALLER_CHAIN__', ($callerChainPath -replace "'", "''"))
    [System.IO.File]::WriteAllText($fxProc, $tp.Replace($lockAnchor, $plant).Replace($snapshot, $fixtureSnapshot), [System.Text.UTF8Encoding]::new($false))
    $fxRoute = Join-Path $fxScripts 'CommentOnlyGate.ps1'
    $routeAnchor = "function Get-CommentOnlyRoute {`n    param([string] `$Repo, `$Tree, [string] `$LedgerDir)`n"
    $rt = [System.IO.File]::ReadAllText($fxRoute)
    if (-not $rt.Contains($routeAnchor)) { throw 'fixture patch anchor missing in CommentOnlyGate.ps1: Get-CommentOnlyRoute' }
    $routeHold = $routeAnchor + "    `$rh = Join-Path `$env:GH_FIX 'route_hold'; `$rd = (Get-Date).AddSeconds(90)`n    while ((Test-Path -LiteralPath `$rh) -and (Get-Date) -lt `$rd) { Start-Sleep -Milliseconds 200 }`n"
    [System.IO.File]::WriteAllText($fxRoute, $rt.Replace($routeAnchor, $routeHold), [System.Text.UTF8Encoding]::new($false))
    $fxSlots = Join-Path $fxScripts 'RuntimeSlots.ps1'
    $t = [System.IO.File]::ReadAllText($fxSlots)
    foreach ($pair in @(@("`$script:RuntimeSlotNamePrefix = 'Global\gdunit4-{{PROJECT_NAME}}-runlock'", "`$script:RuntimeSlotNamePrefix = '$slotPrefix'"),
                        @("`$script:CheckoutLockPrefix = 'Global\gdunit4-runlock-'", "`$script:CheckoutLockPrefix = '$ckPrefix'"),
                        @("`$script:RuntimeSlotRecordRoot = ''", "`$script:RuntimeSlotRecordRoot = '$fxRec'"),
                        @("`$script:RuntimeSlotOverrideFile = ''", "`$script:RuntimeSlotOverrideFile = '$(Join-Path $fix 'runtime-slots')'"))) {
        if (-not $t.Contains($pair[0])) { throw "fixture patch anchor missing in RuntimeSlots.ps1: $($pair[0])" }
        $t = $t.Replace($pair[0], $pair[1])
    }
    [System.IO.File]::WriteAllText($fxSlots, $t, [System.Text.UTF8Encoding]::new($false))
    # The batcher must never be invoked by a gate.
    [System.IO.File]::WriteAllText((Join-Path $fxScripts 'run_integration_batched.ps1'), "Set-Content -LiteralPath '$(Join-Path $fix 'BATCHER_INVOKED')' -Value `"`$args`"`nexit 0`n")

    $stubWrapper = @'
[CmdletBinding()]
param([string] $Repo, [string] $Filter, [string] $Label, [int] $TimeoutMs = 480000, [string] $ResultsRoot = '', [switch] $IgnoreEditor,
      [switch] $HeldCheckout, [switch] $RecompilePass, [string] $Verbosity = 'quiet', [string] $RunMode = '')
$ErrorActionPreference = 'Stop'
$repo = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$fix = Split-Path $repo -Parent
. (Join-Path $PSScriptRoot 'RuntimeSlots.ps1')
$rec = Read-SlotRecord -RecordDir (Get-RuntimeSlotRecordDir) -Name (Get-CheckoutRecordName -Repo $repo)
$denials = @(if ($HeldCheckout) { Get-HeldCheckoutDenials -Repo $repo -RecompilePass:$RecompilePass })
$plan = @{}
$planP = Join-Path $fix 'stub_plan.json'
if (Test-Path $planP) { $plan = Get-Content -LiteralPath $planP -Raw | ConvertFrom-Json -AsHashtable }
$spec = if ($plan.ContainsKey($Label)) { [string]$plan[$Label] } else { 'green:10' }
$mode, $n = $spec -split ':', 2
$n = if ($n) { [int]$n } else { 10 }
$callDir = Join-Path $fix 'stub_calls'
New-Item -ItemType Directory -Force -Path $callDir | Out-Null
[ordered]@{ label = $Label; filter = $Filter; held = [bool]$HeldCheckout; recompilePass = [bool]$RecompilePass; resultsRoot = $ResultsRoot
            ignoreEditor = [bool]$IgnoreEditor; ownerRootPid = [string]$env:HARNESS_OWNER_ROOT_PID; denials = @($denials)
            built = [bool]($rec -and $rec -isnot [string] -and $rec.built); recompiled = [bool]($rec -and $rec -isnot [string] -and $rec.recompiled)
            pid = $PID; at = (Get-Date).ToUniversalTime().ToString('o'); mode = $mode } |
    ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $callDir "$Label-$PID.json")
if ($denials.Count -gt 0) { Write-Output "STATUS=HELD_REFUSED  label=$Label  unmet=$($denials -join ',')"; exit 122 }
if ($mode -eq 'refuse') { Write-Output "STATUS=HELD_REFUSED  label=$Label  unmet=planted"; exit 122 }
if ($mode -eq 'locked') { Write-Output "STATUS=LOCK_TIMEOUT  label=$Label  lock=runtime-slots  (a peer held runtime-slots > 1ms)"; exit 125 }
Write-Output 'LOCKWAIT_MS=0'
if ($mode -eq 'hang') { Write-Output "STATUS=HANG  label=$Label  elapsed=1s  cap=${TimeoutMs}ms  attempts=2"; exit 124 }
$dir = Join-Path $ResultsRoot $Label
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$trx = Join-Path $dir "$Label.trx"
[System.IO.File]::WriteAllText($trx, '<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results /></TestRun>')
Write-Output "TRX=$trx"
if ($mode -eq 'ignored') { Write-Output "STATUS=SKIP_RECOMPILE_IGNORED  label=$Label  trx=$trx"; exit 127 }
$passed = $n; $failed = 0
if ($mode -in @('red', 'redsilent')) { $failed = 1 }
if ($mode -eq 'zero') { $passed = 0 }
$code = if ($failed -gt 0) { 1 } else { 0 }
Write-Output "STATUS=DONE  label=$Label  exit=$code  elapsed=1.0s  attempts=1"
Write-Output ("{0}!  - Failed:     {1}, Passed:     {2}, Skipped:     0, Total:     {3}" -f $(if ($failed) { 'Failed' } else { 'Passed' }), $failed, $passed, ($passed + $failed))
if ($failed) { Write-Output "FAILED_TEST=Stub.$Label.Fails"; Write-Output "FAILED_MSG=Stub.$Label.Fails :: planted failure in $Label" }
if ($mode -in @('silent', 'redsilent')) { Write-Output 'WARN=SILENT_SKIP_SIGNATURE  signature=executor-connect  (persisted after retry -- results INVALID, counts prove nothing)' }
exit $code
'@
    [System.IO.File]::WriteAllText((Join-Path $fxScripts 'run_test_suite.ps1'), $stubWrapper, [System.Text.UTF8Encoding]::new($false))

    $dotnetStub = @'
$fix = $env:GH_FIX
Set-Content -LiteralPath (Join-Path $fix 'build_started') -Value "$args"
$deadline = (Get-Date).AddSeconds(90)
while ((Test-Path -LiteralPath (Join-Path $fix 'build_hold')) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
Write-Output 'stub build output'
$codeP = Join-Path $fix 'build_exit'
exit $(if (Test-Path -LiteralPath $codeP) { [int](Get-Content -LiteralPath $codeP -Raw).Trim() } else { 0 })
'@
    [System.IO.File]::WriteAllText((Join-Path $fxBin 'dotnet.ps1'), $dotnetStub, [System.Text.UTF8Encoding]::new($false))
    $gitStub = @'
$fix = $env:GH_FIX
$command = $args -join ' '
switch -Wildcard ($command) {
    '* status *' {
        if ($command -like '* status --porcelain -- Tests/*' -and (Test-Path (Join-Path $fix 'status-fail'))) { exit 1 }
        $wip = Join-Path $fix 'test-wip'
        if (Test-Path $wip) { Get-Content -LiteralPath $wip }
    }
    '* diff *' {
        if (Test-Path (Join-Path $fix 'diff-fail')) { exit 1 }
        $patch = Join-Path $fix 'deleted.patch'
        if ($command -like '*--unified=*' -and (Test-Path $patch)) { Get-Content -LiteralPath $patch }
    }
    '*unreadable*' { exit 1 }
    '*rev-parse HEAD' { 'b' * 40 }
    '*branch --show-current' { 'fixture-branch' }
    default { 'a' * 40 }
}
exit 0
'@
    [IO.File]::WriteAllText((Join-Path $fxBin 'git.ps1'), $gitStub)
    $godotStub = Join-Path $fxBin 'godot.ps1'
    [IO.File]::WriteAllText($godotStub, @'
if ('--import' -in $args) {
    $fix = $env:GH_FIX
    if (Test-Path (Join-Path $fix 'late-test-wip')) { Set-Content -LiteralPath (Join-Path $fix 'test-wip') -Value ' M Tests/Logic/Peer.cs' }
    $code = Join-Path $fix 'import_exit'
    if (Test-Path $code) { exit [int](Get-Content -LiteralPath $code -Raw) }
}
'4.7.1.stable.mono.official'
exit 0
'@)
    [System.IO.File]::WriteAllText((Join-Path $fxRepo '.runsettings'), "<RunSettings><RunConfiguration><EnvironmentVariables><GODOT_BIN>$godotStub</GODOT_BIN></EnvironmentVariables></RunConfiguration></RunSettings>")
    [System.IO.File]::WriteAllText((Join-Path $fxRepo '{{PROJECT_NAME}}.csproj'), ('<Project Sdk="Godot.NET.Sdk/4.7.1"><PackageReference Include="gdUnit4.api" Version="{0}" /></Project>' -f $gatePin))
    $baselineJson = [ordered]@{
        schema_version = 1; description = 'fixture'; updated_at = '2026-01-01T00:00:00Z'
        updated_on_commit = ('a' * 40); updated_on_branch = 'main'
        suites = [ordered]@{ Logic = [ordered]@{ passed = 10; failed = 0 }; Integration = [ordered]@{ passed = 30; failed = 0 }; Sanity = [ordered]@{ passed = 10; failed = 0 } }
        silent_skip_sentinels = [ordered]@{ Logic_min = 1; Integration_min = 1; Sanity_min = 1 }
        drift_thresholds = [ordered]@{ warn_ratio = 0.9; hard_fail_ratio = 0.7 }
    } | ConvertTo-Json -Depth 5
    # Reservations far above the unit durations: the gate runs every unit whatever the elapsed time.
    $manifestJson = [ordered]@{
        schema_version = 3; description = 'fixture'; updated_at = '2026-01-01T00:00:00Z'
        units = [ordered]@{ SegA = [ordered]@{ seconds = 50; tests = 10 }; SegB = [ordered]@{ seconds = 50; tests = 10 }; SegC = [ordered]@{ seconds = 50; tests = 10 } }
        batch_wall = [ordered]@{ Integration_B1 = [ordered]@{ wall = 100000; consecutive_failures = 0 }; Integration_B2 = [ordered]@{ wall = 100000; consecutive_failures = 0 }
                                 Integration_B3 = [ordered]@{ wall = 100000; consecutive_failures = 0 } }
    } | ConvertTo-Json -Depth 5
    # A baseline-ratchet case reads only its suite's row; one Integration batch (10 s units, all under the 60 s target)
    # keeps its pool at two children instead of five. The 30-test count is unchanged.
    $manifestCoalesced = [ordered]@{
        schema_version = 3; description = 'fixture'; updated_at = '2026-01-01T00:00:00Z'
        units = [ordered]@{ SegA = [ordered]@{ seconds = 10; tests = 10 }; SegB = [ordered]@{ seconds = 10; tests = 10 }; SegC = [ordered]@{ seconds = 10; tests = 10 } }
        batch_wall = [ordered]@{ Integration_B1 = [ordered]@{ wall = 100000; consecutive_failures = 0 } }
    } | ConvertTo-Json -Depth 5

    $savedEnv = @{}
    foreach ($k in @('TEMP', 'TMP', 'PATH', 'GH_FIX', 'HARNESS_OWNER_ROOT_PID', 'HARNESS_OWNER_ROOT_START', 'HARNESS_GATE_RUN')) { $savedEnv[$k] = [System.Environment]::GetEnvironmentVariable($k, 'Process') }

    function Reset-Fixture {
        param([hashtable] $Plan = @{}, [int] $BuildExit = 0, [switch] $HoldBuild, [switch] $BadManifest, [string] $Quarantine = '', [switch] $Coalesced)
        foreach ($f in @('stub_plan.json', 'build_started', 'build_hold', 'build_exit', 'BATCHER_INVOKED', 'deleted.patch', 'test-wip', 'late-test-wip', 'diff-fail', 'status-fail', 'import_exit', 'route_hold')) { Remove-Item -LiteralPath (Join-Path $fix $f) -Force -ErrorAction SilentlyContinue }
        $adaptDir = Join-Path $fxRepo '.claude\skills\project_subsystems'
        Remove-Item -LiteralPath (Join-Path $adaptDir 'adaptation.json') -Force -ErrorAction SilentlyContinue
        if ($Quarantine) {
            New-Item -ItemType Directory -Force -Path $adaptDir | Out-Null
            @{ test_quarantine_filter = $Quarantine } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $adaptDir 'adaptation.json') -Encoding utf8
        }
        Remove-Item -LiteralPath (Join-Path $fix 'stub_calls') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $fxRepo 'TestResults') -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $fxRepo '.claude\scratch') -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $fxRec -Filter 'checkout-*.json' -ErrorAction SilentlyContinue | Remove-Item -Force
        [System.IO.File]::WriteAllText((Join-Path $fxRepo 'Tests\regression_baseline.json'), $baselineJson)
        [System.IO.File]::WriteAllText((Join-Path $fxRepo 'Tests\integration_batch_durations.json'), $(if ($BadManifest) { '{ "units": ' } elseif ($Coalesced) { $manifestCoalesced } else { $manifestJson }))
        $Plan | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $fix 'stub_plan.json')
        if ($BuildExit) { Set-Content -LiteralPath (Join-Path $fix 'build_exit') -Value "$BuildExit" }
        if ($HoldBuild) { Set-Content -LiteralPath (Join-Path $fix 'build_hold') -Value 'x' }
    }

    function Start-FixtureGate {
        param([switch] $Detach, [switch] $StaticOnly, [int] $WaitSec = 0, [switch] $Reuse, $OwnerRoot)
        $env:TEMP = $fxTemp; $env:TMP = $fxTemp; $env:GH_FIX = $fix
        $env:PATH = "$fxBin$([System.IO.Path]::PathSeparator)$($savedEnv['PATH'])"
        Remove-Item Env:\HARNESS_OWNER_ROOT_PID, Env:\HARNESS_OWNER_ROOT_START, Env:\HARNESS_GATE_RUN -ErrorAction SilentlyContinue
        if ($OwnerRoot) { $env:HARNESS_OWNER_ROOT_PID = "$($OwnerRoot.RootPid)"; $env:HARNESS_OWNER_ROOT_START = $OwnerRoot.RootStart }
        try {
            $out = Join-Path $fix "gate-$([guid]::NewGuid().ToString('N').Substring(0, 6)).out"
            $p = Start-Process $Pwsh.FilePath -PassThru @HiddenWindow -RedirectStandardOutput $out -RedirectStandardError "$out.err" `
                -ArgumentList (@($Pwsh.Lead) + @('-NoProfile', '-File', (Join-Path $fxScripts 'regression_gate.ps1'), '-Repo', $fxRepo, '-IgnoreEditor', '-WaitSec', "$WaitSec") + @(if (-not $Reuse) { '-NoReuse' }) + @(if ($Detach) { '-Detach' }) + @(if ($StaticOnly) { '-StaticOnly' }))
            $script:Owned.Add($p)
            [pscustomobject]@{ Proc = $p; Out = $out }
        } finally {
            foreach ($k in $savedEnv.Keys) {
                if ($null -eq $savedEnv[$k]) { Remove-Item "Env:\$k" -ErrorAction SilentlyContinue } else { [System.Environment]::SetEnvironmentVariable($k, $savedEnv[$k], 'Process') }
            }
        }
    }

    function Wait-Gate {
        param($G, [int] $Seconds = 240)
        if (-not $G.Proc.WaitForExit($Seconds * 1000)) { $G.Proc.Kill($true); throw "fixture gate did not finish in ${Seconds}s: $(Get-Content $G.Out -Raw -ErrorAction SilentlyContinue)" }
        $G.Proc.WaitForExit()
        [pscustomobject]@{ Exit = $G.Proc.ExitCode; Text = [string](Get-Content -LiteralPath $G.Out -Raw -ErrorAction SilentlyContinue)
                           Err = [string](Get-Content -LiteralPath "$($G.Out).err" -Raw -ErrorAction SilentlyContinue); GatePid = $G.Proc.Id }
    }

    function Get-StubCalls {
        $d = Join-Path $fix 'stub_calls'
        if (-not (Test-Path $d)) { return @() }
        @(Get-ChildItem -LiteralPath $d -Filter '*.json' | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } | Sort-Object at)
    }

    function Get-Detail { param($R) if ($R.Text -match '(?m)^DETAIL=(.+)$') { [string](Get-Content -LiteralPath $Matches[1].Trim() -Raw -ErrorAction SilentlyContinue) } else { '' } }

    # A fixture lock taker: a child that dot-sources the patched RuntimeSlots.ps1 and tries the checkout lock.
    function Test-FixtureLock {
        param([ValidateSet('ordinary', 'held')] [string] $Kind, [int] $TimeoutMs = 1500)
        $o = Join-Path $fix "lock-$Kind-$([guid]::NewGuid().ToString('N').Substring(0, 6)).json"
        $call = if ($Kind -eq 'held') { "Enter-HeldCheckout -Repo '$fxRepo' -TimeoutMs $TimeoutMs" } else { "Enter-RunLocks -Repo '$fxRepo' -Label probe -TimeoutMs $TimeoutMs" }
        $cmd = "`$env:TEMP='$fxTemp'; . '$fxSlots'; `$l = $call; [ordered]@{ acquired = [bool]`$l.Acquired; blocker = [string]`$l.Blocker; abandoned = [bool]`$l.Abandoned } | ConvertTo-Json -Compress | Set-Content -LiteralPath '$o'; " +
               $(if ($Kind -eq 'held') { "if (`$l.Acquired) { Exit-HeldCheckout -Lease `$l }" } else { 'Exit-RunLocks -Lease $l' })
        $p = Start-Owned $cmd "lock-$Kind"
        [void]$p.WaitForExit(30000)
        if (Test-Path -LiteralPath $o) { Get-Content -LiteralPath $o -Raw | ConvertFrom-Json } else { $null }
    }

    $ckRecord = { Get-ChildItem -LiteralPath $fxRec -Filter 'checkout-*.json' -ErrorAction SilentlyContinue }

    # A fixture lock holder: a child that holds the fixture checkout lock through Enter-RunLocks (so it writes the holder
    # record, as every lock taker does) until a release file appears. The proof kills it only to abandon the mutex.
    function Start-FixtureHolder {
        param([string] $Label = 'planted-holder')
        $g = [guid]::NewGuid().ToString('N').Substring(0, 6)
        $rel = Join-Path $fix "holder-$g.release"; $pidFile = Join-Path $fix "holder-$g.pid"
        $mutexFile = Join-Path $fix "holder-$g.mutex"
        $cmd = "`$env:TEMP='$fxTemp'; . '$fxSlots'; `$l = Enter-RunLocks -Repo '$fxRepo' -Label '$Label' -TimeoutMs 5000; if (-not `$l.Acquired) { exit 3 }; " +
               "Get-CheckoutLockName -Repo '$fxRepo' | Set-Content -LiteralPath '$mutexFile'; Set-Content -LiteralPath '$pidFile' -Value `$PID; " +
               "while (-not (Test-Path -LiteralPath '$rel') -and (Get-Process -Id $PID -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 100 }; Exit-RunLocks -Lease `$l"
        $p = Start-Owned $cmd "holder-$g"
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $pidFile) -and -not $p.HasExited) { Start-Sleep -Milliseconds 100 }
        if (-not (Test-Path -LiteralPath $pidFile)) { throw "fixture holder did not take the lock: $(Get-Content (Join-Path $work "holder-$g.err") -Raw -ErrorAction SilentlyContinue)" }
        [pscustomobject]@{ Proc = $p; Release = $rel; Pid = $p.Id; MutexName = (Get-Content -LiteralPath $mutexFile -Raw).Trim() }
    }
    function Stop-FixtureHolder { param($H) if ($H -and -not $H.Proc.HasExited) { Set-Content -LiteralPath $H.Release -Value 'x'; [void]$H.Proc.WaitForExit(15000) } }

    # Waits until the gate's OUT matches $Pattern (or the gate exits); returns the OUT text.
    function Wait-GateLine {
        param($G, [string] $Pattern, [int] $Seconds = 60)
        $deadline = (Get-Date).AddSeconds($Seconds)
        $text = ''
        while ((Get-Date) -lt $deadline) {
            $text = [string](Get-Content -LiteralPath $G.Out -Raw -ErrorAction SilentlyContinue)
            if ($text -match $Pattern -or $G.Proc.HasExited) { break }
            Start-Sleep -Milliseconds 200
        }
        $text
    }

    # True when the named fixture mutex can be taken right now (an abandoned one counts as free).
    function Test-FixtureMutexFree {
        param([string] $Name)
        $m = New-RunLock $Name
        try {
            $got = $false
            try { $got = $m.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
            if ($got) { $m.ReleaseMutex() }
            $got
        } finally { $m.Dispose() }
    }

    # G1: a green gate. The lock spans the build (two lock takers planted during it block), every child is a held child
    # admitted without denial, Sanity is the one -RecompilePass run, the run dir is gate-<runId>, the owner root passes to
    # every child, every Integration unit is submitted although the standalone budget would skip them, and release frees
    # the lock cleanly.
    Invoke-Case 'G1' {
        Reset-Fixture -HoldBuild
        $g = Start-FixtureGate
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline -and -not (Test-Path (Join-Path $fix 'build_started')) -and -not $g.Proc.HasExited) { Start-Sleep -Milliseconds 200 }
        Assert-True 'G1 setup: the gate reached its build' (Test-Path (Join-Path $fix 'build_started')) (Get-Content $g.Out -Raw -ErrorAction SilentlyContinue)
        $rec = @(& $ckRecord)
        $recObj = if ($rec.Count -eq 1) { Get-Content -LiteralPath $rec[0].FullName -Raw | ConvertFrom-Json } else { $null }
        Assert-True 'G1 during the build the gate holds the checkout record, built not yet stamped' ($recObj -and [int]$recObj.holderPid -eq $g.Proc.Id -and $recObj.built -eq $false) ($recObj | ConvertTo-Json -Compress)
        $ord = Test-FixtureLock -Kind ordinary
        Assert-True 'G1 an ordinary wrapper run planted during the build blocks on the checkout lock' ($ord -and -not $ord.acquired -and $ord.blocker -eq 'checkout') ($ord | ConvertTo-Json -Compress)
        $held = Test-FixtureLock -Kind held
        Assert-True 'G1 a second Enter-HeldCheckout planted during the build blocks' ($held -and -not $held.acquired) ($held | ConvertTo-Json -Compress)
        Remove-Item -LiteralPath (Join-Path $fix 'build_hold') -Force
        $r = Wait-Gate $g
        $calls = Get-StubCalls
        Assert-True 'G1 the gate passes' ($r.Exit -eq 0 -and $r.Text -match '(?m)^VERDICT=PASS') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
        Assert-True 'G1 built and recompiled are stamped, in that order' ($r.Text -match '(?s)BUILD=OK.*HELD stamp=built.*SUITE Sanity.*HELD stamp=recompiled') $r.Text
        $labels = @($calls | ForEach-Object label)
        Assert-True 'G1 Sanity, Logic and every Integration unit ran' ((($labels | Sort-Object) -join ',') -eq 'Integration_B1,Integration_B2,Integration_B3,Logic,Sanity') ($labels -join ',')
        Assert-True 'G1 Sanity ran first, alone, as the one -RecompilePass child' ($calls.Count -gt 0 -and $calls[0].label -eq 'Sanity' -and $calls[0].recompilePass -and
            @($calls | Where-Object { $_.recompilePass }).Count -eq 1) ($calls | ConvertTo-Json -Compress)
        Assert-True 'G1 every child is held, IgnoreEditor, and admitted with no denial' (@($calls | Where-Object { -not $_.held -or -not $_.ignoreEditor -or @($_.denials).Count -gt 0 }).Count -eq 0) ($calls | ConvertTo-Json -Compress)
        Assert-True 'G1 every pool child after Sanity saw built and recompiled' (@($calls | Where-Object { $_.label -ne 'Sanity' -and (-not $_.built -or -not $_.recompiled) }).Count -eq 0)
        $trxDir = if ($r.Text -match '(?m)^TRX_DIR=(.+)$') { $Matches[1].Trim() } else { '' }
        Assert-True 'G1 the run dir is TestResults/runs/gate-<runId> with the gate''s own runId' ($trxDir -and (Split-Path $trxDir -Leaf) -match "^gate-\d{8}-\d{6}-$($r.GatePid)$" -and
            (Split-Path $trxDir -Parent) -eq (Join-Path $fxRepo 'TestResults\runs')) "trxDir=$trxDir"
        Assert-True 'G1 every child writes under that run dir' (@($calls | Where-Object { $_.resultsRoot -ne $trxDir }).Count -eq 0)
        $root = Get-OwnerRoot
        $want = if ($root) { [string]$root.RootPid } else { '' }
        Assert-True 'G1 every child receives the gate''s owner root' (@($calls | Where-Object { [string]$_.ownerRootPid -ne $want }).Count -eq 0) "want=$want got=$(($calls | ForEach-Object ownerRootPid) -join ',')"
        Assert-True 'G1 a gate whose reservations are far above the unit durations submits every unit and prints no BUDGET_EXCEEDED' ($r.Text -notmatch 'BUDGET_EXCEEDED' -and @($calls | Where-Object { $_.label -like 'Integration_B*' }).Count -eq 3)
        Assert-True 'G1 the gate never invokes the batcher' (-not (Test-Path (Join-Path $fix 'BATCHER_INVOKED')))
        Assert-True 'G1 release removes the checkout record' (@(& $ckRecord).Count -eq 0)
        $after = Test-FixtureLock -Kind ordinary
        Assert-True 'G1 release frees the checkout lock (acquired, not abandoned)' ($after -and $after.acquired -and -not $after.abandoned) ($after | ConvertTo-Json -Compress)
        Assert-True 'G1 the pool prints the run-dir REAP line' ($r.Text -match '(?m)^REAP site=pool:main killed=0 scope=run-dir')
        Assert-True 'G1 a one-unit pool prints width=1, not the slot count' ($r.Text -match '(?m)^POOL phase=sanity units=1 width=1 ') (($r.Text -split "`n" | Where-Object { $_ -match '^POOL ' }) -join ' | ')
        # What a default green run prints and omits: asserted here on the one default run, so no case spends a second gate run on it.
        Assert-True 'G1 the import runs once, after the build and before Sanity' ($r.Text -match '(?s)BUILD=OK.*IMPORT_GATE=PASS.*POOL phase=sanity' -and ([regex]::Matches($r.Text, '(?m)^IMPORT_GATE=')).Count -eq 1) $r.Text
        Assert-True 'G1 an unlocked console emits no SESSION line' ($r.Text -notmatch '(?m)^SESSION=') $r.Text
        Assert-True 'G1 with no adaptation value the gate prints no QUARANTINE line and adds no exclusion' ($r.Text -notmatch '(?m)^QUARANTINE ' -and
            @($calls | Where-Object { ([string]$_.filter) -match 'Quarantined' }).Count -eq 0) $r.Text
        Assert-True 'G1 a quiet checkout prints its acquired line (an absent line means the site never ran)' ($r.Text -match '(?m)^WAIT site=entry acquired=1 waited=0s abandoned=0\r?$') $r.Text
    }

    # G2: a HANG unit retries only through the second held pool; a RED unit never retries; the gate never re-invokes
    # itself or the batcher with -RetryOnly. (Which rows Select-RetryUnits picks is D4's decision; this case proves the
    # pool's second phase and the child handoff.)
    Invoke-Case 'G2' {
        Reset-Fixture -Plan @{ Integration_B1 = 'hang'; Integration_B2 = 'red:9' }
        $r = Wait-Gate (Start-FixtureGate)
        $calls = Get-StubCalls
        $retry = @($calls | Where-Object { $_.label -eq 'Integration_B1_retry' })
        Assert-True 'G2 the HANG unit is retried once, as Integration_B1_retry' ($retry.Count -eq 1) (($calls | ForEach-Object label) -join ',')
        Assert-True 'G2 the retry child starts held with no denial' ($retry.Count -eq 1 -and $retry[0].held -and -not $retry[0].recompilePass -and @($retry[0].denials).Count -eq 0)
        $firstPool = @($calls | Where-Object { $_.label -in 'Logic', 'Integration_B1', 'Integration_B2', 'Integration_B3' })
        Assert-True 'G2 the retry runs in a second pool call, after every first-pool unit started' ($retry.Count -eq 1 -and @($firstPool | Where-Object { [datetime]$_.at -gt [datetime]$retry[0].at }).Count -eq 0 -and
            $r.Text -match '(?m)^POOL phase=retry units=1')
        Assert-True 'G2 the RED unit is not retried' (@($calls | Where-Object { $_.label -eq 'Integration_B2_retry' }).Count -eq 0)
        Assert-True 'G2 the gate never re-invokes itself or the batcher with -RetryOnly' ($r.Text -notmatch 'retry=1 reason=BUDGET_EXCEEDED' -and -not (Test-Path (Join-Path $fix 'BATCHER_INVOKED')))
        Assert-True 'G2 the red unit makes the gate FAIL, with its failure message in the detail' ($r.Exit -eq 1 -and (Get-Detail $r) -match 'Stub\.Integration_B2\.Fails :: planted failure') "exit=$($r.Exit)`n$($r.Text)"
    }

    # G3: a failed build leaves no built stamp and launches nothing.
    Invoke-Case 'G3' {
        Reset-Fixture -BuildExit 1
        $r = Wait-Gate (Start-FixtureGate)
        Assert-True 'G3 a failed build blocks the gate' ($r.Exit -eq 4 -and $r.Text -match 'BUILD=FAIL') "exit=$($r.Exit)`n$($r.Text)"
        Assert-True 'G3 a failed build leaves no built stamp' ($r.Text -notmatch 'HELD stamp=built')
        Assert-True 'G3 no child runs' (@(Get-StubCalls).Count -eq 0)
        Assert-True 'G3 the checkout record is removed' (@(& $ckRecord).Count -eq 0)
    }

    Invoke-Case 'G4' {
        # One live run at the no-recompile-evidence flow: the silent-skip signature tiers INVALID, no recompiled stamp
        # lands and the pool never starts. The zero-test input reaches the same tier (D2).
        Reset-Fixture -Plan @{ Sanity = 'silent'; Sanity_retry = 'silent' }
        $r = Wait-Gate (Start-FixtureGate)
        $calls = Get-StubCalls
        Assert-True 'G4 Sanity with the silent-skip signature leaves no recompiled stamp' ($r.Text -notmatch 'HELD stamp=recompiled') $r.Text
        Assert-True 'G4 Sanity with the silent-skip signature stops the gate before the pool' (@($calls | Where-Object { $_.label -notlike 'Sanity*' }).Count -eq 0 -and $r.Exit -ne 0) "exit=$($r.Exit) calls=$(($calls | ForEach-Object label) -join ',')"
        Assert-True 'G4 every Sanity attempt before the stamp is a -RecompilePass child' (@($calls | Where-Object { -not $_.recompilePass }).Count -eq 0)
        Assert-True 'G4 the detail names the missing recompile evidence' ((Get-Detail $r) -match 'recompile')
    }

    # G5: a red Sanity with executed tests stamps recompiled and ends the gate FAIL before the pool.
    Invoke-Case 'G5' {
        Reset-Fixture -Plan @{ Sanity = 'red:9' }
        $r = Wait-Gate (Start-FixtureGate)
        $calls = Get-StubCalls
        Assert-True 'G5 Sanity reds with executed tests stamp recompiled' ($r.Text -match 'HELD stamp=recompiled') $r.Text
        Assert-True 'G5 no pool unit runs after a red Sanity' (@($calls | Where-Object { $_.label -notlike 'Sanity*' }).Count -eq 0 -and $r.Text -notmatch '(?m)^POOL phase=main') (($calls | ForEach-Object label) -join ',')
        Assert-True 'G5 the skip lines name the red Sanity' ($r.Text -match '(?m)^SUITE Logic skipped=1 reason=sanity-red' -and $r.Text -match '(?m)^SUITE Integration skipped=1 reason=sanity-red') $r.Text
        Assert-True 'G5 the gate reports FAIL for the Sanity red' ($r.Exit -eq 1) "exit=$($r.Exit)"
    }

    # G6: a throw inside the held region ends in a verdict and a result record (a detached caller polls for VERDICT=), and
    # still releases: the record is removed and the lock released, not abandoned.
    Invoke-Case 'G6' {
        Reset-Fixture -BadManifest
        $r = Wait-Gate (Start-FixtureGate)
        Assert-True 'G6 setup: the gate threw inside the held region' ($r.Text -match 'HELD stamp=recompiled' -and $r.Text -match '(?m)^GATE_ERROR ') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
        Assert-True 'G6 the throw ends in VERDICT=INCOMPLETE, exit 6' ($r.Exit -eq 6 -and $r.Text -match '(?m)^VERDICT=INCOMPLETE\r?$') "exit=$($r.Exit)`n$($r.Text)"
        $resFiles = @(Get-ChildItem -LiteralPath (Join-Path $fxRepo '.claude\scratch\gate_ledger') -Filter '*.result.json' -ErrorAction SilentlyContinue)
        $res = if ($resFiles.Count -eq 1) { Get-Content -LiteralPath $resFiles[0].FullName -Raw | ConvertFrom-Json } else { $null }
        Assert-True 'G6 the throw writes the result record' ($res -and $res.status -eq 'INCOMPLETE' -and [int]$res.exitCode -eq 6) "records=$($resFiles.Count) $($res | ConvertTo-Json -Compress)"
        Assert-True 'G6 the detail names the harness fault and the exception' ((Get-Detail $r) -match 'Harness fault — the gate threw') (Get-Detail $r)
        Assert-True 'G6 the throw still removes the checkout record' (@(& $ckRecord).Count -eq 0)
        $after = Test-FixtureLock -Kind ordinary
        Assert-True 'G6 the throw still releases the checkout lock (not abandoned)' ($after -and $after.acquired -and -not $after.abandoned) ($after | ConvertTo-Json -Compress)
        Assert-True 'G6 release reaps the run dir first' ($r.Text -match '(?m)^REAP site=held-exit killed=\d+ scope=run-dir')
    }

    # G7 (R1): a refused held child and an ignored recompile opt-out end the gate INCOMPLETE, naming the status and the
    # child's wrapper.out; neither is queued.
    Invoke-Case 'G7' {
        Reset-Fixture -Plan @{ Logic = 'refuse'; Integration_B3 = 'ignored' }
        $r = Wait-Gate (Start-FixtureGate)
        $d = Get-Detail $r
        Assert-True 'G7 the gate ends INCOMPLETE (exit 6)' ($r.Exit -eq 6 -and $r.Text -match '(?m)^VERDICT=INCOMPLETE') "exit=$($r.Exit)`n$($r.Text)"
        Assert-True 'G7 the detail names HELD_REFUSED with the Logic wrapper.out' ($d -match 'HELD_REFUSED' -and $d -match [regex]::Escape('\Logic\wrapper.out')) $d
        Assert-True 'G7 the detail names SKIP_RECOMPILE_IGNORED with its wrapper.out' ($d -match 'SKIP_RECOMPILE_IGNORED' -and $d -match [regex]::Escape('\Integration_B3\wrapper.out')) $d
        Assert-True 'G7 a harness fault is never BUSY' ($r.Text -notmatch '(?m)^VERDICT=BUSY')
    }

    # G8: a -Detach gate's children record the launcher's owner root. The launcher exits within about a second, so the
    # detached child's own walk reaches no session; it must keep the live pair the launcher passed.
    Invoke-Case 'G8' {
        Reset-Fixture
        $root = Get-OwnerRoot
        $planted = $null
        if (-not $root) {
            # No session in this proof's chain (the land runner service, or a `timeout` wrapper that breaks the walk):
            # plant this process as the root through the pair the gate falls back to.
            $root = [pscustomobject] @{ RootPid = $PID; RootStart = (Get-ProofStamp $PID) }
            $planted = $root
        }
        $l = Wait-Gate (Start-FixtureGate -Detach -OwnerRoot $planted) -Seconds 60
        $outP = if ($l.Text -match '(?m)^OUT=(.+)$') { $Matches[1].Trim() } else { '' }
        Assert-True 'G8 setup: the launcher detached and exited' ($l.Exit -eq 0 -and $outP) "exit=$($l.Exit)`n$($l.Text)`n$($l.Err)"
        if (-not $outP) { return }
        $dg = if ($l.Text -match '(?m)^DETACHED pid=(\d+)') { [int]$Matches[1] } else { 0 }
        $dp = if ($dg) { Get-Process -Id $dg -ErrorAction SilentlyContinue } else { $null }
        if ($dp) { $script:Owned.Add($dp) }
        $deadline = (Get-Date).AddSeconds(240)
        $text = ''
        while ((Get-Date) -lt $deadline) {
            $text = [string](Get-Content -LiteralPath $outP -Raw -ErrorAction SilentlyContinue)
            if ($text -match '(?m)^VERDICT=') { break }
            Start-Sleep -Milliseconds 500
        }
        Assert-True 'G8 the detached gate passes' ($text -match '(?m)^VERDICT=PASS') $text
        $calls = Get-StubCalls
        Assert-True 'G8 every child of the detached gate receives the launcher''s owner root' ($calls.Count -ge 5 -and @($calls | Where-Object { [string]$_.ownerRootPid -ne [string]$root.RootPid }).Count -eq 0) "want=$($root.RootPid) got=$(($calls | ForEach-Object ownerRootPid) -join ',')"
        if ($dp) { [void]$dp.WaitForExit(30000) }
    }

    # G10: a -StaticOnly gate takes the checkout lease around its build, so no ad-hoc run rebuilds under a held pool's
    # children, and holds it through its build, DOCS and the comment-only route check.
    Invoke-Case 'G10' {
        Reset-Fixture -HoldBuild
        $g = Start-FixtureGate -StaticOnly
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline -and -not (Test-Path (Join-Path $fix 'build_started')) -and -not $g.Proc.HasExited) { Start-Sleep -Milliseconds 200 }
        Assert-True 'G10 setup: the static-only gate reached its build' (Test-Path (Join-Path $fix 'build_started')) (Get-Content $g.Out -Raw -ErrorAction SilentlyContinue)
        $ord = Test-FixtureLock -Kind ordinary
        Assert-True 'G10 a run planted during the static-only build blocks on the checkout lock' ($ord -and -not $ord.acquired -and $ord.blocker -eq 'checkout') ($ord | ConvertTo-Json -Compress)
        Set-Content -LiteralPath (Join-Path $fix 'route_hold') -Value 'x'
        Remove-Item -LiteralPath (Join-Path $fix 'build_hold') -Force
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline -and (Get-Content $g.Out -Raw -ErrorAction SilentlyContinue) -notmatch 'DOCS=OK' -and -not $g.Proc.HasExited) { Start-Sleep -Milliseconds 200 }
        Assert-True 'G10 setup: the static-only gate passed DOCS' ((Get-Content $g.Out -Raw -ErrorAction SilentlyContinue) -match 'DOCS=OK') (Get-Content $g.Out -Raw -ErrorAction SilentlyContinue)
        $ord = Test-FixtureLock -Kind ordinary
        Assert-True 'G10 a run planted after DOCS, at the route check, blocks on the checkout lock' ($ord -and -not $ord.acquired -and $ord.blocker -eq 'checkout') ($ord | ConvertTo-Json -Compress)
        Remove-Item -LiteralPath (Join-Path $fix 'route_hold') -Force
        $r = Wait-Gate $g
        Assert-True 'G10 the static-only gate passes with no suite child' ($r.Exit -eq 0 -and $r.Text -match '(?m)^VERDICT=STATIC_PASS' -and @(Get-StubCalls).Count -eq 0) "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
        Assert-True 'G10 the lease is stamped built before the docs check' ($r.Text -match '(?s)BUILD=OK.*HELD stamp=built.*DOCS=OK') $r.Text
        Assert-True 'G10 release removes the checkout record' (@(& $ckRecord).Count -eq 0)
        $after = Test-FixtureLock -Kind ordinary
        Assert-True 'G10 release frees the checkout lock' ($after -and $after.acquired -and -not $after.abandoned) ($after | ConvertTo-Json -Compress)
    }

    # G17: a gate that finds its checkout busy waits on the lock, holding nothing (activity record says waiter, no runtime
    # slot, no checkout record), and prints who holds it and for how long; it takes the lock once the holder leaves, says
    # kind=gate, and then builds.
    Invoke-Case 'G17' {
        Reset-Fixture
        $h = Start-FixtureHolder
        $g = Start-FixtureGate -StaticOnly -WaitSec 60
        $text = Wait-GateLine $g '(?m)^WAIT site=entry .*holder='
        $preBuild = Test-Path (Join-Path $fix 'build_started')
        $actP = Join-Path $fxTemp "harness-activity\gate-$($g.Proc.Id).json"
        $act = if (Test-Path -LiteralPath $actP) { Get-Content -LiteralPath $actP -Raw | ConvertFrom-Json } else { $null }
        Assert-True 'G17 while waiting, the activity record says kind=waiter' ($act -and $act.kind -eq 'waiter') "record=$($act | ConvertTo-Json -Compress)"
        $slotNames = @($slotPrefix) + @(1..7 | ForEach-Object { "$slotPrefix-slot$_" })
        Assert-True 'G17 while waiting, every runtime slot is free' (@($slotNames | Where-Object { -not (Test-FixtureMutexFree $_) }).Count -eq 0)
        $mine = @(& $ckRecord | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } | Where-Object { [int]$_.holderPid -eq $g.Proc.Id })
        Assert-True 'G17 while waiting, no checkout record names the gate' ($mine.Count -eq 0)
        Stop-FixtureHolder $h
        # The record is written at acquisition and removed when the gate ends, so it is read while the gate runs: a
        # one-shot read after the build can land after a fast fixture gate has already finished and cleared it.
        $deadline = (Get-Date).AddSeconds(60)
        $act = $null
        while ((Get-Date) -lt $deadline -and -not $g.Proc.HasExited) {
            try { $act = Get-Content -LiteralPath $actP -Raw | ConvertFrom-Json } catch { $act = $null }
            if ($act -and $act.kind -eq 'gate') { break }
            Start-Sleep -Milliseconds 50
        }
        Assert-True 'G17 once it holds the lease, the record says kind=gate' ($act -and $act.kind -eq 'gate') "record=$($act | ConvertTo-Json -Compress)"
        $r = Wait-Gate $g
        Assert-True 'G17 the wait line names the holder: kind, pid, label, how long it has held, time left' (
            $text -match "(?m)^WAIT site=entry .*holder=run pid=$($h.Pid) .*held=\d+s label=`"planted-holder`" .*left=\d+s") $text
        Assert-True 'G17 the gate built nothing while the holder held the lock' (-not $preBuild)
        Assert-True 'G17 the wait ends in an acquired line, then the build, then a pass' (
            $r.Text -match '(?s)WAIT site=entry acquired=1 waited=\d+s abandoned=0.*HELD site=enter acquired=1.*BUILD=OK' -and $r.Exit -eq 0 -and $r.Text -match '(?m)^VERDICT=STATIC_PASS') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
        Assert-True 'G17 the build started only after the holder released' (Test-Path (Join-Path $fix 'build_started'))
    }

    # G18: a wait that outlasts -WaitSec ends BUSY (exit 7) naming the holder, writes the ledger and the detail, runs nothing
    # and leaves the holder alone.
    Invoke-Case 'G18' {
        Reset-Fixture
        $h = Start-FixtureHolder
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Wait-Gate (Start-FixtureGate -WaitSec 4) -Seconds 90
        $sw.Stop()
        $d = Get-Detail $r
        $ledger = @(Get-ChildItem -LiteralPath (Join-Path $fxRepo '.claude\scratch\gate_ledger') -Filter '*.result.json' -ErrorAction SilentlyContinue)
        $res = if ($ledger.Count -eq 1) { Get-Content -LiteralPath $ledger[0].FullName -Raw | ConvertFrom-Json } else { $null }
        $busy = @($r.Text -split "`n" | Where-Object { $_ -match '^BUSY ' })
        Assert-True 'G18 the gate ends VERDICT=BUSY with exit 7' ($r.Exit -eq 7 -and $r.Text -match '(?m)^VERDICT=BUSY\r?$') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
        Assert-True 'G18 exactly one BUSY line, naming the holder and the next step' ($busy.Count -eq 1 -and
            $busy[0] -match "^BUSY site=entry holder=run pid=$($h.Pid) .*waited=\d+s .*next=`"re-invoke after pid $($h.Pid) ends") ($busy -join ' | ')
        Assert-True 'G18 the detail has the Busy section naming the holder' ($d -match '## Busy — no verdict' -and $d -match "pid=$($h.Pid)") $d
        Assert-True 'G18 the ledger records BUSY, exit 7' ($res -and $res.status -eq 'BUSY' -and [int]$res.exitCode -eq 7) "records=$($ledger.Count) $($res | ConvertTo-Json -Compress)"
        Assert-True 'G18 no build started and no child ran' (-not (Test-Path (Join-Path $fix 'build_started')) -and @(Get-StubCalls).Count -eq 0)
        Assert-True 'G18 the wait is bounded by -WaitSec plus one poll' ($sw.Elapsed.TotalSeconds -lt (4 + 5 + 25)) "elapsed=$($sw.Elapsed.TotalSeconds)s"
        Assert-True 'G18 the holder is still alive: the gate only reads' (-not $h.Proc.HasExited)
        Stop-FixtureHolder $h
    }

    # G20: the holder dies mid-wait (the proof kills its own fixture process; the gate never kills): the abandoned mutex is an
    # acquisition and the gate goes on.
    Invoke-Case 'G20' {
        $fxGate = Join-Path $fxScripts 'regression_gate.ps1'
        $gateSource = [System.IO.File]::ReadAllText($fxGate)
        $take = '$l = Enter-HeldCheckout -Repo $Checkout -Label "gate $runId" -TimeoutMs $takeMs'
        if (-not $gateSource.Contains($take)) { throw 'G20 fixture checkout-take anchor missing' }
        foreach ($lateOpen in @($false, $true)) {
            Reset-Fixture
            $resume = Join-Path $fix 'g20-resume'
            Remove-Item -LiteralPath $resume -Force -ErrorAction SilentlyContinue
            # Force the schedule where the holder dies after the WAIT banner but before the gate opens its handle.
            $pause = "Write-Host 'G20 before-open'; while (-not (Test-Path -LiteralPath '$resume')) { Start-Sleep -Milliseconds 50 }; "
            [System.IO.File]::WriteAllText($fxGate, $(if ($lateOpen) { $gateSource.Replace($take, $pause + $take) } else { $gateSource }))
            $keepAlive = $null
            try {
                $h = Start-FixtureHolder
                # An abandoned named mutex survives only while a handle stays open. The WAIT banner precedes the
                # gate's open, so keep the object alive without acquiring it, even when the gate is descheduled there.
                $keepAlive = New-RunLock $h.MutexName
                $g = Start-FixtureGate -StaticOnly -WaitSec 60
                $pattern = if ($lateOpen) { '(?m)^G20 before-open' } else { '(?m)^WAIT site=entry .*holder=' }
                $text = Wait-GateLine $g $pattern
                if ($text -notmatch $pattern) { throw "G20 lateOpen=$lateOpen did not reach the kill point: $text" }
                $killedAt = Get-Date
                $h.Proc.Kill($true); [void]$h.Proc.WaitForExit(10000)
                Set-Content -LiteralPath $resume -Value 'x'
                $text = Wait-GateLine $g '(?m)^WAIT site=entry acquired=1' 30
                $seenAt = Get-Date
                $r = Wait-Gate $g
                Assert-True "G20 lateOpen=$lateOpen the wait named the holder, then recorded the acquisition as abandoned" (
                    $r.Text -match "(?s)WAIT site=entry .*pid=$($h.Pid) .*WAIT site=entry acquired=1 waited=\d+s abandoned=1") $r.Text
                Assert-True "G20 lateOpen=$lateOpen the gate proceeds to a pass" ($r.Exit -eq 0 -and $r.Text -match '(?m)^VERDICT=STATIC_PASS') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
                Assert-True "G20 lateOpen=$lateOpen the gate noticed the death within one poll" (($seenAt - $killedAt).TotalSeconds -lt (5 + 15)) "seconds=$(($seenAt - $killedAt).TotalSeconds)"
            } finally {
                if ($keepAlive) { $keepAlive.Dispose() }
                Set-Content -LiteralPath $resume -Value 'x'
                [System.IO.File]::WriteAllText($fxGate, $gateSource)
            }
        }
    }

    # G21: a waiter adopts the verdict of the run it waited on (REUSE re-checked after the wait): the fold's value, with no
    # second run.
    Invoke-Case 'G21' {
        Reset-Fixture -HoldBuild
        $a = Start-FixtureGate -Reuse
        $deadline = (Get-Date).AddSeconds(90)
        while ((Get-Date) -lt $deadline -and -not (Test-Path (Join-Path $fix 'build_started')) -and -not $a.Proc.HasExited) { Start-Sleep -Milliseconds 200 }
        $b = Start-FixtureGate -Reuse -WaitSec 120
        $btext = Wait-GateLine $b '(?m)^WAIT site=entry .*holder='
        Remove-Item -LiteralPath (Join-Path $fix 'build_hold') -Force
        $ra = Wait-Gate $a
        $rb = Wait-Gate $b
        $calls = @(Get-StubCalls)
        Assert-True 'G21 setup: the first gate passes' ($ra.Exit -eq 0 -and $ra.Text -match '(?m)^VERDICT=PASS') "exit=$($ra.Exit)`n$($ra.Text)`n$($ra.Err)"
        Assert-True 'G21 the second gate named the first as the holder while it waited' ($btext -match "(?m)^WAIT site=entry .*holder=gate pid=$($ra.GatePid) ") $btext
        Assert-True 'G21 after the wait the second gate adopts the first one''s verdict' (
            $rb.Text -match "(?s)WAIT site=entry acquired=1.*REUSE from=\d{8}-\d{6}-$($ra.GatePid) .*VERDICT=PASS" -and $rb.Exit -eq 0) "exit=$($rb.Exit)`n$($rb.Text)`n$($rb.Err)"
        Assert-True 'G21 only one run of the suites happened' (((@($calls | ForEach-Object label) | Sort-Object) -join ',') -eq 'Integration_B1,Integration_B2,Integration_B3,Logic,Sanity') (($calls | ForEach-Object label) -join ',')
    }

    # G23: -WaitSec 0 probes once. A busy checkout ends BUSY at once and the site still prints its line.
    Invoke-Case 'G23' {
        Reset-Fixture
        $h = Start-FixtureHolder
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $r = Wait-Gate (Start-FixtureGate -WaitSec 0) -Seconds 90
        $sw.Stop()
        Assert-True 'G23 a busy checkout with -WaitSec 0 ends BUSY at once' ($r.Exit -eq 7 -and $r.Text -match '(?m)^VERDICT=BUSY' -and $r.Text -match "(?m)^BUSY site=entry holder=run pid=$($h.Pid) " -and
            $sw.Elapsed.TotalSeconds -lt 30) "exit=$($r.Exit) elapsed=$($sw.Elapsed.TotalSeconds)s`n$($r.Text)`n$($r.Err)"
        Stop-FixtureHolder $h
    }

    # G25: a nonzero Godot exit from the import fails the import gate and names the exit code, even with a clean log.
    Invoke-Case 'G25' {
        Reset-Fixture
        Set-Content -LiteralPath (Join-Path $fix 'import_exit') -Value '7'
        $r = Wait-Gate (Start-FixtureGate)
        Assert-True 'G25 a nonzero import exit prints IMPORT_GATE=FAIL naming the code' ($r.Text -match '(?m)^IMPORT_GATE=FAIL exit=7\r?$' -and $r.Text -notmatch 'IMPORT_GATE=PASS') $r.Text
        Assert-True 'G25 a failed import stops the gate before Sanity' ($r.Exit -ne 0 -and $r.Text -notmatch 'POOL phase=sanity') "exit=$($r.Exit)`n$($r.Text)"
    }

    # G26: a Sanity that is red AND carries the silent-skip signature skips Logic and Integration once, under one reason.
    Invoke-Case 'G26' {
        Reset-Fixture -Plan @{ Sanity = 'redsilent:9'; Sanity_retry = 'redsilent:9' }
        $r = Wait-Gate (Start-FixtureGate)
        Assert-True 'G26 each skipped suite names exactly one skip reason' (
            ([regex]::Matches($r.Text, '(?m)^SUITE Logic skipped=1 reason=')).Count -eq 1 -and
            ([regex]::Matches($r.Text, '(?m)^SUITE Integration skipped=1 reason=')).Count -eq 1) $r.Text
    }

    # G27: a checkout with no .runsettings (gitignored, per-checkout) gets one generated from the tracked template before
    # the engine preflight; a generator failure keeps the BLOCKED engine path. (An existing file is D7's row.)
    Invoke-Case 'G27' {
        Reset-Fixture
        $rs = Join-Path $fxRepo '.runsettings'
        $keep = [System.IO.File]::ReadAllText($rs)
        foreach ($name in @('session_context_loader.py', 'session_context_loader_godot.py', '_optional_hooks.py', '_bounded_run.py', '_claude_scope.py', '_checkout_root.py', '_git_run.py', '_hook_parts.py', '_hook_state.py', '_msys.py', '_owner_text.py', '_prompt_provenance.py', '_win_path.py', 'amend_delivery.py')) { Copy-Item -LiteralPath (Join-Path (Split-Path $scriptsDir) "hooks\$name") -Destination (Join-Path $fxHooks $name) -Force }
        # The loader imports adaptation at module level; the fixture tools dir must carry it.
        Copy-Item -LiteralPath (Join-Path (Split-Path $scriptsDir) 'tools\adaptation.py') -Destination $fxTools -Force
        [System.IO.File]::WriteAllText((Join-Path $fxRepo '.runsettings.template'), "<RunSettings><RunConfiguration><EnvironmentVariables><GODOT_BIN>{{GODOT_BIN}}</GODOT_BIN></EnvironmentVariables></RunConfiguration></RunSettings>")
        $savedBin = $env:GODOT_BIN
        try {
            $env:GODOT_BIN = $godotStub
            Remove-Item -LiteralPath $rs -Force
            $r = Wait-Gate (Start-FixtureGate -StaticOnly)
            Assert-True 'G27 a missing .runsettings is generated, named in one line, and the engine preflight passes' (
                (@($r.Text -split "`n" | Where-Object { $_ -match '^RUNSETTINGS=generated from=\.runsettings\.template' }).Count -eq 1) -and
                $r.Text -match '(?m)^ENGINE=OK ver=4.7.1' -and $r.Text -notmatch 'ENGINE=INCONCLUSIVE' -and (Test-Path $rs) -and
                ([System.IO.File]::ReadAllText($rs)).Contains($godotStub)) "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"

            Remove-Item -LiteralPath $rs -Force
            Remove-Item -LiteralPath (Join-Path $fxRepo '.runsettings.template') -Force
            Reset-Fixture
            $r3 = Wait-Gate (Start-FixtureGate -StaticOnly)
            Assert-True 'G27 a generator failure keeps the BLOCKED engine path and names the reason' (
                $r3.Exit -eq 4 -and $r3.Text -match '(?m)^RUNSETTINGS=FAILED' -and $r3.Text -match 'ENGINE=INCONCLUSIVE' -and $r3.Text -match '(?m)^VERDICT=BLOCKED') "exit=$($r3.Exit)`n$($r3.Text)"
        } finally {
            $env:GODOT_BIN = $savedBin
            [System.IO.File]::WriteAllText($rs, $keep)
            Remove-Item -LiteralPath (Join-Path $fxRepo '.runsettings.template') -Force -ErrorAction SilentlyContinue
        }
    }

    # G29: a locked console is advisory. The gate emits SESSION=LOCKED, names the one windowed unit exposed to the lock
    # slowdown, and still builds. (The lock state emits and adds detail only; G1 proves suites run when unlocked.)
    Invoke-Case 'G29' {
        Reset-Fixture
        $env:HARNESS_SESSION_LOCKED = '1'
        try { $r = Wait-Gate (Start-FixtureGate -StaticOnly) } finally { $env:HARNESS_SESSION_LOCKED = '0' }
        Assert-True 'G29 a locked console emits SESSION=LOCKED and does not block the gate' ($r.Exit -eq 0 -and $r.Text -match '(?m)^SESSION=LOCKED' -and $r.Text -match '(?m)^VERDICT=STATIC_PASS') "exit=$($r.Exit)`n$($r.Text)"
        Assert-True 'G29 the build still runs' ($r.Text -match 'BUILD=OK')
        Assert-True 'G29 the detail names the Render unit as the only one exposed' ((Get-Detail $r) -match 'only the windowed Render unit') (Get-Detail $r)
    }

    # G30: renderer-tagged classes leave every headless unit's filter and run as one windowed Render unit. A planted
    # [RequiresRenderer] class stands for the real ones; Render counts in the totals like any batch. A planted
    # adaptation quarantine is printed and reaches every batch and Render child, never Sanity or Logic. (The plan rows
    # themselves are the base's IntegrationBatches proof and D3's quarantine row; this case proves the held child.)
    Invoke-Case 'G30' {
        $tagFile = Join-Path $fxRepo 'Tests\Integration\SegA\RenderPlanted.cs'
        $fqn = '{{PROJECT_NAME}}.Tests.Integration.SegA.RenderPlantedTests'
        [System.IO.File]::WriteAllText($tagFile, "namespace {{PROJECT_NAME}}.Tests.Integration.SegA;`n[TestSuite]`n[RequiresRenderer]`npublic partial class RenderPlantedTests { }`n")
        try {
            $q = 'FullyQualifiedName!~Quarantined.Suite'
            Reset-Fixture -Quarantine $q
            $r = Wait-Gate (Start-FixtureGate)
            $calls = Get-StubCalls
            $excl = "FullyQualifiedName!~$fqn."
            Assert-True 'G30 the gate passes' ($r.Exit -eq 0 -and $r.Text -match '(?m)^VERDICT=PASS') "exit=$($r.Exit)`n$($r.Text)`n$($r.Err)"
            $headless = @($calls | Where-Object { $_.label -ne 'Render' })
            Assert-True 'G30 Sanity, Logic and every Integration batch exclude the tagged class' ($headless.Count -ge 5 -and @($headless | Where-Object { ([string]$_.filter).IndexOf($excl) -lt 0 }).Count -eq 0) (($headless | ForEach-Object { "$($_.label)=$($_.filter)" }) -join ' | ')
            $render = @($calls | Where-Object { $_.label -eq 'Render' })
            Assert-True 'G30 exactly one Render unit runs, selecting the tagged class and excluding nothing but the quarantine' ($render.Count -eq 1 -and ([string]$render[0].filter).IndexOf("FullyQualifiedName~$fqn.") -ge 0 -and ([string]$render[0].filter).Replace("&$q", '') -notmatch '!~') (($render | ForEach-Object filter) -join ' | ')
            Assert-True 'G30 the gate prints the quarantine filter' ($r.Text -match ('(?m)^QUARANTINE filter=' + [regex]::Escape($q) + '\r?$')) $r.Text
            $quarantined = @($calls | Where-Object { $_.label -like 'Integration_B*' -or $_.label -eq 'Render' })
            Assert-True 'G30 every Integration batch and the Render unit carry the quarantine; Sanity and Logic carry none' ($quarantined.Count -ge 2 -and
                @($quarantined | Where-Object { ([string]$_.filter).IndexOf($q) -lt 0 }).Count -eq 0 -and
                @($calls | Where-Object { $_.label -in 'Sanity', 'Logic' -and ([string]$_.filter).IndexOf($q) -ge 0 }).Count -eq 0) (($calls | ForEach-Object { "$($_.label)=$($_.filter)" }) -join ' | ')
            Assert-True 'G30 the Render unit is a held pool child like the batches' ($render.Count -eq 1 -and $render[0].held -and $render[0].ignoreEditor -and $render[0].resultsRoot -eq $headless[0].resultsRoot)
            Assert-True 'G30 the Render unit has a BATCH line and its tests count in the Integration total' ($r.Text -match '(?m)^BATCH \d+/4 label=Render status=GREEN passed=10' -and $r.Text -match 'TOTAL passed=40 ') $r.Text
        } finally { Remove-Item -LiteralPath $tagFile -Force -ErrorAction SilentlyContinue }
    }

    # G31: Logic runs as partitions in the pool. A unit that ran no tests retries every Logic unit once and the retry can
    # pass. (The partition filters are D1's rows; the silent and short-sum tiers are D2's.)
    Invoke-Case 'G31' {
        $made = foreach ($n in 'AI', 'Combat') { $d = Join-Path $fxRepo "Tests\Logic\$n"; New-Item -ItemType Directory -Force -Path $d | Out-Null; $d }
        $gdm = Join-Path $fxScripts 'gate_domain_map.json'
        try {
            [System.IO.File]::WriteAllText($gdm, '{ "schema_version": 1, "domains": {}, "logic_groups": [["AI"], ["Combat"]] }')
            Reset-Fixture -Coalesced -Plan @{ Logic_1 = 'green:4'; Logic_2 = 'zero'; Logic_rest = 'green:2'; Integration_B1 = 'green:30'
                                             Logic_1_retry = 'green:4'; Logic_2_retry = 'green:4'; Logic_rest_retry = 'green:2' }
            $r = Wait-Gate (Start-FixtureGate)
            $calls = Get-StubCalls
            Assert-True 'G31 the Logic units are one per kept group plus the complement, and the gate sums them' ((@($calls | Where-Object { $_.label -like 'Logic*' -and $_.label -notlike '*_retry' } | ForEach-Object label | Sort-Object) -join ',') -eq 'Logic_1,Logic_2,Logic_rest' -and
                $r.Text -match '(?m)^LOGIC units=3 ') "exit=$($r.Exit)`n$($r.Text)"
            Assert-True 'G31 a unit that ran no tests retries every Logic unit once, and the retry can pass' ((@($calls | Where-Object { $_.label -like 'Logic*_retry' } | ForEach-Object label | Sort-Object) -join ',') -eq 'Logic_1_retry,Logic_2_retry,Logic_rest_retry' -and
                $r.Exit -eq 0 -and $r.Text -match '(?m)^SUITE Logic rerun=1 reason=INVALID' -and $r.Text -match '(?m)^SUITE Logic passed=10 ') "exit=$($r.Exit)`n$($r.Text)"
        } finally {
            Remove-Item -LiteralPath $gdm -Force -ErrorAction SilentlyContinue
            foreach ($d in $made) { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }

    function Write-DeletionPatch {
        param([switch] $AddCase)
        $patch = @'
diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs
--- a/Tests/Logic/Cases.cs
+++ b/Tests/Logic/Cases.cs
@@ -1,5 +1,2 @@
 class Cases {
-    [TestCase(1)]
-    [TestCase(2)]
-    public void Gone(int n) { }
 }
'@
        if ($AddCase) {
            $patch = $patch.Replace('@@ -1,5 +1,2 @@', '@@ -1,5 +1,4 @@').Replace("`n }", "`n+    [TestCase(3)]`n+    public void New(int n) { }`n }")
        }
        $patch | Set-Content -LiteralPath (Join-Path $fix 'deleted.patch')
    }

    # G11: the baseline ratchet's one live row, the gate's call site end to end: a clean count drop that committed test
    # deletions explain lowers the baseline and passes, and the green write covers every suite and stamp field. The
    # ratchet's own row decisions -- the PARTIAL band, an unexplained drop, uncommitted test changes at each timing, and
    # an unreadable commit, diff or status -- are planted cases in the base proof `test_regression_gate_ratchet.py`, so
    # they cost no gate run here. The fixture's Integration units coalesce into one batch: the decision reads the Logic
    # row, not the batch count.
    Invoke-Case 'G11' {
        Reset-Fixture -Coalesced -Plan @{ Logic = 'green:8'; Sanity = 'green:11'; Integration_B1 = 'green:30' }
        Write-DeletionPatch
        $bp = Join-Path $fxRepo 'Tests/regression_baseline.json'
        $before = Get-Content -LiteralPath $bp -Raw
        $r = Wait-Gate (Start-FixtureGate)
        $after = Get-Content -LiteralPath $bp -Raw
        $detail = Get-Detail $r
        $writeOk = ($after | ConvertFrom-Json).suites.Logic.passed -eq 8
        Assert-True 'G11 an explained WARN-band drop lowers the baseline and passes' ($r.Exit -eq 0 -and
            @(@('(?m)^VERDICT=PASS', '(?m)^BASELINE action=lowered suite=Logic drop=2 deleted=2 added=0\r?$') |
                Where-Object { $r.Text -notmatch $_ }).Count -eq 0 -and $writeOk)
            "exit=$($r.Exit)`n$($r.Text)`n$detail`n$($r.Err)"
        $b = $after | ConvertFrom-Json
        Assert-True 'G11 the green write covers every suite and every updated field' ($b.suites.Sanity.passed -eq 11 -and
            $b.suites.Integration.passed -eq 30 -and $b.updated_on_commit -eq ('b' * 40) -and
            $b.updated_on_branch -eq 'fixture-branch' -and $b.updated_at -ne '2026-01-01T00:00:00Z') ($b | ConvertTo-Json -Depth 5 -Compress)
    }

    }
} finally {
    foreach ($p in $script:Owned) { try { if (-not $p.HasExited) { $p.Kill($true); [void]$p.WaitForExit(5000) } } catch { } }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Fails -gt 0) { Write-Host "FAILED ($script:Fails)"; exit 1 }
Write-Host 'ALL PASS'
exit 0
