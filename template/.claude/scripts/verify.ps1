<#
.SYNOPSIS
  Scope-sized test verification. The instrument for every slice and every Part close.

.DESCRIPTION
  Two verbs govern testing in this repo:

      verify   after every slice and at every Part close   (this script)
      gate     once, at the drive close, with no flags     (regression_gate.ps1)

  `verify` expands one or more DOMAIN names into the Logic filter and Integration
  segments that domain owns, then runs them after the gate's static guards
  (StaticGuards.ps1, ~15 s; skipped by -RetryOnly). It is deliberately not a gate: no
  preflight, no build gate, no DOCS check, no baseline ratchet,
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

.PARAMETER RetryOnly
  Rerun only the integration batches the last run of this scope left non-green (a
  red, hung or locked batch); its green batches are kept. Pair with -IntegrationOnly
  to skip the Logic tier.

.PARAMETER SelfTest
  Namespace/folder parity checks over gate_domain_map.json. Exits 1 on any failure.

.EXAMPLE
  pwsh -NoProfile -File .claude/scripts/verify.ps1 -Scope NPCs,AI
  pwsh -NoProfile -File .claude/scripts/verify.ps1 -Filter "FullyQualifiedName~Tests.Integration.<Domain>.<SuiteName>"
#>
[CmdletBinding(DefaultParameterSetName = 'Scope')]
param(
    [string] $Repo = '',
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
    [switch] $RetryOnly,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [int] $TimeoutMs,

    [Parameter(ParameterSetName = 'Scope')]
    [Parameter(ParameterSetName = 'Filter')]
    [switch] $IgnoreEditor,
    # Print the checkout these suites would build in (ROUTE=<path>) and exit 0 before any run.
    [switch] $RouteOnly,

    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch] $SelfTest
)

$ErrorActionPreference = 'Stop'
$scripts = $PSScriptRoot
. (Join-Path $scripts 'WideRunGuard.ps1')
Assert-WideRunAllowed -Entry 'verify.ps1' -Repo $Repo -Filter $Filter -Scope $Scope -Flags @(@('SelfTest', 'RouteOnly') | Where-Object { $PSBoundParameters.ContainsKey($_) })
. (Join-Path $scripts 'GodotProcess.ps1')
. (Join-Path $scripts 'StaticGuards.ps1')
$repo = Resolve-TestRepo -Repo $Repo -ScriptsRoot $scripts
. (Join-Path $scripts 'TwinCheckout.ps1')
# A live checkout's suites build in its twin, which both children then receive as -Repo.
if (-not $IgnoreEditor -and -not $SelfTest) {
    $route = Resolve-RunCheckout -Repo $repo -ReleaseLock
    if (-not $route.Failed) { $repo = $route.Path }
}
if ($RouteOnly) { Write-Output "ROUTE=$repo"; exit 0 }
$mapPath = Join-Path $repo '.claude/scripts/gate_domain_map.json'

if (-not (Test-Path $mapPath)) { Write-Output "VERIFY=BLOCKED reason=gate_domain_map.json missing at $mapPath"; exit 4 }
$map        = Get-Content $mapPath -Raw | ConvertFrom-Json
$domainKeys = @($map.domains.PSObject.Properties.Name | Where-Object { $_ })
$mergeKeys  = @($map.merges.PSObject.Properties.Name | Where-Object { $_ })

# A domain's logic_partition is one name or a list. Each is a Tests.Logic.<name> substring filter, so it
# names a namespace folder or the prefix of root-level test classes.
function Get-LogicPartitions {
    param([string] $Domain)
    @($map.domains.$Domain.logic_partition | Where-Object { $_ })
}
function Test-LogicPartition {
    param([string] $Name)
    $logicRoot = Join-Path $repo 'Tests\Logic'
    if ((Test-Path (Join-Path $logicRoot $Name)) -or (Get-ChildItem -Path $logicRoot -Filter "$Name*.cs" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) { return $true }
    # A namespace may differ from its folder.
    [bool] (Get-ChildItem -Path $logicRoot -Filter '*.cs' -File -Recurse | Select-String -Pattern ('^\s*namespace\s+[\w.]*Tests\.Logic\.' + [regex]::Escape($Name)) -List | Select-Object -First 1)
}

# ---------------------------------------------------------------- -SelfTest
if ($SelfTest) {
    $script:SelfFail = $false
    function Assert {
        param([bool] $Cond, [string] $Msg)
        if ($Cond) { Write-Output "  ok: $Msg" } else { Write-Output "  FAIL: $Msg"; $script:SelfFail = $true }
    }
    Write-Output '=== gate_domain_map.json self-test ==='

    # every integration_segments entry resolves to a real folder, or to a root-level test class file
    foreach ($dn in $domainKeys) {
        foreach ($seg in @($map.domains.$dn.integration_segments)) {
            $full = Join-Path $repo ($seg -replace '/', '\')
            $isDir = (Test-Path $full) -and (Get-Item $full).PSIsContainer
            Assert ($isDir -or (Test-Path "$full.cs")) "domain '$dn' segment '$seg' resolves to a real folder or root test class"
        }
    }

    # every logic partition matches >=1 test class in namespace Tests.Logic.<name>
    foreach ($dn in $domainKeys) {
        foreach ($lp in (Get-LogicPartitions $dn)) {
            Assert (Test-LogicPartition $lp) "domain '$dn' logic_partition '$lp' has >=1 suite in namespace Tests.Logic.$lp"
        }
    }

    # reverse coverage: every test class is selected by some domain or named in excluded_namespaces.
    # Selection mirrors the real filters: FullyQualifiedName~Tests.Logic.<partition> and
    # FullyQualifiedName~Tests.Integration.<segment>. (the trailing dot pins the segment).
    $logicParts = @($domainKeys | ForEach-Object { Get-LogicPartitions $_ })
    $intSegs    = @($domainKeys | ForEach-Object { @($map.domains.$_.integration_segments) } | ForEach-Object { $_.TrimEnd('/') -split '/' | Select-Object -Last 1 })
    $excluded   = @(if ($map.PSObject.Properties['excluded_namespaces']) { $map.excluded_namespaces.PSObject.Properties.Name })
    $unmapped   = [System.Collections.Generic.SortedSet[string]]::new()
    foreach ($tier in 'Logic', 'Integration') {
        $root = Join-Path $repo "Tests\$tier"
        foreach ($cs in Get-ChildItem -Path $root -Filter '*.cs' -File -Recurse) {
            $src = Get-Content $cs.FullName -Raw
            if ($src -notmatch '\[(TestSuite|TestCase)\b') { continue }
            $nsm = [regex]::Match($src, '(?m)^\s*namespace\s+([\w.]+)')
            if (-not $nsm.Success) { continue }
            $ns = $nsm.Groups[1].Value -replace '^{{PROJECT_NAMESPACE}}\.', ''
            $classes = @([regex]::Matches($src, '(?m)^(?:\w+\s+)*?class\s+(\w+)') | ForEach-Object { $_.Groups[1].Value })
            if ($classes.Count -eq 0) { $classes = @([regex]::Match($src, '\bclass\s+(\w+)').Groups[1].Value) }
            $prefix = "Tests.$tier."
            foreach ($c in $classes) {
                $fqn = "$ns.$c"
                $hit = if ($tier -eq 'Logic') { $logicParts | Where-Object { $fqn.Contains("$prefix$_") } }
                       else { $intSegs | Where-Object { "$fqn.".Contains("$prefix$_.") } }
                if ($hit) { continue }
                if ($excluded | Where-Object { $fqn -eq $_ -or $fqn.StartsWith("$_.", [System.StringComparison]::Ordinal) }) { continue }
                # A namespace deeper than the tier names the namespace; a root-level class names itself.
                [void] $unmapped.Add($(if ($ns -eq "Tests.$tier") { $fqn } else { $ns }))
            }
        }
    }
    foreach ($u in $unmapped) { Assert $false "test namespace '$u' maps to no domain (add it to a domain's logic_partition / integration_segments, or name it in excluded_namespaces)" }
    Assert ($unmapped.Count -eq 0) "every test namespace maps to a domain or a named exclusion"

    # mutual non-subsumption: a prefix pair must be declared in 'merges', else one
    # domain's filter silently drags in another's tests.
    $domNames = @($domainKeys | Where-Object { Get-LogicPartitions $_ })
    foreach ($a in $domNames) {
        foreach ($b in $domNames) {
            if ($a -eq $b) { continue }
            foreach ($pa in (Get-LogicPartitions $a)) { foreach ($pb in (Get-LogicPartitions $b)) {
                if ($pb.StartsWith($pa, [System.StringComparison]::Ordinal) -and $pb.Length -gt $pa.Length) {
                    $declared = $mergeKeys -contains $b
                    Assert $declared "prefix pair '$a'/'$b' (partitions '$pa'/'$pb'): '$b' subsumed by '$a' and $(if ($declared) { 'declared in merges' } else { 'NOT declared' })"
                }
            } }
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
        $lps = @(Get-LogicPartitions $canon | Where-Object { Test-LogicPartition $_ })
        if ($lps.Count -gt 0) {
            foreach ($lp in $lps) { $logicParts += "FullyQualifiedName~Tests.Logic.$lp" }
        } else {
            $gaps += "$canon(no-logic-partition)"
        }
        $segs = @($map.domains.$canon.integration_segments)
        foreach ($seg in $segs) {
            $bn = ((($seg -replace '\\', '/').TrimEnd('/') -split '/')[-1]) -replace '\.cs$', ''
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

# ---------------------------------------------------------------- static guards
# The gate's guard set, so a guard red surfaces here and not first at the drive-close gate. -RetryOnly
# continues a run whose guards already passed.
if (-not $RetryOnly) {
    $py = Get-PythonCommand
    if (-not $py) { Write-Output 'VERIFY=BLOCKED reason=no python interpreter on PATH; the static guards cannot run'; exit 4 }
    $guardRun = Invoke-StaticGuards -Repo $repo -Hooks (Join-Path (Split-Path $scripts) 'hooks') -Py $py
    Write-Output "GUARDS $($guardRun.Results -join ' ')"
    if ($guardRun.Unavailable -or $guardRun.Blocked.Count -gt 0) {
        $guardRun.Details | ForEach-Object { Write-Output $_ }
        Write-Output "VERIFY=BLOCKED scope=$scopeLabel reason=static guard $(if ($guardRun.Unavailable) { 'unavailable' } else { "failed: $($guardRun.Blocked -join ', ')" })"
        exit 4
    }
}

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
    $argv = @('-NoProfile', '-File', $Script, '-Repo', $repo) + $ScriptArgs
    & pwsh @argv | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

if (-not $IntegrationOnly -and $logicFilter) {
    $a = @('-Filter', $logicFilter, '-Label', "verify-$scopeLabel")
    if ($PSBoundParameters.ContainsKey('TimeoutMs')) { $a += @('-TimeoutMs', "$TimeoutMs") }
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
    if ($RetryOnly) { $a += '-RetryOnly' }
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
