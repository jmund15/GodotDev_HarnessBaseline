#!/usr/bin/env python3
"""Proof: each checkout gets its own Godot user:// directory under LOCALAPPDATA, handed to testhost through the
inline RunConfiguration APPDATA value, and that isolation has one home: RuntimeSlots.ps1 `Get-TestIsolation`.
The proof evaluates `Get-TestIsolation` for two repo paths, then evaluates run_test_suite.ps1's own statements
(`$iso = ...`, the run-dir assignments and the `$testArgs` array) from the script's AST, so it runs the shipped
statements, not a copy.

Per-run isolation (`-ResultsRoot`): the same statements are evaluated with a results root and a label. The run dir
and TRX are named by the sanitized label, the pipe suffix and APPDATA leaf are `<worktreeId>-<tag>` (tag =
`Get-RunTag -Label`), and every inline value is ONE argv token; a token may be wrapped in one pair of quotes so a
spaced path stays one argument, but carries no quote inside (an embedded quote reached Godot literally and crashed
it). `Write-FailureLines` reads the run's own TRX, and `Write-PhaseMs` prints the `PHASE_MS name=<phase> ms=<n>`
line A8 consumes.
Usage: test_run_test_suite_userdir.py [--script <path>]   (default: the checked-out wrapper)
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SCRIPT = os.path.join(ROOT, ".claude", "scripts", "run_test_suite.ps1")
LABEL = "Logic slice.A"
PIPE_SUFFIX_MAX = 16

EVAL = r"""
$ScriptPath = $env:RTS_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
. (Join-Path (Split-Path $ScriptPath) 'GodotProcess.ps1')
. (Join-Path (Split-Path $ScriptPath) 'RuntimeSlots.ps1')
function Find-Assign([string] $Name, [string] $RightMatch = '') {
    $pred = { param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq $Name -and $n.Right.Extent.Text -match $RightMatch }.GetNewClosure()
    @($ast.FindAll($pred, $true))
}
function Get-Code([string] $Name, [string] $RightMatch = '') {
    $f = Find-Assign $Name $RightMatch
    [scriptblock]::Create($(if ($f.Count -ge 1) { $f[0].Extent.Text } else { "`$$($Name.TrimStart('$')) = `$null" }))
}
$found = (Find-Assign '$iso').Count
$local = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$testAppData' -and $n.Right.Extent.Text -match 'LOCALAPPDATA' }, $true).Count
$argParam = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandParameterAst] -and $n.ParameterName -eq 'Arguments' -and $n.Parent.CommandElements[0].Extent.Text -eq 'Start-IsolatedNativeProcess' -and $n.Parent.Extent.Text -match 'dotnet' }, $true)
$argUse = if ($argParam.Count -eq 1) { $argParam[0].Parent.CommandElements[$argParam[0].Parent.CommandElements.IndexOf($argParam[0]) + 1].Extent.Text } else { "count=$($argParam.Count)" }
$overlap = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.CommandElements[0].Extent.Text -eq 'Get-OverlapSignatureKind' }, $true)
$overlapArg = @($overlap | ForEach-Object {
    $els = $_.CommandElements; $i = 0
    for ($k = 0; $k -lt $els.Count; $k++) { if ($els[$k] -is [System.Management.Automation.Language.CommandParameterAst] -and $els[$k].ParameterName -eq 'GodotLog') { $i = $k + 1 } }
    if ($i) { $els[$i].Extent.Text }
})
$pipeEnv = @(Find-Assign '$env:GDUNIT4_PIPE_SUFFIX' | ForEach-Object { $_.Right.Extent.Text })
$safeCode = Get-Code '$safe'
$rootCode = Get-Code '$runRoot'
$dirCode  = Get-Code '$runDir'
$trxCode  = Get-Code '$trxPath'
$passCode = Get-Code '$passTrx'
$logCode  = Get-Code '$godotLog'
$isoCode  = Get-Code '$iso'
$argsCode = Get-Code '$testArgs'
$Filter = 'FullyQualifiedName~X'
$Label = $env:RTS_LABEL
$out = foreach ($repo in (ConvertFrom-Json $env:RTS_REPOS)) {
    foreach ($mode in 'plain', 'tagged') {
        $ResultsRoot = if ($mode -eq 'tagged') { 'TestResults/runs/gate-x' } else { '' }
        $safe = $null; $iso = $null; $runRoot = $null; $runDir = $null; $trxPath = $null; $passTrx = $null; $godotLog = $null; $testArgs = $null
        . $safeCode; . $rootCode; . $dirCode; . $trxCode; . $passCode; . $logCode; . $isoCode; . $argsCode
        $own = Get-TestIsolation -Repo $repo
        [pscustomobject]@{ repo = $repo; mode = $mode; dir = [string]$own.AppData; arg = [string]$own.AppDataArg; suffix = [string]$own.PipeSuffix
                           wrapperDir = [string]$(if ($iso) { $iso.AppData }); wrapperSuffix = [string]$(if ($iso) { $iso.PipeSuffix }); wrapperArg = [string]$(if ($iso) { $iso.AppDataArg })
                           args = @(if ($testArgs) { $testArgs | ForEach-Object { [string]$_ } }); id = (Get-WorktreeId $repo); safe = [string]$safe; runDir = [string]$runDir; trx = [string]$trxPath; godotLog = [string]$godotLog
                           tag = [string]$(if (Get-Command Get-RunTag -ErrorAction SilentlyContinue) { Get-RunTag -Label $Label }) }
    }
}
$capMsg = ''; $edge = $null; $chars12 = ''; $tagDefault = ''
$r0 = (ConvertFrom-Json $env:RTS_REPOS)[0]
try { $null = Get-TestIsolation -Repo $r0 -Tag 'ABCDEFGH'; $capMsg = 'NO THROW' } catch { $capMsg = $_.Exception.Message }
try { $edge = Get-TestIsolation -Repo $r0 -Tag 'ABCDEFG' } catch { }
if (Get-Command Get-RunTag -ErrorAction SilentlyContinue) { $chars12 = Get-RunTag -Label 'x' -Chars 12; $tagDefault = Get-RunTag -Label 'x' }
ConvertTo-Json -Compress -Depth 5 @{ found = $found; localCopies = $local; runs = @($out); localAppData = $env:LOCALAPPDATA
    argUse = $argUse; overlapArg = @($overlapArg); pipeEnv = @($pipeEnv); capMsg = $capMsg; edgeSuffix = [string]$(if ($edge) { $edge.PipeSuffix })
    pipeMax = [int]$(if (Test-Path variable:PipeSuffixMax) { $PipeSuffixMax } else { 0 }); chars12 = $chars12; tagDefault = $tagDefault }
"""

FUNCS = r"""
$ScriptPath = $env:RTS_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
foreach ($name in 'Write-FailureLines', 'Write-PhaseMs') {
    $f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true))
    if ($f.Count -eq 1) { . ([scriptblock]::Create($f[0].Extent.Text)) }
}
$own = $env:RTS_OWN; $dir = $env:RTS_DIR
$a = @(if (Get-Command Write-FailureLines -ErrorAction SilentlyContinue) { Write-FailureLines -OwnTrx $own -Since ([DateTimeOffset]::FromUnixTimeSeconds(1799999499).LocalDateTime) })
$b = @(if (Get-Command Write-FailureLines -ErrorAction SilentlyContinue) { Write-FailureLines -OwnTrx '' -Since (Get-Date).AddHours(-1) })
$s = @(if (Get-Command Write-FailureLines -ErrorAction SilentlyContinue) { Write-FailureLines -OwnTrx $own -Since ([DateTimeOffset]::FromUnixTimeSeconds(1799999501).LocalDateTime) })
$p = @(if (Get-Command Write-PhaseMs -ErrorAction SilentlyContinue) { Write-PhaseMs -Name 'pipe-drain' -Ms 5 })
ConvertTo-Json -Compress @{ own = @($a); newest = @($b); stale = @($s); phase = @($p) }
"""


HELD = r"""
$ScriptPath = $env:RTS_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
. (Join-Path (Split-Path $ScriptPath) 'GodotProcess.ps1')
. (Join-Path (Split-Path $ScriptPath) 'RuntimeSlots.ps1')
function Get-Code([string] $Name, [string] $RightMatch = '') {
    $f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq $Name -and $n.Right.Extent.Text -match $RightMatch }.GetNewClosure(), $true))
    [scriptblock]::Create($(if ($f.Count -ge 1) { $f[0].Extent.Text } else { "`$$($Name.TrimStart('$')) = `$null" }))
}
$envAssign = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$env:GDUNIT4_SKIP_RECOMPILE' }, $true))
$envCode = if ($envAssign.Count -eq 1) { [scriptblock]::Create($envAssign[0].Right.Extent.Text) } else { { 'NO-ASSIGNMENT' } }
$Filter = 'FullyQualifiedName~X'; $Label = 'Logic'; $repo = 'C:\work\{{PROJECT_NAME}}'; $ResultsRoot = 'TestResults/runs/gate-x'
$out = foreach ($c in @(@{ n = 'none'; r = $null }, @{ n = 'built'; r = [pscustomobject]@{ built = $true; recompiled = $false } },
                         @{ n = 'both'; r = [pscustomobject]@{ built = $true; recompiled = $true } })) {
    $heldRecord = $c.r; $noBuild = $null; $skipRecompile = $null; $safe = $null; $runRoot = $null; $runDir = $null; $godotLog = $null; $iso = $null; $testArgs = $null
    . (Get-Code '$noBuild'); . (Get-Code '$skipRecompile'); . (Get-Code '$safe'); . (Get-Code '$runRoot'); . (Get-Code '$runDir')
    . (Get-Code '$trxPath'); . (Get-Code '$passTrx'); . (Get-Code '$godotLog'); . (Get-Code '$iso'); . (Get-Code '$testArgs')
    [pscustomobject]@{ n = $c.n; args = @($testArgs | ForEach-Object { [string]$_ }); env = [string](& $envCode) }
}
$funcs = @{}
foreach ($name in 'Assert-RecompileSkipped', 'Get-OwnTreeReap', 'Get-SelfTreePids', 'Write-ReapLine', 'Invoke-HeldPreCheck') {
    $f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true))
    $funcs[$name] = if ($f.Count -eq 1) { $f[0].Extent.Text } else { '' }
}
ConvertTo-Json -Compress -Depth 5 @{ runs = @($out); envAssigns = $envAssign.Count; funcs = $funcs }
"""

REAP = r"""
. ([scriptblock]::Create($env:RTS_FUNCS))
function P($id, $ppid, $name, $cmd = '') { [pscustomobject]@{ ProcessId = $id; ParentProcessId = $ppid; Name = $name; CommandLine = $cmd; CreationDate = [datetime]'2026-01-01' } }
$map = @{}
foreach ($p in @((P 50 1 'pwsh.exe' 'pwsh -File regression_gate.ps1'),
                 (P 100 50 'pwsh.exe' 'pwsh -File run_test_suite.ps1 -HeldCheckout'), (P 101 100 'dotnet.exe' 'dotnet test --settings .runsettings'),
                 (P 102 101 'testhost.exe'), (P 103 102 'Godot_v4.exe' 'godot --headless'),
                 (P 200 50 'pwsh.exe' 'pwsh -File run_test_suite.ps1 -HeldCheckout'), (P 201 200 'dotnet.exe' 'dotnet test --settings .runsettings'),
                 (P 202 201 'testhost.exe'), (P 203 202 'Godot_v4.exe' 'godot --headless'))) { $map[[int]$p.ProcessId] = $p }
$r = Get-OwnTreeReap -Map $map -RootPid 100
Write-ReapLine -Site 'own-tree:Logic' -Kills $r.Kills -Spares $r.Spares
"""


RETRY = r"""
$ScriptPath = $env:RTS_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
. (Join-Path (Split-Path $ScriptPath) 'GodotProcess.ps1')
foreach ($name in 'Clear-RunDirGodot', 'Write-ReapLine') {
    $f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true))
    if ($f.Count -eq 1) { . ([scriptblock]::Create($f[0].Extent.Text)) }
}
function P($id, $ppid, $name, $cmd = '') { [pscustomobject]@{ ProcessId = $id; ParentProcessId = $ppid; Name = $name; CommandLine = $cmd; CreationDate = [datetime]'2026-01-01' } }
$map = @{}
foreach ($p in @((P 301 999 'Godot_v4.exe' 'godot --headless --log-file C:/r/gate-x/Logic/godot_test.log'),
                 (P 302 202 'Godot_v4.exe' 'godot --headless --log-file C:/r/gate-x/Integration/godot_test.log'),
                 (P 303 998 'Godot_v4.exe' 'godot --headless --log-file C:/r/gate-x/Logic2/godot_test.log'))) { $map[[int]$p.ProcessId] = $p }
$out = @(if (Get-Command Clear-RunDirGodot -ErrorAction SilentlyContinue) { Clear-RunDirGodot -RunDir 'C:\r\gate-x\Logic' -Site 'run-dir:Logic:retry' -Map $map -DryRun })
$loop = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.WhileStatementAst] }, $true))
$resetInLoop = @($loop | ForEach-Object { $_.Body.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$selfOverlap' -and $n.Right.Extent.Text -eq '$false' }, $true) }).Count
ConvertTo-Json -Compress @{ reap = @($out | ForEach-Object { [string]$_ }); loops = $loop.Count; resetInLoop = $resetInLoop }
"""


def retry_cases(script):
    """A held child's retry rebinds its own pipe: it first kills its run dir's Godot (which outlives an exited test host
    and escapes the own-tree reap) and settles. The self-overlap marker describes the attempt that reports."""
    d = pwsh(RETRY, dict(RTS_SCRIPT=script))
    src = open(script, encoding="utf-8").read()
    reap = d.get("reap") or []
    kills = sorted(int(m.group(1)) for l in reap for m in [re.match(r"^\s+kill\s+(\d+) \S+ run-dir$", l)] if m)
    return [
        ("held retry reap: only the Godot naming this run's dir is killed, a sibling's and a prefix-sharing dir's are not (%s)" % reap,
         kills == [301] and "REAP site=run-dir:Logic:retry killed=1 spared=0" in reap),
        ("a held child's retry reaps its run dir's Godot and settles its unique pipe before it rebinds",
         re.search(r"if \(\$HeldCheckout -and \$attempt -gt 1\) \{[^\n]*\n[^\n]*Clear-RunDirGodot -RunDir \$runDir[^\n]*\n"
                   r"[^\n]*Get-PipeSettleMs -UniquePipe \$true -Attempt \$attempt", src) is not None),
        ("selfOverlap resets inside the attempt loop, so a retry's clean attempt carries no stale marker (%s)" % d,
         d.get("resetInLoop") == 1),
    ]


def lock_error_cases(script):
    """A held child whose Enter-RunLocks throws: the wrapper's own catch prints the STATUS line. The sandbox copy of
    RuntimeSlots.ps1 admits the pre-check and plants the throw, so the run stops before any lock, reap or dotnet."""
    tmp = tempfile.mkdtemp(prefix="rtslock_")
    try:
        scripts = os.path.join(tmp, "repo", ".claude", "scripts")
        os.makedirs(scripts)
        src_dir = os.path.dirname(script)
        for name in ("run_test_suite.ps1", "GodotProcess.ps1", "RuntimeSlots.ps1", "TwinCheckout.ps1", "PeerActivity.ps1", "IntegrationBatches.ps1"):
            if os.path.exists(os.path.join(src_dir, name)):
                shutil.copyfile(os.path.join(src_dir, name), os.path.join(scripts, name))
        open(os.path.join(tmp, "repo", ".runsettings"), "w").close()
        os.makedirs(os.path.join(tmp, "repo", "Tests"))
        # A checkout's committed duration manifests: the default ceiling reads both, as in a real repo.
        open(os.path.join(tmp, "repo", "Tests", "integration_batch_durations.json"), "w", encoding="utf-8", newline="\n").write('{"units": {"Logic": {"seconds": 10}}}')
        open(os.path.join(tmp, "repo", "Tests", "regression_baseline.json"), "w", encoding="utf-8", newline="\n").write('{"suites": {"Logic": {"duration_sec": 10}}}')
        temp = os.path.join(tmp, "temp")
        os.makedirs(temp)
        rs = os.path.join(scripts, "RuntimeSlots.ps1")
        base = open(rs, encoding="utf-8").read()

        def run(message):
            with open(rs, "w", encoding="utf-8", newline="\n") as f:
                f.write(base + "\nfunction Get-HeldCheckoutDenials { @() }\nfunction Enter-RunLocks { throw '%s' }\n" % message)
            return run_raw(["pwsh", "-NoProfile", "-File", os.path.join(scripts, "run_test_suite.ps1"), "-Filter",
                            "FullyQualifiedName~X", "-Label", "Logic", "-IgnoreEditor", "-HeldCheckout", "-ResultsRoot", "r"],
                           {"TEMP": temp, "TMP": temp, "TMPDIR": temp}, cwd=tmp)

        cases = []
        for message, want, unwanted in (("planted lock failure", "unmet=lock-error  error=planted lock failure", None),
                                        ("held checkout refused: holder-dead", "held checkout refused: holder-dead", "unmet=lock-error")):
            code, out, err = run(message)
            line = next((l for l in out.splitlines() if l.startswith("STATUS=HELD_REFUSED")), "")
            cases.append(("a held child whose lock throws '%s' exits 122 with that reason (exit=%s line=%r err=%r)" % (message, code, line, err[-300:]),
                          code == 122 and want in line and (unwanted is None or unwanted not in line)
                          and "LOCKWAIT_MS=" not in out))
        return cases
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


A8 = r"""
$ScriptPath = $env:RTS_SCRIPT
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
. (Join-Path (Split-Path $ScriptPath) 'GodotProcess.ps1')
foreach ($name in 'Get-PipeSettleMs', 'Get-DrainWaitSet') {
    $f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true))
    if ($f.Count -eq 1) { . ([scriptblock]::Create($f[0].Extent.Text)) }
}
$repo = 'C:\work\{{PROJECT_NAME}}'
function P($id, $ppid, $name, $exe = '') { [pscustomobject]@{ ProcessId = $id; ParentProcessId = $ppid; Name = $name; ExecutablePath = $exe; CommandLine = ''; CreationDate = [datetime]'2026-01-01' } }
$own = P 9001 1 'testhost.exe' "$repo\.godot\mono\temp\bin\Debug\testhost.exe"
$peer = P 9002 1 'testhost.exe' "$repo\.claude\worktrees\b\.godot\mono\temp\bin\Debug\testhost.exe"
$script:Snaps = 0
$fresh = { $script:Snaps++; @{ 9001 = $own; 9002 = $peer } }
$settle = @{}
if (Get-Command Get-PipeSettleMs -ErrorAction SilentlyContinue) {
    $settle = [ordered]@{ uniqueFirst = Get-PipeSettleMs -UniquePipe $true -Attempt 1; shared = Get-PipeSettleMs -UniquePipe $false -Attempt 1
                          sharedRetry = Get-PipeSettleMs -UniquePipe $false -Attempt 2; uniqueRetry = Get-PipeSettleMs -UniquePipe $true -Attempt 2 }
}
$drain = @{}
if (Get-Command Get-DrainWaitSet -ErrorAction SilentlyContinue) {
    $live = @([pscustomobject]@{ Id = 9001; ProcessName = 'testhost' })
    $stale = @(Get-DrainWaitSet -Repo $repo -Map @{ 1 = (P 1 0 'System') } -Live $live -Snapshot $fresh); $staleSnaps = $script:Snaps; $script:Snaps = 0
    $covered = @(Get-DrainWaitSet -Repo $repo -Map @{ 9001 = $own } -Live $live -Snapshot $fresh); $coveredSnaps = $script:Snaps; $script:Snaps = 0
    $peerSet = @(Get-DrainWaitSet -Repo $repo -Map @{ 9002 = $peer } -Live @([pscustomobject]@{ Id = 9002; ProcessName = 'testhost' }) -Snapshot $fresh)
    $drain = [ordered]@{ stale = @($stale | ForEach-Object { [int]$_.Id }); staleSnaps = $staleSnaps; covered = @($covered | ForEach-Object { [int]$_.Id })
                         coveredSnaps = $coveredSnaps; peer = @($peerSet | ForEach-Object { [int]$_.Id }) }
}
ConvertTo-Json -Compress -Depth 4 @{ settle = $settle; drain = $drain }
"""


PPP = r"""
$ScriptPath = $env:RTS_SCRIPT
$dir = Split-Path $ScriptPath
. (Join-Path $dir 'GodotProcess.ps1'); . (Join-Path $dir 'RuntimeSlots.ps1'); . (Join-Path $dir 'PeerActivity.ps1')
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
$f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-PeerProtectedPids' }, $true))
if ($f.Count -ne 1) { ConvertTo-Json -Compress @{ defined = $false }; return }
. ([scriptblock]::Create($f[0].Extent.Text))
$env:TEMP = $env:RTS_TMP
$env:TMPDIR = $env:RTS_TMP
$env:CLAUDE_SESSION_ID = 'sess-ppp'
$act = Join-Path $env:TEMP 'harness-activity'
New-Item -ItemType Directory -Force -Path $act | Out-Null
$live = Get-Process -Id ([int]$env:RTS_LIVE)
foreach ($p in @($live, (Get-Process -Id $PID))) {
    [ordered]@{ pid = $p.Id; procStart = $p.StartTime.ToUniversalTime().ToString('o'); kind = 'suite'; checkout = 'C:\x'; sessionId = 'sess-ppp'; label = "p$($p.Id)" } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $act "suite-$($p.Id).json")
}
$set = Get-PeerProtectedPids -Map (Get-ProcSnapshot) -Repo 'C:\x'
ConvertTo-Json -Compress @{ defined = $true; live = $set.Contains($live.Id); self = $set.Contains($PID); label = [string]$script:PeerLabels[$live.Id] }
"""


def ppp_cases(script):
    """The wrapper's reaper spares every live record but its own (activity_registry.py's no-ownership policy), now read
    through PeerActivity.ps1: a same-checkout, same-session record is protected; the wrapper's own record is not."""
    tmp = tempfile.mkdtemp(prefix="rtsppp_")
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        d = pwsh(PPP, dict(RTS_SCRIPT=script, RTS_TMP=tmp, RTS_LIVE=str(live.pid)))
    finally:
        live.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    return [
        ("Get-PeerProtectedPids spares a live same-session record: the reaper keeps no ownership axis (%s)" % d,
         d.get("defined") and d.get("live") is True and "label='p%d'" % live.pid in (d.get("label") or "")),
        ("Get-PeerProtectedPids does not protect the wrapper's own record", d.get("defined") and d.get("self") is False),
    ]


def a8_cases(script):
    """A8 overhead cuts, each with its planted-condition guard, plus the A7 wrapper seams (owner root, peer reader,
    overlap judgement)."""
    d = pwsh(A8, dict(RTS_SCRIPT=script))
    st = d.get("settle") or {}
    dr = d.get("drain") or {}
    src = open(script, encoding="utf-8").read()
    ppp = re.search(r"(?s)function Get-PeerProtectedPids \{.*?\n\}", src)
    return [
        ("settle cut: a unique-pipe run's first attempt skips the settle (Get-PipeSettleMs = 0)", st.get("uniqueFirst") == 0),
        ("settle guard: a shared-pipe run still settles 2500 ms", st.get("shared") == 2500 and st.get("sharedRetry") == 2500),
        ("settle guard: a unique-pipe retry reuses its own pipe and still settles", st.get("uniqueRetry") == 2500),
        ("the attempt loop takes its settle from Get-PipeSettleMs and still prints PHASE_MS settle",
         re.search(r"Get-PipeSettleMs -UniquePipe \(\[bool\]\$ResultsRoot\) -Attempt \$attempt", src) is not None
         and "Write-PhaseMs -Name 'settle'" in src),
        ("snapshot cut: a snapshot covering every live candidate is reused, no second snapshot (%s)" % dr,
         dr.get("covered") == [9001] and dr.get("coveredSnaps") == 0),
        ("snapshot guard: a stale process that appeared after the snapshot forces a fresh one and is still waited on",
         dr.get("stale") == [9001] and dr.get("staleSnaps") == 1),
        ("a peer worktree's live testhost is not waited on", dr.get("peer") == []),
        ("the suite reap hands its snapshot to the first drain pass",
         "$script:ReapMap = $map" in src and re.search(r"Wait-PipeDrained -Repo \$repo -SettleMs [^\n]*-Map \$script:ReapMap", src) is not None),
        ("the wrapper keeps no Get-OwnerRoot of its own and walks Get-RunOwnerRoot once for its activity record",
         re.search(r"(?m)^function Get-OwnerRoot\b", src) is None and re.search(r"\$owner = Get-RunOwnerRoot\b", src) is not None
         and len(re.findall(r"(?<!function )\bGet-RunOwnerRoot\b", src)) == 1
         and "ownerRootPid   = $(if ($owner)" in src and "ownerRootStart = $(if ($owner)" in src),
        ("Get-PeerProtectedPids reads through the shared Get-LivePeerActivity and keeps its no-ownership policy",
         ppp is not None and "Get-LivePeerActivity -OwnRoots @($PID)" in ppp.group(0) and "harness-activity" not in ppp.group(0)),
        ("the wrapper logs an overlap trigger only through Register-OverlapSignature",
         "Register-OverlapSignature" in src and "Add-RuntimeSlotTrigger" not in src),
        ("an own-pool overlap marks selfOverlap=1 on the STATUS=DONE line",
         re.search(r"STATUS=DONE[^\n]*selfOverlap=1", src) is not None),
        ("the run dir name comes from the one label-to-dir home", re.search(r"\$safe\s*=\s*Get-SafeRunLabel ", src) is not None),
    ]


def pwsh(code, env_extra):
    env = dict(os.environ, **env_extra)
    proc = subprocess.run(["pwsh", "-NoProfile", "-Command", code], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("CRASH pwsh exit %d: %s" % (proc.returncode, proc.stderr.strip()[:600]))
        sys.exit(2)
    try:
        return json.loads(proc.stdout.strip())
    except ValueError:
        print("CRASH unparseable output: %s | %s" % (proc.stdout.strip()[:400], proc.stderr.strip()[:300]))
        sys.exit(2)


def evaluate(script, repos):
    data = pwsh(EVAL, dict(RTS_SCRIPT=script, RTS_REPOS=json.dumps(repos), RTS_LABEL=LABEL))
    if not isinstance(data, dict) or len(data.get("runs") or []) != 2 * len(repos):
        print("CRASH expected %d runs, got: %s" % (2 * len(repos), str(data)[:400]))
        sys.exit(2)
    return data


TRX = ('<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
       '<Results><UnitTestResult testName="%s" outcome="Failed"><Output><ErrorInfo><Message>%s</Message></ErrorInfo></Output>'
       '</UnitTestResult><UnitTestResult testName="Ns.Passing" outcome="Passed" /></Results></TestRun>')


def failure_lines(script):
    with tempfile.TemporaryDirectory(prefix="rtsfail_") as tmp:
        own = os.path.join(tmp, "own.trx")
        sibling = os.path.join(tmp, "sibling.trx")
        with open(own, "w", encoding="utf-8", newline="\n") as f:
            f.write(TRX % ("Ns.OwnFailure", "boom own\nsecond line"))
        with open(sibling, "w", encoding="utf-8", newline="\n") as f:
            f.write(TRX % ("Ns.SiblingFailure", "boom sibling"))
        os.utime(own, (1_800_000_000 - 500, 1_800_000_000 - 500))
        os.utime(sibling, (1_800_000_000, 1_800_000_000))
        return pwsh(FUNCS, dict(RTS_SCRIPT=script, RTS_OWN=own, RTS_DIR=tmp))


def log_token(args, log):
    """Whether `args` hold a GdUnit4.Parameters token whose --log-file is `log`; engine flags may follow it."""
    want = "GdUnit4.Parameters=--log-file " + log.replace("\\", "/")
    return any(t == want or t.startswith(want + " ") for t in args)


def no_embedded_quote(tokens):
    """One outer quote pair is the argv wrapper; a quote left inside is embedded."""
    for t in tokens:
        inner = t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t
        if '"' in inner:
            return False
    return True


def run_raw(args, env_extra=None, cwd=None):
    env = dict(os.environ, **(env_extra or {}))
    proc = subprocess.run(args, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


SKIP_LINE = "Skipping Godot project recompile (GDUNIT4_SKIP_RECOMPILE=1)"
RUN_TRX = ('<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
           '<ResultSummary outcome="Completed">%s<Output><StdOut>%s</StdOut></Output></ResultSummary></TestRun>')


def recompile_check(func_text, tmp, with_line, executed=None, runner="GodotRuntimeTestRunner"):
    """Runs the wrapper's Assert-RecompileSkipped in a child: its stdout and exit code are the wrapper's own channel."""
    tag = "%d_%s_%s" % (with_line, executed, runner)
    trx = os.path.join(tmp, "rc_%s.trx" % tag)
    counters = "" if executed is None else '<Counters total="%d" executed="%d" passed="%d" failed="0" />' % (executed, executed, executed)
    with open(trx, "w", encoding="utf-8", newline="\n") as f:
        f.write(RUN_TRX % (counters, "[{{PROJECT_NAME}}] Starting %s\n" % runner + (SKIP_LINE + "\n" if with_line else "") + "Run finished"))
    script = os.path.join(tmp, "rc_%s.ps1" % tag)
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(func_text + "\nAssert-RecompileSkipped -Trx '%s' -Label 'Logic'\nWrite-Output 'CONTINUED'\nexit 0\n" % trx)
    return run_raw(["pwsh", "-NoProfile", "-File", script])


def worktree_id(path):
    return hashlib.sha1(path.rstrip("\\/").lower().encode("utf-8")).hexdigest()[:8]


def held_sandbox(script):
    """Runs a byte-for-byte copy of the wrapper from a sandbox checkout, with TEMP pointed at the sandbox so its record
    dir is planted. Each run must stop at the held pre-check, before any lock, reap or dotnet call."""
    tmp = tempfile.mkdtemp(prefix="rtsheld_")
    scripts = os.path.join(tmp, "repo", ".claude", "scripts")
    os.makedirs(scripts)
    src_dir = os.path.dirname(script)
    for name in ("run_test_suite.ps1", "GodotProcess.ps1", "RuntimeSlots.ps1", "PeerActivity.ps1", "IntegrationBatches.ps1"):
        if not os.path.exists(os.path.join(src_dir, name)):
            continue
        with open(os.path.join(src_dir, name), "rb") as a, open(os.path.join(scripts, name), "wb") as b:
            b.write(a.read())
    temp = os.path.join(tmp, "temp")
    recdir = os.path.join(temp, "harness-activity", "runtime-slots")
    os.makedirs(recdir)
    _, full, _ = run_raw(["pwsh", "-NoProfile", "-Command", "(Get-Item '%s').FullName" % os.path.join(tmp, "repo")])
    rec_path = os.path.join(recdir, "checkout-%s.json" % worktree_id(full.strip()))
    wrapper = os.path.join(scripts, "run_test_suite.ps1")
    env = {"TEMP": temp, "TMP": temp, "TMPDIR": temp}

    def run(*extra):
        return run_raw(["pwsh", "-NoProfile", "-File", wrapper, "-Filter", "FullyQualifiedName~X", "-Label", "Logic",
                        "-IgnoreEditor"] + list(extra), env, cwd=tmp)

    results = {}
    results["no_root"] = run("-HeldCheckout")
    results["pass_only"] = run("-RecompilePass", "-ResultsRoot", "r")
    results["missing"] = run("-HeldCheckout", "-ResultsRoot", "r")
    with open(rec_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"holderPid": os.getpid(), "procStart": "2001-01-01T00:00:00.0000000Z", "checkout": full.strip(),
                   "built": False, "recompiled": False}, f)
    results["dead"] = run("-HeldCheckout", "-ResultsRoot", "r")
    return tmp, results


def held_cases(script):
    """Held mode (-HeldCheckout / -RecompilePass): validity rules through the wrapper's real output channel, the derived
    --no-build and recompile opt-out, the skip-took-effect check and the own-tree reap classifier."""
    h = pwsh(HELD, dict(RTS_SCRIPT=script))
    runs = {r["n"]: r for r in h.get("runs") or []}
    funcs = h.get("funcs") or {}
    strip = lambda t: t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t
    args = {k: [strip(t) for t in v["args"]] for k, v in runs.items()}
    cases = []
    tmp, sb = held_sandbox(script)
    try:
        def refused(key, *needles):
            code, out, err = sb[key]
            line = next((l for l in out.splitlines() if l.startswith("STATUS=HELD_REFUSED")), "")
            return code not in (0, None) and bool(line) and all(n in line for n in needles), "exit=%s line=%r err=%r" % (code, line, err[-300:])
        ok, why = refused("no_root", "results-root")
        cases.append(("-HeldCheckout without -ResultsRoot is refused on stdout with a non-zero exit (%s)" % why, ok))
        ok, why = refused("pass_only", "recompile-pass")
        cases.append(("-RecompilePass without -HeldCheckout is refused on stdout with a non-zero exit (%s)" % why, ok))
        ok, why = refused("missing", "unmet=no-checkout-record")
        cases.append(("a held run with no checkout record is refused, naming the rule (%s)" % why, ok))
        ok, why = refused("dead", "holder=%d" % os.getpid(), "chain=", "holder-dead", "not-built", "not-recompiled")
        cases.append(("one deny names the holder, the caller's chain and every unmet rule (%s)" % why, ok))
        no_touch = all("LOCKWAIT_MS=" not in sb[k][1] and "REAP " not in sb[k][1] for k in sb)
        cases.append(("a refused held run stops before any lock or reap", no_touch))

        cases.append(("the wrapper sets GDUNIT4_SKIP_RECOMPILE in exactly one assignment", h.get("envAssigns") == 1))
        cases.append(("a non-held run passes no --no-build and no recompile opt-out",
                      "none" in args and "--no-build" not in args["none"] and runs["none"]["env"] == ""))
        cases.append(("a held child with built=true derives --no-build, and without recompiled no opt-out",
                      "built" in args and "--no-build" in args["built"] and runs["built"]["env"] == ""))
        cases.append(("a held child with recompiled=true derives the recompile opt-out",
                      "both" in args and "--no-build" in args["both"] and runs["both"]["env"] == "1"))
        cases.append(("--no-build sits before the -- separator",
                      "built" in args and "--no-build" in args["built"] and "--" in args["built"]
                      and args["built"].index("--no-build") < args["built"].index("--")))
        wrapper_src = open(script, encoding="utf-8").read()
        cases.append(("the derivation carries its why: the holder built this tree and agents never pass --no-build",
                      re.search(r"#[^\n]*holder built[^\n]*\n(?:[^\n]*\n){0,3}[^\n]*\$noBuild =", wrapper_src) is not None))

        rc = funcs.get("Assert-RecompileSkipped", "")
        cases.append(("the wrapper defines Assert-RecompileSkipped", bool(rc)))
        if rc:
            code, out, _ = recompile_check(rc, tmp, False)
            cases.append(("opt-out set, no skip line in the TRX run-level StdOut -> STATUS=SKIP_RECOMPILE_IGNORED and a non-zero exit",
                          "STATUS=SKIP_RECOMPILE_IGNORED" in out and code != 0 and "CONTINUED" not in out))
            code, out, _ = recompile_check(rc, tmp, True)
            cases.append(("the same TRX with the skip line -> no such status, the run continues",
                          "SKIP_RECOMPILE_IGNORED" not in out and code == 0 and "CONTINUED" in out))
            code, out, _ = recompile_check(rc, tmp, False, executed=12)
            cases.append(("tests executed with no skip line -> STATUS=SKIP_RECOMPILE_IGNORED",
                          "STATUS=SKIP_RECOMPILE_IGNORED" in out and code != 0 and "CONTINUED" not in out))
            code, out, _ = recompile_check(rc, tmp, False, executed=3, runner="DefaultTestRunner")
            cases.append(("tests ran only on the direct runner, which never reaches the recompile step -> no status",
                          "SKIP_RECOMPILE_IGNORED" not in out and code == 0 and "CONTINUED" in out))
            code, out, _ = recompile_check(rc, tmp, False, executed=0)
            cases.append(("a run that executed no test proves nothing about the opt-out -> no status, left to the TRX checks",
                          "SKIP_RECOMPILE_IGNORED" not in out and code == 0 and "CONTINUED" in out))
        cases.append(("the wrapper calls Assert-RecompileSkipped only when it set the opt-out",
                      re.search(r"if \(\$skipRecompile[^\n]*Assert-RecompileSkipped", wrapper_src) is not None))

        reap = funcs.get("Get-OwnTreeReap", "")
        cases.append(("the wrapper defines Get-OwnTreeReap", bool(reap)))
        if reap:
            code, out, err = run_raw(["pwsh", "-NoProfile", "-Command", REAP],
                                     {"RTS_FUNCS": "\n".join([reap, funcs.get("Get-SelfTreePids", ""), funcs.get("Write-ReapLine", "")])})
            kills = sorted(int(m) for m in re.findall(r"^\s+kill\s+(\d+) \S+ own-tree", out, re.M))
            spares = sorted(int(m) for m in re.findall(r"^\s+spare\s+(\d+)", out, re.M))
            cases.append(("-ReapReport held classifier: the child's own-tree decoys are kill ... own-tree (%s)" % out.strip()[:200],
                          code == 0 and kills == [101, 102, 103]))
            cases.append(("-ReapReport held classifier: a sibling held child's live processes are spare",
                          code == 0 and spares == [201, 202, 203] and not set(kills) & {200, 201, 202, 203}))
            cases.append(("the own-tree REAP line prints its counts", re.search(r"^REAP site=own-tree:Logic killed=3 spared=3$", out, re.M) is not None))
        cases.append(("a held run skips the checkout-wide sweep and the pipe drain, and reaps its own tree with a REAP line",
                      re.search(r"if \(-not \$HeldCheckout\) \{[^\n]*Clear-TestRunners", wrapper_src) is not None
                      and "Wait-PipeDrained" in wrapper_src and re.search(r"-not \$NoGodotRuntime -and -not \$HeldCheckout", wrapper_src) is not None
                      and "Clear-OwnTree" in wrapper_src))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return cases


def main():
    if sys.platform != "win32":
        # The isolation cases assert Windows paths, a LOCALAPPDATA base and the APPDATA key; the Linux isolation (XDG_DATA_HOME) is proven in test_RuntimeSlots.py. The held-mode and reap cases are skipped with them and have not run on Linux.
        import _platform_skip
        _platform_skip.skip_proof("test_run_test_suite_userdir", "whole file; Windows-shaped isolation assertions (Linux isolation is covered by test_RuntimeSlots.py); held-mode and reap cases not run on Linux")
    script = sys.argv[sys.argv.index("--script") + 1] if "--script" in sys.argv[:-1] else DEFAULT_SCRIPT
    repos = [r"C:\work\{{PROJECT_NAME}}", r"C:\work\{{PROJECT_NAME}}\.claude\worktrees\b"]
    data = evaluate(script, repos)
    plain_a, tag_a, plain_b, tag_b = data["runs"]
    a = plain_a
    base = os.path.normcase(os.path.normpath(data["localAppData"]))
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    strip = lambda t: t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t
    tag = hashlib.sha256(LABEL.encode("utf-8")).hexdigest().upper()[:PIPE_SUFFIX_MAX - 8 - 1]
    safe = re.sub(r"[^\w.-]", "_", LABEL)
    targs = [strip(t) for t in tag_a["args"]]
    tokens = tag_a["args"]
    wrapper_src = open(script, encoding="utf-8").read()
    gp_src = open(os.path.join(os.path.dirname(script), "GodotProcess.ps1"), encoding="utf-8").read()
    fl = failure_lines(script)
    planted = '"GdUnit4.Parameters=--log-file C:/a "b"/godot_test.log"'
    cases = [
        ("the wrapper takes its isolation from Get-TestIsolation exactly once ($iso = ...)", data["found"] == 1),
        ("the wrapper keeps no local copy of the user-dir path", data["localCopies"] == 0),
        ("the wrapper's user dir is Get-TestIsolation's", a["wrapperDir"] == a["dir"] and plain_b["wrapperDir"] == plain_b["dir"]),
        ("the directory sits under LOCALAPPDATA", norm(a["dir"]).startswith(base + os.sep)),
        ("the directory and pipe suffix carry the worktree id (%s)" % a["id"],
         a["dir"].rstrip(os.sep + "/").endswith(a["id"]) and a["suffix"] == a["id"]),
        ("two repo paths get different directories and suffixes", a["dir"] != plain_b["dir"] and a["suffix"] != plain_b["suffix"]),
        ("the directory is named test-appdata under {{PROJECT_NAME}}",
         ("{{PROJECT_NAME}}".lower() + os.sep + "test-appdata") in norm(a["dir"])),
        ("the argument list passes -- and a raw APPDATA value; the isolated launcher owns quoting",
         "--" in a["args"] and a["args"][a["args"].index("--") + 1] == a["arg"]
         and a["arg"] == "RunConfiguration.EnvironmentVariables.APPDATA=" + a["dir"].replace("\\", "/")),
        # VSTest doubles a backslash in an inline env value; Godot maps each to '/', and DirAccess.Open
        # rejects the resulting 'C://Users//...' user dir (gotcha_vstest_inline_env_backslash_breaks_godot_diraccess).
        ("the inline APPDATA value carries no backslash", "\\" not in a["arg"]),
        ("the argument list keeps its dotnet test head", a["args"][:1] == ["test"] and "--filter" in a["args"]),
        ("the isolated dotnet launcher takes its arguments from $testArgs", data["argUse"] == "$testArgs"),
        # --- per-run isolation ---
        ("Get-RunTag is the leading hex of the label's SHA-256 (default %d chars, -Chars overrides)" % len(tag),
         tag_a["tag"] == tag and data["tagDefault"] == data["tagDefault"].upper() and len(data["tagDefault"]) == len(tag)
         and len(data["chars12"]) == 12),
        ("$PipeSuffixMax is 16 and its comment cites the fork line",
         data["pipeMax"] == PIPE_SUFFIX_MAX and "GodotRuntimeTestRunner.cs:42" in gp_src and "PIPE_SUFFIX_MAX_LENGTH" in gp_src),
        ("the wrapper delegates its label cap to Get-SafeRunLabel",
         re.search(r'(?m)^\$safe\s*=\s*Get-SafeRunLabel\b', wrapper_src) is not None),
        ("a tagged run's pipe suffix and APPDATA leaf are <worktreeId>-<tag>",
         tag_a["wrapperSuffix"] == a["id"] + "-" + tag and tag_a["wrapperDir"].rstrip("\\/").endswith(a["id"] + "-" + tag)
         and tag_a["wrapperDir"].replace("\\", "/").split("/")[-2] == "test-appdata"),
        ("the wrapper sets the pipe suffix env from the isolation's suffix", data["pipeEnv"] == ["$iso.PipeSuffix"]),
        ("the tagged APPDATA token is <worktreeId>-<tag> with forward slashes",
         tag_a["wrapperArg"] == "RunConfiguration.EnvironmentVariables.APPDATA=" + tag_a["wrapperDir"].replace("\\", "/")
         and tag_a["wrapperArg"] in tokens),
        ("the run dir and the TRX are named by the sanitized label",
         os.path.basename(tag_a["runDir"]) == safe and os.path.basename(tag_a["trx"]) == safe + ".trx"
         and norm(os.path.dirname(tag_a["trx"])) == norm(tag_a["runDir"])
         and norm(os.path.dirname(tag_a["runDir"])) == norm(os.path.join(repos[0], "TestResults", "runs", "gate-x"))),
        ("--results-directory carries the run dir", "--results-directory" in targs
         and targs[targs.index("--results-directory") + 1] == tag_a["runDir"]),
        ("the trx logger token names the label's TRX", "--logger" in targs
         and targs[targs.index("--logger") + 1] == "trx;LogFileName=%s.trx" % safe),
        ("the GdUnit4.Parameters token points --log-file at the run's own godot_test.log",
         log_token(targs, tag_a["godotLog"])
         and os.path.basename(tag_a["godotLog"]) == "godot_test.log" and norm(os.path.dirname(tag_a["godotLog"])) == norm(tag_a["runDir"])),
        ("no tagged token carries an embedded quote", len(tokens) > 0 and no_embedded_quote(tokens)),
        ("the no-quote check catches a planted token with an embedded quote", len(tokens) > 0 and not no_embedded_quote(tokens + [planted])),
        ("an untagged run names its completion TRX and runtime log",
         len(a["args"]) > 0 and "--results-directory" in a["args"] and "--logger" in a["args"]
         and norm(a["args"][a["args"].index("--results-directory") + 1]) == norm(os.path.join(repos[0], "TestResults"))
         and a["args"][a["args"].index("--logger") + 1] == "trx;LogFileName=%s.trx" % a["safe"]
         and log_token(a['args'], a['godotLog'])),
        ("a tagged run gets a different pipe suffix and APPDATA dir than the untagged run of the same checkout",
         tag_a["wrapperSuffix"] != a["suffix"] and tag_a["wrapperDir"] != a["dir"]),
        ("a composed suffix over $PipeSuffixMax is rejected, naming worktreeId, tag and cap",
         a["id"] in data["capMsg"] and "ABCDEFGH" in data["capMsg"] and str(PIPE_SUFFIX_MAX) in data["capMsg"]),
        ("a composed suffix at exactly $PipeSuffixMax is accepted", data["edgeSuffix"] == a["id"] + "-ABCDEFG" and len(data["edgeSuffix"]) == PIPE_SUFFIX_MAX),
        ("the failure listing reads the run's own TRX when a newer sibling TRX exists",
         "FAILED_TEST=Ns.OwnFailure" in fl["own"] and not any("Sibling" in l for l in fl["own"])),
        ("FAILED_MSG carries the fqn and the first message line", "FAILED_MSG=Ns.OwnFailure :: boom own" in fl["own"]
         and not any("second line" in l for l in fl["own"])),
        ("a run with no TRX of its own prints NO_TRX and never a sibling's failures",
         any(l.startswith("NO_TRX") for l in fl["newest"]) and not any("Sibling" in l for l in fl["newest"])),
        ("a TRX older than the run is an earlier run's: NO_TRX, no FAILED_ lines",
         any(l.startswith("NO_TRX") for l in fl["stale"]) and not any(l.startswith("FAILED_") for l in fl["stale"])),
        ("the wrapper lists failures from the label's own TRX, written since the run started",
         "Write-FailureLines -OwnTrx $passTrx -Since $runStartedAt" in wrapper_src),
        ("the overlap classifier receives the run's own log path", data["overlapArg"] == ["$godotLog"]
         and norm(tag_a["godotLog"]).endswith(norm(os.path.join(safe, "godot_test.log")))
         and norm(a["godotLog"]) == norm(os.path.join(repos[0], "TestResults", "godot_test.log"))),
        ("the run prints TRX= and GODOT_LOG= for a tagged run", "TRX=$trxPath" in wrapper_src and "GODOT_LOG=$godotLog" in wrapper_src),
        ("Write-PhaseMs prints PHASE_MS name=<phase> ms=<n>",
         len(fl["phase"]) == 1 and re.fullmatch(r"PHASE_MS name=[a-z][a-z-]* ms=\d+", fl["phase"][0]) is not None),
        ("the wrapper reports all six pre-work phases",
         all(("Write-PhaseMs -Name '%s'" % p) in wrapper_src for p in
             ("module-load", "lock", "slot-handoff-reap", "suite-reap", "pipe-drain", "settle"))),
    ]
    cases += held_cases(script)
    cases += a8_cases(script)
    cases += ppp_cases(script)
    cases += retry_cases(script)
    cases += lock_error_cases(script)
    failed = 0
    for name, ok in cases:
        print(("ok   " if ok else "FAIL ") + name)
        failed += not ok
    print("\n%d/%d passed" % (len(cases) - failed, len(cases)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
