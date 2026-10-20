# The static guard set shared by regression_gate.ps1 (before its build) and verify.ps1 (before its suites): one table,
# one runner, so a guard red surfaces at a Part close and not first at the drive-close gate.
function Get-PythonCommand {
    foreach ($c in @('python3', 'python', 'py')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}

# Older static guards derive REPO from __file__. Keep their code and policy in main, but point
# their input roots at the target; cwd-aware guards already see the target as the working directory.
$script:GuardRunner = @'
import runpy, sys
from pathlib import Path
script, target = sys.argv[1:]
sys.path.insert(0, str(Path(script).parent))
sys.argv = [script]
module = runpy.run_path(script)
entry = module['main']
globals_ = entry.__globals__
if 'REPO' in globals_:
    globals_['REPO'] = Path(target)
if 'TESTS_ROOT' in globals_:
    globals_['TESTS_ROOT'] = Path(target) / globals_.get('_TESTS_ROOT_REL', 'Tests')
sys.exit(entry())
'@

# Runs every guard against $Repo's files. Returns Results (`name=OK|FAIL|UNAVAILABLE`), Blocked (failed names),
# Details (markdown for each failure) and Unavailable ($true stops the run at the first missing guard).
function Invoke-StaticGuards {
    param([Parameter(Mandatory)] [string] $Repo, [Parameter(Mandatory)] [string] $Hooks, [Parameter(Mandatory)] [string] $Py)
    $guards = [ordered]@{
        nullstrip      = 'tres_nullstrip_guard.py'
        tool_cascade   = 'tool_cascade_audit.py'
        script_strip   = 'tres_script_strip_guard.py'
        gate_coverage  = 'test_suite_gate_coverage_guard.py'
        dup_double     = 'duplicate_test_double_guard.py'
        refcount_free  = 'refcounted_free_guard.py'
    }
    # Get-AdaptationValue's string contract carries this project table as a JSON object. IntegrationBatches.ps1 loads
    # batch_diagnosis.ps1, whose param block resets a caller's own switches (verify's $SelfTest), so it loads in its own scope.
    $table = & { . (Join-Path $PSScriptRoot 'IntegrationBatches.ps1'); Get-AdaptationValue -Repo $Repo -Key 'gate_guards' -Default '{}' }
    $projectGuards = $table | ConvertFrom-Json -ErrorAction Stop
    if ($projectGuards -isnot [System.Management.Automation.PSCustomObject]) { throw 'gate_guards must encode a JSON object' }
    foreach ($guard in $projectGuards.PSObject.Properties) {
        if ($guards.Contains($guard.Name)) { throw "gate_guards cannot replace generic guard $($guard.Name)" }
        if ($guard.Value -isnot [string] -or -not $guard.Value) { throw "gate_guards $($guard.Name) must name a guard" }
        $guards[$guard.Name] = $guard.Value
    }
    $out = [pscustomobject]@{ Results = @(); Blocked = @(); Details = @(); Unavailable = $false }
    Push-Location -LiteralPath $Repo
    try {
        foreach ($name in $guards.Keys) {
            $path = Join-Path $Hooks $guards[$name]
            if (-not (Test-Path $path)) {
                $out.Results += "$name=UNAVAILABLE"
                $out.Details += "Mandatory static coverage unavailable: $name ($path) is missing. Restore the guard before accepting coverage."
                $out.Unavailable = $true
                return $out
            }
            $text = & $Py '-c' $script:GuardRunner $path $Repo 2>&1
            $code = $LASTEXITCODE
            if ($code -eq 0) { $out.Results += "$name=OK"; continue }
            $out.Results += "$name=FAIL"
            $out.Blocked += $name
            $out.Details += "## Guard $name (exit $code)`n`n``````"
            $out.Details += (($text | Out-String).TrimEnd())
            $out.Details += '``````'
        }
    } finally { Pop-Location }
    return $out
}
