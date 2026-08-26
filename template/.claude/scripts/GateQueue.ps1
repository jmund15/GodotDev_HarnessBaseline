<#
.SYNOPSIS
  The gate queue's file protocol: which pending request a run may satisfy, and how a digest
  comparison is made. Dot-sourced by regression_gate.ps1, gate_queue_watcher.ps1, and
  .claude/tests/test_gate_queue_fold.ps1.

.WHY THIS EXISTS
  Three sessions share one checkout, so their request digests are versions of ONE tree, not
  separate trees. Deciding which of them a single run satisfies is the one place in the queue
  where a wrong answer is a FALSE GREEN — a verdict handed to a session whose edits the run
  never compiled. That decision was previously inline in two scripts (the watcher had its own
  copy of the prefix compare), with no test on either. It lives here so it has exactly one
  implementation and a re-runnable proof.

.THE SOUNDNESS RULE
  A run satisfies a request if and only if their digests match. The digest is content-exact
  over every non-excluded dirty path, so equal digests mean byte-identical build inputs and
  the verdict transfers whole. Unequal digests are left pending on purpose: a PASS on tree A
  backs nothing on tree B, and the watcher re-fires them against the live tree.
#>

Set-StrictMode -Version Latest

# Reads a field off either a PSCustomObject (ConvertFrom-Json) or a dictionary without
# tripping StrictMode's non-existent-property rule.
function Get-QueueProp {
    param($Obj, [string] $Name, $Default = $null)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] } else { return $Default }
    }
    if ($Obj.PSObject.Properties[$Name]) { return $Obj.$Name }
    $Default
}

# Prefix comparison: digests cross the process boundary abbreviated in the gate's TREE line,
# so a full digest is compared against a short one on the watcher's parse path. Empty is never
# a match — an absent digest must not read as "same tree as everything".
function Test-DigestMatch {
    param([string] $A, [string] $B)
    if (-not $A -or -not $B) { return $false }
    $n = [math]::Min($A.Length, $B.Length)
    $A.Substring(0, $n) -eq $B.Substring(0, $n)
}

# Every pending request in $QueueDir, oldest first. Pending means: parses, carries an id, and
# has no result file. A result file is the single completion signal (written once, by
# Complete-Gate), so its presence is what makes a request no longer claimable.
function Get-PendingQueueRequests {
    param([string] $QueueDir)
    $out = @()
    foreach ($f in @(Get-ChildItem -Path $QueueDir -Filter '*.request.json' -ErrorAction SilentlyContinue | Sort-Object Name)) {
        $req = $null
        try { $req = Get-Content -Path $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        if (-not [string](Get-QueueProp $req 'id' '')) { continue }
        $out += $req
    }
    @($out | Where-Object { -not (Test-Path (Join-Path $QueueDir "$([string](Get-QueueProp $_ 'id' '')).result.json")) })
}

# The id a newly-arriving session should JOIN instead of opening a second request, or '' when
# none matches and it must open its own.
#
# runDigest before treeDigest is the whole point. A queued run waits while the user keeps
# editing and re-digests the live tree at its own start, so by the time it is running its
# request-time digest is a stale snapshot. Matching on that snapshot made a session whose tree
# was byte-identical to the RUNNING tree fail to join, and open a duplicate run — measured
# 2026-08-23: three sessions, three serialized runs, two INVALID. runDigest is absent until
# the run publishes it, and then it is the only honest answer to "what is being tested".
function Get-FoldTargetId {
    param([string] $QueueDir, [string] $Digest)
    foreach ($req in @(Get-PendingQueueRequests -QueueDir $QueueDir)) {
        $cmp = [string](Get-QueueProp $req 'runDigest' '')
        if (-not $cmp) { $cmp = [string](Get-QueueProp $req 'treeDigest' '') }
        if (Test-DigestMatch $cmp $Digest) { return [string](Get-QueueProp $req 'id' '') }
    }
    ''
}

# The ids a finishing run may satisfy besides its own: pending requests whose REQUEST-time
# digest equals the tree the run actually tested. Deliberately not runDigest — a request
# carrying someone else's runDigest is another run's, and claiming it would let two runs
# write one result.
function Get-FoldableRequestIds {
    param([string] $QueueDir, [string] $RunId, [string] $RunDigest)
    if (-not $RunDigest) { return @() }
    @(Get-PendingQueueRequests -QueueDir $QueueDir |
      Where-Object { [string](Get-QueueProp $_ 'id' '') -ne $RunId } |
      Where-Object { Test-DigestMatch ([string](Get-QueueProp $_ 'treeDigest' '')) $RunDigest } |
      ForEach-Object { [string](Get-QueueProp $_ 'id' '') })
}
