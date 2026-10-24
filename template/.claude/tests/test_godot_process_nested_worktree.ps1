#requires -Version 7
# Proof for GodotProcess.ps1 Test-UnderRoot's nested-worktree guard: a process whose path lies in
# any checkout nested under the main root belongs to that checkout, not to the main root.
#   pwsh -NoProfile -File .claude/tests/test_godot_process_nested_worktree.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..' 'scripts' 'GodotProcess.ps1')

$root = 'C:\repo'
$cases = @(
    @('the main checkout''s own testhost is under Root', 'C:\repo\.godot\mono\temp\bin\testhost.exe', $true),
    @('a .claude\worktrees checkout is not Root''s', 'C:\repo\.claude\worktrees\w1\.godot\mono\temp\bin\testhost.exe', $false),
    @('a .claude\.cache\task-worktrees checkout is not Root''s', 'C:\repo\.claude\.cache\task-worktrees\w1\.godot\mono\temp\bin\testhost.exe', $false),
    @('a .claude\.cache dir not named *worktrees stays under Root', 'C:\repo\.claude\.cache\baseline-repo\bin\testhost.exe', $true),
    @('a .claude\.cache\baseline-worktrees checkout is not Root''s', 'C:\repo\.claude\.cache\baseline-worktrees\w1\bin\testhost.exe', $false),
    @('an unrelated path is not under Root', 'D:\other\testhost.exe', $false)
)
$failed = 0
foreach ($c in $cases) {
    $got = Test-UnderRoot $c[1] $root
    if ($got -eq $c[2]) { Write-Host "ok   $($c[0])" } else { Write-Host "FAIL $($c[0]) (got $got)"; $failed++ }
}
function New-PlantedProc([int] $ProcId, [int] $ParentId, [string] $CommandLine, [datetime] $Created) {
    [pscustomobject]@{ ProcessId = $ProcId; ParentProcessId = $ParentId; Name = 'Godot_v4.exe'; CommandLine = $CommandLine; CreationDate = $Created }
}
function Get-GodotCandidate { param([hashtable] $Map) @($Map.Values | Where-Object { $_.CommandLine -match '^PLANT' }) }
$t0 = [datetime]'2026-01-01T10:00:00'
# Rooted on the running platform: a drive path is unrooted on Linux, so its checkout would resolve to none.
$repoA, $repoB = if ($IsWindows) { 'C:\repoA', 'C:\repoB' } else { '/repoA', '/repoB' }
$attrib = @(
    @{ n = 'a parent editor is attributed to its child''s --path: another checkout sees none'; kids = @($repoB); ask = $repoA; want = 0 },
    @{ n = 'a parent editor is live on the checkout its child names (parent and child)'; kids = @($repoB); ask = $repoB; want = 2 },
    @{ n = 'a parent editor with no child --path still matches every checkout'; kids = @(); ask = $repoA; want = 1 }
)
$total = $cases.Count
foreach ($a in $attrib) {
    $map = @{}
    $map[10] = New-PlantedProc 10 1 'PLANT godot -e' $t0
    $i = 11
    foreach ($k in $a.kids) { $map[$i] = New-PlantedProc $i 10 "PLANT godot --path $k" $t0.AddSeconds(5); $i++ }
    $got = @(Get-LiveEditor -Checkout $a.ask -Map $map -CandidateRoots @($a.ask)).Count
    if ($got -eq $a.want) { Write-Host "ok   $($a.n)" } else { Write-Host "FAIL $($a.n) (got $got)"; $failed++ }
    $total++
}
Write-Host "`n$($total - $failed)/$total cases pass"

$real = Test-InteractiveSessionLocked
$lockFailed = 0
foreach ($planted in '0', '1') {
    $env:HARNESS_SESSION_LOCKED = $planted
    try { $got = Test-InteractiveSessionLocked } finally { Remove-Item Env:HARNESS_SESSION_LOCKED -ErrorAction SilentlyContinue }
    if ($got -eq $real) { Write-Host "ok   the real Test-InteractiveSessionLocked ignores HARNESS_SESSION_LOCKED=$planted" } else { Write-Host "FAIL the real Test-InteractiveSessionLocked honors HARNESS_SESSION_LOCKED=$planted (got $got, real $real)"; $lockFailed++ }
}
exit ([int](($failed + $lockFailed) -gt 0))
