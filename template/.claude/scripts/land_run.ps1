# Lands one pushed `land/<name>` branch on main: fetches origin/main, rebases the branch onto it when main has moved,
# reads the submodules, and fast-forwards origin/main. It runs no proof and no C# gate: the main check proves main
# after the land (`rules/harness_tooling.md` §Land and main check).
#   pwsh -NoProfile -File .claude/scripts/land_run.ps1 -Sha <commit> -Branch land/<name>
# Run by `.github/workflows/land.yml` on a GitHub-hosted runner, in a checkout of the pushed commit. Its checkouts are
# forced, so a leftover in the worktree never blocks one.
# Steps: a branch behind origin/main is rebased onto it first (the lock driver resolves lock rows; any other conflict
# stops the run); a fast-forward push of the commit to main. A push that loses a race to a moved main fetches, rebases
# and pushes again until it lands, the rebase conflicts, or the job's cap ends it; a push the remote refuses while main
# has not moved stops the run. The land branch is deleted while its tip is still the pushed commit, so a newer push to
# it keeps its own run. A commit main already holds lands as a no-op. Any red stops before main moves and exits 1 with a
# `LAND_RESULT=` line naming the args, fetch, rebase, submodule or push stage. Stage lines stream to stdout as they happen.
param(
    [Parameter(Mandatory)] [string] $Sha,
    [Parameter(Mandatory)] [string] $Branch
)
$ErrorActionPreference = 'Stop'

function Stop-Land([string] $Stage, [string] $Detail) {
    Write-Output "LAND_RESULT=red stage=$Stage sha=$Sha branch=$Branch :: $Detail"
    exit 1
}

if ($Branch -notmatch '^land/[A-Za-z0-9._/-]+$' -or $Sha -notmatch '^[0-9a-f]{40}$') {
    Stop-Land 'args' 'expects -Sha <40-hex commit> and -Branch land/<name>'
}

# A dropped connection to the remote is transient, so each git call that reaches it gets three tries.
# A push the remote rejects (a moved main, a lost lease) exits 1 and fails the same way again, so it returns at once;
# a dropped connection exits 128.
function Invoke-RemoteGit {
    for ($try = 1; $try -le 3; $try++) {
        & git @args
        if ($LASTEXITCODE -eq 0 -or $try -eq 3 -or ($args[0] -eq 'push' -and $LASTEXITCODE -eq 1)) { return }
        Write-Host "LAND retry git $($args[0]) (try $($try + 1) of 3)"
        Start-Sleep -Seconds 5
    }
}

# Replays the branch's own commits (those after their fork from $Onto) onto $Onto and returns the new tip. A conflict
# aborts the rebase and stops the run: the lock driver is the only merge driver that resolves rows here.
# The runner has no git identity, so the replayed commits keep the pushed tip's committer.
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
& git merge-base --is-ancestor $base $Sha
$behind = $LASTEXITCODE -ne 0

# Each round integrates the branch onto the current main and pushes. A push that loses a race to a moved main starts
# the next round on the new main; the rounds end when the push lands, a rebase conflicts or the job's cap ends them.
for ($round = 1; ; $round++) {
    if ($round -gt 1 -or $behind) {
        $tip = Invoke-Rebase $base
    } else {
        & git checkout -q --force --detach $Sha
        $tip = $Sha
    }
    Invoke-RemoteGit submodule update -q --init --recursive
    if ($LASTEXITCODE -ne 0) { Stop-Land 'submodule' 'git submodule update failed: push each submodule commit the branch names before the branch (the submodule rules), then push the branch again' }

    Write-Output 'LAND stage=push'
    Invoke-RemoteGit push origin "${tip}:refs/heads/main"
    if ($LASTEXITCODE -eq 0) { break }
    Invoke-RemoteGit fetch -q origin main
    if ($LASTEXITCODE -ne 0) { Stop-Land 'fetch' 'git fetch origin main failed' }
    $moved = (& git rev-parse origin/main).Trim()
    if ($moved -eq $base) { Stop-Land 'push' 'the remote refused the push to main while origin/main did not move' }
    $base = $moved
    Write-Output 'LAND push lost to a moved origin/main; rebasing onto it'
}

$kept = Remove-LandBranch
$landed = if ($tip -ne $Sha) { " landed=$tip" } else { '' }
Write-Output "LAND_RESULT=green sha=$Sha branch=$Branch$landed$kept"
