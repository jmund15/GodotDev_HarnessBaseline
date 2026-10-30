<#
.SYNOPSIS
  The content digest that names the tree a verdict was produced on (Get-TreeDigest), and the comparison that decides
  whether two digests name the same tree (Test-DigestMatch). Dot-sourced by regression_gate.ps1, run_integration_batched.ps1,
  TwinCheckout.ps1 and tools/boot_artifact.py.

.THE SOUNDNESS RULE
  A verdict transfers to another run only when their digests are equal. The digest is content-exact over every
  non-excluded path of the HEAD tree, with working-file overrides for changed paths, and carries no HEAD sha, so equal
  digests mean byte-identical build inputs however they were committed. A git failure yields an empty digest, which never
  matches. Unequal digests do not transfer: a PASS on tree A backs nothing on tree B.
#>

Set-StrictMode -Version Latest

# Exact comparison: every caller holds two full digests. Empty is never a match — an absent digest must not read as
# "same tree as everything".
function Test-DigestMatch {
    param([string] $A, [string] $B)
    if (-not $A -or -not $B) { return $false }
    $A -eq $B
}

# ---------------------------------------------------------------- tree digest
# Identity of the tree a run actually ran against. The gate's verdict is only readable against the tree it ran on.
#
# The gate's OWN artifacts are excluded: the ratchet rewrites the baseline during
# a green run, so an unexcluded digest would stamp every ratcheting run
# dirty against itself. All of .claude/ is the same class: harness doctrine, tooling, and
# agent artifacts are not under test, and a concurrent session (or the gate itself — the
# guard regenerates hooks/tool_resource_classes.txt) writing them must not invalidate a
# running gate (measured 2026-08-13 auto-memory, 2026-08-14 rules/ + hooks/:
# TREE_CHANGED=1 mid-run).
# Digest exclusion list — script scope so a caller can apply the same class test.
$script:DigestExcl = @('Tests/regression_baseline.json',
          'Tests/integration_batch_durations.json',
          'TestResults/',
          # Engine-regenerated import artifact: the Godot test host rewrites this CSV (+ its
          # .translation siblings) whenever spell .tres data changed, so a run adding spell
          # sheets self-invalidates mid-Logic (measured 2026-08-20, VERDICT=INVALID at
          # suite:Integration). Same class as TestResults/ — the run's own artifacts.
          'Tools/spell_stat_comparison',
          # Blanket, not per-tree: narrow carve-outs repeatedly missed a .claude tree and
          # tripped TREE_CHANGED (auto-memory 2026-08-13, rules/ + hooks/ 2026-08-14).
          '.claude/')

function Test-DigestExcluded {
    param([string] $Path)
    foreach ($e in $script:DigestExcl) { if ($Path.StartsWith($e)) { return $true } }
    $false
}

# One repo's paths named by `git status --porcelain -uall`, both sides of a rename, exclusion-
# filtered. -uall lists untracked files individually (their content is hashed too).
function Get-DigestStatusPaths {
    param([string] $Dir)
    $out = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $lines = @(& git -C $Dir -c core.quotepath=false status --porcelain -uall 2>$null)
    if ($LASTEXITCODE -ne 0) { $script:DigestGitFailed = $true }
    foreach ($line in $lines) {
        if (-not $line -or $line.Length -lt 4) { continue }
        $p = $line.Substring(3).Trim()
        $untracked = $line.StartsWith('??')
        foreach ($side in @($p -split '\s->\s')) {
            $q = $side.Trim().Trim('"') -replace '\\', '/'
            # The test host writes <X>.cs.uid for a .cs it has not seen: a gate beside any untracked .cs
            # would invalidate itself.
            if ($untracked -and $q.EndsWith('.cs.uid')) { continue }
            if ($q -and -not (Test-DigestExcluded $q)) { [void]$out.Add($q) }
        }
    }
    $out
}

# Working-file blob shas for $Paths in one git process; a path git cannot hash (vanished
# mid-run, a directory, locked open) maps to UNHASHABLE. When the batch returns the wrong
# number of shas, each path is re-hashed alone so one bad path marks only itself.
# Returns path -> sha.
function Get-WorkingBlobShas {
    param([string] $Dir, [string[]] $Paths)
    $res = @{}
    $live = @($Paths | Where-Object { [System.IO.File]::Exists((Join-Path $Dir $_)) })
    $shas = @()
    if ($live.Count) { $shas = @($live | & git -C $Dir hash-object --stdin-paths 2>$null) }
    if ($shas.Count -eq $live.Count) {
        for ($i = 0; $i -lt $live.Count; $i++) { $res[$live[$i]] = if ($shas[$i]) { [string]$shas[$i] } else { 'UNHASHABLE' } }
    } else {
        foreach ($p in $live) {
            $one = @($p | & git -C $Dir hash-object --stdin-paths 2>$null)
            $res[$p] = if ($LASTEXITCODE -eq 0 -and $one.Count -eq 1 -and $one[0]) { [string]$one[0] } else { 'UNHASHABLE' }
        }
    }
    $res
}

function Get-TreeDigest {
    # Content-exact and commit-invariant: the digest depends on the bytes a build would see,
    # never on how they were committed. The map starts from every `git ls-tree -r HEAD` blob
    # (path|blobsha), then each porcelain path overrides its entry with the WORKING file's
    # `git hash-object` (covers staged+unstaged identically; binaries hash raw bytes) or
    # removes it when the file is gone. A checked-out submodule contributes its own files the same
    # way under its path, never its commit sha, so committing inside it keeps the digest. The digest is the
    # SHA-1 of the ordinally sorted `path|value` lines; HEAD is returned for reporting only.
    param([Parameter(Mandatory)][string] $Repo)
    $script:DigestGitFailed = $false
    $head = (& git -C $Repo rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $head) {
        return [pscustomobject]@{ head = ''; digest = ''; entries = [string[]]@() }
    }
    $map = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
    $links = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)

    $tree = @(& git -C $Repo -c core.quotepath=false ls-tree -r HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { $script:DigestGitFailed = $true }
    foreach ($line in $tree) {
        $tab = $line.IndexOf("`t")
        if ($tab -lt 0) { continue }
        $path = $line.Substring($tab + 1).Trim('"')
        if (Test-DigestExcluded $path) { continue }
        $meta = $line.Substring(0, $tab) -split ' '
        if ($meta[0] -eq '160000') { [void]$links.Add($path); $map[$path] = "gitlink|$($meta[2])" }
        else { $map[$path] = $meta[2] }
    }

    foreach ($l in $links) {
        $full = Join-Path $Repo $l
        if (-not (Test-Path (Join-Path $full '.git'))) { continue }
        $subTree = @(& git -C $full -c core.quotepath=false ls-tree -r HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) { $script:DigestGitFailed = $true; continue }
        [void]$map.Remove($l)
        foreach ($line in $subTree) {
            $tab = $line.IndexOf("`t")
            if ($tab -lt 0) { continue }
            $map["$l/$($line.Substring($tab + 1).Trim('"'))"] = ($line.Substring(0, $tab) -split ' ')[2]
        }
        $subPaths = @(Get-DigestStatusPaths -Dir $full)
        $subShas  = Get-WorkingBlobShas -Dir $full -Paths $subPaths
        foreach ($p in $subPaths) {
            if ($subShas.ContainsKey($p)) { $map["$l/$p"] = $subShas[$p] } else { [void]$map.Remove("$l/$p") }
        }
    }

    $paths = @(Get-DigestStatusPaths -Dir $Repo | Where-Object { -not $links.Contains($_) })
    $shas  = Get-WorkingBlobShas -Dir $Repo -Paths $paths
    foreach ($p in $paths) {
        if ($shas.ContainsKey($p)) { $map[$p] = $shas[$p] } else { [void]$map.Remove($p) }
    }

    if ($script:DigestGitFailed) {
        return [pscustomobject]@{ head = $head; digest = ''; entries = [string[]]@() }
    }

    $entries = [string[]]@($map.GetEnumerator() | ForEach-Object { "$($_.Key)|$($_.Value)" })
    [Array]::Sort($entries, [System.StringComparer]::Ordinal)
    $sha = [System.Security.Cryptography.SHA1]::Create()
    try   { $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes(($entries -join "`n"))) }
    finally { $sha.Dispose() }
    [pscustomobject]@{ head = $head; digest = (($bytes | ForEach-Object { $_.ToString('x2') }) -join ''); entries = $entries }
}
