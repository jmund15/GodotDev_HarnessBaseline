#requires -Version 7
<#
.SYNOPSIS
  The Integration suite's batch model: unit enumeration, the duration-binned plan, the state file, the durations
  manifest, batch reservations, retry selection, diagnosis and the verdict lines. It selects and returns units and
  never launches a child; its caller (run_integration_batched.ps1 standalone, the gate in held mode) runs them through
  RunPool.ps1 and hands the results back here. Every plan, state and manifest write happens in that caller's process.

.UNITS
  Units are the direct children of Tests/Integration: each subfolder, plus each root-level test class. A unit's filter
  clause is `FullyQualifiedName~Tests.Integration.<Segment>.`: the trailing dot pins the segment to the position right
  after `Integration.`, so every test matches exactly one unit and batch sums partition the suite. A segment over
  -MaxBatchTests whose files declare sub-namespaces (`Tests.Integration.<Seg>.<Sub>`) splits into `<Seg>.<Sub>` units plus
  a `<Seg>` remainder that excludes them; the manifest keys each sub as `<Seg>.<Sub>`.

.NOTES
  Dot-sourced flat .ps1 (house pattern). Dot-sources batch_diagnosis.ps1 when present; $script:IbDiagLoaded says so.
#>

$ibDiagScript = Join-Path $PSScriptRoot 'batch_diagnosis.ps1'
$script:IbDiagLoaded = $false
if (Test-Path $ibDiagScript) { . $ibDiagScript; $script:IbDiagLoaded = $true }

# A result that carries no readable evidence: not a regression signal, never GREEN or RED (RunPool.ps1 statuses).
$script:IncompleteStatuses = @('NO_OUTPUT', 'NO_TRX', 'BAD_TRX', 'HELD_REFUSED', 'EDITOR_OPEN', 'SKIP_RECOMPILE_IGNORED')

# ---------------------------------------------------------------- renderer-tagged classes
# A test class that needs a real renderer carries [RequiresRenderer], an attribute the project's test framework defines. This
# file is the one reader of that tag: the gate plans the `Render` unit from it and run_test_suite.ps1 chooses headless or
# windowed from it. Every other test runs headless.
$script:RenderUnit = 'Render'
$script:CsNoise = [regex]::new('//[^\r\n]*|/\*[\s\S]*?\*/|@"(?:[^"]|"")*"|"(?:[^"\\\r\n]|\\.)*"', 'Compiled')
$script:CsNamespace = [regex]::new('(?m)^\s*namespace\s+([\w.]+)\s*[;{]', 'Compiled')
$script:CsClass = [regex]::new('(?m)^[ \t]*((?:\[(?:[^\[\]]|\[[^\[\]]*\])*\]\s*)*)(?:(?:public|internal|private|protected|sealed|abstract|static|partial|unsafe|file)\s+)*class\s+(\w+)', 'Compiled')
$script:RenderTag = [regex]::new('\bRequiresRenderer(?:Attribute)?\b', 'Compiled')
$script:SuiteTag = [regex]::new('\bTestSuite(?:Attribute)?\b', 'Compiled')

# Every test class under Tests/ as { fqn; tagged }: a class carrying [TestSuite] or [RequiresRenderer], its namespace from the
# file's first `namespace` declaration (file-scoped or block). Comments and string literals are blanked first, so an
# attribute named there counts for nothing.
function Get-TestClassCatalog {
    param([Parameter(Mandatory)] [string] $Repo)
    $dir = Join-Path $Repo 'Tests'
    $byFqn = [ordered]@{}
    if (-not (Test-Path -LiteralPath $dir)) { return @() }
    foreach ($path in [System.IO.Directory]::EnumerateFiles($dir, '*.cs', [System.IO.SearchOption]::AllDirectories)) {
        $text = [System.IO.File]::ReadAllText($path)
        if (-not $text.Contains('TestSuite') -and -not $text.Contains('RequiresRenderer')) { continue }
        $clean = $script:CsNoise.Replace($text, ' ')
        $ns = $script:CsNamespace.Match($clean)
        foreach ($m in $script:CsClass.Matches($clean)) {
            $attrs = $m.Groups[1].Value
            $tagged = $script:RenderTag.IsMatch($attrs)
            if (-not $tagged -and -not $script:SuiteTag.IsMatch($attrs)) { continue }
            $fqn = $(if ($ns.Success) { $ns.Groups[1].Value + '.' } else { '' }) + $m.Groups[2].Value
            $byFqn[$fqn] = [bool] ($tagged -or ($byFqn.Contains($fqn) -and $byFqn[$fqn]))
        }
    }
    @($byFqn.Keys | ForEach-Object { [pscustomobject]@{ fqn = $_; tagged = $byFqn[$_] } })
}

function Get-RendererTaggedClasses {
    param([Parameter(Mandatory)] [string] $Repo)
    @(Get-TestClassCatalog -Repo $Repo | Where-Object { $_.tagged } | ForEach-Object { $_.fqn } | Sort-Object)
}

# The `&FullyQualifiedName!~<fqn>.` clauses that keep tagged classes out of a headless unit (the trailing dot pins the class).
function Format-RenderExclusions {
    param([string[]] $Classes = @())
    (@($Classes | ForEach-Object { "&FullyQualifiedName!~$_." })) -join ''
}

# ---- vstest filter evaluation, enough of the grammar to decide which classes a filter selects:
# `FullyQualifiedName` with ~ !~ = !=, joined by & and | (& binds tighter), parenthesised, an empty operand true.
function Read-FilterTerm {
    param([hashtable] $S)
    if ($S.i -ge $S.t.Count -or $S.t[$S.i] -in @(')', '&', '|')) { return $true }
    $tok = $S.t[$S.i]
    if ($tok -eq '(') {
        $S.i++
        $v = Read-FilterOr $S
        if ($S.i -lt $S.t.Count -and $S.t[$S.i] -eq ')') { $S.i++ }
        return $v
    }
    $S.i++
    if ($tok -notmatch '^FullyQualifiedName\s*(!~|~|!=|=)\s*(.*)$') { $S.unsupported = $true; return $false }
    $op = $Matches[1]; $val = $Matches[2]
    switch ($op) {
        '~'  { return $S.target.IndexOf($val, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
        '!~' { return $S.target.IndexOf($val, [StringComparison]::OrdinalIgnoreCase) -lt 0 }
        '='  { return $S.target -ieq $val }
        '!=' { return $S.target -ine $val }
    }
}

function Read-FilterAnd {
    param([hashtable] $S)
    $v = Read-FilterTerm $S
    while ($S.i -lt $S.t.Count -and $S.t[$S.i] -eq '&') { $S.i++; $r = Read-FilterTerm $S; $v = $v -and $r }
    $v
}

function Read-FilterOr {
    param([hashtable] $S)
    $v = Read-FilterAnd $S
    while ($S.i -lt $S.t.Count -and $S.t[$S.i] -eq '|') { $S.i++; $r = Read-FilterAnd $S; $v = $v -or $r }
    $v
}

# The catalog entries the filter selects, matched on `<fqn>.` (a test's full name is `<fqn>.<method>`). $null when the filter
# uses anything beyond the grammar above, so the caller cannot treat it as a selection. The array is wrapped (`, $x`) so a lone
# class or none survives the pipeline; assign the result without @().
function Get-FilterSelectedClasses {
    param([string] $Filter, $Catalog)
    $tokens = @([regex]::Matches($Filter, '[()&|]|[^()&|]+') | ForEach-Object { $_.Value.Trim() } | Where-Object { $_ -ne '' })
    $selected = [System.Collections.Generic.List[object]]::new()
    foreach ($c in @($Catalog)) {
        $s = @{ t = $tokens; i = 0; target = ($c.fqn + '.'); unsupported = $false }
        $hit = Read-FilterOr $s
        if ($s.unsupported -or $s.i -lt $tokens.Count) { return $null }
        if ($hit) { $selected.Add($c) }
    }
    , $selected.ToArray()
}

# headless unless -Windowed or every class the filter selects is renderer-tagged. A mixed filter runs headless and returns the
# tagged classes it will not render in .skipped; an unreadable filter or one selecting no class fails closed to headless.
# .engineArgs are the engine flags for the mode: headless implies the Dummy audio driver, and a windowed run names it, so no
# test run plays through the machine's speakers.
function Resolve-RunMode {
    param([Parameter(Mandatory)] [string] $Repo, [Parameter(Mandatory)] [string] $Filter, [switch] $Windowed)
    $mode, $skipped = 'headless', [string[]]@()
    if ($Windowed) { $mode = 'windowed' }
    else {
        $selected = Get-FilterSelectedClasses -Filter $Filter -Catalog (Get-TestClassCatalog -Repo $Repo)
        if ($null -ne $selected) {
            $tagged = @($selected | Where-Object { $_.tagged })
            if ($selected.Count -gt 0 -and $tagged.Count -eq $selected.Count) { $mode = 'windowed' }
            elseif ($tagged.Count -lt $selected.Count) { $skipped = [string[]]@($tagged | ForEach-Object { $_.fqn }) }
        }
    }
    $engineArgs = if ($mode -eq 'headless') { '--headless' } else { '--audio-driver Dummy' }
    [pscustomobject]@{ mode = $mode; skipped = $skipped; engineArgs = $engineArgs }
}

# `adaptation.json` `<key>` (Design Doc §8): absent file or key -> $Default, silently.
# Malformed JSON, a non-object root, or a key present but not a string -> $Default plus one
# stderr line naming the file. Never throws — the caller must keep running either way.
function Get-AdaptationValue {
    param([Parameter(Mandatory)][string] $Repo, [Parameter(Mandatory)][string] $Key, [string] $Default = '')
    $path = Join-Path $Repo '.claude\skills\project_subsystems\adaptation.json'
    if (-not (Test-Path $path)) { return $Default }
    try {
        $json = Get-Content $path -Raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        # [Console]::Error, not Write-Error: Write-Error's formatted host view spans several
        # lines once it crosses a process boundary (position markers, a source line excerpt),
        # which breaks the "one stderr line" contract every other adaptation.json seam holds.
        [Console]::Error.WriteLine("adaptation: $path is unparseable -- using default for $Key")
        return $Default
    }
    if ($json -isnot [System.Management.Automation.PSCustomObject]) {
        [Console]::Error.WriteLine("adaptation: $path is not a JSON object -- using default for $Key")
        return $Default
    }
    $prop = $json.PSObject.Properties[$Key]
    if (-not $prop) { return $Default }
    if ($prop.Value -isnot [string]) {
        [Console]::Error.WriteLine("adaptation: $path key '$Key' is wrong-typed (want string) -- using default")
        return $Default
    }
    return $prop.Value
}

# ---------------------------------------------------------------- TRX -> per-unit seconds
# A test in a split segment's sub-namespace (SubUnits: seg -> sub names) is keyed `seg.sub`; every other test keys by its segment.
# -AsUnit keys every test of the TRX under that one name: the Render unit's tests span segments, so a segment key would
# overwrite the headless batch that measures the same segment.
function Get-TrxUnitDurations {
    param([string] $Path, [hashtable] $SubUnits = @{}, [string] $AsUnit = '')
    $sums = @{}
    [xml] $xml = Get-Content -Path $Path -Raw
    foreach ($r in $xml.TestRun.Results.UnitTestResult) {
        if ($AsUnit) { $key = $AsUnit }
        else {
            if ($r.testName -notmatch '^{{PROJECT_NAME}}\.Tests\.Integration\.([^.]+)\.(?:([^.]+)\.)?') { continue }
            $seg = $Matches[1]
            $sub = $Matches[2]
            $key = if ($sub -and $SubUnits.ContainsKey($seg) -and $sub -in $SubUnits[$seg]) { "$seg.$sub" } else { $seg }
        }
        $dur = 0.0
        if ($r.duration) { $dur = ([TimeSpan]::Parse($r.duration)).TotalSeconds }
        if (-not $sums.ContainsKey($key)) { $sums[$key] = [pscustomobject]@{ seconds = 0.0; tests = 0 } }
        $sums[$key].seconds += $dur
        $sums[$key].tests   += 1
    }
    return $sums
}

# ---------------------------------------------------------------- manifest
function Write-Manifest {
    param([Parameter(Mandatory)] [string] $Path, [hashtable] $Units, [hashtable] $BatchWalls, [hashtable] $Counters)
    $obj = [ordered]@{
        schema_version = 3
        description    = 'Per-unit measured Integration test durations (TRX-derived) + per-batch end-to-end work wall { wall, consecutive_failures }. The counter increments on HANG/SILENT_SKIP and resets on GREEN — the diagnosis trigger ledger for batch_diagnosis.ps1. Consumed by run_integration_batched.ps1 for duration-based batch binning, honest reservations, and hang diagnosis. Auto-rewritten by batched runs that measure a GREEN batch; commit alongside test changes. Units missing here bin at 3s and 1 test, each in a batch of its own with the cost ceiling as its hang cap until a run measures it. Stale values shift bin balance, reservations and hang caps.'
        updated_at     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        units          = [ordered]@{}
        batch_wall     = [ordered]@{}
    }
    foreach ($k in ($Units.Keys | Sort-Object)) {
        $obj.units[$k] = [ordered]@{
            seconds = [math]::Round($Units[$k].seconds, 1)
            tests   = $Units[$k].tests
        }
    }
    if ($BatchWalls) {
        foreach ($k in ($BatchWalls.Keys | Sort-Object)) {
            $obj.batch_wall[$k] = [ordered]@{
                wall = [math]::Round($BatchWalls[$k], 1)
                consecutive_failures = $(if ($Counters -and $Counters.ContainsKey($k)) { $Counters[$k] } else { 0 })
            }
        }
    }
    $obj | ConvertTo-Json -Depth 5 | Set-Content -Path $Path -Encoding utf8
}

# seconds/tests per unit segment, and wall/consecutive_failures per batch label (v3 objects; v2 scalar walls load).
function Read-BatchManifest {
    param([string] $Path)
    $m = [pscustomobject]@{ seconds = @{}; tests = @{}; batchWall = @{}; counters = @{} }
    if (-not (Test-Path $Path)) { return $m }
    $mj = Get-Content $Path -Raw | ConvertFrom-Json
    foreach ($p in $mj.units.PSObject.Properties) {
        $m.seconds[$p.Name] = [double] $p.Value.seconds
        $m.tests[$p.Name]   = [int] $p.Value.tests
    }
    if ($mj.PSObject.Properties['batch_wall']) {
        foreach ($p in $mj.batch_wall.PSObject.Properties) {
            if ($p.Value -is [PSCustomObject]) {
                $m.batchWall[$p.Name] = [double] $p.Value.wall
                $m.counters[$p.Name]  = [int] $p.Value.consecutive_failures
            } else { $m.batchWall[$p.Name] = [double] $p.Value }
        }
    }
    $m
}

# ---------------------------------------------------------------- units and plan
# Enumerated from DISK each run, so a new folder can never be silently un-run.
function Get-IntegrationSegments {
    param([Parameter(Mandatory)] [string] $Repo)
    $segs = [System.Collections.Generic.List[string]]::new()
    Get-ChildItem -Path (Join-Path $Repo 'Tests\Integration') -Directory | ForEach-Object { $segs.Add($_.Name) }
    Get-ChildItem -Path (Join-Path $Repo 'Tests\Integration') -File -Filter '*.cs' | ForEach-Object {
        # Root-level test classes sit directly in the Tests.Integration namespace; their class
        # name IS the FQN segment after Integration, so the same trailing-dot clause works.
        $m = [regex]::Match((Get-Content $_.FullName -Raw), 'class\s+(\w+)')
        if ($m.Success) { $segs.Add($m.Groups[1].Value) }
    }
    @($segs | Sort-Object -Unique)
}

# -Only: the named domains resolve FROM the enumerated set (never a second map that can drift from disk), case-insensitive.
function Resolve-OnlySegments {
    param([string[]] $Segments, [string[]] $Wanted)
    @(foreach ($seg in $Segments) { foreach ($d in $Wanted) { if ($seg -ieq $d) { $seg; break } } })
}

# The sub-namespaces of each segment whose measured tests exceed -MaxBatchTests (a segment's measure is its keys:
# `seg` plus any `seg.sub`). Only a segment with sub-namespace files splits. Read from the .cs files, never from folder
# names: the filter matches namespaces, so a folder that names a different namespace would split nothing.
function Get-IntegrationSubUnits {
    param([string] $Repo, [string[]] $Segments, $Manifest, [int] $MaxBatchTests)
    $out = @{}
    foreach ($seg in $Segments) {
        $keys = @($Manifest.seconds.Keys | Where-Object { $_ -eq $seg -or $_.StartsWith("$seg.") })
        if ((Get-UnitMeasure -Manifest $Manifest -Keys $keys).tests -le $MaxBatchTests) { continue }
        $subs = @(Get-SegmentSubNamespaces -Repo $Repo -Seg $seg)
        if ($subs.Count -gt 0) { $out[$seg] = $subs }
    }
    $out
}

# Sub-namespaces under Tests/Integration/<Seg>: each file's `namespace {{PROJECT_NAME}}.Tests.Integration.<Seg>.<Sub>`.
function Get-SegmentSubNamespaces {
    param([string] $Repo, [string] $Seg)
    $dir = Join-Path $Repo "Tests\Integration\$Seg"
    if (-not (Test-Path $dir)) { return @() }
    $subs = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    $pattern = '(?m)^namespace\s+{{PROJECT_NAME}}\.Tests\.Integration\.' + [regex]::Escape($Seg) + '\.([A-Za-z_]\w*)'
    foreach ($f in @(Get-ChildItem -Path $dir -Recurse -File -Filter '*.cs')) {
        $m = [regex]::Match((Get-Content -LiteralPath $f.FullName -Raw), $pattern)
        if ($m.Success) { [void] $subs.Add($m.Groups[1].Value) }
    }
    @($subs)
}

# seg -> sub names, from the plan's `seg.sub` units, so the TRX parser keys each sub's tests as the plan does.
function Get-PlanSubUnits {
    param($Plan)
    $subs = @{}
    foreach ($p in $Plan) {
        foreach ($s in @($p.segs)) {
            if ($s -notmatch '^([^.]+)\.(.+)$') { continue }
            if (-not $subs.ContainsKey($Matches[1])) { $subs[$Matches[1]] = @() }
            $subs[$Matches[1]] += $Matches[2]
        }
    }
    $subs
}

# Measured seconds and tests summed over the manifest keys that exist; measured is false when none does.
function Get-UnitMeasure {
    param($Manifest, [string[]] $Keys)
    $found = @($Keys | Where-Object { $Manifest.seconds.ContainsKey($_) })
    [pscustomobject]@{
        measured = $found.Count -gt 0
        sec      = [double] (($found | ForEach-Object { $Manifest.seconds[$_] } | Measure-Object -Sum).Sum)
        tests    = [int] (($found | ForEach-Object { $Manifest.tests[$_] } | Measure-Object -Sum).Sum)
    }
}

# A planning unit: its measure from the manifest, or the 3 s / 1 test default when unmeasured. Excl are the sub-namespace
# filters a remainder unit leaves out; an Alone unit never shares a batch.
function New-PlanUnit {
    param($Manifest, [string] $Seg, [string[]] $Keys, [string[]] $Excl = @(), [bool] $Alone = $false)
    $m = Get-UnitMeasure -Manifest $Manifest -Keys $Keys
    [pscustomobject]@{
        seg = $Seg; sec = $(if ($m.measured) { $m.sec } else { 3.0 }); tests = $(if ($m.measured) { $m.tests } else { 1 })
        unmeasured = (-not $m.measured); alone = $Alone; excl = $Excl
    }
}

# First-fit decreasing over measured seconds, honoring both -TargetBatchSec and -MaxBatchTests. Batch numbering follows
# bin creation order (largest first). Units missing from the manifest default to 3 s and 1 test, and each gets a batch of
# its own: it never shares one, so its batch's cost-ceiling cap covers no other segment's work. A -SubUnits segment
# (Get-IntegrationSubUnits) plans one unit per sub-namespace plus a remainder unit that excludes them; the remainder runs alone.
# -RenderClasses (Get-RendererTaggedClasses) leave every batch's filter and form one more unit, `Render`, that selects them: run_test_suite.ps1
# runs that filter windowed and every batch headless. The unit is measured and retried like a batch, but never binned.
function New-IntegrationPlan {
    param([string[]] $Segments, $Manifest, [int] $TargetBatchSec, [int] $MaxBatchTests, [string] $Quarantine = '', [hashtable] $SubUnits = @{}, [string[]] $RenderClasses = @())
    $weighted = @(foreach ($seg in $Segments) {
        if ($SubUnits.ContainsKey($seg)) {
            foreach ($sub in $SubUnits[$seg]) { New-PlanUnit -Manifest $Manifest -Seg "$seg.$sub" -Keys @("$seg.$sub") }
            New-PlanUnit -Manifest $Manifest -Seg $seg -Keys @($seg) -Excl @($SubUnits[$seg] | ForEach-Object { "$seg.$_" }) -Alone $true
        } else {
            New-PlanUnit -Manifest $Manifest -Seg $seg -Keys @($Manifest.seconds.Keys | Where-Object { $_ -eq $seg -or $_.StartsWith("$seg.") })
        }
    }) | Sort-Object sec -Descending
    $batches = [System.Collections.Generic.List[object]]::new()
    foreach ($u in $weighted) {
        $placed = $false
        if (-not $u.unmeasured -and -not $u.alone) {
            foreach ($b in $batches) {
                if (-not $b.unmeasured -and -not $b.alone -and $b.sec + $u.sec -le $TargetBatchSec -and $b.tests + $u.tests -le $MaxBatchTests) {
                    $b.segs.Add($u.seg); $b.sec += $u.sec; $b.tests += $u.tests; $placed = $true; break
                }
            }
        }
        if (-not $placed) {
            $nb = [pscustomobject]@{ segs = [System.Collections.Generic.List[string]]::new(); sec = $u.sec; tests = $u.tests; unmeasured = $u.unmeasured; alone = $u.alone; excl = $u.excl }
            $nb.segs.Add($u.seg)
            $batches.Add($nb)
        }
    }
    $renderExcl = Format-RenderExclusions -Classes $RenderClasses
    $i = 0
    $rows = @(foreach ($b in $batches) {
        $i++
        [pscustomobject]@{
            label = "Integration_B$i"; segs = @($b.segs); expectedSec = [math]::Round($b.sec, 1); unmeasured = [bool] $b.unmeasured
            filter = '(' + (($b.segs | ForEach-Object { "FullyQualifiedName~Tests.Integration.$_." }) -join '|') + ')' + (($b.excl | ForEach-Object { "&FullyQualifiedName!~Tests.Integration.$_." }) -join '') + $renderExcl + "&$Quarantine"
            status = 'PENDING'; passed = 0; failed = 0; elapsed = 0; workWall = 0; ran = $false; trx = ''; out = ''
        }
    })
    if (@($RenderClasses).Count -gt 0) {
        $m = Get-UnitMeasure -Manifest $Manifest -Keys @($script:RenderUnit)
        $rows += [pscustomobject]@{
            label = $script:RenderUnit; segs = @($script:RenderUnit); expectedSec = $(if ($m.measured) { [math]::Round($m.sec, 1) } else { 3.0 }); unmeasured = (-not $m.measured)
            filter = '(' + (($RenderClasses | ForEach-Object { "FullyQualifiedName~$_." }) -join '|') + ")&$Quarantine"
            status = 'PENDING'; passed = 0; failed = 0; elapsed = 0; workWall = 0; ran = $false; trx = ''; out = ''
        }
    }
    $rows
}

# The wrapper's hang cap for a batch: 60 s plus 3x its expected seconds, at least 120 s, bounded by -CostCeilingMs but never
# below twice the expected seconds (a cap under a unit's own measured time kills honest work). The batch of an unmeasured
# segment gets -CostCeilingMs outright: its expected seconds are the 3 s default, so a cap from them kills a first run.
function Get-BatchTimeoutMs {
    param([double] $ExpectedSec, [int] $CostCeilingMs, [bool] $Unmeasured = $false)
    if ($Unmeasured) { return $CostCeilingMs }
    $ceiling = [math]::Max($CostCeilingMs, [int](2 * $ExpectedSec * 1000))
    [math]::Min($ceiling, [math]::Max(120000, [int]((60 + 3 * $ExpectedSec) * 1000)))
}

# The cap's expected seconds are the reservation's (Get-BatchReservationMs): a batch whose recorded wall outgrew its unit
# sum would otherwise be killed before the time its reservation holds for it, and never go green to refresh the sum.
function ConvertTo-PoolUnit {
    param($Row, [string] $Label, [int] $CostCeilingMs, [hashtable] $BatchWall = @{})
    $basisSec = (Get-BatchReservationMs -Label $Row.label -ExpectedSec $Row.expectedSec -BatchWall $BatchWall) / 1000.0
    @{ label = $Label; of = $Row.label; filter = $Row.filter; expectSec = [double] $Row.expectedSec
       timeoutMs = (Get-BatchTimeoutMs -ExpectedSec $basisSec -CostCeilingMs $CostCeilingMs -Unmeasured ([bool] $Row.unmeasured)) }
}

# Pool units for every plan row not already GREEN, in plan order.
function Get-IntegrationUnits {
    param($Plan, [int] $CostCeilingMs = 300000, [hashtable] $BatchWall = @{})
    @($Plan | Where-Object { $_.status -ne 'GREEN' } | ForEach-Object { ConvertTo-PoolUnit -Row $_ -Label $_.label -CostCeilingMs $CostCeilingMs -BatchWall $BatchWall })
}

# One retry for transient wedges: the HANG, SILENT_SKIP and NO_TRX (a crash that left no TRX) rows only (RED is adjudicated
# and never retries). Each retry unit is labelled <label>_retry, so its run dir, TRX and run tag never overwrite the
# original batch's evidence.
function Select-RetryUnits {
    param($Plan, [int] $CostCeilingMs = 300000, [hashtable] $BatchWall = @{})
    @($Plan | Where-Object { $_.status -in 'HANG', 'SILENT_SKIP', 'NO_TRX' } | ForEach-Object { ConvertTo-PoolUnit -Row $_ -Label "$($_.label)_retry" -CostCeilingMs $CostCeilingMs -BatchWall $BatchWall })
}

function Format-RetryNoTrxLine {
    param($Plan)
    "RETRY_NO_TRX=$(@($Plan | Where-Object { $_.status -eq 'NO_TRX' }).Count)"
}

# ---------------------------------------------------------------- reservation
# Reservation = the honest duration: last run's end-to-end work wall for this label, or the
# in-suite manifest estimate, whichever is larger. It is the basis of a batch's hang cap and of its
# diagnosis. No whole-run budget sits above the per-batch caps: contention makes a run slow, and a
# wedge is the batch cap's to kill.
function Get-BatchReservationMs {
    param([string] $Label, [double] $ExpectedSec, [hashtable] $BatchWall = @{})
    $lastWall = 0.0
    if ($BatchWall.ContainsKey($Label)) { $lastWall = [double]$BatchWall[$Label] }
    [math]::Max([int]($ExpectedSec * 1000), [int]($lastWall * 1000))
}

# ---------------------------------------------------------------- state
function Restore-PriorGreens {
    param($Plan, [string] $StatePath, [string] $TreeDigest)
    $reason = ''
    if (-not (Test-Path $StatePath)) { $reason = 'no-state' }
    elseif (-not $TreeDigest) { $reason = 'no-digest' }
    else {
        $prev = Get-Content $StatePath -Raw | ConvertFrom-Json
        $prevDigest = if ($prev.PSObject.Properties['treeDigest']) { [string]$prev.treeDigest } else { '' }
        if ($prevDigest -ne $TreeDigest) { $reason = 'digest-mismatch' }
    }
    if ($reason) { Write-Output "RETRY_ONLY_IGNORED reason=$reason"; return }
    $greenLabels = @($prev.batches | Where-Object { $_.status -eq 'GREEN' } | ForEach-Object { $_.label })
    # Only honor prior greens if the batch composition is unchanged (same segs -> same label).
    foreach ($p in $Plan) {
        $match = $prev.batches | Where-Object { $_.label -eq $p.label -and (($_.segs -join ',') -eq ($p.segs -join ',')) }
        if ($match -and $p.label -in $greenLabels) { $p.status = 'GREEN'; $p.passed = $match.passed }
    }
}

# Applies pool results to their plan rows by label (a <label>_retry result lands on <label>), in any order. In memory.
function Update-IntegrationPlan {
    param($Plan, $Results)
    foreach ($r in @($Results)) {
        $row = @($Plan | Where-Object { $r.label -eq $_.label -or $r.label -eq "$($_.label)_retry" }) | Select-Object -First 1
        if (-not $row) { continue }
        $vals = [ordered]@{
            status   = $r.status; passed = [int] $r.passed; failed = [int] $r.failed; elapsed = [double] $r.elapsedSec
            # Work wall (wall minus lock-wait) is what the reservation persists — a contended run must
            # not inflate the next run's honest estimate.
            workWall = [math]::Round([math]::Max(0.0, [double] $r.elapsedSec - ([double] $r.lockWaitMs / 1000.0)), 1)
            ran = $true; trx = [string] $r.trx; out = [string] $r.outPath
        }
        foreach ($k in $vals.Keys) { $row | Add-Member -NotePropertyName $k -NotePropertyValue $vals[$k] -Force }
    }
}

# The per-batch breadcrumb .claude/scratch/test_runs/integration_batches.json: one write per call.
function Save-BatchState {
    param([Parameter(Mandatory)] [string] $StatePath, $Plan, [int] $TargetBatchSec, [string] $TreeDigest, [string] $RunId = '')
    New-Item -ItemType Directory -Force -Path (Split-Path $StatePath) | Out-Null
    [pscustomobject]@{
        updated_at       = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        target_batch_sec = $TargetBatchSec
        treeDigest       = $TreeDigest
        runId            = $RunId
        batches          = $Plan
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $StatePath -Encoding utf8
}

# v3 consecutive_failures: GREEN resets; HANG/SILENT_SKIP increment. RED is adjudicated, LOCKED is
# machine contention and an incomplete result carries no evidence, so none of them count.
function Get-UpdatedCounters {
    param($Plan, [hashtable] $Counters = @{})
    $out = @{}
    foreach ($k in $Counters.Keys) { $out[$k] = $Counters[$k] }
    foreach ($p in $Plan) {
        if ($p.status -in 'HANG', 'SILENT_SKIP') {
            $out[$p.label] = 1 + $(if ($out.ContainsKey($p.label)) { [int] $out[$p.label] } else { 0 })
        } else { $out[$p.label] = 0 }
    }
    $out
}

# Durations from the TRX of every row that ran this invocation; complete only when each of those ended GREEN with a
# parseable TRX.
function Get-FreshDurations {
    param($Plan)
    $fresh = @{}
    $why = @()
    $subs = Get-PlanSubUnits -Plan $Plan
    foreach ($p in @($Plan | Where-Object { $_.PSObject.Properties['ran'] -and $_.ran })) {
        if ($p.status -ne 'GREEN') { $why += "$($p.label):status=$($p.status)"; continue }
        if (-not $p.trx -or -not (Test-Path -LiteralPath $p.trx)) { $why += "$($p.label):trx-missing"; continue }
        try { $d = Get-TrxUnitDurations -Path $p.trx -SubUnits $subs -AsUnit $(if ($p.label -eq $script:RenderUnit) { $script:RenderUnit } else { '' }); foreach ($k in $d.Keys) { $fresh[$k] = $d[$k] } }
        catch { $why += "$($p.label):trx-unparsed" }
    }
    [pscustomobject]@{ durations = $fresh; complete = ($why.Count -eq 0); why = ($why -join ',') }
}

# The manifest's one write per run. With -Fresh measured units (every GREEN batch of this run, red or green run alike) it
# MERGES them over the existing manifest (a -RetryOnly run measures only its reruns), drops units no longer on disk
# (-AllSegments is the full enumeration, never a -Only subset), and records the work walls of this run's GREEN batches
# (a red batch's wall is capped by its hang cap, so it would inflate the next reservation); otherwise it updates only the
# counters of existing batch_wall entries. Returns the MANIFEST_UPDATED line for a merge, else a MANIFEST_KEPT line
# naming why the durations stayed.
function Save-BatchManifest {
    param([Parameter(Mandatory)] [string] $Path, $Plan, [hashtable] $Counters, $Fresh = $null, [string[]] $AllSegments = @())
    if ($Fresh -and $Fresh.durations.Count -gt 0) {
        $m = Read-BatchManifest -Path $Path
        $merged = @{}
        foreach ($k in $m.seconds.Keys) { $merged[$k] = [pscustomobject]@{ seconds = $m.seconds[$k]; tests = $m.tests[$k] } }
        foreach ($k in $Fresh.durations.Keys) { $merged[$k] = $Fresh.durations[$k] }
        # A segment measured whole replaces its earlier sub-namespace keys; a split segment keeps the subs it did not run.
        $planSubs = Get-PlanSubUnits -Plan $Plan
        foreach ($k in @($Fresh.durations.Keys | Where-Object { -not $_.Contains('.') })) {
            if ($planSubs.ContainsKey($k)) { continue }
            foreach ($stale in @($merged.Keys | Where-Object { $_.StartsWith("$k.") })) { $merged.Remove($stale) }
        }
        $liveSegs = @($AllSegments) + @(if (@($Plan | Where-Object { $_.label -eq $script:RenderUnit }).Count -gt 0) { $script:RenderUnit })
        foreach ($k in @($merged.Keys)) { if (($k -split '\.')[0] -notin $liveSegs) { $merged.Remove($k) } }
        $walls = @{}
        foreach ($k in $m.batchWall.Keys) { $walls[$k] = $m.batchWall[$k] }
        foreach ($p in $Plan) { if ($p.status -eq 'GREEN' -and $p.workWall -gt 0) { $walls[$p.label] = $p.workWall } }
        Write-Manifest -Path $Path -Units $merged -BatchWalls $walls -Counters $Counters
        $partial = if ($Fresh.complete) { '' } else { " partial=$($Fresh.why)" }
        return "MANIFEST_UPDATED measured=$($Fresh.durations.Count) total=$($merged.Count) walls=$($walls.Count)$partial (commit Tests/integration_batch_durations.json if changed)"
    }
    $kept = if (-not $Fresh) { 'MANIFEST_KEPT reason=run-not-green' }
            elseif (-not $Fresh.complete) { "MANIFEST_KEPT reason=$($Fresh.why)" }
            else { 'MANIFEST_KEPT reason=no-units-measured' }
    if (-not (Test-Path $Path)) { return $kept }
    try {
        $mjc = Get-Content $Path -Raw | ConvertFrom-Json
        if (-not $mjc.PSObject.Properties['batch_wall']) { return $kept }
        foreach ($p in $mjc.batch_wall.PSObject.Properties) {
            if (-not $Counters.ContainsKey($p.Name)) { continue }
            if ($p.Value -is [PSCustomObject]) { $p.Value.consecutive_failures = $Counters[$p.Name] }
            else { $p.Value = [ordered]@{ wall = [double]$p.Value; consecutive_failures = $Counters[$p.Name] } }
        }
        $mjc | ConvertTo-Json -Depth 5 | Set-Content -Path $Path -Encoding utf8
    } catch { Write-Warning "batch counter ledger update failed: $_" }
    $kept
}

# ---------------------------------------------------------------- diagnosis
# DIAGNOSE lines for every trigger candidate (batch_diagnosis.ps1), then DIAGNOSE_PASS; nothing when it is absent.
function Get-BatchDiagnosisLines {
    param($Plan, [hashtable] $Counters, $Manifest)
    if (-not $script:IbDiagLoaded) { return }
    $n = 0
    foreach ($p in $Plan) {
        $resSec = (Get-BatchReservationMs -Label $p.label -ExpectedSec $p.expectedSec -BatchWall $Manifest.batchWall) / 1000.0
        $counter = if ($Counters.ContainsKey($p.label)) { [int]$Counters[$p.label] } else { 0 }
        if (-not (Test-BatchNeedsDiagnosis -Batch $p -Counter $counter -ReservationSec $resSec)) { continue }
        $wall = if ($p.workWall -gt 0) { [double]$p.workWall } elseif ($Manifest.batchWall.ContainsKey($p.label)) { [double]$Manifest.batchWall[$p.label] } else { 0.0 }
        $last = if ($p.PSObject.Properties['out'] -and $p.out -and (Test-Path -LiteralPath $p.out)) { Get-Content -LiteralPath $p.out -Raw } else { '' }
        $d = Get-BatchDiagnosis -Batch $p -ReservationSec $resSec -WorkWallSec $wall -LastOutput $last -UnitSecs $Manifest.seconds -UnitTests $Manifest.tests
        if ($d) {
            $n++
            "DIAGNOSE batch=$($p.label) status=$($p.status) verdict=$($d.verdict) evidence=$($d.evidence) action=$($d.action)"
        }
    }
    "DIAGNOSE_PASS candidates=$n"
}

# ---------------------------------------------------------------- verdict
function Read-IntegrationBaseline {
    param([string] $Path)
    $b = [pscustomobject]@{ baseline = 0; sentinel = 0 }
    if (Test-Path $Path) {
        $bj = Get-Content $Path -Raw | ConvertFrom-Json
        $b.baseline = [int] $bj.suites.Integration.passed
        $b.sentinel = [int] $bj.silent_skip_sentinels.Integration_min
    }
    $b
}

# The final Integration lines in the batcher's stdout contract (TOTAL, LOCKWAIT_TOTAL_MS, TRX_DIR, COMPLETENESS, STATUS)
# and the exit code that goes with them. Exit 0 prints no STATUS line: the caller finishes with STATUS=DONE.
function Get-IntegrationVerdict {
    param($Plan, [string[]] $Only = @(), [int] $Baseline, [int] $Sentinel, [double] $ElapsedSec, [int64] $LockWaitMs, [string] $RunsRoot)
    $lines = [System.Collections.Generic.List[string]]::new()
    $totalPassed = [int](($Plan | Measure-Object -Property passed -Sum).Sum)
    $totalFailed = [int](($Plan | Measure-Object -Property failed -Sum).Sum)
    $lines.Add("TOTAL passed=$totalPassed failed=$totalFailed baseline=$Baseline sentinel=$Sentinel elapsed=$([math]::Round($ElapsedSec,1))s lockwait=$([math]::Round($LockWaitMs/1000,1))s")
    $lines.Add("LOCKWAIT_TOTAL_MS=$LockWaitMs")
    $lines.Add("TRX_DIR=$RunsRoot")
    $locked        = @($Plan | Where-Object { $_.status -eq 'LOCKED' })
    $hung          = @($Plan | Where-Object { $_.status -in 'HANG', 'SILENT_SKIP' })
    $red           = @($Plan | Where-Object { $_.status -eq 'RED' })
    $incomplete    = @($Plan | Where-Object { $_.status -in $script:IncompleteStatuses })
    $labels = { param($rows) ($rows | ForEach-Object label) -join ',' }
    $done = { param([int] $Exit) [pscustomobject]@{ Lines = $lines.ToArray(); Exit = $Exit } }
    $incompleteNote = { "incomplete=$($incomplete.Count) batches=$(& $labels $incomplete) statuses=$(($incomplete | ForEach-Object status) -join ',') (no readable result — NOT a regression signal)" }
    $scope = if ($Only) { "COMPLETENESS=TARGETED domains=$($Only -join ',') batches=$($Plan.Count)" } else { $null }

    if ($Only) {
        # Targeted mode: the total is partial BY DESIGN, so it is never tiered against the full
        # baseline or the architectural sentinel. COMPLETENESS=TARGETED marks it non-comparable on
        # every exit path; the real failure signals (red/locked/hang) keep their exit codes.
        if ($red.Count -gt 0) { $lines.Add($scope); $lines.Add("STATUS=FAIL red_batches=$(& $labels $red)"); return (& $done 1) }
        if ($incomplete.Count -gt 0) { $lines.Add("$scope $(& $incompleteNote)"); $lines.Add("STATUS=FAIL incomplete_batches=$(& $labels $incomplete)"); return (& $done 1) }
        # LOCKED-only completion (no real failure). Exit 5 with STATUS=LOCKED — the
        # gate skips its automatic -RetryOnly for this signature (the lock may still be held) and
        # routes to the queue when the machine-busy condition holds.
        if ($locked.Count -gt 0) {
            $lines.Add("$scope locked=$($locked.Count) (a batch could not acquire its checkout lock or a runtime slot — NOT a regression signal)")
            $lines.Add("STATUS=LOCKED skipped=$($locked.Count) (machine busy — re-run when quiet; the gate reports BUSY when the busy signature holds)")
            return (& $done 5)
        }
        if ($hung.Count -gt 0) {
            $lines.Add($scope)
            $lines.Add("STATUS=HANG hung_batches=$(& $labels $hung) (persisted after retry — machine state suspect; see regression_gate reboot guidance)")
            return (& $done 124)
        }
        $lines.Add($scope)
        return (& $done 0)
    }
    if ($red.Count -gt 0) { $lines.Add("STATUS=FAIL red_batches=$(& $labels $red)"); return (& $done 1) }
    if ($incomplete.Count -gt 0) { $lines.Add("COMPLETENESS=INCOMPLETE $(& $incompleteNote)"); $lines.Add("STATUS=FAIL incomplete_batches=$(& $labels $incomplete)"); return (& $done 1) }
    if ($locked.Count -gt 0) {
        $lines.Add("COMPLETENESS=INCOMPLETE locked=$($locked.Count) batches=$(& $labels $locked) (a batch could not acquire its checkout lock or a runtime slot — NOT a regression signal)")
        $lines.Add("STATUS=LOCKED skipped=$($locked.Count) (machine busy — re-run when quiet; the gate reports BUSY when the busy signature holds)")
        return (& $done 5)
    }
    if ($hung.Count -gt 0) { $lines.Add("STATUS=HANG hung_batches=$(& $labels $hung) (persisted after retry — machine state suspect; see regression_gate reboot guidance)"); return (& $done 124) }
    if ($totalPassed -lt $Sentinel) { $lines.Add('COMPLETENESS=SILENT_SKIP (total below architectural floor — results INVALID)'); return (& $done 2) }
    if ($totalPassed -lt $Baseline) {
        $lines.Add("COMPLETENESS=SHORTFALL total=$totalPassed < baseline=$Baseline (a unit may be missing from every batch, or tests were removed — gate Tier-2 judgment applies)")
        return (& $done 3)
    }
    $lines.Add('COMPLETENESS=OK')
    & $done 0
}
