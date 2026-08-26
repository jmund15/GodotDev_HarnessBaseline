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
  - The headless [Tool] cascade import gate runs AFTER the suites, never
    before (a pre-suite Godot process poisons the runtime-executor handoff).
  - Tier 1 silent-skip sentinel, Tier 2 drift ratios, Tier 3 Failed:0.
  - Partial-but-clean counts (sentinel < passed < baseline) re-run once.
  - Baseline ratchets FORWARD ONLY, and only on a fully green run.
  - Baseline stamp trust: updated_on_commit must equal the commit that last
    touched the file, or its parent (the benign one-commit-lag shape). Compared
    as full shas; a fully green run re-establishes an untrusted stamp.
  - Never auto-lower, never write while FAIL/WARN.

.EXIT CODES
  0   PASS          all tiers clear, baseline ratcheted if it grew
  1   FAIL          a suite reported failures -> model runs step-5 adjudication
  2   INVALID       silent-skip signature / below sentinel -> results untrustworthy
  3   WARN          Tier-2 moderate drop or untrusted baseline -> needs user ack
  4   BLOCKED       preflight, guard, or build failure -> fix before re-running
  6   INCOMPLETE    a suite did not finish (wall-clock budget, no machine-busy signature),
                    OR a -FromQueue run hit the LOCKED tier (a watcher-fired run already waited
                    for machine-wide quiet; the watcher re-queues this, capped)
  7   QUEUED        an editor holds this checkout, a peer gate (any checkout, any session) or
                    test run didn't clear, a suite's lock-wait expired (LOCKED tier,
                    runtime-mutex-busy), or a budget-starved run deferred — handed to
                    gate_queue_watcher.ps1, which fires it when the machine is quiet
  8   CONTENTION    external-kill signature AND a live peer harness run -> not a
                    regression signal; re-run once the peer clears
  124 HANG          a suite wedged and was tree-killed after retry

.NOTES
  Detail (failing test names, guard stdout, engine paths) is written to
  .claude/scratch/test_runs/gate_last_<runId>.md so stdout stays ~20 lines;
  gate_last.md is a latest-run pointer copy. Read the detail file only when the
  verdict is not PASS. Every completed run also writes <queueDir>/<runId>.result.json
  (the ledger cross-session REUSE consumes) — best-effort, never fatal.
#>

[CmdletBinding()]
param(
    # Run preflight + guards + build only, then stop. ~20s, takes no test lock.
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
    # Forwarded to run_integration_batched.ps1: rerun only non-green batches.
    [switch]   $RetryOnly,
    # Run even though a Godot editor holds this checkout. The escape hatch for
    # the queue; also what gate_queue_watcher.ps1 passes when IT fires a run.
    [switch]   $IgnoreEditor,
    # Set only by gate_queue_watcher.ps1. Suppresses the inline phase-boundary
    # editor checks: the watcher owns mid-run protection for a queued run and
    # tree-kills this process directly, which a process cannot do to itself.
    [switch]   $FromQueue,
    # Read-only report of the queue directory. Takes no locks, spawns nothing.
    [switch]   $QueueStatus,
    # Seconds to block on a queued run's RESULT before returning exit 7. Pass 0 for the
    # fire-and-forget handoff.
    #
    # DEFAULTS TO WAITING because the caller is an agent that must decide something. A
    # bare exit 7 hands back no verdict, so the only autonomous move left is to give up
    # or to invent a polling loop; waiting turns the queue into a resume point instead.
    # It costs a 5s-poll process and nothing else — the documented invocation is
    # `run_in_background: true` (commands/regression_gate.md), where blocking is free and
    # process exit is exactly what wakes the session with the verdict.
    #
    # Expiry WAKES, it never kills: the request stays pending, the watcher still owns it,
    # and the caller re-checks (feedback_timeouts_wake_dont_kill.md). 30min covers a full
    # suite run plus a realistic wait for the machine to go quiet.
    #
    # The wait polls the result FILE, never process absence: the batched runner tears down
    # testhost/Godot between every batch, so a process-absence waiter fires in an
    # inter-batch gap and calls a live peer run finished
    # (gotcha_runtime_suite_pipe_contention.md). Deliberately NOT forwarded through
    # Get-GateArgs — a watcher-fired run must never wait on itself.
    [int]      $WaitForQueue = 1800,
    # Set only by gate_queue_watcher.ps1: this run's queue request id. The runId and the
    # result record use it (one record per request, never a stray); treeDelta compares the
    # request-time treeDigest against the tree this run actually saw.
    [string]   $QueueId = '',
    # Force a fresh run: refuse a ledger REUSE even when a digest+mode+age match exists.
    [switch]   $NoReuse,
    # Spawn the real gate as a DETACHED process, print where its stdout lands, and exit in ~1s.
    #
    # This is the agent-session entry point, and it exists because `run_in_background: true` is
    # not durable: Claude Code terminates a backgrounded Bash task once the session goes idle,
    # and the task's whole process tree dies with it. Measured 2026-08-25 — five gates killed
    # mid-Logic at a byte-identical 327 bytes of stdout, plus a bare `sleep` loop killed at 0
    # bytes, which is what rules out anything gate-specific. A detached child is not a
    # descendant of that shell, so the reap cannot reach it (the same reason
    # Start-GateWatcherIfAbsent detaches).
    #
    # Stdout goes to a FILE, never a pipe back to the caller: an inherited pipe is what makes a
    # child die with its parent. The caller polls that file for a line starting VERDICT=.
    [switch]   $Detach
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo      = (Get-Item $PSScriptRoot).Parent.Parent.FullName
$scripts   = Join-Path $repo '.claude\scripts'
$hooks     = Join-Path $repo '.claude\hooks'
$logDir    = Join-Path $repo '.claude\scratch\test_runs'
    # NOTE: must not be named $detail — PowerShell variable names are
    # case-insensitive, so $detail and $script:Detail are the same variable.
$runId     = $(if ($QueueId) { $QueueId } else { '{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID })
$detailPath = Join-Path $logDir "gate_last_$runId.md"
$baselineP = Join-Path $repo 'Tests\regression_baseline.json'
$queueDir  = Join-Path $repo '.claude\scratch\gate_queue'
# Machine-global on purpose: worktrees are separate directories, so a
# per-checkout path cannot see peer sessions.
$activityDir = Join-Path $env:TEMP 'pp-activity'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

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
$script:ActivityKind = 'gate'  # 'waiter' while parked in Wait-QueueResult (holds nothing)
$script:QueueWaitResult = $null # Wait-QueueResult's out-param; see the note there
$suiteResults        = [ordered]@{}

# ---------------------------------------------------------------- coverage contract (mode)
# The normalized arg set, NOT a bare enum: -RetryOnly reruns only non-green batches, so it
# would otherwise carry mode=full and let a reuse satisfy a full request with reduced coverage.
# Dispatch artifacts (-FromQueue, -QueueId, -IgnoreEditor) are excluded: editor state is
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
    $header = @(
        "# Regression Gate detail — $(Get-Date -Format 'u')",
        "",
        "VERDICT=$Verdict (exit $Code)",
        ""
    )
    Set-Content -Path $detailPath -Value (@($header) + @($script:Detail)) -Encoding utf8
    # Latest-run pointer: readers that know the stable path still work; the per-run file is
    # what the ledger record and REUSE point at. Single choke point — no concurrent-gate race.
    try { Copy-Item -Path $detailPath -Destination (Join-Path $logDir 'gate_last.md') -Force -ErrorAction Stop } catch { }
    # Ledger record — the ONLY writer of completed-run results (the watcher keeps only its
    # watcher-error path). Best-effort: a record-write hiccup must never fail a gate.
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
            # record — for a QUEUED run that means the watcher never sees a result and re-fires
            # to its GIVE-UP ceiling. Guard: empty head/digest on early exits.
            head           = $(if ($script:RunTree) { $script:RunTree.head } else { '' })
            treeDigest     = $(if ($script:RunTree) { $script:RunTree.digest } else { '' })
            treeDelta      = $false
            startedAt      = $script:RunStartedUtc
            finishedAt     = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
            mode           = $script:Mode
            pid            = $PID
            sessionId      = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $null })
            source         = $(if ($QueueId) { 'queued' } else { 'inline' })
            baselineAction = $script:BaselineAction
            detail         = $detailPath
            reusedFrom     = $script:ReusedFrom
            failedTests    = $(if ($ft.Count -gt 0) { @($ft) } else { $null })
        }
        if ($QueueId) {
            # treeDelta: request-time digest vs the tree this run actually saw. A queued run
            # waited while the user kept editing; a delta means the result backs no "Verified".
            $reqP = Join-Path $queueDir "$QueueId.request.json"
            if (Test-Path $reqP) {
                try {
                    $req = Get-Content $reqP -Raw | ConvertFrom-Json
                    $rec.treeDelta = -not (Test-DigestMatch ([string](Get-Prop $req 'treeDigest' '')) $script:RunTree.digest)
                } catch { }
            }
        }
        # Fold: satisfy every OTHER pending request whose tree is the one this run tested. The
        # digest is content-exact, so an equal digest means byte-identical inputs — the verdict
        # transfers with no soundness loss, and a session that queued three minutes after us
        # unblocks now instead of serializing a duplicate 15-minute run behind us. Requests
        # whose digest differs are deliberately left pending: a PASS on tree A backs nothing on
        # tree B, and the watcher re-fires them against the live tree.
        $folded = @()
        if ($script:RunTree) {
            foreach ($exId in @(Get-FoldableRequestIds -QueueDir $queueDir -RunId $runId -RunDigest $script:RunTree.digest)) {
                try {
                    # Field-by-field copy, NOT $rec.Clone(): [ordered]@{} is an
                    # OrderedDictionary, which has no Clone method (Hashtable does). The call
                    # threw on every fold, the catch below swallowed it, and the run's own
                    # result still landed — so the whole optimization was dead while every
                    # unit test passed. Caught only by the e2e (test_gate_queue_e2e.ps1).
                    $copy = [ordered]@{}
                    foreach ($kv in $rec.GetEnumerator()) { $copy[$kv.Key] = $kv.Value }
                    $copy.id        = $exId
                    $copy.source    = 'folded'
                    $copy.treeDelta = $false   # digest-equal by construction — that is the fold test
                    Set-Content -Path (Join-Path $queueDir "$exId.result.json") `
                                -Value ($copy | ConvertTo-Json -Depth 5) -Encoding utf8
                    $folded += $exId
                } catch {
                    # Never silent: a swallowed fold is indistinguishable from "nothing to fold",
                    # which is precisely how the Clone bug survived. The run still completes.
                    Emit "QUEUE_FOLD_ERROR id=$exId $($_.Exception.Message)"
                }
            }
        }
        # Recorded so the watcher's re-queue path can un-satisfy them together: a folded result
        # carrying an exit 6/7/8 contention artifact must not outlive the run that produced it.
        $rec.foldedIds = $(if ($folded.Count -gt 0) { @($folded) } else { $null })
        if ($folded.Count -gt 0) { Emit "QUEUE_FOLD ids=$($folded -join ',') into=$runId" }
        Set-Content -Path (Join-Path $queueDir "$runId.result.json") -Value ($rec | ConvertTo-Json -Depth 5) -Encoding utf8
    } catch { }
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
# Digests cross process boundaries at different widths (the TREE stdout line the
# watcher parses is abbreviated), so equality is a prefix test, never -eq.
# Test-DigestMatch, Get-FoldTargetId and Get-FoldableRequestIds come from the queue-protocol
# module: the fold decision is the one place here whose wrong answer is a false green, so it
# has one implementation and a test (.claude/tests/test_gate_queue_fold.ps1) rather than a
# copy in each script that reads the queue.
. (Join-Path $PSScriptRoot 'GateQueue.ps1')

# ---------------------------------------------------------------- tree digest
# Identity of the tree a run was requested for / actually ran against. The gate's
# verdict is only readable against the tree it ran on, and a queued run waits
# while the user keeps editing, so both ends must be recorded.
#
# The gate's OWN artifacts are excluded: the ratchet rewrites the baseline during
# a green run, so an unexcluded digest would stamp every ratcheting queued run
# dirty against itself. All of .claude/ is the same class: harness doctrine, tooling, and
# agent artifacts are not under test, and a concurrent session (or the gate itself — the
# guard regenerates hooks/tool_resource_classes.txt) writing them must not invalidate a
# running gate (measured 2026-08-13 auto-memory, 2026-08-14 rules/ + hooks/:
# TREE_CHANGED=1 mid-run).
# Digest exclusion list — script scope so Assert-TreeStable can apply the same class test
# to a moved HEAD's committed diff (a peer session committing only excluded paths must not
# invalidate a running gate; measured 2026-08-20, two INVALIDs from mid-run .claude/ commits).
$script:DigestExcl = @('Tests/regression_baseline.json',
          'Tests/integration_batch_durations.json',
          'TestResults/',
          # Engine-regenerated import artifact: the Godot test host rewrites this CSV (+ its
          # .translation siblings) whenever authored .tres data changed, so a run adding
          # sheets self-invalidates mid-Logic (measured 2026-08-20, VERDICT=INVALID at
          # suite:Integration). Same class as TestResults/ — the run's own artifacts.
          # Blanket, not per-tree: narrow carve-outs repeatedly missed a .claude tree and
          # tripped TREE_CHANGED (auto-memory 2026-08-13, rules/ + hooks/ 2026-08-14).
          '.claude/')

function Get-TreeDigest {
    # Content-exact: identical digest <=> identical committed state + working-tree content +
    # Jmodot internal state. The old HEAD+porcelain hash read status letters and paths only,
    # so two different edits to one already-modified file digested identically, and the Jmodot
    # submodule surfaced as `+Subproject <sha>-dirty` — internally-dirty trees digesting as one
    # state. Live paths now contribute `git hash-object` of the WORKING file (what the build
    # sees — covers staged+unstaged identically; binaries hash raw bytes); deleted paths
    # contribute their status line; a hash-object failure (path vanished mid-run) falls back to
    # the status line. A gitlink (submodule) entry contributes its recorded-vs-checkout
    # pointers and recurses into its working tree.
    $head = (& git -C $repo rev-parse HEAD 2>$null)
    if (-not $head) { $head = '' }
    $excl = $script:DigestExcl
    $entries = [System.Collections.Generic.List[string]]::new()

    # One repo's changed paths, exclusion-filtered at the porcelain-line level. hash-object
    # cannot take pathspecs, so enumeration is where exclusion must happen; -uall lists
    # untracked files individually (their content is hashed too).
    function Get-ChangedPaths {
        param([string] $Dir, [string[]] $Excl)
        $out = [System.Collections.Generic.List[object]]::new()
        foreach ($line in @(& git -C $Dir status --porcelain -uall 2>$null)) {
            if (-not $line -or $line.Length -lt 4) { continue }
            $p = $line.Substring(3).Trim()
            if ($p -match '\s->\s') { $p = ($p -split '\s->\s')[-1] }
            $p = $p.Trim('"') -replace '\\', '/'
            $skip = $false
            foreach ($e in $Excl) { if ($p.StartsWith($e)) { $skip = $true; break } }
            if (-not $skip) { $out.Add([pscustomobject]@{ st = $line.Substring(0, 2); p = $p }) }
        }
        $out
    }

    # Recursive content collector. $Prefix keeps Jmodot's entries distinguishable from the
    # outer repo's (jm/...); a gitlink entry contributes recorded-vs-checkout and recurses.
    function Add-ContentEntries {
        param([string] $Dir, [string[]] $Excl, [string] $Prefix)
        foreach ($e in @(Get-ChangedPaths -Dir $Dir -Excl $Excl | Sort-Object p)) {
            $full = Join-Path $Dir $e.p
            $isLink = ((& git -C $Dir ls-files -s -- $e.p 2>$null) -match '^160000')
            if ($isLink -and (Test-Path $full)) {
                $recorded = (& git -C $Dir rev-parse "HEAD:$e.p" 2>$null)
                $checked  = (& git -C $full rev-parse HEAD 2>$null)
                $entries.Add("$Prefix$($e.p)|gitlink|$recorded|$checked")
                Add-ContentEntries -Dir $full -Excl $Excl -Prefix "jm/"
            }
            elseif (Test-Path $full) {
                $h = (& git -C $Dir hash-object $full 2>$null)
                if ($h) { $entries.Add("$Prefix$($e.p)|$($e.st)|$h") }
                else    { $entries.Add("$Prefix$($e.p)|$($e.st)|UNHASHABLE") }
            }
            else {
                $entries.Add("$Prefix$($e.p)|$($e.st)|DELETED")
            }
        }
    }

    Add-ContentEntries -Dir $repo -Excl $excl -Prefix ''
    $text = "$head`n" + ((@($entries) | Sort-Object) -join "`n")
    $sha  = [System.Security.Cryptography.SHA1]::Create()
    try   { $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text)) }
    finally { $sha.Dispose() }
    [pscustomobject]@{ head = $head; digest = (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''); entries = @(@($entries) | Sort-Object) }
}

# ---------------------------------------------------------------- queue status
# Deliberately ABOVE the GodotProcess.ps1 dot-source: reporting the queue is a
# pure directory read that needs no process identity and takes no locks.
if ($QueueStatus) {
    $cur = Get-TreeDigest
    Write-Output "QUEUE dir=$queueDir"
    Write-Output ("CURRENT head={0} digest={1}" -f (Format-Short $cur.head), (Format-Short $cur.digest))
    $reqs = @()
    if (Test-Path $queueDir) {
        $reqs = @(Get-ChildItem -Path $queueDir -Filter '*.request.json' -ErrorAction SilentlyContinue | Sort-Object Name)
    }
    if ($reqs.Count -eq 0) {
        Write-Output 'QUEUE empty=1'
        exit 0
    }
    foreach ($f in $reqs) {
        $req = $null
        try { $req = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json } catch { }
        $id  = Get-Prop $req 'id' ([System.IO.Path]::GetFileName($f.FullName) -replace '\.request\.json$', '')
        $res = $null
        $resP = Join-Path $queueDir "$id.result.json"
        if (Test-Path $resP) { try { $res = Get-Content -Path $resP -Raw | ConvertFrom-Json } catch { } }
        $status = if ($res) { Get-Prop $res 'status' 'UNKNOWN' } else { 'pending' }
        Write-Output ("REQ id={0} status={1} reason={2} attempt={3} head={4} digest={5} treeDelta={6}" -f `
            $id, $status,
            (Get-Prop $req 'reason' '-'), (Get-Prop $req 'attempt' 0),
            (Format-Short ([string](Get-Prop $req 'head' ''))),
            (Format-Short ([string](Get-Prop $req 'treeDigest' ''))),
            $(if ($res) { Get-Prop $res 'treeDelta' $false } else { '-' }))
        if ($res) {
            $ranDigest = [string](Get-Prop $res 'treeDigest' '')
            Write-Output ("    ran head={0} digest={1} exit={2} detail={3}" -f `
                (Format-Short ([string](Get-Prop $res 'head' ''))), (Format-Short $ranDigest),
                (Get-Prop $res 'exitCode' '-'), (Get-Prop $res 'detail' '-'))
            if (-not (Test-DigestMatch $ranDigest $cur.digest)) {
                Write-Output '    STALE (tree changed since run — cannot back a "Verified" claim)'
            }
        }
    }
    exit 0
}

# ---------------------------------------------------------------- identity
# GodotProcess.ps1 is the ONLY home for "what is this Godot process, and whose
# checkout is it". Nothing below re-derives it.
$godotProcP = Join-Path $scripts 'GodotProcess.ps1'
if (-not (Test-Path $godotProcP)) {
    Write-Output 'PREFLIGHT=BLOCKED reason=GodotProcess.ps1-missing'
    exit 4
}
. $godotProcP

# ---------------------------------------------------------------- activity registry
# Advisory telemetry only — never a correctness mechanism. Every write is
# swallowed, because a gate must not fail on a temp-file hiccup.
# The Claude Code process that owns this script's subtree. This is the axis that tells "my
# session's run" apart from a concurrent session's when both share ONE checkout, where `checkout`
# cannot distinguish them and `sessionId` is null (CLAUDE_SESSION_ID is not exported into this
# shell). Captured HERE, at record-creation time, and never re-derived by a reader later: a
# detached run outlives its launcher — Claude Code kills a backgrounded wrapper at its ~10-minute
# ceiling — and the orphaned process's parent chain then reaches no session at all.
# Declared at script scope — under Set-StrictMode Latest an unset variable READ throws, and the
# first call hits `if ($script:OwnerRootComputed)` before any assignment. That throw is swallowed
# by Update-ActivityRecord's best-effort catch, so without this declaration the gate silently
# never writes its activity record — measured 2026-08-14: every live gate ran record-less,
# blinding gate-level peer detection (machineGates preflight, CONTENTION adjudication).
$script:OwnerRootComputed = $false
$script:OwnerRootCache    = $null
function Get-OwnerRoot {
    if ($script:OwnerRootComputed) { return $script:OwnerRootCache }
    $script:OwnerRootComputed = $true
    $script:OwnerRootCache = $null
    try {
        $seen = @{}
        $cur = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
        for ($i = 0; $i -lt 12 -and $cur; $i++) {
            $ppid = [int] $cur.ParentProcessId
            if ($ppid -le 0 -or $seen.ContainsKey($ppid)) { break }
            $seen[$ppid] = $true
            $par = Get-CimInstance Win32_Process -Filter "ProcessId=$ppid" -ErrorAction SilentlyContinue
            if (-not $par) { break }
            if ($par.Name -match '^(node|claude)(\.exe)?$') {
                $script:OwnerRootCache = [pscustomobject] @{
                    RootPid   = [int] $par.ProcessId
                    RootStart = ([datetime] $par.CreationDate).ToUniversalTime().ToString('o')
                }
                break
            }
            $cur = $par
        }
    } catch { }
    return $script:OwnerRootCache
}
function Update-ActivityRecord {
    param([string] $Label)
    if (-not $script:ActivityOn) { return }
    try {
        $now = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
        $rec = [ordered]@{
            pid         = $PID
            procStart   = $script:ActivityProcStart
            # 'gate' means "I am consuming build output and the runtime mutex". A run parked in
            # Wait-QueueResult consumes NEITHER, so it downgrades to 'waiter' for the duration —
            # see the deadlock note there. Every blocker check keys on kind eq 'gate'
            # (gate_queue_watcher.ps1 Test-PeerGateLive, Get-PeerGates here), so a waiter stays
            # visible to operators without blocking anyone.
            kind        = $script:ActivityKind
            checkout    = $repo
            sessionId   = $(if ($env:CLAUDE_SESSION_ID) { $env:CLAUDE_SESSION_ID } else { $null })
            ownerRootPid   = $(if ($o = Get-OwnerRoot) { $o.RootPid }   else { $null })
            ownerRootStart = $(if ($o = Get-OwnerRoot) { $o.RootStart } else { $null })
            label       = $Label
            startedAt   = $script:ActivityStart
            heartbeatAt = $now
            # A parked waiter is deliberately idle for its whole -WaitForQueue window (default
            # 1800s), so advertising a gate's 600s made every long wait read as stale/hung to
            # peers and to the activity hook. Advertise the window each kind actually occupies.
            expectedSec = $(if ($script:ActivityKind -eq 'waiter' -and $WaitForQueue -gt 0) { $WaitForQueue } else { 600 })
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

# Live PEER harness activity: a gate/suite record owned by another process whose
# PID still exists AND whose CreationDate matches the record (PID-reuse guard).
# A record failing either test is garbage and is swept here, so a crashed session
# cannot wedge a phantom blocker in the registry forever.
function Get-LivePeerActivity {
    param([datetime] $WindowEnd = (Get-Date))
    $live = @()
    if (-not (Test-Path $activityDir)) { return $live }
    foreach ($f in @(Get-ChildItem -Path $activityDir -Filter '*.json' -ErrorAction SilentlyContinue)) {
        $rec = $null
        try { $rec = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        $rpid = [int](Get-Prop $rec 'pid' -Default 0)
        if ($rpid -le 0 -or $rpid -eq $PID) { continue }
        if ((Get-Prop $rec 'kind' '') -notin @('gate', 'suite')) { continue }
        $proc = Get-Process -Id $rpid -ErrorAction SilentlyContinue
        if (-not $proc) { Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue; continue }
        # The [string] cast of a ConvertFrom-Json-materialized DateTime drops the Z marker;
        # the re-parse lands Kind=Unspecified and ToUniversalTime() ADDS the machine offset —
        # measured +5h skew (17999.7s), sweeping every live peer record as stale. Compare
        # DateTimes directly; only a raw string (hand-written record) goes through the parse.
        $ps = Get-Prop $rec 'procStart' ''
        if ($ps) {
            try {
                $psDt = if ($ps -is [datetime]) { $ps } else { [datetime]$ps }
                if ([math]::Abs(($psDt.ToUniversalTime() - $proc.StartTime.ToUniversalTime()).TotalSeconds) -gt 5) {
                    Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue; continue
                }
            } catch { continue }
        }
        # Same checkout AND same session is our own family, not a peer.
        if ((Get-Prop $rec 'checkout' '') -eq $repo -and
            $env:CLAUDE_SESSION_ID -and (Get-Prop $rec 'sessionId' '') -eq $env:CLAUDE_SESSION_ID) { continue }
        $started = $null
        try { $started = ([datetime](Get-Prop $rec 'startedAt' '')).ToUniversalTime() } catch { }
        if ($started -and $started -gt $WindowEnd.ToUniversalTime()) { continue }
        $live += $rec
    }
    $live
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
function Get-CheckoutTestRunners {
    param($Map)
    $vals = @()
    if ($Map -is [System.Collections.IDictionary]) { $vals = @($Map.Values) } else { $vals = @($Map) }
    @($vals | Where-Object {
        $n = $null
        foreach ($k in @('Name', 'ProcessName', 'Caption')) { if ($_.PSObject.Properties[$k]) { $n = [string]$_.$k; break } }
        $n -and $n -like 'Godot*' -and
        (Get-GodotRole -Proc $_ -Map $Map) -eq 'TestRunner' -and
        # -CandidateRoots is load-bearing: runners launch with a relative `--path .`, so only
        # the parent-chain attribution (which needs a candidate root) can place them.
        (Resolve-GodotCheckout -Proc $_ -Map $Map -CandidateRoots @($repo)) -eq $repo
    })
}

# ---------------------------------------------------------------- queue entry
function Get-GateArgs {
    # Plain array output, no comma-wrap: call sites use @(Get-GateArgs), and a wrapped return
    # would nest ([["-StaticOnly"]]) through the JSON round-trip — the watcher's Start-Process
    # then dies on an Object[] element inside ArgumentList (observed live 2026-08-13).
    $a = @()
    if ($StaticOnly)       { $a += '-StaticOnly' }
    if ($NoBaselineUpdate) { $a += '-NoBaselineUpdate' }
    if ($RetryOnly)        { $a += '-RetryOnly' }
    if ($NoReuse)          { $a += '-NoReuse' }
    $a
}

# Stamp the request with the digest this run is ACTUALLY testing. A queued run waits while
# the user keeps editing, so it re-digests at its own start (RunTree, below the build) and the
# request's own digest is by then a stale snapshot. Without this stamp a session arriving
# mid-run compared its tree against that stale snapshot, failed to match, and opened a SECOND
# request for a tree the live run was already testing byte-for-byte — measured 2026-08-23,
# three sessions, three serialized runs, two INVALID (q...-54276 ran a tree nobody still had).
# All sessions share ONE checkout: request digests are versions of one tree, not separate
# trees, and only the newest version exists on disk.
function Publish-RunDigest {
    if (-not $QueueId -or -not $script:RunTree) { return }
    try {
        $p = Join-Path $queueDir "$QueueId.request.json"
        if (-not (Test-Path $p)) { return }
        $obj = Get-Content -Path $p -Raw | ConvertFrom-Json
        # Add-Member -Force: a request written by an older gate build has no runDigest property,
        # and StrictMode makes the bare assignment throw on the missing member.
        $obj | Add-Member -NotePropertyName 'runDigest' -NotePropertyValue $script:RunTree.digest -Force
        $obj | Add-Member -NotePropertyName 'runHead'   -NotePropertyValue $script:RunTree.head   -Force
        $obj | Add-Member -NotePropertyName 'runPid'    -NotePropertyValue $PID                   -Force
        Set-Content -Path $p -Value ($obj | ConvertTo-Json -Depth 5) -Encoding utf8
        Emit "QUEUE_FOLD_OPEN id=$QueueId digest=$(Format-Short $script:RunTree.digest)"
    } catch { }
}

function New-GateRequest {
    param([string] $Reason, [string[]] $ExtraArgs = @())
    New-Item -ItemType Directory -Force -Path $queueDir | Out-Null
    $tree = Get-TreeDigest
    # Two sessions on the same tree get ONE run — including onto a run that has already fired,
    # matched against the tree it is actually testing.
    $join = Get-FoldTargetId -QueueDir $queueDir -Digest $tree.digest
    if ($join) { return $join }
    # 'q' prefix is load-bearing, not decoration. $runId (script top) uses the SAME
    # 'yyyyMMdd-HHmmss-PID' shape, and the queueing run's own Complete-Gate writes
    # <runId>.result.json into this very directory. A handoff inside the same clock
    # second as script start (the editor/peer preflight checks are reachable in well
    # under 1s) made request id == run id, so the QUEUED record instantly satisfied
    # the watcher's "result exists => not pending" test at gate_queue_watcher.ps1:157
    # and the queued run was dropped without ever firing. Namespacing the request id
    # makes the two files structurally incapable of colliding.
    $id  = 'q{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID
    $req = [ordered]@{
        id          = $id
        head        = $tree.head
        treeDigest  = $tree.digest
        attempt     = 0
        requestedAt = (Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ')
        reason      = $Reason
        gateArgs    = @(@(Get-GateArgs) + @($ExtraArgs) | Select-Object -Unique)
    }
    Set-Content -Path (Join-Path $queueDir "$id.request.json") -Value ($req | ConvertTo-Json -Depth 5) -Encoding utf8
    $id
}

# Ensure exactly one watcher per checkout. The mutex here is a PROBE, not the
# guard: the watcher re-acquires it for its own lifetime and exits silently if it
# loses the race, so a double spawn resolves itself.
function Start-GateWatcherIfAbsent {
    $mutex = $null
    try {
        $mutex = New-Object System.Threading.Mutex($false, "Global\pp-gatequeue-$(Get-WorktreeId $repo)")
        $got = $false
        try { $got = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
        if (-not $got) { return $false }
        $mutex.ReleaseMutex()
        # Detached: no -Wait, no redirected handles, so the child holds no pipe
        # back to the session that spawned it and survives the session's exit.
        Start-Process -FilePath 'pwsh' -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-File', (Join-Path $scripts 'gate_queue_watcher.ps1')) | Out-Null
        return $true
    } catch { return $false } finally { if ($mutex) { $mutex.Dispose() } }
}

# Block until a queued request's result lands, or the window expires. Returns the
# parsed result record, or $null on expiry.
#
# Polls the RESULT FILE and nothing else. Process-absence is not completion: the
# batched Integration runner tears testhost.exe and headless Godot down between
# every batch, so a waiter watching for their absence fires in that gap and reports
# a still-live peer run as finished (observed 2026-08-18). The result record is the
# only signal written exactly once, at genuine completion, by a single writer
# (Complete-Gate). Dedup-safe: when New-GateRequest folded us into an existing
# pending request we poll THAT id, so both sessions unblock on the one shared run.
# Sets $script:QueueWaitResult to the parsed result record, or leaves it $null on expiry.
#
# RESULT VIA A SCRIPT VARIABLE, NOT THE PIPELINE. Every Emit inside this loop writes to the
# output stream, and PowerShell folds a function's uncaptured output into its return value —
# so returning the record made the caller receive [heartbeat strings + record] as an array,
# whose Get-Prop lookups all missed and produced a fabricated `VERDICT=UNKNOWN exit=7`
# (measured in the e2e run, 2026-08-19). A verdict is the one thing here that must never be
# synthesized from stream noise, so the value leaves by a channel the stream cannot reach.
function Wait-QueueResult {
    param([string] $Id, [int] $Seconds)
    $script:QueueWaitResult = $null
    $resP      = Join-Path $queueDir "$Id.result.json"
    $reqP      = Join-Path $queueDir "$Id.request.json"
    $deadline  = (Get-Date).AddSeconds($Seconds)
    $nextBeat  = (Get-Date).AddSeconds(60)

    # DEADLOCK FIX — downgrade our own activity record for the duration of the wait.
    # gate_queue_watcher.ps1's Test-PeerGateLive refuses to fire while ANY live kind=gate
    # record exists. A gate parked here holds one, so it blocked the watcher from firing the
    # very run it was waiting for: measured 2026-08-19, the watcher fired 2s AFTER the 420s
    # wait expired, making -WaitForQueue strictly worse than not waiting. A parked run holds
    # no build output and no runtime mutex, so 'gate' was simply the wrong claim.
    $script:ActivityKind = 'waiter'
    Update-ActivityRecord -Label "waiting on queued run $Id" | Out-Null
    try {
        while ((Get-Date) -lt $deadline) {
            if (Test-Path $resP) {
                # The writer is Set-Content on a whole object, but a reader can still catch a
                # partial file; a parse failure means "not yet", never "no result".
                try {
                    $script:QueueWaitResult = (Get-Content -Path $resP -Raw | ConvertFrom-Json)
                    return
                } catch { }
            }
            # The request vanishing with no result means someone cleared the queue underneath
            # us. Wake now rather than serving out a 30-minute wait on a request that will
            # never produce anything.
            if (-not (Test-Path $reqP)) { return }
            if ((Get-Date) -ge $nextBeat) {
                # Heartbeat, not chatter. Emit refreshes this run's activity record, so without
                # it a long wait ages past the reader-side staleness window and peers read us as
                # hung — while we are in fact deliberately idle. It also gives an operator
                # tailing the background task a liveness signal instead of 30 minutes of silence.
                $att = '?'
                try { $att = [string](Get-Prop (Get-Content -Path $reqP -Raw | ConvertFrom-Json) 'attempt' '?') } catch { }
                $left = [int]((New-TimeSpan -End $deadline).TotalSeconds)
                # State, not just a countdown. "pending=1" was true both before the watcher fired
                # and fifteen minutes into the suites, so the line was identical whether the run
                # had started or not — the operator reads a shrinking number and cannot tell a
                # live run from a wedged queue. The fired run streams its phases to <id>.log; the
                # last line of it IS the progress feed, already on disk and previously unread.
                $state = 'queued'; $phase = 'waiting for a quiet checkout'
                try {
                    $logP = Join-Path $queueDir "$Id.log"
                    if (Test-Path $logP) {
                        $last = @(Get-Content -Path $logP -Tail 4 -ErrorAction SilentlyContinue |
                                  Where-Object { $_ -and $_.Trim() })
                        if ($last.Count -gt 0) { $state = 'running'; $phase = $last[-1].Trim() }
                    }
                } catch { }
                if ($state -eq 'queued') {
                    # Name WHY the checkout is not quiet. "waiting for a quiet checkout" alone is
                    # ambiguous between an open editor/playtest and a peer session's run, and the
                    # reader acts differently on each (close the editor vs leave the peer alone).
                    # The watcher already classifies the blocker into its own log — surface its
                    # latest verdict here instead of re-probing.
                    try {
                        $wlog = Join-Path $queueDir 'watcher.log'
                        if (Test-Path $wlog) {
                            $bline = @(Get-Content -Path $wlog -Tail 40 -ErrorAction SilentlyContinue |
                                       Where-Object { $_ -match 'BLOCKED blocker=|QUIET ' })
                            if ($bline.Count -gt 0 -and $bline[-1] -match 'blocker=(\S+)') {
                                $why = switch ($Matches[1]) {
                                    'editor'      { 'a Godot editor/playtest is open on this checkout — close it to let the run fire' }
                                    'test-runner' { 'a test runner (possibly a peer session) is live on this checkout — wait, never kill it' }
                                    'peer-gate'   { 'another session''s gate run is live — wait, never kill it' }
                                    default       { "the machine-global runtime mutex is held ($($Matches[1]))" }
                                }
                                $phase = "waiting for a quiet checkout: $why"
                            }
                        }
                    } catch { }
                }
                Emit "QUEUE_WAIT id=$Id state=$state attempt=$att remaining=${left}s phase=`"$phase`""
                $nextBeat = (Get-Date).AddSeconds(60)
            }
            Start-Sleep -Seconds 5
        }
    } finally {
        # Restored on every path: whatever runs after the wait is a real gate again.
        $script:ActivityKind = 'gate'
        Update-ActivityRecord -Label "queue wait finished ($Id)" | Out-Null
    }
}

function Invoke-QueueHandoff {
    param([string] $Reason)
    # A starvation handoff has, by construction, an inline run behind it whose green batches are
    # recorded in the state file — re-proving them is pure waste (measured 2026-08-19: 13 min
    # inline + 23 min queued full repeat for one verdict). Queue the re-run as -RetryOnly: it
    # re-runs only the non-green batches, and its composition guard degrades it to a full pass
    # by itself if the state file no longer matches.
    $extra = @()
    if ($Reason -eq 'budget-starvation' -and -not $RetryOnly) { $extra = @('-RetryOnly') }
    $id      = New-GateRequest -Reason $Reason -ExtraArgs $extra
    $spawned = Start-GateWatcherIfAbsent
    Add-Detail "Handed to the gate queue (reason=$Reason). The run fires when this checkout is quiet: no editor or playtest, no peer test runner, and the machine-global runtime mutex free. Watch it with ``/regression_gate --queue-status``; the result records the tree it actually ran against."
    Emit "QUEUED id=$id reason=$Reason watcher=$(if ($spawned) { 'started' } else { 'live' })"

    if ($WaitForQueue -gt 0) {
        Emit "QUEUE_WAIT id=$id window=${WaitForQueue}s"
        Wait-QueueResult -Id $id -Seconds $WaitForQueue
        $res = $script:QueueWaitResult
        # A record with no readable status is not a verdict. Adopting one would hand the caller
        # a synthesized answer with a gate's authority behind it — strictly worse than exit 7,
        # which at least says "no answer yet". Fall through to the expiry path instead.
        if ($res -and -not [string](Get-Prop $res 'status' '')) {
            Emit "QUEUE_WAIT id=$id malformed-result=1 (ignored)"
            $res = $null
        }
        if ($res) {
            # Adopt the queued run's verdict as this invocation's exit status, so a caller
            # that must gate a commit gets a real answer instead of a pointer. Provenance is
            # explicit: ReusedFrom threads the producing run's id into our own record, and
            # treeDelta is surfaced because a run whose tree moved underneath it backs no
            # "Verified" claim regardless of its verdict.
            $qStatus = [string](Get-Prop $res 'status' '')
            $qExit   = [int](Get-Prop $res 'exitCode' -Default 7)
            $qDelta  = Get-Prop $res 'treeDelta' $false
            $script:ReusedFrom = $id
            Emit "QUEUE_RESULT id=$id status=$qStatus exit=$qExit treeDelta=$qDelta detail=$(Get-Prop $res 'detail' '-')"
            Add-Detail "## Queued run completed inside the wait window`n`nThis invocation waited ${WaitForQueue}s and adopted queued run ``$id``'s verdict (**$qStatus**, exit $qExit). Its full detail is at ``$(Get-Prop $res 'detail' '-')``.$(if ($qDelta -eq $true) { " **treeDelta=true** — the tree changed between the request and the run, so this result cannot back a ``Verified`` claim." })"
            Complete-Gate $qStatus $qExit
        }
        Emit "QUEUE_WAIT id=$id expired=1 resume=re-invoke"
        Add-Detail @"
## Wait expired — the run is queued, not lost

The ${WaitForQueue}s window closed before queued run ``$id`` finished. The request is still
pending and the detached watcher still owns it. Expiry wakes the caller to re-check; it
never cancels the run.

**To resume: re-invoke the gate with the same flags.** One of two things happens, both correct:

- the queued run finished meanwhile -> the ledger serves its verdict via ``REUSE`` (digest +
  mode match, under 6h) and you get the real answer immediately, with no second run;
- it is still pending -> the request dedups by tree digest, so you fold back into ``$id``
  and resume waiting rather than stacking a duplicate run.

The result is also announced unprompted on the next turn as a ``[gate-queue]`` line, and on
demand via ``/regression_gate --queue-status``.
"@
    }

    Complete-Gate 'QUEUED' 7
}

# Phase-boundary editor guard for the INLINE path. A queued run skips this: the
# watcher polls continuously and tree-kills the run, which a process cannot do
# to itself.
function Assert-TreeStable {
    # The gate builds ONCE (BUILD=OK above) but every batch re-invokes `dotnet test`, which
    # rebuilds. A concurrent session's mid-run edit turns later batches into compile failures
    # that PRESENT as test failures, while BUILD=OK at the top vouches for a tree that no
    # longer compiles. The digest re-check names the cause instead. The gate's own artifacts
    # (baseline, durations manifest, TestResults/) and the agent-artifact trees
    # (.claude/scratch/, .claude/auto-memory/, .claude/plans/) are excluded from the
    # digest, so the gate cannot trip itself — and a peer session's memory/plan writes
    # (design drives) are not under test either.
    param([string] $Phase)
    $now = Get-TreeDigest
    if (Test-DigestMatch $now.digest $script:RunTree.digest) { return }
    # Name the delta: an INVALID that doesn't say WHICH path moved forces post-hoc
    # archaeology against a transient state that may already be gone.
    $delta = @()
    try {
        $startE = @($script:RunTree.entries); $nowE = @($now.entries)
        $delta = @(Compare-Object -ReferenceObject $startE -DifferenceObject $nowE |
                   ForEach-Object { "$(if ($_.SideIndicator -eq '=>') { 'NOW ' } else { 'WAS ' })$($_.InputObject)" })
        if (-not $delta.Count -and ($now.head -ne $script:RunTree.head)) {
            # HEAD moved with an identical dirty-set: a peer session committed. If every
            # committed path is digest-excluded (harness commits — the measured recurring
            # case), the tested content is byte-identical: adopt the new head and continue.
            # Any non-excluded committed path, or an unreadable diff, stays INVALID.
            $moved = @(& git -C $repo diff --name-only "$($script:RunTree.head)..$($now.head)" 2>$null)
            $diffOk = ($LASTEXITCODE -eq 0)
            $hot = @($moved | ForEach-Object { $_ -replace '\\', '/' } | Where-Object {
                $p = $_; -not @($script:DigestExcl | Where-Object { $p.StartsWith($_) }).Count })
            if ($diffOk -and -not $hot.Count) {
                # Write-Host, NOT Emit: this is the one Assert-TreeStable path that RETURNS, and
                # Invoke-IntegrationRun captures its caller's output stream — an Emit here lands
                # inside the returned hashtable ($intRun.code lookup fails). Return-pollution class.
                $line = "TREE_HEAD_MOVED phase=$Phase from=$(Format-Short $script:RunTree.head) to=$(Format-Short $now.head) committedPaths=$($moved.Count) allExcluded=1 action=continue"
                Write-Host $line
                Update-ActivityRecord -Label $line
                $script:RunTree = $now
                return
            }
            $delta = @("HEAD $($script:RunTree.head) -> $($now.head)") +
                     @($hot | Select-Object -First 19 | ForEach-Object { "COMMITTED $_" })
        }
    } catch { $delta = @("(entry diff unavailable: $($_.Exception.Message))") }
    Emit "TREE_CHANGED=1 phase=$Phase delta=$($delta.Count)"
    foreach ($d in @($delta | Select-Object -First 20)) { Emit "TREE_DELTA $d" }
    Add-Detail ("## Tree changed mid-run ($Phase)`n`nThe working tree's digest differs from the run-start digest. A concurrent session's edit (or your own edit under test) invalidates every result after the change — later batches would fail with compile errors that present as test failures. Results are INVALID, not a regression. Re-run when the tree is stable.`n`nChanged entries:`n" + (($delta | Select-Object -First 50) -join "`n"))
    Complete-Gate 'INVALID' 2
}

function Assert-NoEditor {
    param([string] $Phase)
    if ($IgnoreEditor -or $FromQueue) { return }
    $eds = @(Get-LiveEditor -Checkout $repo -Map (Get-ProcSnapshot))
    if ($eds.Count -eq 0) { return }
    Emit "EDITOR detected=1 phase=$Phase"
    Add-Detail "A Godot editor appeared on this checkout at phase '$Phase'. The gate and the editor share ``.godot/mono/temp/bin/Debug/{{PROJECT_NAME}}.dll``, so they are mutually destructive; the editor wins unconditionally. The remaining work was queued."
    Invoke-QueueHandoff 'editor-appeared-midrun'
}

# exit != 0 with no 'Failed: N, Passed: M' line is the documented external-kill
# signature — but ONLY a signature. It becomes CONTENTION when a live peer
# harness run overlapped the window, never on the signature alone.
function Test-QueueHandoffCondition {
    # The INCOMPLETE -> QUEUED conversion predicate, factored as a named function so the
    # verification can call it directly with synthetic inputs. Busy = the run was starved by
    # the MACHINE (mutex wait, LOCKED batches, or a peer seen at any suite boundary), not by
    # its own slowness. -FromQueue runs never re-queue: the watcher already waited for quiet,
    # and re-queueing forever would loop.
    param([int] $LockWaitTotalMs = 0, [switch] $StatusLocked, [switch] $PeersSeen, [switch] $FromQueue)
    if ($FromQueue) { return $false }
    ($LockWaitTotalMs -gt 0) -or $StatusLocked -or $PeersSeen
}

function Get-SuitePeers {
    # Suite-boundary peer snapshot + $script:PeersSeen tracker in one call: a peer that
    # contended with a suite and then exited is the common shape, and the INCOMPLETE->QUEUE
    # conversion needs the overlap fact even after the peer is gone.
    $p = @(Get-LivePeerActivity)
    if ($p.Count -gt 0) { $script:PeersSeen = $true }
    $p
}

function Assert-NotContention {
    # $PeersAtStart is what makes this correct. Sampling only at call time — i.e. AFTER the suite has
    # finished — misses the peer that contended and then exited, which is the common shape: the peer's
    # run ending is often exactly what let ours finish. Measured 2026-08-13: Logic burned its 480s cap
    # against a concurrent gate, that gate exited first, Get-LivePeerActivity returned empty, and an
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
    $peers = @(Get-LivePeerActivity)
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
# hand off here and leave without anything to unwind. -FromQueue is refused: the watcher
# already detaches its child, and a second hop would orphan the QueueId this run must report.
if ($Detach) {
    if ($FromQueue) {
        Write-Output 'DETACH=REFUSED reason=from-queue (the watcher already spawns detached)'
        exit 4
    }
    $outPath = Join-Path $logDir "gate_run_$runId.out"
    $errPath = Join-Path $logDir "gate_run_$runId.err"
    $childArgs = @('-NoProfile', '-File', $PSCommandPath) + @(Get-GateArgs)
    if ($IgnoreEditor) { $childArgs += '-IgnoreEditor' }
    $childArgs += @('-WaitForQueue', "$WaitForQueue")
    # -WindowStyle Hidden + file redirection, no -Wait and no inherited handles: the child
    # keeps no channel back to this process, which is the whole point.
    $child = Start-Process -FilePath 'pwsh' -WindowStyle Hidden -ArgumentList $childArgs `
                -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    Write-Output "DETACHED pid=$($child.Id) mode=$($script:Mode)"
    Write-Output "OUT=$outPath"
    Write-Output "ERR=$errPath"
    Write-Output 'WAIT=poll OUT until a line begins VERDICT= (the run is over only then)'
    exit 0
}

Start-ActivityRecord
try {

# ---------------------------------------------------------------- cross-session REUSE
# A peer's completed verdict for byte-identical content + the same coverage contract + a live
# toolchain probe IS this run's verdict — the cheapest way to stop two gates fighting is for
# the second not to run. No in-flight wait: a busy machine queues (exit 7, <1s) and the reuse
# check re-runs at watcher-fire time, when the peer's result exists. Never fires for -NoReuse;
# always re-probes the engine (the gitignored toolchain is not in the digest). A reused FAIL
# carries the producer's failing-test list so the exit-1 adjudication presents real failures.
if (-not $NoReuse) {
    $curDigest = (Get-TreeDigest).digest
    $nowUtc    = (Get-Date).ToUniversalTime()
    $best      = $null
    foreach ($f in @(Get-ChildItem -Path $queueDir -Filter '*.result.json' -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
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
        # The reused run's own record must carry the CURRENT tree (a queued reused run's record
        # is what the watcher waits for — a null RunTree would throw under StrictMode and the
        # record would never land). The producer's verdict is only valid for its own digest,
        # which the scan already verified matches this one.
        $script:RunTree = Get-TreeDigest
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

# ---------------------------------------------------------------- queue entry
# DELIBERATELY BELOW the REUSE check. Ordered the other way, an open editor queued a
# full run even when a byte-identical verdict from minutes ago was sitting in the
# ledger — the caller waited (or gave up) for a result it already had. REUSE takes no
# locks, spawns nothing, and touches no build output, so it is safe beside a live
# editor; its only external call is the engine probe, which preflight above already
# made. Nothing below this point is editor-safe, which is why the check sits here and
# not later.
if (-not $IgnoreEditor) {
    $liveEds = @(Get-LiveEditor -Checkout $repo -Map (Get-ProcSnapshot))
    if ($liveEds.Count -gt 0) {
        Add-Detail "A Godot editor is open on this checkout ($($liveEds.Count) process(es))."
        Invoke-QueueHandoff 'editor-open'
    }
}

# ---------------------------------------------------------------- python
$py = $null
foreach ($c in @('python3', 'python', 'py')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) {
    Emit 'PREFLIGHT=BLOCKED reason=no-python-interpreter'
    Add-Detail 'None of python3/python/py resolved on PATH; the 1b-1h static guards cannot run.'
    Complete-Gate 'BLOCKED' 4
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

# Sparing a peer's runner is not enough on its own: the build below writes the
# same .godot/mono/temp/bin/Debug/{{PROJECT_NAME}}.dll a live peer run holds open,
# which lands MSB3021/MSB3027 -> BLOCKED. So peer quiet is a readiness clause for
# the BUILD, not just for the suites. Bounded, because blocking here would burn
# the caller's 600s ceiling and the run budget: at expiry the run goes to the
# queue, which is the general "machine busy" mechanism, not an editor-only one.
# A peer GATE is invisible to Get-CheckoutTestRunners until it reaches its suites: in guards/build it
# has spawned no runner. Two gates therefore both pass this check and collide later, at the suite stage,
# where the damage is (measured 2026-08-13: 12:41 and 13:06/13:07, the second starting 51s into the
# first's BUILD; the first collision cost a HANG verdict and ~25min). The activity registry carries a
# kind=gate record from Start-ActivityRecord — i.e. from the run's first moment — so consult it too.
# Skipped on -FromQueue: the watcher already established quiet, and re-queueing there could loop.
function Get-PeerGates {
    if ($FromQueue) { return @() }
    @(Get-LivePeerActivity | Where-Object {
        (Get-Prop $_ 'kind' '') -eq 'gate' -and (Get-Prop $_ 'checkout' '') -eq $repo
    })
}

# Cross-checkout gate preflight: REPORT, never queue.
#
# A peer gate in a DIFFERENT worktree shares exactly one resource with us — the
# machine-global runtime mutex (Global\gdunit4-{{PROJECT_NAME}}-runlock), and only for
# runtime suites. Everything else is per-checkout: its own .godot/mono/temp/bin/Debug
# build output, its own per-worktree Logic mutex (run_test_suite.ps1:378), its own
# salted GdUnit4 pipe. Queueing the whole run on that record therefore serialized
# preflight, guards, build, DOCS and every Logic suite — all fully parallel work — to
# protect a contention that already has a correct, finer-grained mechanism: the
# runtime suites block on the mutex, and a genuine starvation degrades to the LOCKED
# tier and queues from there.
#
# SAME-checkout gates are a different animal and are NOT relaxed: they share the build
# output, which is what produced the measured 2026-08-13 double-gate HANG. The wait-then-
# queue block below owns them via Get-PeerGates, and gives them a 90s wait first.
if (-not $FromQueue) {
    $machineGates = @(Get-LivePeerActivity | Where-Object {
        (Get-Prop $_ 'kind' '') -eq 'gate' -and (Get-Prop $_ 'checkout' '') -ne $repo
    })
    if ($machineGates.Count -gt 0) {
        # Emitted, not silent: runtime suites may still wait on the mutex, and an operator
        # reading a slow run needs the cross-checkout gate named rather than inferred.
        Emit "PEERS site=preflight crossCheckoutGates=$($machineGates.Count) action=proceed  (shares only the runtime mutex; runtime suites may wait)"
        Add-Detail "A gate is live in another checkout ($($machineGates.Count) record(s)). This run proceeds: the two share only the machine-global runtime mutex, so guards, build and Logic suites are parallel-safe. Runtime suites may wait on the mutex, and a wait that expires surfaces as ``STATUS=LOCKED`` → the LOCKED tier → the queue."
    }
}

$peerDeadline = (Get-Date).AddSeconds(90)
$peerRunners  = @(Get-CheckoutTestRunners -Map $map)
$peerGates    = @(Get-PeerGates)
if ($peerRunners.Count + $peerGates.Count -gt 0) {
    Emit "PEERS site=preflight runners=$($peerRunners.Count) gates=$($peerGates.Count) action=waiting  (peer run on this checkout — never reaped, we wait or queue)"
    while (($peerRunners.Count + $peerGates.Count) -gt 0 -and (Get-Date) -lt $peerDeadline) {
        Start-Sleep -Seconds 5
        $peerRunners = @(Get-CheckoutTestRunners -Map (Get-ProcSnapshot))
        $peerGates   = @(Get-PeerGates)
    }
    if ($peerRunners.Count + $peerGates.Count -gt 0) {
        Add-Detail "A peer session's gate or test run has held this checkout for 90s. Waiting inline would burn the run budget and land a false INCOMPLETE, so the run was queued instead. Two gates on one checkout do not merely duplicate work — they contend for the shared ``.godot/mono/temp/bin/Debug/`` output and the machine-global runtime mutex, which is what produces HANG/UNPARSED suites."
        Invoke-QueueHandoff $(if ($peerGates.Count -gt 0) { 'peer-gate' } else { 'peer-test-run' })
    }
}
# Outside the block on purpose: every site prints its own line, always, including at zero — an ABSENT
# line must mean "this site never ran", never "nothing was found". Emitting only when peers were seen
# made the quiet path indistinguishable from a skipped check, and contradicted the documented output.
Emit "PEERS site=preflight runners=$($peerRunners.Count) gates=$($peerGates.Count) action=proceed"

# Engine-version preflight: csproj SDK vs the engine .runsettings launches. The probe itself
# lives in Get-EngineProbe (defined above, near the utils) because REUSE also calls it.
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
$guards = [ordered]@{
    nullstrip      = 'tres_nullstrip_guard.py'
    tool_cascade   = 'tool_cascade_audit.py'
    script_strip   = 'tres_script_strip_guard.py'
    trail_seam     = 'trail_mutation_seam_guard.py'
    floorcell_seam = 'floorcell_mutation_seam_guard.py'
    gate_coverage  = 'test_suite_gate_coverage_guard.py'
    dup_double     = 'duplicate_test_double_guard.py'
    refcount_free  = 'refcounted_free_guard.py'
}
if ($true) {
    $results = @(); $blocked = @()
    foreach ($name in $guards.Keys) {
        $path = Join-Path $hooks $guards[$name]
        if (-not (Test-Path $path)) { $results += "$name=ABSENT"; continue }
        $out  = & $py $path 2>&1
        $code = $LASTEXITCODE
        if ($code -eq 0) { $results += "$name=OK" }
        else {
            $results += "$name=FAIL"
            $blocked += $name
            Add-Detail "## Guard $name (exit $code)`n`n``````"
            Add-Detail (($out | Out-String).TrimEnd())
            Add-Detail '``````'
        }
    }
    Emit "GUARDS $($results -join ' ')"
    if ($blocked.Count -gt 0) {
        Add-Detail "Each guard prints its own remediation above. Treat a guard red exactly like a build failure — these catch green-build/runtime-throw shapes (null-stripped value Exports, stripped script bindings, missing [Tool], un-gated suites) that no test can catch."
        Complete-Gate 'BLOCKED' 4
    }
} else {
    Emit 'GUARDS skipped=1'
}

# ---------------------------------------------------------------- build
# Warnings stay IN $buildOut (no -consoleLoggerParameters:ErrorsOnly) so the DOCS check below can
# read them. Costs nothing visible: $buildOut is a captured variable, printed only on build failure.
Assert-NoEditor -Phase 'build'
# Computed at run start, before the build — this is the tree the verdict is
# readable against, and a queued run's request tree may already be stale.
$script:RunTree = Get-TreeDigest
Emit ("TREE head={0} digest={1}" -f (Format-Short $script:RunTree.head), (Format-Short $script:RunTree.digest))
Publish-RunDigest
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

# ---------------------------------------------------------------- suites
function Invoke-Suite {
    param([string] $Label, [string] $Filter)
    # -IgnoreEditor always: the GATE owns editor policy for its children — Assert-NoEditor at
    # phase boundaries (inline) or the watcher's abort poll (queued). A child-level refusal
    # mid-phase would surface as UNPARSED/INVALID instead of a queue conversion.
    $out  = & pwsh -NoProfile -File (Join-Path $scripts 'run_test_suite.ps1') -Filter $Filter -Label $Label -IgnoreEditor 2>&1
    $code = $LASTEXITCODE
    $txt  = ($out | Out-String)
    $r = [ordered]@{ label = $Label; raw = $txt; exit = $code; status = 'UNKNOWN'; passed = -1; failed = -1; elapsed = ''; silentSkip = $false; failedTests = @() }

    if ($txt -match 'STATUS=(\w+)')                     { $r.status  = $Matches[1] }
    if ($txt -match 'elapsed=([\d.]+)s')                { $r.elapsed = $Matches[1] }
    if ($txt -match 'WARN=SILENT_SKIP_SIGNATURE')       { $r.silentSkip = $true }
    if ($txt -match 'Failed:\s*(\d+),\s*Passed:\s*(\d+)') {
        $r.failed = [int]$Matches[1]; $r.passed = [int]$Matches[2]
    }
    $r.failedTests = @([regex]::Matches($txt, 'FAILED_TEST=(.+)') | ForEach-Object { $_.Groups[1].Value.Trim() })

    # The runner reaps before EVERY suite and those kills were previously unreported — the operator
    # read `PREFLIGHT orphans_killed=0` (one site, run once) as a whole-run guarantee while a
    # per-suite reap took a peer's processes. Surface any reap that touched something; a 0/0 line
    # stays in the detail file so the summary keeps its ~10-line budget.
    foreach ($m in [regex]::Matches($txt, '(?m)^REAP site=(\S+) killed=(\d+) spared=(\d+)$')) {
        if ([int] $m.Groups[2].Value -eq 0 -and [int] $m.Groups[3].Value -eq 0) { continue }
        Emit $m.Value
    }
    Add-Detail "## $Label — reaper`n`n``````"
    Add-Detail (($txt -split "`r?`n" | Where-Object { $_ -match '^(REAP |  kill |  spare )' }) -join "`n")
    Add-Detail '``````'
    $r
}

function Test-SuiteTiers {
    # $R is an OrderedDictionary from Invoke-Suite or a Hashtable from the
    # Integration path — leave it untyped so neither is coerced.
    param($R, [string] $Label)
    $base     = [int]$baseline.suites.$Label.passed
    $sentinel = [int]$baseline.silent_skip_sentinels."${Label}_min"
    $warnAt   = [math]::Floor($base * [double]$baseline.drift_thresholds.warn_ratio)
    $hardAt   = [math]::Floor($base * [double]$baseline.drift_thresholds.hard_fail_ratio)
    if ($R.silentSkip)            { return @{ tier = 'INVALID'; note = "silent-skip signature (results INVALID regardless of count)" } }
    if ($R.status -in @('LOCK_TIMEOUT', 'LOCKED')) { return @{ tier = 'LOCKED'; note = "lock-wait window on the machine-global runtime mutex expired — a live holder owns the runtime slot" } }
    if ($R.passed -lt 0)          { return @{ tier = 'UNPARSED'; note = "no 'Failed: N, Passed: M' line in wrapper output — do NOT treat as green" } }
    if ($R.passed -lt $sentinel)  { return @{ tier = 'INVALID'; note = "passed=$($R.passed) below architectural floor $sentinel — GodotRuntimeExecutor likely failed" } }
    # A real failure explains its own count shortfall — do not also label it a
    # flaky-executor PARTIAL, and do not re-run it (step 5 adjudicates instead).
    if ($R.failed -gt 0)          { return @{ tier = 'FAILING';  note = "failed=$($R.failed) — genuine test failure, not a count-drift signal" } }
    if ($R.passed -lt $hardAt)    { return @{ tier = 'HARDFAIL'; note = "passed=$($R.passed) vs baseline $base ($([math]::Round(100*$R.passed/$base))%) — major drop" } }
    if ($R.passed -lt $warnAt)    { return @{ tier = 'WARN';     note = "passed=$($R.passed) vs baseline $base ($([math]::Round(100*$R.passed/$base))%) — moderate drop, needs user acknowledgement" } }
    if ($R.passed -lt $base)      { return @{ tier = 'PARTIAL';  note = "passed=$($R.passed) between sentinel and baseline $base — partial-but-clean flaky-executor shape" } }
    @{ tier = 'PASS'; note = '' }
}


$suiteResults = [ordered]@{}

# LOCKED conversion — the deterministic machine-busy verdict, keyed on the runner's own STATUS
# line and never on the advisory registry. A live holder — another session's gate, a raw suite
# run, or an unannounced consumer — owns the machine's single runtime slot. Nothing failed; the
# counts prove nothing. An inline run hands to the queue (exit 7); a -FromQueue run reports
# INCOMPLETE (exit 6) and the watcher re-queues it (capped at its attempt ceiling), because a
# watcher-fired run already waited for machine-wide quiet and re-queueing there could loop.
# Called mid-phase (a LOCKED Logic/Integration pre-empts the remaining suites, which would only
# re-wait the same mutex or trip Assert-NotContention's phase-level exit 8 before the
# adjudication LOCKED branch ever runs — measured 2026-08-14, V5 run C) or from adjudication
# (a LOCKED Sanity, the last phase).
function Complete-LockedGate {
    $lockedSuite = $suiteResults.Keys | Where-Object { $suiteResults[$_].t.tier -eq 'LOCKED' } | Select-Object -First 1
    if ($lockedSuite -and $suiteResults[$lockedSuite].t.note) {
        Add-Detail "## $lockedSuite — LOCKED`n`n$($suiteResults[$lockedSuite].t.note)"
    }
    Add-Detail "## Machine-busy — runtime mutex held`n`nAt least one suite reported LOCK_TIMEOUT/LOCKED: its lock-wait window expired while a live holder owned the machine-global runtime mutex. Not a regression. An inline run is handed to the gate queue (reason=runtime-mutex-busy); a queued re-fire reports INCOMPLETE and the watcher re-queues it."
    if (-not $FromQueue) { Invoke-QueueHandoff 'runtime-mutex-busy' }
    Complete-Gate 'INCOMPLETE' 6
}

# --- Logic
$logicFilter = 'FullyQualifiedName~Tests.Logic'
Assert-TreeStable -Phase 'suite:Logic'
Assert-NoEditor -Phase 'suite:Logic'
$t0 = Get-Date
$peers0 = @(Get-SuitePeers)
$r = Invoke-Suite -Label 'Logic' -Filter $logicFilter
$t = Test-SuiteTiers -R $r -Label 'Logic'
# Partial-but-clean: re-run that one suite ONCE before evaluating.
if ($t.tier -in @('PARTIAL', 'INVALID') -and $r.status -ne 'HANG') {
    Emit "SUITE Logic rerun=1 reason=$($t.tier)"
    $t0 = Get-Date
    $peers0 = @(Get-SuitePeers)
    $r = Invoke-Suite -Label 'Logic' -Filter $logicFilter
    $t = Test-SuiteTiers -R $r -Label 'Logic'
}
Assert-NotContention -R $r -StartedAt $t0 -Label 'Logic' -PeersAtStart $peers0
$suiteResults['Logic'] = @{ r = $r; t = $t }
Emit ("SUITE Logic passed={0} failed={1} tier={2} delta={3:+#;-#;+0} status={4} dur={5}s" -f `
      $r.passed, $r.failed, $t.tier, ($r.passed - [int]$baseline.suites.Logic.passed), $r.status, $r.elapsed)

# A LOCKED Logic tier means the wrapper's lock-wait on the machine-global runtime mutex expired —
# the slot was busy for the entire window. The remaining phases would only re-wait the same
# mutex (Integration) or run mid-phase under live peers, where Assert-NotContention's phase-level
# exit 8 pre-empts this deterministic tier (measured 2026-08-14, V5 run C). Convert now.
if ($t.tier -eq 'LOCKED') {
    Emit 'SUITE Integration skipped=1 reason=logic-locked'
    Emit 'SUITE Sanity skipped=1 reason=logic-locked'
    Complete-LockedGate
}

# --- Integration (batched runner already evaluates its own sentinel/baseline)
function Invoke-IntegrationRun {
    param([switch] $Retry)
    # Covers BOTH the first invocation and the automatic -RetryOnly pass: a tree changed
    # between them would make the retry's results unreadable against the run-start digest.
    Assert-TreeStable -Phase 'suite:Integration'
    $a = @('-NoProfile', '-File', (Join-Path $scripts 'run_integration_batched.ps1'), '-IgnoreEditor')
    if ($Retry) { $a += '-RetryOnly' }
    # Stream the runner's per-batch lines to stdout (Write-Host: the success stream is
    # captured by the caller's assignment); $o stays intact for STATUS parsing.
    $o = & pwsh @a 2>&1 | Tee-Object -Variable streamed
    $streamed | ForEach-Object { Write-Host $_ }
    @{ txt = ($o | Out-String); code = $LASTEXITCODE }
}

Assert-NoEditor -Phase 'suite:Integration'
$intT0  = Get-Date
$intPeers0 = @(Get-SuitePeers)
$intRun = Invoke-IntegrationRun -Retry:$RetryOnly
# exit 5 = BUDGET_EXCEEDED: the runner's wall-clock budget counts the wrapper's wait on the
# machine-global runtime mutex, so a concurrent suite run starves the last batches without anything
# having failed. -RetryOnly resumes exactly those batches and keeps prior greens, so recover
# automatically instead of reporting a partial count. Once only — a second overrun is real contention
# the gate cannot resolve on its own.
# A STATUS=LOCKED completion (mutex starvation, no budget skip) is NOT auto-retried: the
# automatic -RetryOnly pass would re-wait a still-held mutex. It routes to the queue
# conversion at the verdict cascade instead.
if ($intRun.code -eq 5 -and $intRun.txt -notmatch 'STATUS=LOCKED') {
    Emit 'SUITE Integration retry=1 reason=BUDGET_EXCEEDED'
    # Carry the first run's COMPLETENESS across the retry. $intRun is replaced wholesale below, so
    # without this the "batches were skipped" verdict the runner already emitted is discarded; if the
    # retry then reports no completeness of its own, $intComplete falls back to its UNKNOWN
    # initialiser, the INCOMPLETE guard downstream never fires, and a knowingly-partial total gets
    # tiered against the full baseline as a count regression. Measured 2026-08-13: batch B4 (247
    # tests) was skipped for budget under peer contention and the gate reported WARN delta=-247.
    $priorCompleteness = ([regex]::Match($intRun.txt, 'COMPLETENESS=.*')).Value
    $intT0  = Get-Date
    $intRun = Invoke-IntegrationRun -Retry
    if ($priorCompleteness -and $intRun.txt -notmatch 'COMPLETENESS=') {
        $intRun.txt = "$($intRun.txt)`n$priorCompleteness"
    }
}
$intTxt  = $intRun.txt
$intCode = $intRun.code
$intPassed = -1; $intFailed = -1; $intComplete = 'UNKNOWN'; $intStatus = 'UNKNOWN'
if ($intTxt -match 'TOTAL passed=(\d+) failed=(\d+)') { $intPassed = [int]$Matches[1]; $intFailed = [int]$Matches[2] }
if ($intTxt -match 'COMPLETENESS=(\w+)')             { $intComplete = $Matches[1] }
if ($intTxt -match 'STATUS=(\w+)\s*$')               { $intStatus = $Matches[1] }
elseif ($intTxt -match 'STATUS=(\w+)')               { $intStatus = $Matches[1] }
$intFailedTests = @([regex]::Matches($intTxt, 'FAILED_TEST=(.+)') | ForEach-Object { $_.Groups[1].Value.Trim() })
$intR = @{ label = 'Integration'; passed = $intPassed; failed = $intFailed; status = $intStatus; exit = $intCode
           silentSkip = ($intComplete -eq 'SILENT_SKIP'); failedTests = $intFailedTests; elapsed = ''; raw = $intTxt }
Assert-NotContention -R $intR -StartedAt $intT0 -Label 'Integration' -PeersAtStart $intPeers0
$intT = Test-SuiteTiers -R $intR -Label 'Integration'
if ($intComplete -eq 'SHORTFALL' -and $intT.tier -eq 'PASS') { $intT = @{ tier = 'PARTIAL'; note = 'runner reported COMPLETENESS=SHORTFALL' } }
# A partial total is not a count regression — tiering it against the full baseline yields HARDFAIL,
# which reads as "major drop" for a run that simply did not finish. Real failures still outrank it.
# UNKNOWN counts as not-comparable, not as complete. The runner emits COMPLETENESS on every exit
# path, so its ABSENCE means the run did not report — which is weaker evidence than INCOMPLETE, never
# stronger. Treating it as comparable is what turns a silent partial into a fabricated count drop.
if ($intComplete -in @('INCOMPLETE', 'UNKNOWN') -and $intFailed -le 0 -and $intT.tier -ne 'LOCKED') {
    $why = if ($intComplete -eq 'UNKNOWN') { 'the runner reported no COMPLETENESS at all, so the total cannot be assumed whole' }
           else { 'runner reported COMPLETENESS=INCOMPLETE after an automatic -RetryOnly pass — batches were skipped for wall-clock budget' }
    $intT = @{ tier = 'INCOMPLETE'; note = "$why, so passed=$intPassed is partial by construction and is NOT comparable to the baseline" }
}
$suiteResults['Integration'] = @{ r = $intR; t = $intT }
Emit ("SUITE Integration passed={0} failed={1} tier={2} delta={3:+#;-#;+0} completeness={4} exit={5}" -f `
      $intPassed, $intFailed, $intT.tier, ($intPassed - [int]$baseline.suites.Integration.passed), $intComplete, $intCode)

if ($intT.tier -eq 'LOCKED') {
    Emit 'SUITE Sanity skipped=1 reason=integration-locked'
    Complete-LockedGate
}

# --- Sanity (always: the tier that used to defer it bought 21 seconds of a 576-second suite)
if ($true) {
    Assert-TreeStable -Phase 'suite:Sanity'
    Assert-NoEditor -Phase 'suite:Sanity'
    $t0 = Get-Date
    $peers0 = @(Get-SuitePeers)
    $r = Invoke-Suite -Label 'Sanity' -Filter 'FullyQualifiedName~Tests.Sanity'
    $t = Test-SuiteTiers -R $r -Label 'Sanity'
    if ($t.tier -in @('PARTIAL', 'INVALID') -and $r.status -ne 'HANG') {
        Emit "SUITE Sanity rerun=1 reason=$($t.tier)"
        $t0 = Get-Date
        $peers0 = @(Get-SuitePeers)
        $r = Invoke-Suite -Label 'Sanity' -Filter 'FullyQualifiedName~Tests.Sanity'
        $t = Test-SuiteTiers -R $r -Label 'Sanity'
    }
    Assert-NotContention -R $r -StartedAt $t0 -Label 'Sanity' -PeersAtStart $peers0
    $suiteResults['Sanity'] = @{ r = $r; t = $t }
    Emit ("SUITE Sanity passed={0} failed={1} tier={2} delta={3:+#;-#;+0} status={4} dur={5}s" -f `
          $r.passed, $r.failed, $t.tier, ($r.passed - [int]$baseline.suites.Sanity.passed), $r.status, $r.elapsed)
}

# ---------------------------------------------------------------- adjudicate
$tiers = @($suiteResults.Keys | ForEach-Object { $suiteResults[$_].t.tier })
foreach ($k in $suiteResults.Keys) {
    $sr = $suiteResults[$k]
    if ($sr.t.note) { Add-Detail "## $k — $($sr.t.tier)`n`n$($sr.t.note)" }
    if ($sr.r.failedTests.Count -gt 0) {
        Add-Detail "### $k failing tests"
        $sr.r.failedTests | ForEach-Object { Add-Detail "- $_" }
    }
}

if ($suiteResults.Keys | Where-Object { $suiteResults[$_].r.status -eq 'HANG' }) {
    Add-Detail 'A suite wedged and was tree-killed after the wrapper''s built-in retry. Re-run once. If a second run also HANGs or counts DROP across retries, machine named-pipe state is exhausted — reboot is the terminal fix.'
    Complete-Gate 'HANG' 124
}
# LOCKED: the deterministic machine-busy tier — converted by Complete-LockedGate (defined above
# the phase flow). Reached from adjudication only by a LOCKED Sanity (the last phase); Logic and
# Integration LOCKED tiers complete the gate at their phase boundary instead, so the remaining
# suites never re-wait the same mutex mid-hold.
if ($tiers -contains 'LOCKED') { Complete-LockedGate }
if ($tiers -contains 'INVALID' -or $tiers -contains 'UNPARSED') {
    Add-Detail 'Results are untrustworthy — not a regression signal. Kill orphaned Godot processes and re-run; do not interpret counts.'
    Complete-Gate 'INVALID' 2
}
$anyFailed = @($suiteResults.Keys | Where-Object { $suiteResults[$_].r.failed -gt 0 })
if ($anyFailed.Count -gt 0 -or ($tiers -contains 'HARDFAIL')) {
    Add-Detail 'Step 5 applies: present each failure and ask the user via AskUserQuestion (Fix now / Known issue / Abort). NEVER auto-fix, auto-skip, or auto-continue. If "Fix now", re-run ALL suites afterwards, not just the fixed one.'
    Complete-Gate 'FAIL' 1
}
if ($tiers -contains 'INCOMPLETE') {
    $lockWaitTotal = 0
    if ($intTxt -match 'LOCKWAIT_TOTAL_MS=(\d+)') { $lockWaitTotal = [int]$Matches[1] }
    $statusLocked = ($intTxt -match 'STATUS=LOCKED')
    if (Test-QueueHandoffCondition -LockWaitTotalMs $lockWaitTotal -StatusLocked:$statusLocked -PeersSeen:$script:PeersSeen -FromQueue:$FromQueue) {
        # Machine-busy starvation: the mutex was held, batches LOCKED out, or a peer overlapped
        # the run. Defer to the queue — the watcher fires when the machine is quiet — instead of
        # returning a partial verdict the operator must re-run by hand. The queued re-run
        # re-checks REUSE first, so a peer's completed result may resolve it without running.
        Add-Detail 'A suite did not FINISH — batches were skipped for budget or mutex-starvation, and the machine-busy signature holds (lock-wait, LOCKED batches, or a peer overlapped the run). Nothing failed; the counts are partial by construction. Handed to the gate queue instead of returning INCOMPLETE — the run fires when the machine is quiet.'
        Invoke-QueueHandoff 'budget-starvation'
    }
    Add-Detail 'A suite did not FINISH — batches were skipped for wall-clock budget even after the automatic -RetryOnly pass, with no machine-busy signature (no lock-wait, no LOCKED batches, no peer overlap). The machine was simply slow. Nothing failed; the counts are partial by construction and prove nothing either way. Re-run the gate when the machine is less loaded — prior green batches are preserved. Do NOT interpret the counts and do NOT treat this as a pass.'
    Complete-Gate 'INCOMPLETE' 6
}
if ($tiers -contains 'WARN' -or $tiers -contains 'PARTIAL') {
    Add-Detail 'Tier-2 moderate drop survived a re-run. Ask the user to acknowledge before proceeding. The baseline was NOT written.'
    Complete-Gate 'WARN' 3
}

# ------------------------------------------- 4b. [Tool] cascade import gate
# MUST run AFTER the suites, never before: the import spawns a Godot process
# that poisons the GdUnit4 runtime-executor handoff if it precedes them
# (observed as a false-low Integration count at its non-runtime floor).
# Catches what the static gate (1c) cannot: Node cascades, escape-hatch inline
# placements ([Export] Resource?), and Jmodot-side gaps.
if ($true) {
    Assert-TreeStable -Phase 'import-gate'
    Assert-NoEditor -Phase 'import-gate'
    # Orphan scope again: the suites have finished, so anything of OURS is gone,
    # and anything still alive on this checkout with a live parent belongs to a
    # peer session.
    foreach ($o in @(Get-ReapableGodot -Checkout $repo -Map (Get-ProcSnapshot))) {
        $opid = Get-ProcPid $o
        if ($opid -gt 0) { & taskkill /F /T /PID $opid *> $null }
    }
    $importLog = Join-Path $repo 'logs\tool_import_gate.log'
    New-Item -ItemType Directory -Force -Path (Split-Path $importLog) | Out-Null
    # $godotBin was resolved and existence-checked during engine preflight; an
    # empty path here would yield a vacuous PASS, so preflight already blocked.
    & $godotBin --headless --import --path $repo *> $importLog
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
$testWip = @(& git -C $repo status --porcelain -- Tests/ 2>$null | Where-Object {
    $_ -notmatch 'Tests/regression_baseline\.json' -and $_ -notmatch 'Tests/integration_batch_durations\.json'
})
$reestablish = ($stampTrust -eq 'UNTRUSTED')
if ($NoBaselineUpdate) {
    $script:BaselineAction = 'skipped:flag'
    Emit 'BASELINE action=skipped reason=flag'
} elseif ($testWip.Count -gt 0) {
    $script:BaselineAction = 'skipped:uncommitted-test-changes'
    Add-Detail "The tree carries uncommitted test changes ($($testWip.Count) file(s)) which may be a peer's — the baseline stays at its floor. The next clean-tree green run ratchets/re-establishes."
    Emit 'BASELINE action=skipped reason=uncommitted-test-changes'
} elseif ($deltas.Count -eq 0 -and -not $reestablish) {
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
    $act = if ($reestablish) { 're-established' } else { 'updated' }
    $script:BaselineAction = "$act $($deltas -join ' ')"
    Emit "BASELINE action=$act $($deltas -join ' ') stamp=$($head.Substring(0,10))"
}

Add-Detail 'All tiers clear. Stage Tests/regression_baseline.json (and integration_batch_durations.json / tool_resource_classes.txt if they changed) alongside whatever caused the growth.'
Complete-Gate 'PASS' 0

} finally {
    # Complete-Gate's `exit` unwinds through here, so every verdict path — and a
    # thrown terminating error — removes this run's activity record.
    Stop-ActivityRecord
}
