#requires -Version 7
<#
.SYNOPSIS
  Duration-binned batch orchestrator for the Integration suite. Splits the suite into
  serial batches of ~-TargetBatchSec measured test-time each (default 60 -> ~3 batches),
  runs every batch through run_test_suite.ps1 (which owns the hang-cap, the worktree-scoped
  tree-kill sweep, and the machine-global runtime-suite mutex — batches are runtime suites,
  so they never pass -NoGodotRuntime), and sum-checks total passed against
  Tests/regression_baseline.json.

.WHY
  One monolithic Integration run (~1150 tests) wedges with probability proportional to
  its duration, and a wedge costs the full 8-min wall-clock cap plus a full re-run.
  Batching (a) shrinks retry blast radius to one batch, (b) shrinks the per-batch hang
  cap so a wedge is detected in ~2 min instead of 8, and (c) gives each batch a fresh
  headless Godot process, resetting the orphan/memory accumulation that makes long runs
  increasingly wedge-prone. Batches are SERIAL — gdUnit4's connect pipe is machine-global
  per assembly name, so parallel batches would collide and silent-skip.

.PARTITION SOUNDNESS
  Units are the direct children of Tests/Integration: each subfolder, plus each root-level
  test class. A unit's filter clause is `FullyQualifiedName~Tests.Integration.<Segment>.`
  — the trailing dot pins the segment to the position directly after `Integration.`, so
  every test matches exactly one unit and batch sums partition the suite. Units are
  enumerated from DISK each run (a new folder can never be silently un-run), and the final
  sum-check against the committed baseline converts any residual gap into a loud failure.

.OUTPUT CONTRACT (stdout, one line each; callers parse these)
  BATCH <n>/<N> label=... status=DONE|HANG|RED|LOCKED|SILENT_SKIP|SKIPPED_BUDGET|SKIPPED_GREEN passed=P failed=F elapsed=Ss
  RETRY <n>/<N> label=... prior=HANG|SILENT_SKIP
  FAILED_TEST=<fqn>            (passed through from the wrapper on red batches)
  DIAGNOSE batch=<label> status=<s> verdict=BUDGET_CLASS|TEST_CLASS|INSUFFICIENT_EVIDENCE evidence=<...> action=<exact change/filter>
  DIAGNOSE_PASS candidates=<n> (emitted after the retry pass; n = candidates classified by batch_diagnosis.ps1)
  TOTAL passed=P failed=F  baseline=B  sentinel=S
  LOCKWAIT_TOTAL_MS=<ms>     (machine-global mutex wait this invocation; excluded from the budget)
  COMPLETENESS=OK|SHORTFALL|SILENT_SKIP|INCOMPLETE   (emitted on every exit path)
  STATUS=DONE|FAIL|HANG|BUDGET_EXCEEDED|LOCKED  exit codes: 0|1|124|5 (3 = shortfall)
  INCOMPLETE means batches were skipped for budget or mutex-starvation (LOCKED), not that
  anything regressed — the total is partial by construction. Recover with -RetryOnly, which
  keeps prior greens. A LOCKED-only completion (no budget skip) emits STATUS=LOCKED; the gate
  does NOT auto-retry it inline — it routes to the queue when the machine-busy signature holds.
  State breadcrumbs: .claude/scratch/test_runs/integration_batches.json (per-batch history
  — persisted so hang concentration is diagnosable after the fact).

.MANIFEST
  Tests/integration_batch_durations.json maps unit segment -> measured test-duration
  seconds (from TRX), plus batch_wall: label -> { wall, consecutive_failures } (schema v3;
  v2 scalar entries load fine). wall is the measured end-to-end WORK wall per batch (wall
  minus lock-wait — a contended measurement never inflates the reservation). The counter
  increments on HANG/SILENT_SKIP/SKIPPED_BUDGET, resets on GREEN, and persists on every exit
  path — it is the diagnosis trigger's "continuously hanging/out-budgeting" ledger. RED
  (adjudicated) and LOCKED (machine contention) never count. Committed like
  regression_baseline.json; auto-rewritten only on a fully-green run. Unknown units
  default to 3s. Bootstrap without running tests:
    pwsh -File run_integration_batched.ps1 -BootstrapFromTrx TestResults/test-result.trx
#>
[CmdletBinding()]
param(
    [int] $TargetBatchSec = 60,
    [int] $MaxBatchTimeoutMs = 300000,
    [int] $TotalBudgetMs = 690000,   # fits 6 batches at ~100-106s honest wall (~636s) + slack; bounded by the wrapper ceiling (the gate child survives the ~10-min kill), not the Bash tool's 600s
    [switch] $RetryOnly,             # rerun only non-green batches from the last state file
    [switch] $DryRun,                # print the batch plan and exit without running
    [string] $BootstrapFromTrx = '', # build the manifest from an existing TRX, run nothing
    # Forwarded to run_test_suite.ps1 per batch. The gate always passes it: the gate owns editor
    # policy at its phase boundaries (inline) or via the watcher (queued), so its children must
    # not re-refuse mid-phase and mangle the verdict shape. Direct invocations omit it and keep
    # the wrapper's per-batch editor guard.
    [switch] $IgnoreEditor
)

$ErrorActionPreference = 'Stop'
$repo = (Get-Item $PSScriptRoot).Parent.Parent.FullName
Set-Location $repo

$manifestPath = Join-Path $repo 'Tests\integration_batch_durations.json'
$baselinePath = Join-Path $repo 'Tests\regression_baseline.json'
$statePath    = Join-Path $repo '.claude\scratch\test_runs\integration_batches.json'
$trxPath      = Join-Path $repo 'TestResults\test-result.trx'
$wrapper      = Join-Path $PSScriptRoot 'run_test_suite.ps1'
$diagScript   = Join-Path $PSScriptRoot 'batch_diagnosis.ps1'
$diagLoaded   = $false
if (Test-Path $diagScript) { . $diagScript; $diagLoaded = $true }
else { Write-Warning "batch_diagnosis.ps1 not found — hang/budget diagnosis pass disabled" }

# ---------------------------------------------------------------- TRX -> per-unit seconds
function Get-TrxUnitDurations {
    param([string] $Path)
    $sums = @{}
    [xml] $xml = Get-Content -Path $Path -Raw
    foreach ($r in $xml.TestRun.Results.UnitTestResult) {
        if ($r.testName -notmatch '^{{PROJECT_NAME}}\.Tests\.Integration\.([^.]+)\.') { continue }
        $seg = $Matches[1]
        $dur = 0.0
        if ($r.duration) { $dur = ([TimeSpan]::Parse($r.duration)).TotalSeconds }
        if (-not $sums.ContainsKey($seg)) { $sums[$seg] = [pscustomobject]@{ seconds = 0.0; tests = 0 } }
        $sums[$seg].seconds += $dur
        $sums[$seg].tests   += 1
    }
    return $sums
}

function Write-Manifest {
    param([hashtable] $Units, [hashtable] $BatchWalls, [hashtable] $Counters)
    $obj = [ordered]@{
        schema_version = 3
        description    = 'Per-unit measured Integration test durations (TRX-derived) + per-batch end-to-end work wall { wall, consecutive_failures }. The counter increments on HANG/SILENT_SKIP/SKIPPED_BUDGET and resets on GREEN — the diagnosis trigger ledger for batch_diagnosis.ps1. Consumed by run_integration_batched.ps1 for duration-based batch binning, honest budget reservations, and hang/budget diagnosis. Auto-rewritten on fully-green batched runs; commit alongside test changes. Units missing here default to 3s — staleness only degrades bin balance, never correctness.'
        updated_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        units          = [ordered]@{}
        batch_wall     = [ordered]@{}
    }
    foreach ($k in ($Units.Keys | Sort-Object)) {
        $obj.units[$k] = [ordered]@{
            seconds = [math]::Round($Units[$k].seconds, 1)
            tests   = $Units[$k].tests
        }
    }
    if ($BatchWalls) {
        foreach ($k in ($BatchWalls.Keys | Sort-Object)) {
            $obj.batch_wall[$k] = [ordered]@{
                wall = [math]::Round($BatchWalls[$k], 1)
                consecutive_failures = $(if ($Counters -and $Counters.ContainsKey($k)) { $Counters[$k] } else { 0 })
            }
        }
    }
    $obj | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding utf8
}

if ($BootstrapFromTrx) {
    $units = Get-TrxUnitDurations -Path $BootstrapFromTrx
    Write-Manifest -Units $units
    Write-Output "MANIFEST_BOOTSTRAPPED units=$($units.Count) from=$BootstrapFromTrx"
    exit 0
}

# ---------------------------------------------------------------- enumerate units from disk
$unitSegs = [System.Collections.Generic.List[string]]::new()
Get-ChildItem -Path (Join-Path $repo 'Tests\Integration') -Directory | ForEach-Object { $unitSegs.Add($_.Name) }
Get-ChildItem -Path (Join-Path $repo 'Tests\Integration') -File -Filter '*.cs' | ForEach-Object {
    # Root-level test classes sit directly in the Tests.Integration namespace; their class
    # name IS the FQN segment after Integration, so the same trailing-dot clause works.
    $m = [regex]::Match((Get-Content $_.FullName -Raw), 'class\s+(\w+)')
    if ($m.Success) { $unitSegs.Add($m.Groups[1].Value) }
}
$unitSegs = $unitSegs | Sort-Object -Unique

$manifest = @{}
$batchWall = @{}
$counters  = @{}
$unitTests = @{}
if (Test-Path $manifestPath) {
    $mj = Get-Content $manifestPath -Raw | ConvertFrom-Json
    foreach ($p in $mj.units.PSObject.Properties) {
        $manifest[$p.Name]  = [double] $p.Value.seconds
        $unitTests[$p.Name] = [int] $p.Value.tests
    }
    if ($mj.PSObject.Properties['batch_wall']) {
        foreach ($p in $mj.batch_wall.PSObject.Properties) {
            if ($p.Value -is [PSCustomObject]) {
                # v3 entry: { wall, consecutive_failures }
                $batchWall[$p.Name] = [double] $p.Value.wall
                $counters[$p.Name]  = [int] $p.Value.consecutive_failures
            } else { $batchWall[$p.Name] = [double] $p.Value }   # v2 scalar
        }
    }
}

# Reservation = the honest duration: last run's end-to-end work wall for this label, or the
# in-suite manifest estimate, whichever is larger. The wrapper's -TimeoutMs stays a pure hang
# guard and never enters the budget test. Lock-wait is excluded from elapsed, so contention
# makes the gate SLOW, not INCOMPLETE.
function Get-BatchReservationMs {
    param([string] $Label, [double] $ExpectedSec)
    $lastWall = 0.0
    if ($batchWall.ContainsKey($Label)) { $lastWall = [double]$batchWall[$Label] }
    [math]::Max([int]($ExpectedSec * 1000), [int]($lastWall * 1000))
}

# ---------------------------------------------------------------- bin-pack (first-fit decreasing)
$weighted = $unitSegs | ForEach-Object {
    [pscustomobject]@{ seg = $_; sec = $(if ($manifest.ContainsKey($_)) { $manifest[$_] } else { 3.0 }) }
} | Sort-Object sec -Descending

$batches = [System.Collections.Generic.List[object]]::new()
foreach ($u in $weighted) {
    $placed = $false
    foreach ($b in $batches) {
        if ($b.sec + $u.sec -le $TargetBatchSec) { $b.segs.Add($u.seg); $b.sec += $u.sec; $placed = $true; break }
    }
    if (-not $placed) {
        $nb = [pscustomobject]@{ segs = [System.Collections.Generic.List[string]]::new(); sec = $u.sec }
        $nb.segs.Add($u.seg)
        $batches.Add($nb)
    }
}

# Quarantine (user-ratified 2026-08-11): ApproachEncounterMigrationTests trips a
# deterministic engine FATAL (gchandle.is_released) when its two test methods share a
# process. Excluded from every batch until the worklog item "Diagnose
# gchandle.is_released() runtime hang" is fixed. Gate verdicts must state this exclusion.
$quarantine = 'FullyQualifiedName!~ApproachEncounterMigration'

# Stable order + filters. Batch numbering follows bin creation order (largest-first).
$plan = @()
$i = 0
foreach ($b in $batches) {
    $i++
    $filter = '(' + (($b.segs | ForEach-Object { "FullyQualifiedName~Tests.Integration.$_." }) -join '|') + ")&$quarantine"
    $plan += [pscustomobject]@{
        label = "Integration_B$i"; segs = @($b.segs); expectedSec = [math]::Round($b.sec, 1)
        filter = $filter; status = 'PENDING'; passed = 0; failed = 0; elapsed = 0; workWall = 0
    }
}

if ($DryRun) {
    Write-Output "PLAN batches=$($plan.Count) units=$($unitSegs.Count) target=${TargetBatchSec}s"
    foreach ($p in $plan) {
        Write-Output "BATCH label=$($p.label) expected=$($p.expectedSec)s units=$($p.segs -join ',')"
    }
    exit 0
}

if ($RetryOnly -and (Test-Path $statePath)) {
    $prev = Get-Content $statePath -Raw | ConvertFrom-Json
    $greenLabels = @($prev.batches | Where-Object { $_.status -eq 'GREEN' } | ForEach-Object { $_.label })
    # Only honor prior greens if the batch composition is unchanged (same segs -> same label).
    foreach ($p in $plan) {
        $match = $prev.batches | Where-Object { $_.label -eq $p.label -and (($_.segs -join ',') -eq ($p.segs -join ',')) }
        if ($match -and $p.label -in $greenLabels) { $p.status = 'GREEN'; $p.passed = $match.passed }
    }
}

function Save-State {
    New-Item -ItemType Directory -Force -Path (Split-Path $statePath) | Out-Null
    [pscustomobject]@{
        updated_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        target_batch_sec = $TargetBatchSec
        batches = $plan
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $statePath -Encoding utf8
}

# ---------------------------------------------------------------- run batches serially
# Per-batch logs are named by label, so a batch skipped this run leaves the PREVIOUS run's log in
# place and a reader summing the log files attributes stale results to the current run. Clear the
# log of every batch this run intends to execute, so afterwards "log present" == "this run wrote it"
# and "log absent" == "this batch never ran". Batches kept green by -RetryOnly keep their logs.
$logDir = Join-Path $repo '.claude\scratch\test_runs'
foreach ($p in ($plan | Where-Object { $_.status -ne 'GREEN' })) {
    foreach ($suffix in @('.log', '.err.log')) {
        $stale = Join-Path $logDir ($p.label + $suffix)
        if (Test-Path $stale) { Remove-Item $stale -Force -ErrorAction SilentlyContinue }
    }
}

$freshDurations = @{}
$lastText = @{}
$allTrxParsed = $true
$totalSw = [System.Diagnostics.Stopwatch]::StartNew()
$lockWaitAccumMs = 0
Write-Output "PLAN batches=$($plan.Count) units=$($unitSegs.Count) target=${TargetBatchSec}s"

$batchIdx = 0
foreach ($p in $plan) {
    $batchIdx++
    if ($p.status -eq 'GREEN') { Write-Output "BATCH $batchIdx/$($plan.Count) skip label=$($p.label) (green in prior run)"; continue }

    $timeoutMs = [math]::Min($MaxBatchTimeoutMs, [math]::Max(120000, [int]((60 + 3 * $p.expectedSec) * 1000)))
    $reservationMs = Get-BatchReservationMs -Label $p.label -ExpectedSec $p.expectedSec
    if (($totalSw.ElapsedMilliseconds - $lockWaitAccumMs) + $reservationMs -gt $TotalBudgetMs) {
        # Reported, never silent: a skipped batch reads downstream as a count regression. Elapsed
        # excludes the machine-global mutex wait (LOCKWAIT_MS from the wrapper), so contention
        # makes the gate slow, not incomplete; a skip here means genuine work-time exhaustion.
        $p.status = 'SKIPPED_BUDGET'
        Write-Output "BATCH $batchIdx/$($plan.Count) label=$($p.label) status=SKIPPED_BUDGET remaining=$([math]::Round(($TotalBudgetMs - ($totalSw.ElapsedMilliseconds - $lockWaitAccumMs))/1000,1))s needed=$([math]::Round($reservationMs/1000,1))s"
        continue
    }

    $batchSw = [System.Diagnostics.Stopwatch]::StartNew()
    $out = & pwsh -NoProfile -File $wrapper -Filter $p.filter -Label $p.label -TimeoutMs $timeoutMs -IgnoreEditor:$IgnoreEditor 2>&1
    $code = $LASTEXITCODE
    $batchSw.Stop()
    $text = $out -join "`n"
    $lastText[$p.label] = $text

    $lockWaitMs = 0
    foreach ($m in [regex]::Matches($text, 'LOCKWAIT_MS=(\d+)')) { $lockWaitMs += [int]$m.Groups[1].Value }
    $lockWaitAccumMs += $lockWaitMs
    # Work wall (wall minus lock-wait) is what the reservation persists — a contended run must
    # not inflate the next run's honest estimate.
    $p.workWall = [math]::Round([math]::Max(0.0, $batchSw.Elapsed.TotalSeconds - ($lockWaitMs / 1000.0)), 1)

    $p.elapsed = if ($text -match 'elapsed=([\d.]+)s') { [double]$Matches[1] } else { 0 }
    if ($text -match 'Failed:\s*(\d+),\s*Passed:\s*(\d+)') {
        $p.failed = [int]$Matches[1]; $p.passed = [int]$Matches[2]
    }

    if ($code -eq 124) { $p.status = 'HANG' }
    elseif ($text -match 'WARN=SILENT_SKIP_SIGNATURE') { $p.status = 'SILENT_SKIP' }
    elseif ($text -match 'STATUS=LOCK_TIMEOUT') { $p.status = 'LOCKED' }
    elseif ($code -ne 0 -or $p.failed -gt 0) { $p.status = 'RED' }
    else { $p.status = 'GREEN' }

    Write-Output "BATCH $batchIdx/$($plan.Count) label=$($p.label) status=$($p.status) passed=$($p.passed) failed=$($p.failed) elapsed=$($p.elapsed)s expected=$($p.expectedSec)s"
    $out | Where-Object { $_ -match '^FAILED_TEST=' } | ForEach-Object { Write-Output $_ }
    Save-State

    # Harvest durations before the next batch overwrites the fixed-name TRX.
    if ($p.status -eq 'GREEN' -and (Test-Path $trxPath)) {
        try {
            $d = Get-TrxUnitDurations -Path $trxPath
            foreach ($k in $d.Keys) { $freshDurations[$k] = $d[$k] }
        } catch { $allTrxParsed = $false }
    } else { $allTrxParsed = $false }
}

# One retry pass for transient wedges (HANG / silent-skip). Real failures (RED) never retry.
$retrySet = @($plan | Where-Object { $_.status -in 'HANG', 'SILENT_SKIP' })
$retryIdx = 0
foreach ($p in $retrySet) {
    $retryIdx++
    $timeoutMs = [math]::Min($MaxBatchTimeoutMs, [math]::Max(120000, [int]((60 + 3 * $p.expectedSec) * 1000)))
    $reservationMs = Get-BatchReservationMs -Label $p.label -ExpectedSec $p.expectedSec
    if (($totalSw.ElapsedMilliseconds - $lockWaitAccumMs) + $reservationMs -gt $TotalBudgetMs) { break }
    Write-Output "RETRY $retryIdx/$($retrySet.Count) label=$($p.label) prior=$($p.status)"
    $batchSw = [System.Diagnostics.Stopwatch]::StartNew()
    $out = & pwsh -NoProfile -File $wrapper -Filter $p.filter -Label "$($p.label)_retry" -TimeoutMs $timeoutMs -IgnoreEditor:$IgnoreEditor 2>&1
    $code = $LASTEXITCODE
    $batchSw.Stop()
    $text = $out -join "`n"
    $lastText[$p.label] = $text
    $lockWaitMs = 0
    foreach ($m in [regex]::Matches($text, 'LOCKWAIT_MS=(\d+)')) { $lockWaitMs += [int]$m.Groups[1].Value }
    $lockWaitAccumMs += $lockWaitMs
    $p.workWall = [math]::Round([math]::Max(0.0, $batchSw.Elapsed.TotalSeconds - ($lockWaitMs / 1000.0)), 1)
    if ($text -match 'Failed:\s*(\d+),\s*Passed:\s*(\d+)') { $p.failed = [int]$Matches[1]; $p.passed = [int]$Matches[2] }
    if ($code -eq 124) { $p.status = 'HANG' }
    elseif ($text -match 'WARN=SILENT_SKIP_SIGNATURE') { $p.status = 'SILENT_SKIP' }
    elseif ($text -match 'STATUS=LOCK_TIMEOUT') { $p.status = 'LOCKED' }
    elseif ($code -ne 0 -or $p.failed -gt 0) { $p.status = 'RED' }
    else {
        $p.status = 'GREEN'
        if (Test-Path $trxPath) { try { $d = Get-TrxUnitDurations -Path $trxPath; foreach ($k in $d.Keys) { $freshDurations[$k] = $d[$k] } } catch {} }
    }
    Write-Output "BATCH $retryIdx/$($retrySet.Count) label=$($p.label) status=$($p.status) passed=$($p.passed) failed=$($p.failed)"
    Save-State
}

# ---------------------------------------------------------------- counter ledger + diagnosis
# v3 consecutive_failures: GREEN resets; HANG/SILENT_SKIP/SKIPPED_BUDGET increment. RED is
# adjudicated and LOCKED is machine contention — neither is a hang/budget signal, so neither
# counts. Persisted on every exit path (the committed manifest is the ledger), then the
# diagnosis pass classifies each trigger candidate from this run's evidence.
$counterDelta = @{ HANG = 1; SILENT_SKIP = 1; SKIPPED_BUDGET = 1 }
foreach ($p in $plan) {
    if ($counterDelta.ContainsKey($p.status)) {
        $counters[$p.label] = (if ($counters.ContainsKey($p.label)) { [int]$counters[$p.label] } else { 0 }) + 1
    } else { $counters[$p.label] = 0 }   # GREEN, RED (adjudicated) and LOCKED (contention) all normalize to 0
}
if (Test-Path $manifestPath) {
    try {
        $mjc = Get-Content $manifestPath -Raw | ConvertFrom-Json
        if ($mjc.PSObject.Properties['batch_wall']) {
            foreach ($p in $mjc.batch_wall.PSObject.Properties) {
                if (-not $counters.ContainsKey($p.Name)) { continue }
                if ($p.Value -is [PSCustomObject]) { $p.Value.consecutive_failures = $counters[$p.Name] }
                else { $p.Value = [ordered]@{ wall = [double]$p.Value; consecutive_failures = $counters[$p.Name] } }
            }
            $mjc | ConvertTo-Json -Depth 5 | Set-Content -Path $manifestPath -Encoding utf8
        }
    } catch { Write-Warning "batch counter ledger update failed: $_" }
}

if ($diagLoaded) {
    $diagCount = 0
    foreach ($p in $plan) {
        $resSec = (Get-BatchReservationMs -Label $p.label -ExpectedSec $p.expectedSec) / 1000.0
        $counter = if ($counters.ContainsKey($p.label)) { [int]$counters[$p.label] } else { 0 }
        if (-not (Test-BatchNeedsDiagnosis -Batch $p -Counter $counter -ReservationSec $resSec)) { continue }
        $wall = if ($p.workWall -gt 0) { [double]$p.workWall } elseif ($batchWall.ContainsKey($p.label)) { [double]$batchWall[$p.label] } else { 0.0 }
        $d = Get-BatchDiagnosis -Batch $p -ReservationSec $resSec -WorkWallSec $wall -LastOutput $lastText[$p.label] -UnitSecs $manifest -UnitTests $unitTests
        if ($d) {
            $diagCount++
            Write-Output "DIAGNOSE batch=$($p.label) status=$($p.status) verdict=$($d.verdict) evidence=$($d.evidence) action=$($d.action)"
        }
    }
    Write-Output "DIAGNOSE_PASS candidates=$diagCount"
}

# ---------------------------------------------------------------- verdict + sum-check
$totalPassed = ($plan | Measure-Object -Property passed -Sum).Sum
$totalFailed = ($plan | Measure-Object -Property failed -Sum).Sum
$baseline = 0; $sentinel = 0
if (Test-Path $baselinePath) {
    $bj = Get-Content $baselinePath -Raw | ConvertFrom-Json
    $baseline = [int] $bj.suites.Integration.passed
    $sentinel = [int] $bj.silent_skip_sentinels.Integration_min
}
Write-Output "TOTAL passed=$totalPassed failed=$totalFailed baseline=$baseline sentinel=$sentinel elapsed=$([math]::Round($totalSw.Elapsed.TotalSeconds,1))s lockwait=$([math]::Round($lockWaitAccumMs/1000,1))s"
Write-Output "LOCKWAIT_TOTAL_MS=$lockWaitAccumMs"

$budgetSkipped = @($plan | Where-Object { $_.status -eq 'SKIPPED_BUDGET' })
$locked        = @($plan | Where-Object { $_.status -eq 'LOCKED' })
$hung          = @($plan | Where-Object { $_.status -in 'HANG', 'SILENT_SKIP' })
$red           = @($plan | Where-Object { $_.status -eq 'RED' })

if ($budgetSkipped.Count -gt 0) {
    # COMPLETENESS is emitted on EVERY exit path. Callers parse it to decide whether a count is a
    # regression signal at all; omitting it here made a budget overrun indistinguishable from an
    # untrustworthy run, and the partial total then tiered as a major regression.
    Write-Output "COMPLETENESS=INCOMPLETE skipped=$($budgetSkipped.Count) batches=$(($budgetSkipped | ForEach-Object label) -join ',') (partial total — NOT a regression signal)"
    Write-Output "STATUS=BUDGET_EXCEEDED skipped=$($budgetSkipped.Count) (re-invoke with -RetryOnly to run remaining batches; prior greens are kept)"
    exit 5
}
if ($red.Count -gt 0)  { Write-Output "STATUS=FAIL red_batches=$(($red | ForEach-Object label) -join ',')"; exit 1 }
# LOCKED-only completion (no budget skip, no real failure). Exit 5 with STATUS=LOCKED — the
# gate skips its automatic -RetryOnly for this signature (the mutex may still be held) and
# routes to the queue when the machine-busy condition holds.
if ($locked.Count -gt 0) {
    Write-Output "COMPLETENESS=INCOMPLETE locked=$($locked.Count) batches=$(($locked | ForEach-Object label) -join ',') (a batch could not acquire the machine-global runtime mutex — NOT a regression signal)"
    Write-Output "STATUS=LOCKED skipped=$($locked.Count) (machine busy — re-run when quiet; the gate queues when the busy signature holds)"
    exit 5
}
if ($hung.Count -gt 0) { Write-Output "STATUS=HANG hung_batches=$(($hung | ForEach-Object label) -join ',') (persisted after retry — machine state suspect; see regression_gate reboot guidance)"; exit 124 }
if ($totalPassed -lt $sentinel) { Write-Output 'COMPLETENESS=SILENT_SKIP (total below architectural floor — results INVALID)'; exit 2 }
if ($totalPassed -lt $baseline) {
    Write-Output "COMPLETENESS=SHORTFALL total=$totalPassed < baseline=$baseline (a unit may be missing from every batch, or tests were removed — gate Tier-2 judgment applies)"
    exit 3
}

Write-Output 'COMPLETENESS=OK'
if ($allTrxParsed -and $freshDurations.Count -gt 0) {
    # MERGE over the existing manifest — a -RetryOnly run only measures the rerun batches,
    # and wholesale replacement would silently drop every other unit's measurement.
    $merged = @{}
    $wallMerge = @{}
    if (Test-Path $manifestPath) {
        $mj = Get-Content $manifestPath -Raw | ConvertFrom-Json
        foreach ($p in $mj.units.PSObject.Properties) {
            $merged[$p.Name] = [pscustomobject]@{ seconds = [double]$p.Value.seconds; tests = [int]$p.Value.tests }
        }
        if ($mj.PSObject.Properties['batch_wall']) {
            foreach ($p in $mj.batch_wall.PSObject.Properties) {
                if ($p.Value -is [PSCustomObject]) { $wallMerge[$p.Name] = [double]$p.Value.wall } else { $wallMerge[$p.Name] = [double]$p.Value }
            }
        }
    }
    foreach ($k in $freshDurations.Keys) { $merged[$k] = $freshDurations[$k] }
    # Drop units that no longer exist on disk (deleted/renamed folders).
    foreach ($k in @($merged.Keys)) { if ($k -notin $unitSegs) { $merged.Remove($k) } }
    foreach ($p in $plan) { if ($p.workWall -gt 0) { $wallMerge[$p.label] = $p.workWall } }
    Write-Manifest -Units $merged -BatchWalls $wallMerge -Counters $counters
    Write-Output "MANIFEST_UPDATED measured=$($freshDurations.Count) total=$($merged.Count) walls=$($wallMerge.Count) (commit Tests/integration_batch_durations.json if changed)"
}
Write-Output 'STATUS=DONE'
exit 0
