<#
.SYNOPSIS
  The gate's engine-ERROR profile: counts Godot ERROR lines per normalized signature and compares them with
  Tests/regression_baseline.json `engine_errors`.

.DESCRIPTION
  Godot ERROR lines that are not test failures fail no suite, so a gate can pass with thousands of them. The
  profile makes growth visible. It is advisory: the verdict never depends on it.

  Noise floor 10 drops signatures seen fewer times, which are one-run noise. The baseline is the max over each
  signature's counts from the last 5 green runs (an absent run counts 0), so run-to-run variance stays inside
  it. A signature leaves the baseline after 5 consecutive green runs without it. Advisory until more runs bound them.

  Dot-source for the functions. regression_gate.ps1 calls them on a full run only: a -RetryOnly run restores
  prior greens whose logs this run never produced.
#>

$script:EngineErrorFloor = 10
$script:EngineErrorWindow = 5
$script:EngineErrorLine = '^(?:ERROR|SCRIPT ERROR|USER ERROR):'

function ConvertTo-EngineErrorSignature {
    param([Parameter(Mandatory)] [string] $Line)
    $s = $Line.Trim()
    $s = [regex]::Replace($s, '[A-Za-z]:\\[^\s'']+', '<path>')
    $s = [regex]::Replace($s, 'user://\S+', 'user://<path>')
    $s = [regex]::Replace($s, '0x[0-9a-fA-F]+', 'N')
    $s = [regex]::Replace($s, '\d+', 'N')
    if ($s.Length -gt 120) { $s = $s.Substring(0, 120) }
    $s
}

function Get-EngineErrorProfile {
    param([Parameter(Mandatory)] [string] $RunDir)
    $all = @{}
    $total = 0
    foreach ($f in @(Get-ChildItem -LiteralPath $RunDir -Recurse -Filter 'godot_test.log' -File -ErrorAction SilentlyContinue)) {
        foreach ($line in [System.IO.File]::ReadLines($f.FullName)) {
            if ($line.Length -lt 6 -or $line[0] -notin 'E', 'S', 'U') { continue }
            if ($line -notmatch $script:EngineErrorLine) { continue }
            $total++
            $sig = ConvertTo-EngineErrorSignature -Line $line
            $all[$sig] = 1 + $(if ($all.ContainsKey($sig)) { $all[$sig] } else { 0 })
        }
    }
    $kept = [System.Collections.Specialized.OrderedDictionary]::new()
    foreach ($k in ($all.Keys | Where-Object { $all[$_] -ge $script:EngineErrorFloor } | Sort-Object { -$all[$_] }, { $_ })) { $kept[$k] = $all[$k] }
    [pscustomobject]@{ Total = $total; Signatures = $kept }
}

function Compare-EngineErrors {
    param([Parameter(Mandatory)] $Profile, $Baseline)
    $baseSigs = if ($Baseline) { $Baseline.PSObject.Properties['signatures'] } else { $null }
    if (-not $baseSigs) {
        return [pscustomobject]@{ State = 'no-baseline'; New = @(); Grown = @(); Total = $Profile.Total; BaselineTotal = $null }
    }
    $known = $baseSigs.Value.PSObject.Properties
    $new = @(); $grown = @()
    foreach ($sig in $Profile.Signatures.Keys) {
        $count = [int]$Profile.Signatures[$sig]
        $prop = $known[$sig]
        if (-not $prop) { $new += [pscustomobject]@{ Signature = $sig; Count = $count }; continue }
        $was = [int]$prop.Value
        if ($count -gt $was) { $grown += [pscustomobject]@{ Signature = $sig; Count = $count; Baseline = $was } }
    }
    $bt = $Baseline.PSObject.Properties['total']
    [pscustomobject]@{ State = 'ok'; New = $new; Grown = $grown; Total = $Profile.Total; BaselineTotal = $(if ($bt) { [int]$bt.Value } else { $null }) }
}

function ConvertTo-JsonString {
    param([string] $Text)
    $e = $Text.Replace('\', '\\').Replace('"', '\"').Replace("`t", '\t').Replace("`r", '\r').Replace("`n", '\n')
    '"' + $e + '"'
}

# Index one past the '}' closing the object whose '{' is at $Open, skipping braces inside strings.
function Find-ObjectEnd {
    param([string] $Text, [int] $Open)
    $depth = 0; $inString = $false
    for ($i = $Open; $i -lt $Text.Length; $i++) {
        $c = $Text[$i]
        if ($inString) {
            if ($c -eq '\') { $i++ } elseif ($c -eq '"') { $inString = $false }
        } elseif ($c -eq '"') { $inString = $true }
        elseif ($c -eq '{') { $depth++ }
        elseif ($c -eq '}') { $depth--; if ($depth -eq 0) { return $i + 1 } }
    }
    -1
}

# Previous counts for one signature, newest last: the stored window, or the one count of a min-style block.
function Get-EngineErrorWindow {
    param($Previous, [string] $Sig)
    if ($Previous -and $Previous.PSObject.Properties['runs']) {
        $runs = $Previous.runs.PSObject.Properties[$Sig]
        if ($runs) { return @($runs.Value | ForEach-Object { [int]$_ }) }
    }
    if ($Previous -and $Previous.PSObject.Properties['signatures']) {
        $prev = $Previous.signatures.PSObject.Properties[$Sig]
        if ($prev) { return @([int]$prev.Value) }
    }
    @()
}

# Targeted text surgery, not a JSON round-trip: the tracked baseline carries mojibake that ConvertTo-Json would re-encode.
# Each stored count is the max of the last 5 green runs; a signature absent from a run counts 0 for it.
function Set-EngineErrorsText {
    param([Parameter(Mandatory)] [string] $Raw, [Parameter(Mandatory)] $Profile, $Previous)
    $eol = if ($Raw.Contains("`r`n")) { "`r`n" } else { "`n" }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($sig in $Profile.Signatures.Keys) { $names.Add($sig) }
    if ($Previous -and $Previous.PSObject.Properties['signatures']) {
        foreach ($prop in $Previous.signatures.PSObject.Properties) { if (-not $names.Contains($prop.Name)) { $names.Add($prop.Name) } }
    }
    $rows = foreach ($sig in $names) {
        $now = if ($Profile.Signatures.Contains($sig)) { [int]$Profile.Signatures[$sig] } else { 0 }
        $window = @(@(Get-EngineErrorWindow -Previous $Previous -Sig $sig) + $now | Select-Object -Last $script:EngineErrorWindow)
        [pscustomobject]@{ Sig = $sig; Runs = $window; N = [int]($window | Measure-Object -Maximum).Maximum }
    }
    $rows = @($rows | Where-Object { $_.N -gt 0 })

    $totals = @()
    if ($Previous -and $Previous.PSObject.Properties['total_runs']) { $totals = @($Previous.total_runs | ForEach-Object { [int]$_ }) }
    elseif ($Previous -and $Previous.PSObject.Properties['total']) { $totals = @([int]$Previous.total) }
    $totals = @(@($totals + [int]$Profile.Total) | Select-Object -Last $script:EngineErrorWindow)
    $total = [int]($totals | Measure-Object -Maximum).Maximum

    $lines = @('  "engine_errors": {',
        ('    "notes": "Godot ERROR lines per normalized signature (signatures seen {0} times or more) from the last clean full green gate. The gate reports growth past the max of the last {1} green runs and never fails on it. A signature leaves after {1} green runs without it.",' -f $script:EngineErrorFloor, $script:EngineErrorWindow),
        "    `"total`": $total,",
        ('    "total_runs": [{0}],' -f ($totals -join ', ')),
        '    "signatures": {')
    $sigLines = @($rows | Sort-Object @{ Expression = 'N'; Descending = $true }, 'Sig' | ForEach-Object { '      ' + (ConvertTo-JsonString $_.Sig) + ': ' + $_.N })
    for ($i = 0; $i -lt $sigLines.Count; $i++) { $lines += $sigLines[$i] + $(if ($i -lt $sigLines.Count - 1) { ',' } else { '' }) }
    $lines += '    },'
    $lines += '    "runs": {'
    $runLines = @($rows | Sort-Object @{ Expression = 'N'; Descending = $true }, 'Sig' | ForEach-Object { '      ' + (ConvertTo-JsonString $_.Sig) + ': [' + ($_.Runs -join ', ') + ']' })
    for ($i = 0; $i -lt $runLines.Count; $i++) { $lines += $runLines[$i] + $(if ($i -lt $runLines.Count - 1) { ',' } else { '' }) }
    $lines += '    }'
    $lines += '  }'
    $block = $lines -join $eol

    $key = [regex]::Match($Raw, '"engine_errors"\s*:\s*\{')
    if ($key.Success) {
        $end = Find-ObjectEnd -Text $Raw -Open ($key.Index + $key.Length - 1)
        if ($end -lt 0) { throw 'engine_errors block is not closed' }
        $lineStart = $Raw.LastIndexOf("`n", $key.Index) + 1
        return $Raw.Substring(0, $lineStart) + $block + $Raw.Substring($end)
    }
    $close = $Raw.LastIndexOf('}')
    if ($close -lt 0) { throw 'baseline has no closing brace' }
    $Raw.Substring(0, $close).TrimEnd() + ',' + $eol + $block + $eol + '}' + $Raw.Substring($close + 1)
}
