<#
.SYNOPSIS
  Scope-sized test verification. The instrument for every slice and every Part close.

.DESCRIPTION
  Two verbs govern testing in this repo:

      verify   after every slice and at every Part close   (this script)
      gate     once, at the drive close, with no flags     (regression_gate.ps1)

  `verify` expands one or more DOMAIN names into the Logic filter and Integration
  segments that domain owns, then runs them. It is deliberately not a gate: no
  preflight, no static guards, no build gate, no DOCS check, no baseline ratchet,
  no queue, no ledger, no reuse, no cadence marker. It prints counts and exits.

  A verify result NEVER backs a commit. That is the gate's job, and the reason this
  script records nothing: a narrow verdict that could be reused or ratcheted is a
  narrow verdict that can be mistaken for a gate, which is exactly the confusion the
  deleted -Smoke/-SmokeImport/-Targeted tiers produced (measured 2026-08-20: 0 uses
  of -Targeted in 69 gate runs, while -SmokeImport -- "the full gate minus 21
  seconds" -- was chosen 11 times by agents who believed they had bought a cheap check).

  Domain names are the same vocabulary the retired -Targeted flag took; they live in
  gate_domain_map.json's `domains` (with `merges` resolving subsumed aliases).

.PARAMETER Scope
  One or more domain names, comma- or space-separated: -Scope NPCs,AI

.PARAMETER Filter
  Raw vstest filter, passed through untouched. The escape hatch for a scope the
  domain map does not name (one test, one class, one namespace below a domain).

.PARAMETER LogicOnly / IntegrationOnly
  Run one tier instead of both.

.PARAMETER SelfTest
  Namespace/folder parity checks over gate_domain_map.json. Exits 1 on any failure.

.EXAMPLE
  pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope NPCs,AI
  pwsh -NoProfile -File .claude/scripts/verify.ps1 -Filter "FullyQualifiedName~Tests.Integration.<Domain>.<SuiteName>"
#>
[CmdletBinding(DefaultParameterSetName = 'Scope')]
param(
    [Parameter(ParameterSetName = 'Scope', Position = 0)]
    [string[]] $Scope,

    [Parameter(ParameterSetName = 'Filter', Mandatory = $true)]
    [string] $Filter,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [switch] $LogicOnly,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [switch] $IntegrationOnly,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [int] $TimeoutMs = 480000,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [switch] $IgnoreEditor,

    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch] $SelfTest
)

$ErrorActionPreference = 'Stop'
$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo    = Split-Path -Parent (Split-Path -Parent $scripts)
$mapPath = Join-Path $scripts 'gate_domain_map.json'

if (-not (Test-Path $mapPath)) { Write-Output "VERIFY=BLOCKED reason=gate_domain_map.json missing at $mapPath"; exit 4 }
$map        = Get-Content $mapPath -Raw | ConvertFrom-Json
$domainKeys = @($map.domains.PSObject.Properties.Name)
$mergeKeys  = @($map.merges.PSObject.Properties.Name)

# ---------------------------------------------------------------- -SelfTest
if ($SelfTest) {
    $script:SelfFail = $false
    function Assert {
        param([bool] $Cond, [string] $Msg)
        if ($Cond) { Write-Output "  ok: $Msg" } else { Write-Output "  FAIL: $Msg"; $script:SelfFail = $true }
    }
    Write-Output '=== gate_domain_map.json self-test ==='

    # every integration_segments entry resolves to a real folder
    foreach ($dn in $domainKeys) {
        foreach ($seg in @($map.domains.$dn.integration_segments)) {
            $full = Join-Path $repo ($seg -replace '/', '\')
            Assert ((Test-Path $full) -and ((Get-Item $full).PSIsContainer)) "domain '$dn' segment '$seg' resolves to a real folder"
        }
    }

    # namespace<->folder parity: each logic_partition matches >=1 concrete suite
    foreach ($dn in $domainKeys) {
        $lp = $map.domains.$dn.logic_partition
        if (-not $lp) { continue }
        $dir = Join-Path $repo "Tests\Logic\$lp"
        if (-not (Test-Path $dir)) { Assert $false "domain '$dn' logic_partition '$lp' folder missing on disk"; continue }
        $found = $false
        $pat = 'Tests\.Logic\.' + [regex]::Escape($lp) + '(?![A-Za-z0-9_])'
        foreach ($cs in @(Get-ChildItem -Path $dir -Filter '*.cs' -Recurse -ErrorAction SilentlyContinue)) {
            $c = Get-Content $cs.FullName -Raw -ErrorAction SilentlyContinue
            if ($c -match $pat) { $found = $true; break }
        }
        Assert $found "domain '$dn' logic_partition '$lp' has >=1 suite in namespace Tests.Logic.$lp"
    }

    # mutual non-subsumption: a prefix pair must be declared in 'merges', else one
    # domain's filter silently drags in another's tests.
    $domNames = @($domainKeys | Where-Object { $map.domains.$_.logic_partition })
    foreach ($a in $domNames) {
        $fa = "Tests.Logic.$a"
        foreach ($b in $domNames) {
            if ($a -eq $b) { continue }
            $fb = "Tests.Logic.$b"
            if ($fb.StartsWith($fa, [System.StringComparison]::Ordinal) -and $fb.Length -gt $fa.Length) {
                $declared = $mergeKeys -contains $b
                Assert $declared "prefix pair '$a'/'$b': '$b' subsumed by '$a' and $(if ($declared) { 'declared in merges' } else { 'NOT declared' })"
            }
        }
    }
    foreach ($mk in $mergeKeys) {
        $canon = [string]$map.merges.$mk
        Assert ($domainKeys -contains $canon) "merges key '$mk' -> canonical '$canon' is a real domain"
        Assert ($mk.StartsWith($canon, [System.StringComparison]::Ordinal) -and $mk.Length -gt $canon.Length) "merges key '$mk' is genuinely prefixed by '$canon'"
    }

    if ($script:SelfFail) { Write-Output 'SELFTEST=FAIL'; exit 1 }
    Write-Output 'SELFTEST=PASS'
    exit 0
}

# ---------------------------------------------------------------- scope -> filters
$logicFilter = $null
$onlySegs    = @()
$gaps        = @()
$scopeLabel  = 'filter'

if ($PSCmdlet.ParameterSetName -eq 'Scope') {
    if (-not $Scope -or $Scope.Count -eq 0) {
        Write-Output 'VERIFY=BLOCKED reason=no -Scope given. Domains:'
        Write-Output ('  ' + (($domainKeys | Sort-Object) -join ' '))
        Write-Output '  (or pass -Filter "<raw vstest filter>")'
        exit 4
    }
    # -File binding does not comma-split arrays: '-Scope A,B' arrives as one element.
    $names = @($Scope | ForEach-Object { $_ -split '[,\s]+' } | Where-Object { $_.Trim() })

    $canonical = @()
    foreach ($d in $names) {
        $canon = if ($mergeKeys -contains $d) { [string]$map.merges.$d }
                 elseif ($domainKeys -contains $d) { $d }
                 else { $null }
        if (-not $canon) { $gaps += "$d(unknown)"; continue }
        if ($canonical -notcontains $canon) { $canonical += $canon }
    }
    if ($canonical.Count -eq 0) {
        Write-Output "VERIFY=BLOCKED reason=no domain resolved ($($gaps -join '; ')). Domains:"
        Write-Output ('  ' + (($domainKeys | Sort-Object) -join ' '))
        exit 4
    }

    $logicParts = @()
    foreach ($canon in ($canonical | Sort-Object)) {
        $lp = $map.domains.$canon.logic_partition
        if ($lp -and (Test-Path (Join-Path $repo "Tests\Logic\$lp"))) {
            $logicParts += "FullyQualifiedName~Tests.Logic.$lp"
        } else {
            $gaps += "$canon(no-logic-partition)"
        }
        $segs = @($map.domains.$canon.integration_segments)
        foreach ($seg in $segs) {
            $bn = (($seg -replace '\\', '/').TrimEnd('/') -split '/')[-1]
            if ($onlySegs -notcontains $bn) { $onlySegs += $bn }
        }
        if ($segs.Count -eq 0) { $gaps += "$canon(no-integration)" }
    }
    if ($logicParts.Count -gt 0) { $logicFilter = "($($logicParts -join '|'))" }
    $scopeLabel = ($canonical | Sort-Object) -join '+'
} else {
    $logicFilter = $Filter
    # A raw filter names its own tier; run it once through the suite runner and stop.
    $IntegrationOnly = $false
}

Write-Output "VERIFY scope=$scopeLabel"
if ($gaps.Count -gt 0) { Write-Output "  gaps: $($gaps -join ', ')  (declared, not silently skipped)" }

# ---------------------------------------------------------------- run
$suiteRunner = Join-Path $scripts 'run_test_suite.ps1'
$batchRunner = Join-Path $scripts 'run_integration_batched.ps1'
$exitCode    = 0
$ran         = 0
$blockedTiers = @()

function Invoke-Runner {
    param([string] $Script, [string[]] $ScriptArgs, [string] $Label)
    # Write-Host, not Write-Output: a function's success stream IS its return value, so a
    # banner written here would be captured into the caller's $rc alongside the exit code
    # and make every green run compare non-zero.
    Write-Host ''
    Write-Host "--- $Label ---"
    $argv = @('-NoProfile', '-File', $Script) + $ScriptArgs
    & pwsh @argv | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

if (-not $IntegrationOnly -and $logicFilter) {
    $a = @('-Filter', $logicFilter, '-Label', "verify-$scopeLabel", '-TimeoutMs', "$TimeoutMs")
    if ($IgnoreEditor) { $a += '-IgnoreEditor' }
    $rc = Invoke-Runner $suiteRunner $a "logic  $scopeLabel"
    $ran++
    if ($rc -ne 0) { $exitCode = $rc }
}

if (-not $LogicOnly -and $PSCmdlet.ParameterSetName -eq 'Scope' -and $onlySegs.Count -gt 0) {
    # Comma-JOINED, deliberately: `pwsh -File` binds one literal per parameter, so passing the
    # domains as separate elements makes the second one bind positionally to the next parameter
    # (measured: `Movement` landed on -TargetBatchSec and hard-failed). The callee splits this
    # string back into domains — that split is what makes the filter resolve at all.
    $a = @('-Only', ($onlySegs -join ','))
    if ($IgnoreEditor) { $a += '-IgnoreEditor' }
    $rc = Invoke-Runner $batchRunner $a "integration  $($onlySegs -join ',')"
    $ran++
    # 4 = the filter matched no segment. That is an unrunnable scope, not a failing test.
    if ($rc -eq 4) { $blockedTiers += "integration ($($onlySegs -join ',')) matched no test segment" }
    elseif ($rc -ne 0) { $exitCode = $rc }
}

Write-Output ''
if ($ran -eq 0) {
    Write-Output "VERIFY=BLOCKED reason=scope '$scopeLabel' resolved to no runnable tier ($($gaps -join ', '))"
    exit 4
}
# A tier that was asked to run and selected nothing cannot back a PASS: the scope the caller named
# went unverified, and reporting green over it is indistinguishable from having verified it.
if ($blockedTiers.Count -gt 0) {
    Write-Output "VERIFY=BLOCKED scope=$scopeLabel reason=$($blockedTiers -join '; ')"
    exit 4
}
Write-Output "VERIFY=$(if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }) scope=$scopeLabel tiers=$ran"
Write-Output 'A verify result does not back a commit; the drive-close gate does.'
exit $exitCode
