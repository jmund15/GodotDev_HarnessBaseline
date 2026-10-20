#requires -Version 7
<#
.SYNOPSIS
  Duration-binned batch orchestrator for the Integration suite, standalone (verify.ps1). Splits
  the suite into batches of ~-TargetBatchSec measured test-time each (default 60 -> ~3 batches),
  runs them one at a time through the run pool (RunPool.ps1 Invoke-RunPool -MaxParallel 1), each
  as an ordinary run_test_suite.ps1 child (which owns the hang-cap, the worktree-scoped tree-kill
  sweep, the checkout lock and a runtime slot — batches are runtime suites, so they never pass
  -NoGodotRuntime), and sum-checks total passed against Tests/regression_baseline.json. Units,
  plan, state, manifest, reservations, retry selection and diagnosis live in IntegrationBatches.ps1.

.WHY
  One monolithic Integration run (~1150 tests) wedges with probability proportional to
  its duration, and a wedge costs the full 8-min wall-clock cap plus a full re-run.
  Batching (a) shrinks retry blast radius to one batch, (b) shrinks the per-batch hang
  cap so a wedge is detected in ~2 min instead of 8, and (c) gives each batch a fresh
  headless Godot process, resetting the orphan/memory accumulation that makes long runs
  increasingly wedge-prone. Standalone, batches run one at a time with ordinary lock-taking
  children: ad-hoc runs from one checkout serialize on its checkout lock, and only a gate's held
  pool may skip the build. The gate submits the same units (IntegrationBatches.ps1) to that pool.

.PARTITION SOUNDNESS
  Units are the direct children of Tests/Integration: each subfolder, plus each root-level
  test class. A unit's filter clause is `FullyQualifiedName~Tests.Integration.<Segment>.`
  — the trailing dot pins the segment to the position directly after `Integration.`, so
  every test matches exactly one unit and batch sums partition the suite. Units are
  enumerated from DISK each run (a new folder can never be silently un-run), and the final
  sum-check against the committed baseline converts any residual gap into a loud failure.

.OUTPUT CONTRACT (stdout, one line each; callers parse these)
  BATCH <n>/<N> label=... status=GREEN|HANG|RED|LOCKED|SILENT_SKIP|<incomplete> passed=P failed=F elapsed=Ss
                               (<incomplete>: NO_OUTPUT|NO_TRX|BAD_TRX|EDITOR_OPEN, RunPool.ps1; elapsed is the child's wall)
  RETRY <n>/<N> label=... prior=HANG|SILENT_SKIP
  RETRY_ONLY_IGNORED reason=no-state|no-digest|digest-mismatch  (-RetryOnly kept no prior green)
  FAILED_TEST=<fqn>            (passed through from the wrapper on red batches)
  FAILED_MSG=<fqn> :: <first message line>  (passed through beside its FAILED_TEST=)
  TRX_DIR=<dir>                (this run's dir, TestResults/runs/<RunId>: one <label>/<label>.trx per batch)
  SLOT=<slots>/<C> ...         (passed through from the wrapper: the runtime slot each batch held)
  OVERLAP_SIGNATURE=<kind> ... (passed through from the wrapper: a rollback candidate; running.md
                                §Runtime slots and parallel dev)
  DIAGNOSE batch=<label> status=<s> verdict=BUDGET_CLASS|TEST_CLASS|INSUFFICIENT_EVIDENCE evidence=<...> action=<exact change/filter>
  DIAGNOSE_PASS candidates=<n> (emitted after the retry pass; n = candidates classified by batch_diagnosis.ps1)
  TOTAL passed=P failed=F  baseline=B  sentinel=S
  LOCKWAIT_TOTAL_MS=<ms>     (checkout-lock + runtime-slot wait this invocation)
  COMPLETENESS=OK|SHORTFALL|SILENT_SKIP|INCOMPLETE|TARGETED   (emitted on every exit path)
  STATUS=DONE|FAIL|HANG|LOCKED  exit codes: 0|1|124|5 (3 = shortfall)
  INCOMPLETE means batches were skipped for lock-starvation (LOCKED), or returned no
  readable result (an <incomplete> status: STATUS=FAIL incomplete_batches=..., exit 1), not that
  anything regressed — the total is partial by construction. Recover with -RetryOnly, which
  keeps prior greens. A LOCKED-only completion emits STATUS=LOCKED; the gate
  does NOT auto-retry it inline — it routes to the queue when the machine-busy signature holds.
  TARGETED means a -Only domain filter was applied — the total is partial BY DESIGN and is
  never tiered against the full baseline. Emitted on every exit path of a -Only run.
  State breadcrumbs: .claude/scratch/test_runs/integration_batches.json (per-batch history
  — persisted so hang concentration is diagnosable after the fact).

.MANIFEST
  Tests/integration_batch_durations.json maps unit segment -> measured test-duration
  seconds (from TRX), plus batch_wall: label -> { wall, consecutive_failures } (schema v3;
  v2 scalar entries load fine). wall is the measured end-to-end WORK wall per batch (wall
  minus lock-wait — a contended measurement never inflates the reservation). The counter
  increments on HANG/SILENT_SKIP, resets on GREEN, and persists on every exit
  path — it is the diagnosis trigger's "continuously hanging" ledger. RED
  (adjudicated) and LOCKED (machine contention) never count. Committed like
  regression_baseline.json; auto-rewritten only on a fully-green run. Unknown units
  default to 3s. Bootstrap without running tests:
    pwsh -File run_integration_batched.ps1 -BootstrapFromTrx TestResults/test-result.trx
#>
[CmdletBinding()]
param(
    [string] $Repo = '',
    [int] $TargetBatchSec = 60,
    # Cap batch test-count: a batch too big for one invocation (too much pre-connect
    # discovery + scene/asset load + object churn) deterministically wedges the Godot runtime
    # (documented fifth trigger — gchandle crash / "too big" wedge). Duration alone under-packs
    # many tiny-test segments into one oversized batch. Bins honor BOTH axes.
    [int] $MaxBatchTests = 380,
    [int] $CostCeilingMs = 300000,
    [switch] $RetryOnly,             # rerun only non-green batches from the last state file
    [switch] $DryRun,                # print the batch plan and exit without running
    [string[]] $Only,                # filter Integration units to the named domains' segments (mid-chain targeted runs). A domain resolves to its Tests/Integration/<D> folder segment(s) from the disk enumeration; unknown domains resolve to zero and emit COMPLETENESS=TARGETED batches=0
    [string] $BootstrapFromTrx = '', # build the manifest from an existing TRX, run nothing
    # Skips this batcher's per-batch editor check. Children always run -IgnoreEditor (the pool's
    # owner owns editor policy), so the check lives here: a live editor on this checkout makes the
    # batch EDITOR_OPEN instead of launching it.
    [switch] $IgnoreEditor,
    # Print the checkout these batches would build in (ROUTE=<path>) and exit 0 before any run.
    [switch] $RouteOnly,
    # Names the run dir TestResults/runs/<RunId>/ that every batch writes under. A caller that owns a run dir passes
    # its id and keeps retention; absent, this run mints verify-<timestamp> and runs it.
    [string] $RunId = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'GodotProcess.ps1')
$repo = Resolve-TestRepo -Repo $Repo -ScriptsRoot $PSScriptRoot
. (Join-Path $PSScriptRoot 'TwinCheckout.ps1')
# A live checkout's batches build in its twin; a twin that cannot be synced leaves the per-batch EDITOR_OPEN records.
if (-not $IgnoreEditor) {
    $route = Resolve-RunCheckout -Repo $repo -ReleaseLock
    if (-not $route.Failed) { $repo = $route.Path }
}
if ($RouteOnly) { Write-Output "ROUTE=$repo"; exit 0 }
Set-Location $repo

$manifestPath = Join-Path $repo 'Tests\integration_batch_durations.json'
$baselinePath = Join-Path $repo 'Tests\regression_baseline.json'
$statePath    = Join-Path $repo '.claude\scratch\test_runs\integration_batches.json'
. (Join-Path $PSScriptRoot 'RunPool.ps1')
$script:PoolRepo = $repo
. (Join-Path $PSScriptRoot 'IntegrationBatches.ps1')
if (-not $script:IbDiagLoaded) { Write-Warning "batch_diagnosis.ps1 not found — hang diagnosis pass disabled" }

if ($BootstrapFromTrx) {
    $units = Get-TrxUnitDurations -Path $BootstrapFromTrx
    Write-Manifest -Path $manifestPath -Units $units
    Write-Output "MANIFEST_BOOTSTRAPPED units=$($units.Count) from=$BootstrapFromTrx"
    exit 0
}

# ---------------------------------------------------------------- units from disk
$allUnitSegs = @(Get-IntegrationSegments -Repo $repo)   # full disk set, kept for the manifest's stale-unit drop-check
$unitSegs = $allUnitSegs
# -Only: a targeted total is partial BY DESIGN — it bypasses the full-baseline sum-check in favour of COMPLETENESS=TARGETED.
if ($Only) {
    # Split on comma: `pwsh -File` passes every argument as a LITERAL string, so a caller that
    # joins domains (`-Only AI,Movement,NPCs`) hands this parameter ONE element that matches no
    # segment. That resolved to zero, exited 0, and reported a clean targeted run over nothing
    # (measured 2026-08-20: verify.ps1 -Scope AI,Movement,NPCs printed VERIFY=PASS having selected
    # no integration test at all). Splitting here fixes every caller, including a human typing it.
    $wanted = @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $unitSegs = @(Resolve-OnlySegments -Segments $allUnitSegs -Wanted $wanted)
    # An unresolvable filter is operator error, not a clean run. Exiting 0 here is what let a
    # zero-selection run read as green; a named non-zero exit is the only thing callers can act on.
    if ($unitSegs.Count -eq 0) {
        Write-Output "COMPLETENESS=TARGETED domains=$($wanted -join ',') batches=0 (NO SEGMENT MATCHED — nothing ran)"
        Write-Output "STATUS=BLOCKED reason=no Tests/Integration segment matches $($wanted -join ',') (available: $($allUnitSegs -join ', '))"
        exit 4
    }
}

$man = Read-BatchManifest -Path $manifestPath
# Quarantine filter appended to every batch, e.g. 'FullyQualifiedName!~SomeSuite' for a suite
# that trips a deterministic engine FATAL when its methods share a process. From
# `adaptation.json` `test_quarantine_filter` (Design Doc §8); empty string = no exclusion.
# Gate verdicts must state any exclusion set here.
$quarantine = Get-AdaptationValue -Repo $repo -Key 'test_quarantine_filter' -Default ''
$plan = New-IntegrationPlan -Segments $unitSegs -Manifest $man -TargetBatchSec $TargetBatchSec -MaxBatchTests $MaxBatchTests -Quarantine $quarantine

if ($DryRun) {
    Write-Output "PLAN batches=$($plan.Count) units=$($unitSegs.Count) target=${TargetBatchSec}s"
    foreach ($p in $plan) {
        Write-Output "BATCH label=$($p.label) expected=$($p.expectedSec)s units=$($p.segs -join ',')"
    }
    exit 0
}

# The gate's content digest (TreeDigest.ps1), dot-sourced in a child scope so its StrictMode stays there. -RetryOnly keeps
# a prior green only when this equals the digest the state file was saved under; the gate saves under the same digest.
$runDigest = & { . (Join-Path $PSScriptRoot 'TreeDigest.ps1'); (Get-TreeDigest -Repo $repo).digest }
if ($RetryOnly) { Restore-PriorGreens -Plan $plan -StatePath $statePath -TreeDigest $runDigest }

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

$ownsRunDir = -not $PSBoundParameters.ContainsKey('RunId')
if ($ownsRunDir) { $RunId = 'verify-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss') }
$runsParent = Join-Path $repo 'TestResults\runs'
$runsRoot = New-RunDir -RunsRoot $runsParent -RunId $RunId
if ($ownsRunDir) { Remove-OldRunDirs -RunsRoot $runsParent }
$passThroughPattern = '^(FAILED_TEST|FAILED_MSG|OVERLAP_SIGNATURE|SLOT)='

# ---------------------------------------------------------------- run batches, one at a time
# Ordinary lock-taking children only: standalone runs take no held checkout, so no child skips its build (owner ruling 2026-09-29).
$launch = { param($x) Start-SuiteChild -Unit $x -ResultsRoot $runsRoot }
function Invoke-BatchUnit {
    param($u)
    if (-not $IgnoreEditor -and @(Get-LiveEditor -Checkout $repo).Count -gt 0) {
        return , @([pscustomobject]@{ label = $u.label; status = 'EDITOR_OPEN'; passed = 0; failed = 0; exit = 126; trx = ''
                                      elapsedSec = 0; lockWaitMs = 0; outPath = ''; failedTests = @(); failedMessages = @() })
    }
    Invoke-RunPool -Units @($u) -ResultsRoot $runsRoot -MaxParallel 1 -Launch $launch
}
function Write-PassThrough {
    param($r)
    if ($r.outPath -and (Test-Path -LiteralPath $r.outPath)) {
        Get-Content -LiteralPath $r.outPath | Where-Object { $_ -match $passThroughPattern } | ForEach-Object { Write-Output $_ }
    }
}

$unitByLabel = @{}
foreach ($u in @(Get-IntegrationUnits -Plan $plan -CostCeilingMs $CostCeilingMs -BatchWall $man.batchWall)) { $unitByLabel[$u.label] = $u }
$totalSw = [System.Diagnostics.Stopwatch]::StartNew()
$lockWaitAccumMs = 0
Write-Output "PLAN batches=$($plan.Count) units=$($unitSegs.Count) target=${TargetBatchSec}s"

$batchIdx = 0
foreach ($p in $plan) {
    $batchIdx++
    if ($p.status -eq 'GREEN') { Write-Output "BATCH $batchIdx/$($plan.Count) skip label=$($p.label) (green in prior run)"; continue }

    $res = Invoke-BatchUnit $unitByLabel[$p.label]
    Update-IntegrationPlan -Plan $plan -Results $res
    $lockWaitAccumMs += $res[0].lockWaitMs
    Write-Output "BATCH $batchIdx/$($plan.Count) label=$($p.label) status=$($p.status) passed=$($p.passed) failed=$($p.failed) elapsed=$($p.elapsed)s expected=$($p.expectedSec)s"
    Write-PassThrough $res[0]
    Save-BatchState -StatePath $statePath -Plan $plan -TargetBatchSec $TargetBatchSec -TreeDigest $runDigest
}

# One retry pass for transient wedges (HANG / silent-skip), each under its <label>_retry run dir. RED never retries.
$retryUnits = @(Select-RetryUnits -Plan $plan -CostCeilingMs $CostCeilingMs -BatchWall $man.batchWall)
$retryIdx = 0
foreach ($u in $retryUnits) {
    $retryIdx++
    $p = @($plan | Where-Object { $_.label -eq $u.of })[0]
    Write-Output "RETRY $retryIdx/$($retryUnits.Count) label=$($p.label) prior=$($p.status)"
    $res = Invoke-BatchUnit $u
    Update-IntegrationPlan -Plan $plan -Results $res
    $lockWaitAccumMs += $res[0].lockWaitMs
    Write-Output "BATCH $retryIdx/$($retryUnits.Count) label=$($p.label) status=$($p.status) passed=$($p.passed) failed=$($p.failed)"
    Write-PassThrough $res[0]
    Save-BatchState -StatePath $statePath -Plan $plan -TargetBatchSec $TargetBatchSec -TreeDigest $runDigest
}

# ---------------------------------------------------------------- counters, diagnosis, verdict, manifest
# The committed manifest is the consecutive_failures ledger; it gets exactly one write per run, after the verdict:
# a merge of fresh durations on a passing run, the counters alone otherwise.
$counters = Get-UpdatedCounters -Plan $plan -Counters $man.counters
foreach ($line in @(Get-BatchDiagnosisLines -Plan $plan -Counters $counters -Manifest $man)) { Write-Output $line }
$bl = Read-IntegrationBaseline -Path $baselinePath
$verdict = Get-IntegrationVerdict -Plan $plan -Only $Only -Baseline $bl.baseline -Sentinel $bl.sentinel `
    -ElapsedSec $totalSw.Elapsed.TotalSeconds -LockWaitMs $lockWaitAccumMs -RunsRoot $runsRoot
$fresh = if ($verdict.Exit -eq 0) { Get-FreshDurations -Plan $plan } else { $null }
$manifestLine = Save-BatchManifest -Path $manifestPath -Plan $plan -Counters $counters -Fresh $fresh -AllSegments $allUnitSegs
foreach ($line in $verdict.Lines) { Write-Output $line }
if ($manifestLine) { Write-Output $manifestLine }
if ($verdict.Exit -eq 0) { Write-Output 'STATUS=DONE' }
exit $verdict.Exit
