function Invoke-Isolated {
    # Runs one script file in a fresh runspace of this pwsh and returns rc, out and err as a `pwsh -File` child would.
    # The case gets its own `$script:` and `$global:` state and functions. The process shares the compiled .NET types,
    # the environment and the working directory: the environment is restored after the case, and a case names every
    # path absolute. `exit N` gives N wherever it runs, a normal end gives 0 whatever code a native command left in
    # $LASTEXITCODE, and a terminating error gives 1. Write-Host, verbose and warning text joins stdout. Errors and
    # [Console]::Error writes join stderr.
    param([Parameter(Mandatory)] [string] $File, [hashtable] $Environment = @{}, [object[]] $ArgumentList = @())
    $saved = [Environment]::GetEnvironmentVariables()
    foreach ($name in $Environment.Keys) { [Environment]::SetEnvironmentVariable($name, [string]$Environment[$name]) }
    $ps = [powershell]::Create()
    $output = [Management.Automation.PSDataCollection[psobject]]::new()
    $rc = 0; $err = @()
    $oldErr = [Console]::Error; $consoleErr = [IO.StringWriter]::new(); [Console]::SetError($consoleErr)
    try {
        # After `&`, `$?` is false only when the script ran `exit` with a non-zero code.
        $wrapper = 'param($__isolatedFile, $__isolatedArgs) & $__isolatedFile @__isolatedArgs; $global:__IsolatedEnd = $?, $LASTEXITCODE'
        [void]$ps.AddScript($wrapper, $true)
        [void]$ps.AddArgument($File).AddArgument($ArgumentList)
        $ps.Invoke($null, $output)
        $end = $ps.Runspace.SessionStateProxy.GetVariable('__IsolatedEnd')
        if (-not $end[0]) { $rc = [int]$end[1] }
    } catch {
        $rc = 1; $err += [string]$_.Exception.GetBaseException().Message
    } finally {
        [Console]::SetError($oldErr)
        foreach ($name in @([Environment]::GetEnvironmentVariables().Keys)) {
            if (-not $saved.Contains($name)) { [Environment]::SetEnvironmentVariable($name, $null) }
        }
        foreach ($name in @($saved.Keys)) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
    }
    if ($consoleErr.ToString()) { $err += $consoleErr.ToString() }
    $err += @($ps.Streams.Error | ForEach-Object { [string]$_ })
    $hostLines = @(@($ps.Streams.Information) + @($ps.Streams.Verbose) + @($ps.Streams.Warning) | ForEach-Object { [string]$_ })
    $out = (@(@($output) | Out-String -Stream) + $hostLines | Where-Object { $_ }) -join "`n"
    $ps.Dispose()
    [pscustomobject]@{ rc = $rc; out = $out; err = ($err -join "`n") }
}
