<# Executed J11 contracts; AST extraction avoids launching the real gate or taking machine slots. #>
[CmdletBinding()]
param([ValidateSet('tiers', 'guards', 'advice', 'all')][string] $Case = 'all')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$scripts = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts'
$gate = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $scripts 'regression_gate.ps1'), [ref]$null, [ref]$null)
function Import-Function($Ast, $Name) {
    $f = @($Ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }.GetNewClosure(), $true))
    if ($f.Count -ne 1) { throw "expected one function: $Name" }
    . ([scriptblock]::Create($f[0].Extent.Text))
    # Export from this helper's scope to the proof's scope.
    Set-Item "Function:script:$Name" (Get-Item "Function:$Name").ScriptBlock
}
function Assert-True($Name, $Condition) {
    if (-not $Condition) { throw "FAIL $Name" }
    Write-Host "PASS $Name"
}
function Select-Statement($Ast, $Needle) {
    $s = @($Ast.FindAll({ param($n)
        ($n -is [System.Management.Automation.Language.IfStatementAst] -or
         ($Needle.StartsWith('$logicRetry') -and $n -is [System.Management.Automation.Language.AssignmentStatementAst])) -and $n.Extent.Text.Contains($Needle)
    }.GetNewClosure(), $true) | Sort-Object { $_.Extent.Text.Length })
    if (-not $s.Count) { throw "missing statement: $Needle" }
    [scriptblock]::Create($s[0].Extent.Text)
}
$root = Join-Path ([IO.Path]::GetTempPath()) "conformance-gate-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $root | Out-Null
try {
    if ($Case -in @('tiers', 'all')) {
        Import-Function $gate 'Test-SuiteTiers'
        $baseline = @{ suites = @{ Sanity = @{passed=100}; Logic = @{passed=100} }; silent_skip_sentinels = @{Sanity_min=20; Logic_min=20}; drift_thresholds = @{warn_ratio=.95; hard_fail_ratio=.8} }
        $script:IncompleteStatuses = @('NO_OUTPUT')
        foreach ($label in 'Sanity', 'Logic') {
            $r = @{passed=3; failed=1; silentSkip=$false; status='DONE'; outPath='fixture'}
            $t = Test-SuiteTiers $r $label
            Assert-True "$label low-pass real failure is FAILING" ($t.tier -eq 'FAILING')
            $r.silentSkip = $true
            Assert-True "$label parsed failure outranks silent-skip retry" ((Test-SuiteTiers $r $label).tier -eq 'FAILING')
            $r.failed = 0; $r.silentSkip = $false
            Assert-True "$label clean below-floor remains INVALID" ((Test-SuiteTiers $r $label).tier -eq 'INVALID')
            $r.passed = -1
            Assert-True "$label unparsed remains UNPARSED" ((Test-SuiteTiers $r $label).tier -eq 'UNPARSED')
        }
        # Execute the real Sanity retry statements with a potential green second executor.
        function Assert-NoEditor { param($Phase) }
        function Get-SuitePeers { @() }
        Import-Function $gate 'Get-FailedMessages'
        Import-Function $gate 'ConvertTo-SuiteResult'
        function Invoke-GatePool {
            param($Units, $Phase, [switch]$RecompilePass)
            $script:Calls++
            $script:PoolResults = @(@{label='Sanity'; counted=$true; elapsedSec=0; exit=$(if ($script:Calls -eq 1) {1} else {0}); passed=$(if ($script:Calls -eq 1) {3} else {100}); failed=$(if ($script:Calls -eq 1) {1} else {0}); status='DONE'; outPath='fixture'; failedTests=@('low-pass-red'); failedMessages=@()})
        }
        function Test-RecompileEvidence($R) { $false }
        function Set-GateRecompiled { $script:Recompiled=$true }
        function Emit($Text) { }
        $script:Calls=0; $script:Recompiled=$false; $suiteTimeoutMs=1
        $start = @($gate.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | Where-Object { $_.Extent.Text -eq "Assert-NoEditor -Phase 'suite:Sanity'" })[0].Extent.StartOffset
        $end = @($gate.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | Where-Object { $_.Extent.Text.StartsWith('Assert-NotContention -R $r') })[0].Extent.StartOffset
        . ([scriptblock]::Create($gate.Extent.Text.Substring($start, $end-$start)))
        Assert-True 'real Sanity retry path retains RED without calling green executor' ($script:Calls -eq 1 -and $r.failed -eq 1 -and $t.tier -eq 'FAILING')
        $r = @{status='DONE'}; $t = @{tier='FAILING'}
        . (Select-Statement $gate '$logicRetry = (')
        Assert-True 'real Logic retry selector does not retry FAILING' (-not $logicRetry)
    }
    if ($Case -in @('guards', 'all')) {
        $hooks = Join-Path $root 'hooks'; New-Item -ItemType Directory $hooks | Out-Null
        . (Join-Path $PSScriptRoot '..\scripts\StaticGuards.ps1')
        $run = Invoke-StaticGuards -Repo $root -Hooks $hooks -Py 'unused'
        Assert-True 'missing mandatory cascade stops as unavailable' ($run.Unavailable -and ($run.Results -contains 'nullstrip=UNAVAILABLE' -or $run.Results -contains 'tool_cascade=UNAVAILABLE') -and ($run.Details -join ' ') -match 'unavailable')
        foreach ($f in @('tres_nullstrip_guard.py', 'tool_cascade_audit.py', 'tres_script_strip_guard.py', 'test_suite_gate_coverage_guard.py', 'duplicate_test_double_guard.py', 'refcounted_free_guard.py')) { Set-Content (Join-Path $hooks $f) 'fixture' }
        function Invoke-GuardStub { $global:LASTEXITCODE=0; 'guard executed' }
        $run = Invoke-StaticGuards -Repo $root -Hooks $hooks -Py 'Invoke-GuardStub'
        Assert-True 'present successful mandatory guard remains OK' (-not $run.Unavailable -and $run.Blocked.Count -eq 0 -and $run.Results -contains 'tool_cascade=OK')
    }
    if ($Case -in @('advice', 'all')) {
        $script:Detail=@(); $script:Verdict=''; $script:Code=-1
        function Add-Detail($Text) { $script:Detail += $Text }
        function Complete-Gate($Verdict, $Code) { $script:Verdict=$Verdict; $script:Code=$Code; throw 'fixture-complete' }
        $anyFailed=@('Logic'); $tiers=@('FAILING')
        try { . (Select-Statement $gate 'if ($anyFailed.Count -gt 0') } catch { if ($_.Exception.Message -ne 'fixture-complete') { throw } }
        $text=$script:Detail -join ' '
        Assert-True 'FAIL remediation uses targeted Filter and canonical cadence' ($script:Verdict -eq 'FAIL' -and $script:Code -eq 1 -and $text -match '-Filter' -and $text -match 'change_control' -and $text -notmatch 'ALL suites')
        Assert-True 'FAIL remediation names the command that owns failure handling' ($text -match 'regression_gate\.md §Failure handling' -and $text -notmatch 'AskUserQuestion|NEVER auto-fix')
    }
    Write-Host 'ALL PASS'
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
