#requires -Version 7
<#
.SYNOPSIS
  Re-runnable proof for scripts/run_integration_batched.ps1's `adaptation.json`
  `test_quarantine_filter` seam (Design Doc §8) and its plan-derived wall-clock budget.

  Three shapes: adaptation.json absent -> the default (empty string, so `$quarantine` adds no
  filter); present with a valid string -> that value is read; a wrong-typed value (not a
  string) -> the default plus one stderr line. Also proves the wiring itself: the script's
  `$quarantine =` assignment actually calls `Get-AdaptationValue`, not a literal.

  `Get-AdaptationValue` is extracted verbatim from the real script (never retyped by hand) and
  run as a real child process per case, so this proof reads the actual stderr stream a
  consumer would see -- not `-ErrorVariable`, which also collects the engine's own internal
  (and already-caught) exception records alongside the function's one intentional message.

  Budget: the batcher has no whole-run budget; a batch's own hang cap is the only time bound.

  Unmeasured segments: a segment with no manifest entry gets a batch of its own, never packed beside another segment, and
  its batch's hang cap is the cost ceiling (also for its HANG retry); a fully measured batch keeps 60 s + 3x its expected
  seconds, floored at 120 s.

    pwsh -NoProfile -File .claude/tests/test_run_integration_batched_adaptation.ps1
#>

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$scriptPath = Join-Path $here '..\scripts\run_integration_batched.ps1'
$src = Get-Content $scriptPath -Raw
# The batcher's unit building, plan, state, manifest, budget, retry selection and diagnosis live in IntegrationBatches.ps1.
$ibPath = Join-Path $here '..\scripts\IntegrationBatches.ps1'
$ibSrc = if (Test-Path $ibPath) { Get-Content $ibPath -Raw } else { '' }

# --- wiring: $quarantine is assigned FROM Get-AdaptationValue, not a literal --------------
$wiringOk = $src -match [regex]::Escape('$quarantine = Get-AdaptationValue -Repo $repo -Key ''test_quarantine_filter''')

# --- extract Get-AdaptationValue verbatim --------------------------------------------------
$funcMatch = [regex]::Match($ibSrc, '(?s)function Get-AdaptationValue \{.*?\n\}\r?\n')
if (-not $funcMatch.Success) {
    Write-Host "FAIL could not extract Get-AdaptationValue from IntegrationBatches.ps1"
    exit 1
}
$funcText = $funcMatch.Value

function New-ScratchRepo {
    # $Seed is intentionally untyped: a `[string]` parameter coerces a passed $null into "",
    # which would create an empty (and therefore malformed) adaptation.json for the "absent
    # file" case instead of no file at all.
    param($Seed)
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ribp_repo_" + [System.Guid]::NewGuid().ToString('N').Substring(0,8))
    $skillDir = Join-Path $tmp '.claude\skills\project_subsystems'
    New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
    if ($null -ne $Seed) {
        Set-Content -Path (Join-Path $skillDir 'adaptation.json') -Value $Seed -NoNewline -Encoding utf8
    }
    return $tmp
}

function Invoke-InChildProcess {
    # Runs Get-AdaptationValue in a fresh pwsh process against $Repo, exactly as a consumer
    # would see it: stdout carries the value, stderr carries whatever the function itself
    # writes -- and nothing else.
    param([string] $Repo, [string] $Key, [string] $Default)
    $wrapper = Join-Path ([System.IO.Path]::GetTempPath()) ("ribp_wrap_" + [System.Guid]::NewGuid().ToString('N').Substring(0,8) + ".ps1")
    $body = @"
$funcText
Write-Output (Get-AdaptationValue -Repo '$Repo' -Key '$Key' -Default '$Default')
"@
    Set-Content -Path $wrapper -Value $body -Encoding utf8
    try {
        $stdout = & pwsh -NoProfile -File $wrapper 2>(Join-Path ([System.IO.Path]::GetTempPath()) "ribp_err.txt")
        $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) "ribp_err.txt"
        $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { '' }
        if (Test-Path $stderrPath) { Remove-Item -Force $stderrPath }
        return @{ stdout = ($stdout -join "`n").Trim(); stderr = if ($stderr) { $stderr.Trim() } else { '' } }
    } finally {
        Remove-Item -Force $wrapper -ErrorAction SilentlyContinue
    }
}

$cases = @()
$scratches = @()

try {
    # --- absent: default empty string, no stderr -------------------------------------------
    $repo = New-ScratchRepo -Seed $null
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'absent adaptation.json: default empty string'; ok = ($r.stdout -eq '') }
    $cases += @{ label = 'absent adaptation.json: no stderr'; ok = ($r.stderr -eq '') }

    # --- present: a valid string is read ---------------------------------------------------
    $repo = New-ScratchRepo -Seed '{"test_quarantine_filter": "FullyQualifiedName!~SomeSuite"}'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'present adaptation.json: project value is read'; ok = ($r.stdout -eq 'FullyQualifiedName!~SomeSuite') }
    $cases += @{ label = 'present adaptation.json: no stderr on a valid value'; ok = ($r.stderr -eq '') }

    # --- wrong-typed: not a string -> default + one stderr line -----------------------------
    $repo = New-ScratchRepo -Seed '{"test_quarantine_filter": ["not", "a", "string"]}'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default ''
    $cases += @{ label = 'wrong-typed test_quarantine_filter: falls back to default'; ok = ($r.stdout -eq '') }
    $cases += @{ label = 'wrong-typed test_quarantine_filter: one stderr line names the key';
                 ok = (($r.stderr -split "`n").Count -eq 1 -and $r.stderr -match 'test_quarantine_filter') }

    # --- unparseable file: default + one stderr line ----------------------------------------
    $repo = New-ScratchRepo -Seed '{not json'
    $scratches += $repo
    $r = Invoke-InChildProcess -Repo $repo -Key 'test_quarantine_filter' -Default 'D'
    $cases += @{ label = 'unparseable adaptation.json: falls back to default'; ok = ($r.stdout -eq 'D') }
    $cases += @{ label = 'unparseable adaptation.json: one stderr line'; ok = (($r.stderr -split "`n").Count -eq 1 -and $r.stderr -ne '') }

    $cases += @{ label = '$quarantine is wired to Get-AdaptationValue, not a literal'; ok = $wiringOk }
} finally {
    foreach ($r in $scratches) {
        if (Test-Path $r) { Remove-Item -Recurse -Force $r -Confirm:$false }
    }
}

# --- no whole-run budget --------------------------------------------------------------------
# A batch's hang cap bounds a wedge and the gate's pool has no whole-run budget; a budget sized from measured
# reservations skipped healthy batches whenever a peer's suites slowed the run (1.3-2.5x their expected time).
$cases += @{ label = 'no whole-run budget: the batcher has no budget parameter, derivation or skip'
             ok = ($src -notmatch 'TotalBudgetMs|BudgetFloorMs|BudgetMargin|SKIPPED_BUDGET') }
$cases += @{ label = 'no whole-run budget: the library has no budget function, status or exit line'
             ok = ($ibSrc -notmatch 'Get-TotalBudgetMs|SKIPPED_BUDGET|BUDGET_EXCEEDED') }
$cases += @{ label = 'no whole-run budget: the diagnosis names no budget skip or margin'
             ok = ((Get-Content -Raw (Join-Path $here '../scripts/batch_diagnosis.ps1')) -notmatch 'SKIPPED_BUDGET|BudgetMargin|Get-TotalBudgetMs') }

# --- RetryOnly: prior greens are bound to the tree digest ---------------------------------
$restoreMatch = [regex]::Match($ibSrc, '(?s)function Restore-PriorGreens \{.*?
\}
?
')
$cases += @{ label = 'retry-only: Restore-PriorGreens is extractable from IntegrationBatches.ps1'; ok = $restoreMatch.Success }
$cases += @{ label = 'retry-only: the runner derives its digest through TreeDigest.ps1 Get-TreeDigest (no -TreeDigest parameter) and Save-BatchState records it'
             ok = ($src -match 'Get-TreeDigest -Repo \$repo' -and $src -notmatch '\[string\] \$TreeDigest' -and
                   $src -match [regex]::Escape('Restore-PriorGreens -Plan $plan -StatePath $statePath -TreeDigest $runDigest') -and $ibSrc -match '(?m)^\s+treeDigest\s+= \$TreeDigest') }
function Invoke-Restore {
    param([string] $StateJson, [string] $Digest)
    $tag = [System.Guid]::NewGuid().ToString('N').Substring(0, 8)
    $wrapper = Join-Path ([System.IO.Path]::GetTempPath()) "ribr_$tag.ps1"
    $state = Join-Path ([System.IO.Path]::GetTempPath()) "ribr_$tag.json"
    if ($StateJson) { Set-Content -Path $state -Value $StateJson -Encoding utf8 }
    Set-Content -Path $wrapper -Encoding utf8 -Value @"
Set-StrictMode -Version Latest
$($restoreMatch.Value)
`$plan = @([pscustomobject]@{ label = 'B1'; segs = @('A'); status = 'PENDING'; passed = 0 }, [pscustomobject]@{ label = 'B2'; segs = @('C'); status = 'PENDING'; passed = 0 })
Restore-PriorGreens -Plan `$plan -StatePath '$state' -TreeDigest '$Digest'
Write-Output ('STATUS=' + (`$plan.status -join ','))
"@
    try { return (& pwsh -NoProfile -File $wrapper) -join "`n" }
    finally { Remove-Item -Force $wrapper, $state -ErrorAction SilentlyContinue }
}
if ($restoreMatch.Success) {
    $greens = '{"treeDigest":"d1","batches":[{"label":"B1","segs":["A"],"status":"GREEN","passed":5},{"label":"B2","segs":["C"],"status":"RED","passed":0}]}'
    $old    = '{"batches":[{"label":"B1","segs":["A"],"status":"GREEN","passed":5}]}'
    $r = Invoke-Restore -StateJson $greens -Digest 'd1'
    $cases += @{ label = 'retry-only: an equal digest keeps the prior green'; ok = ($r -match 'STATUS=GREEN,PENDING' -and $r -notmatch 'RETRY_ONLY_IGNORED') }
    $r = Invoke-Restore -StateJson $greens -Digest 'd2'
    $cases += @{ label = 'retry-only: a different digest keeps none'; ok = ($r -match 'RETRY_ONLY_IGNORED reason=digest-mismatch' -and $r -match 'STATUS=PENDING,PENDING') }
    $r = Invoke-Restore -StateJson $greens -Digest ''
    $cases += @{ label = 'retry-only: an empty digest keeps none'; ok = ($r -match 'RETRY_ONLY_IGNORED reason=no-digest' -and $r -match 'STATUS=PENDING,PENDING') }
    $r = Invoke-Restore -StateJson $old -Digest 'd1'
    $cases += @{ label = 'retry-only: an old state file without treeDigest keeps none and does not throw under StrictMode'; ok = ($r -match 'RETRY_ONLY_IGNORED reason=digest-mismatch' -and $r -match 'STATUS=PENDING,PENDING') }
    $r = Invoke-Restore -StateJson '' -Digest 'd1'
    $cases += @{ label = 'retry-only: no state file keeps none'; ok = ($r -match 'RETRY_ONLY_IGNORED reason=no-state') }
}

# --- run dirs: New-RunDir / Remove-OldRunDirs, extracted verbatim from RunPool.ps1 ----------
$poolPath = Join-Path $here '..\scripts\RunPool.ps1'
$poolSrc = if (Test-Path $poolPath) { Get-Content $poolPath -Raw } else { '' }
$newDirMatch = [regex]::Match($poolSrc, '(?s)function New-RunDir \{.*?\n\}\r?\n')
$oldDirsMatch = [regex]::Match($poolSrc, '(?s)function Remove-OldRunDirs \{.*?\n\}\r?\n')
$cases += @{ label = 'run dirs: New-RunDir and Remove-OldRunDirs are extractable from RunPool.ps1'; ok = ($newDirMatch.Success -and $oldDirsMatch.Success) }
if ($newDirMatch.Success -and $oldDirsMatch.Success) {
    $slots = (Join-Path $here '..\scripts\RuntimeSlots.ps1')
    $tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ribrd_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    $wrapper = Join-Path ([System.IO.Path]::GetTempPath()) ("ribrd_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    # -WindowStyle exists only on Windows; Start-Process on Linux rejects it.
    $hide = if ([System.Environment]::OSVersion.Platform -eq 'Win32NT') { @{ WindowStyle = 'Hidden' } } else { @{} }
    $foreign = Start-Process -FilePath 'pwsh' -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 120') -PassThru @hide
    $gone = Start-Process -FilePath 'pwsh' -ArgumentList @('-NoProfile', '-Command', 'exit 0') -PassThru @hide
    $gone.WaitForExit()
    try {
        Set-Content -Path $wrapper -Encoding utf8 -Value @"
Set-StrictMode -Version Latest
. '$slots'
$($newDirMatch.Value)
$($oldDirsMatch.Value)
`$root = '$tmpRoot'
`$deadPid = $($gone.Id)
`$deadStart = (Get-Date).AddMinutes(-5).ToUniversalTime().ToString('o')
`$foreignPid = $($foreign.Id)
`$foreignStart = Get-ProcStartUtc (Get-Process -Id `$foreignPid)
function Set-Stamp(`$dir, `$procId, `$start) { [pscustomobject]@{ pid = `$procId; procStart = `$start } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path `$dir 'owner.json') -Encoding utf8 }
function Add-Dead(`$id, `$ageMin) {
    `$d = New-RunDir -RunsRoot `$root -RunId `$id
    Set-Stamp `$d `$deadPid `$deadStart
    (Get-Item -LiteralPath `$d).CreationTimeUtc = (Get-Date).ToUniversalTime().AddMinutes(-`$ageMin)
    `$d
}
`$first = New-RunDir -RunsRoot `$root -RunId 'gate-fresh'
`$stamp = Get-Content -LiteralPath (Join-Path `$first 'owner.json') -Raw | ConvertFrom-Json
Write-Output ('CREATED=' + [int](`$first -eq (Join-Path `$root 'gate-fresh') -and (Test-Path `$first)))
Write-Output ('STAMP=' + [int](`$stamp.pid -eq `$PID -and `$stamp.procStart -eq (Get-ProcStartUtc (Get-Process -Id `$PID))))
Set-Stamp `$first `$foreignPid `$foreignStart
Set-Content -LiteralPath (Join-Path `$first 'kept.trx') -Value x
`$null = New-RunDir -RunsRoot `$root -RunId 'gate-fresh'
Write-Output ('KEEPSTAMP=' + [int](((Get-Content -LiteralPath (Join-Path `$first 'owner.json') -Raw | ConvertFrom-Json).pid -eq `$foreignPid) -and (Test-Path (Join-Path `$first 'kept.trx'))))
Set-Stamp `$first `$deadPid `$deadStart
Set-Content -LiteralPath (Join-Path `$first 'stale.trx') -Value x
`$null = New-RunDir -RunsRoot `$root -RunId 'gate-fresh'
Write-Output ('RESTAMP=' + [int](((Get-Content -LiteralPath (Join-Path `$first 'owner.json') -Raw | ConvertFrom-Json).pid -eq `$PID) -and -not (Test-Path (Join-Path `$first 'stale.trx'))))
Remove-Item -LiteralPath `$first -Recurse -Force
foreach (`$n in 1..4) { `$null = Add-Dead "gate-g`$n" (100 - `$n) }
foreach (`$n in 1..3) { `$null = Add-Dead "verify-v`$n" (100 - `$n) }
`$null = Add-Dead 'other-x' 500
`$live = New-RunDir -RunsRoot `$root -RunId 'gate-live'
Set-Stamp `$live `$foreignPid `$foreignStart
(Get-Item -LiteralPath `$live).CreationTimeUtc = (Get-Date).ToUniversalTime().AddMinutes(-900)
`$reused = New-RunDir -RunsRoot `$root -RunId 'gate-reused'
Set-Stamp `$reused `$foreignPid '2001-01-01T00:00:00.0000000Z'
(Get-Item -LiteralPath `$reused).CreationTimeUtc = (Get-Date).ToUniversalTime().AddMinutes(-901)
Remove-OldRunDirs -RunsRoot `$root
Write-Output ('LEFT=' + ((Get-ChildItem -LiteralPath `$root -Directory | Sort-Object Name | ForEach-Object Name) -join ','))
"@
        $out = (& pwsh -NoProfile -File $wrapper 2>&1) -join "`n"
    } finally {
        Remove-Item -Force $wrapper -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue
        Stop-Process -Id $foreign.Id -Force -ErrorAction SilentlyContinue
    }
    $left = if ($out -match 'LEFT=(.*)') { $Matches[1].Trim() } else { "no LEFT line: $out" }
    $cases += @{ label = 'run dirs: New-RunDir creates <root>/<runId>'; ok = ($out -match 'CREATED=1') }
    $cases += @{ label = 'run dirs: New-RunDir stamps the owner pid and procStart'; ok = ($out -match 'STAMP=1') }
    $cases += @{ label = 'run dirs: a second New-RunDir for the same runId keeps a live owner''s stamp and contents'; ok = ($out -match 'KEEPSTAMP=1') }
    $cases += @{ label = 'run dirs: New-RunDir over a dead owner''s dir restamps it and drops the prior attempt''s files'; ok = ($out -match 'RESTAMP=1') }
    $cases += @{ label = "run dirs: retention keeps the newest 2 per owner kind, keeps a live foreign owner, drops a reused pid and spares other names (left: $left)"
                 ok = ($left -eq 'gate-g3,gate-g4,gate-live,other-x,verify-v2,verify-v3') }
}

# --- stdout contract: FAILED_TEST unchanged, FAILED_MSG added, both pass through the batcher ---
$passMatch = [regex]::Match($src, '\$passThroughPattern = ''([^'']+)''')
$cases += @{ label = 'contract: the batcher pass-through pattern is a named variable'; ok = $passMatch.Success }
if ($passMatch.Success) {
    $pat = $passMatch.Groups[1].Value
    $cases += @{ label = 'contract: FAILED_TEST=<fqn> still passes through'; ok = ('FAILED_TEST={{PROJECT_NAME}}.Tests.Integration.A.B.C' -match $pat) }
    $cases += @{ label = 'contract: FAILED_MSG=<fqn> :: <message> passes through'; ok = ('FAILED_MSG={{PROJECT_NAME}}.Tests.Integration.A.B.C :: Expected 1 but was 2' -match $pat) }
    $cases += @{ label = 'contract: SLOT= and OVERLAP_SIGNATURE= still pass through'; ok = ('SLOT=0/4 source=x' -match $pat -and 'OVERLAP_SIGNATURE=k attempt=1' -match $pat) }
    $cases += @{ label = 'contract: LOG= and STATUS= lines do not pass through'; ok = ('LOG=x' -notmatch $pat -and 'STATUS=DONE  label=x' -notmatch $pat) }
}
$cases += @{ label = 'contract: the batcher prints TRX_DIR=<runsRoot> through the verdict lines'
             ok = ($ibSrc -match [regex]::Escape('$lines.Add("TRX_DIR=$RunsRoot")') -and $src -match '-RunsRoot \$runsRoot') }
$cases += @{ label = 'contract: a standalone batcher mints a verify- run id and runs retention only for a run dir it minted'
             ok = ($src -match [regex]::Escape("'verify-{0}'") -and $src -match 'Remove-OldRunDirs' -and $src -match 'PSBoundParameters\.ContainsKey\(''RunId''\)') }
$cases += @{ label = 'contract: every batch and retry runs through Invoke-RunPool at width 1 under -ResultsRoot $runsRoot, launched by Start-SuiteChild'
             ok = (([regex]::Matches($src, 'Invoke-RunPool -Units @\(\$u\) -ResultsRoot \$runsRoot -MaxParallel 1 -Launch \$launch')).Count -eq 1 -and
                   $src -match 'Invoke-BatchUnit \$unitByLabel\[\$p\.label\]' -and $src -match 'Invoke-BatchUnit \$u\b' -and
                   $src -match '\$launch = \{ param\(\$x\) Start-SuiteChild -Unit \$x -ResultsRoot \$runsRoot \}' -and $src -notmatch '& pwsh -NoProfile -File \$wrapper') }
$gateSrc = Get-Content (Join-Path $here '..\scripts\regression_gate.ps1') -Raw
# The gate's pool children run under this one dir (behaviour: test_regression_gate_held.ps1 G1); the gate no longer invokes the batcher.
$cases += @{ label = 'gate: mints gate-<runId>, passes every pool child that dir as -ResultsRoot, and prints TRX_DIR'
             ok = ($gateSrc -match 'New-RunDir -RunsRoot \$runsParent -RunId "gate-\$runId"' -and $gateSrc -match '\$gpRunDir = \$script:RunDir' -and
                   $gateSrc -match 'Start-SuiteChild -Unit \$u -ResultsRoot \$gpRunDir -Held' -and $gateSrc -match 'TRX_DIR=' -and $gateSrc -notmatch 'run_integration_batched\.ps1''') }
$cases += @{ label = 'gate: joins each FAILED_TEST with its FAILED_MSG in the detail'; ok = ($gateSrc -match 'FAILED_MSG=') }


# --- A6: IntegrationBatches.ps1 owns units, plan/state/manifest writes, retry selection and diagnosis ------------------
function Invoke-IbChild {
    # Dot-sources the shipped IntegrationBatches.ps1 into a fresh pwsh, after $Prelude (which may shadow a cmdlet with a
    # counting function), then runs $Body. Returns stdout joined.
    param([string] $Prelude = '', [string] $Body)
    $f = Join-Path ([System.IO.Path]::GetTempPath()) ("ribib_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8) + '.ps1')
    Set-Content -Path $f -Encoding utf8 -Value @"
`$ErrorActionPreference = 'Stop'
$Prelude
. '$ibPath'
$Body
"@
    try { return ((& pwsh -NoProfile -File $f 2>&1) -join "`n") } finally { Remove-Item -Force $f -ErrorAction SilentlyContinue }
}

$planJson = '[{"label":"Integration_B1","segs":["A"],"expectedSec":50,"filter":"(FullyQualifiedName~Tests.Integration.A.)&","status":"GREEN","passed":4,"failed":0,"elapsed":0,"workWall":0},' +
            '{"label":"Integration_B2","segs":["B"],"expectedSec":40,"filter":"(FullyQualifiedName~Tests.Integration.B.)&","status":"PENDING","passed":0,"failed":0,"elapsed":0,"workWall":0},' +
            '{"label":"Integration_B3","segs":["C"],"expectedSec":30,"filter":"(FullyQualifiedName~Tests.Integration.C.)&","status":"RED","passed":0,"failed":0,"elapsed":0,"workWall":0}]'
if ($ibSrc) {
    $out = Invoke-IbChild -Body @"
`$plan = @('$planJson' | ConvertFrom-Json)
`$units = @(Get-IntegrationUnits -Plan `$plan -CostCeilingMs 300000)
Write-Output ('UNITS=' + ((`$units | ForEach-Object { `$_.label + ':' + `$_.expectSec + ':' + `$_.timeoutMs + ':' + [int][bool]`$_.filter }) -join ','))
"@
    $cases += @{ label = "A6 units: built from the non-green plan rows only, each with label, filter, timeoutMs and expectSec ($out)"
                 ok = ($out -match 'UNITS=Integration_B2:40:180000:1,Integration_B3:30:150000:1$') }

    # The hang cap and the reservation read one basis: a batch whose recorded wall outgrew its unit sum keeps a cap above that wall.
    $out = Invoke-IbChild -Body @"
`$plan = @('$planJson' | ConvertFrom-Json)
`$units = @(Get-IntegrationUnits -Plan `$plan -CostCeilingMs 2000000 -BatchWall @{ Integration_B2 = 245.3 })
`$plan[1].status = 'HANG'
`$retry = @(Select-RetryUnits -Plan `$plan -CostCeilingMs 2000000 -BatchWall @{ Integration_B2 = 245.3 })
Write-Output ('CAPS=' + ((@(`$units) + @(`$retry) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
"@
    $cases += @{ label = "A6 units: the cap follows the larger of the unit sum and the recorded batch wall, as the reservation does ($out)"
                 ok = ($out -match 'CAPS=Integration_B2:795900,Integration_B3:150000,Integration_B2_retry:795900$') }

    # The ceiling bounds a wedge's cost but never sits under twice a unit's measured time: a 216 s unit under a 300 s ceiling gets 432 s.
    $out = Invoke-IbChild -Body @"
`$units = @(Get-IntegrationUnits -Plan @('[{"label":"Integration_B1","segs":["Floors"],"expectedSec":216,"filter":"f","status":"PENDING"},{"label":"Integration_B2","segs":["B"],"expectedSec":40,"filter":"g","status":"PENDING"}]' | ConvertFrom-Json) -CostCeilingMs 300000)
Write-Output ('CEIL=' + ((`$units | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
"@
    $cases += @{ label = "A6 units: a measured unit past the ceiling keeps twice its time; a small one keeps 60 s + 3x ($out)"
                 ok = ($out -match 'CEIL=Integration_B1:432000,Integration_B2:180000$') }

    # An unmeasured segment is a guess (3 s): it gets a batch of its own, whose hang cap is the cost ceiling. Measured segments pack as before.
    $out = Invoke-IbChild -Body @"
`$man = [pscustomobject]@{ seconds = @{ A = 10.0 }; tests = @{ A = 1 }; batchWall = @{}; counters = @{} }
`$big = [pscustomobject]@{ seconds = @{ A = 40.0 }; tests = @{ A = 1 }; batchWall = @{}; counters = @{} }
`$two = [pscustomobject]@{ seconds = @{ A = 10.0; B = 20.0 }; tests = @{ A = 1; B = 1 }; batchWall = @{}; counters = @{} }
`$three = [pscustomobject]@{ seconds = @{ A = 10.0; B = 20.0; C = 30.0 }; tests = @{ A = 1; B = 1; C = 1 }; batchWall = @{}; counters = @{} }
`$unmeasured = @(New-IntegrationPlan -Segments @('X', 'Y') -Manifest `$man -TargetBatchSec 60 -MaxBatchTests 380)
`$mixed = @(New-IntegrationPlan -Segments @('A', 'X') -Manifest `$man -TargetBatchSec 60 -MaxBatchTests 380)
`$small = @(New-IntegrationPlan -Segments @('A', 'B', 'X') -Manifest `$two -TargetBatchSec 60 -MaxBatchTests 380)
`$measured = @(New-IntegrationPlan -Segments @('A') -Manifest `$man -TargetBatchSec 60 -MaxBatchTests 380)
`$measuredBig = @(New-IntegrationPlan -Segments @('A') -Manifest `$big -TargetBatchSec 60 -MaxBatchTests 380)
`$allMeasured = @(New-IntegrationPlan -Segments @('A', 'B', 'C') -Manifest `$three -TargetBatchSec 45 -MaxBatchTests 380)
Write-Output ('UNMEAS=' + ((@(Get-IntegrationUnits -Plan `$unmeasured -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
Write-Output ('MIXED=' + ((@(Get-IntegrationUnits -Plan `$mixed -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
Write-Output ('SMALL=' + ((@(Get-IntegrationUnits -Plan `$small -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
Write-Output ('MEAS=' + ((@(Get-IntegrationUnits -Plan `$measured -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
Write-Output ('MEASBIG=' + ((@(Get-IntegrationUnits -Plan `$measuredBig -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
Write-Output ('ALLM=' + ((@(Get-IntegrationUnits -Plan `$allMeasured -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
`$hung = @(`$unmeasured | ForEach-Object { `$_.status = 'HANG'; `$_ })
Write-Output ('RETRYU=' + ((@(Select-RetryUnits -Plan `$hung -CostCeilingMs 300000) | ForEach-Object { `$_.label + ':' + `$_.timeoutMs }) -join ','))
"@
    $cases += @{ label = "A6 unmeasured: two unmeasured segments get two batches, each with the cost ceiling, not one batch ($out)"
                 ok = ($out -match '(?m)^UNMEAS=Integration_B1:300000,Integration_B2:300000$') }
    $cases += @{ label = 'A6 unmeasured: a measured segment never shares a batch with an unmeasured one (its batch keeps 120 s)'
                 ok = ($out -match '(?m)^MIXED=Integration_B1:120000,Integration_B2:300000$') }
    $cases += @{ label = 'A6 unmeasured: measured segments that would fit beside an unmeasured one stay out of its batch'
                 ok = ($out -match '(?m)^SMALL=Integration_B1:150000,Integration_B2:300000$') }
    $cases += @{ label = 'A6 unmeasured: a fully measured small batch keeps 120 s'
                 ok = ($out -match '(?m)^MEAS=Integration_B1:120000$') }
    $cases += @{ label = 'A6 unmeasured: a fully measured batch past 120 s keeps 60 s + 3x its expected seconds'
                 ok = ($out -match '(?m)^MEASBIG=Integration_B1:180000$') }
    $cases += @{ label = 'A6 unmeasured: an all-measured plan packs first-fit as before (B1 holds C and A, B2 holds B)'
                 ok = ($out -match '(?m)^ALLM=Integration_B1:180000,Integration_B2:120000$') }
    $cases += @{ label = 'A6 unmeasured: a HANG retry of each unmeasured batch gets the cost ceiling'
                 ok = ($out -match '(?m)^RETRYU=Integration_B1_retry:300000,Integration_B2_retry:300000$') }

    # Results handed back out of order: one plan update, one state write and one manifest write, all from the parent.
    $tmpState = Join-Path ([System.IO.Path]::GetTempPath()) ("ribst_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmpState | Out-Null
    try {
        $trxA = Join-Path $tmpState 'a.trx'; $trxC = Join-Path $tmpState 'c.trx'
        Set-Content -Path $trxA -Encoding utf8 -Value '<?xml version="1.0"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results><UnitTestResult testName="{{PROJECT_NAME}}.Tests.Integration.B.T1" duration="00:00:02.0000000" outcome="Passed" /></Results></TestRun>'
        Set-Content -Path $trxC -Encoding utf8 -Value '<?xml version="1.0"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results><UnitTestResult testName="{{PROJECT_NAME}}.Tests.Integration.C.T1" duration="00:00:03.0000000" outcome="Passed" /></Results></TestRun>'
        $manifest = Join-Path $tmpState 'durations.json'
        Set-Content -Path $manifest -Encoding utf8 -Value '{"schema_version":3,"units":{"A":{"seconds":50,"tests":4}},"batch_wall":{"Integration_B2":{"wall":40,"consecutive_failures":1}}}'
        $state = Join-Path $tmpState 'state.json'
        $prelude = @'
$script:Writes = @{}
function Set-Content {
    [CmdletBinding()] param([Parameter(ValueFromPipeline)] $Value, [string] $Path, [string] $LiteralPath, [string] $Encoding, [switch] $NoNewline)
    begin { $buf = [System.Collections.Generic.List[object]]::new() }
    process { $buf.Add($Value) }
    end {
        $p = if ($LiteralPath) { $LiteralPath } else { $Path }
        $script:Writes[$p] = 1 + $(if ($script:Writes.ContainsKey($p)) { $script:Writes[$p] } else { 0 })
        Microsoft.PowerShell.Management\Set-Content -LiteralPath $p -Value ($buf -join "`n") -Encoding utf8
    }
}
'@
        $out = Invoke-IbChild -Prelude $prelude -Body @"
`$plan = @('$planJson' | ConvertFrom-Json)
`$results = @(
    [pscustomobject]@{ label = 'Integration_B3'; status = 'GREEN'; passed = 9; failed = 0; exit = 0; trx = '$trxC'; elapsedSec = 12.5; lockWaitMs = 500; outPath = '' },
    [pscustomobject]@{ label = 'Integration_B2'; status = 'GREEN'; passed = 7; failed = 0; exit = 0; trx = '$trxA'; elapsedSec = 20.0; lockWaitMs = 0; outPath = '' })
Update-IntegrationPlan -Plan `$plan -Results `$results
Save-BatchState -StatePath '$state' -Plan `$plan -TargetBatchSec 60 -TreeDigest 'd1'
`$m = Read-BatchManifest -Path '$manifest'
`$counters = Get-UpdatedCounters -Plan `$plan -Counters `$m.counters
`$fresh = Get-FreshDurations -Plan `$plan
Save-BatchManifest -Path '$manifest' -Plan `$plan -Counters `$counters -Fresh `$fresh -AllSegments @('A', 'B', 'C')
Write-Output ('ROWS=' + ((`$plan | ForEach-Object { `$_.label + ':' + `$_.status + ':' + `$_.passed + ':' + `$_.workWall }) -join ','))
Write-Output ('STATEW=' + `$script:Writes['$state'] + ' MANW=' + `$script:Writes['$manifest'])
`$mj = Get-Content -Raw '$manifest' | ConvertFrom-Json
Write-Output ('MAN=' + `$mj.units.B.seconds + ':' + `$mj.units.C.seconds + ':' + `$mj.batch_wall.Integration_B2.consecutive_failures + ':' + `$mj.batch_wall.Integration_B3.wall)
"@
    } finally { Remove-Item -Recurse -Force $tmpState -ErrorAction SilentlyContinue }
    $cases += @{ label = "A6 single writer: out-of-order results update each plan row by label ($($out -split "`n" | Select-String 'ROWS='))"
                 ok = ($out -match 'ROWS=Integration_B1:GREEN:4:0,Integration_B2:GREEN:7:20,Integration_B3:GREEN:9:12') }
    $cases += @{ label = "A6 single writer: one state write and one manifest write ($($out -split "`n" | Select-String 'STATEW='))"
                 ok = ($out -match 'STATEW=1 MANW=1') }
    $cases += @{ label = "A6 single writer: the one manifest write carries fresh durations, reset counters and work walls ($($out -split "`n" | Select-String 'MAN='))"
                 ok = ($out -match 'MAN=2:3:0:12') }
    # A green run whose durations cannot be measured says why instead of keeping the old ones in silence.
    $keptDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ribkept_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $keptDir | Out-Null
    try {
        $keptMan = Join-Path $keptDir 'durations.json'
        Set-Content -Path $keptMan -Encoding utf8 -Value '{"schema_version":3,"units":{"A":{"seconds":5,"tests":1}},"batch_wall":{}}'
        $out = Invoke-IbChild -Body @"
`$plan = @([pscustomobject]@{ label = 'Integration_B1'; status = 'GREEN'; ran = `$true; trx = '$keptDir\absent.trx'; workWall = 3 })
`$fresh = Get-FreshDurations -Plan `$plan
Write-Output ('KEPT=' + (Save-BatchManifest -Path '$keptMan' -Plan `$plan -Counters @{} -Fresh `$fresh -AllSegments @('A')))
"@
    } finally { Remove-Item -Recurse -Force $keptDir -ErrorAction SilentlyContinue }
    $cases += @{ label = "A6 manifest: a green run whose TRX is missing names it in a MANIFEST_KEPT line ($out)"
                 ok = ($out -match 'KEPT=MANIFEST_KEPT reason=Integration_B1:trx-missing') }
    # A manifest with no batch_wall key still returns the MANIFEST_KEPT reason.
    $noWallDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ribnowall_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $noWallDir | Out-Null
    try {
        $noWallMan = Join-Path $noWallDir 'durations.json'
        Set-Content -Path $noWallMan -Encoding utf8 -Value '{"schema_version":3,"units":{"A":{"seconds":5,"tests":1}}}'
        $out = Invoke-IbChild -Body @"
`$plan = @([pscustomobject]@{ label = 'Integration_B1'; status = 'GREEN'; ran = `$true; trx = '$noWallDir\absent.trx'; workWall = 3 })
`$fresh = Get-FreshDurations -Plan `$plan
Write-Output ('KEPT=' + (Save-BatchManifest -Path '$noWallMan' -Plan `$plan -Counters @{} -Fresh `$fresh -AllSegments @('A')))
"@
    } finally { Remove-Item -Recurse -Force $noWallDir -ErrorAction SilentlyContinue }
    $cases += @{ label = "A6 manifest: a manifest without batch_wall still returns its MANIFEST_KEPT line ($out)"
                 ok = ($out -match 'KEPT=MANIFEST_KEPT reason=Integration_B1:trx-missing') }
    $wrapperSrc = Get-Content (Join-Path $here '..\scripts\run_test_suite.ps1') -Raw
    $poolSrc2 = Get-Content (Join-Path $here '..\scripts\RunPool.ps1') -Raw
    $cases += @{ label = 'A6 single writer: children never write plan, state or manifest (no writer in the wrapper or the pool)'
                 ok = (-not (($wrapperSrc + $poolSrc2) -match 'integration_batches\.json|integration_batch_durations\.json|Save-BatchState|Save-BatchManifest')) }

    # Retry selection returns units and launches nothing.
    $prelude = @'
$script:Launches = 0
function Invoke-RunPool { $script:Launches++ }
function Start-SuiteChild { $script:Launches++ }
function Start-Process { $script:Launches++ }
'@
    $retryPlan = '[{"label":"Integration_B1","segs":["A"],"expectedSec":50,"filter":"f1","status":"RED"},{"label":"Integration_B2","segs":["B"],"expectedSec":40,"filter":"f2","status":"HANG"},' +
                 '{"label":"Integration_B3","segs":["C"],"expectedSec":30,"filter":"f3","status":"SILENT_SKIP"},{"label":"Integration_B4","segs":["D"],"expectedSec":20,"filter":"f4","status":"GREEN"},' +
                 '{"label":"Integration_B5","segs":["E"],"expectedSec":10,"filter":"f5","status":"LOCKED"}]'
    $out = Invoke-IbChild -Prelude $prelude -Body @"
`$units = @(Select-RetryUnits -Plan @('$retryPlan' | ConvertFrom-Json) -CostCeilingMs 300000)
Write-Output ('RETRY=' + ((`$units | ForEach-Object { `$_.label + '<' + `$_.of + ':' + `$_.filter }) -join ','))
Write-Output ('LAUNCHES=' + `$script:Launches)
"@
    $cases += @{ label = "A6 retry: a planted HANG and SILENT_SKIP unit are returned; RED, GREEN and LOCKED are not ($($out -split "`n" | Select-String 'RETRY='))"
                 ok = ($out -match '(?m)^RETRY=Integration_B2_retry<Integration_B2:f2,Integration_B3_retry<Integration_B3:f3$') }
    $cases += @{ label = 'A6 retry: selection launches nothing'; ok = ($out -match 'LAUNCHES=0') }
}
$poolPathR = Join-Path $here '..\scripts\RunPool.ps1'
$tagCmd = ". '$poolPathR'; `$a = Get-RunTag -Label 'Integration_B2'; `$b = Get-RunTag -Label 'Integration_B2_retry'; " +
    "`$da = Split-Path (Get-SuiteChildOutPath -ResultsRoot 'R' -Label 'Integration_B2'); `$db = Split-Path (Get-SuiteChildOutPath -ResultsRoot 'R' -Label 'Integration_B2_retry'); " +
    "Write-Output ('TAGS=' + [int](`$a -ne `$b) + ' DIRS=' + [int](`$da -ne `$db))"
$tagOut = (& pwsh -NoProfile -Command $tagCmd 2>&1) -join "`n"
$cases += @{ label = "A6 retry: a retry unit's derived tag and run dir differ from the original unit's ($tagOut)"; ok = ($tagOut -match 'TAGS=1 DIRS=1') }

# --- A6 end to end: the shipped batcher in a sandbox checkout whose run_test_suite.ps1 is a stub --------------------------
$sb = Join-Path ([System.IO.Path]::GetTempPath()) ("ribsb_" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
try {
    $sbScripts = Join-Path $sb 'repo\.claude\scripts'
    New-Item -ItemType Directory -Force -Path $sbScripts | Out-Null
    foreach ($n in 'run_integration_batched.ps1', 'IntegrationBatches.ps1', 'RunPool.ps1', 'RuntimeSlots.ps1', 'GodotProcess.ps1', 'TwinCheckout.ps1', 'CSharpSource.ps1', 'batch_diagnosis.ps1', 'TreeDigest.ps1') {
        $from = Join-Path $here "..\scripts\$n"
        if (Test-Path $from) { Copy-Item -LiteralPath $from -Destination $sbScripts }
    }
    foreach ($seg in 'Alpha', 'Beta', 'Gamma', 'Delta') { New-Item -ItemType Directory -Force -Path (Join-Path $sb "repo\Tests\Integration\$seg") | Out-Null }
    # Target 60 s: Alpha 50 -> B1, Beta 40 -> B2, Gamma 30 -> B3, Delta 20 joins B2 (60). Plan order B1, B2, B3 is not
    # longest-first (B2 is the longest), so a width-1 run in plan order shows the batcher kept its order.
    $sbManifest = '{"schema_version":3,"units":{"Alpha":{"seconds":50,"tests":2},"Beta":{"seconds":40,"tests":2},"Gamma":{"seconds":30,"tests":2},"Delta":{"seconds":20,"tests":2}},"batch_wall":{}}'
    Set-Content -Path (Join-Path $sb 'repo\Tests\integration_batch_durations.json') -Encoding utf8 -Value $sbManifest
    Set-Content -Path (Join-Path $sb 'repo\Tests\regression_baseline.json') -Encoding utf8 -Value '{"suites":{"Integration":{"passed":8}},"silent_skip_sentinels":{"Integration_min":1}}'
    $calls = Join-Path $sb 'calls.log'
    Set-Content -Path (Join-Path $sbScripts 'run_test_suite.ps1') -Encoding utf8 -Value @"
param([string] `$Filter, [string] `$Label, [int] `$TimeoutMs, [string] `$ResultsRoot, [switch] `$IgnoreEditor, [switch] `$HeldCheckout, [switch] `$RecompilePass, [switch] `$NoBuild)
Add-Content -LiteralPath '$calls' -Value (`$Label + '|' + ((`$PSBoundParameters.Keys | Sort-Object) -join ',') + '|' + (`$args -join ' '))
if (`$Label -eq 'Integration_B2') { Write-Output 'LOCKWAIT_MS=0'; Write-Output "STATUS=HANG  label=`$Label"; exit 124 }
`$segs = @([regex]::Matches(`$Filter, 'Tests\.Integration\.(\w+)\.') | ForEach-Object { `$_.Groups[1].Value })
`$dir = Join-Path `$ResultsRoot `$Label; New-Item -ItemType Directory -Force -Path `$dir | Out-Null
`$trx = Join-Path `$dir "`$Label.trx"
`$rows = (`$segs | ForEach-Object { '<UnitTestResult testName="{{PROJECT_NAME}}.Tests.Integration.' + `$_ + '.T1" duration="00:00:01.0000000" outcome="Passed" /><UnitTestResult testName="{{PROJECT_NAME}}.Tests.Integration.' + `$_ + '.T2" duration="00:00:01.0000000" outcome="Passed" />' }) -join ''
Set-Content -LiteralPath `$trx -Encoding utf8 -Value ('<?xml version="1.0"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>' + `$rows + '</Results></TestRun>')
`$n = 2 * `$segs.Count
Write-Output 'LOCKWAIT_MS=0'; Write-Output "TRX=`$trx"; Write-Output "STATUS=DONE  label=`$Label  exit=0  elapsed=0.2s  attempts=1"
Write-Output "Passed!  - Failed:     0, Passed:     `$n, Skipped:     0, Total:     `$n"
exit 0
"@
    # A git checkout, so the batcher can derive the tree digest -RetryOnly binds prior greens to.
    $sbRepo = Join-Path $sb 'repo'
    & git -C $sbRepo init -q 2>$null | Out-Null
    & git -C $sbRepo -c user.name=proof -c user.email=proof@example.invalid add -A 2>$null | Out-Null
    & git -C $sbRepo -c user.name=proof -c user.email=proof@example.invalid commit -q -m fixture 2>$null | Out-Null
    $sbOut = (& pwsh -NoProfile -File (Join-Path $sbScripts 'run_integration_batched.ps1') -Repo $sbRepo -IgnoreEditor 2>&1) -join "`n"
    $sbCode = $LASTEXITCODE
    $callLines = if (Test-Path $calls) { @(Get-Content $calls) } else { @() }
    $order = @($callLines | ForEach-Object { ($_ -split '\|')[0] }) -join ','
    $state = Join-Path $sb 'repo\.claude\scratch\test_runs\integration_batches.json'
    $stateRows = if (Test-Path $state) { ((Get-Content $state -Raw | ConvertFrom-Json).batches | ForEach-Object { "$($_.label):$($_.status)" }) -join ',' } else { 'no state' }
    $tail = ($sbOut -split "`n" | Select-Object -Last 6) -join ' / '
    # The green run rewrote the manifest with measured durations, which re-bins the plan; a run that leaves work for
    # -RetryOnly writes no fresh durations, so the retry sees the plan the state file was saved under.
    Set-Content -Path (Join-Path $sb 'repo\Tests\integration_batch_durations.json') -Encoding utf8 -Value $sbManifest
    $roOut = (& pwsh -NoProfile -File (Join-Path $sbScripts 'run_integration_batched.ps1') -Repo $sbRepo -IgnoreEditor -RetryOnly 2>&1) -join "`n"
    $roCode = $LASTEXITCODE
    $roCalls = if (Test-Path $calls) { @(Get-Content $calls).Count - $callLines.Count } else { -1 }
    $cases += @{ label = "A6 e2e: the standalone batcher finishes green through the pool (exit=$sbCode; $tail)"
                 ok = ($sbCode -eq 0 -and $sbOut -match 'COMPLETENESS=OK' -and $sbOut -match '(?m)^STATUS=DONE' -and $sbOut -match 'TOTAL passed=8 failed=0') }
    $cases += @{ label = "A6 e2e: width 1 keeps the plan order, then retries the HANG unit under its _retry label (order=$order)"
                 ok = ($order -eq 'Integration_B1,Integration_B2,Integration_B3,Integration_B2_retry') }
    $cases += @{ label = "A6 e2e: the retried unit ends GREEN in the state file ($stateRows)"
                 ok = ($stateRows -eq 'Integration_B1:GREEN,Integration_B2:GREEN,Integration_B3:GREEN') }
    $cases += @{ label = 'A6 e2e: a standalone run takes no held checkout and passes no --no-build; every child gets -IgnoreEditor and -ResultsRoot'
                 ok = ($callLines.Count -eq 4 -and -not ($callLines | Where-Object { $_ -match 'HeldCheckout|RecompilePass|NoBuild|no-build' }) -and
                       -not ($callLines | Where-Object { ($_ -split '\|')[1] -notmatch 'IgnoreEditor' -or ($_ -split '\|')[1] -notmatch 'ResultsRoot' })) }
    $cases += @{ label = 'A6 e2e: every child writes under TestResults/runs/verify-<timestamp>/ and the batcher prints that TRX_DIR'
                 ok = ($sbOut -match 'TRX_DIR=.*TestResults[\\/]runs[\\/]verify-\d{8}-\d{6}' -and (Test-Path (Join-Path (Join-Path (Join-Path $sb 'repo') 'TestResults') 'runs'))) }
    $roTail = ($roOut -split "`n" | Select-Object -Last 4) -join ' / '
    $cases += @{ label = "F14 standalone -RetryOnly on the unchanged tree keeps every prior green and launches nothing (exit=$roCode launched=$roCalls; $roTail)"
                 ok = ($roCode -eq 0 -and $roCalls -eq 0 -and $roOut -notmatch 'RETRY_ONLY_IGNORED' -and $roOut -match 'TOTAL passed=8 failed=0' -and $roOut -match 'COMPLETENESS=OK') }
    $cases += @{ label = 'A6 standalone: the batcher source takes no held checkout and derives no --no-build'
                 ok = ($src -notmatch 'Enter-HeldCheckout|-Held\b|--no-build|GDUNIT4_SKIP_RECOMPILE') }
} finally {
    Remove-Item -Recurse -Force $sb -ErrorAction SilentlyContinue
}
$cases += @{ label = 'A6 docs: the batcher header and SYNOPSIS no longer call batches serial'
             ok = ($src -notmatch '(?i)batches (are|stay) serial|serial (duration-balanced )?batches') }
$diagSrc = Get-Content (Join-Path $here '..\scripts\batch_diagnosis.ps1') -Raw
$cases += @{ label = 'A6 citations: batch_diagnosis.ps1 is loaded by IntegrationBatches.ps1'
             ok = ($diagSrc -notmatch 'BudgetMargin \(run_integration_batched\.ps1\)' -and $diagSrc -match 'IntegrationBatches\.ps1') }

$failures = @()
foreach ($c in $cases) {
    $mark = if ($c.ok) { 'ok' } else { 'FAIL' }
    Write-Host ("{0,-4} {1}" -f $mark, $c.label)
    if (-not $c.ok) { $failures += $c.label }
}

Write-Host ""
Write-Host ("{0}/{1} cases pass" -f ($cases.Count - $failures.Count), $cases.Count)
foreach ($f in $failures) { Write-Host "  FAIL $f" }

if ($failures.Count -gt 0) { exit 1 } else { exit 0 }
