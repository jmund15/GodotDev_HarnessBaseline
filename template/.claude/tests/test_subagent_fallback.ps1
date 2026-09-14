# Proof for Get-SubagentFallback in scripts/claude_profile_functions.ps1.
#
# It decides which model an UNPINNED subagent spawn runs on. Getting it wrong is silent: the spawn
# succeeds either way, and the only symptom is quota drawn from a model the roster excluded. So the
# load-bearing cases are the planted ones — a clean roster exercises none of the logic.
#
# Run: pwsh -File .claude/tests/test_subagent_fallback.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here "..\scripts\claude_profile_functions.ps1")

$registry = Join-Path $here "..\reference\external_models.json"
$data = Get-Content $registry -Raw | ConvertFrom-Json
function Row([string]$alias) { $data.models | Where-Object { $_.alias -eq $alias } | Select-Object -First 1 }

# Live-roster preconditions. If these stop holding the roster changed, and the assertions below are
# asserting nothing — a proof that silently stops covering its subject is the failure mode here.
$astra = Row 'astra'; $luna = Row 'luna'; $flash = Row 'flash'
$pre = @(
    @{ n = "astra is row-level excluded (the case that needs a fallback)"
       t = { $astra.status.state -eq 'unavailable' } }
    @{ n = "luna is dispatchable (the case that needs none)"
       t = { -not $luna.status -or $luna.status.state -ne 'unavailable' } }
    @{ n = "flash is excluded at its TRANSPORT, not its row -- the check a row-only test misses"
       t = { (-not $flash.status -or $flash.status.state -ne 'unavailable') -and
             $data.transports.deepseek.status.state -eq 'unavailable' } }
)

$cases = @(
    @{ n = "an excluded driver returns some other row, never `$null"
       t = { $r = Get-SubagentFallback -Row $astra; $null -ne $r } }

    @{ n = "...and that row is itself dispatchable"
       t = { $r = Get-SubagentFallback -Row $astra
             $null -ne $r -and (-not $r.status -or $r.status.state -ne 'unavailable') } }

    @{ n = "...and it is never the excluded driver itself"
       t = { $r = Get-SubagentFallback -Row $astra; $r.id -ne $astra.id } }

    @{ n = "...and it stays on the driver's own transport"
       t = { $r = Get-SubagentFallback -Row $astra; $r.transport -eq $astra.transport } }

    @{ n = "a dispatchable driver returns `$null -- spawns stay on it"
       t = { $null -eq (Get-SubagentFallback -Row $luna) } }

    # PLANTED: the old code took a hardcoded alias with no availability test at all. A -Prefer whose
    # TRANSPORT is dead must be refused, or the banner names a model that cannot answer.
    @{ n = "-Prefer pointing at a transport-excluded row is REFUSED, not honoured"
       t = { $r = Get-SubagentFallback -Row $astra -Prefer 'flash'
             $null -ne $r -and $r.id -ne $flash.id } }

    @{ n = "-Prefer pointing at a dispatchable row IS honoured"
       t = { $r = Get-SubagentFallback -Row $astra -Prefer 'luna'; $r.id -eq $luna.id } }

    @{ n = "-Prefer naming nothing in the registry falls through to a real sibling"
       t = { $r = Get-SubagentFallback -Row $astra -Prefer 'no-such-alias'
             $null -ne $r -and $r.transport -eq $astra.transport } }

    # A row this registry does not describe cannot be checked, and there is no sibling to move to
    # either. It must return `$null (leave the driver alone) rather than throwing on the missing
    # transport entry, which would kill the launch banner for a session that is otherwise fine.
    @{ n = "a row on an unregistered transport returns `$null instead of throwing"
       t = { $null -eq (Get-SubagentFallback -Row ([pscustomobject]@{
                 id = 'x'; alias = 'x'; transport = 'no-such-transport' })) } }

    # --- Get-TransportModelIds: what the banner offers as `/model` targets -------------------
    # It answers a different question from the fallback above, so it must NOT reuse that filter:
    # dispatch exclusions gate delegation, never which model may DRIVE a session.
    @{ n = "the /model id list includes a DISPATCH-excluded row (astra can still drive)"
       t = { (Get-TransportModelIds -Transport 'codex') -contains $astra.id } }

    @{ n = "...and lists dispatchable siblings alongside it"
       t = { (Get-TransportModelIds -Transport 'codex') -contains $luna.id } }

    @{ n = "the list is ids, never aliases -- an alias is not what /model accepts"
       t = { (Get-TransportModelIds -Transport 'codex') -notcontains 'luna' } }

    @{ n = "it never crosses transports"
       t = { (Get-TransportModelIds -Transport 'codex') -notcontains 'claude-opus-5' } }

    @{ n = "an unknown transport yields an empty list, not `$null or a throw"
       t = { @(Get-TransportModelIds -Transport 'no-such-transport').Count -eq 0 } }
)

$failed = 0
foreach ($c in ($pre + $cases)) {
    try { $ok = [bool](& $c.t); $detail = "" }
    catch { $ok = $false; $detail = "  threw: $($_.Exception.Message)" }
    if (-not $ok) { $failed++ }
    Write-Host ("{0} {1}{2}" -f $(if ($ok) { "ok  " } else { "FAIL" }), $c.n, $detail)
}
Write-Host ""
Write-Host ("{0}/{1} passed" -f (($pre.Count + $cases.Count) - $failed), ($pre.Count + $cases.Count))
exit $(if ($failed) { 1 } else { 0 })
