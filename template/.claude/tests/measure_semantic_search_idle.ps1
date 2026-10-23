# Private-bytes proof for the semantic-search idle unload.
#
#   pwsh -NoProfile -File .claude/tests/measure_semantic_search_idle.ps1 `
#        -Root <.claude/scratch/... isolated working root> -Query "<fixed query>" `
#        -IdleMinutes 10 -Out .claude/scratch/semantic_search_idle/<run>.json
#
# It clones the TRACKED tree of `-Source` into `-Root` and drives the shim over real stdio, so the
# first search is a genuine cold full rebuild and the engine warms the way a session warms it. The
# live shared `.search-index` is never read, written or copied. `-Root` is deleted and rebuilt, so it
# must sit below `.claude/scratch/`.
#
# Measures, by exact PID: the owned backend child's and the wrapper's `PrivateMemorySize64` while
# warm, the same figures after the idle close, and the residual child count. Exits non-zero with a
# diagnosis when any floor is missed or a reply is not a search result, so a failure to warm is never
# reported as a clean run.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Query,
    [Parameter(Mandatory = $true)][double]$IdleMinutes,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Source = '',
    [string]$PluginRoot = '',
    [double]$WarmFloorGb = 2.0,
    [double]$DropFloorGb = 2.0,
    [double]$WrapperCeilingGb = 0.2,
    [int]$SampleSeconds = 5,
    [switch]$KeepRoot
)

$ErrorActionPreference = 'Stop'
$GB = 1e9
$failures = New-Object System.Collections.ArrayList
$result = [ordered]@{}

function Say([string]$message) { Write-Host "[measure-idle] $message" }
function Fail([string]$message) { [void]$failures.Add($message); Say "FAIL: $message" }
. (Join-Path $PSScriptRoot 'lib\measure_common.ps1')

function Start-Proxy([string]$proxyPath, [string]$pluginRoot, [string]$cwd, [double]$minutes) {
    $env:SEMANTIC_SEARCH_IDLE_MINUTES = $minutes.ToString([System.Globalization.CultureInfo]::InvariantCulture)
    Remove-Item Env:\MCP_TOOL_TIMEOUT -ErrorAction SilentlyContinue
    $env:SEMANTIC_SEARCH_IDLE_DEBUG = '1'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-Command node).Source
    $psi.Arguments = '"' + $proxyPath + '" "' + $pluginRoot + '"'
    $psi.WorkingDirectory = $cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    return $proc
}

function Child-Pids([int]$parentPid) {
    $rows = Get-CimInstance Win32_Process -Filter "ParentProcessId=$parentPid" -ErrorAction SilentlyContinue
    return @($rows | Where-Object { $_.Name -eq 'node.exe' } | ForEach-Object { [int]$_.ProcessId })
}

function Private-Bytes([int]$processId) {
    $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $p) { return $null }
    return [int64]$p.PrivateMemorySize64
}

# --------------------------------------------------------------------------- run
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$plugin = Resolve-PluginRoot $PluginRoot
$rootFull = Assert-ScratchRoot $repo $Root
if (-not $Source) { $Source = $repo }
$sourceFull = (Resolve-Path $Source).Path
$outFull = Resolve-UserPath $Out
$proxyPath = (Resolve-Path (Join-Path $repo '.claude\tools\semantic_search_idle_proxy.mjs')).Path
$requestedToolTimeout = $env:MCP_TOOL_TIMEOUT

$result.repo = $repo
$result.plugin_root = $plugin
$result.proxy = $proxyPath
$result.root = $rootFull
$result.source = $sourceFull
$result.query = $Query
$result.idle_minutes = $IdleMinutes
$result.mcp_tool_timeout = if ($requestedToolTimeout) { $requestedToolTimeout } else { '<unset>' }
$result.floors = @{ warm_child_gb = $WarmFloorGb; drop_gb = $DropFloorGb; wrapper_after_gb = $WrapperCeilingGb }

$proc = $null
try {
    Say "preparing isolated root $rootFull from $sourceFull"
    if (Test-Path -LiteralPath $rootFull) { Remove-Item -LiteralPath $rootFull -Recurse -Force }
    New-Item -ItemType Directory -Path $rootFull | Out-Null
    $sourceUrl = 'file:///' + ($sourceFull -replace '\\', '/')
    git clone --quiet --no-hardlinks --depth 1 $sourceUrl $rootFull
    if ($LASTEXITCODE -ne 0) { throw "git clone into $rootFull failed" }
    $indexLeak = Join-Path $rootFull '.search-index'
    if (Test-Path -LiteralPath $indexLeak) { Remove-Item -LiteralPath $indexLeak -Recurse -Force }
    $result.root_files = (Get-ChildItem -LiteralPath $rootFull -Recurse -File -Force |
        Where-Object { $_.FullName -notmatch '\\\.git\\' }).Count

    $proc = Start-Proxy $proxyPath $plugin $rootFull $IdleMinutes
    $result.wrapper_pid = $proc.Id
    Say "wrapper pid $($proc.Id)"

    Send-Line $proc @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{
        protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'idle-memory-proof'; version = '1.0.0' } } }
    $init = Read-Reply $proc 1 60
    $result.server_info = $init.result.serverInfo
    Send-Line $proc @{ jsonrpc = '2.0'; method = 'notifications/initialized'; params = @{} }

    Send-Line $proc @{ jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = @{} }
    $listed = Read-Reply $proc 2 60
    $result.tools = @($listed.result.tools | ForEach-Object { $_.name })
    if ($result.tools.Count -ne 1 -or $result.tools[0] -ne 'search') {
        Fail "tools/list returned $($result.tools -join ',') instead of exactly one 'search'"
    }

    $before = @(Child-Pids $proc.Id)
    $result.children_before_search = $before
    if ($before.Count -ne 0) { Fail "a backend child existed before the first search: $($before -join ',')" }

    Say "cold full rebuild + first search"
    $t0 = Get-Date
    Send-Line $proc @{ jsonrpc = '2.0'; id = 3; method = 'tools/call'; params = @{
        name = 'search'; arguments = @{ query = $Query; searchDir = '.'; rebuild = $true; limit = 5 } } }
    $first = Read-Reply $proc 3 3600
    $result.cold_rebuild_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    $result.first_result_sha256 = Sha (Result-Text $first)
    $result.first_result_length = (Result-Text $first).Length
    $result.first_result_isError = [bool]$first.result.isError
    $problem = Reply-Problem $first
    if ($problem) { Fail "the first search: $problem" }
    Say "cold rebuild + first search took $($result.cold_rebuild_seconds)s"

    $children = @(Child-Pids $proc.Id)
    if ($children.Count -ne 1) { Fail "expected exactly one owned backend child after the first search, found $($children.Count): $($children -join ',')" }
    $childPid = if ($children.Count -ge 1) { $children[0] } else { 0 }
    $result.child_pid_warm = $childPid

    Start-Sleep -Seconds 2
    $warmChild = Private-Bytes $childPid
    $warmWrapper = Private-Bytes $proc.Id
    $result.warm_child_private_bytes = $warmChild
    $result.warm_wrapper_private_bytes = $warmWrapper
    $result.warm_child_private_gb = if ($warmChild) { [math]::Round($warmChild / $GB, 3) } else { $null }
    $result.warm_wrapper_private_gb = if ($warmWrapper) { [math]::Round($warmWrapper / $GB, 3) } else { $null }
    $result.combined_warm_gb = if ($warmChild -and $warmWrapper) { [math]::Round(($warmChild + $warmWrapper) / $GB, 3) } else { $null }
    Say "warm: child $($result.warm_child_private_gb) GB, wrapper $($result.warm_wrapper_private_gb) GB"
    if ($null -eq $warmChild -or $warmChild -lt ($WarmFloorGb * $GB)) {
        Fail "the warm child never reached the $WarmFloorGb GB floor (measured $($result.warm_child_private_gb) GB); diagnose the warm-up, do not read this as a clean run"
    }

    Say "idle window: waiting up to $($IdleMinutes + 5) minutes for pid $childPid to exit"
    $idleStart = Get-Date
    $deadline = $idleStart.AddMinutes($IdleMinutes + 5)
    while ((Get-Date) -lt $deadline) {
        if ((Child-Pids $proc.Id).Count -eq 0) { break }
        Start-Sleep -Seconds $SampleSeconds
    }
    $result.idle_wait_seconds = [math]::Round(((Get-Date) - $idleStart).TotalSeconds, 1)
    $residual = @(Child-Pids $proc.Id)
    $result.children_after_idle = $residual
    $result.idle_child_exited = ($residual.Count -eq 0)
    if ($residual.Count -gt 0) { Fail "backend child $($residual -join ',') survived the idle window after $($result.idle_wait_seconds)s" }

    $afterWrapper = Private-Bytes $proc.Id
    $result.wrapper_private_bytes_after = $afterWrapper
    $result.wrapper_private_gb_after = if ($afterWrapper) { [math]::Round($afterWrapper / $GB, 3) } else { $null }
    $result.combined_after_gb = if ($afterWrapper) { [math]::Round($afterWrapper / $GB, 3) } else { $null }
    $dropGb = $null
    if ($warmChild -and $afterWrapper) {
        $dropGb = [math]::Round((($warmChild + $warmWrapper) - $afterWrapper) / $GB, 3)
    }
    $result.private_bytes_drop_gb = $dropGb
    if ($null -eq $afterWrapper -or $afterWrapper -gt ($WrapperCeilingGb * $GB)) {
        Fail "the wrapper held $($result.wrapper_private_gb_after) GB after the idle close, ceiling $WrapperCeilingGb GB"
    }
    if ($null -eq $dropGb -or $dropGb -lt $DropFloorGb) {
        Fail "combined private bytes dropped only $dropGb GB, floor $DropFloorGb GB"
    }

    Say "second search: the child must be a different pid and the result identical"
    $t1 = Get-Date
    Send-Line $proc @{ jsonrpc = '2.0'; id = 4; method = 'tools/call'; params = @{
        name = 'search'; arguments = @{ query = $Query; searchDir = '.'; limit = 5 } } }
    $second = Read-Reply $proc 4 3600
    $result.warm_second_search_seconds = [math]::Round(((Get-Date) - $t1).TotalSeconds, 1)
    $secondText = Result-Text $second
    $problem = Reply-Problem $second
    if ($problem) { Fail "the second search: $problem" }
    $result.second_result_sha256 = Sha $secondText
    $result.results_identical = ($result.second_result_sha256 -eq $result.first_result_sha256)
    if (-not $result.results_identical) { Fail "the post-idle result differs from the pre-idle result" }

    $children2 = @(Child-Pids $proc.Id)
    $result.child_pid_post_idle = if ($children2.Count -ge 1) { $children2[0] } else { 0 }
    if ($children2.Count -ne 1) { Fail "expected exactly one fresh child after the second search, found $($children2.Count)" }
    if ($childPid -ne 0 -and $result.child_pid_post_idle -eq $childPid) {
        Fail "the second search reused pid $childPid, so nothing was unloaded"
    }
}
catch {
    Fail "the run stopped: $($_.Exception.Message)"
    throw
}
finally {
    $result.wrapper_stderr = 'inherited: the shim and its child write to this console stderr'
    if ($null -ne $proc) {
        if (-not $proc.HasExited) { $proc.Kill(); [void]$proc.WaitForExit(10000) }
        Start-Sleep -Seconds 1
        $orphans = @(Child-Pids $proc.Id)
        $result.children_after_wrapper_exit = $orphans
        foreach ($orphanPid in $orphans) {
            Fail "orphan backend child $orphanPid survived the wrapper exit"
            Stop-Process -Id $orphanPid -Force -ErrorAction SilentlyContinue
        }
        $result.wrapper_exited = $proc.HasExited
    }
    $result.failures = @($failures)
    $result.ok = ($failures.Count -eq 0)
    $json = $result | ConvertTo-Json -Depth 8
    $outDir = Split-Path -Parent $outFull
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    Set-Content -LiteralPath $outFull -Value $json -Encoding utf8
    Say "wrote $outFull"
    if (-not $KeepRoot) { Remove-Item -LiteralPath $rootFull -Recurse -Force -ErrorAction SilentlyContinue }
}

Say ($result | ConvertTo-Json -Depth 8)
if ($failures.Count -gt 0) { exit 1 }
exit 0
