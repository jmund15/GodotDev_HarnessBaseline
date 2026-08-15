# Claude Code shell integration — canonical home for the `claude-*` PowerShell functions.
#
# The user's $PROFILE dot-sources this file rather than copying the bodies, so these
# functions are version-controlled and edits here reach the shell with no sync step:
#
#     . "<path-to-repo>\.claude\scripts\claude_profile_functions.ps1"
#
# Provisioned/verified by /workstation_setup Phase 4.

function claude-primary {
    $old = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = "$HOME\.claude"
    try {
        claude @args
    } finally {
        $env:CLAUDE_CONFIG_DIR = $old
    }
}

function claude-secondary {
    $old = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = "$HOME\.claude-secondary"
    try {
        claude @args
    } finally {
        $env:CLAUDE_CONFIG_DIR = $old
    }
}

# --------------------------------------------------------------------------
# DeepSeek sessions. Three entry points, one body:
#
#     claude-deepseek-pro     V4 Pro   (large-scope architecting, orchestration)
#     claude-deepseek-flash   V4 Flash (everything else - the cheap default)
#     claude-deepseek         = pro
#
# Model ids, versions and prices all come from
# .claude/reference/external_models.json - the one place they are authored.
# Never hardcode a rate or an id in this file.
# --------------------------------------------------------------------------

function Get-DeepSeekModel {
    param([Parameter(Mandatory)][string]$Alias)
    $registry = Join-Path $PSScriptRoot "..\reference\external_models.json"
    if (-not (Test-Path $registry)) { return $null }
    try { $data = Get-Content $registry -Raw | ConvertFrom-Json } catch { return $null }
    $data.models | Where-Object { $_.alias -eq $Alias -or $_.id -eq $Alias } | Select-Object -First 1
}

function Invoke-ClaudeDeepSeek {
    # One-off Claude Code session against DeepSeek's Anthropic-compatible
    # endpoint. Env changes are restored on exit, so this session only —
    # future `claude` invocations stay on the subscription plan.
    param(
        [Parameter(Mandatory)][string]$Alias,
        [string[]]$Passthru = @()
    )

    $model = Get-DeepSeekModel -Alias $Alias
    if (-not $model) {
        Write-Error "model '$Alias' not in .claude/reference/external_models.json (expected: pro | flash)"
        return
    }
    $sub = Get-DeepSeekModel -Alias 'flash'   # unpinned spawns stay cheap regardless of driver

    $envFile = "$HOME\.env.ai-worker.cmd"
    if (-not (Test-Path $envFile)) { Write-Error "credential file missing: $envFile"; return }
    $line = Select-String -Path $envFile -Pattern '^\s*set\s+DEEPSEEK_API_KEY=' | Select-Object -First 1
    if (-not $line) { Write-Error "DEEPSEEK_API_KEY not found in $envFile"; return }
    $key = ($line.Line -replace '^\s*set\s+DEEPSEEK_API_KEY=', '').Trim().Trim('"')
    if (-not $key -or $key -like 'sk-xxx*' -or $key -eq '<redacted>') { Write-Error "DEEPSEEK_API_KEY not populated"; return }

    $oldBase  = $env:ANTHROPIC_BASE_URL
    $oldTok   = $env:ANTHROPIC_AUTH_TOKEN
    $oldKey   = $env:ANTHROPIC_API_KEY
    $oldFast  = $env:ANTHROPIC_SMALL_FAST_MODEL
    $oldSub   = $env:CLAUDE_CODE_SUBAGENT_MODEL
    $oldEntry = $env:CLAUDE_CODE_ENTRYPOINT
    $oldCtx   = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
    $env:ANTHROPIC_BASE_URL   = "https://api.deepseek.com/anthropic"
    $env:ANTHROPIC_AUTH_TOKEN = $key
    $env:ANTHROPIC_API_KEY    = ""
    # Host-auth leak (measured 2026-08-04): when CLAUDE_CODE_ENTRYPOINT is
    # 'claude-desktop' the child authenticates through the HOST's subscription
    # OAuth and ignores ANTHROPIC_AUTH_TOKEN entirely — DeepSeek then 401s on a
    # rotating token whose tail matches no key you own. Harmless from a plain
    # terminal (entrypoint is already 'cli'); load-bearing when this function is
    # invoked from a desktop-hosted shell, which inherits the desktop value.
    $env:CLAUDE_CODE_ENTRYPOINT = "cli"
    # Pin subagent/background-model names too. Left unpinned, a spawned agent's
    # Anthropic name reaches the compat layer, which aliases full `claude-*` ids
    # BY TIER: claude-opus-* -> V4 Pro (billing-confirmed 2026-08-03),
    # claude-sonnet-*/haiku-*/fable-* -> V4 Flash. Pinning removes that lottery.
    # UNPINNED spawns stay on FLASH even in a Pro-led session — reaching Pro is
    # always deliberate. Explicitly-pinned Workflow dispatches are tier-preserved
    # by the model_pin_translate.py PreToolUse hook.
    $env:ANTHROPIC_SMALL_FAST_MODEL  = $sub.id
    $env:CLAUDE_CODE_SUBAGENT_MODEL  = $sub.id
    # This build has no registry entry for `deepseek-v4-flash` and assumes a 200K
    # window, auto-compacting far too early. Declare the real window (1M, DeepSeek
    # docs [P1]) here instead of via the `[1m]` model-name suffix: the suffix rides
    # on the API string and only Anthropic-registry names survive that round-trip.
    # Compaction threshold = min(autoCompactWindow, this number); the supported knob
    # is `autoCompactWindow` in ~/.claude/settings.json (700000 = 70% of this).
    $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = "$($model.limits.contextTokens)"

    $p = $model.price
    Write-Host ""
    Write-Host "  DeepSeek session - driving model: " -NoNewline -ForegroundColor Cyan
    Write-Host "$($model.id)" -NoNewline -ForegroundColor White
    Write-Host "  [$($model.version)]" -ForegroundColor DarkGray
    Write-Host "    price/1M   cache-hit `$$($p.cacheHitPer1M)   fresh `$$($p.cacheMissPer1M)   output `$$($p.outputPer1M)" -ForegroundColor DarkGray
    Write-Host "    unpinned subagents -> $($sub.id)" -NoNewline -ForegroundColor DarkGray
    if ($model.id -ne $sub.id) { Write-Host "  (NOT $($model.alias) - reaching $($model.alias) is always deliberate)" -ForegroundColor DarkGray }
    else { Write-Host "" }
    if ($model.authTier -eq 'gated') {
        Write-Host "    $($model.alias) is GATED for agent-initiated sidecar dispatch (band $($model.gate.minBand), balance `$$($model.gate.minBalanceUSD)); driving it from here is your authorization." -ForegroundColor DarkYellow
    }
    Write-Host "    mid-session /model switches alias BY TIER: Opus -> pro, Sonnet/Haiku/Fable -> flash." -ForegroundColor DarkGray
    Write-Host "    The statusline shows which model is live at any moment - trust it over this banner." -ForegroundColor DarkGray
    Write-Host ""

    try {
        # An explicit --model from the caller always wins. Otherwise drive the
        # resolved id: an Anthropic model NAME would alias by tier on this
        # endpoint, and a bare role name would hard-error outright.
        if ($Passthru -contains '--model') { claude @Passthru }
        else { claude --model $model.id @Passthru }
    } finally {
        $env:ANTHROPIC_BASE_URL   = $oldBase
        $env:ANTHROPIC_AUTH_TOKEN = $oldTok
        $env:ANTHROPIC_API_KEY    = $oldKey
        $env:ANTHROPIC_SMALL_FAST_MODEL = $oldFast
        $env:CLAUDE_CODE_SUBAGENT_MODEL = $oldSub
        $env:CLAUDE_CODE_ENTRYPOINT     = $oldEntry
        $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = $oldCtx
    }
}

function claude-deepseek-pro   { Invoke-ClaudeDeepSeek -Alias 'pro'   -Passthru $args }
function claude-deepseek-flash { Invoke-ClaudeDeepSeek -Alias 'flash' -Passthru $args }

# Bare `claude-deepseek` drives PRO (user directive 2026-08-12). The banner is
# what makes that unambiguous — never make this silent.
function claude-deepseek       { Invoke-ClaudeDeepSeek -Alias 'pro'   -Passthru $args }
