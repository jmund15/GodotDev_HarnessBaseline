<#
  Agent twin checkout: a run that targets a checkout whose Godot editor or playtest is live runs in that checkout's
  twin worktree instead, so the editor never sees a build. The twin holds the source's exact bytes (Get-TreeDigest
  equal), so a verdict from it is a verdict on the source.

  Twin: <main checkout>/.claude/worktrees/<source leaf>--twin, a detached worktree of the source repository whose
  Jmodot is a detached worktree of the source's Jmodot repository. Ignored files survive a sync, so the twin's .godot
  import cache and build output stay warm across runs. Removal is a user-named delete like any worktree.

  Sync-TwinCheckout takes the twin's checkout lock (RuntimeSlots.ps1 Get-CheckoutLockName) and keeps it for the
  calling process's life: the runner re-enters it on the same thread, and a second routed run waits for the first
  instead of re-syncing files under it. The lock also writes the holder record RuntimeSlots.ps1 describes, so a waiting
  gate can name who holds the twin. A caller whose runs are child processes (verify.ps1, the batcher) passes
  -ReleaseLock, since its children take the same lock and would otherwise wait on their parent. HARNESS_TWIN_WARM=0 skips the engine import warm-up (proofs plant no engine).
#>

if (-not (Get-Command Get-CheckoutLockName -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'RuntimeSlots.ps1') }

# Ignored files a twin needs that a worktree never carries: the runners' entry checks refuse without them.
$script:TwinRequiredIgnored = @('.runsettings')
$script:TwinLockWaitMs = 1800000
$script:TwinLock = $null
$script:TwinHolderRecord = $false
$script:TwinHolderTwin = ''

function Initialize-Runsettings {
    <#
      Returns '' when $Repo already has .runsettings (never overwritten), 'generated' after the loader's setup_runsettings
      wrote it from .runsettings.template, else 'FAILED <reason>'. The loader owns the generation; this only calls its CLI.
    #>
    param([Parameter(Mandatory)] [string] $Repo)
    if (Test-Path -LiteralPath (Join-Path $Repo '.runsettings')) { return '' }
    $loader = Join-Path $PSScriptRoot '..\hooks\session_context_loader.py'
    $py = @('python3', 'python', 'py') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
    if (-not $py) { return 'FAILED no python interpreter on PATH' }
    if (-not (Test-Path -LiteralPath $loader)) { return "FAILED loader missing: $loader" }
    $out = (& $py $loader --setup-runsettings $Repo 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0 -and $out -match '^FIXED') { return 'generated' }
    return "FAILED $($out -replace '\s+', ' ')"
}

function Get-TwinFileHash {
    param([string] $Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash } else { '' }
}

function Get-TwinOutputHashes {
    # The caller names $Paths: the gate owns what a green run rewrites, so the list lives there, not here.
    param([Parameter(Mandatory)] [string] $Source, [Parameter(Mandatory)] [string[]] $Paths)
    $h = @{}
    foreach ($rel in $Paths) { $h[$rel] = Get-TwinFileHash (Join-Path $Source $rel) }
    $h
}

function Copy-TwinOutputsBack {
    # Each named output the twin changed goes back to the source unless the source's copy moved since the sync.
    param([Parameter(Mandatory)] [string] $Source, [Parameter(Mandatory)] [string] $Twin, [Parameter(Mandatory)] [string[]] $Paths, [Parameter(Mandatory)] [hashtable] $Hashes)
    foreach ($rel in $Paths) {
        $from = Join-Path $Twin $rel; $to = Join-Path $Source $rel
        $twinHash = Get-TwinFileHash $from
        if (-not $twinHash -or $twinHash -eq (Get-TwinFileHash $to)) { continue }
        if ((Get-TwinFileHash $to) -ne $Hashes[$rel]) {
            Write-Output "TWIN_WRITEBACK=skipped path=$rel reason=source-changed"
            continue
        }
        Copy-Item -LiteralPath $from -Destination $to -Force
        Write-Output "TWIN_WRITEBACK=copied path=$rel"
    }
}

function Get-TwinPath {
    param([Parameter(Mandatory)] [string] $Source)
    $common = (& git -C $Source rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $common) { throw "not a git checkout: $Source" }
    $main = Split-Path -Parent ([IO.Path]::GetFullPath([string] $common))
    Join-Path $main (".claude\worktrees\" + (Split-Path -Leaf $Source) + '--twin')
}

function Get-TwinDigest {
    param([Parameter(Mandatory)] [string] $Repo)
    # TreeDigest.ps1 sets StrictMode; a child scope keeps it there.
    $treeDigest = Join-Path $PSScriptRoot 'TreeDigest.ps1'
    & { . $args[0]; (Get-TreeDigest -Repo $args[1]).digest } $treeDigest $Repo
}

function Enter-TwinLock {
    param([Parameter(Mandatory)] [string] $Twin)
    $name = Get-CheckoutLockName -Repo $Twin
    $mutex = New-RunLock $name
    try {
        if (-not $mutex.WaitOne($script:TwinLockWaitMs)) { throw "twin busy past $($script:TwinLockWaitMs) ms: $Twin" }
    } catch [System.Threading.AbandonedMutexException] { }
    # Held until the process exits: the run that follows this sync owns the twin until it ends.
    $script:TwinLock = $mutex
    # A waiting gate names its holder from this record; a re-entry under a lease that already wrote one leaves it alone.
    $script:TwinHolderRecord = Write-CheckoutHolder -Repo $Twin -Label 'twin sync'
    $script:TwinHolderTwin = $Twin
}

function Exit-TwinLock {
    if ($script:TwinLock) {
        if ($script:TwinHolderRecord) { Remove-CheckoutHolder -Repo $script:TwinHolderTwin; $script:TwinHolderRecord = $false }
        $script:TwinLock.ReleaseMutex(); $script:TwinLock.Dispose(); $script:TwinLock = $null
    }
}

function Sync-GitTree {
    # Make $Target's working tree match $Source's: HEAD, then every modified, untracked or deleted non-ignored file.
    param([string] $Source, [string] $Target)
    $head = (& git -C $Source rev-parse HEAD)
    $rows = @(& git -C $Source -c core.quotepath=false ls-files -m -o -d -t --exclude-standard)
    # Copy-Item keeps the source's write time, which can predate the twin's last build; MSBuild then skips the
    # changed file. Each file whose content this sync changes is stamped now instead.
    $before = @{}
    foreach ($row in $rows) {
        $rel = $row.Substring(2); $path = Join-Path $Target $rel
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $before[$rel] = @{ Hash = Get-TwinFileHash $path; Time = (Get-Item -LiteralPath $path).LastWriteTimeUtc }
        }
    }
    & git -C $Target checkout --quiet --detach --force $head 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "checkout $head failed in $Target" }
    & git -C $Target clean -fdq 2>&1 | Out-Null
    foreach ($row in $rows) {
        $tag = $row.Substring(0, 1); $rel = $row.Substring(2)
        if ($rel -eq 'Jmodot') { continue }
        $from = Join-Path $Source $rel; $to = Join-Path $Target $rel
        if ($tag -eq 'R') { Remove-Item -LiteralPath $to -Force -ErrorAction SilentlyContinue; continue }
        if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { continue }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
        $prior = $before[$rel]
        $keep = $prior -and $prior.Hash -eq (Get-TwinFileHash $from)
        Copy-Item -LiteralPath $from -Destination $to -Force
        (Get-Item -LiteralPath $to).LastWriteTimeUtc = if ($keep) { $prior.Time } else { [DateTime]::UtcNow }
    }
}

function Copy-TwinGeneratedUids {
    # The warm import writes a .uid companion beside each script the source has not imported yet. The source needs
    # it too (it is committed with the script), and without it the digests differ and the run is refused.
    param([Parameter(Mandatory)] [string] $Source, [Parameter(Mandatory)] [string] $Twin)
    foreach ($rel in @(& git -C $Twin -c core.quotepath=false ls-files -o --exclude-standard -- '*.uid')) {
        $to = Join-Path $Source $rel
        if (Test-Path -LiteralPath $to) { continue }
        Copy-Item -LiteralPath (Join-Path $Twin $rel) -Destination $to
        Write-Host "TWIN_UID_COPIED path=$rel"
    }
}

function Sync-TwinCheckout {
    param([Parameter(Mandatory)] [string] $Source)
    $Source = [IO.Path]::GetFullPath($Source)
    $twin = Get-TwinPath -Source $Source
    Enter-TwinLock -Twin $twin
    $created = $false
    if (-not (Test-Path -LiteralPath (Join-Path $twin '.git'))) {
        & git -C $Source worktree add --quiet --detach $twin HEAD 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "worktree add failed: $twin" }
        $created = $true
    }
    $srcJm = Join-Path $Source 'Jmodot'; $twinJm = Join-Path $twin 'Jmodot'
    if (Test-Path -LiteralPath (Join-Path $srcJm '.git')) {
        if (-not (Test-Path -LiteralPath (Join-Path $twinJm '.git'))) {
            if ((Test-Path -LiteralPath $twinJm) -and @(Get-ChildItem -LiteralPath $twinJm -Force).Count -gt 0) {
                throw "twin Jmodot is non-empty and not a checkout: $twinJm"
            }
            & git -C $srcJm worktree add --quiet --detach $twinJm (& git -C $srcJm rev-parse HEAD) 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Jmodot worktree add failed: $twinJm" }
        }
    }
    Sync-GitTree -Source $Source -Target $twin
    if (Test-Path -LiteralPath (Join-Path $twinJm '.git')) { Sync-GitTree -Source $srcJm -Target $twinJm }

    [void](Initialize-Runsettings -Repo $Source)
    foreach ($rel in $script:TwinRequiredIgnored) {
        $from = Join-Path $Source $rel
        if (-not (Test-Path -LiteralPath $from -PathType Leaf)) { throw "source lacks required ignored file $rel" }
        Copy-Item -LiteralPath $from -Destination (Join-Path $twin $rel) -Force
    }

    # Warm when the twin was created, has no import cache, or holds fewer entries than the source's: the digest
    # compare below cannot see an ignored cold cache, and a partial one (an interrupted import, or assets the sync
    # just added) reports the same mass false reds as a missing one.
    $twinImported = Join-Path $twin '.godot\imported'
    $srcImported  = Join-Path $Source '.godot\imported'
    $twinCount    = @(Get-ChildItem -LiteralPath $twinImported -ErrorAction SilentlyContinue).Count
    $srcCount     = @(Get-ChildItem -LiteralPath $srcImported -ErrorAction SilentlyContinue).Count
    $cold = $created -or -not (Test-Path -LiteralPath $twinImported) -or $twinCount -lt $srcCount
    if ($cold -and $env:HARNESS_TWIN_WARM -ne '0') {
        & bash (Join-Path $PSScriptRoot 'godot_bin.sh') --headless --path $twin --import 2>&1 | Out-Null
        Copy-TwinGeneratedUids -Source $Source -Twin $twin
    }

    $want = Get-TwinDigest -Repo $Source; $have = Get-TwinDigest -Repo $twin
    if (-not $want -or $want -ne $have) { throw "twin digest $have differs from source digest $want" }
    [pscustomobject]@{ Path = $twin; Digest = $have }
}

function Get-RunRoute {
    <#
      Where a run should build, decided without a sync and without a lock: the twin's path when $Repo has a live editor
      or playtest (Twin true), else $Repo itself. A twin is terminal: it is harness-owned and holds no owner editor, so
      liveness on it never routes further. The path may not exist yet; the gate waits on the twin's lock first and syncs
      under it.
    #>
    param([Parameter(Mandatory)] [string] $Repo)
    if ((Split-Path -Leaf ([IO.Path]::GetFullPath($Repo).TrimEnd('\', '/'))) -like '*--twin') {
        return [pscustomobject]@{ Path = $Repo; Twin = $false }
    }
    if (@(Get-LiveEditor -Checkout $Repo).Count -eq 0) {
        return [pscustomobject]@{ Path = $Repo; Twin = $false }
    }
    [pscustomobject]@{ Path = (Get-TwinPath -Source ([IO.Path]::GetFullPath($Repo))); Twin = $true }
}

function Resolve-RunCheckout {
    <#
      The checkout a run should build in: $Repo itself when no editor or playtest is live on it, else its synced twin.
      Failed is true when $Repo is live and the twin could not be synced; the caller then keeps its old refusal.
    #>
    param([Parameter(Mandatory)] [string] $Repo, [switch] $ReleaseLock)
    try {
        $route = Get-RunRoute -Repo $Repo
        if (-not $route.Twin) { return [pscustomobject]@{ Path = $Repo; Twin = $false; Failed = $false } }
        $t = Sync-TwinCheckout -Source $Repo
        if ($ReleaseLock) { Exit-TwinLock }
        Write-Output "TWIN=$($t.Path) source=$Repo digest=$($t.Digest)" | Out-Host
        return [pscustomobject]@{ Path = $t.Path; Twin = $true; Failed = $false }
    } catch {
        Exit-TwinLock
        Write-Output "TWIN_SYNC_FAILED source=$Repo reason=$($_.Exception.Message)" | Out-Host
        return [pscustomobject]@{ Path = $Repo; Twin = $false; Failed = $true }
    }
}
