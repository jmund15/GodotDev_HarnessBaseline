#requires -Version 7
<#
.SYNOPSIS
  Re-runnable proof for scripts/run_integration_batched.ps1's `adaptation.json`
  `test_quarantine_filter` seam (Design Doc §8).

  Three shapes: adaptation.json absent -> the default (empty string, so `$quarantine` adds no
  filter); present with a valid string -> that value is read; a wrong-typed value (not a
  string) -> the default plus one stderr line. Also proves the wiring itself: the script's
  `$quarantine =` assignment actually calls `Get-AdaptationValue`, not a literal.

  `Get-AdaptationValue` is extracted verbatim from the real script (never retyped by hand) and
  run as a real child process per case, so this proof reads the actual stderr stream a
  consumer would see -- not `-ErrorVariable`, which also collects the engine's own internal
  (and already-caught) exception records alongside the function's one intentional message.

    pwsh -NoProfile -File .claude/tests/test_run_integration_batched_adaptation.ps1
#>

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$scriptPath = Join-Path $here '..\scripts\run_integration_batched.ps1'
$src = Get-Content $scriptPath -Raw

# --- wiring: $quarantine is assigned FROM Get-AdaptationValue, not a literal --------------
$wiringOk = $src -match [regex]::Escape('$quarantine = Get-AdaptationValue -Key ''test_quarantine_filter''')

# --- extract Get-AdaptationValue verbatim --------------------------------------------------
$funcMatch = [regex]::Match($src, '(?s)function Get-AdaptationValue \{.*?\n\}\r?\n')
if (-not $funcMatch.Success) {
    Write-Host "FAIL could not extract Get-AdaptationValue from the real script"
    exit 2
}
$funcText = $funcMatch.Value

function New-ScratchRepo {
    # $Seed is intentionally untyped: a `[string]` parameter coerces a passed $null into "",
    # which would create an empty (and therefore malformed) adaptation.json for the "absent
    # file" case instead of no file at all.
    param($Seed)
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ribp_repo_" + [System.Guid]::NewGuid().ToString('N').Substring(0,8))
    $skillDir = Join-Path $tmp '.claude\skills\project_subsystems'
    New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
    if ($null -ne $Seed) {
        Set-Content -Path (Join-Path $skillDir 'adaptation.json') -Value $Seed -NoNewline -Encoding utf8
    }
    return $tmp
}

function Invoke-InChildProcess {
    # Runs Get-AdaptationValue in a fresh pwsh process against $Repo, exactly as a consumer
    # would see it: stdout carries the value, stderr carries whatever the function itself
    # writes -- and nothing else.
    param([string] $Repo, [string] $Key, [string] $Default)
    $wrapper = Join-Path ([System.IO.Path]::GetTempPath()) ("ribp_wrap_" + [System.Guid]::NewGuid().ToString('N').Substring(0,8) + ".ps1")
    $body = @"
`$repo = '$Repo'
$funcText
Write-Output (Get-AdaptationValue -Key '$Key' -Default '$Default')
"@
    Set-Content -Path $wrapper -Value $body -Encoding utf8
    try {
        $stdout = & pwsh -NoProfile -File $wrapper 2>(Join-Path ([System.IO.Path]::GetTempPath()) "ribp_err.txt")
        $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) "ribp_err.txt"
        $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { '' }
        if (Test-Path $stderrPath) { Remove-Item -Force $stderrPath }
        return @{ stdout = ($stdout -join "`n").Trim(); stderr = if ($stderr) { $stderr.Trim() } else { '' } }
    } finally {
        Remove-Item -Force $wrapper -ErrorAction SilentlyContinue
    }
}

$cases = @()
$scratches = @()

try {
    # --- absent: default empty string, no stderr -------------------------------------------
    $repo = New-ScratchRepo -Seed $null
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'absent adaptation.json: default empty string'; ok = ($r.stdout -eq '') }
    $cases += @{ label = 'absent adaptation.json: no stderr'; ok = ($r.stderr -eq '') }

    # --- present: a valid string is read ---------------------------------------------------
    $repo = New-ScratchRepo -Seed '{"test_quarantine_filter": "FullyQualifiedName!~SomeSuite"}'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'present adaptation.json: project value is read'; ok = ($r.stdout -eq 'FullyQualifiedName!~SomeSuite') }
    $cases += @{ label = 'present adaptation.json: no stderr on a valid value'; ok = ($r.stderr -eq '') }

    # --- wrong-typed: not a string -> default + one stderr line -----------------------------
    $repo = New-ScratchRepo -Seed '{"test_quarantine_filter": ["not", "a", "string"]}'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'wrong-typed test_quarantine_filter: falls back to default'; ok = ($r.stdout -eq '') }
    $cases += @{ label = 'wrong-typed test_quarantine_filter: one stderr line names the key';
                 ok = (($r.stderr -split "`n").Count -eq 1 -and $r.stderr -match 'test_quarantine_filter') }

    # --- unparseable file: default + one stderr line ----------------------------------------
    $repo = New-ScratchRepo -Seed '{not json'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default 'D'
    $cases += @{ label = 'unparseable adaptation.json: falls back to default'; ok = ($r.stdout -eq 'D') }
    $cases += @{ label = 'unparseable adaptation.json: one stderr line'; ok = (($r.stderr -split "`n").Count -eq 1 -and $r.stderr -ne '') }

    $cases += @{ label = '$quarantine is wired to Get-AdaptationValue, not a literal'; ok = $wiringOk }
} finally {
    foreach ($r in $scratches) {
        if (Test-Path $r) { Remove-Item -Recurse -Force $r -Confirm:$false }
    }
}

$failures = @()
foreach ($c in $cases) {
    $mark = if ($c.ok) { 'ok' } else { 'FAIL' }
    Write-Host ("{0,-4} {1}" -f $mark, $c.label)
    if (-not $c.ok) { $failures += $c.label }
}

Write-Host ""
Write-Host ("{0}/{1} cases pass" -f ($cases.Count - $failures.Count), $cases.Count)
foreach ($f in $failures) { Write-Host "  FAIL $f" }

if ($failures.Count -gt 0) { exit 1 } else { exit 0 }
