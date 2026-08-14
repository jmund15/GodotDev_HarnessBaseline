#requires -Version 7
<#
.SYNOPSIS
  Hang / budget classification for run_integration_batched.ps1. Dot-sourced by the runner;
  callers get Test-BatchNeedsDiagnosis (trigger) + Get-BatchDiagnosis (classify). Run
  directly with -SelfTest for the synthetic case table.

.CLASSIFICATION
  BUDGET_CLASS           The batch's honest work fills its reservation (wall/reservation >= 0.9,
                         or the batch was budget-skipped while upstream consumed the global
                         budget). The reservation/budget was wrong, not a wedge. Action names
                         the exact change: raise expectedSec, move a dominant segment to a
                         lighter batch, or raise TotalBudgetMs.
  TEST_CLASS             The batch died before its honest time AND the captured output names a
                         last test. Action: run that test standalone — passes-alone-vs-suite-only
                         separates a wedge from batch composition (AttachedDeathFling lesson).
  INSUFFICIENT_EVIDENCE  Neither class is provable from the captured output. Names what is
                         missing and the exact enabling change — never guesses a suspect.

.TRIGGER (Test-BatchNeedsDiagnosis)
  HANG / SILENT_SKIP persisted after the retry pass; SKIPPED_BUDGET with consecutive_failures
  >= 2 (manifest v3 ledger); GREEN with workWall >= 1.5x reservation and reservation >= 60s
  (advisory — catches a batch running 4x its expectation without tripping the hang cap, e.g.
  Integration_B5's 239.7s-vs-59.9s run). RED (adjudicated) and LOCKED (machine contention) are
  not hang/budget signals and never trigger.
#>
[CmdletBinding()]
param(
    [switch] $SelfTest
)

function Get-LastTestName {
    param([string] $Output)
    # dotnet test verbosity-normal per-test lines; the LAST match wins. Conservative on purpose:
    # an unrecognized shape falls through to INSUFFICIENT_EVIDENCE rather than a guessed name.
    $m = [regex]::Matches([string]$Output, '(?m)^\s*(?:Passed|Failed|Skipped)\s+([A-Za-z_][\w.]*)\s*$')
    if ($m.Count -gt 0) { return $m[$m.Count - 1].Groups[1].Value }
    return $null
}

function Test-BatchNeedsDiagnosis {
    param(
        [pscustomobject] $Batch,        # .label .status .workWall .expectedSec
        [int] $Counter,                 # consecutive_failures ledger (manifest v3)
        [double] $ReservationSec
    )
    switch ($Batch.status) {
        'HANG'            { return $true }
        'SILENT_SKIP'     { return $true }
        'SKIPPED_BUDGET'  { return $Counter -ge 2 }
        'GREEN'           { return ($Batch.workWall -ge 1.5 * $ReservationSec) -and ($ReservationSec -ge 60) }
        default           { return $false }
    }
}

function Get-BatchDiagnosis {
    param(
        [pscustomobject] $Batch,        # .label .status .segs .expectedSec .workWall
        [double] $ReservationSec,
        [double] $WorkWallSec,          # this run's work wall, or the manifest's last wall for never-ran batches
        [string] $LastOutput,           # captured output of the batch's most recent attempt
        [hashtable] $UnitSecs,          # manifest: seg -> measured seconds
        [hashtable] $UnitTests          # manifest: seg -> test count
    )
    $ratio = if ($ReservationSec -gt 0) { $WorkWallSec / $ReservationSec } else { 0.0 }

    if ($Batch.status -eq 'SKIPPED_BUDGET') {
        return [pscustomobject]@{
            verdict  = 'BUDGET_CLASS'
            evidence = "skipped before running (global budget exhausted upstream); last_wall=${WorkWallSec}s reservation=${ReservationSec}s"
            action   = 'raise TotalBudgetMs (690s) in run_integration_batched.ps1 or shift units into lighter batches — the batch itself never got to run'
        }
    }

    if ($ratio -ge 0.9) {
        # Honest work filling the reservation -> the reservation/budget was wrong, not a wedge.
        $total = [math]::Max(0.001, [double]$Batch.expectedSec)
        $dom = $null; $domSec = 0.0
        foreach ($s in $Batch.segs) {
            if ($UnitSecs.ContainsKey($s) -and [double]$UnitSecs[$s] -gt $domSec) { $domSec = [double]$UnitSecs[$s]; $dom = $s }
        }
        $action = if ($dom -and $Batch.segs.Count -gt 1 -and ($domSec / $total) -ge 0.5) {
            "move segment $dom (${domSec}s of $total) to a lighter batch in Tests/integration_batch_durations.json"
        } else {
            "raise expectedSec for $($Batch.label) in Tests/integration_batch_durations.json (the reservation self-corrects; rebalance if this recurs)"
        }
        return [pscustomobject]@{
            verdict  = 'BUDGET_CLASS'
            evidence = "wall=${WorkWallSec}s reservation=${ReservationSec}s ratio=$([math]::Round($ratio, 2)) segments=$($Batch.segs -join ',')"
            action   = $action
        }
    }

    $test = Get-LastTestName -Output $LastOutput
    if ($test) {
        # Historical per-test duration: the unit's measured seconds / test count — the only
        # per-test timing the manifest carries. Named as evidence, never as the verdict.
        $avg = 0.0
        foreach ($s in $Batch.segs) {
            if ($UnitTests.ContainsKey($s) -and [int]$UnitTests[$s] -gt 0 -and $UnitSecs.ContainsKey($s)) {
                $avg = [double]$UnitSecs[$s] / [int]$UnitTests[$s]
            }
        }
        return [pscustomobject]@{
            verdict  = 'TEST_CLASS'
            evidence = "last_test=$test avg_unit_duration=$([math]::Round($avg, 2))s wall_ratio=$([math]::Round($ratio, 2))"
            action   = "pwsh .claude/scripts/run_test_suite.ps1 -Filter 'FullyQualifiedName~$test' — passes-alone-vs-suite-only separates a wedge from batch composition"
        }
    }

    return [pscustomobject]@{
        verdict  = 'INSUFFICIENT_EVIDENCE'
        evidence = 'no per-test progress lines in the batch output — cannot name a suspect'
        action   = 'add a -Verbosity normal passthrough to run_test_suite.ps1 and re-run the batch to capture per-test progress'
    }
}

# ---------------------------------------------------------------- self-test (run directly)
if ($SelfTest) {
    $lines = [System.Collections.Generic.List[string]]::new()
    function Test-Expect {
        param([string] $Name, [bool] $Ok, [string] $Detail)
        $line = if ($Ok) { "PASS $Name" } else { "FAIL $Name — $Detail" }
        $lines.Add($line)
    }

    $unitSecs  = @{ Enemies = 30.0; Casting = 49.4; AI = 12.5; A = 10.0; B = 9.0; C = 8.0 }
    $unitTests = @{ Enemies = 83; Casting = 146; AI = 270; A = 10; B = 9; C = 8 }
    $hangOut   = "Test run for {{PROJECT_NAME}}.dll`nStarting test execution...`nPassed {{PROJECT_NAME}}.Tests.Integration.Enemies.TinyTest`n"
    $plainOut  = "Test run for {{PROJECT_NAME}}.dll`nPassed!  - Failed: 0, Passed: 807`n"

    # --- matcher
    Test-Expect 'matcher picks last test line' (
        (Get-LastTestName -Output ($hangOut + "Failed {{PROJECT_NAME}}.Tests.Integration.AI.OtherTest`n")) -eq '{{PROJECT_NAME}}.Tests.Integration.AI.OtherTest') 'expected the LAST test line'
    Test-Expect 'matcher null on plain output' (
        $null -eq (Get-LastTestName -Output $plainOut)) 'expected no match on summary-only output'

    # --- trigger
    $hang = [pscustomobject]@{ label = 'B1'; status = 'HANG'; segs = @('Enemies'); expectedSec = 30.0; workWall = 40 }
    Test-Expect 'HANG always triggers' (Test-BatchNeedsDiagnosis -Batch $hang -Counter 0 -ReservationSec 100) 'HANG must diagnose'
    $silent = [pscustomobject]@{ label = 'B2'; status = 'SILENT_SKIP'; segs = @('AI'); expectedSec = 12.5; workWall = 0 }
    Test-Expect 'SILENT_SKIP triggers' (Test-BatchNeedsDiagnosis -Batch $silent -Counter 0 -ReservationSec 60) 'persisted silent-skip must diagnose'
    $skip1 = [pscustomobject]@{ label = 'B3'; status = 'SKIPPED_BUDGET'; segs = @('AI'); expectedSec = 12.5; workWall = 0 }
    Test-Expect 'SKIPPED_BUDGET counter 1 does not trigger' (-not (Test-BatchNeedsDiagnosis -Batch $skip1 -Counter 1 -ReservationSec 60)) 'first skip is informational'
    Test-Expect 'SKIPPED_BUDGET counter 2 triggers' (Test-BatchNeedsDiagnosis -Batch $skip1 -Counter 2 -ReservationSec 60) 'second consecutive skip must diagnose'
    $greenSlow = [pscustomobject]@{ label = 'B5'; status = 'GREEN'; segs = @('Casting'); expectedSec = 49.4; workWall = 160 }
    Test-Expect 'GREEN 1.6x reservation triggers' (Test-BatchNeedsDiagnosis -Batch $greenSlow -Counter 0 -ReservationSec 100) 'green-but-4x must diagnose'
    $greenOk = [pscustomobject]@{ label = 'B4'; status = 'GREEN'; segs = @('AI'); expectedSec = 12.5; workWall = 110 }
    Test-Expect 'GREEN 1.1x does not trigger' (-not (Test-BatchNeedsDiagnosis -Batch $greenOk -Counter 0 -ReservationSec 100)) 'sub-1.5x is absorbed by the reservation'
    $greenTiny = [pscustomobject]@{ label = 'B6'; status = 'GREEN'; segs = @('AI'); expectedSec = 12.5; workWall = 160 }
    Test-Expect 'GREEN tiny reservation does not trigger' (-not (Test-BatchNeedsDiagnosis -Batch $greenTiny -Counter 0 -ReservationSec 30)) 'sub-60s reservations are noise'
    $red = [pscustomobject]@{ label = 'B7'; status = 'RED'; segs = @('AI'); expectedSec = 12.5; workWall = 20 }
    Test-Expect 'RED never triggers' (-not (Test-BatchNeedsDiagnosis -Batch $red -Counter 5 -ReservationSec 60)) 'RED is adjudicated, not a hang signal'
    $locked = [pscustomobject]@{ label = 'B8'; status = 'LOCKED'; segs = @('AI'); expectedSec = 12.5; workWall = 0 }
    Test-Expect 'LOCKED never triggers' (-not (Test-BatchNeedsDiagnosis -Batch $locked -Counter 5 -ReservationSec 60)) 'LOCKED is machine contention'

    # --- classification
    $d1 = Get-BatchDiagnosis -Batch $hang -ReservationSec 100 -WorkWallSec 40 -LastOutput $hangOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'TEST_CLASS from identifiable test' ($d1.verdict -eq 'TEST_CLASS' -and $d1.action -match 'TinyTest' -and $d1.evidence -match 'avg_unit_duration=0.36') "got $($d1.verdict) / $($d1.action)"

    $d2 = Get-BatchDiagnosis -Batch $hang -ReservationSec 100 -WorkWallSec 95 -LastOutput $plainOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'BUDGET_CLASS at 0.95 ratio' ($d2.verdict -eq 'BUDGET_CLASS') "got $($d2.verdict)"

    $single = [pscustomobject]@{ label = 'B1'; status = 'HANG'; segs = @('Enemies'); expectedSec = 30.0; workWall = 95 }
    $d3 = Get-BatchDiagnosis -Batch $single -ReservationSec 100 -WorkWallSec 95 -LastOutput $plainOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'single-segment batch raises, not moves' ($d3.action -match 'raise expectedSec' -and $d3.action -notmatch 'move segment') "got $($d3.action)"

    $dom = [pscustomobject]@{ label = 'B1'; status = 'HANG'; segs = @('Enemies', 'AI'); expectedSec = 42.5; workWall = 95 }
    $d4 = Get-BatchDiagnosis -Batch $dom -ReservationSec 100 -WorkWallSec 95 -LastOutput $plainOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'dominant segment named in action' ($d4.action -match 'move segment Enemies') "got $($d4.action)"

    $bal = [pscustomobject]@{ label = 'B1'; status = 'HANG'; segs = @('A', 'B', 'C'); expectedSec = 27.0; workWall = 95 }
    $d5 = Get-BatchDiagnosis -Batch $bal -ReservationSec 100 -WorkWallSec 95 -LastOutput $plainOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'balanced batch raises expectedSec' ($d5.action -match 'raise expectedSec') "got $($d5.action)"

    $d6 = Get-BatchDiagnosis -Batch $hang -ReservationSec 100 -WorkWallSec 40 -LastOutput $plainOut -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'INSUFFICIENT_EVIDENCE without test lines' ($d6.verdict -eq 'INSUFFICIENT_EVIDENCE' -and $d6.action -match 'Verbosity') "got $($d6.verdict) / $($d6.action)"

    $d7 = Get-BatchDiagnosis -Batch $skip1 -ReservationSec 60 -WorkWallSec 12.5 -LastOutput '' -UnitSecs $unitSecs -UnitTests $unitTests
    Test-Expect 'SKIPPED_BUDGET classifies BUDGET_CLASS' ($d7.verdict -eq 'BUDGET_CLASS' -and $d7.action -match 'TotalBudgetMs') "got $($d7.verdict)"

    $lines | ForEach-Object { Write-Output $_ }
    if ($lines.Count -eq 0) { Write-Output 'SELFTEST_ERROR no cases executed — a zero-case self-test is a no-op, not a pass'; exit 1 }
    if (($lines | Where-Object { $_ -like 'FAIL*' }).Count -gt 0) { exit 1 }
    Write-Output "SELFTEST_OK cases=$($lines.Count)"
    exit 0
}
