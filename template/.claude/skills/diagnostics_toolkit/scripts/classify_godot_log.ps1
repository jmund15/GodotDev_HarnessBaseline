#requires -Version 7
<#
.SYNOPSIS
  Read-only godot.log classifier. Prints: provenance verdict (test-run exhaust vs
  live/editor session), exit-time leak-dump accounting, and top ERROR/WARNING
  signatures (digits normalized). Never writes anything.

.WHY
  Step 0 of any log question: headline error counts are test-harness noise when the
  log is a GdUnit4 run. The exit-time ObjectDB leak dump prints one ERROR per leaked
  out-of-tree Node, so raw error counts scale with orphan volume, not bug frequency.
  Doctrine: diagnostics_toolkit SKILL.md §1. Since the parallel-test-runner-unlock,
  test-spawned Godot writes to `<worktree>/TestResults/godot_test.log` (`.runsettings`
  `--log-file`), or under `TestResults/runs/<runId>/<safe>/` for a `-ResultsRoot` run.
  USERDIR selects the agent run's log. Without it, default resolution prefers the newest test log,
  then the owner's live/editor log.

.EXAMPLE
  pwsh -NoProfile -File .claude/skills/diagnostics_toolkit/scripts/classify_godot_log.ps1
  pwsh -NoProfile -File .claude/skills/diagnostics_toolkit/scripts/classify_godot_log.ps1 -LogPath "<dir>\godot2026-07-03T17.04.06.log" -Top 5
#>
[CmdletBinding()]
param(
    # Defaults to USERDIR/logs/godot.log when a run supplies USERDIR, else the newest test log,
    # else the owner's live log. -LogPath overrides every default.
    [string] $LogPath = $(
        $testLog = @(Get-Item -LiteralPath (Join-Path (Get-Location) 'TestResults\godot_test.log') -ErrorAction SilentlyContinue) +
                   @(Get-ChildItem -LiteralPath (Join-Path (Get-Location) 'TestResults\runs') -Recurse -File -Filter 'godot_test.log' -ErrorAction SilentlyContinue) |
                   Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($env:USERDIR) { Join-Path $env:USERDIR 'logs/godot.log' }
        elseif ($testLog) { $testLog.FullName }
        else { Join-Path $env:APPDATA 'Godot\app_userdata\{{PROJECT_NAME}}\logs\godot.log' }
    ),
    [int] $Top = 8
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $LogPath)) { Write-Error "log not found: $LogPath"; exit 2 }

$item   = Get-Item $LogPath
$ageMin = [math]::Round(((Get-Date) - $item.LastWriteTime).TotalMinutes, 1)
$sizeMb = [math]::Round($item.Length / 1MB, 1)

# --- Provenance: adapter markers load near startup; head scan first, full-file fallback ---
$markers = 'gdunit4_testadapter|GdUnit4TestRunnerScene|GodotGdUnit4RestApi'
$isTest  = [bool]((Get-Content $LogPath -TotalCount 800) | Select-String -Pattern $markers -Quiet)
if (-not $isTest) { $isTest = [bool](Select-String -Path $LogPath -Pattern $markers -Quiet) }

# --- Single streaming pass: leak-dump accounting + severity signature histograms ---
$errSig = @{}; $warnSig = @{}
$leaked = 0; $leakDep = 0; $cannotPath = 0; $objDb = $false; $lines = 0
foreach ($chunk in (Get-Content $LogPath -ReadCount 4000)) {
    foreach ($line in $chunk) {
        $lines++
        if ($line.StartsWith('Leaked instance:')) { $leaked++; continue }
        if ($line.StartsWith('ERROR:')) {
            $msg = $line.Substring(6).Trim()
            if ($msg.StartsWith('Cannot get path of node')) { $cannotPath++ }
            $key = ($msg -replace '\d+', '#')
            if ($key.Length -gt 110) { $key = $key.Substring(0, 110) }
            $errSig[$key] = 1 + ($errSig[$key] ?? 0)
            continue
        }
        if ($line.StartsWith('WARNING:')) {
            $msg = $line.Substring(8).Trim()
            if ($msg.StartsWith('Leaked instance dependency')) { $leakDep++ }
            elseif ($msg.StartsWith('ObjectDB instances leaked at exit')) { $objDb = $true }
            $key = ($msg -replace '\d+', '#')
            if ($key.Length -gt 110) { $key = $key.Substring(0, 110) }
            $warnSig[$key] = 1 + ($warnSig[$key] ?? 0)
        }
    }
}

function Write-TopSignatures {
    param([hashtable] $Table, [string] $Title)
    Write-Output "Top $Title signatures (digits -> #):"
    if ($Table.Count -eq 0) { Write-Output '  (none)'; return }
    $Table.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First $Top | ForEach-Object {
        $note = ''
        if ($_.Key.StartsWith('Cannot get path of node') -or
            $_.Key -match 'leaked at exit|Leaked instance dependency|RIDs of type') { $note = '  [leak-dump artifact]' }
        Write-Output ('  {0,7}x  {1}{2}' -f $_.Value, $_.Key, $note)
    }
}

Write-Output '== godot.log classifier (read-only) =='
Write-Output "File     : $LogPath"
Write-Output "           $sizeMb MB / $lines lines / mtime $($item.LastWriteTime) ($ageMin min ago)"
if ($isTest) {
    Write-Output 'Verdict  : TEST-RUN EXHAUST — GdUnit4 adapter markers present. Error/warning counts'
    Write-Output '           are test-harness noise until proven otherwise; do NOT read as live-gameplay health.'
} else {
    Write-Output 'Verdict  : LIVE/EDITOR SESSION — no GdUnit4 adapter markers found in this file.'
}
if ($ageMin -lt 10) {
    Write-Output 'Mid-write: mtime < 10 min — sessions share this working tree; the run may belong to'
    Write-Output '           ANOTHER session (memory: feedback_concurrent_session_shared_tree.md).'
}
Write-Output ("Leak dump: exit dump present={0}; leaked instances={1} (out-of-tree Nodes={2}); dependency warnings={3}" -f $objDb, $leaked, $cannotPath, $leakDep)
Write-Output '           One ERROR per out-of-tree leaked Node -> error volume scales with orphans, not bugs.'
Write-TopSignatures -Table $errSig  -Title 'ERROR'
Write-TopSignatures -Table $warnSig -Title 'WARNING'
Write-Output 'Block-accurate analysis: python3 .claude/hooks/analyze_godot_logs.py --json --mode summary'
