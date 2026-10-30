<#
.SYNOPSIS
  Proof for the gate's content digest (Get-TreeDigest in .claude/scripts/TreeDigest.ps1).

.WHY
  The digest decides whether a verdict transfers to another tree. It must depend on tree CONTENT
  only: a commit of digest-excluded files, or committing an edit already on disk, changes HEAD
  but no build input, and must not change the digest (PR 111 ran two full gates for that).

.RUN
  pwsh -NoProfile -File .claude/tests/test_gate_tree_digest.ps1
  Exit 0 = all pass, 1 = at least one failure.
#>

[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\TreeDigest.ps1')

$script:Fails = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "  PASS  $Name" }
    else { Write-Host "  FAIL  $Name $Detail"; $script:Fails++ }
}

$repo = Join-Path ([System.IO.Path]::GetTempPath()) "gatedigest-test-$PID"
function Invoke-G { & git -C $repo @args 2>&1 | Out-Null; if ($LASTEXITCODE -ne 0) { throw "git $args failed" } }
function Write-F { param([string] $Rel, [string] $Text)
    $p = Join-Path $repo $Rel
    New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent) | Out-Null
    [System.IO.File]::WriteAllText($p, $Text)
}
function Digest { (Get-TreeDigest -Repo $repo).digest }

try {
    if (Test-Path $repo) { Remove-Item -Recurse -Force $repo }
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    & git -C $repo init -q 2>&1 | Out-Null
    Invoke-G config user.email t@t
    Invoke-G config user.name t
    Invoke-G config core.autocrlf false
    Write-F 'src/a.cs' "a1`n"
    Write-F 'src/b.cs' "b1`n"
    Write-F 'Tests/regression_baseline.json' "{}`n"
    Write-F '.claude/x.md' "x1`n"
    Invoke-G add -A
    Invoke-G commit -q -m init

    $base = Digest
    Assert-True 'digest is 40 hex' ($base -match '^[0-9a-f]{40}$') "got '$base'"
    Assert-True 'stable across calls' ((Digest) -eq $base)

    Write-F 'Tests/regression_baseline.json' "{`"n`":1}`n"
    Assert-True 'excluded dirty edit leaves digest' ((Digest) -eq $base)
    Invoke-G add -A
    Invoke-G commit -q -m 'baseline only'
    Assert-True 'a) commit of only the excluded baseline leaves digest equal' ((Digest) -eq $base)

    Write-F 'src/a.cs' "a2`n"
    $dirty = Digest
    Assert-True 'c) editing a tracked file changes the digest' ($dirty -ne $base)
    Invoke-G add -A
    Invoke-G commit -q -m 'commit the dirty edit'
    Assert-True 'b) committing a dirty tracked edit keeps the digest' ((Digest) -eq $dirty)

    $before = Digest
    Write-F 'src/new.cs' "n1`n"
    Assert-True 'd) new untracked non-excluded file changes the digest' ((Digest) -ne $before)
    Remove-Item (Join-Path $repo 'src/new.cs')
    Assert-True 'removing the untracked file restores the digest' ((Digest) -eq $before)

    Remove-Item (Join-Path $repo 'src/b.cs')
    Assert-True 'e) deleting a tracked file changes the digest' ((Digest) -ne $before)
    Invoke-G checkout -- src/b.cs
    Assert-True 'restoring the file restores the digest' ((Digest) -eq $before)

    Write-F '.claude/x.md' "x2`n"
    Write-F '.claude/new.md' "n`n"
    Assert-True 'f) edits under .claude/ leave the digest' ((Digest) -eq $before)
    Invoke-G add -A
    Invoke-G commit -q -m 'claude only'
    Assert-True 'f) committing .claude/ edits leaves the digest' ((Digest) -eq $before)

    $r = Get-TreeDigest -Repo $repo
    Assert-True 'result carries head, digest, entries' ($r.head -match '^[0-9a-f]{40}$' -and @($r.entries).Count -ge 2)

    Write-F 'src/a.cs' "a-dirty-1`n"
    Write-F 'src/b.cs' "b-locked`n"
    $lock = [System.IO.File]::Open((Join-Path $repo 'src/b.cs'), 'Open', 'Read', 'None')
    try {
        $lockedBase = Digest
        Write-F 'src/a.cs' "a-dirty-2-while-b-locked`n"
        Assert-True 'g) one unhashable changed file does not blind the digest to another edit' ((Digest) -ne $lockedBase)
        $ent = @((Get-TreeDigest -Repo $repo).entries)
        Assert-True 'g) only the locked path is marked UNHASHABLE' (@($ent | Where-Object { $_ -like '*|UNHASHABLE' }).Count -eq 1 -and @($ent | Where-Object { $_ -like 'src/b.cs|UNHASHABLE' }).Count -eq 1)
    } finally { $lock.Dispose() }

    $notRepo = Join-Path ([System.IO.Path]::GetTempPath()) "gatedigest-notrepo-$PID"
    New-Item -ItemType Directory -Force -Path $notRepo | Out-Null
    try {
        $nr = Get-TreeDigest -Repo $notRepo
        Assert-True 'h) a directory that is not a git repo yields an empty digest' ($nr.digest -eq '') "got '$($nr.digest)'"
        Assert-True 'h) an empty digest never matches, not even itself' (-not (Test-DigestMatch $nr.digest $nr.digest))
    } finally { Remove-Item -Recurse -Force $notRepo -ErrorAction SilentlyContinue }
    Assert-True 'i) equal digests match; a prefix of a digest is not a match (every comparison is full against full)' (
        (Test-DigestMatch 'abcdef0123456789' 'abcdef0123456789') -and -not (Test-DigestMatch 'abcdef01' 'abcdef0123456789') -and -not (Test-DigestMatch 'abcdef0123456789' 'abcdef01'))

    # j) The Godot test host writes <X>.cs.uid beside every .cs it has not seen; that file is the run's own artifact.
    $j0 = Digest
    Write-F 'src/c.cs' "c1`n"
    $j1 = Digest
    Assert-True 'j) a new untracked .cs changes the digest' ($j1 -ne $j0)
    Write-F 'src/c.cs.uid' "uid://c1`n"
    Assert-True 'j) the engine-written untracked .cs.uid leaves the digest' ((Digest) -eq $j1)
    Invoke-G add src/c.cs src/c.cs.uid
    Invoke-G commit -q -m 'c with uid'
    $j2 = Digest
    Write-F 'src/c.cs.uid' "uid://c2`n"
    Assert-True 'j) an edit to a TRACKED .cs.uid still changes the digest' ((Digest) -ne $j2)

    $lib = Join-Path $repo 'Lib'
    New-Item -ItemType Directory -Force -Path $lib | Out-Null
    & git -C $lib init -q 2>&1 | Out-Null
    & git -C $lib config user.email t@t; & git -C $lib config user.name t; & git -C $lib config core.autocrlf false
    Write-F 'Lib/x.cs' "x1`n"
    & git -C $lib add -A 2>&1 | Out-Null; & git -C $lib commit -q -m lib 2>&1 | Out-Null
    Invoke-G add Lib
    Invoke-G commit -q -m 'add lib'
    $k0 = Digest
    Write-F 'Lib/x.cs' "x2`n"
    $k1 = Digest
    Assert-True 'k) a dirty edit inside a submodule changes the digest' ($k1 -ne $k0)
    & git -C $lib commit -q -am 'x2' 2>&1 | Out-Null
    Assert-True 'k) committing that edit inside the submodule keeps the digest' ((Digest) -eq $k1) "got $(Digest) want $k1"
    Invoke-G add Lib
    Invoke-G commit -q -m 'bump lib'
    Assert-True 'k) bumping the parent gitlink to it keeps the digest' ((Digest) -eq $k1)
}
finally {
    if (Test-Path $repo) { Remove-Item -Recurse -Force $repo -ErrorAction SilentlyContinue }
}

if ($script:Fails) { Write-Host "FAILED: $script:Fails"; exit 1 }
Write-Host 'ALL PASS'
exit 0
