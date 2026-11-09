#requires -Version 7
<#
.SYNOPSIS
  Proof for the gate's one verdict table (regression_gate.ps1 $script:VerdictRows): every verdict a gate exit
  prints a MEANING and a NEXT line, the module-missing exits print the same row, and -Help lists every flag and
  every verdict code the script exits with.

.HOW
  Part S reads the real script by AST: every literal Complete-Gate call and every module-missing exit. Part H runs
  the real script's -Help (it exits before any gate work). Part R extracts the real table and its two printers and
  runs them in a child pwsh, so the verdict rows print without a gate run. No build, Godot or test run starts.

.RUN
  pwsh -NoProfile -File .claude/tests/test_regression_gate_verdicts.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$gatePath = Join-Path $PSScriptRoot '..\scripts\regression_gate.ps1'
$gateAst = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $gatePath).Path, [ref]$null, [ref]$null)

function Assert-True($Name, $Condition, $Detail = '') {
    if (-not $Condition) { throw "FAIL $Name $Detail" }
    Write-Host "PASS $Name"
}

# ---- Part S: the call sites, read from the real AST
$completeCalls = @($gateAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
        $n.GetCommandName() -eq 'Complete-Gate' }, $true))
$literalPairs = @()
foreach ($c in $completeCalls) {
    $els = @($c.CommandElements | Select-Object -Skip 1)
    if ($els.Count -ge 2 -and $els[0] -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $els[1] -is [System.Management.Automation.Language.ConstantExpressionAst]) {
        $literalPairs += [pscustomobject]@{ Verdict = $els[0].Value; Code = [int]$els[1].Value }
    }
}
Assert-True 'Complete-Gate has literal call sites to check' ($literalPairs.Count -ge 10) "found=$($literalPairs.Count)"

$moduleExits = @($gateAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
        $n.GetCommandName() -eq 'Exit-ModuleMissing' }, $true))
Assert-True 'the six module-missing exits route through Exit-ModuleMissing' ($moduleExits.Count -eq 6) "found=$($moduleExits.Count)"

$rawBlocked = @($gateAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.StringConstantExpressionAst] -and
        $n.Value -like 'PREFLIGHT=BLOCKED reason=*.ps1-missing' }, $true))
Assert-True 'no module-missing exit still writes its PREFLIGHT line by hand' ($rawBlocked.Count -eq 0) "left=$($rawBlocked.Count)"

# ---- Part H: -Help on the real script
$help = & pwsh -NoProfile -File (Resolve-Path $gatePath).Path -Help 2>&1
$helpCode = $LASTEXITCODE
$helpText = ($help | Out-String)
Assert-True '-Help exits 0' ($helpCode -eq 0) "exit=$helpCode first=$(@($help)[0])"
foreach ($p in $literalPairs) {
    $needle = "$($p.Verdict)  *$($p.Code)\b|\b$($p.Verdict)\b.*\b$($p.Code)\b"
    Assert-True "-Help lists $($p.Verdict) exit $($p.Code)" ($helpText -match $needle) "missing row"
}
$paramNames = @($gateAst.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($name in $paramNames) {
    Assert-True "-Help names flag -$name" ($helpText -match "(?m)^\s*-$name\b") "missing flag"
}

# ---- Part R: the real table and printers, run in a child pwsh
$tableAst = $gateAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $n.Left.Extent.Text -eq '$script:VerdictRows' }, $true)
Assert-True 'the verdict table is one assignment' (@($tableAst).Count -eq 1)
$printerNames = 'Write-VerdictRow', 'Exit-ModuleMissing'
$fnTexts = foreach ($fn in $printerNames) {
    $f = @($gateAst.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fn }, $true))
    Assert-True "function $fn exists" ($f.Count -eq 1)
    $f[0].Extent.Text
}
$work = Join-Path ([IO.Path]::GetTempPath()) "verdict-proof-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $rowsPath = Join-Path $work 'rows.ps1'
    $body = @(
        $tableAst[0].Extent.Text,
        ($fnTexts -join "`n"),
        "foreach (`$v in @(`$script:VerdictRows.Keys)) { Write-VerdictRow -Verdict `$v }",
        "Exit-ModuleMissing 'RunPool.ps1'"
    ) -join "`n"
    Set-Content -Path $rowsPath -Value $body -Encoding utf8
    $rowsOut = & pwsh -NoProfile -File $rowsPath 2>&1
    $rowsCode = $LASTEXITCODE
    $rowsText = ($rowsOut | Out-String)
    Assert-True 'module-missing exit ends with exit 4' ($rowsCode -eq 4) "exit=$rowsCode out=$rowsText"
    Assert-True 'module-missing exit keeps its PREFLIGHT line' ($rowsText -match 'PREFLIGHT=BLOCKED reason=RunPool\.ps1-missing')
    $nextLines = @(($rowsText -split "`r?`n") | Where-Object { $_ -match '^NEXT=\S' })
    $meanLines = @(($rowsText -split "`r?`n") | Where-Object { $_ -match '^MEANING=\S' })
    $verdictCount = $script:VerdictRowCount = @([regex]::Matches($body, "^\s*'[A-Z_]+'\s*=\s*@\{", 'Multiline')).Count
    Assert-True 'every verdict row prints a NEXT line' ($nextLines.Count -eq $verdictCount + 1) "next=$($nextLines.Count) rows=$verdictCount"
    Assert-True 'every verdict row prints a MEANING line' ($meanLines.Count -eq $verdictCount + 1) "meaning=$($meanLines.Count)"
} finally { Remove-Item -LiteralPath $work -Recurse -Force }

Write-Host 'ALL PASS'
