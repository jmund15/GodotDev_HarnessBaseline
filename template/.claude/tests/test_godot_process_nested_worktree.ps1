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
Write-Host "`n$($cases.Count - $failed)/$($cases.Count) cases pass"
exit ([int]($failed -gt 0))
