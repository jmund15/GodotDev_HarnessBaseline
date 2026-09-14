# Claude Code shell integration — canonical home for the `claude-*` PowerShell functions.
#
# The user's $PROFILE dot-sources this file rather than copying the bodies, so these
# functions are version-controlled and edits here reach the shell with no sync step:
#
#     . "<repo>\.claude\scripts\claude_profile_functions.ps1"
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

function Get-ExternalModel {
    param([Parameter(Mandatory)][string]$Alias)
    $registry = Join-Path $PSScriptRoot "..\reference\external_models.json"
    if (-not (Test-Path $registry)) { return $null }
    try { $data = Get-Content $registry -Raw | ConvertFrom-Json } catch { return $null }
    $data.models | Where-Object { $_.alias -eq $Alias -or $_.id -eq $Alias } | Select-Object -First 1
}

function Get-TransportModelIds {
    param([Parameter(Mandatory)][string]$Transport)
    # The ids `/model` accepts in a session on $Transport. Dispatch exclusions are NOT applied: they
    # gate delegation, never which model may drive a session, so an excluded row is still a legal
    # `/model` target. Telling the user to "name a GPT id" without naming them leaves them guessing,
    # and the one guess the client accepts silently is a Claude name, which resolves to whatever the
    # proxy feels like.
    $registry = Join-Path $PSScriptRoot "..\reference\external_models.json"
    if (-not (Test-Path $registry)) { return @() }
    try { $data = Get-Content $registry -Raw | ConvertFrom-Json } catch { return @() }
    @($data.models | Where-Object { $_.transport -eq $Transport } | ForEach-Object { $_.id })
}

function Get-SubagentFallback {
    param([Parameter(Mandatory)]$Row, [string]$Prefer)
    # A subagent spawned with no `model` param inherits CLAUDE_CODE_SUBAGENT_MODEL, so pointing that
    # at a row the roster excludes reopens the leak the exclusion exists to close -- silently, since
    # the spawn still succeeds. Returns the row those spawns should use, or $null to leave them on
    # the driver: $Prefer when it is dispatchable (a caller keeping unpinned spawns cheap), else the
    # driver itself when IT is dispatchable, else the first dispatchable sibling on its transport.
    #
    # No alias is written here. A literal stops tracking the registry the day it is excluded, and
    # the banner then names a dead model with total confidence.
    $registry = Join-Path $PSScriptRoot "..\reference\external_models.json"
    if (-not (Test-Path $registry)) { return $null }
    try { $data = Get-Content $registry -Raw | ConvertFrom-Json } catch { return $null }
    # Dispatchability is TWO checks, and model_registry.py applies both: the row's own status, and
    # its transport's. The deepseek rows carry no row-level status at all and are excluded entirely
    # at the transport, so a row-only test reports every one of them dispatchable.
    $ok = {
        param($m)
        if ($m.status -and $m.status.state -eq 'unavailable') { return $false }
        $t = $data.transports.($m.transport)
        if ($t -and $t.status -and $t.status.state -eq 'unavailable') { return $false }
        return $true
    }
    if ($Prefer) {
        $p = $data.models | Where-Object { $_.alias -eq $Prefer -or $_.id -eq $Prefer } | Select-Object -First 1
        if ($p -and (& $ok $p)) { return $p }
    }
    if (& $ok $Row) { return $null }
    $data.models | Where-Object {
        $_.transport -eq $Row.transport -and $_.id -ne $Row.id -and (& $ok $_)
    } | Select-Object -First 1
}

function Invoke-ClaudeDeepSeek {
    # One-off Claude Code session against DeepSeek's Anthropic-compatible
    # endpoint. Env changes are restored on exit, so this session only —
    # future `claude` invocations stay on the subscription plan.
    param(
        [Parameter(Mandatory)][string]$Alias,
        [string[]]$Passthru = @()
    )

    $model = Get-ExternalModel -Alias $Alias
    if (-not $model) {
        Write-Error "model '$Alias' not in .claude/reference/external_models.json (expected: pro | flash)"
        return
    }
    # unpinned spawns stay cheap regardless of driver -- and never land on an excluded row
    $sub = Get-SubagentFallback -Row $model -Prefer 'flash'
    if (-not $sub) { $sub = $model }

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
    $oldTrans = $env:CLAUDE_CODE_TRANSPORT
    # Declared so hooks/_session_transport.py resolves from the launcher's own statement
    # rather than inferring it from the URL; both work here, only this works for a proxy.
    $env:CLAUDE_CODE_TRANSPORT = 'deepseek'
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
    # by the workflow_provider_guard.py PreToolUse hook.
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
        $env:CLAUDE_CODE_TRANSPORT      = $oldTrans
    }
}

function claude-deepseek-pro   { Invoke-ClaudeDeepSeek -Alias 'pro'   -Passthru $args }
function claude-deepseek-flash { Invoke-ClaudeDeepSeek -Alias 'flash' -Passthru $args }

# Bare `claude-deepseek` drives PRO (user directive 2026-08-12). The banner is
# what makes that unambiguous — never make this silent.
function claude-deepseek       { Invoke-ClaudeDeepSeek -Alias 'pro'   -Passthru $args }

# --------------------------------------------------------------------------
# GPT sessions, billed against the ChatGPT plan. Same shape as the DeepSeek
# block, but the varying axis is EFFORT, not model — the Codex route exposes one
# model and grades it by reasoning effort:
#
#     claude-gpt-high    deep reasoning
#     claude-gpt-low     fast, mechanical
#     claude-gpt         = medium
#
# DeepSeek reaches its endpoint with env vars alone. This one cannot: the Codex
# backend speaks a different protocol, so a local claude-code-proxy translates,
# and this function owns its whole lifecycle — start on a free port, wait for
# health, kill on exit. That per-session proxy is forced, not tidiness: the model
# and effort pins are read from the PROXY SERVER's environment at startup, never
# the client's, so a shared proxy would serve whichever pins its starter happened
# to set. The same constraint fixes effort for the session's lifetime; a
# mid-session change means exiting and relaunching.
#
# Mechanism, exit codes and attestation: .claude/scripts/codex_proxy_sidecar.sh.
# --------------------------------------------------------------------------

function Invoke-ClaudeGpt {
    param(
        [ValidateSet('low', 'medium', 'high', 'max')][string]$Effort = 'medium',
        [string]$Model = 'luna',
        [string[]]$Passthru = @()
    )

    $root  = Join-Path $PSScriptRoot ".."
    # NOTE: PowerShell variable names are CASE-INSENSITIVE -- a local named $model would BE
    # the $Model parameter, silently clobbering it before the error paths read it.
    $row = Get-ExternalModel -Alias $Model      # registry lookup is alias-generic
    if (-not $row) { Write-Error "model '$Model' not on the codex transport in .claude/reference/external_models.json - run: python .claude/tools/model_registry.py available"; return }
    if ($row.transport -ne 'codex') { Write-Error "'$Model' is on the '$($row.transport)' transport, not codex - this launcher starts a Codex proxy"; return }

    $ccp = $env:CCP_BIN
    if (-not $ccp -or -not (Test-Path $ccp)) { $ccp = "$HOME\AppData\Local\claude-code-proxy\claude-code-proxy.exe" }
    if (-not (Test-Path $ccp)) {
        $onPath = Get-Command claude-code-proxy -ErrorAction SilentlyContinue
        if ($onPath) { $ccp = $onPath.Source } else { Write-Error "claude-code-proxy not installed (set CCP_BIN)"; return }
    }
    # The proxy authenticates separately from the `codex` CLI; ~/.codex/auth.json does not satisfy it.
    $auth = "$HOME\.config\claude-code-proxy\codex\auth.json"
    if (-not (Test-Path $auth)) { Write-Error "no $auth - run: claude-code-proxy codex auth login"; return }

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()

    $log = "$HOME\.local\state\claude-code-proxy\interactive-serve.log"
    New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

    # CCP_TRAFFIC_LOG stays OFF here. The sidecar sets it because a benchmark record needs
    # model attestation; an interactive session would only accumulate full request bodies,
    # prompts included, in a tree nothing prunes.
    # CCP_CODEX_MODEL is deliberately NOT set. It forces EVERY request through this proxy onto one
    # model - session and subagents alike - so setting it makes per-agent Workflow pins inert. With
    # it unset the proxy honours each request's own `model` field (measured 2026-09-04, probe2.sh),
    # which is what lets this session dispatch to sibling GPT models in-harness with no sidecar.
    # CCP_CODEX_EFFORT is NOT set either, for the same reason. Setting it made the proxy rewrite
    # every request's effort, so `/effort` mid-session was silently inert while the statusline went
    # on showing the value the user had just chosen (measured 2026-09-09: client `low` reached the
    # model as `medium`). Unset, the client's own `output_config.effort` passes through, which
    # Claude Code sends on essentially every request. The launch rung is handed to the CHILD via
    # `--effort` below instead, so it is honoured AND remains changeable in session.
    $oldCcpModel = $env:CCP_CODEX_MODEL; $oldCcpEffort = $env:CCP_CODEX_EFFORT
    Remove-Item Env:CCP_CODEX_MODEL -ErrorAction SilentlyContinue   # REMOVE, not set-empty
    Remove-Item Env:CCP_CODEX_EFFORT -ErrorAction SilentlyContinue
    $proxy = Start-Process -FilePath $ccp -ArgumentList @('serve', '--no-monitor', '--port', "$port") `
                           -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    if ($oldCcpModel) { $env:CCP_CODEX_MODEL = $oldCcpModel }
    $env:CCP_CODEX_EFFORT = $oldCcpEffort

    # /healthz, not /health - the latter 404s on v0.1.35. Bounded: a proxy that cannot bind
    # fails the same way forever, and waiting longer only delays the report.
    $healthy = $false
    foreach ($i in 1..50) {
        Start-Sleep -Milliseconds 500
        try { Invoke-WebRequest -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2 -UseBasicParsing | Out-Null; $healthy = $true; break } catch {}
    }
    if (-not $healthy) {
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
        Write-Error "proxy did not become healthy on :$port within 25s - see $log"
        return
    }

    $ctx = & python (Join-Path $root "tools\model_registry.py") context-window $row.id 2>$null

    $oldBase  = $env:ANTHROPIC_BASE_URL
    $oldTok   = $env:ANTHROPIC_AUTH_TOKEN
    $oldKey   = $env:ANTHROPIC_API_KEY
    $oldFast  = $env:ANTHROPIC_SMALL_FAST_MODEL
    $oldSub   = $env:CLAUDE_CODE_SUBAGENT_MODEL
    $oldEntry = $env:CLAUDE_CODE_ENTRYPOINT
    $oldCtxV  = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
    $env:ANTHROPIC_BASE_URL   = "http://127.0.0.1:$port"
    # A placeholder by design: the proxy accepts any value and holds the real Codex
    # credential itself. Host Anthropic auth is kept out of the path by ENTRYPOINT=cli -
    # a Desktop-hosted shell exports 'claude-desktop', which makes the child authenticate
    # through the HOST's subscription OAuth and ignore this token entirely.
    $env:ANTHROPIC_AUTH_TOKEN = "unused"
    $env:ANTHROPIC_API_KEY    = ""
    $env:CLAUDE_CODE_ENTRYPOINT = "cli"
    $env:ANTHROPIC_SMALL_FAST_MODEL = $row.id
    # A row excluded from dispatch is still session-launchable -- this function's alias lookup never
    # reads availability. But defaulting unpinned subagent spawns (a bare Agent call with no model
    # param) to the SAME excluded model would reopen the quota leak the exclusion exists to stop.
    # Pinned Workflow jobs are unaffected; they resolve their own model.
    $fallback = Get-SubagentFallback -Row $row
    $subagentId = if ($fallback) { $fallback.id } else { $row.id }
    $env:CLAUDE_CODE_SUBAGENT_MODEL = $subagentId
    # Tells the CHILD which transport it is on, so hooks/_session_transport.py resolves it from a
    # declaration rather than from a loopback URL that carries no vendor name. Without it the
    # session reports `unknown` and every model pin is denied.
    $oldTransport = $env:CLAUDE_CODE_TRANSPORT
    $env:CLAUDE_CODE_TRANSPORT = 'codex'
    # The child cannot see CCP_CODEX_EFFORT: it is restored above, right after the proxy reads it.
    # So the session had no way to learn its own real effort, `/effort` and the statusline both
    # showed the CLIENT value the proxy overrides, and the rails could only say "run a probe".
    # Hand the number down instead; hooks/session_model_rails.py states it as fact.
    $oldPpEffort = $env:HARNESS_SESSION_EFFORT
    $env:HARNESS_SESSION_EFFORT = $Effort
    # Claude Code assumes 200,000 for any model it does not recognize, and every GPT id is
    # unrecognized to it. The registry derives the real effective window; never a literal
    # here, and never DeepSeek's 1000000 - over-declaring past this model's ceiling turns a
    # managed client-side compaction into a hard upstream error mid-session.
    if ($ctx) { $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = "$ctx" }

    Write-Host ""
    Write-Host "  GPT session - driving model: " -NoNewline -ForegroundColor Cyan
    Write-Host "$($row.id)" -NoNewline -ForegroundColor White
    Write-Host "  [effort $Effort]" -ForegroundColor DarkGray
    Write-Host "    cost       ChatGPT plan quota - no marginal dollars, but a PER-MODEL allowance" -ForegroundColor DarkGray
    Write-Host "    context    $ctx tokens declared; auto-compaction fires near 80% of that" -ForegroundColor DarkGray
    Write-Host "    effort     /effort works mid-session; the model gets the new value." -ForegroundColor DarkGray
    Write-Host "    /model     switches the driver mid-session. Ids it takes here:" -ForegroundColor DarkGray
    $ids = @(Get-TransportModelIds -Transport $row.transport | ForEach-Object {
        if ($_ -eq $row.id) { "$_ (current)" } else { $_ } })
    if ($ids.Count) { Write-Host ("               " + ($ids -join "   ")) -ForegroundColor White }
    Write-Host "               opus, sonnet and haiku are NOT on that list. Type one and the proxy" -ForegroundColor DarkYellow
    Write-Host "               picks some GPT model for you, without saying which." -ForegroundColor DarkYellow
    Write-Host "    agents     pin GPT ids per agent (Workflow) - siblings need no sidecar." -ForegroundColor DarkGray
    if ($fallback) {
        Write-Host "               $($row.id) is not a dispatch target, so an agent with no model pin runs on" -ForegroundColor DarkYellow
        Write-Host "               $subagentId. Both names are read from reference/external_models.json." -ForegroundColor DarkYellow
    }
    Write-Host "    proxy      pid $($proxy.Id) on :$port - killed when this session exits." -ForegroundColor DarkGray
    Write-Host ""

    try {
        # `--effort` sets the CHILD's rung, so the launch value rides the client's own
        # output_config.effort rather than a proxy override -- honoured at launch and still
        # changeable with /effort. Skipped when the caller passed their own.
        $effortArgs = if ($Passthru -contains '--effort') { @() } else { @('--effort', $Effort) }
        if ($Passthru -contains '--model') { claude @effortArgs @Passthru }
        else { claude --model $row.id @effortArgs @Passthru }
    } finally {
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
        $env:ANTHROPIC_BASE_URL   = $oldBase
        $env:ANTHROPIC_AUTH_TOKEN = $oldTok
        $env:ANTHROPIC_API_KEY    = $oldKey
        $env:ANTHROPIC_SMALL_FAST_MODEL = $oldFast
        $env:CLAUDE_CODE_SUBAGENT_MODEL = $oldSub
        $env:CLAUDE_CODE_ENTRYPOINT     = $oldEntry
        $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = $oldCtxV
        $env:CLAUDE_CODE_TRANSPORT = $oldTransport
        $env:HARNESS_SESSION_EFFORT = $oldPpEffort
    }
}

function claude-gpt-high  { Invoke-ClaudeGpt -Effort 'high'   -Passthru $args }
function claude-gpt-low   { Invoke-ClaudeGpt -Effort 'low'    -Passthru $args }
function claude-gpt       { Invoke-ClaudeGpt -Effort 'medium' -Passthru $args }
function claude-gpt-terra { Invoke-ClaudeGpt -Model 'terra' -Effort 'medium' -Passthru $args }
function claude-gpt-sol   { Invoke-ClaudeGpt -Model 'sol'   -Effort 'medium' -Passthru $args }
# astra is dispatch-excluded (owner decision, plan quota) but session-launchable -- see the
# subagent-fallback note in Invoke-ClaudeGpt above. Unpinned subagent spawns from this session
# land on luna, never astra.
function claude-gpt-astra { Invoke-ClaudeGpt -Model 'astra' -Effort 'medium' -Passthru $args }

# --------------------------------------------------------------------------
# OpenCode Zen FREE-tier sessions (https://opencode.ai/zen):
#
#     claude-opencode            muse-spark-1.3-contributor-free (1M ctx, gated)
#
# Only `muse` is selectable as of 2026-09-03 — pickle/mimo/hy3/nemotron-ultra/
# lightning/laguna are registered but owner-excluded (usage limits too low,
# tested directly); x-preview-f-free/hy3 were removed (gone from Zen's
# catalog). muse is Contributor-Free, not anonymous-free: it needs the real
# OPENCODE_API_KEY in ~/.env.ai-worker.cmd (public bearer 500s on it) and
# trains on submissions by design — benchmark-only prompts, never real
# {{PROJECT_NAME}} material. Zero marginal cost either way (Zen's own $0 cost
# field). Same per-session LiteLLM proxy lifecycle as the GPT block, and the
# same reason it is forced: the model pin lives in the config file read at
# proxy startup. Unlike the Codex route there is NO alias surface — a
# mid-session /model to another id 400s at the proxy ('No deployments
# available') rather than silently rerouting; relaunch with a different
# alias instead.
#
# Mechanism, auth shape and privacy caveat: .claude/scripts/opencode_sidecar.sh header.
# --------------------------------------------------------------------------

function Get-OpencodeAliases {
    # Live roster query for the opencode FREE tier - never a hardcoded list here,
    # because free models churn (refresh via .claude/scripts/opencode_refresh_models.py).
    try {
        $registry = Join-Path $PSScriptRoot "..\reference\external_models.json"
        $data = Get-Content $registry -Raw | ConvertFrom-Json
        @($data.models | Where-Object { $_.transport -eq 'opencode' } | ForEach-Object { $_.alias })
    } catch { @() }
}

function Invoke-ClaudeOpenCode {
    param(
        [string]$Alias = 'muse',
        # Fast companion for BACKGROUND traffic (Bash auto-mode safety classification,
        # small summarization calls). Driving it on the main model means a saturated
        # free tier blocks EVERY Bash call behind a classifier timeout (measured
        # 2026-08-22). Interactive sessions get a second proxy deployment instead;
        # the benchmark sidecar deliberately does NOT - its records must stay
        # comparable with the sealed arms, which ran one model end to end.
        # Defaults to the same alias as -Alias (muse is the only live row as of
        # 2026-09-03): the companion.id -eq model.id check below collapses this to one
        # deployment automatically. Pass a second live alias explicitly once one exists.
        [string]$SmallFast = 'muse',
        [string[]]$Passthru = @()
    )

    $root  = Join-Path $PSScriptRoot ".."
    $model = Get-ExternalModel -Alias $Alias
    if (-not $model -or $model.transport -ne 'opencode') {
        $known = Get-OpencodeAliases
        Write-Error "opencode model '$Alias' not in .claude/reference/external_models.json (available: $($known -join ' | ')). Refresh stale rosters with scripts/opencode_refresh_models.py."; return
    }
    $companion = Get-ExternalModel -Alias $SmallFast
    if (-not $companion -or $companion.transport -ne 'opencode') { $companion = $model }
    if ($companion.id -eq $model.id) { $deployments = @($model) } else { $deployments = @($model, $companion) }

    $litellm = $env:OC_LITELLM_BIN
    if (-not $litellm -or -not (Test-Path $litellm)) { $litellm = "$HOME\.local\bin\litellm.exe" }
    if (-not (Test-Path $litellm)) {
        $onPath = Get-Command litellm -ErrorAction SilentlyContinue
        if ($onPath) { $litellm = $onPath.Source } else { Write-Error "litellm not installed (uv tool install 'litellm[proxy]'; set OC_LITELLM_BIN)"; return }
    }

    # Free tier rides `public`; a populated OPENCODE_API_KEY in the shared env file wins.
    $key = 'public'
    $envFile = "$HOME\.env.ai-worker.cmd"
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*set\s+OPENCODE_API_KEY=' | Select-Object -First 1
        if ($line) {
            $k = ($line.Line -replace '^\s*set\s+OPENCODE_API_KEY=', '').Trim().Trim('"')
            if ($k -and $k -ne '<redacted>' -and $k -ne 'your-key-here') { $key = $k }
        }
    }

    # Saturation preflight: a free model that cannot answer a tiny ping inside 15s will
    # time out every auto-mode safety classification (Bash/Workflow) once the session
    # starts - measured 2026-08-22. Warn at launch, where picking another alias is cheap.
    try {
        $ping = @{ model = $model.id; messages = @(@{role='user'; content='Reply with the single word OK.'}); max_tokens = 16 } | ConvertTo-Json -Depth 4
        $null = Invoke-RestMethod -Uri 'https://opencode.ai/zen/v1/chat/completions' -Method Post -Body $ping -ContentType 'application/json' -Headers @{Authorization = "Bearer $key"} -TimeoutSec 15
    } catch {
        Write-Host "    WARNING: $($model.id) failed a 15s saturation preflight ($($_.Exception.Message))." -ForegroundColor Yellow
        Write-Host "    Expect tool-permission classifier timeouts; consider: claude-opencode <other-alias>." -ForegroundColor Yellow
    }

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()

    $stateDir = "$HOME\.local\state\litellm-opencode"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $cfg = Join-Path $stateDir "interactive-$port.yaml"
    $yaml = @('model_list:')
    foreach ($m in $deployments) {
        $yaml += @(
            "  - model_name: $($m.id)"
            '    litellm_params:'
            "      model: openai/$($m.id)"
            '      api_base: https://opencode.ai/zen/v1'
            "      api_key: $key"
        )
    }
    # INTERACTIVE-ONLY alias surface. Harness agents spawn with Anthropic-tier names
    # (measured 2026-08-22: explore-fanout lenses requested claude-sonnet-5 -> instant
    # 400 at the no-alias proxy). Tier names route to the DRIVEN model here; nothing
    # records servedModel identity in an interactive session, so the ambiguity is free.
    # The benchmark sidecar keeps strict single-deployment configs and must not grow this.
    foreach ($tier in @('claude-sonnet-5', 'claude-haiku-4-5', 'claude-opus-4-6',
                        'sonnet', 'haiku', 'opus')) {
        if ($tier -ne $model.id -and $tier -ne $companion.id) {
            $yaml += @(
                "  - model_name: $tier"
                '    litellm_params:'
                "      model: openai/$($model.id)"
                '      api_base: https://opencode.ai/zen/v1'
                "      api_key: $key"
            )
        }
    }
    $yaml += @('', 'litellm_settings:', '  drop_params: true')
    Set-Content -Path $cfg -Encoding utf8 -Value $yaml
    $log = Join-Path $stateDir 'interactive-serve.log'

    $oldUtf8 = $env:PYTHONUTF8; $env:PYTHONUTF8 = '1'
    $proxy = Start-Process -FilePath $litellm -ArgumentList @('--config', $cfg, '--port', "$port", '--host', '127.0.0.1') `
                           -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    $env:PYTHONUTF8 = $oldUtf8

    # /health/liveliness is litellm's path (not /healthz); cold start measured ~12-15s.
    $healthy = $false
    foreach ($i in 1..90) {
        Start-Sleep -Milliseconds 500
        try { Invoke-WebRequest -Uri "http://127.0.0.1:$port/health/liveliness" -TimeoutSec 2 -UseBasicParsing | Out-Null; $healthy = $true; break } catch {}
    }
    if (-not $healthy) {
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
        Write-Error "litellm did not become healthy on :$port within 45s - see $log"
        return
    }

    $ctx = & python (Join-Path $root "tools\model_registry.py") context-window $Alias 2>$null

    $oldBase  = $env:ANTHROPIC_BASE_URL
    $oldTok   = $env:ANTHROPIC_AUTH_TOKEN
    $oldKey   = $env:ANTHROPIC_API_KEY
    $oldFast  = $env:ANTHROPIC_SMALL_FAST_MODEL
    $oldSub   = $env:CLAUDE_CODE_SUBAGENT_MODEL
    $oldEntry = $env:CLAUDE_CODE_ENTRYPOINT
    $oldNonEss= $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
    $oldCtxV  = $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS
    $env:ANTHROPIC_BASE_URL   = "http://127.0.0.1:$port"
    $env:ANTHROPIC_AUTH_TOKEN = "unused"
    $env:ANTHROPIC_API_KEY    = ""
    $env:CLAUDE_CODE_ENTRYPOINT = "cli"
    $env:ANTHROPIC_SMALL_FAST_MODEL = $companion.id
    $env:CLAUDE_CODE_SUBAGENT_MODEL = $model.id
    $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
    if ($ctx) { $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = "$ctx" }

    Write-Host ""
    Write-Host "  OpenCode session - driving model: " -NoNewline -ForegroundColor Cyan
    Write-Host "$($model.id)" -NoNewline -ForegroundColor White
    Write-Host "  [FREE tier]" -ForegroundColor DarkGray
    Write-Host "    cost       `$0 marginal (Zen free tier); context $ctx tokens declared" -ForegroundColor DarkGray
    Write-Host "    effort     unmeasured on this tier - `-e is a passthrough request, not evidence" -ForegroundColor DarkGray
    if ($companion.id -ne $model.id) {
        Write-Host "    background Bash-classifier/summary calls -> $($companion.id) (fast companion)" -ForegroundColor DarkGray
    }
    Write-Host "    /model mid-session 400s at the proxy (no alias surface) - relaunch with an alias instead." -ForegroundColor DarkYellow
    Write-Host "    Privacy: some free ids may train on submissions during their free period (Zen docs)." -ForegroundColor DarkYellow
    Write-Host "    proxy pid $($proxy.Id) on :$port - killed when this session exits." -ForegroundColor DarkGray
    Write-Host ""

    try {
        if ($Passthru -contains '--model') { claude @Passthru }
        else { claude --model $model.id @Passthru }
    } finally {
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
        Remove-Item $cfg -ErrorAction SilentlyContinue
        $env:ANTHROPIC_BASE_URL   = $oldBase
        $env:ANTHROPIC_AUTH_TOKEN = $oldTok
        $env:ANTHROPIC_API_KEY    = $oldKey
        $env:ANTHROPIC_SMALL_FAST_MODEL = $oldFast
        $env:CLAUDE_CODE_SUBAGENT_MODEL = $oldSub
        $env:CLAUDE_CODE_ENTRYPOINT     = $oldEntry
        $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = $oldNonEss
        $env:CLAUDE_CODE_MAX_CONTEXT_TOKENS = $oldCtxV
    }
}

# First bare word selects the model (default muse); everything else passes to claude.
# Background/classifier companion defaults to the same alias; override with -SmallFast
# <alias> once a second live row exists. Manual scan rather than positional params:
# PowerShell fills open positionals from dash-tokens too, so `claude-opencode muse
# --resume X` would bind --resume as the second positional and corrupt what reaches
# claude (mock-tested 2026-08-22).
#   claude-opencode                          muse-spark-1.3-contributor-free
#   claude-opencode muse --resume X          same model, resuming session X
function claude-opencode {
    $alias = 'muse'; $bg = 'muse'; $seen = $false
    $rest = New-Object System.Collections.Generic.List[string]
    $tokens = @($args)
    for ($i = 0; $i -lt $tokens.Count; $i++) {
        $t = "$($tokens[$i])"
        # NB: not `switch` - its `continue` targets the switch, not this loop.
        if ($t -eq '-SmallFast' -or $t -eq '-b') { $i++; $bg = "$($tokens[$i])" }
        elseif (-not $seen -and $t -notlike '-*') { $alias = $t; $seen = $true }
        else { $rest.Add($t) }
    }
    Invoke-ClaudeOpenCode -Alias $alias -SmallFast $bg -Passthru $rest.ToArray()
}

Register-ArgumentCompleter -CommandName claude-opencode -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete)
    Get-OpencodeAliases | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}
