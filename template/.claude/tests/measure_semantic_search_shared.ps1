# Multi-client backend-count and private-bytes measurement for the semantic-search frontend shim.
#
#   pwsh -NoProfile -File .claude/tests/measure_semantic_search_shared.ps1 `
#        -Root <a directory below .claude/scratch> -Source <git repo> `
#        -Proxies <shim.mjs>[,<shim.mjs>...] -Clients 3 -Query "<fixed query>" `
#        -Out .claude/scratch/semantic_search_shared/<run>.json `
#        [-ExpectPeakBackends 1] [-EstimateBackendGb 0.3] [-ReuseRoot]
#
# Arms run in the order `-Proxies` gives, in one invocation, against one root whose index the stock
# backend pre-builds alone and a checkpoint then makes self-contained. Only the proxy varies.
#
# Frozen-input gate, before the first arm and after every arm: every backend an arm started has
# exited, `search.db-wal` is absent or empty, and `search.db` hashes to the pre-build value.
# A failed gate fails the run before the next arm starts.
#
# RAM: an arm starts only when free physical memory is at least `-MinFreeGb` plus the expected
# backends (`-ExpectPeakBackends` when given, else one per client) x `-EstimateBackendGb`; an arm
# that fails admission, or sees free memory fall under
# `-MinFreeGb` while sampling, stops only its own processes and is recorded `blocked`.
#
# Sampling runs every `-SampleMs` from the first search until every reply is in plus `-HoldSeconds`:
# every node.exe running the stock `bin/server.js` whose parent is one of the arm's wrappers, by
# exact PID. An arm with fewer than two samples during its calls fails: a peak nobody sampled is not
# a measurement. `-Root` is deleted and rebuilt, so it must sit below `.claude/scratch`. Exits non-zero
# on any failed reply (a JSON-RPC error, a missing result or a tool error), an exception, differing result texts (within or across arms),
# a peak backend count other than `-ExpectPeakBackends` when given, a failed gate or a blocked arm.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string[]]$Proxies,
    [Parameter(Mandatory = $true)][int]$Clients,
    [Parameter(Mandatory = $true)][string]$Query,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$PluginRoot = '',
    [int]$ExpectPeakBackends = 0,
    [double]$MinFreeGb = 3.0,
    [double]$EstimateBackendGb = 0.3,
    [int]$SampleMs = 500,
    [int]$HoldSeconds = 5,
    [int]$ReplyTimeoutSeconds = 1800,
    [switch]$ReuseRoot,
    [switch]$KeepRoot
)

$ErrorActionPreference = 'Stop'
$GB = 1e9
$failures = New-Object System.Collections.ArrayList
$result = [ordered]@{}

function Say([string]$message) { Write-Host "[measure-shared] $message" }
function Fail([string]$message) { [void]$failures.Add($message); Say "FAIL: $message" }
. (Join-Path $PSScriptRoot 'lib\measure_common.ps1')

# -File does not comma-split array parameters.
$Proxies = @($Proxies | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
if ($Proxies.Count -eq 0) { throw '-Proxies names no shim: at least one arm is required' }

function Free-Gb {
    return [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
}

function Start-Node([string]$scriptArgs, [string]$cwd) {
    $env:SEMANTIC_SEARCH_IDLE_MINUTES = '30'
    Remove-Item Env:\MCP_TOOL_TIMEOUT -ErrorAction SilentlyContinue
    Remove-Item Env:\SEMANTIC_SEARCH_IDLE_DEBUG -ErrorAction SilentlyContinue
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-Command node).Source
    $psi.Arguments = $scriptArgs
    $psi.WorkingDirectory = $cwd
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    # Drain stderr so a chatty child never blocks on a full pipe.
    [void]$proc.StandardError.ReadToEndAsync()
    return $proc
}

function Handshake($proc) {
    $timeout = [math]::Min(120, $ReplyTimeoutSeconds)
    Send-Line $proc @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{
        protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'shared-memory-proof'; version = '1.0.0' } } }
    [void](Read-Reply $proc 1 $timeout)
    Send-Line $proc @{ jsonrpc = '2.0'; method = 'notifications/initialized'; params = @{} }
}

function Backends([int[]]$parents) {
    $rows = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue
    return @($rows | Where-Object { $parents -contains [int]$_.ParentProcessId -and $_.CommandLine -match 'bin[\\/]server\.js' } |
        ForEach-Object { [int]$_.ProcessId })
}

function Private-Bytes([int]$processId) {
    $p = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $p) { return [int64]0 }
    return [int64]$p.PrivateMemorySize64
}

function Stop-Owned($proc) {
    if ($null -eq $proc -or $proc.HasExited) { return }
    try { $proc.StandardInput.Close() } catch { }
    if (-not $proc.WaitForExit(10000)) { $proc.Kill(); [void]$proc.WaitForExit(10000) }
}

function Index-State([string]$indexDir) {
    $db = Join-Path $indexDir 'search.db'
    $wal = Join-Path $indexDir 'search.db-wal'
    return [ordered]@{
        db_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $db).Hash
        wal_bytes = if (Test-Path -LiteralPath $wal) { (Get-Item -LiteralPath $wal).Length } else { 0 }
    }
}

function Frozen-Gate([string]$label, [string]$indexDir, [string]$expectedSha, [int[]]$ownedBackends) {
    $alive = @($ownedBackends | Where-Object { $_ -gt 0 -and (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
    $state = Index-State $indexDir
    $gate = [ordered]@{ at = $label; backends_alive = $alive; db_sha256 = $state.db_sha256; wal_bytes = $state.wal_bytes; ok = $true }
    if ($alive.Count -gt 0) { $gate.ok = $false; Fail "gate ${label}: backends still alive: $($alive -join ',')" }
    if ($state.wal_bytes -gt 0) { $gate.ok = $false; Fail "gate ${label}: search.db-wal holds $($state.wal_bytes) bytes" }
    if ($state.db_sha256 -ne $expectedSha) { $gate.ok = $false; Fail "gate ${label}: search.db hash moved" }
    return $gate
}

function Run-Arm([string]$proxyFull, [int]$index) {
    $arm = [ordered]@{ index = $index; proxy = $proxyFull; proxy_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $proxyFull).Hash; owned_backends = @() }
    $arm.free_gb_admission = Free-Gb
    $expectedBackends = if ($ExpectPeakBackends -gt 0) { $ExpectPeakBackends } else { $Clients }
    $arm.admission_floor_gb = [math]::Round($MinFreeGb + $expectedBackends * $EstimateBackendGb, 2)
    if ($arm.free_gb_admission -lt $arm.admission_floor_gb) {
        $arm.status = 'blocked'
        Fail "arm ${index}: blocked at admission, free $($arm.free_gb_admission) GB under $($arm.admission_floor_gb) GB"
        return $arm
    }

    $wrappers = @()
    $backendsSeen = @{}
    $texts = @()
    try {
        Say "arm ${index}: starting $Clients frontends on $proxyFull"
        for ($i = 0; $i -lt $Clients; $i++) {
            $w = Start-Node ('"' + $proxyFull + '" "' + $plugin + '"') $rootFull
            $wrappers += $w
            Handshake $w
        }
        $wrapperPids = @($wrappers | ForEach-Object { $_.Id })
        $arm.wrapper_pids = $wrapperPids
        Start-Sleep -Seconds 2
        $idleWrapper = @($wrapperPids | ForEach-Object { Private-Bytes $_ })
        $arm.wrapper_private_bytes_before_search = $idleWrapper
        $arm.wrapper_private_gb_before_search_mean = [math]::Round((($idleWrapper | Measure-Object -Average).Average) / $GB, 3)
        $pre = Backends $wrapperPids
        $arm.backends_before_search = $pre.Count
        if ($pre.Count -ne 0) { Fail "arm ${index}: backends existed before any search: $($pre -join ',')" }

        Say "arm ${index}: sending $Clients concurrent searches"
        $pending = @{}
        foreach ($w in $wrappers) {
            Send-Line $w @{ jsonrpc = '2.0'; id = 3; method = 'tools/call'; params = @{
                name = 'search'; arguments = @{ query = $Query; searchDir = '.'; limit = 5 } } }
        }
        foreach ($w in $wrappers) { $pending[$w.Id] = $w.StandardOutput.ReadLineAsync() }

        $replies = @{}
        $peakCount = 0; $peakBackendBytes = [int64]0; $peakCombined = [int64]0; $minFree = $arm.free_gb_admission
        $samplesDuringCalls = 0; $samples = 0; $peakAt = $null; $lastSample = $null
        $t0 = Get-Date
        $holdUntil = $null
        $deadline = $t0.AddSeconds($ReplyTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            # Drain every line already read: progress notifications precede each reply.
            foreach ($w in $wrappers) {
                while ($null -ne $pending[$w.Id] -and $pending[$w.Id].IsCompleted) {
                    $line = $pending[$w.Id].Result
                    if ($null -eq $line) { $replies[$w.Id] = $null; $pending[$w.Id] = $null; break }
                    $msg = $null
                    try { $msg = $line.Trim() | ConvertFrom-Json } catch { }
                    if ($null -ne $msg -and $null -ne $msg.id -and [int]$msg.id -eq 3) {
                        $replies[$w.Id] = $msg; $pending[$w.Id] = $null
                    } else {
                        $pending[$w.Id] = $w.StandardOutput.ReadLineAsync()
                    }
                }
            }
            # Replies are polled every 100 ms; the process sample runs every -SampleMs.
            if ($null -ne $lastSample -and ((Get-Date) - $lastSample).TotalMilliseconds -lt $SampleMs) {
                if ($replies.Count -eq $wrappers.Count -and $null -eq $holdUntil) {
                    $arm.all_replied_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
                    $holdUntil = (Get-Date).AddSeconds($HoldSeconds)
                }
                if ($null -ne $holdUntil -and (Get-Date) -ge $holdUntil) { break }
                Start-Sleep -Milliseconds 100
                continue
            }
            $lastSample = Get-Date
            $live = Backends $wrapperPids
            foreach ($b in $live) { $backendsSeen[$b] = $true }
            $bBytes = [int64]0; foreach ($b in $live) { $bBytes += Private-Bytes $b }
            $wBytes = [int64]0; foreach ($p in $wrapperPids) { $wBytes += Private-Bytes $p }
            $samples++
            if ($replies.Count -lt $wrappers.Count) { $samplesDuringCalls++ }
            if ($live.Count -gt $peakCount) { $peakCount = $live.Count }
            if ($bBytes -gt $peakBackendBytes) { $peakBackendBytes = $bBytes; $peakAt = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1) }
            if (($bBytes + $wBytes) -gt $peakCombined) { $peakCombined = $bBytes + $wBytes }
            $free = Free-Gb
            if ($free -lt $minFree) { $minFree = $free }
            if ($free -lt $MinFreeGb) {
                $arm.status = 'blocked'
                Fail "arm ${index}: free memory fell to $free GB, under the $MinFreeGb GB floor; arm stopped"
                break
            }
            if ($replies.Count -eq $wrappers.Count -and $null -eq $holdUntil) {
                $arm.all_replied_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
                $holdUntil = (Get-Date).AddSeconds($HoldSeconds)
            }
            if ($null -ne $holdUntil -and (Get-Date) -ge $holdUntil) { break }
            Start-Sleep -Milliseconds 100
        }

        $arm.sample_ms = $SampleMs
        $arm.samples = $samples
        $arm.samples_during_calls = $samplesDuringCalls
        $arm.peak_backends = $peakCount
        $arm.backend_pids_seen = @($backendsSeen.Keys)
        $arm.peak_backend_private_gb = [math]::Round($peakBackendBytes / $GB, 3)
        $arm.peak_backend_at_seconds = $peakAt
        $arm.peak_combined_private_gb = [math]::Round($peakCombined / $GB, 3)
        $arm.min_free_gb = $minFree
        $arm.steady_backends = (Backends $wrapperPids).Count

        if ($arm.status -ne 'blocked') {
            if ($samplesDuringCalls -lt 2) { Fail "arm ${index}: only $samplesDuringCalls samples while calls were in flight; the peak was not observed" }
            if ($replies.Count -ne $wrappers.Count) { Fail "arm ${index}: only $($replies.Count) of $($wrappers.Count) clients replied" }
            foreach ($w in $wrappers) {
                if (-not $replies.ContainsKey($w.Id)) { continue }
                $r = $replies[$w.Id]
                if ($null -eq $r) { Fail "arm ${index}: pid $($w.Id) closed stdout before its reply"; continue }
                $problem = Reply-Problem $r
                if ($problem) { Fail "arm ${index}: pid $($w.Id): $problem"; continue }
                $texts += (Result-Text $r)
            }
            $arm.result_sha256 = @($texts | ForEach-Object { Sha $_ } | Sort-Object -Unique)
            if ($arm.result_sha256.Count -gt 1) { Fail "arm ${index}: the $Clients clients got $($arm.result_sha256.Count) different results for one query" }
            if ($ExpectPeakBackends -gt 0 -and $peakCount -ne $ExpectPeakBackends) {
                Fail "arm ${index}: peak backend count was $peakCount, expected $ExpectPeakBackends"
            }
            $arm.status = 'measured'
        }
    }
    finally {
        foreach ($w in $wrappers) { Stop-Owned $w }
        Start-Sleep -Seconds 2
        $orphans = @($backendsSeen.Keys | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        $arm.backends_after_teardown = $orphans
        foreach ($o in $orphans) {
            Fail "arm ${index}: backend $o outlived its wrapper"
            Stop-Process -Id $o -Force -ErrorAction SilentlyContinue
        }
    }
    $arm.owned_backends = @($backendsSeen.Keys)
    return $arm
}

# --------------------------------------------------------------------------- run
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$plugin = Resolve-PluginRoot $PluginRoot
$rootFull = Assert-ScratchRoot $repo $Root
$sourceFull = (Resolve-Path $Source).Path
$proxyPaths = @($Proxies | ForEach-Object { (Resolve-Path $_).Path })
$outFull = Resolve-UserPath $Out
$stockServer = Join-Path $plugin 'bin\server.js'
$indexDir = Join-Path $rootFull '.search-index'
$indexDb = Join-Path $indexDir 'search.db'

$result.plugin_root = $plugin
$result.root = $rootFull
$result.source = $sourceFull
$result.clients = $Clients
$result.query = $Query
$result.proxies = $proxyPaths
$result.min_free_gb = $MinFreeGb
$result.estimate_backend_gb = $EstimateBackendGb
$result.free_gb_start = Free-Gb
$result.arms = @()
$result.gates = @()

try {
    if (-not ($ReuseRoot -and (Test-Path -LiteralPath $indexDb))) {
        Say "preparing isolated root $rootFull from $sourceFull"
        if (Test-Path -LiteralPath $rootFull) { Remove-Item -LiteralPath $rootFull -Recurse -Force }
        New-Item -ItemType Directory -Path $rootFull | Out-Null
        git clone --quiet --no-hardlinks --depth 1 ('file:///' + ($sourceFull -replace '\\', '/')) $rootFull
        if ($LASTEXITCODE -ne 0) { throw "git clone into $rootFull failed" }
        if (Test-Path -LiteralPath $indexDir) { Remove-Item -LiteralPath $indexDir -Recurse -Force }

        if ((Free-Gb) -lt ($MinFreeGb + $EstimateBackendGb)) { throw "free memory is under the pre-build floor of $($MinFreeGb + $EstimateBackendGb) GB" }
        Say 'pre-building the index with the stock backend alone'
        $builder = Start-Node ('"' + $stockServer + '"') $rootFull
        try {
            Handshake $builder
            $t0 = Get-Date
            Send-Line $builder @{ jsonrpc = '2.0'; id = 2; method = 'tools/call'; params = @{
                name = 'search'; arguments = @{ query = $Query; searchDir = '.'; rebuild = $true; limit = 5 } } }
            $built = Read-Reply $builder 2 $ReplyTimeoutSeconds
            $problem = Reply-Problem $built
            if ($problem) { throw "the pre-build failed: $problem" }
            $result.prebuild_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
        } finally {
            Stop-Owned $builder
        }
    }
    # A checkpoint folds any WAL frames into search.db, so its hash is the whole index.
    python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" $indexDb
    if ($LASTEXITCODE -ne 0) { throw "checkpointing $indexDb failed" }
    $result.index_bytes = (Get-Item -LiteralPath $indexDb).Length
    $expectedSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $indexDb).Hash
    $result.index_sha256 = $expectedSha

    $gate = Frozen-Gate 'before-arm-1' $indexDir $expectedSha @()
    $result.gates += $gate
    if ($gate.ok) {
        for ($i = 0; $i -lt $proxyPaths.Count; $i++) {
            $arm = Run-Arm $proxyPaths[$i] ($i + 1)
            $result.arms += $arm
            $gate = Frozen-Gate "after-arm-$($i + 1)" $indexDir $expectedSha @($arm.owned_backends)
            $result.gates += $gate
            if (-not $gate.ok -or $arm.status -eq 'blocked') { break }
        }
    }
    $allTexts = @($result.arms | Where-Object { $_.result_sha256 } | ForEach-Object { $_.result_sha256 } | Sort-Object -Unique)
    $result.distinct_results_across_arms = $allTexts.Count
    if ($allTexts.Count -gt 1) { Fail "arms returned $($allTexts.Count) different results for one query on one frozen index" }
}
catch {
    Fail "the run stopped: $($_.Exception.Message)"
    throw
}
finally {
    $result.failures = @($failures)
    $result.ok = ($failures.Count -eq 0)
    $outDir = Split-Path -Parent $outFull
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    Set-Content -LiteralPath $outFull -Value ($result | ConvertTo-Json -Depth 8) -Encoding utf8
    Say "wrote $outFull"
    if (-not $KeepRoot -and -not $ReuseRoot) { Remove-Item -LiteralPath $rootFull -Recurse -Force -ErrorAction SilentlyContinue }
}

Say ($result | ConvertTo-Json -Depth 8)
if ($failures.Count -gt 0) { exit 1 }
exit 0
