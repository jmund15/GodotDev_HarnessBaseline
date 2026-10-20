# Dot-sourced by run_test_suite.ps1, verify.ps1 and regression_gate.ps1 before they do any work.
# A delegate (a `claude -p` sidecar child, which carries CLAUDE_CODE_SIDECAR) may not start a wide test run its
# brief does not name: hooks/wide_test_guard.py decides, and owns the rule (guards/author.md). The Bash hook
# cannot see inside a script a delegate wrote; this entry check can, because every route reaches these files.
# A missing Python reports the lost check and admits the run: the guard compensates for delegate behavior, it
# does not protect the checkout.
function Assert-WideRunAllowed {
    param(
        [Parameter(Mandatory)] [string] $Entry,
        [string] $Filter = '',
        [string[]] $Scope = @(),
        [string[]] $Flags = @(),
        [string] $Repo = ''
    )
    if (-not $env:CLAUDE_CODE_SIDECAR -or $env:HARNESS_WIDE_RUN_ADMITTED) { return }
    $guard = Join-Path $PSScriptRoot '..\hooks\wide_test_guard.py'
    $python = $null
    foreach ($candidate in @('python3', 'python', 'py')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { $python = $candidate; break }
    }
    if (-not $python -or -not (Test-Path $guard)) {
        Write-Host "WIDE_RUN_GUARD=SKIPPED reason=no python or hooks/wide_test_guard.py; the wide-run check did not run"
        return
    }
    $guardArgs = @($guard, '--entry', $Entry, '--flags', ($Flags -join ','))
    if ($Filter) { $guardArgs += @('--filter', $Filter) }
    if ($Repo) { $guardArgs += @('--repo', $Repo) }
    if ($Scope.Count -gt 0) { $guardArgs += @('--scope', (($Scope | ForEach-Object { $_ -split ',' }) -join ',')) }
    $said = & $python @guardArgs 2>&1
    if ($LASTEXITCODE -eq 2) {
        Write-Host ($said -join "`n")
        exit 2
    }
    $env:HARNESS_WIDE_RUN_ADMITTED = '1'
}
