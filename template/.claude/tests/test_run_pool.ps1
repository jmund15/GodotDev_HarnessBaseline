#requires -Version 7
<#
.SYNOPSIS
  Proof for .claude/scripts/RunPool.ps1's run pool: Start-SuiteChild (the one child launcher) and Invoke-RunPool (the
  scheduler). Every case drives the shipped functions with stub children: `pwsh -File` scripts that sleep and print
  canned wrapper output into the pool's own wrapper.out path, so the pool's scheduling, backstop, parsing and cleanup run
  against real processes while no test runner, build or Godot starts.

.RUN
  pwsh -NoProfile -File .claude/tests/test_run_pool.ps1
  pwsh -NoProfile -File .claude/tests/test_run_pool.ps1 -ModulePath <planted RunPool.ps1>
#>
[CmdletBinding()]
param([string] $ModulePath = '')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not $ModulePath) { $ModulePath = Join-Path $repoRoot '.claude\scripts\RunPool.ps1' }
$work = Join-Path ([System.IO.Path]::GetTempPath()) "runpool-test-$PID-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$script:Fails = 0
$script:Launched = [System.Collections.Generic.List[object]]::new()

function Assert-True {
    param([string] $Name, $Condition, [string] $Detail = '')
    if ($Condition) { Write-Host "  PASS  $Name" }
    else { Write-Host "  FAIL  $Name$(if ($Detail) { "`n        $Detail" })"; $script:Fails++ }
}

function Invoke-Case {
    param([string] $Id, [scriptblock] $Body)
    Write-Host "`n$Id"
    try { & $Body } catch { Write-Host "  FAIL  $Id threw: $($_.Exception.Message)"; $script:Fails++ }
}

# A unit plus its stub behaviour: sleep, print the canned lines, exit.
function New-Unit {
    param([string] $Label, [double] $ExpectSec = 1, [int] $SleepMs = 300, [string[]] $Lines = @(), [int] $Exit = 0, [int] $TimeoutMs = 60000)
    @{ label = $Label; filter = "FullyQualifiedName~$Label."; timeoutMs = $TimeoutMs; expectSec = $ExpectSec
       stubSleepMs = $SleepMs; stubLines = $Lines; stubExit = $Exit }
}

function New-Trx {
    param([string] $Name, [switch] $Malformed)
    $p = Join-Path $work "$Name.trx"
    $body = if ($Malformed) { '<TestRun><Results><UnitTest' } else {
        '<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results /></TestRun>' }
    [System.IO.File]::WriteAllText($p, $body)
    $p
}

function Get-GreenLines {
    param([string] $Label, [int] $Passed = 7, [int] $LockMs = 0)
    @("LOCKWAIT_MS=$LockMs", "TRX=$(New-Trx $Label)", "STATUS=DONE  label=$Label  exit=0  elapsed=0.3s  attempts=1",
      "Passed!  - Failed:     0, Passed:     $Passed, Skipped:     0, Total:     $Passed")
}

$stubLauncher = {
    param($u)
    $stub = Join-Path $work ("stub-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    $body = "Start-Sleep -Milliseconds $($u.stubSleepMs)`n" +
        (@($u.stubLines | ForEach-Object { "Write-Output '" + ($_ -replace "'", "''") + "'" }) -join "`n") + "`nexit $($u.stubExit)`n"
    [System.IO.File]::WriteAllText($stub, $body)
    $out = Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $u.label
    New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
    $pw = Get-PwshStart
    $p = Start-Process $pw.FilePath -ArgumentList (@($pw.Lead) + @('-NoProfile', '-File', $stub)) -PassThru -WindowStyle Hidden -RedirectStandardOutput $out
    $script:Launched.Add([pscustomobject]@{ Label = $u.label; Proc = $p; At = [datetime]::UtcNow })
    $p
}

function Get-MaxOverlap {
    param($Records)
    $events = foreach ($r in $Records) {
        [void]$r.Proc.WaitForExit(30000)
        [pscustomobject]@{ t = $r.At; d = 1 }
        [pscustomobject]@{ t = $r.Proc.ExitTime.ToUniversalTime(); d = -1 }
    }
    $cur = 0; $max = 0
    foreach ($e in @($events | Sort-Object t, d)) { $cur += $e.d; if ($cur -gt $max) { $max = $cur } }
    $max
}

function Reset-Launched { $script:Launched.Clear(); $script:RunsRoot = Join-Path $work ("runs-" + [guid]::NewGuid().ToString('N').Substring(0, 6)) }

function Stop-Launched {
    foreach ($r in $script:Launched) {
        try { if (-not $r.Proc.HasExited) { & taskkill.exe '/F' '/T' '/PID' $r.Proc.Id 2>$null | Out-Null } } catch { }
    }
}

try {
    . $ModulePath
    $script:PoolPollMs = 100
    Write-Host "Module: $ModulePath"

    Invoke-Case 'P1 max concurrency never exceeds -MaxParallel' {
        Reset-Launched
        $units = @(1..5 | ForEach-Object { New-Unit -Label "U$_" -SleepMs 1500 -Lines (Get-GreenLines "U$_") })
        $r = Invoke-RunPool -Units $units -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 2
        $max = Get-MaxOverlap $script:Launched
        Assert-True 'P1 at most 2 children ran at once, and 2 did' ($max -eq 2) "max=$max"
        Assert-True 'P1 every unit returns one result' (@($r).Count -eq 5 -and ((@($r | ForEach-Object label) | Sort-Object) -join ',') -eq 'U1,U2,U3,U4,U5') "labels=$(@($r | ForEach-Object label) -join ',')"
        Assert-True 'P1 green children parse GREEN with their counts' (@($r | Where-Object { $_.status -eq 'GREEN' -and $_.passed -eq 7 -and $_.failed -eq 0 }).Count -eq 5) (($r | ForEach-Object { "$($_.label)=$($_.status)" }) -join ' ')
    }

    Invoke-Case 'P2 default width is Get-RuntimeSlotCount capped by $IntraCheckoutWidthMax' {
        Reset-Launched
        $script:RuntimeSlotOverrideFile = Join-Path $work 'no-override-file'
        $IntraCheckoutWidthMax = $null
        $w = Get-RunPoolWidth
        Assert-True 'P2 $IntraCheckoutWidthMax $null means no cap (the default count, source default)' ($w.Count -eq $RuntimeSlotDefault -and $w.Source -eq 'default') "count=$($w.Count) source=$($w.Source)"
        $IntraCheckoutWidthMax = 2
        $w = Get-RunPoolWidth
        Assert-True 'P2 a cap below the slot count is the width, with Source=ceiling' ($w.Count -eq 2 -and $w.Source -eq 'ceiling') "count=$($w.Count) source=$($w.Source)"
        $units = @(1..4 | ForEach-Object { New-Unit -Label "W$_" -SleepMs 1500 -Lines (Get-GreenLines "W$_") })
        $null = Invoke-RunPool -Units $units -ResultsRoot $script:RunsRoot -Launch $stubLauncher
        $max = Get-MaxOverlap $script:Launched
        Assert-True 'P2 a pool without -MaxParallel runs at the capped width' ($max -eq 2) "max=$max"
        $IntraCheckoutWidthMax = $null
        $script:RuntimeSlotOverrideFile = ''
    }

    Invoke-Case 'P3 units start longest-first' {
        Reset-Launched
        $units = @((New-Unit -Label 'Short' -ExpectSec 1 -Lines (Get-GreenLines 'Short')), (New-Unit -Label 'Long' -ExpectSec 9 -Lines (Get-GreenLines 'Long')),
                   (New-Unit -Label 'Mid' -ExpectSec 5 -Lines (Get-GreenLines 'Mid')))
        $null = Invoke-RunPool -Units $units -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        $order = @($script:Launched | ForEach-Object Label) -join ','
        Assert-True 'P3 launch order is Long,Mid,Short' ($order -eq 'Long,Mid,Short') "order=$order"
    }

    Invoke-Case 'P4 a child past the backstop is tree-killed and reported HANG' {
        Reset-Launched
        $saved = $script:PoolBackstopTailMs; $savedExtra = $script:SuiteLockWaitExtraMs; $savedMax = $script:SuiteLockWaitFloorMs
        $script:PoolBackstopTailMs = 300; $script:SuiteLockWaitExtraMs = 0; $script:SuiteLockWaitFloorMs = 0
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $r = Invoke-RunPool -Units @(New-Unit -Label 'Wedged' -SleepMs 60000 -TimeoutMs 200 -Lines (Get-GreenLines 'Wedged')) `
                -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        } finally { $script:PoolBackstopTailMs = $saved; $script:SuiteLockWaitExtraMs = $savedExtra; $script:SuiteLockWaitFloorMs = $savedMax }
        Assert-True 'P4 the wedged unit is HANG' (@($r).Count -eq 1 -and $r[0].status -eq 'HANG') "status=$(@($r | ForEach-Object status) -join ',')"
        Assert-True 'P4 the pool returned well before the child''s own sleep' ($sw.Elapsed.TotalSeconds -lt 20) "elapsed=$($sw.Elapsed.TotalSeconds)"
        Assert-True 'P4 the wedged child is gone' ($script:Launched[0].Proc.HasExited)
        Assert-True 'P4 the backstop tail is a named constant with its measured basis' ((Get-Content $ModulePath -Raw) -match '# basis: A4a tail probe[^\n]*\n#[^\n]*2x margin\.\s*\n\$script:PoolBackstopTailMs = \d+')
    }

    Invoke-Case 'P4b a child inside the wrapper''s legal lifetime (slot wait plus a retry) is not killed' {
        Reset-Launched
        $saved = $script:PoolBackstopTailMs
        $script:PoolBackstopTailMs = 300
        try {
            # Past timeoutMs + tail (500 ms), well inside lock wait + 2 attempts: a slot wait or the wrapper's own retry.
            $r = Invoke-RunPool -Units @(New-Unit -Label 'SlowStart' -SleepMs 2500 -TimeoutMs 200 -Lines (Get-GreenLines 'SlowStart')) `
                -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        } finally { $script:PoolBackstopTailMs = $saved }
        Assert-True 'P4b the slow-starting unit is GREEN, not HANG' (@($r).Count -eq 1 -and $r[0].status -eq 'GREEN') "status=$(@($r | ForEach-Object status) -join ',')"
        $b = Get-SuiteRunBounds -TimeoutMs 200
        Assert-True 'P4b the wrapper''s bounds are one function: lock wait, 2 attempts, lifetime' ($b.MaxAttempts -eq 2 -and $b.LifetimeMs -eq ($b.LockWaitMs + 2 * 200 * $script:SuiteProgressCeilingFactor)) "bounds=$($b | ConvertTo-Json -Compress)"
        # A short unit's slot wait must outlast the longest holder it can queue behind (a 480 s Logic run), not its
        # own cap.
        $short = Get-SuiteRunBounds -TimeoutMs 120000
        Assert-True 'P4b a short unit waits at least one longest suite cap for a slot' ($short.LockWaitMs -ge $script:SuiteLockWaitFloorMs + $script:SuiteLockWaitExtraMs -and $script:SuiteLockWaitFloorMs -ge 480000) "short=$($short | ConvertTo-Json -Compress) max=$script:SuiteLockWaitFloorMs"
        # A holder that retries keeps its slot for every attempt at its cap, so one retrying 480 s holder outlasts a
        # wait of one cap plus the buffer.
        # The holder's worst case is literal: 2 attempts, each at the 480 s fixed-unit cap, each allowed the 3x progress
        # ceiling, plus the 120 s buffer. A holder can run this long while it still writes its log.
        Assert-True 'P4b a waiter outlasts one retrying holder''s worst-case progress-ceiling hold' ($short.LockWaitMs -ge 2 * 480000 * 3 + 120000) "short=$($short | ConvertTo-Json -Compress)"
    }

    Invoke-Case 'P5 incomplete evidence gets a distinct status, never pass or fail' {
        Reset-Launched
        $units = @(
            (New-Unit -Label 'Silent' -Lines @()),
            (New-Unit -Label 'NoTrx' -Lines @('LOCKWAIT_MS=0', "TRX=$(Join-Path $work 'absent.trx')", 'STATUS=DONE  label=NoTrx  exit=0', 'Passed!  - Failed:     0, Passed:     3, Skipped:     0, Total:     3')),
            (New-Unit -Label 'BadTrx' -Lines @('LOCKWAIT_MS=0', "TRX=$(New-Trx 'bad' -Malformed)", 'STATUS=DONE  label=BadTrx  exit=0', 'Passed!  - Failed:     0, Passed:     3, Skipped:     0, Total:     3')),
            (New-Unit -Label 'Skip' -Lines @('LOCKWAIT_MS=0', "TRX=$(New-Trx 'skip')", 'STATUS=DONE  label=Skip  exit=0', 'WARN=SILENT_SKIP_SIGNATURE  signature=executor-connect  (persisted after retry)')))
        $r = Invoke-RunPool -Units $units -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 4
        $by = @{}; foreach ($x in @($r)) { $by[$x.label] = $x.status }
        $st = @('Silent', 'NoTrx', 'BadTrx', 'Skip' | ForEach-Object { $by[$_] })
        Assert-True 'P5 no wrapper output, missing TRX, malformed TRX and silent skip get four distinct statuses' (@($st | Select-Object -Unique).Count -eq 4 -and -not ($st | Where-Object { $_ -in 'GREEN', 'RED' -or -not $_ })) ($st -join ',')
        Assert-True 'P5 the silent-skip signature is SILENT_SKIP' ($by['Skip'] -eq 'SILENT_SKIP') "got=$($by['Skip'])"
    }

    Invoke-Case 'P6 duplicate derived tags throw before any launch' {
        Reset-Launched
        $thrown = $null
        try { $null = Invoke-RunPool -Units @((New-Unit -Label 'Same'), (New-Unit -Label 'Same')) -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 2 }
        catch { $thrown = $_.Exception.Message }
        Assert-True 'P6 the pool throws on a duplicate tag' ($null -ne $thrown -and $thrown -match 'tag') "thrown=$thrown"
        Assert-True 'P6 nothing was launched' ($script:Launched.Count -eq 0) "launched=$($script:Launched.Count)"
    }

    Invoke-Case 'P7 results parse passed, failed, FAILED_TEST, FAILED_MSG and lock wait' {
        Reset-Launched
        $red = @('LOCKWAIT_MS=40', 'LOCKWAIT_MS=2', "TRX=$(New-Trx 'red')", 'STATUS=DONE  label=Red  exit=1  elapsed=0.3s  attempts=1',
                 'Failed!  - Failed:     2, Passed:     5, Skipped:     0, Total:     7',
                 'FAILED_TEST=Ns.A.One', 'FAILED_MSG=Ns.A.One :: Expected 1 but was 2', 'FAILED_TEST=Ns.A.Two', 'FAILED_MSG=Ns.A.Two :: boom')
        $r = Invoke-RunPool -Units @(New-Unit -Label 'Red' -Lines $red -Exit 1) -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        $x = $r[0]
        Assert-True 'P7 a red child is RED with its counts' ($x.status -eq 'RED' -and $x.passed -eq 5 -and $x.failed -eq 2 -and $x.exit -eq 1) "status=$($x.status) p=$($x.passed) f=$($x.failed) exit=$($x.exit)"
        Assert-True 'P7 failedTests are the FAILED_TEST names' ((@($x.failedTests) -join ',') -eq 'Ns.A.One,Ns.A.Two') "got=$(@($x.failedTests) -join ',')"
        Assert-True 'P7 failedMessages carry fqn :: message' ((@($x.failedMessages) -join '|') -eq 'Ns.A.One :: Expected 1 but was 2|Ns.A.Two :: boom') "got=$(@($x.failedMessages) -join '|')"
        Assert-True 'P7 lockWaitMs sums the LOCKWAIT_MS lines' ($x.lockWaitMs -eq 42) "got=$($x.lockWaitMs)"
        Assert-True 'P7 trx is the wrapper''s TRX= path and tag is Get-RunTag of the label' ($x.trx -like '*red.trx' -and $x.tag -eq (Get-RunTag -Label 'Red')) "trx=$($x.trx) tag=$($x.tag)"
        Assert-True 'P7 elapsedSec is measured' ($x.elapsedSec -gt 0) "got=$($x.elapsedSec)"
        Assert-True 'P7 counted says the wrapper printed a counts line' ($x.PSObject.Properties['counted'] -and $x.counted -eq $true) "counted=$(if ($x.PSObject.Properties['counted']) { $x.counted } else { 'absent' })"
        $r2 = Invoke-RunPool -Units @(New-Unit -Label 'NoCounts' -Lines @('LOCKWAIT_MS=0', "TRX=$(New-Trx 'nocounts')", 'STATUS=DONE  label=NoCounts  exit=1') -Exit 1) `
            -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        Assert-True 'P7 a run with no counts line is not counted (the gate''s UNPARSED input)' ($r2[0].PSObject.Properties['counted'] -and $r2[0].counted -eq $false -and $r2[0].passed -eq 0)
    }

    Invoke-Case 'P11 a hashed long label puts wrapper.out and the TRX in one dir' {
        $wrapper = Join-Path (Split-Path $ModulePath) 'run_test_suite.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($wrapper, [ref]$null, [ref]$null)
        $stmts = @($ast.EndBlock.Statements)
        $first = @(0..($stmts.Count - 1) | Where-Object { $stmts[$_].Extent.Text -match '^\$logDir\s*=' })[0]
        $last = @(0..($stmts.Count - 1) | Where-Object { $stmts[$_].Extent.Text -match '^\$runDir\s*=' })[0]
        $code = (@($stmts[$first..$last] | ForEach-Object { $_.Extent.Text })) -join "`n"
        $repo = $script:PoolRepo
        $Label = ('Integration domain slice.' * 12) + 'Tail'
        $ResultsRoot = Join-Path $work 'runs-p11\gate-20260101-000000-1'
        $safe = $null; $runDir = $null
        . ([scriptblock]::Create($code))
        $out = Get-SuiteChildOutPath -ResultsRoot $ResultsRoot -Label $Label
        Assert-True 'P11 setup: the wrapper hashed the label to fit the path cap' ($safe -and $safe.Length -lt ($Label -replace '[^\w.-]', '_').Length) "safe=$safe"
        Assert-True 'P11 the pool''s wrapper.out sits in the wrapper''s run dir (the TRX''s dir)' ((Split-Path $out -Parent) -eq $runDir) "out=$out runDir=$runDir"
        $gp = Get-Content (Join-Path (Split-Path $ModulePath) 'GodotProcess.ps1') -Raw
        Assert-True 'P11 the label-to-dir rule has one home in GodotProcess.ps1' ($gp -match '(?m)^function Get-SafeRunLabel\b' -and
            (Get-Content $ModulePath -Raw) -match 'Get-SafeRunLabel' -and (Get-Content $wrapper -Raw) -match '\$safe\s*=\s*Get-SafeRunLabel')
    }

    Invoke-Case 'P8 a single-unit call returns a one-element array' {
        Reset-Launched
        $r = Invoke-RunPool -Units @(New-Unit -Label 'One' -Lines (Get-GreenLines 'One')) -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 1
        Assert-True 'P8 the result is an array of one, not a bare record' ($r -is [array] -and $r.Count -eq 1 -and $r[0].label -eq 'One') "type=$(if ($null -ne $r) { $r.GetType().Name } else { 'null' })"
    }

    Invoke-Case 'P9 Start-SuiteChild argv' {
        Reset-Launched
        $echo = Join-Path $work 'echo_wrapper.ps1'
        [System.IO.File]::WriteAllText($echo, 'Write-Output ("ARGV=" + ($args -join "|"))')
        $saved = $script:SuiteWrapperPath
        $script:SuiteWrapperPath = $echo
        try {
            $u = New-Unit -Label 'Argv Plain'
            $p = Start-SuiteChild -Unit $u -ResultsRoot $script:RunsRoot; [void]$p.WaitForExit(30000)
            $plain = Get-Content (Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $u.label) -Raw
            $u2 = New-Unit -Label 'ArgvHeld'
            $p = Start-SuiteChild -Unit $u2 -ResultsRoot $script:RunsRoot -Held -RecompilePass; [void]$p.WaitForExit(30000)
            $held = Get-Content (Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $u2.label) -Raw
            $u3 = New-Unit -Label 'ArgvHeldOnly'
            $p = Start-SuiteChild -Unit $u3 -ResultsRoot $script:RunsRoot -Held; [void]$p.WaitForExit(30000)
            $heldOnly = Get-Content (Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $u3.label) -Raw
        } finally { $script:SuiteWrapperPath = $saved }
        $argv = { param($t) if ($t -match 'ARGV=(.*)') { @($Matches[1].Trim() -split '\|') } else { @() } }
        $a = & $argv $plain; $b = & $argv $held; $c = & $argv $heldOnly
        Assert-True 'P9 every child carries -IgnoreEditor' ($a -contains '-IgnoreEditor' -and $b -contains '-IgnoreEditor' -and $c -contains '-IgnoreEditor') "plain=$($a -join ' ') held=$($b -join ' ')"
        Assert-True 'P9 a non-held child carries neither -HeldCheckout nor -RecompilePass' ($a.Count -gt 0 -and $a -notcontains '-HeldCheckout' -and $a -notcontains '-RecompilePass') "plain=$($a -join ' ')"
        Assert-True 'P9 -Held -RecompilePass carries -HeldCheckout -RecompilePass' ($b -contains '-HeldCheckout' -and $b -contains '-RecompilePass') "held=$($b -join ' ')"
        Assert-True 'P9 -Held alone carries -HeldCheckout only' ($c -contains '-HeldCheckout' -and $c -notcontains '-RecompilePass') "heldOnly=$($c -join ' ')"
        Assert-True 'P9 the child gets -Label, -Filter, -TimeoutMs and -ResultsRoot, a spaced label staying one argument' (
            $a -contains '-Label' -and $a[[array]::IndexOf($a, '-Label') + 1] -eq 'Argv Plain' -and $a -contains '-Filter' -and $a -contains '-TimeoutMs' -and
            $a -contains '-ResultsRoot' -and $a[[array]::IndexOf($a, '-ResultsRoot') + 1] -eq $script:RunsRoot) "plain=$($a -join ' ')"
        # The planner decides a unit's mode; the child receives it, so the wrapper never re-derives it from the filter.
        $winUnit = New-Unit -Label 'ArgvWin'; $winUnit.windowed = $true
        $modeWin = @(Get-SuiteChildArgv -Unit $winUnit -ResultsRoot $script:RunsRoot)
        $modeHeadless = @(Get-SuiteChildArgv -Unit (New-Unit -Label 'ArgvHeadless') -ResultsRoot $script:RunsRoot)
        $runModeOf = { param($t) $i = [array]::IndexOf($t, '-RunMode'); if ($i -ge 0) { $t[$i + 1] } else { '' } }
        Assert-True 'P9 a windowed unit forwards -RunMode windowed' ((& $runModeOf $modeWin) -eq 'windowed') "argv=$($modeWin -join ' ')"
        Assert-True 'P9 a unit not marked windowed forwards -RunMode headless' ((& $runModeOf $modeHeadless) -eq 'headless') "argv=$($modeHeadless -join ' ')"
        $thrown = $null
        try { $null = Start-SuiteChild -Unit (New-Unit -Label 'Bad') -ResultsRoot $script:RunsRoot -RecompilePass } catch { $thrown = $_.Exception.Message }
        Assert-True 'P9 -RecompilePass without -Held throws' ($null -ne $thrown) "thrown=$thrown"
    }

    Invoke-Case 'P10 an -OnPoll that throws on the second poll ends the pool with no live child' {
        Reset-Launched
        $script:Polls = 0
        $onPoll = { $script:Polls++; if ($script:Polls -ge 2) { throw 'editor opened' } }
        $units = @(1..3 | ForEach-Object { New-Unit -Label "Z$_" -SleepMs 60000 })
        $thrown = $null
        try { $null = Invoke-RunPool -Units $units -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 3 -OnPoll $onPoll }
        catch { $thrown = $_.Exception.Message }
        Assert-True 'P10 the pool rethrows the -OnPoll error' ($thrown -match 'editor opened') "thrown=$thrown"
        Start-Sleep -Milliseconds 500
        $live = @($script:Launched | Where-Object { -not $_.Proc.HasExited })
        Assert-True 'P10 no launched child is still alive' ($script:Launched.Count -eq 3 -and $live.Count -eq 0) "launched=$($script:Launched.Count) live=$($live.Count)"
    }

    Invoke-Case 'P12 labels that sanitize to one run dir throw before any launch' {
        Reset-Launched
        $a = New-Unit -Label 'Dir Clash'; $b = New-Unit -Label 'Dir_Clash'; $c = New-Unit -Label 'dir_clash'
        Assert-True 'P12 setup: the labels derive distinct tags and one wrapper.out dir' ((Get-RunTag -Label $a.label) -ne (Get-RunTag -Label $b.label) -and
            (Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $a.label) -eq (Get-SuiteChildOutPath -ResultsRoot $script:RunsRoot -Label $b.label))
        foreach ($pair in @(@($a, $b), @($b, $c))) {
            $thrown = $null
            try { $null = Invoke-RunPool -Units $pair -ResultsRoot $script:RunsRoot -Launch $stubLauncher -MaxParallel 2 } catch { $thrown = $_.Exception.Message }
            Assert-True "P12 '$($pair[0].label)' and '$($pair[1].label)' throw on the shared run dir" ($null -ne $thrown -and $thrown -match 'run dir') "thrown=$thrown"
        }
        Assert-True 'P12 nothing was launched' ($script:Launched.Count -eq 0) "launched=$($script:Launched.Count)"
    }

    # The wrapper derives its default from target data in GodotProcess.ps1; verify forwards only an explicit override.
    Invoke-Case 'P13 the suite cap has one home' {
        function Get-TimeoutDefault([string] $Script) {
            $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repoRoot ".claude\scripts\$Script"), [ref] $null, [ref] $null)
            $p = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'TimeoutMs' })[0]
            if ($p.DefaultValue) { $p.DefaultValue.SafeGetValue() } else { $null }
        }
        $wrapper = Get-TimeoutDefault 'run_test_suite.ps1'
        Assert-True 'P13 run_test_suite.ps1 -TimeoutMs has no fixed default; GodotProcess derives it' ($null -eq $wrapper -and
            (Get-Content (Join-Path $repoRoot '.claude/scripts/run_test_suite.ps1') -Raw) -match 'Get-SuiteTimeoutMs') "wrapper=$wrapper"
        $verify = Get-TimeoutDefault 'verify.ps1'
        Assert-True 'P13 verify.ps1 -TimeoutMs has no default of its own' ($null -eq $verify) "verify=$verify"
    }
} finally {
    Stop-Launched
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Fails -gt 0) { Write-Host "FAILED ($script:Fails)"; exit 1 }
Write-Host 'ALL PASS'
exit 0
