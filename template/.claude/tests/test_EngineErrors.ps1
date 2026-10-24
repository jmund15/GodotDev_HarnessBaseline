<#
.SYNOPSIS
  Proof for the gate's engine-ERROR profile (.claude/scripts/EngineErrors.ps1).

.WHY
  A green gate passed with hundreds of Godot ERROR lines that no suite reads. The profile counts those lines per
  normalized signature so growth shows in the verdict. Fixture lines follow the shapes in a real gate run's
  godot_test.log (gate-20261022-190352-55492), with project names made generic.

.RUN
  pwsh -NoProfile -File .claude/tests/test_EngineErrors.ps1
  Exit 0 = all pass, 1 = at least one failure.
#>

[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\EngineErrors.ps1')

$script:Fails = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "  PASS  $Name" }
    else { Write-Host "  FAIL  $Name $Detail"; $script:Fails++ }
}

$root = Join-Path ([System.IO.Path]::GetTempPath()) "gateengineerr-test-$PID"
function Write-Log {
    param([string] $Batch, [string[]] $Lines)
    $d = Join-Path $root $Batch
    New-Item -ItemType Directory -Force -Path $d | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $d 'godot_test.log'), (($Lines -join "`n") + "`n"))
}
function Rep { param([string] $Line, [int] $N) 1..$N | ForEach-Object { $Line } }
function New-Profile { param([hashtable] $Counts) $o = [ordered]@{}; foreach ($k in $Counts.Keys) { $o[$k] = $Counts[$k] }; [pscustomobject]@{ Total = 0; Signatures = $o } }

try {
    if (Test-Path $root) { Remove-Item -Recurse -Force $root }
    $tile = 'ERROR: TileSet has no layer with name: material_id'
    $disp = 'ERROR: System.ObjectDisposedException: Cannot access a disposed object.'
    Write-Log 'Integration_B1' (@(Rep $tile 40) + @(Rep $disp 12) + @(
        'WARNING: No Target found (explicit field null, auto-discovery empty). Effect disabled.',
        '   at: push_error (core/variant/variant.cpp:1)',
        'ERROR: Poses disabled - no Target found under ''@Node@4521''. @ Widget.cs:88 in Resolve()',
        'SCRIPT ERROR: Invalid call. Nonexistent function ''Foo'' in base ''Node (Bar)''.'))
    Write-Log 'Integration_B2' (@(Rep $tile 25) + @(
        'ERROR: ConfigFile parse error at user://tests/a1b2c3d4e5f60718/run_suspend.tres:7.',
        'ERROR: [Report] perf C:\Users\someone\AppData\Local\Temp\run_8f3a91c2\out.json missing'))
    Write-Log 'Sanity' @('plain output line', 'USER ERROR: Something the user triggered.')

    $p = Get-EngineErrorProfile -RunDir $root
    Assert-True 'total counts every ERROR / SCRIPT ERROR / USER ERROR line, no WARNING' ($p.Total -eq 40 + 12 + 1 + 1 + 25 + 1 + 1 + 1) "total=$($p.Total)"
    Assert-True 'a signature counts across batches' ($p.Signatures[$tile] -eq 65) "got=$($p.Signatures[$tile])"
    Assert-True 'a repeated line below the noise floor stays out of Signatures' (@($p.Signatures.Keys | Where-Object { $_ -like '*Nonexistent*' }).Count -eq 0)
    Assert-True 'a signature at the floor stays in' ($p.Signatures[$disp] -eq 12)
    Assert-True 'digits and node ids collapse into one signature' (@($p.Signatures.Keys | Where-Object { $_ -match '\d' }).Count -eq 0)
    Assert-True 'a temp path and a user:// hash do not make a new signature each run' (@($p.Signatures.Keys | Where-Object { $_ -match 'a1b2c3|8f3a91' }).Count -eq 0)

    # Compare: new signature, growth past the 5-run max, absent baseline.
    $base = [pscustomobject]@{ total = 100; signatures = [pscustomobject]@{ $tile = 65; $disp = 12 } }
    $c = Compare-EngineErrors -Profile $p -Baseline $base
    Assert-True 'state ok with a baseline' ($c.State -eq 'ok')
    Assert-True 'a count within the 5-run max is not reported' (@($c.Grown).Count -eq 0) "grown=$(@($c.Grown) -join ',')"
    $base2 = [pscustomobject]@{ total = 100; signatures = [pscustomobject]@{ $tile = 5; $disp = 12 } }
    $c2 = Compare-EngineErrors -Profile $p -Baseline $base2
    Assert-True 'a count above the 5-run max is reported grown' (@($c2.Grown).Count -eq 1 -and $c2.Grown[0].Signature -eq $tile) "grown=$(@($c2.Grown).Count)"
    $base3 = [pscustomobject]@{ total = 10; signatures = [pscustomobject]@{ $disp = 12 } }
    $c3 = Compare-EngineErrors -Profile $p -Baseline $base3
    Assert-True 'a signature at the floor and absent from the baseline is new' (@($c3.New).Count -eq 1 -and $c3.New[0].Signature -eq $tile)
    $c4 = Compare-EngineErrors -Profile $p -Baseline $null
    Assert-True 'no baseline block reports state no-baseline and nothing new' ($c4.State -eq 'no-baseline' -and @($c4.New).Count -eq 0)

    # Ratchet text surgery: other bytes stay, the block parses, values only fall.
    $moji = 'mojibake ' + [char]0xE2 + [char]0x20AC + [char]0x2014 + ' kept'
    $raw = "{`r`n  `"schema_version`": 1,`r`n  `"description`": `"$moji`",`r`n  `"suites`": { `"Logic`": { `"passed`": 5 } }`r`n}`r`n"
    $added = Set-EngineErrorsText -Raw $raw -Profile $p -Previous $null
    Assert-True 'a block is added to a file without one' ($added -match '"engine_errors"')
    $parsed = $added | ConvertFrom-Json
    Assert-True 'the edited text still parses' ($parsed.engine_errors.signatures.$tile -eq 65)
    Assert-True 'the rest of the file is untouched' ($added.StartsWith($raw.TrimEnd().TrimEnd('}').TrimEnd()) -and $added.Contains($moji))
    $lower = Get-EngineErrorProfile -RunDir $root
    $lower.Signatures[$tile] = 30
    $again = Set-EngineErrorsText -Raw $added -Profile $lower -Previous $parsed.engine_errors
    Assert-True 'a lower observation keeps the 5-run max' (($again | ConvertFrom-Json).engine_errors.signatures.$tile -eq 65)
    $higher = Get-EngineErrorProfile -RunDir $root
    $higher.Signatures[$tile] = 90
    $kept = Set-EngineErrorsText -Raw $again -Profile $higher -Previous ($again | ConvertFrom-Json).engine_errors
    Assert-True 'a higher observation raises the max' (($kept | ConvertFrom-Json).engine_errors.signatures.$tile -eq 90)
    Assert-True 'only one engine_errors block remains after rewriting' (([regex]::Matches($kept, '"engine_errors"')).Count -eq 1)

    # Five-run window: an absent run counts as 0; a signature drops after 5 runs of 0.
    $w1 = Set-EngineErrorsText -Raw $raw -Profile (New-Profile @{ $tile = 65 }) -Previous $null
    $wp = ($w1 | ConvertFrom-Json).engine_errors
    $gone = $w1
    for ($i = 1; $i -le 4; $i++) { $gone = Set-EngineErrorsText -Raw $gone -Profile (New-Profile @{}) -Previous $wp; $wp = ($gone | ConvertFrom-Json).engine_errors }
    Assert-True 'a signature absent 4 runs stays' ($wp.signatures.PSObject.Properties[$tile] -ne $null -and $wp.signatures.$tile -eq 65)
    $gone = Set-EngineErrorsText -Raw $gone -Profile (New-Profile @{}) -Previous $wp
    Assert-True 'a signature absent 5 runs drops' (($gone | ConvertFrom-Json).engine_errors.signatures.PSObject.Properties[$tile] -eq $null)

    # A min-style block from before the window: its count becomes the first window entry.
    $legacyRaw = "{`r`n  `"engine_errors`": {`r`n    `"total`": 50,`r`n    `"signatures`": { `"$tile`": 30 }`r`n  }`r`n}`r`n"
    $legacyPrev = [pscustomobject]@{ total = 50; signatures = [pscustomobject]@{ $tile = 30 } }
    $mig = Set-EngineErrorsText -Raw $legacyRaw -Profile (New-Profile @{ $tile = 40 }) -Previous $legacyPrev
    Assert-True 'a min-style block migrates to the window without error' (($mig | ConvertFrom-Json).engine_errors.signatures.$tile -eq 40)
    $brace = Get-EngineErrorProfile -RunDir $root
    $brace.Signatures['ERROR: a } brace { in a signature'] = 11
    $b2 = Set-EngineErrorsText -Raw $kept -Profile $brace -Previous ($kept | ConvertFrom-Json).engine_errors
    Assert-True 'a brace inside a signature does not break the block replacement' (($b2 | ConvertFrom-Json).engine_errors.signatures.'ERROR: a } brace { in a signature' -eq 11)
}
finally {
    if (Test-Path $root) { Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue }
}

if ($script:Fails -gt 0) { Write-Host "$($script:Fails) FAILED"; exit 1 }
Write-Host 'all pass'
exit 0
