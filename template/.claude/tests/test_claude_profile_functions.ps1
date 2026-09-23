#!/usr/bin/env pwsh
$ErrorActionPreference = 'Stop'

$ProfileFunctions = Join-Path $PSScriptRoot '..\scripts\claude_profile_functions.ps1'
. $ProfileFunctions

$script:Failed = 0
$script:Total = 0
function Assert-True {
    param([bool]$Condition, [string]$Name, [string]$Detail = '')
    $script:Total++
    if ($Condition) { Write-Host "ok   $Name"; return }
    $script:Failed++
    Write-Host "FAIL $Name$(if ($Detail) { ": $Detail" })"
}

# Exercise Invoke-ClaudeGpt without starting a proxy or a Claude child.
function Get-ExternalModel {
    param([string]$Alias)
    [pscustomobject]@{ id = "gpt-5.6-$Alias"; alias = $Alias; transport = 'codex' }
}
$script:FallbackId = $null
function Get-SubagentFallback {
    if ($script:FallbackId) { [pscustomobject]@{ id = $script:FallbackId } }
}
function Get-TransportModelIds { @('gpt-5.6-luna', 'gpt-5.6-sol') }
function Test-Path { return $true }
function New-Item { [CmdletBinding()] param([string]$ItemType, [switch]$Force, [string]$Path); [pscustomobject]@{ FullName = $Path } }
function Start-Process {
    [CmdletBinding()]
    param([string]$FilePath, [object[]]$ArgumentList, [switch]$NoNewWindow, [switch]$PassThru,
          [string]$RedirectStandardOutput, [string]$RedirectStandardError)
    $script:ProxyStarted = $true
    $script:ProxyPins = "$($env:CCP_CODEX_MODEL)|$($env:CCP_CODEX_EFFORT)"
    $script:ProxyContinuation = $env:CCP_CODEX_PREVIOUS_RESPONSE_ID
    $script:ProxyServerCompaction = $env:CCP_CODEX_SERVER_COMPACTION
    [pscustomobject]@{ Id = 4242 }
}
function Invoke-WebRequest {
    param([string]$Uri, [int]$TimeoutSec, [switch]$UseBasicParsing)
    [pscustomobject]@{ StatusCode = 200 }
}
function Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) }
$script:PythonCalls = @()
$script:MissingContextFor = $null
$script:ContinuationReply = '1'
$script:ServerCompactionReply = '1'
$script:ContinuationExit = 0
$script:ServerCompactionExit = 0
function python {
    $global:LASTEXITCODE = 0
    if ($args -contains 'continuation') {
        $global:LASTEXITCODE = $script:ContinuationExit
        $script:ContinuationArgs = @($args)
        return $script:ContinuationReply
    }
    if ($args -contains 'server-compaction') {
        $global:LASTEXITCODE = $script:ServerCompactionExit
        $script:ServerCompactionArgs = @($args)
        return $script:ServerCompactionReply
    }
    $script:PythonArgs = @($args)
    $script:PythonCalls += ,@($args)
    if ($script:MissingContextFor -and $args -contains $script:MissingContextFor) { return }
    if ($args -contains '--max') { '828400' } else { '258400' }
}
function claude {
    $script:ClaudeArgs = @($args)
    $script:ChildContext = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
    $script:ChildSubagent = $env:CLAUDE_CODE_SUBAGENT_MODEL
    $script:ChildFast = $env:ANTHROPIC_SMALL_FAST_MODEL
}

$env:CCP_CODEX_MODEL = 'gpt-5.4-mini'
$env:CCP_CODEX_EFFORT = 'high'
$env:CCP_CODEX_PREVIOUS_RESPONSE_ID = 'parent'
$env:CCP_CODEX_SERVER_COMPACTION = 'parent-server'
$null = Invoke-ClaudeGpt -Model 'sol' -Effort 'high' -LongContext -Passthru @('--version')
Assert-True (($script:ContinuationArgs -join '|') -match 'ccp_probe\.py\|continuation\|.+claude-code-proxy') 'the launcher asks the probe about the resolved proxy binary' ($script:ContinuationArgs -join ' ')
Assert-True (($script:ServerCompactionArgs -join '|') -match 'ccp_probe\.py\|server-compaction\|.+claude-code-proxy') 'the launcher asks the probe about native server compaction' ($script:ServerCompactionArgs -join ' ')
Assert-True ($script:ProxyContinuation -eq '1') 'a continuation-capable proxy starts with continuation on' $script:ProxyContinuation
Assert-True ($script:ProxyServerCompaction -eq '1') 'a native-compaction proxy starts with server compaction on' $script:ProxyServerCompaction
Assert-True ($env:CCP_CODEX_PREVIOUS_RESPONSE_ID -eq 'parent') 'the launcher restores the parent continuation value' $env:CCP_CODEX_PREVIOUS_RESPONSE_ID
Assert-True ($env:CCP_CODEX_SERVER_COMPACTION -eq 'parent-server') 'the launcher restores the parent server-compaction value' $env:CCP_CODEX_SERVER_COMPACTION
foreach ($reply in @('0', '', 'garbage')) {
    $script:ContinuationReply = $reply
    $null = Invoke-ClaudeGpt -Model 'sol' -Passthru @('--version')
    Assert-True ($script:ProxyContinuation -eq '0') "probe reply '$reply' starts the proxy with continuation explicitly off" $script:ProxyContinuation
}
$script:ContinuationReply = '1'
foreach ($reply in @('0', '', 'garbage')) {
    $script:ServerCompactionReply = $reply
    $null = Invoke-ClaudeGpt -Model 'sol' -Passthru @('--version')
    Assert-True ($script:ProxyServerCompaction -eq '0') "server-compaction probe reply '$reply' starts the proxy with native compaction explicitly off" $script:ProxyServerCompaction
}
$script:ServerCompactionReply = '1'
$script:ContinuationExit = 7
$script:ProxyStarted = $false
$script:ProbeError = ''
try { Invoke-ClaudeGpt -Model 'sol' -Passthru @('--version') } catch { $script:ProbeError = "$_" }
Assert-True ($script:ProbeError -match 'continuation.*probe.*exit 7|probe.*continuation.*exit 7') 'a crashed continuation probe is reported as a probe failure' $script:ProbeError
Assert-True (-not $script:ProxyStarted) 'a crashed continuation probe starts no proxy'
$script:ContinuationExit = 0
$script:ServerCompactionExit = 8
$script:ProxyStarted = $false
$script:ProbeError = ''
try { Invoke-ClaudeGpt -Model 'sol' -Passthru @('--version') } catch { $script:ProbeError = "$_" }
Assert-True ($script:ProbeError -match 'server-compaction.*probe.*exit 8|probe.*server-compaction.*exit 8') 'a crashed server-compaction probe is reported as a probe failure' $script:ProbeError
Assert-True (-not $script:ProxyStarted) 'a crashed server-compaction probe starts no proxy'
$script:ServerCompactionExit = 0
Remove-Item Env:CCP_CODEX_PREVIOUS_RESPONSE_ID
Remove-Item Env:CCP_CODEX_SERVER_COMPACTION
$null = Invoke-ClaudeGpt -Model 'sol' -Effort 'high' -LongContext -Passthru @('--version')
Assert-True (($script:PythonArgs -join '|') -match 'context-window\|gpt-5\.6-sol\|--max$') 'long mode asks the registry for the max window' ($script:PythonArgs -join ' ')
Assert-True (($script:ClaudeArgs -join '|') -eq '--model|gpt-5.6-sol[1m]|--effort|high|--version') 'long mode launches the suffixed model' ($script:ClaudeArgs -join ' ')
Assert-True ($script:ChildContext -eq '828400') 'long mode declares the effective max window' $script:ChildContext
Assert-True ($script:ChildSubagent -eq 'gpt-5.6-sol[1m]') 'long mode keeps unpinned subagents on long context' $script:ChildSubagent
Assert-True ($script:ChildFast -eq 'gpt-5.6-sol[1m]') 'long mode keeps small-fast requests on long context' $script:ChildFast
Assert-True ($script:ProxyPins -eq '|') 'the proxy starts with no server-wide model or effort pin' $script:ProxyPins
Assert-True ($env:CCP_CODEX_MODEL -eq 'gpt-5.4-mini' -and $env:CCP_CODEX_EFFORT -eq 'high') 'the launcher restores the parent proxy pins'

$null = Invoke-ClaudeGpt -Model 'sol' -Effort 'medium' -Passthru @('--version')
Assert-True (-not ($script:PythonArgs -contains '--max')) 'standard mode keeps the default window query' ($script:PythonArgs -join ' ')
Assert-True (($script:ClaudeArgs -join '|') -eq '--model|gpt-5.6-sol|--effort|medium|--version') 'standard mode keeps the base model id' ($script:ClaudeArgs -join ' ')
Assert-True ($script:ChildContext -eq '258400') 'standard mode declares the effective default window' $script:ChildContext
Assert-True ($script:ChildSubagent -eq 'gpt-5.6-sol') 'standard mode keeps the base subagent id' $script:ChildSubagent

$script:ClaudeArgs = $null
$null = Invoke-ClaudeGpt -Model 'sol' -Effort 'medium' -LongContext -Passthru @('--model', 'terra', '--version')
Assert-True (($script:ClaudeArgs -join '|') -eq '--effort|medium|--model|gpt-5.6-terra[1m]|--version') 'long mode applies the suffix to a caller model override' ($script:ClaudeArgs -join ' ')
Assert-True ($script:ChildFast -eq 'gpt-5.6-terra[1m]') 'a caller model override also drives small-fast requests' $script:ChildFast

$script:FallbackId = 'gpt-5.6-luna'
$null = Invoke-ClaudeGpt -Model 'astra' -LongContext -Passthru @('--version')
Assert-True ($script:ChildSubagent -eq 'gpt-5.6-luna[1m]') 'long mode suffixes a dispatch-safe fallback' $script:ChildSubagent

$script:MissingContextFor = 'gpt-5.6-luna'
$script:ClaudeArgs = $null
$script:ProxyStarted = $false
$script:WindowError = ''
try { Invoke-ClaudeGpt -Model 'astra' -LongContext -Passthru @('--version') } catch { $script:WindowError = "$_" }
Assert-True ($script:WindowError -match "gpt-5\.6-luna.*no max context window|no max context window.*gpt-5\.6-luna") 'a fallback with no max window refuses to launch' $script:WindowError
Assert-True ($null -eq $script:ClaudeArgs -and -not $script:ProxyStarted) 'an invalid fallback starts neither the proxy nor Claude'

$script:FallbackId = $null
$script:MissingContextFor = 'gpt-5.6-sol'
$script:ClaudeArgs = $null
$script:ProxyStarted = $false
$script:WindowError = ''
try { Invoke-ClaudeGpt -Model 'sol' -LongContext -Passthru @('--version') } catch { $script:WindowError = "$_" }
Assert-True ($script:WindowError -match 'no max context window') 'a missing driver max window refuses to launch' $script:WindowError
Assert-True ($script:WindowError -match 'model_registry.py context-window gpt-5\.6-sol --max') 'the missing-window error gives the exact lookup command' $script:WindowError
Assert-True ($null -eq $script:ClaudeArgs -and -not $script:ProxyStarted) 'a missing driver window starts neither the proxy nor Claude'
$script:MissingContextFor = $null

# ---- the interactive opencode alias surface derives from the registry's anthropic rows ----
# A planted registry, so the case proves derivation rather than today's roster: a hardcoded list
# can match the live rows by coincidence, never these invented ids.
$plantedRegistry = Join-Path ([IO.Path]::GetTempPath()) "profile_registry_$PID.json"
$plantedJson = @{ transports = @{}; models = @(
    @{ id = 'claude-opus-9'; alias = 'opus'; transport = 'anthropic' },
    @{ id = 'claude-opus-8'; alias = 'opus8'; transport = 'anthropic' },
    @{ id = 'claude-haiku-9-20990101'; alias = 'haiku'; transport = 'anthropic'; servedIds = @('claude-haiku-9') },
    @{ id = 'muse-x-free'; alias = 'muse'; transport = 'opencode' },
    @{ id = 'gpt-x-luna'; alias = 'luna'; transport = 'codex' }
) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($plantedRegistry, $plantedJson)
$oldRegistryEnv = $env:HARNESS_MODEL_REGISTRY
$env:HARNESS_MODEL_REGISTRY = $plantedRegistry
$expectedTierNames = @('claude-opus-9', 'opus', 'claude-opus-8', 'opus8', 'claude-haiku-9-20990101', 'haiku', 'claude-haiku-9') | Sort-Object

function Get-ExternalModel {
    param([string]$Alias)
    ((Get-Content $env:HARNESS_MODEL_REGISTRY -Raw | ConvertFrom-Json).models |
        Where-Object { $_.alias -eq $Alias -or $_.id -eq $Alias } | Select-Object -First 1)
}
function Invoke-RestMethod { param([string]$Uri, [string]$Method, [string]$Body, [string]$ContentType, [hashtable]$Headers, [int]$TimeoutSec) }
function Select-String { param([string]$Path, [string]$Pattern) }
function Set-Content { param([string]$Path, [string]$Encoding, [object[]]$Value) $script:OpencodeYaml = @($Value) }

$tierNames = @()
try { $tierNames = @(Get-AnthropicTierNames) | Sort-Object } catch { $tierNames = @("threw: $_") }
Assert-True (($tierNames -join '|') -eq ($expectedTierNames -join '|')) 'the opencode alias list is the registry anthropic ids, aliases and served spellings' ($tierNames -join ' ')

$script:OpencodeYaml = @()
$null = Invoke-ClaudeOpenCode -Alias 'muse' -Passthru @('--version')
$yamlNames = @($script:OpencodeYaml | Where-Object { $_ -match '^\s+- model_name: ' } |
    ForEach-Object { ($_ -replace '^\s+- model_name: ', '').Trim() } | Where-Object { $_ -ne 'muse-x-free' } | Sort-Object)
Assert-True (($yamlNames -join '|') -eq ($expectedTierNames -join '|')) 'the interactive proxy config routes exactly those names to the driven model' ($yamlNames -join ' ')
Assert-True (-not ($script:OpencodeYaml -match 'claude-opus-4-6|claude-sonnet-5')) 'no hardcoded tier id survives in the proxy config' ($script:OpencodeYaml -join ' ')

# A null alias is not a tier name: it would emit an empty 'model_name:' line.
$nullAliasJson = @{ transports = @{}; models = @(@{ id = 'claude-x-9'; alias = $null; transport = 'anthropic' }) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($plantedRegistry, $nullAliasJson)
$nullNames = @(Get-AnthropicTierNames)
Assert-True (($nullNames -join '|') -eq 'claude-x-9') 'a null alias or served id never becomes a tier name' (($nullNames | ForEach-Object { "[$_]" }) -join ' ')

# An unreadable or missing registry fails visibly, and Invoke-ClaudeOpenCode stops before the proxy.
[IO.File]::WriteAllText($plantedRegistry, '{ not json')
$script:TierError = $null
try { $null = Get-AnthropicTierNames } catch { $script:TierError = "$_" }
Assert-True ($script:TierError -match 'unreadable') 'an unparseable registry throws instead of returning no tier names' "$script:TierError"
$script:ProxyStarted = $false; $script:TierError = $null
try { $null = Invoke-ClaudeOpenCode -Alias 'muse' -Passthru @('--version') } catch { $script:TierError = "$_" }
Assert-True ($script:TierError -and -not $script:ProxyStarted) 'an unreadable registry starts no interactive proxy' "$script:TierError"
$env:HARNESS_MODEL_REGISTRY = Join-Path ([IO.Path]::GetTempPath()) "absent_registry_$PID.json"
$script:TierError = $null
try { $null = Get-AnthropicTierNames } catch { $script:TierError = "$_" }
Assert-True ($null -ne $script:TierError) 'a missing registry throws instead of returning no tier names' "$script:TierError"

$env:HARNESS_MODEL_REGISTRY = $oldRegistryEnv
Remove-Item $plantedRegistry -ErrorAction SilentlyContinue

# Replace the body with a spy so each public wrapper proves its wiring.
$script:WrapperCalls = @()
function Invoke-ClaudeGpt {
    param(
        [ValidateSet('low', 'medium', 'high', 'max')][string]$Effort = 'medium',
        [string]$Model = 'luna',
        [switch]$LongContext,
        [string[]]$Passthru = @()
    )
    $script:WrapperCalls += [pscustomobject]@{
        Model = $Model
        Effort = $Effort
        LongContext = $LongContext.IsPresent
        Passthru = @($Passthru)
    }
}

$Cases = @(
    @('claude-gpt-1m', 'luna', 'medium'),
    @('claude-gpt-low-1m', 'luna', 'low'),
    @('claude-gpt-high-1m', 'luna', 'high'),
    @('claude-gpt-terra-1m', 'terra', 'medium'),
    @('claude-gpt-sol-1m', 'sol', 'medium'),
    @('claude-gpt-astra-1m', 'astra', 'medium')
)
foreach ($Case in $Cases) {
    $before = $script:WrapperCalls.Count
    & $Case[0] '--version'
    $call = $script:WrapperCalls[-1]
    Assert-True ($script:WrapperCalls.Count -eq $before + 1) "$($Case[0]) invokes the shared launcher"
    Assert-True ($call.Model -eq $Case[1] -and $call.Effort -eq $Case[2] -and $call.LongContext) "$($Case[0]) selects $($Case[1]) $($Case[2]) long context"
    Assert-True (($call.Passthru -join '|') -eq '--version') "$($Case[0]) preserves passthrough args"
}

claude-gpt-sol '--version'
$control = $script:WrapperCalls[-1]
Assert-True ($control.Model -eq 'sol' -and -not $control.LongContext) 'the existing sol wrapper stays standard-context'

$total = $script:Total
Write-Host "`n$($total - $script:Failed)/$total passed"
exit $(if ($script:Failed) { 1 } else { 0 })
