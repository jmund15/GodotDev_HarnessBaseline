# Shared by measure_semantic_search_idle.ps1 and measure_semantic_search_shared.ps1; dot-source it.
# Stdio JSON-RPC to a node child, reply validation, and the one root either script may delete.

function Resolve-UserPath([string]$path) {
    # [IO.Path]::GetFullPath resolves against the process directory, which pwsh does not keep equal to $PWD.
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
}

function Resolve-PluginRoot([string]$pluginRoot) {
    if ($pluginRoot) { return (Resolve-Path $pluginRoot).Path }
    $registry = Join-Path $HOME '.claude\plugins\installed_plugins.json'
    $data = Get-Content -Raw -LiteralPath $registry | ConvertFrom-Json
    $entries = @($data.plugins.'semantic-search@dreb' | Where-Object { $_.scope -eq 'user' })
    if ($entries.Count -ne 1) { throw "expected exactly one user-scope semantic-search@dreb registration, found $($entries.Count)" }
    return $entries[0].installPath
}

function Assert-ScratchRoot([string]$repo, [string]$path) {
    $full = (Resolve-UserPath $path).TrimEnd('\', '/')
    $scratch = [System.IO.Path]::GetFullPath((Join-Path $repo '.claude\scratch')).TrimEnd('\', '/')
    if (-not $full.StartsWith($scratch + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "refusing -Root ${full}: the root is deleted and rebuilt, so it must sit below $scratch"
    }
    return $full
}

function Send-Line($proc, $payload) {
    $proc.StandardInput.WriteLine(($payload | ConvertTo-Json -Compress -Depth 12))
    $proc.StandardInput.Flush()
}

function Read-Reply($proc, [int]$id, [int]$timeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ($true) {
        $remaining = [int][math]::Ceiling(($deadline - (Get-Date)).TotalMilliseconds)
        if ($remaining -le 0) { throw "timed out after ${timeoutSeconds}s waiting for id=$id from pid $($proc.Id)" }
        $read = $proc.StandardOutput.ReadLineAsync()
        if (-not $read.Wait($remaining)) { throw "timed out after ${timeoutSeconds}s waiting for id=$id from pid $($proc.Id)" }
        $line = $read.Result
        if ($null -eq $line) { throw "pid $($proc.Id) closed stdout before replying to id=$id" }
        $line = $line.Trim()
        if (-not $line) { continue }
        try { $msg = $line | ConvertFrom-Json } catch { continue }
        if ($null -ne $msg.id -and [int]$msg.id -eq $id) { return $msg }
    }
}

function Result-Text($reply) {
    if ($null -eq $reply.result -or $null -eq $reply.result.content) { return '' }
    return (($reply.result.content | ForEach-Object { $_.text }) -join '')
}

# Why a tools/call reply is not a usable search result, or $null when it is one.
function Reply-Problem($reply) {
    if ($null -eq $reply) { return 'no reply' }
    if ($null -ne $reply.error) { return "JSON-RPC error $($reply.error.code): $($reply.error.message)" }
    if ($null -eq $reply.result -or @($reply.result.content).Count -eq 0) { return 'the reply carries no tool result content' }
    if ($reply.result.isError) { return "the tool returned an error: $(Result-Text $reply)" }
    return $null
}

function Sha([string]$text) {
    return [System.BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes($text))).Replace('-', '')
}
