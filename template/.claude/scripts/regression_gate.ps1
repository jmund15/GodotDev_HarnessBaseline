<#
.SYNOPSIS
  Deterministic regression-gate orchestrator. Runs every mechanical phase of
  /regression_gate in one process and emits a compact, machine-readable verdict.

.WHY THIS EXISTS
  The gate's procedure is ~14 serialized Bash round-trips whose decisions are
  integer comparisons already specified in Tests/regression_baseline.json
  (exit-code checks, sentinel floors, drift ratios, ratchet-forward writes).
  Driving those from the model costs a full context read per step and required
  ~32KB of prose instruction resident in context. Everything deterministic
  lives here; the command file keeps only what needs judgment — failure
  adjudication (AskUserQuestion), chain-mode tiering, and checklist rendering.

  This generalizes what run_integration_batched.ps1 already does for the
  Integration suite (baseline + sentinel evaluation in code) to all three.

.INVARIANTS PRESERVED FROM THE COMMAND FILE
  - Engine MISMATCH stops before the suites (a version disagreement is
    indistinguishable from a real regression and costs a full ~8min run).
  - Static guards 1b-1h run before the build; any exit 1 blocks.
  - The headless import runs after the build and before Sanity, and finishes
    before any suite starts: a suite on a stale import cache fails on resources
    that cannot load. A red Sanity ends the gate before the pool.
  - Parsed failures precede executor/sentinel classification; clean counts use sentinel and drift tiers.
  - Partial-but-clean counts (sentinel < passed < baseline) re-run once.
  - Baseline ratchets only on a fully green run; a moderate drop clears when
    committed net test-attribute deletions explain it and completeness is OK.
  - Baseline stamp trust: updated_on_commit must equal the commit that last
    touched the file, or its parent (the benign one-commit-lag shape). Compared
    as full shas; a fully green run re-establishes an untrusted stamp.
  - Never write while FAIL/WARN or with uncommitted test changes.

.EXIT CODES
  0   PASS          all tiers clear, baseline ratcheted on growth or explained deletion
  1   FAIL          a suite reported failures -> model runs step-5 adjudication
  2   INVALID       silent-skip signature / below sentinel -> results untrustworthy
  3   WARN          unexplained count drop -> needs user ack
  4   BLOCKED       preflight, guard, or build failure -> fix before re-running
  6   INCOMPLETE    a unit returned no readable result (no wrapper output, no or malformed TRX)
                    with no machine-busy signature; a held child was refused (HELD_REFUSED) or
                    its recompile opt-out was ignored (SKIP_RECOMPILE_IGNORED), or the gate itself
                    threw (GATE_ERROR), all harness faults
  7   BUSY          no verdict: the wait for this checkout ran out (its lock held, a live editor, a
                    foreign test run), a suite's lock-wait expired (LOCKED tier), an incomplete
                    run carried the machine-busy signature (starved), or an editor appeared
                    mid-run. One BUSY line names the holder and the next step; nothing failed
  8   CONTENTION    external-kill signature AND a live peer harness run -> not a
                    regression signal; re-run once the peer clears
  124 HANG          a suite wedged and was tree-killed after retry

.NOTES
  Detail (failing test names, guard stdout, engine paths) is written to
  .claude/scratch/test_runs/gate_last_<runId>.md so stdout stays ~20 lines;
  gate_last.md is a latest-run pointer copy. Read the detail file only when the
  verdict is not PASS. Every completed run also writes <ledgerDir>/<runId>.result.json
  (the ledger cross-session REUSE consumes) — best-effort, never fatal.

  A gate that finds its checkout busy waits on the lock itself (Enter-GateHeld), holding nothing, and prints WAIT lines
  naming the holder; at -WaitSec it ends BUSY. The ledger record is written before the lock is released, so a waiter
  that acquires next re-checks REUSE and finds the verdict it waited behind.
#>

[CmdletBinding()]
param(
    [string] $Repo = '',
    # Run preflight + guards + build only, then stop. ~20s; holds the checkout lease from entry through its build.
    # The correct gate for a pure-data commit that skips the .cs-triggered path.
    # The gate's ONLY remaining width knob, and it is a different QUESTION ("do the
    # guards and the build pass"), not a cheaper answer to the same one. Narrow test
    # runs live in verify.ps1 and never produce a gate verdict.
    [switch]   $StaticOnly,
    # Evaluate and report WITHOUT writing Tests/regression_baseline.json. A recording
    # flag, not a width flag: coverage is unchanged, so it forks the reuse key but never
    # lets a reduced-coverage run satisfy a full one. /test_compact needs it to capture
    # counts without ratcheting.
    [switch]   $NoBaselineUpdate,
    # Keeps the Integration batches the state file recorded green for this exact tree
    # (IntegrationBatches.ps1 Restore-PriorGreens) and submits only the rest.
    [switch]   $RetryOnly,
    # Run even though a Godot editor holds this checkout: no routing to a twin, and an editor is no holder.
    [switch]   $IgnoreEditor,
    # Print the checkout this gate would run in (ROUTE=<path>) and exit 0 before any build or wait.
    # A live checkout's twin is synced first, then its lock is released.
    [switch]   $RouteOnly,
    # Seconds to wait for a busy checkout (its lock held, a live editor, a foreign test runner) before ending BUSY
    # (exit 7). The wait holds nothing and prints a WAIT line naming the holder. 0 = probe once, do not wait.
    #
    # DEFAULTS TO WAITING because the caller is an agent that must decide something: a bare exit 7 hands back no
    # verdict, and the documented invocation is `run_in_background: true` (commands/regression_gate.md), where blocking
    # is free and process exit is exactly what wakes the session with the verdict. 30min covers a full suite run by the
    # holder. Expiry WAKES, it never kills (feedback_timeouts_wake_dont_kill.md): the gate ends BUSY and the caller
    # re-invokes; the holder is never touched.
    [int]      $WaitSec = 1800,
    # Force a fresh run: refuse a ledger REUSE even when a digest+mode+age match exists.
    [switch]   $NoReuse,
    # Spawn the real gate as a DETACHED process, print where its stdout lands, and exit in ~1s.
    #
    # This is the agent-session entry point, and it exists because a harness kill of a
    # backgrounded Bash task takes the task's whole process tree with it: on 2026-08-25 five gates
    # died mid-Logic at a byte-identical 327 bytes of stdout, plus a bare `sleep` loop at 0 bytes,
    # which rules out anything gate-specific. Idle turns alone do not kill it (2026-09-25: a
    # backgrounded 52-minute battery survived them). A detached child is not a descendant of that
    # shell, so the kill cannot reach it (the same reason a detached run survives its launcher).
    #
    # Stdout goes to a FILE, never a pipe back to the caller: an inherited pipe is what makes a
    # child die with its parent. The caller polls that file for a line starting VERDICT=.
    [switch]   $Detach,
    # Stream a detached run's stage lines and finished-batch transitions to stdout, once each,
    # until its VERDICT=, then exit. The supported Monitor for `-Detach`; read-only, so it takes
    # no lock, spawns nothing, and runs no gate. It never emits a line it has already emitted
    # (the seen-set lives in this process, so no seen-file and no CRLF re-emit).
    [switch]   $Watch,
    # With -Watch: the detached run's OUT path (the `OUT=` it printed).
    [string]   $Out = '',
    # With -Watch: the Integration batch state file. Empty uses this checkout's default.
    [string]   $Batches = '',
    # With -Watch: poll interval in seconds. The wait costs process time, not caller turns.
    [int]      $WatchIntervalSec = 10
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'WideRunGuard.ps1')
Assert-WideRunAllowed -Entry 'regression_gate.ps1' -Repo $Repo -Flags @(@('StaticOnly', 'RouteOnly', 'Watch', 'Detach') | Where-Object { $PSBoundParameters.ContainsKey($_) })
. (Join-Path $PSScriptRoot 'GodotProcess.ps1')
$repo = Resolve-TestRepo -Repo $Repo -ScriptsRoot $PSScriptRoot
. (Join-Path $PSScriptRoot 'TwinCheckout.ps1')
. (Join-Path $PSScriptRoot 'StaticGuards.ps1')
# A live checkout's gate runs in its twin instead. The route is decided once, by Get-RunRoute, with no sync and no lock;
# the twin is synced only after the gate holds the twin's lock (the entry wait below). The -Detach launcher and an
# -IgnoreEditor run keep the source.
$script:TwinSource = $null
$script:TwinHashes = $null
$script:TwinSyncReason = $null
# The tracked files a green gate rewrites: this script the baseline, the batcher's manifest the durations. The twin's
# write-back copies exactly these, so the list is owned here, where the write set is known (not in TwinCheckout.ps1).
$script:GateOutputs = @('Tests\regression_baseline.json', 'Tests\integration_batch_durations.json')
if ($RouteOnly) {
    $rr = if ($IgnoreEditor -or $Detach -or $Watch) { [pscustomobject]@{ Path = $repo } } else { Resolve-RunCheckout -Repo $repo -ReleaseLock }
    Write-Output "ROUTE=$($rr.Path)"
    exit 0
}
$scripts = $PSScriptRoot
$hooks = Join-Path (Split-Path $PSScriptRoot) 'hooks'
if (-not $Watch) { Assert-GateTargetCompatible -Repo $repo }
$runId     = '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
# Every child wrapper inherits this, so its overlap-signature trigger lines name this run. Complete-Gate collects them.
$env:HARNESS_GATE_RUN = $runId
# Machine-global on purpose: worktrees are separate directories, so a
# per-checkout path cannot see peer sessions.
$activityDir = Join-Path $env:TEMP 'harness-activity'
# The checkout-derived paths. A routed gate re-points them at its twin (Set-GateCheckout) once it holds the twin's lock.
function Set-GateCheckout {
    param([Parameter(Mandatory)] [string] $Path)
    $script:repo = $Path
    Set-Location $Path
    $script:logDir     = Join-Path $Path '.claude\scratch\test_runs'
    # NOTE: must not be named $detail — PowerShell variable names are
    # case-insensitive, so $detail and $script:Detail are the same variable.
    $script:detailPath = Join-Path $script:logDir "gate_last_$runId.md"
    $script:baselineP  = Join-Path $Path 'Tests\regression_baseline.json'
    $script:ledgerDir  = Join-Path $Path '.claude\scratch\gate_ledger'
    $script:PoolRepo   = $Path
    New-Item -ItemType Directory -Force -Path $script:logDir | Out-Null
}
Set-GateCheckout -Path $repo

$script:Detail       = [System.Collections.Generic.List[string]]::new()
$script:ActivityOn   = $false
$script:ActivityPath = $null
$script:ActivityStart     = $null
$script:ActivityProcStart = $null
$script:RunTree      = $null
$script:RunStartedUtc = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
$script:Mode         = $null   # normalized coverage contract; set below
$script:BaselineAction = 'unknown'   # threaded to the result record by Complete-Gate
$script:ReusedFrom   = $null   # producer record id when this run reused a verdict
$script:ReusedFailedTests = @() # producer's failing tests on a reused-FAIL
$script:PeersSeen    = $false  # any suite-boundary peer snapshot non-empty
$script:ActivityKind = 'waiter' # 'waiter' until Enter-GateHeld holds the lease, then 'gate': a waiting gate holds nothing
$script:GateOwnerRoot = $null  # Resolve-GateOwnerRoot, once, before the first activity write
$script:PeersSeenList = @()    # the peer records behind PeersSeen, for the starved BUSY line
$script:Waited       = $false  # Enter-GateHeld saw a busy checkout; a gate that waited re-checks REUSE
$script:WaitTickSec  = 60      # how often a long wait prints its WAIT line
$suiteResults        = [ordered]@{}

# ---------------------------------------------------------------- coverage contract (mode)
# The normalized arg set, NOT a bare enum: -RetryOnly reruns only non-green batches, so it
# would otherwise carry mode=full and let a reuse satisfy a full request with reduced coverage.
# Dispatch artifacts (-IgnoreEditor, -WaitSec) are excluded: editor state is
# re-checked fresh at every gate entry. Three modes total since the width lattice was deleted
# (2026-08-20) — the reuse key is now trivially correct by construction.
$script:Mode = if ($StaticOnly) { 'static-only' } else { 'full' }
if ($RetryOnly)        { $script:Mode += '+retry-only' }
if ($NoBaselineUpdate) { $script:Mode += '+no-baseline-update' }

function Add-Detail { param([string] $Text) $script:Detail.Add($Text) }
function Emit {
    param([string] $Text)
    Write-Output $Text
    # Every phase line is also this run's registry heartbeat + label, so a peer
    # reading the registry sees WHAT we are doing, not just that we exist.
    Update-ActivityRecord -Label $Text
}

function Complete-Gate {
    param([string] $Verdict, [int] $Code)
    # Overlap-signature rollback candidates from the durable trigger log — for this run and for a
    # run whose verdict this session adopted. Read here, at the single exit writer, so no retry
    # pass or batch filter can drop one. Best-effort, like the ledger write below.
    try {
        if (Get-Command Get-RuntimeSlotTriggers -ErrorAction SilentlyContinue) {
            $trig = @(Get-RuntimeSlotTriggers -GateRun $runId)
            if ($script:ReusedFrom) { $trig += @(Get-RuntimeSlotTriggers -GateRun $script:ReusedFrom) }
            foreach ($t in $trig) {
                $sig   = if ($t -match '(OVERLAP_SIGNATURE=.*)$') { $Matches[1] } else { $t }
                $suite = if ($t -match ' label=(\S+) ') { $Matches[1] } else { '-' }
                Emit "ROLLBACK_TRIGGER suite=$suite $sig"
            }
            if ($trig.Count -gt 0) {
                Add-Detail ("## Overlap signature — rollback candidate`n`nA run overlapped a peer runtime run and hit a trigger kind. Act per .claude/skills/testing/reference/running.md §Runtime slots and parallel dev.`n`n" + (($trig | ForEach-Object { "    $_" }) -join "`n"))
            }
        }
    } catch { }
    $sessionId = if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $env:CLAUDE_CODE_SESSION_ID }
    $header = @(
        "# Regression Gate detail — $((Get-Date).ToUniversalTime().ToString('u'))",
        "",
        "VERDICT=$Verdict (exit $Code)",
        "SESSION=$sessionId",
        ""
    )
    Set-Content -Path $detailPath -Value (@($header) + @($script:Detail)) -Encoding utf8
    # Latest-run pointer: readers that know the stable path still work; the per-run file is
    # what the ledger record and REUSE point at. Single choke point — no concurrent-gate race.
    try { Copy-Item -Path $detailPath -Destination (Join-Path $logDir 'gate_last.md') -Force -ErrorAction Stop } catch { }
    # Ledger record — the ONLY writer of completed-run results. Written BEFORE the locks are released below, so a gate
    # waiting on this checkout takes the lock and finds this verdict for its REUSE re-check. Best-effort: a record-write
    # hiccup must never fail a gate.
    try {
        $ft = @($script:ReusedFailedTests)
        foreach ($k in $suiteResults.Keys) {
            $sr = $suiteResults[$k]
            if ($sr.r.failedTests) { $ft += @($sr.r.failedTests) }
        }
        $rec = [ordered]@{
            id             = $runId
            status         = $Verdict
            exitCode       = $Code
            # Early exits (preflight/BLOCKED) reach here before $script:RunTree is set; a null
            # deref would throw under StrictMode and the best-effort catch would swallow the
            # record. Guard: empty head/digest on early exits.
            head           = $(if ($script:RunTree) { $script:RunTree.head } else { '' })
            treeDigest     = $(if ($script:RunTree) { $script:RunTree.digest } else { '' })
            startedAt      = $script:RunStartedUtc
            finishedAt     = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
            mode           = $script:Mode
            pid            = $PID
            sessionId      = $sessionId
            baselineAction = $script:BaselineAction
            detail         = $detailPath
            reusedFrom     = $script:ReusedFrom
            failedTests    = $(if ($ft.Count -gt 0) { @($ft) } else { $null })
        }
        New-Item -ItemType Directory -Force -Path $ledgerDir | Out-Null
        Set-Content -Path (Join-Path $ledgerDir "$runId.result.json") -Value ($rec | ConvertTo-Json -Depth 5) -Encoding utf8
    } catch { }
    if ($script:TwinSource) {
        foreach ($line in @(Copy-TwinOutputsBack -Source $script:TwinSource -Twin $repo -Paths $script:GateOutputs -Hashes $script:TwinHashes)) { Emit $line }
    }
    Exit-TwinLock
    Exit-GateHeld
    Emit "DETAIL=$detailPath"
    Emit "VERDICT=$Verdict"
    exit $Code
}

# ---------------------------------------------------------------- small utils
# Reads a field off either a PSCustomObject (ConvertFrom-Json) or a dictionary
# without tripping Set-StrictMode's non-existent-property rule.
function Get-Prop {
    param($Obj, [string] $Name, $Default = $null)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] } else { return $Default }
    }
    if ($Obj.PSObject.Properties[$Name]) { return $Obj.$Name }
    $Default
}
function Format-Short { param([string] $S) if ($S -and $S.Length -ge 10) { $S.Substring(0, 10) } else { $S } }
# Engine probe — defined EARLY because the REUSE block (gate entry) calls it; the probe call
# with its hard-stops lives at engine preflight. The toolchain is not in the tree digest
# (.runsettings/GODOT_BIN is gitignored), so a reused verdict must vouch for the machine too.
function Format-Ver {
    param([string] $V)
    if (-not $V) { return $null }
    $p = $V.Split('.'); '{0}.{1}.{2}' -f $p[0], $p[1], $(if ($p.Count -ge 3) { $p[2] } else { '0' })
}
function Get-EngineProbe {
    $csproj  = Join-Path $repo '{{PROJECT_NAME}}.csproj'
    $runset  = Join-Path $repo '.runsettings'
    $sdk = $null; $engine = $null; $godotBin = $null
    if (Test-Path $csproj) {
        $m = [regex]::Match((Get-Content $csproj -Raw), 'Godot\.NET\.Sdk/([0-9][0-9.]*)')
        if ($m.Success) { $sdk = $m.Groups[1].Value }
    }
    if (Test-Path $runset) {
        $m = [regex]::Match((Get-Content $runset -Raw), '<GODOT_BIN>(.*?)</GODOT_BIN>')
        if ($m.Success) { $godotBin = $m.Groups[1].Value.Trim() }
    }
    if ($godotBin -and (Test-Path $godotBin)) {
        $verOut = (& $godotBin --version 2>$null | Select-Object -First 1)
        if ($verOut) { $engine = ($verOut -replace '\.(stable|beta|rc|dev).*$', '').Trim() }
    }
    @{ sdk = $sdk; engine = $engine; godotBin = $godotBin; ok = ($sdk -and $engine -and ((Format-Ver $sdk) -eq (Format-Ver $engine))) }
}
# Test-DigestMatch and Get-TreeDigest come from the digest module: the REUSE decision is the one place here whose wrong
# answer is a false green, so the tree comparison has one implementation and a test (.claude/tests/test_gate_tree_digest.ps1).
. (Join-Path $PSScriptRoot 'TreeDigest.ps1')

# ---------------------------------------------------------------- identity
# GodotProcess.ps1 is the ONLY home for "what is this Godot process, and whose
# checkout is it". Nothing below re-derives it.
$godotProcP = Join-Path $scripts 'GodotProcess.ps1'
if (-not (Test-Path $godotProcP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=GodotProcess.ps1-missing'
    exit 4
}
. $godotProcP
# The checkout lock, runtime slots and trigger log live in ONE home (it dot-sources GodotProcess.ps1).
$runtimeSlotsP = Join-Path $scripts 'RuntimeSlots.ps1'
if (-not (Test-Path $runtimeSlotsP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=RuntimeSlots.ps1-missing'
    exit 4
}
. $runtimeSlotsP
# Run-dir layout (TestResults/runs/<runId>/) lives in ONE home.
$runPoolP = Join-Path $scripts 'RunPool.ps1'
if (-not (Test-Path $runPoolP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=RunPool.ps1-missing'
    exit 4
}
. $runPoolP
$script:PoolRepo = $repo
# The Integration batch model (units, plan, state, manifest, retry selection, verdict lines) lives in ONE home.
$intBatchesP = Join-Path $scripts 'IntegrationBatches.ps1'
if (-not (Test-Path $intBatchesP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=IntegrationBatches.ps1-missing'
    exit 4
}
. $intBatchesP
# The activity-registry reader, the own-family predicate and the owner-root walk live in ONE home.
$peerActivityP = Join-Path $scripts 'PeerActivity.ps1'
if (-not (Test-Path $peerActivityP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=PeerActivity.ps1-missing'
    exit 4
}
. $peerActivityP
# C# comment/string masking and the test-attribute diff counter live in ONE home.
$csharpSourceP = Join-Path $scripts 'CSharpSource.ps1'
if (-not (Test-Path $csharpSourceP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=CSharpSource.ps1-missing'
    exit 4
}
. $csharpSourceP
# Walked now and passed to every child: a child of a detached gate has no session in its own chain, and its activity
# record must still name this gate's session. A -Detach child's own walk usually fails too (its launcher exits within
# about a second), so it keeps the live pair its launcher passed.
function Resolve-GateOwnerRoot {
    if ($o = Get-OwnerRoot) { return $o }
    $rootPid = 0
    if ([int]::TryParse([string] $env:HARNESS_OWNER_ROOT_PID, [ref] $rootPid) -and
        (Test-SlotProcessLive $rootPid $env:HARNESS_OWNER_ROOT_START)) {
        return [pscustomobject] @{ RootPid = $rootPid; RootStart = [string] $env:HARNESS_OWNER_ROOT_START }
    }
    $null
}
$script:GateOwnerRoot = Resolve-GateOwnerRoot
if ($script:GateOwnerRoot) {
    $env:HARNESS_OWNER_ROOT_PID = "$($script:GateOwnerRoot.RootPid)"
    $env:HARNESS_OWNER_ROOT_START = $script:GateOwnerRoot.RootStart
} else {
    Remove-Item Env:\HARNESS_OWNER_ROOT_PID, Env:\HARNESS_OWNER_ROOT_START -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- held checkout
# The gate holds this checkout's lock from entry to its verdict (RuntimeSlots.ps1 Enter-HeldCheckout): its pool's held
# children run --no-build on the tree it built, and no ad-hoc run rebuilds under them. Enter-GateHeld is the gate's ONE
# wait: it holds nothing while it waits. Every exit releases the lock: Complete-Gate and the finally below call
# Exit-GateHeld, which reaps the run dir first. A static-only run holds it from entry through its build only.
$script:HeldLease = $null
$script:RunDir = $null
function Invoke-RunDirReap {
    param([string] $Site, [hashtable] $Map)
    if (-not $script:RunDir) { return }
    # A held child's Godot whose test host already exited escapes the child's own-tree reap; its --log-file still
    # names this gate's run dir.
    $found = @(Get-RunDirGodot -RunDir $script:RunDir -Map $Map)
    foreach ($o in $found) { & taskkill /F /T /PID ([int] $o.ProcessId) *> $null }
    Emit "REAP site=$Site killed=$($found.Count) scope=run-dir"
    foreach ($o in $found) { Emit ("  kill  {0} {1} run-dir" -f $o.ProcessId, ([string] $o.Name -replace '\.exe$', '')) }
}

# Who keeps $Checkout from being taken, read-only: the lock holder (named by RuntimeSlots.ps1's holder record), a live
# editor or playtest (unless -IgnoreEditor), then a foreign test runner that holds no lock. -LateOnly skips the lock
# probe, for a caller that already holds the lock. -Map is the process table (default: a fresh snapshot).
function Get-CheckoutHolders {
    param([Parameter(Mandatory)] [string] $Checkout, [switch] $LateOnly, [hashtable] $Map)
    $out = [System.Collections.Generic.List[object]]::new()
    $mk = {
        param([string] $Kind, [int] $HolderPid, [string] $Session, $Since, [string] $Label, [string] $Source)
        $held = 0
        if ($null -ne $Since) { try { $held = [int][math]::Max(0, ((Get-Date).ToUniversalTime() - ([datetime]$Since).ToUniversalTime()).TotalSeconds) } catch { } }
        [pscustomobject]@{ Kind = $Kind; Pid = $HolderPid; Session = $(if ($Session) { $Session } else { '-' }); Since = $Since; HeldSec = $held; Label = $(if ($Label) { $Label } else { '-' }); Source = $Source }
    }
    $reg = @()
    if (-not $LateOnly -and -not (Test-CheckoutLockFree -Repo $Checkout)) {
        $reg = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $activityDir)
        $rec = Get-CheckoutHolder -Repo $Checkout
        if ($rec) {
            $hp = [int] $rec.holderPid
            $act = @($reg | Where-Object { [int] $_.pid -eq $hp }) | Select-Object -First 1
            $kind = if ($act) { [string] $act.kind } else { 'run' }
            $sess = [string] $rec.sessionId
            if (-not $sess -and $act -and $act.PSObject.Properties['sessionId']) { $sess = [string] $act.sessionId }
            if (-not $sess -and $act -and $act.PSObject.Properties['ownerRootPid'] -and $act.ownerRootPid) { $sess = "owner:$($act.ownerRootPid)" }
            $out.Add((& $mk $kind $hp $sess $rec.acquiredAt ([string] $rec.label) 'lock'))
        } else {
            # No live record: an older-code holder, or a failed record write. The registry candidates are the only lock
            # takers that announce themselves, so they are an approximation.
            $cand = @($reg | Where-Object { $_.PSObject.Properties['checkout'] -and [string] $_.checkout -eq $Checkout })
            $out.Add((& $mk 'unrecorded' 0 '' $null $(if ($cand.Count -gt 0) { 'candidates:' + (($cand | ForEach-Object { $_.pid }) -join ',') } else { '' }) 'lock'))
        }
    }
    if (-not $Map) { $Map = Get-ProcSnapshot }
    if (-not $IgnoreEditor) {
        foreach ($e in @(Get-LiveEditor -Checkout $Checkout -Map $Map)) {
            $since = if ($e.PSObject.Properties['CreationDate']) { $e.CreationDate } else { $null }
            $out.Add((& $mk 'editor' ([int] $e.ProcessId) '' $since ([string] $e.Name) 'editor'))
        }
    }
    foreach ($r in @(Get-CheckoutTestRunners -Repo $Checkout -Map $Map -OwnRoots @($PID))) {
        $rp = [int] $r.ProcessId
        $owner = if ($reg.Count -gt 0) { @($reg | Where-Object { Test-OwnFamily -ProcessId $rp -OwnRoots @([int] $_.pid) -Map $Map }) | Select-Object -First 1 } else { $null }
        $since = if ($r.PSObject.Properties['CreationDate']) { $r.CreationDate } else { $null }
        $sess = if ($owner -and $owner.PSObject.Properties['sessionId']) { [string] $owner.sessionId } else { '' }
        $out.Add((& $mk 'test runner' $rp $sess $since $(if ($owner) { [string] $owner.label } else { [string] $r.Name }) 'runner'))
    }
    $out.ToArray()
}

# The runtime-slot holders, from the slot records: live, unreleased, not this gate. The slots are machine-wide.
function Get-SlotHolders {
    $dir = Get-RuntimeSlotRecordDir
    $seen = @{}
    $out = [System.Collections.Generic.List[object]]::new()
    foreach ($n in 0..($script:RuntimeSlotMax - 1)) {
        $rec = Read-SlotRecord -RecordDir $dir -Name "slot-$n"
        if ($null -eq $rec -or $rec -is [string]) { continue }
        if ($rec.PSObject.Properties['releasedAt'] -and $rec.releasedAt) { continue }
        if (-not (Test-SlotProcessLive $rec.holderPid $rec.procStart)) { continue }
        $hp = [int] $rec.holderPid
        if ($hp -eq $PID -or $seen.ContainsKey($hp)) { continue }
        $seen[$hp] = $true
        $held = 0
        try { $held = [int][math]::Max(0, ((Get-Date).ToUniversalTime() - ([datetime] $rec.acquiredAt).ToUniversalTime()).TotalSeconds) } catch { }
        $out.Add([pscustomobject]@{ Kind = 'slot'; Pid = $hp; Session = '-'; Since = $rec.acquiredAt; HeldSec = $held; Label = [string] $rec.label; Source = 'slot' })
    }
    $out.ToArray()
}

function Format-HolderSince { param($When) if ($null -eq $When -or '' -eq $When) { return '-' }; try { ([datetime] $When).ToUniversalTime().ToString('HH:mm:ssZ') } catch { '-' } }
function Format-HolderLine {
    param($H)
    $pidText = if ($H.Kind -eq 'unrecorded') { '-' } else { "$($H.Pid)" }
    $sess = if ([string] $H.Session -and ([string] $H.Session).Length -gt 8 -and ([string] $H.Session) -notlike 'owner:*') { ([string] $H.Session).Substring(0, 8) } else { [string] $H.Session }
    "holder=$(([string] $H.Kind) -replace ' ', '-') pid=$pidText session=$sess since=$(Format-HolderSince $H.Since) held=$($H.HeldSec)s label=`"$(([string] $H.Label) -replace '"', "'")`""
}

# The step the BUSY line names, by what held the checkout.
function Get-SlotNext {
    param([object[]] $Holders = @())
    $pids = @($Holders | ForEach-Object { $_.Pid })
    if ($pids.Count -gt 0) { "re-invoke with -RetryOnly after slot holders $($pids -join ',') end; never kill them" }
    else { 're-invoke with -RetryOnly: the slot holders left as the gate ended' }
}
function Get-BusyNext {
    param($H)
    switch ($H.Source) {
        'lock' {
            if ($H.Kind -eq 'unrecorded') { 're-invoke after the run holding this checkout ends (' + $(if ($H.Label) { $H.Label } else { 'no candidate recorded' }) + '); never kill it' }
            else { "re-invoke after pid $($H.Pid) ends (raise -WaitSec to wait longer); never kill it" }
        }
        'editor' {
            if ($script:TwinSyncReason) { "fix the twin sync ($(([string] $script:TwinSyncReason) -replace '"', "'")) or close the editor, then re-invoke" }
            else { 'close the editor, or re-invoke: a fresh gate routes a live checkout to its twin' }
        }
        'runner' { "wait for pid $($H.Pid) to end (it holds no lock: an older wrapper or a raw dotnet test); never kill it" }
        default { 'wait for the holder to end, then re-invoke; never kill it' }
    }
}

# The gate's one BUSY verdict (exit 7, no verdict): one BUSY line, a detail section naming every holder, then the verdict.
# Nothing failed; the holders are never touched.
function Complete-Busy {
    param([Parameter(Mandatory)] [string] $Site, [object[]] $Holders = @(), [Parameter(Mandatory)] [string] $Next, [int] $WaitedSec = 0)
    $hs = @($Holders)
    $line = if ($hs.Count -gt 0) { Format-HolderLine $hs[0] } else { 'holder=none pid=- session=- since=- held=0s label="-"' }
    Emit "BUSY site=$Site $line waited=${WaitedSec}s others=$([math]::Max(0, $hs.Count - 1)) next=`"$($Next -replace '"', "'")`""
    $list = if ($hs.Count -gt 0) { ($hs | ForEach-Object { "- $(Format-HolderLine $_)" }) -join "`n" } else { '- (the holder left before the final probe)' }
    Add-Detail "## Busy — no verdict`n`nSite: $Site. Waited ${WaitedSec}s of -WaitSec $WaitSec.`n`n$list`n`nNext: $Next`n`nNothing failed; this is not a regression signal. Never kill a holder."
    Complete-Gate 'BUSY' 7
}

# The gate's one wait. Returns holding $Checkout's lock, with no live editor on it (unless -IgnoreEditor) and no foreign
# test runner on it. Until then it holds nothing: no lease, no twin lock, no runtime slot, and its activity record says
# kind=waiter. At -WaitSec it ends the gate BUSY (exit 7), naming the holder. It only reads; it never kills or signals a
# holder. The lock is taken in 5 s slices: a release (or an abandonment) is seen at once, and the one probe made while
# holding it (an editor or runner that slipped in) lets it go again before any sleep.
function Enter-GateHeld {
    param([Parameter(Mandatory)] [string] $Checkout, [Parameter(Mandatory)] [ValidateSet('entry', 'twin', 'source')] [string] $Site)
    $lease = Get-Variable -Scope Script -Name HeldLease -ValueOnly -ErrorAction SilentlyContinue
    $twinLock = Get-Variable -Scope Script -Name TwinLock -ValueOnly -ErrorAction SilentlyContinue
    if ($lease -or $twinLock) { throw "Enter-GateHeld($Site) entered holding a lock" }
    $script:ActivityKind = 'waiter'
    $t0 = Get-Date
    $deadline = $t0.AddSeconds($WaitSec)
    $state = @{ Key = ''; Next = $t0 }
    $say = {
        param($hs)
        $now = Get-Date
        $key = "$($hs[0].Kind):$($hs[0].Pid):$($hs.Count)"
        if ($key -eq $state.Key -and $now -lt $state.Next) { return }
        $state.Key = $key
        $state.Next = $now.AddSeconds($script:WaitTickSec)
        Emit ("WAIT site=$Site checkout=$(Split-Path -Leaf $Checkout) $(Format-HolderLine $hs[0]) others=$($hs.Count - 1) " +
              "waited=$([int]($now - $t0).TotalSeconds)s left=$([int][math]::Max(0, ($deadline - $now).TotalSeconds))s")
    }
    $h = @()
    while ($true) {
        $h = @(Get-CheckoutHolders -Checkout $Checkout)
        if ($h.Count -gt 0) { & $say $h }
        $lockOnly = ($h.Count -gt 0 -and @($h | Where-Object { $_.Source -ne 'lock' }).Count -eq 0)
        if ($h.Count -eq 0 -or $lockOnly) {
            $takeMs = [int][math]::Min(5000, [math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds))
            $l = Enter-HeldCheckout -Repo $Checkout -Label "gate $runId" -TimeoutMs $takeMs
            if ($l.Acquired) {
                $late = @(Get-CheckoutHolders -Checkout $Checkout -LateOnly)
                if ($late.Count -eq 0) {
                    $script:Waited = ($state.Key -ne '')
                    # A quiet checkout waited for nothing: the probe's own time is not a wait.
                    $waitedSec = if ($script:Waited) { [int]((Get-Date) - $t0).TotalSeconds } else { 0 }
                    Emit "WAIT site=$Site acquired=1 waited=${waitedSec}s abandoned=$([int][bool]$l.Abandoned)"
                    Emit "HELD site=enter acquired=1 waitMs=$($l.WaitMs) abandoned=$([int][bool]$l.Abandoned) reaped=$(@($l.Reaped).Count)"
                    $script:HeldLease = $l
                    $script:ActivityKind = 'gate'
                    Update-ActivityRecord -Label "held $Checkout"
                    return
                }
                Exit-HeldCheckout -Lease $l
                $h = $late
                & $say $h
            }
        } else {
            $left = ($deadline - (Get-Date)).TotalSeconds
            if ($left -gt 0) { Start-Sleep -Milliseconds ([int][math]::Min(5000, $left * 1000)) }
        }
        if ((Get-Date) -ge $deadline) { break }
    }
    if ($h.Count -eq 0) { $h = @(Get-CheckoutHolders -Checkout $Checkout) }
    $waited = [int]((Get-Date) - $t0).TotalSeconds
    $next = if ($h.Count -gt 0) { Get-BusyNext $h[0] } else { 're-invoke: the holder left as the wait ended' }
    Complete-Busy -Site $Site -Holders $h -WaitedSec $waited -Next $next
}
function Exit-GateHeld {
    if (-not $script:HeldLease) { return }
    $l = $script:HeldLease
    $script:HeldLease = $null
    try { Invoke-RunDirReap -Site 'held-exit' } catch { }
    try { Exit-HeldCheckout -Lease $l } catch { Write-Host "HELD release-error=$($_.Exception.Message)" }
}

# ---------------------------------------------------------------- activity registry
# Advisory telemetry only — never a correctness mechanism. Every write is
# swallowed, because a gate must not fail on a temp-file hiccup.
# The record's owner root (PeerActivity.ps1 Get-OwnerRoot) is the axis that tells "my session's run" apart from a
# concurrent session's when both share ONE checkout; it is captured at record-creation time, never re-derived later.
function Update-ActivityRecord {
    param([string] $Label)
    if (-not $script:ActivityOn) { return }
    try {
        $now = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
        $rec = [ordered]@{
            pid         = $PID
            procStart   = $script:ActivityProcStart
            # 'gate' means "I hold this checkout's lease and am consuming the build output and the test locks". A
            # gate waiting in Enter-GateHeld holds NEITHER, so it is 'waiter' until the lease is taken. Every reader
            # that keys on kind eq 'gate' (the cross-checkout PEERS report, Get-LivePeerActivity) therefore ignores a
            # waiting gate, which stays visible to operators without blocking anyone.
            kind        = $script:ActivityKind
            checkout    = $repo
            sessionId   = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } elseif ($env:CLAUDE_CODE_SESSION_ID) { $env:CLAUDE_CODE_SESSION_ID } else { $null })
            ownerRootPid   = $(if ($script:GateOwnerRoot) { $script:GateOwnerRoot.RootPid }   else { $null })
            ownerRootStart = $(if ($script:GateOwnerRoot) { $script:GateOwnerRoot.RootStart } else { $null })
            label       = $Label
            startedAt   = $script:ActivityStart
            heartbeatAt = $now
            # A waiting gate is deliberately idle for its whole -WaitSec window (default 1800s), so advertising a
            # gate's 600s made every long wait read as stale/hung to peers and to the activity hook. Advertise the
            # window each kind actually occupies.
            expectedSec = $(if ($script:ActivityKind -eq 'waiter' -and $WaitSec -gt 0) { $WaitSec } else { 600 })
        }
        Set-Content -Path $script:ActivityPath -Value ($rec | ConvertTo-Json -Depth 4) -Encoding utf8
    } catch { }
}
function Start-ActivityRecord {
    try {
        New-Item -ItemType Directory -Force -Path $activityDir | Out-Null
        $script:ActivityPath      = Join-Path $activityDir "gate-$PID.json"
        $script:ActivityStart     = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
        $script:ActivityProcStart = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        $script:ActivityOn        = $true
        Update-ActivityRecord -Label 'start'
    } catch { $script:ActivityOn = $false }
}
function Stop-ActivityRecord {
    try { if ($script:ActivityPath) { Remove-Item -Path $script:ActivityPath -Force -ErrorAction SilentlyContinue } } catch { }
}

# ---------------------------------------------------------------- godot adapters
# Field probes only — identity itself comes from GodotProcess.ps1. Kept local
# because the process-record shape (CIM vs Diagnostics.Process) is the module's
# choice, not this file's.
function Get-ProcPid {
    param($P)
    foreach ($n in @('ProcessId', 'Id')) {
        if ($null -ne $P -and $P.PSObject.Properties[$n]) { return [int]$P.$n }
    }
    0
}
# ---------------------------------------------------------------- detach arguments
function Get-GateArgs {
    # Plain array output, no comma-wrap: the -Detach launcher appends to it and hands it to Start-DetachedPwshScript,
    # and a wrapped return would nest ([["-StaticOnly"]]) and die on an Object[] element inside ArgumentList.
    $a = @('-Repo', $repo)
    if ($StaticOnly)       { $a += '-StaticOnly' }
    if ($NoBaselineUpdate) { $a += '-NoBaselineUpdate' }
    if ($RetryOnly)        { $a += '-RetryOnly' }
    if ($NoReuse)          { $a += '-NoReuse' }
    $a
}

function Assert-TreeStable {
    # The gate builds ONCE (BUILD=OK above) and its held children run --no-build against that
    # build. A concurrent session's mid-run edit leaves the verdict describing a tree that no
    # longer exists, while BUILD=OK at the top vouches for it. The digest re-check names the
    # cause instead. The gate's own artifacts
    # (baseline, durations manifest, TestResults/) and the agent-artifact trees
    # (.claude/scratch/, .claude/auto-memory/, .claude/plans/) are excluded from the
    # digest, so the gate cannot trip itself — and a peer session's memory/plan writes
    # (design drives) are not under test either.
    param([string] $Phase)
    $now = Get-TreeDigest -Repo $repo
    if (Test-DigestMatch $now.digest $script:RunTree.digest) {
        if ($now.head -ne $script:RunTree.head) {
            # Equal content under a moved HEAD: a peer committed bytes already on disk or
            # digest-excluded paths, so nothing tested changed. Write-Host, NOT Emit: this path
            # RETURNS, and a caller that captures its output would receive the line as a value.
            $line = "TREE_HEAD_MOVED phase=$Phase from=$(Format-Short $script:RunTree.head) to=$(Format-Short $now.head) action=continue"
            Write-Host $line
            Update-ActivityRecord -Label $line
            $script:RunTree = $now
        }
        return
    }
    # Name the delta: an INVALID that doesn't say WHICH path moved forces post-hoc
    # archaeology against a transient state that may already be gone.
    $delta = @()
    try {
        $startE = @($script:RunTree.entries); $nowE = @($now.entries)
        $delta = @(Compare-Object -ReferenceObject $startE -DifferenceObject $nowE |
                   ForEach-Object { "$(if ($_.SideIndicator -eq '=>') { 'NOW ' } else { 'WAS ' })$($_.InputObject)" })
    } catch { $delta = @("(entry diff unavailable: $($_.Exception.Message))") }
    Emit "TREE_CHANGED=1 phase=$Phase delta=$($delta.Count)"
    foreach ($d in @($delta | Select-Object -First 20)) { Emit "TREE_DELTA $d" }
    Add-Detail ("## Tree changed mid-run ($Phase)`n`nThe working tree's digest differs from the run-start digest. A concurrent session's edit (or your own edit under test) invalidates every result after the change — later batches would fail with compile errors that present as test failures. Results are INVALID, not a regression. Re-run when the tree is stable.`n`nChanged entries:`n" + (($delta | Select-Object -First 50) -join "`n"))
    Complete-Gate 'INVALID' 2
}

function Test-EditorAppeared {
    if ($IgnoreEditor) { return $false }
    @(Get-LiveEditor -Checkout $repo -Map (Get-ProcSnapshot)).Count -gt 0
}
function Complete-EditorAppeared {
    param([string] $Phase)
    Emit "EDITOR detected=1 phase=$Phase"
    Add-Detail "A Godot editor appeared on this checkout at phase '$Phase'. The gate and the editor share ``.godot/mono/temp/bin/Debug/{{PROJECT_NAME}}.dll``, so they are mutually destructive; the editor wins unconditionally. The run is torn down and ends BUSY."
    $eds = @(Get-CheckoutHolders -Checkout $repo -LateOnly | Where-Object { $_.Kind -eq 'editor' })
    Complete-Busy -Site 'editor-midrun' -Holders $eds -Next 're-invoke: a fresh gate routes a live checkout to its twin'
}
function Assert-NoEditor {
    param([string] $Phase)
    if (Test-EditorAppeared) { Complete-EditorAppeared -Phase $Phase }
}

# exit != 0 with no 'Failed: N, Passed: M' line is the documented external-kill
# signature — but ONLY a signature. It becomes CONTENTION when a live peer
# harness run overlapped the window, never on the signature alone.
function Get-SuitePeers {
    # Suite-boundary peer snapshot + $script:PeersSeen tracker in one call: a peer that
    # contended with a suite and then exited is the common shape, and the INCOMPLETE->BUSY
    # conversion needs the overlap fact (and the peer's record, to name it) even after the peer is gone.
    $p = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $activityDir)
    if ($p.Count -gt 0) {
        $script:PeersSeen = $true
        $script:PeersSeenList = @($script:PeersSeenList) + @($p)
    }
    $p
}

function Assert-NotContention {
    # $PeersAtStart is what makes this correct. Sampling only at call time — i.e. AFTER the suite has
    # finished — misses the peer that contended and then exited, which is the common shape: the peer's
    # run ending is often exactly what let ours finish. Measured 2026-08-13: Logic burned its 480s cap
    # against a concurrent gate, that gate exited first, the live peer read returned empty, and an
    # obvious contention artifact was adjudicated as a real UNPARSED result. Contention holds if a peer
    # was live at EITHER end of the window.
    param($R, [datetime] $StartedAt, [string] $Label, $PeersAtStart = @())
    # LOCK_TIMEOUT/LOCKED are the runner's own machine-busy statuses — the LOCKED tier owns their
    # classification deterministically. CONTENTION stays for the external-kill signature only;
    # adjudicating a known lock expiry against the advisory registry is what re-opens the
    # INVALID path the LOCKED tier closes.
    if ((Get-Prop $R 'status' '') -in @('LOCK_TIMEOUT', 'LOCKED')) { return }
    if ([int](Get-Prop $R 'exit'   -Default 0)  -eq 0) { return }
    if ([int](Get-Prop $R 'passed' -Default -1) -ge 0) { return }
    $peers = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $activityDir)
    if ($peers.Count -eq 0) { $peers = @($PeersAtStart) }
    if ($peers.Count -eq 0) { return }
    Emit "SUITE $Label contention=1 peers=$($peers.Count)"
    Add-Detail "## $Label — CONTENTION`n"
    Add-Detail "The wrapper exited non-zero and printed no ``Failed: N, Passed: M`` line (the external-kill signature), AND a live peer harness run overlapped this suite (started $($StartedAt.ToString('u'))). This is machine contention, not a regression — the counts prove nothing."
    foreach ($p in $peers) {
        Add-Detail ("- peer pid={0} kind={1} label='{2}' checkout={3} startedAt={4} session={5}" -f `
            (Get-Prop $p 'pid' '?'), (Get-Prop $p 'kind' '?'), (Get-Prop $p 'label' '?'),
            (Get-Prop $p 'checkout' '?'), (Get-Prop $p 'startedAt' '?'), (Get-Prop $p 'sessionId' 'n/a'))
    }
    Add-Detail 'Re-run once the peer clears. A genuine defect reproduces without the peer and reports as FAIL.'
    Complete-Gate 'CONTENTION' 8
}

# ---------------------------------------------------------------- detached spawn
# Nothing above this point takes a lock, writes a record, or spawns work, so the parent can
# hand off here and leave without anything to unwind. The child runs the whole wait.
if ($Detach) {
    $outPath = Join-Path $logDir "gate_run_$runId.out"
    $errPath = Join-Path $logDir "gate_run_$runId.err"
    $childArgs = @(Get-GateArgs)
    if ($IgnoreEditor) { $childArgs += '-IgnoreEditor' }
    $childArgs += @('-WaitSec', "$WaitSec")
    $child = Start-DetachedPwshScript -ScriptPath $PSCommandPath -Arguments $childArgs -OutPath $outPath -ErrPath $errPath
    # The stage watcher's liveness signal: the run's own pid and start time beside its OUT (gate_run_<id>.pid).
    try { "pid=$($child.Id) start=$($child.StartTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" | Set-Content -LiteralPath ([System.IO.Path]::ChangeExtension($outPath, '.pid')) -Encoding ascii } catch { }
    Write-Output "DETACHED pid=$($child.Id) mode=$($script:Mode)"
    Write-Output "OUT=$outPath"
    Write-Output "ERR=$errPath"
    Write-Output 'WAIT=poll OUT until a line begins VERDICT= (the run is over only then)'
    exit 0
}

# ---------------------------------------------------------------- stage watcher
# The supported Monitor for `-Detach`. Read-only: no lock, no record, no gate. It streams each
# stage line and each finished-batch transition once, then returns on the run's VERDICT=. Batch rows stream only from a
# state file whose `runId` is the run named by OUT (`gate_run_<runId>.out`); the file persists across runs, so any other
# run's rows are stale. When the
# `.pid` sidecar `-Detach` wrote beside OUT names a process that is gone and OUT holds no VERDICT=, it
# prints one dead-run line and sets $script:WatchExit to 1; an OUT with no sidecar is only ever ended by VERDICT=.
function Invoke-StageWatch {
    param([string] $OutPath, [string] $BatchPath = '', [int] $IntervalSec = 10)
    $script:WatchExit = 0
    $pidFile = [System.IO.Path]::ChangeExtension($OutPath, '.pid')
    if (-not $OutPath) { throw 'stage watch needs -Out <the detached run OUT path>' }
    if ($IntervalSec -lt 1) { throw '-WatchIntervalSec must be a positive integer' }
    $stage = '^(REAP|PEERS|WAIT |BUSY |ENGINE|GUARDS|BUILD|DOCS|HELD|TRX_DIR|QUARANTINE|POOL|RETRY|UNIT|SUITE|BATCH|EDITOR|IMPORT_GATE|BASELINE|TREE_CHANGED|GATE_ERROR|VERDICT)'
    $watchedRun = if ($OutPath -match '(?:^|[\\/])gate_run_(.+)\.out$') { $Matches[1] } else { '' }
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    while ($true) {
        $done = $false
        $gone = $false
        if (Test-Path -LiteralPath $pidFile) {
            $rec = Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue
            if ($rec -match 'pid=(\d+)') {
                $proc = Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue
                $gone = $null -eq $proc
                if (-not $gone -and $rec -match 'start=(\S+)') {
                    $began = [datetime]::Parse($Matches[1], [cultureinfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
                    $gone = [math]::Abs(($proc.StartTime.ToUniversalTime() - $began).TotalSeconds) -gt 2
                }
            }
        }
        foreach ($m in @(Select-String -Path $OutPath -Pattern $stage -ErrorAction SilentlyContinue)) {
            $line = $m.Line.TrimEnd("`r")
            if ($line -and $seen.Add($line)) {
                Write-Output $line
                if ($line.StartsWith('VERDICT=')) { $done = $true }
            }
        }
        if ($BatchPath -and (Test-Path -LiteralPath $BatchPath)) {
            $doc = $null
            try { $doc = Get-Content -LiteralPath $BatchPath -Raw | ConvertFrom-Json } catch { }
            $docRun = if ($null -ne $doc -and $doc.PSObject.Properties['runId']) { [string]$doc.runId } else { '' }
            $batches = @(if ($docRun -and $docRun -eq $watchedRun) { $doc.batches } else { @() })
            foreach ($b in $batches) {
                if ($null -eq $b -or $b.status -eq 'PENDING') { continue }
                $line = '{0} {1} passed={2} failed={3}' -f $b.label, $b.status, $b.passed, $b.failed
                if ($seen.Add($line)) { Write-Output $line }
            }
        }
        if ($done) { return }
        if ($gone) {
            Write-Output "WATCH_DEAD the detached run exited without a VERDICT= OUT=$OutPath ERR=$([System.IO.Path]::ChangeExtension($OutPath, '.err')) next: rerun the gate"
            $script:WatchExit = 1
            return
        }
        Start-Sleep -Seconds $IntervalSec
    }
}

if ($Watch) {
    $batchPath = if ($Batches) { $Batches } else { Join-Path $logDir 'integration_batches.json' }
    Invoke-StageWatch -OutPath $Out -BatchPath $batchPath -IntervalSec $WatchIntervalSec
    exit $script:WatchExit
}

# ---------------------------------------------------------------- cross-session REUSE
# A peer's completed verdict for byte-identical content + the same coverage contract + a live
# toolchain probe IS this run's verdict — the cheapest way to stop two gates fighting is for
# the second not to run. Called at gate entry, and again once the gate holds the checkout after a wait or a twin
# sync: a gate that waited behind a peer's run takes the lock after the peer's ledger record landed (Complete-Gate
# writes it before it releases), so the second look finds the verdict the wait was for. Never fires for -NoReuse;
# always re-probes the engine (the gitignored toolchain is not in the digest). A reused FAIL
# carries the producer's failing-test list so the exit-1 adjudication presents real failures.
function Invoke-LedgerReuse {
    if ($NoReuse) { return }
    $curDigest = (Get-TreeDigest -Repo $repo).digest
    $nowUtc    = (Get-Date).ToUniversalTime()
    $best      = $null
    foreach ($f in @(Get-ChildItem -Path $ledgerDir -Filter '*.result.json' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $rec = $null
        try { $rec = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        if ((Get-Prop $rec 'mode' '') -ne $script:Mode) { continue }
        if ((Get-Prop $rec 'status' '') -notin @('PASS', 'FAIL', 'WARN', 'STATIC_PASS')) { continue }
        if (-not (Test-DigestMatch ([string](Get-Prop $rec 'treeDigest' '')) $curDigest)) { continue }
        $fin = $null
        try { $fin = ([datetime](Get-Prop $rec 'finishedAt' '')).ToUniversalTime() } catch { }
        if (-not $fin) { continue }
        if (($nowUtc - $fin).TotalHours -gt 6) { continue }
        $best = $rec; break
    }
    if ($best -and (Get-EngineProbe).ok) {
        $from = [string](Get-Prop $best 'id' '?')
        $ageM = [int](($nowUtc - ([datetime](Get-Prop $best 'finishedAt' ''))).TotalMinutes)
        $script:ReusedFrom = $from
        # The reused run's own record must carry the CURRENT tree (a null RunTree would throw under
        # StrictMode and the record would never land). The producer's verdict is only valid for its own
        # digest, which the scan already verified matches this one.
        $script:RunTree = Get-TreeDigest -Repo $repo
        $producerAction = [string](Get-Prop $best 'baselineAction' 'unknown')
        Emit "REUSE from=$from mode=$($script:Mode) age=${ageM}m session=$(Get-Prop $best 'sessionId' 'n/a')"
        Emit "BASELINE reused=$producerAction"
        Add-Detail "## REUSE`n`nThis run reused the verdict of run **$from** (${ageM}m old): content-exact digest match, identical mode ($($script:Mode)), engine probe re-run and OK. The producer's baseline action: $producerAction. A reused verdict is a run against byte-identical content and toolchain — report it as such, never as a fresh run."
        if ((Get-Prop $best 'status' '') -eq 'FAIL') {
            $pft = @(Get-Prop $best 'failedTests' @())
            if ($pft.Count -gt 0) {
                $script:ReusedFailedTests = @($pft)
                Add-Detail "### Failing tests (from the producer's run)"
                $pft | ForEach-Object { Add-Detail "- $_" }
            } else {
                Add-Detail "Producer record carries no failing-test list; its detail file: $(Get-Prop $best 'detail' '?')"
            }
        }
        Complete-Gate ([string](Get-Prop $best 'status' 'PASS')) ([int](Get-Prop $best 'exitCode' 0))
    }
}

Start-ActivityRecord
try {

# ---------------------------------------------------------------- entry: reuse, route, wait
# REUSE first, before any wait: it takes no locks, spawns nothing and touches no build output, so it is safe beside a
# live editor and a busy checkout; its only external call is the engine probe. The route is decided once, here, with no
# sync and no lock. Then Enter-GateHeld waits (holding nothing) for the routed checkout and takes its lock; a twin is
# synced only under its own lock, which closes the twin-lock deadlock by construction. Logs and ledger rows live in the
# source's scratch until the sync re-points the gate at the twin.
Invoke-LedgerReuse
$route = [pscustomobject]@{ Path = $repo; Twin = $false }
if (-not $IgnoreEditor) {
    try { $route = Get-RunRoute -Repo $repo }
    catch { $script:TwinSyncReason = $_.Exception.Message; Emit "TWIN_SYNC_FAILED source=$repo reason=$($script:TwinSyncReason)" }
}
if ($route.Twin) {
    # NOT $twinSource: variable names are case-insensitive, so that would be $script:TwinSource, which means "synced".
    $srcRepo = $repo
    Enter-GateHeld -Checkout $route.Path -Site twin
    $script:TwinHashes = Get-TwinOutputHashes -Source $srcRepo -Paths $script:GateOutputs
    try {
        $twinSynced = Sync-TwinCheckout -Source $srcRepo
        $script:TwinSource = $srcRepo
        Emit "TWIN=$($twinSynced.Path) source=$srcRepo digest=$($twinSynced.Digest)"
        Set-GateCheckout -Path $twinSynced.Path
    } catch {
        # The twin cannot be synced: the editor on the source is the holder. Let go of the twin before waiting on it.
        $script:TwinSyncReason = $_.Exception.Message
        $script:TwinHashes = $null
        Emit "TWIN_SYNC_FAILED source=$srcRepo reason=$($script:TwinSyncReason)"
        Exit-TwinLock
        Exit-GateHeld
        Enter-GateHeld -Checkout $srcRepo -Site source
    }
} else {
    Enter-GateHeld -Checkout $repo -Site entry
}
if ($script:Waited -or $route.Twin) { Invoke-LedgerReuse }

# ---------------------------------------------------------------- python
$py = Get-PythonCommand
if (-not $py) {
    Emit 'PREFLIGHT=BLOCKED reason=no-python-interpreter'
    Add-Detail 'None of python3/python/py resolved on PATH; the 1b-1h static guards cannot run.'
    Complete-Gate 'BLOCKED' 4
}

function Get-TestWorkInProgress {
    param([string] $Repo)
    $paths = @(& git -C $Repo status --porcelain -- Tests/ 2>$null | Where-Object {
        $_ -notmatch 'Tests/regression_baseline\.json' -and $_ -notmatch 'Tests/integration_batch_durations\.json'
    })
    return @{ readable = ($LASTEXITCODE -eq 0); paths = $paths }
}

# ---------------------------------------------------------------- baseline
if (-not (Test-Path $baselineP)) {
    Emit 'PREFLIGHT=BLOCKED reason=baseline-missing'
    Add-Detail "Tests/regression_baseline.json is absent. It should exist on any checked-out branch; do not synthesize one."
    Complete-Gate 'BLOCKED' 4
}
$baselineRaw = Get-Content -Path $baselineP -Raw
$baseline    = $baselineRaw | ConvertFrom-Json

# Baseline-trust check: the stamp must match the commit that last touched the
# file, or that commit's parent (the gate stamps HEAD at run time, and the file
# lands in the NEXT commit). Anything else means the counts were carried
# forward or hand-edited rather than produced by a green run at that state.
#
# Resolve every side to a FULL sha before comparing. %h abbreviates to whatever
# length is currently unambiguous and that length GROWS with the repo, so a
# stamp written at 8 chars stops string-matching the very same commit once git
# moves to 9 — a false UNTRUSTED that no amount of green runs can clear.
$stampTrust = 'OK'
$lastTouch  = (& git -C $repo log -1 --format=%H -- Tests/regression_baseline.json 2>$null)
$rawStamp   = "$($baseline.updated_on_commit)".Trim()
if ($lastTouch -and $rawStamp) {
    # Unresolvable stamp (rewritten history, hand-typed) stays raw and fails below.
    $stamp = (& git -C $repo rev-parse --verify --quiet "$rawStamp^{commit}" 2>$null)
    if (-not $stamp) { $stamp = $rawStamp }
    if ($stamp -ne $lastTouch) {
        $parent = (& git -C $repo rev-parse --verify --quiet "$lastTouch^" 2>$null)
        if ($stamp -ne $parent) {
            $stampTrust = 'UNTRUSTED'
            Add-Detail "Baseline stamp ($rawStamp) is neither the last commit that touched the file ($lastTouch) nor its parent ($parent). Counts may be stale and may be riding over pre-existing reds. Tier-2 is advisory this run; a fully green run re-establishes the stamp and the counts."
        }
    }
}

# ---------------------------------------------------------------- preflight
# Orphan scope only — no -OwnRootPid, because this gate has launched nothing
# yet. A peer session's live runner has a live parent, is therefore not an
# Orphan, and is spared rather than killed out from under it.
$map     = Get-ProcSnapshot
$orphans = @(Get-ReapableGodot -Checkout $repo -Map $map)
# `scope=orphans-only`, not `spared=N`: Get-ReapableGodot enumerates ORPHANS, so a live-owner
# process (a peer session's runner, a peer worktree's, the user's own) is never a candidate here and
# cannot be reported as spared. Claiming a spare count this site cannot compute would be the same
# false reassurance `orphans_killed=0` gave. Per-suite reaping — which DOES classify live owners —
# reports its own REAP line from run_test_suite.ps1.
foreach ($o in $orphans) {
    $opid = Get-ProcPid $o
    if ($opid -gt 0) { & taskkill /F /T /PID $opid *> $null }
}
Emit "REAP site=preflight killed=$($orphans.Count) scope=orphans-only"
foreach ($o in $orphans) {
    $opid = Get-ProcPid $o
    if ($opid -gt 0) { Emit ("  kill  {0} {1} orphan" -f $opid, ($o.Name -replace '\.exe$', '')) }
}

# A peer on THIS checkout is not handled here: the entry wait (Enter-GateHeld) already took the checkout lock, so a peer
# gate, a suite wrapper or a foreign test runner on this checkout is a holder it waited out. Sparing a peer's runner in
# the orphan reap above is still right: it has a live parent.
#
# Cross-checkout gate preflight: REPORT only.
#
# A peer gate in a DIFFERENT worktree shares exactly one resource with us — the
# machine's runtime slots (RuntimeSlots.ps1), and only for runtime suites. Everything
# else is per-checkout: its own .godot/mono/temp/bin/Debug build output, its own
# checkout lock, its own salted GdUnit4 pipe. Waiting on that record would serialize
# preflight, guards, build, DOCS and every Logic suite — all fully parallel work — to
# protect a contention that already has a correct, finer-grained mechanism: runtime
# suites wait for a slot, and a genuine starvation degrades to the LOCKED tier and ends BUSY.
$machineGates = @(Get-LivePeerActivity -OwnRoots @($PID) -ActivityDir $activityDir | Where-Object {
    (Get-Prop $_ 'kind' '') -eq 'gate' -and (Get-Prop $_ 'checkout' '') -ne $repo
})
if ($machineGates.Count -gt 0) {
    # Emitted, not silent: runtime suites may still wait for a slot, and an operator
    # reading a slow run needs the cross-checkout gate named rather than inferred.
    $slotCount = (Get-RuntimeSlotCount -Repo $repo).Count
    Emit "PEERS site=preflight crossCheckoutGates=$($machineGates.Count) action=proceed runtimeSlots=$slotCount  (shares only the runtime slots; runtime suites may wait)"
    Add-Detail "A gate is live in another checkout ($($machineGates.Count) record(s)). This run proceeds: the two share only the runtime slots (runtimeSlots=$slotCount), so guards, build and Logic suites are parallel-safe. Runtime suites may wait for a slot, and a wait that expires surfaces as ``STATUS=LOCKED`` → the LOCKED tier → BUSY."
}

# Engine-version preflight: csproj SDK vs the engine .runsettings launches. The probe itself
# lives in Get-EngineProbe (defined above, near the utils) because REUSE also calls it.
# .runsettings is gitignored, so a fresh worktree has none: the loader's setup_runsettings is its one generator.
$rsStatus = Initialize-Runsettings -Repo $repo
if ($rsStatus -eq 'generated') { Emit 'RUNSETTINGS=generated from=.runsettings.template' }
elseif ($rsStatus) { Emit "RUNSETTINGS=$rsStatus" }
$probe = Get-EngineProbe
$sdk = $probe.sdk; $engine = $probe.engine; $godotBin = $probe.godotBin
if (-not $sdk -or -not $engine) {
    Emit "ENGINE=INCONCLUSIVE sdk='$sdk' engine='$engine' bin='$godotBin'"
    Add-Detail "Engine preflight inconclusive. An unresolved GODOT_BIN or missing binary means the suites cannot run correctly either — this is actionable, not ignorable. Fix the LOCAL side (.runsettings is gitignored and per-checkout); never edit the tracked csproj to match a local engine."
    Complete-Gate 'BLOCKED' 4
}
if (-not $probe.ok) {
    Emit "ENGINE=MISMATCH sdk=$sdk engine=$engine"
    Add-Detail "csproj Godot.NET.Sdk/$sdk vs engine $engine at $godotBin. STOP — a version disagreement surfaces as mass Integration failures scattered across unrelated domains, indistinguishable from a real regression. Point GODOT_BIN at an engine matching csproj, or install it. Bump csproj ONLY when deliberately upgrading (and bump session_context_loader.py GODOT_VERSION with it). NOTE: a mismatched engine regenerates the csproj SDK line to match itself on boot/import — if csproj is dirty and you didn't edit it, that is this drift, not a real edit."
    Complete-Gate 'BLOCKED' 4
}
Emit "ENGINE=OK ver=$sdk"

# ---------------------------------------------------------------- static guards
$guardRun = Invoke-StaticGuards -Repo $repo -Hooks $hooks -Py $py
foreach ($line in $guardRun.Details) { Add-Detail $line }
Emit "GUARDS $($guardRun.Results -join ' ')"
if ($guardRun.Unavailable) { Complete-Gate 'INVALID' 2 }
if ($guardRun.Blocked.Count -gt 0) {
    Add-Detail "Each guard prints its own remediation above. Treat a guard red exactly like a build failure — these catch green-build/runtime-throw shapes (null-stripped value Exports, stripped script bindings, missing [Tool], un-gated suites) that no test can catch."
    Complete-Gate 'BLOCKED' 4
}

# ---------------------------------------------------------------- build
# Warnings stay IN $buildOut (no -consoleLoggerParameters:ErrorsOnly) so the DOCS check below can
# read them. Costs nothing visible: $buildOut is a captured variable, printed only on build failure.
Assert-NoEditor -Phase 'build'
# Computed at run start, before the build — this is the tree the verdict is readable against.
$script:RunTree = Get-TreeDigest -Repo $repo
Emit ("TREE head={0} digest={1}" -f (Format-Short $script:RunTree.head), (Format-Short $script:RunTree.digest))
$buildOut  = & dotnet build $repo 2>&1
$buildCode = $LASTEXITCODE
if ($buildCode -ne 0) {
    Emit "BUILD=FAIL exit=$buildCode"
    Add-Detail "## Build failure`n`n``````"
    Add-Detail (($buildOut | Out-String).TrimEnd())
    Add-Detail '``````'
    Complete-Gate 'BLOCKED' 4
}
Emit 'BUILD=OK'
if ($script:HeldLease) {
    Set-CheckoutStamp -Repo $repo -Name built
    Emit 'HELD stamp=built'
}
if ($StaticOnly) { Exit-GateHeld }

# ---------------------------------------------------------------- doc comments (DOCS)
# XML-doc defects ship in a green build: the compiler reports them, ErrorsOnly used to discard the
# message. Measured 2026-08-12 before this check existed: 173 defects across 116 files, including 56
# [ExportGroup]-orphaned summaries — each one an Inspector tooltip the author wrote and the designer
# never saw. Codes match {{PROJECT_NAME}}.csproj's own stated worklist. Scope is whatever this build
# compiled, which is the newly-changed code a commit-time gate should police; sweep the whole tree
# on demand with .claude/scripts/doc_warning_check.sh. Doctrine: rules/csharp_patterns.md.
$docCodes = 'CS1587|CS1574|CS1734|CS1570|CS1572|CS1711|CS0419'
$docHits = @(($buildOut | Out-String) -split "`r?`n" |
    Select-String -Pattern "\((\d+),(\d+)\): warning ($docCodes)" |
    ForEach-Object { ($_.Line -replace '^\s*\d+>', '' -replace '\s*\[[^\]]*\]\s*$', '').Trim() } |
    Where-Object { $_ -notmatch 'examples_dd3d|debug_draw_3d' } |
    Sort-Object -Unique)

if ($docHits.Count -gt 0) {
    Emit "DOCS=FAIL n=$($docHits.Count)"
    Add-Detail "## Doc-comment defects`n"
    Add-Detail 'A `///` that names code which does not resolve, or sits where the compiler drops it.'
    Add-Detail 'CS1587 on an `[Export]` = a silently-missing Godot Inspector tooltip.'
    Add-Detail "`n``````"
    Add-Detail (($docHits | Select-Object -First 40) -join "`n")
    Add-Detail '``````'
    Add-Detail 'Rule: `.claude/rules/csharp_patterns.md` §Core Conventions. Full-tree sweep: `.claude/scripts/doc_warning_check.sh`.'
    Complete-Gate 'BLOCKED' 4
}
Emit 'DOCS=OK'

if ($StaticOnly) {
    $script:BaselineAction = 'skipped:static-only'
    Add-Detail 'Static-only run: preflight, guards 1b-1h, and build passed. The three suites and the headless import gate did NOT run — this does NOT satisfy the gate for a .cs change.'
    Complete-Gate 'STATIC_PASS' 0
}

# ------------------------------------------- 4b. [Tool] cascade import gate
# Runs before the suites so none of them loads resources from a stale import cache (a submodule move or new
# script classes since the last import). The import process exits before Sanity starts.
# Catches what the static gate (1c) cannot: Node cascades, escape-hatch inline
# placements ([Export] Resource?), and Jmodot-side gaps.
if ($true) {
    Assert-TreeStable -Phase 'import-gate'
    Assert-NoEditor -Phase 'import-gate'
    # Orphan scope: no suite has started, so anything of OURS is gone, and anything
    # still alive on this checkout with a live parent belongs to a peer session.
    foreach ($o in @(Get-ReapableGodot -Checkout $repo -Map (Get-ProcSnapshot))) {
        $opid = Get-ProcPid $o
        if ($opid -gt 0) { & taskkill /F /T /PID $opid *> $null }
    }
    $importLog = Join-Path $repo 'logs\tool_import_gate.log'
    New-Item -ItemType Directory -Force -Path (Split-Path $importLog) | Out-Null
    # $godotBin was resolved and existence-checked during engine preflight; an
    # empty path here would yield a vacuous PASS, so preflight already blocked.
    Set-AgentEngineUserData -Repo $repo
    & $godotBin --headless --import --path $repo *> $importLog
    $importCode = $LASTEXITCODE
    if ($importCode -ne 0) {
        Emit "IMPORT_GATE=FAIL exit=$importCode"
        Add-Detail "## Import failure`n`nThe headless ``--import`` exited $importCode. Full log: $importLog"
        Complete-Gate 'BLOCKED' 4
    }
    $cast = @(Select-String -Path $importLog -Pattern 'InvalidCastException|Unable to cast object of type' -ErrorAction SilentlyContinue)
    if ($cast.Count -gt 0) {
        Emit 'IMPORT_GATE=FAIL'
        Add-Detail "## [Tool] cascade import gate — FAIL`n"
        Add-Detail 'A non-`[Tool]` class sits under a `[Tool]` parent''s typed `[Export]` in a real .tres/.tscn — a cascade gap that currently throws. Add `[Tool]` to the offending target type and its concrete subclasses. Offending lines:'
        Add-Detail '```'
        $cast | Select-Object -First 10 | ForEach-Object { Add-Detail $_.Line.Trim() }
        Add-Detail '```'
        Add-Detail "Full log: $importLog"
        Complete-Gate 'FAIL' 1
    }
    Emit 'IMPORT_GATE=PASS'
}

# ---------------------------------------------------------------- suites
# One run dir for the whole gate: every suite child and the batcher write their TRX and Godot log under it.
# The gate owns it, so it runs retention (newest 2 gate dirs kept; a live owner's dir never deleted).
$runsParent = Join-Path $repo 'TestResults\runs'
$script:RunDir = New-RunDir -RunsRoot $runsParent -RunId "gate-$runId"
Remove-OldRunDirs -RunsRoot $runsParent
Emit "TRX_DIR=$script:RunDir"

# fqn -> first message line, from `FAILED_MSG=<fqn> :: <message>` lines (-Text) or the pool's `<fqn> :: <message>`
# entries (-Entries).
function Get-FailedMessages {
    param([string] $Text = '', [string[]] $Entries = @())
    $m = @{}
    foreach ($x in [regex]::Matches($Text, '(?m)^FAILED_MSG=(.+?) :: (.*?)\r?$')) { $m[$x.Groups[1].Value.Trim()] = $x.Groups[2].Value.Trim() }
    foreach ($e in $Entries) { if ($e -match '^(.+?) :: (.*)$') { $m[$Matches[1].Trim()] = $Matches[2].Trim() } }
    $m
}

# A pool result (RunPool.ps1) in the shape the tiers and the adjudication read. passed/failed are -1 when the wrapper
# printed no counts line, which tiers as UNPARSED; raw is the child's wrapper.out, for the reaper detail.
function ConvertTo-SuiteResult {
    param($P)
    $raw = if ($P.outPath -and (Test-Path -LiteralPath $P.outPath)) { [string](Get-Content -LiteralPath $P.outPath -Raw) } else { '' }
    [ordered]@{
        label = $P.label; raw = $raw; exit = $P.exit; status = $P.status; elapsed = $P.elapsedSec; outPath = $P.outPath
        passed = $(if ($P.counted) { [int] $P.passed } else { -1 }); failed = $(if ($P.counted) { [int] $P.failed } else { -1 })
        silentSkip = ($P.status -eq 'SILENT_SKIP'); failedTests = @($P.failedTests)
        failedMessages = (Get-FailedMessages -Entries @($P.failedMessages))
    }
}

# The wrapper's REAP lines: any that touched something go to stdout, all of them to the detail.
function Write-ReaperDetail {
    param($R)
    foreach ($m in [regex]::Matches([string] $R.raw, '(?m)^REAP site=(\S+) killed=(\d+) spared=(\d+)\r?$')) {
        if ([int] $m.Groups[2].Value -eq 0 -and [int] $m.Groups[3].Value -eq 0) { continue }
        Emit $m.Value.TrimEnd()
    }
    Add-Detail "## $($R.label) — reaper`n`n``````"
    Add-Detail ((([string] $R.raw) -split "`r?`n" | Where-Object { $_ -match '^(REAP |  kill |  spare )' }) -join "`n")
    Add-Detail '``````'
}

function Test-SuiteTiers {
    # $R is an OrderedDictionary from ConvertTo-SuiteResult or a Hashtable from the Integration verdict; leave it
    # untyped so neither is coerced.
    param($R, [string] $Label)
    $base     = [int]$baseline.suites.$Label.passed
    $sentinel = [int]$baseline.silent_skip_sentinels."${Label}_min"
    $warnAt   = [math]::Floor($base * [double]$baseline.drift_thresholds.warn_ratio)
    $hardAt   = [math]::Floor($base * [double]$baseline.drift_thresholds.hard_fail_ratio)
    # Parsed failures remain red even when the executor also reports a count shortfall.
    if ($R.passed -ge 0 -and $R.failed -gt 0) { return @{ tier = 'FAILING'; note = "failed=$($R.failed) — genuine test failure, not a count-drift signal" } }
    if ($R.silentSkip)            { return @{ tier = 'INVALID'; note = "silent-skip signature with no parsed failures" } }
    if ($R.status -in @('LOCK_TIMEOUT', 'LOCKED')) { return @{ tier = 'LOCKED'; note = "lock-wait window expired — a live holder owns this checkout's lock or every runtime slot" } }
    if ($R.status -in $script:IncompleteStatuses) { return @{ tier = 'INCOMPLETE'; note = "status=$($R.status): the child returned no readable result (its output: $($R.outPath)) — NOT a regression signal" } }
    if ($R.passed -lt 0)          { return @{ tier = 'UNPARSED'; note = "no 'Failed: N, Passed: M' line in wrapper output — do NOT treat as green" } }
    if ($R.passed -lt $sentinel)  { return @{ tier = 'INVALID'; note = "passed=$($R.passed) below architectural floor $sentinel — GodotRuntimeExecutor likely failed" } }
    if ($R.passed -lt $hardAt)    { return @{ tier = 'HARDFAIL'; note = "passed=$($R.passed) vs baseline $base ($([math]::Round(100*$R.passed/$base))%) — major drop" } }
    if ($R.passed -lt $warnAt)    { return @{ tier = 'WARN';     note = "passed=$($R.passed) vs baseline $base ($([math]::Round(100*$R.passed/$base))%) — moderate drop, needs user acknowledgement" } }
    if ($R.passed -lt $base)      { return @{ tier = 'PARTIAL';  note = "passed=$($R.passed) between sentinel and baseline $base — partial-but-clean flaky-executor shape" } }
    @{ tier = 'PASS'; note = '' }
}


$suiteResults = [ordered]@{}

# LOCKED conversion — the deterministic machine-busy verdict, keyed on the runner's own STATUS
# line and never on the advisory registry. A live holder — another session's gate, a raw suite
# run, or an unannounced consumer — owns every runtime slot. Nothing failed; the
# counts prove nothing. The gate ends BUSY (exit 7) at once, naming the live slot holders: the children already waited
# out their own lock window, and a second wait here would add a retry loop. The caller re-invokes with -RetryOnly.
# Called before the pool (a LOCKED Sanity: the pool would only re-wait the same slots) or from
# adjudication (a LOCKED Logic or Integration unit).
function Complete-LockedGate {
    $lockedSuite = $suiteResults.Keys | Where-Object { $suiteResults[$_].t.tier -eq 'LOCKED' } | Select-Object -First 1
    if ($lockedSuite -and $suiteResults[$lockedSuite].t.note) {
        Add-Detail "## $lockedSuite — LOCKED`n`n$($suiteResults[$lockedSuite].t.note)"
    }
    Add-Detail "## Machine-busy — test lock held`n`nAt least one suite reported LOCK_TIMEOUT/LOCKED: its lock-wait window expired while a live holder owned every runtime slot (the wrapper's STATUS=LOCK_TIMEOUT line names which). Not a regression. The gate ends BUSY and names the slot holders; re-invoke with -RetryOnly once they end."
    $slots = @(Get-SlotHolders)
    Complete-Busy -Site 'locked' -Holders $slots -Next (Get-SlotNext $slots)
}

# R1: a held child the wrapper refused (HELD_REFUSED) or whose recompile opt-out was ignored
# (SKIP_RECOMPILE_IGNORED) is a harness fault: INCOMPLETE naming each child's wrapper.out, never a
# regression and never BUSY. No child reports EDITOR_OPEN: every one runs -IgnoreEditor, and the pool's -OnPoll owns
# the mid-run editor check.
function Assert-UnitsAdmitted {
    param([object[]] $Results)
    $faults = @($Results | Where-Object { $_.status -in 'HELD_REFUSED', 'SKIP_RECOMPILE_IGNORED' })
    if ($faults.Count -gt 0) {
        Add-Detail "## Harness fault — a held child did not run as admitted`n"
        foreach ($f in $faults) {
            Emit "UNIT label=$($f.label) status=$($f.status) out=$($f.outPath)"
            Add-Detail "- ``$($f.label)``: status=$($f.status); wrapper output ``$($f.outPath)``"
        }
        Add-Detail "`nHELD_REFUSED: the wrapper refused a held child; its STATUS line names every unmet rule. SKIP_RECOMPILE_IGNORED: the consumed gdUnit4.api ignored GDUNIT4_SKIP_RECOMPILE, which brings back the concurrent editor-pass race. Both are harness faults, not regressions: fix the cause, then re-run."
        Complete-Gate 'INCOMPLETE' 6
    }
}

# Runs units as this gate's held children in one pool under its run dir; results land in $script:PoolResults. The
# pool's -OnPoll is the mid-run editor check (every 5 s): a hit ends the pool, which kills every child, before the gate
# hands off. Afterwards the run dir is reaped of any orphaned Godot and every child's admission is checked.
$script:PoolResults = @()
$script:EditorPollSw = [System.Diagnostics.Stopwatch]::StartNew()
function Invoke-GatePool {
    param([object[]] $Units, [string] $Phase, [switch] $RecompilePass)
    $gpRunDir = $script:RunDir
    $gpRecompile = [bool] $RecompilePass
    $w = Get-RunPoolWidth
    $width = [math]::Min([math]::Max(1, $w.Count), $Units.Count)
    Emit "POOL phase=$Phase units=$($Units.Count) width=$width slots=$($w.Count) source=$($w.Source)$(if ($gpRecompile) { ' recompilePass=1' })"
    $results = @()
    try {
        $results = Invoke-RunPool -Units $Units -ResultsRoot $gpRunDir `
            -Launch { param($u) Start-SuiteChild -Unit $u -ResultsRoot $gpRunDir -Held -RecompilePass:$gpRecompile } `
            -OnPoll {
                if ($script:EditorPollSw.ElapsedMilliseconds -lt 5000) { return }
                $script:EditorPollSw.Restart()
                if (Test-EditorAppeared) { throw 'GATE_EDITOR_APPEARED' }
            }
    } catch {
        if ($_.Exception.Message -ne 'GATE_EDITOR_APPEARED') { throw }
        Invoke-RunDirReap -Site "pool:$Phase"
        Complete-EditorAppeared -Phase "pool:$Phase"
    }
    Invoke-RunDirReap -Site "pool:$Phase"
    $script:PoolResults = @($results)
    Assert-UnitsAdmitted -Results $script:PoolResults
}

# The recompile pass completed when Sanity reached test execution. Its reds do not matter here.
function Test-RecompileEvidence {
    param($R)
    (-not $R.silentSkip) -and (($R.passed + $R.failed) -gt 0)
}
$script:Recompiled = $false
function Set-GateRecompiled {
    if ($script:Recompiled) { return }
    Set-CheckoutStamp -Repo $repo -Name recompiled
    $script:Recompiled = $true
    Emit 'HELD stamp=recompiled'
}

$suiteTimeoutMs = 480000

# --- Sanity: the one held child admitted before `recompiled` (-RecompilePass). It runs GdUnit4's single editor pass,
# and it is a 20 s smoke.
Assert-NoEditor -Phase 'suite:Sanity'
$t0 = Get-Date
$peers0 = @(Get-SuitePeers)
$sanityUnit = @{ label = 'Sanity'; filter = 'FullyQualifiedName~Tests.Sanity'; timeoutMs = $suiteTimeoutMs; expectSec = 0 }
Invoke-GatePool -Units @($sanityUnit) -Phase 'sanity' -RecompilePass
$r = ConvertTo-SuiteResult $script:PoolResults[0]
$t = Test-SuiteTiers -R $r -Label 'Sanity'
# Partial-but-clean: re-run that one suite ONCE before evaluating.
if ($t.tier -in @('PARTIAL', 'INVALID') -and $r.status -ne 'HANG') {
    if (Test-RecompileEvidence $r) { Set-GateRecompiled }
    Emit "SUITE Sanity rerun=1 reason=$($t.tier)"
    $t0 = Get-Date
    $peers0 = @(Get-SuitePeers)
    Invoke-GatePool -Units @(@{ label = 'Sanity_retry'; filter = $sanityUnit.filter; timeoutMs = $suiteTimeoutMs; expectSec = 0 }) `
        -Phase 'sanity-retry' -RecompilePass:(-not $script:Recompiled)
    $r = ConvertTo-SuiteResult $script:PoolResults[0]
    $t = Test-SuiteTiers -R $r -Label 'Sanity'
}
Assert-NotContention -R $r -StartedAt $t0 -Label 'Sanity' -PeersAtStart $peers0
$suiteResults['Sanity'] = @{ r = $r; t = $t }
Write-ReaperDetail $r
Emit ("SUITE Sanity passed={0} failed={1} tier={2} delta={3:+#;-#;+0} status={4} dur={5}s" -f `
      $r.passed, $r.failed, $t.tier, ($r.passed - [int]$baseline.suites.Sanity.passed), $r.status, $r.elapsed)
if (Test-RecompileEvidence $r) { Set-GateRecompiled }
Assert-TreeStable -Phase 'suite:Sanity'
if ($t.tier -eq 'LOCKED') {
    Emit 'SUITE Logic skipped=1 reason=sanity-locked'
    Emit 'SUITE Integration skipped=1 reason=sanity-locked'
    Complete-LockedGate
}
# A red Sanity predicts the pool's result (it is the smoke over the same build and import), so the pool is skipped.
$sanityRed = $t.tier -in @('FAILING', 'HARDFAIL')
if ($sanityRed) {
    Emit 'SUITE Logic skipped=1 reason=sanity-red'
    Emit 'SUITE Integration skipped=1 reason=sanity-red'
}
if (-not $script:Recompiled -and -not $sanityRed) {
    Emit 'SUITE Logic skipped=1 reason=no-recompile-evidence'
    Emit 'SUITE Integration skipped=1 reason=no-recompile-evidence'
    Add-Detail "## Recompile pass — no execution evidence`n`nSanity is the one held child that runs GdUnit4's editor (recompile) pass. The gate stamps the checkout recompiled only when Sanity reached test execution (passed + failed > 0, no silent-skip signature), and it did not (status=$($r.status) passed=$($r.passed) failed=$($r.failed)). Logic and Integration were not started: their held children skip the recompile, so they would have run against a project nobody recompiled."
}

# --- Logic and the Integration units: one held pool, longest first. Retries (Logic once on PARTIAL/INVALID; Integration's
# HANG and SILENT_SKIP units) run in a second held pool; RED never retries. Every unit is submitted at the start, so no
# wall-clock budget applies: each is bounded by the wrapper's -TimeoutMs and the pool's backstop.
$intTxt = ''
$intCode = 0
if ($script:Recompiled -and -not $sanityRed) {
    $intStatePath = Join-Path $logDir 'integration_batches.json'
    $manifestPath = Join-Path $repo 'Tests\integration_batch_durations.json'
    $intTargetSec = 60
    $intMaxTimeoutMs = 300000
    $allUnitSegs = @(Get-IntegrationSegments -Repo $repo)
    $man = Read-BatchManifest -Path $manifestPath
    $quarantine = Get-AdaptationValue -Repo $repo -Key 'test_quarantine_filter' -Default ''
    if ($quarantine) { Emit "QUARANTINE filter=$quarantine" }
    $plan = New-IntegrationPlan -Segments $allUnitSegs -Manifest $man -TargetBatchSec $intTargetSec -MaxBatchTests 380 -Quarantine $quarantine
    if ($RetryOnly) { foreach ($line in @(Restore-PriorGreens -Plan $plan -StatePath $intStatePath -TreeDigest $script:RunTree.digest)) { Emit $line } }
    # Logic has no measured duration home; its cap orders it first, as the longest unit.
    $logicUnit = @{ label = 'Logic'; filter = 'FullyQualifiedName~Tests.Logic'; timeoutMs = $suiteTimeoutMs; expectSec = $suiteTimeoutMs / 1000 }
    $poolT0 = Get-Date
    $poolPeers0 = @(Get-SuitePeers)
    $intSw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-GatePool -Units (@($logicUnit) + @(Get-IntegrationUnits -Plan $plan -CostCeilingMs $intMaxTimeoutMs -BatchWall $man.batchWall)) -Phase 'main'
    $first = @($script:PoolResults)
    Assert-TreeStable -Phase 'pool:main'
    $intFinal = @{}
    foreach ($x in @($first | Where-Object { $_.label -ne 'Logic' })) { $intFinal[$x.label] = $x }
    Update-IntegrationPlan -Plan $plan -Results @($intFinal.Values)
    Save-BatchState -StatePath $intStatePath -Plan $plan -TargetBatchSec $intTargetSec -TreeDigest $script:RunTree.digest -RunId $runId
    $r = ConvertTo-SuiteResult (@($first | Where-Object { $_.label -eq 'Logic' })[0])
    $t = Test-SuiteTiers -R $r -Label 'Logic'

    $retryUnits = @(Select-RetryUnits -Plan $plan -CostCeilingMs $intMaxTimeoutMs -BatchWall $man.batchWall)
    foreach ($u in $retryUnits) { Emit "RETRY label=$($u.of) prior=$(@($plan | Where-Object { $_.label -eq $u.of })[0].status)" }
    $logicRetry = ($t.tier -in @('PARTIAL', 'INVALID') -and $r.status -ne 'HANG')
    if ($logicRetry) {
        Emit "SUITE Logic rerun=1 reason=$($t.tier)"
        $retryUnits = @(@{ label = 'Logic_retry'; filter = $logicUnit.filter; timeoutMs = $suiteTimeoutMs; expectSec = $logicUnit.expectSec }) + $retryUnits
    }
    if ($retryUnits.Count -gt 0) {
        Invoke-GatePool -Units $retryUnits -Phase 'retry'
        $again = @($script:PoolResults)
        Assert-TreeStable -Phase 'pool:retry'
        if ($logicRetry) {
            $r = ConvertTo-SuiteResult (@($again | Where-Object { $_.label -eq 'Logic_retry' })[0])
            $t = Test-SuiteTiers -R $r -Label 'Logic'
        }
        foreach ($x in @($again | Where-Object { $_.label -ne 'Logic_retry' })) { $intFinal[($x.label -replace '_retry$', '')] = $x }
        Update-IntegrationPlan -Plan $plan -Results @($again | Where-Object { $_.label -ne 'Logic_retry' })
        Save-BatchState -StatePath $intStatePath -Plan $plan -TargetBatchSec $intTargetSec -TreeDigest $script:RunTree.digest -RunId $runId
        $first += $again
    }
    Assert-NotContention -R $r -StartedAt $poolT0 -Label 'Logic' -PeersAtStart $poolPeers0
    $suiteResults['Logic'] = @{ r = $r; t = $t }
    Write-ReaperDetail $r
    Emit ("SUITE Logic passed={0} failed={1} tier={2} delta={3:+#;-#;+0} status={4} dur={5}s" -f `
          $r.passed, $r.failed, $t.tier, ($r.passed - [int]$baseline.suites.Logic.passed), $r.status, $r.elapsed)

    # --- Integration: state, counters, diagnosis and the verdict lines come from IntegrationBatches.ps1, in the
    # batcher's stdout format, so the tiering below reads them unchanged.
    $intLockMs = [int64](@($first | Where-Object { $_.label -notlike 'Logic*' } | ForEach-Object { [int64] $_.lockWaitMs }) | Measure-Object -Sum).Sum
    $counters = Get-UpdatedCounters -Plan $plan -Counters $man.counters
    $diag = @(Get-BatchDiagnosisLines -Plan $plan -Counters $counters -Manifest $man)
    $bl = Read-IntegrationBaseline -Path $baselineP
    $verdict = Get-IntegrationVerdict -Plan $plan -Baseline $bl.baseline -Sentinel $bl.sentinel -ElapsedSec $intSw.Elapsed.TotalSeconds `
        -LockWaitMs $intLockMs -RunsRoot $script:RunDir
    $fresh = if ($verdict.Exit -eq 0) { Get-FreshDurations -Plan $plan } else { $null }
    $manifestLine = Save-BatchManifest -Path $manifestPath -Plan $plan -Counters $counters -Fresh $fresh -AllSegments $allUnitSegs
    $bi = 0
    foreach ($p in $plan) {
        $bi++
        Emit "BATCH $bi/$($plan.Count) label=$($p.label) status=$($p.status) passed=$($p.passed) failed=$($p.failed) elapsed=$($p.elapsed)s expected=$($p.expectedSec)s"
    }
    $failLines = @(foreach ($x in $intFinal.Values) { @($x.failedTests | ForEach-Object { "FAILED_TEST=$_" }) + @($x.failedMessages | ForEach-Object { "FAILED_MSG=$_" }) })
    $shown = @($diag) + @($verdict.Lines) + @(if ($manifestLine) { $manifestLine }) + @(if ($verdict.Exit -eq 0) { 'STATUS=DONE' })
    foreach ($line in $shown) { Emit $line }
    $intTxt = (@($failLines) + $shown) -join "`n"
    $intCode = $verdict.Exit
    $intPassed = -1; $intFailed = -1; $intComplete = 'UNKNOWN'; $intStatus = 'UNKNOWN'
    if ($intTxt -match 'TOTAL passed=(\d+) failed=(\d+)') { $intPassed = [int]$Matches[1]; $intFailed = [int]$Matches[2] }
    if ($intTxt -match 'COMPLETENESS=(\w+)')             { $intComplete = $Matches[1] }
    $sm = [regex]::Matches($intTxt, '(?m)^STATUS=(\w+)')
    if ($sm.Count -gt 0) { $intStatus = $sm[$sm.Count - 1].Groups[1].Value }
    $intFailedTests = @([regex]::Matches($intTxt, '(?m)^FAILED_TEST=(.+)$') | ForEach-Object { $_.Groups[1].Value.Trim() })
    $intR = @{ label = 'Integration'; passed = $intPassed; failed = $intFailed; status = $intStatus; exit = $intCode
               silentSkip = ($intComplete -eq 'SILENT_SKIP'); failedTests = $intFailedTests; failedMessages = (Get-FailedMessages -Text $intTxt); elapsed = ''; raw = $intTxt }
    Assert-NotContention -R $intR -StartedAt $poolT0 -Label 'Integration' -PeersAtStart $poolPeers0
    $intT = Test-SuiteTiers -R $intR -Label 'Integration'
    if ($intComplete -eq 'SHORTFALL' -and $intT.tier -eq 'PASS') { $intT = @{ tier = 'PARTIAL'; note = 'runner reported COMPLETENESS=SHORTFALL' } }
    # A partial total is not a count regression — tiering it against the full baseline yields HARDFAIL,
    # which reads as "major drop" for a run that simply did not finish. Real failures still outrank it.
    # UNKNOWN counts as not-comparable, not as complete: the verdict emits COMPLETENESS on every exit
    # path, so its ABSENCE means the run did not report — weaker evidence than INCOMPLETE, never stronger.
    if ($intComplete -in @('INCOMPLETE', 'UNKNOWN') -and $intFailed -le 0 -and $intT.tier -ne 'LOCKED') {
        $why = if ($intComplete -eq 'UNKNOWN') { 'the Integration verdict carried no COMPLETENESS line, so the total cannot be assumed whole' }
               else { 'a unit was left LOCKED or returned no readable result (no wrapper output, no or malformed TRX) after the retry pass' }
        $intT = @{ tier = 'INCOMPLETE'; note = "$why, so passed=$intPassed is partial by construction and is NOT comparable to the baseline" }
    }
    $suiteResults['Integration'] = @{ r = $intR; t = $intT }
    Emit ("SUITE Integration passed={0} failed={1} tier={2} delta={3:+#;-#;+0} completeness={4} exit={5}" -f `
          $intPassed, $intFailed, $intT.tier, ($intPassed - [int]$baseline.suites.Integration.passed), $intComplete, $intCode)
}

# ---------------------------------------------------------------- adjudicate
$tiers = @($suiteResults.Keys | ForEach-Object { $suiteResults[$_].t.tier })
foreach ($k in $suiteResults.Keys) {
    $sr = $suiteResults[$k]
    if ($sr.t.note) { Add-Detail "## $k — $($sr.t.tier)`n`n$($sr.t.note)" }
    if ($sr.r.failedTests.Count -gt 0) {
        Add-Detail "### $k failing tests"
        $sr.r.failedTests | ForEach-Object { Add-Detail ("- $_" + $(if ($sr.r.failedMessages[$_]) { " :: $($sr.r.failedMessages[$_])" })) }
    }
}

if ($suiteResults.Keys | Where-Object { $suiteResults[$_].r.status -eq 'HANG' }) {
    Add-Detail 'A suite wedged and was tree-killed after the wrapper''s built-in retry. Re-run once. If a second run also HANGs or counts DROP across retries, machine named-pipe state is exhausted — reboot is the terminal fix.'
    Complete-Gate 'HANG' 124
}
# LOCKED: the deterministic machine-busy tier — converted by Complete-LockedGate (defined above
# the phase flow). A LOCKED Sanity completes the gate before the pool; a LOCKED Logic or
# Integration unit reaches it here.
if ($tiers -contains 'LOCKED') { Complete-LockedGate }
if ($tiers -contains 'INVALID' -or $tiers -contains 'UNPARSED') {
    Add-Detail 'Results are untrustworthy — not a regression signal. Kill orphaned Godot processes and re-run; do not interpret counts.'
    Complete-Gate 'INVALID' 2
}
$anyFailed = @($suiteResults.Keys | Where-Object { $suiteResults[$_].r.failed -gt 0 })
if ($anyFailed.Count -gt 0 -or ($tiers -contains 'HARDFAIL')) {
    Add-Detail 'Next: .claude/commands/regression_gate.md §Failure handling (exit 1). Repair each in-scope failure, then rerun the failing -Filter through run_test_suite.ps1; retain the original failed counts and account for every failed test. Follow .claude/skills/change_control/SKILL.md §Post-failure re-verification and gate cadence; record shared-state coverage limits for close verification.'
    Complete-Gate 'FAIL' 1
}
if ($tiers -contains 'INCOMPLETE') {
    $lockWaitTotal = 0
    if ($intTxt -match 'LOCKWAIT_TOTAL_MS=(\d+)') { $lockWaitTotal = [int]$Matches[1] }
    $statusLocked = ($intTxt -match 'STATUS=LOCKED')
    if ($lockWaitTotal -gt 0 -or $statusLocked -or $script:PeersSeen) {
        # Machine-busy starvation: a runtime slot was waited on, a unit LOCKED out, or a peer overlapped the run. The
        # gate ends BUSY and names who held the machine (the live slot holders and the peers seen at suite boundaries)
        # instead of returning a partial verdict. The green batches are recorded in the state file, so the re-run is
        # -RetryOnly.
        Add-Detail 'A suite did not FINISH — a unit was left LOCKED or returned no readable result, and the machine-busy signature holds (lock wait, a LOCKED unit, or a peer overlapped the run). Nothing failed; the counts are partial by construction. The gate ends BUSY; re-invoke with -RetryOnly once the holders end.'
        $starvedBy = @(Get-SlotHolders)
        foreach ($pr in @($script:PeersSeenList)) {
            $pp = [int](Get-Prop $pr 'pid' 0)
            if ($pp -le 0 -or @($starvedBy | Where-Object { $_.Pid -eq $pp }).Count -gt 0) { continue }
            $starvedBy += [pscustomobject]@{ Kind = [string](Get-Prop $pr 'kind' 'peer'); Pid = $pp; Session = [string](Get-Prop $pr 'sessionId' '-'); Since = (Get-Prop $pr 'startedAt' $null)
                                              HeldSec = 0; Label = [string](Get-Prop $pr 'label' '-'); Source = 'peer' }
        }
        Complete-Busy -Site 'starved' -Holders $starvedBy -Next (Get-SlotNext $starvedBy)
    }
    Add-Detail 'A suite did not FINISH — a unit returned no readable result (no wrapper output, no or malformed TRX) after the retry pass, with no machine-busy signature. Each unit''s wrapper.out under TRX_DIR shows what it printed. Nothing failed; the counts are partial by construction and prove nothing either way. Re-run the gate; -RetryOnly keeps prior green batches. Do NOT interpret the counts and do NOT treat this as a pass.'
    Complete-Gate 'INCOMPLETE' 6
}
$testWip = Get-TestWorkInProgress -Repo $repo
$lowerings = [ordered]@{}
# PARTIAL is the same clean drop, only smaller than the WARN band: committed deletions explain it the same way.
if ($tiers -contains 'WARN' -or $tiers -contains 'PARTIAL') {
    if ($testWip.paths.Count -gt 0) {
        $script:BaselineAction = 'skipped:uncommitted-test-changes'
        Emit 'BASELINE action=skipped reason=uncommitted-test-changes'
    } elseif ($testWip.readable) {
        $deletionBase = if ($rawStamp) { & git -C $repo rev-parse --verify --quiet "$rawStamp^{commit}" 2>$null } else { '' }
        $deletionBaseReadable = ($rawStamp -and $LASTEXITCODE -eq 0 -and "$deletionBase" -match '^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
        foreach ($k in $suiteResults.Keys) {
            $sr = $suiteResults[$k]
            if ($sr.t.tier -notin @('WARN', 'PARTIAL') -or $sr.r.failed -ne 0) { continue }
            if ($k -eq 'Integration' -and $intComplete -ne 'OK') { continue }
            $drop = [int]$baseline.suites.$k.passed - $sr.r.passed
            if (-not $deletionBaseReadable) {
                Add-Detail "Count drop remains unexplained for ${k}: unreadable-baseline-commit ($rawStamp)."
                continue
            }
            $patch = @(& git -C $repo diff --no-ext-diff --no-textconv --no-renames --unified=2147483647 "${deletionBase}..HEAD" -- ":(glob)Tests/$k/**/*.cs" 2>$null)
            if ($LASTEXITCODE -ne 0) {
                Add-Detail "Count drop remains unexplained for ${k}: unreadable-test-diff."
                continue
            }
            $delta = Get-TestAttributeDelta -Patch $patch
            Add-Detail "Count-drop evidence for ${k}: drop=$drop deleted=$($delta.removed) added=$($delta.added)$(if (-not $delta.readable) { " reason=$($delta.reason)" })."
            if ($delta.readable -and $drop -le ($delta.removed - $delta.added)) {
                $lowerings[$k] = @{ drop = $drop; removed = $delta.removed; added = $delta.added }
                $sr.t = @{ tier = 'PASS'; note = 'moderate drop explained by committed test deletions' }
            }
        }
        $tiers = @($suiteResults.Keys | ForEach-Object { $suiteResults[$_].t.tier })
    }
}
if ($tiers -contains 'WARN' -or $tiers -contains 'PARTIAL') {
    Add-Detail 'The count drop remains unexplained or its deletion evidence is unavailable. The baseline was NOT written; commands/regression_gate.md (WARN row) says when the session asks the user.'
    Complete-Gate 'WARN' 3
}
# Unreachable while the tiers hold (a Sanity without execution evidence tiers INVALID, UNPARSED, INCOMPLETE, LOCKED or
# HANG), and fatal if they ever stop holding: a gate whose pool never ran must not reach the ratchet.
if (-not $script:Recompiled) { Complete-Gate 'INVALID' 2 }

# ---------------------------------------------------------------- ratchet
$deltas = @()
foreach ($k in @('Logic', 'Integration', 'Sanity')) {
    if (-not $suiteResults.Contains($k)) { continue }   # deferred in smoke tier
    $d = $suiteResults[$k].r.passed - [int]$baseline.suites.$k.passed
    if ($d -gt 0) { $deltas += "$k+$d" }
}
# Reaching here means every tier cleared and no suite reported a failure, so an
# UNTRUSTED stamp is re-established rather than skipped: the counts about to be
# written were just observed green, which is the only thing the stamp asserts.
# Skipping instead deadlocks — this block is the sole writer of
# updated_on_commit, so gating it on the stamp already being valid means no run
# can ever repair one.
# Polluted-tree guard: the ratchet writes whatever the shared tree measured, so a peer's
# UNCOMMITTED test changes would be baked into a floor the committing session does not own.
# The gate's own writes are excluded (baseline + durations manifest are this run's own).
# Skipping leaves the floor where it is (safe — the green verdict stands); the next
# clean-tree green run ratchets. The UNTRUSTED-stamp reestablish is equally gated — the
# observed counts cannot be attributed on a polluted tree, and a clean-tree run repairs it.
$testWip = Get-TestWorkInProgress -Repo $repo
if ($lowerings.Count -gt 0 -and (-not $testWip.readable -or $testWip.paths.Count -gt 0)) {
    $reason = if ($testWip.readable) { 'uncommitted-test-changes' } else { 'unreadable-test-status' }
    $script:BaselineAction = "skipped:$reason"
    Emit "BASELINE action=skipped reason=$reason"
    Add-Detail 'Deletion evidence no longer belongs to a clean test tree. The baseline was NOT written; commands/regression_gate.md (WARN row) applies.'
    Complete-Gate 'WARN' 3
}
$reestablish = ($stampTrust -eq 'UNTRUSTED')
if ($NoBaselineUpdate) {
    $script:BaselineAction = 'skipped:flag'
    Emit 'BASELINE action=skipped reason=flag'
} elseif (-not $testWip.readable) {
    $script:BaselineAction = 'skipped:unreadable-test-status'
    Emit 'BASELINE action=skipped reason=unreadable-test-status'
} elseif ($testWip.paths.Count -gt 0) {
    $script:BaselineAction = 'skipped:uncommitted-test-changes'
    Add-Detail "The tree carries uncommitted test changes ($($testWip.paths.Count) file(s)) which may be a peer's — the baseline stays at its floor. The next clean-tree green run ratchets/re-establishes."
    Emit 'BASELINE action=skipped reason=uncommitted-test-changes'
} elseif ($deltas.Count -eq 0 -and $lowerings.Count -eq 0 -and -not $reestablish) {
    $script:BaselineAction = 'unchanged'
    Emit 'BASELINE action=unchanged'
} else {
    # Targeted surgery, not a JSON round-trip: the tracked file carries
    # pre-existing mojibake in its description/notes strings, and
    # ConvertTo-Json would re-encode those into a spurious diff.
    $new  = $baselineRaw
    # Full sha, never --short: the abbreviation length is not stable across the
    # repo's lifetime, and this value is compared against a commit id later.
    $head = (& git -C $repo rev-parse HEAD 2>$null)
    $br   = (& git -C $repo branch --show-current 2>$null)
    foreach ($k in @('Logic', 'Integration', 'Sanity')) {
        $p = $suiteResults[$k].r.passed
        $new = [regex]::Replace($new,
            "(?s)(`"$k`"\s*:\s*\{[^}]*?`"passed`"\s*:\s*)\d+",
            "`${1}$p")
    }
    $new = [regex]::Replace($new, '("updated_at"\s*:\s*")[^"]*"',        "`${1}$(Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')`"")
    $new = [regex]::Replace($new, '("updated_on_commit"\s*:\s*")[^"]*"', "`${1}$head`"")
    $new = [regex]::Replace($new, '("updated_on_branch"\s*:\s*")[^"]*"', "`${1}$br`"")
    # Fail loud rather than write a file we can no longer parse.
    try { $null = $new | ConvertFrom-Json } catch {
        Emit 'BASELINE action=ABORTED reason=post-edit-json-invalid'
        Add-Detail "Refused to write Tests/regression_baseline.json — the edited text no longer parses as JSON. The gate result itself stands; update the baseline by hand. Error: $_"
        Complete-Gate 'PASS' 0
    }
    [System.IO.File]::WriteAllText($baselineP, $new, (New-Object System.Text.UTF8Encoding($false)))
    $act = if ($lowerings.Count -gt 0) { 'lowered' } elseif ($reestablish) { 're-established' } else { 'updated' }
    $script:BaselineAction = "$act $($deltas -join ' ')"
    if ($lowerings.Count -gt 0) {
        foreach ($k in $lowerings.Keys) {
            $d = $lowerings[$k]
            Emit "BASELINE action=lowered suite=$k drop=$($d.drop) deleted=$($d.removed) added=$($d.added)"
        }
    } else {
        Emit "BASELINE action=$act $($deltas -join ' ') stamp=$($head.Substring(0,10))"
    }
}

Add-Detail 'All tiers clear. Stage Tests/regression_baseline.json (and integration_batch_durations.json / tool_resource_classes.txt if they changed) alongside whatever caused the growth.'
Complete-Gate 'PASS' 0

} catch {
    # A throw anywhere in the run still ends in a verdict and a result record: a detached caller polls for VERDICT=.
    Emit "GATE_ERROR $($_.Exception.Message)"
    Add-Detail "## Harness fault — the gate threw`n`n``````n$($_.Exception.Message)`n$($_.ScriptStackTrace)`n``````n`nNot a regression signal: the gate itself failed before a verdict. Fix the cause, then re-run."
    Complete-Gate 'INCOMPLETE' 6
} finally {
    # Complete-Gate's `exit` unwinds through here, so every verdict path releases the held checkout and removes this
    # run's activity record.
    Exit-GateHeld
    Stop-ActivityRecord
}
