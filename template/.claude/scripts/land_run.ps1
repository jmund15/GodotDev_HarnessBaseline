# Lands one pushed `land/<name>` branch on main: proves its tip in the land worktree, then fast-forwards origin/main.
#   pwsh -NoProfile -File .claude/scripts/land_run.ps1 -Sha <commit> -Branch land/<name>
# Run by `.github/workflows/land.yml` on the self-hosted runner, inside the persistent land worktree
# (`.claude/worktrees/land-runner`, locked), so the proof pass cache and Godot's import cache stay warm between runs.
# Its checkouts are forced: a prior run's gate or close rewrites tracked files there, and nothing commits from it.
# Steps: a branch behind origin/main is rebased onto it first (the lock driver resolves lock rows; any other conflict
# stops the run); the harness close (`--base origin/main`); the regression gate unless every changed path is harness-only
# (`.claude/`, `.github/`, Markdown, the root `.gitignore`/`.searchignore`, which no build or test reads, or a `.gitattributes` edit whose changed lines are all `.claude/` patterns); a fast-forward push of the proven commit to main. A push that
# loses a race to a moved main rebases and re-proves once; a second move stops. The land branch is deleted while its
# tip is still the pushed commit, so a newer push to it keeps its own run. A commit main already holds lands as a
# no-op. A commit another land branch's tip already contains exits 0 as `LAND_RESULT=superseded ... by=<branch>`
# with no close: that branch's own run proves and lands it. Any red stops before main moves and exits 1 with a `LAND_RESULT=` line naming the stage. Stage lines stream to
# stdout as they happen.
param(
    [Parameter(Mandatory)] [string] $Sha,
    [Parameter(Mandatory)] [string] $Branch
)
$ErrorActionPreference = 'Stop'
$repo = (& git rev-parse --show-toplevel).Trim()

# The runner service starts without the environment a session has: the `env` blocks of the Claude settings files
# (user, then project, then local, later overriding earlier) and the HOME that Git Bash sets. The home directory is
# USERPROFILE on Windows and HOME on Linux; joining a null path throws, so pick whichever the platform set.
$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { $repo }
$settingsFiles = @((Join-Path $homeDir '.claude/settings.json'), (Join-Path $repo '.claude/settings.json'),
    (Join-Path $repo '.claude/settings.local.json'))
foreach ($settings in $settingsFiles) {
    if (-not (Test-Path -LiteralPath $settings)) { continue }
    $block = (Get-Content -Raw -LiteralPath $settings | ConvertFrom-Json).env
    if ($block) { foreach ($entry in $block.PSObject.Properties) { Set-Item -Path "env:$($entry.Name)" -Value ([string]$entry.Value) } }
}
if (-not $env:HOME) { $env:HOME = $homeDir }

function Stop-Land([string] $Stage, [string] $Detail) {
    Write-Output "LAND_RESULT=red stage=$Stage sha=$Sha branch=$Branch :: $Detail"
    exit 1
}

if ($Branch -notmatch '^land/[A-Za-z0-9._/-]+$' -or $Sha -notmatch '^[0-9a-f]{40}$') {
    Stop-Land 'args' 'expects -Sha <40-hex commit> and -Branch land/<name>'
}
$scratch = Join-Path $repo '.claude/scratch'
New-Item -ItemType Directory -Force -Path $scratch | Out-Null

# A dropped connection to the remote is transient, so each git call that reaches it gets three tries.
function Invoke-RemoteGit {
    for ($try = 1; $try -le 3; $try++) {
        & git @args
        if ($LASTEXITCODE -eq 0 -or $try -eq 3) { return }
        Write-Host "LAND retry git $($args[0]) (try $($try + 1) of 3)"
        Start-Sleep -Seconds 5
    }
}

# Replays the branch's own commits (those after their fork from $Onto) onto $Onto and returns the new tip. A conflict
# aborts the rebase and stops the run: the lock driver is the only merge driver that resolves rows here.
# The runner service has no git identity, so the replayed commits keep the pushed tip's committer.
function Invoke-Rebase([string] $Onto) {
    $fork = (& git merge-base $Onto $Sha).Trim()
    & git checkout -q --force --detach $Sha | Out-Null
    $env:GIT_COMMITTER_NAME = (& git log -1 --format=%cn $Sha)
    $env:GIT_COMMITTER_EMAIL = (& git log -1 --format=%ce $Sha)
    & git rebase -q --onto $Onto $fork | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $paths = (@(& git diff --name-only --diff-filter=U) -join ', ')
        & git rebase --abort
        Stop-Land 'rebase' "$Branch conflicts with origin/main $($Onto.Substring(0, 12)) in $paths; rebase locally and push again"
    }
    Remove-Item Env:GIT_COMMITTER_NAME, Env:GIT_COMMITTER_EMAIL -ErrorAction SilentlyContinue
    return (& git rev-parse HEAD).Trim()
}

Invoke-RemoteGit fetch -q origin main
if ($LASTEXITCODE -ne 0) { Stop-Land 'fetch' 'git fetch origin main failed' }
$base = (& git rev-parse origin/main).Trim()

function Remove-LandBranch {
    Invoke-RemoteGit push -q "--force-with-lease=refs/heads/${Branch}:$Sha" origin ":refs/heads/$Branch"
    if ($LASTEXITCODE -ne 0) { ' branch-delete=kept' } else { '' }
}

& git merge-base --is-ancestor $Sha $base
if ($LASTEXITCODE -eq 0) {
    $kept = Remove-LandBranch
    Write-Output "LAND_RESULT=green sha=$Sha branch=$Branch already-on-main=1$kept"
    exit 0
}

# A later push that already contains this commit (the same branch pushed again, or a branch stacked on it) has its own
# queued run, which proves and lands this commit too; proving it here first would repeat a whole close.
Invoke-RemoteGit fetch -q --prune origin '+refs/heads/land/*:refs/remotes/origin/land/*'
if ($LASTEXITCODE -ne 0) { Stop-Land 'fetch' 'git fetch of the land branches failed' }
foreach ($ref in @(& git for-each-ref --format='%(objectname) %(refname:strip=3)' refs/remotes/origin/land)) {
    $tipSha, $name = $ref -split ' ', 2
    if ($tipSha -eq $Sha) { continue }
    & git merge-base --is-ancestor $Sha $tipSha
    if ($LASTEXITCODE -eq 0) {
        $kept = Remove-LandBranch
        Write-Output "LAND_RESULT=superseded sha=$Sha branch=$Branch by=$name$kept"
        exit 0
    }
}
& git merge-base --is-ancestor $base $Sha
$behind = $LASTEXITCODE -ne 0

# Round 1 proves the branch as pushed (rebased first when main has moved past its fork). Round 2 runs only after a
# push lost a race to a moved main: it rebases onto the new main and proves again. A second loss stops the run.
for ($round = 1; $round -le 2; $round++) {
    if ($round -gt 1) {
        Invoke-RemoteGit fetch -q origin main
        if ($LASTEXITCODE -ne 0) { Stop-Land 'fetch' 'git fetch origin main failed' }
        $base = (& git rev-parse origin/main).Trim()
    }
    if ($round -gt 1 -or $behind) {
        $tip = Invoke-Rebase $base
    } else {
        & git checkout -q --force --detach $Sha
        $tip = $Sha
    }
    Invoke-RemoteGit submodule update -q --init --recursive
    if ($LASTEXITCODE -ne 0) { Stop-Land 'submodule' 'git submodule update failed: push each submodule commit the branch names before the branch (the submodule rules), then push the branch again' }

    # The close stops starting proofs and bisect steps at this deadline, so a slow close ends with a verdict before
    # land.yml's 240-minute job cap kills it with none. 210 minutes from the start of the close leave 30 for the gate
    # and the push.
    $env:HARNESS_DEADLINE = [string]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 210 * 60)
    $closeLog = Join-Path $scratch 'land_close.log'
    # A close left incomplete with no new red (machine load slowed its proofs past the deadline or a proof's wait) is
    # run once more: recorded passes are reused, so the rerun repeats only what did not finish.
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Write-Output "LAND stage=close base=$($base.Substring(0, 12)) sha=$($tip.Substring(0, 12))"
        & python3 .claude/scripts/harness_tests.py --close --base $base *>&1 | Tee-Object -FilePath $closeLog | ForEach-Object {
            if ($_ -match '^(FAIL|FIRST RED|NEEDS|harness_tests: (close|pre-existing|push|bisect))') { Write-Output "  $_" }
        }
        $closeText = Get-Content -Raw -LiteralPath $closeLog
        if ($closeText -notmatch 'harness_tests: close incomplete: .* 0 new red,') { break }
    }
    Remove-Item Env:HARNESS_DEADLINE
    $proven = [regex]::Match($closeText, 'git push origin ([0-9a-f]{40}):').Groups[1].Value
    if ($closeText -notmatch 'harness_tests: close green' -or $proven -ne $tip) {
        Stop-Land 'close' "no green close for $($tip.Substring(0, 12)); log $closeLog"
    }

    $changed = @(& git diff --name-only $base $tip)
    if ($LASTEXITCODE -ne 0) { Stop-Land 'diff' "git diff $($base.Substring(0, 12))..$($tip.Substring(0, 12)) failed" }
    # A .gitattributes edit whose every changed line is a .claude/ pattern changes no byte a build checks out.
    $attrsHarnessOnly = $false
    if ($changed -contains '.gitattributes') {
        $attrLines = @(& git diff -U0 $base $tip -- .gitattributes |
            Where-Object { $_ -match '^[+-]' -and $_ -notmatch '^(\+\+\+|---) ' } |
            ForEach-Object { $_.Substring(1).Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
        $attrsHarnessOnly = -not @($attrLines | Where-Object { $_ -notmatch '^\.claude/' }).Count
    }
    if ($changed | Where-Object { $_ -notmatch '^\.claude/|^\.github/|\.md$|^\.gitignore$|^\.searchignore$' -and
                                  -not ($_ -eq '.gitattributes' -and $attrsHarnessOnly) }) {
        Write-Output 'LAND stage=gate'
        $gateLog = Join-Path $scratch 'land_gate.log'
        & pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 *>&1 | Tee-Object -FilePath $gateLog | ForEach-Object {
            if ($_ -match '^(BUILD|IMPORT_GATE|SUITE|BATCH|DIAGNOSE|VERDICT|GATE_ERROR|TREE_CHANGED|MANIFEST)') { Write-Output "  $_" }
        }
        if ((Get-Content -Raw -LiteralPath $gateLog) -notmatch '(?m)^VERDICT=PASS') { Stop-Land 'gate' "gate not PASS; log $gateLog" }
    } else {
        Write-Output 'LAND stage=gate skipped=1 reason=harness-only'
    }

    Write-Output 'LAND stage=push'
    Invoke-RemoteGit push origin "${tip}:refs/heads/main"
    if ($LASTEXITCODE -eq 0) { break }
    if ($round -eq 2) { Stop-Land 'push' 'origin/main moved during the run; rebase the branch on origin/main and push it again' }
    Write-Output 'LAND push lost to a moved origin/main; rebasing once onto it'
}

$kept = Remove-LandBranch
$landed = if ($tip -ne $Sha) { " landed=$tip" } else { '' }
Write-Output "LAND_RESULT=green sha=$Sha branch=$Branch$landed$kept"
