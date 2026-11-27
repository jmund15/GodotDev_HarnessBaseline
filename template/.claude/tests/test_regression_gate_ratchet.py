"""Proof: the gate's baseline ratchet decides every drop / skip / write row on planted facts.

``Invoke-BaselineRatchet`` is extracted from the shipped ``regression_gate.ps1`` by AST and driven in ONE child pwsh
on planted suite results, baseline text, test-WIP states and git reads. Each row covers one decision the old inline
block made only inside a full gate run: an explained WARN-band and PARTIAL-band drop, an unexplained drop, an
uncountable or incomplete one, uncommitted test changes at both timings (drop evaluation and the write), an
unreadable baseline commit, test diff and test status, and the write's own branches (growth, re-establish, engine
errors, the -NoBaselineUpdate flag, unchanged, the post-edit JSON abort, and the pool-never-ran guard). A row also
reads back the written baseline text, so the write's fields are asserted without touching a file.

The gate's wiring is asserted on the real AST (one definition, one call, its returned text written and its verdict
completed), and the gate's own call-site block is lifted verbatim from the AST and run against a real git checkout
that deleted two test attributes: the one live read boundary, and the proof that the block calls the function and
writes its result. The full gate run that would prove the wiring end to end is the project's held-gate proof; the
base harness ships no gate fixture and runs no engine, so this file never starts a gate, Godot, dotnet or a suite.

Run: python3 .claude/tests/test_regression_gate_ratchet.py
"""
import json
import os
import re
import subprocess

from _pwsh_drive import check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(REPO, ".claude", "scripts", "regression_gate.ps1")
CSHARP = os.path.join(REPO, ".claude", "scripts", "CSharpSource.ps1")
ENGINE = os.path.join(REPO, ".claude", "scripts", "EngineErrors.ps1")

DRIVER = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('@GATE@', [ref]$tokens, [ref]$errs)
function Find-Fn([string] $Name) {
    @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true))
}
$ratchetFn = @(Find-Fn 'Invoke-BaselineRatchet')
if ($ratchetFn.Count -ne 1) { Write-Output 'NO-RATCHET-FUNCTION'; exit 3 }
. ([scriptblock]::Create($ratchetFn[0].Extent.Text))
$propFn = @(Find-Fn 'Get-Prop')
. ([scriptblock]::Create($propFn[0].Extent.Text))
$wipFn = @(Find-Fn 'Get-TestWorkInProgress')
. ([scriptblock]::Create($wipFn[0].Extent.Text))
. '@CSHARP@'
. '@ENGINE@'

$rows = @(Get-Content -LiteralPath $env:ROWS -Raw | ConvertFrom-Json -AsHashtable)
$results = foreach ($row in $rows) {
    $suiteResults = [ordered]@{}
    foreach ($s in $row.suiteResults) {
        $suiteResults[[string]$s.key] = @{ r = [pscustomobject]@{ passed = [int]$s.passed; failed = [int]$s.failed }
                                           t = @{ tier = [string]$s.tier; note = '' } }
    }
    $baseline = $row.baseline | ConvertTo-Json -Depth 8 | ConvertFrom-Json
    $testWip = @{ readable = [bool]$row.testWip.readable; paths = @($row.testWip.paths) }
    $wipReadable = [bool]$row.ratchetWip.readable
    $wipPaths = @($row.ratchetWip.paths)
    $readWip = { @{ readable = $wipReadable; paths = @($wipPaths) } }.GetNewClosure()
    $gitMap = $row.git
    $readGit = {
        param([string[]] $GitArgs)
        $kind = if ($GitArgs[0] -eq 'diff') { 'diff' }
                elseif ($GitArgs[0] -eq 'branch') { 'branch' }
                elseif ($GitArgs[0] -eq 'rev-parse' -and [string]$GitArgs[-1] -like '*^{commit}') { 'base' }
                else { 'head' }
        if ($kind -eq 'diff') {
            $suite = if ([string]$GitArgs[-1] -match 'Tests/(\w+)/') { $Matches[1] } else { 'default' }
            $entry = if ($gitMap.diff.ContainsKey($suite)) { $gitMap.diff[$suite] } else { $gitMap.diff['default'] }
        } else {
            $entry = if ($gitMap.ContainsKey($kind)) { $gitMap[$kind] } else { @{ ok = $false; text = $null; lines = @() } }
        }
        $ok = [bool]$entry['ok']
        @{ Ok = $ok
           Text = $(if ($ok -and $entry['text']) { [string]$entry['text'] } else { $null })
           Lines = [string[]]@($entry['lines']) }
    }.GetNewClosure()
    $engineProfile = $null
    if ($null -ne $row.engineProfile) {
        $sigs = [ordered]@{}
        foreach ($e in $row.engineProfile.signatures.GetEnumerator()) { $sigs[$e.Key] = [int]$e.Value }
        $engineProfile = [pscustomobject]@{ Total = [int]$row.engineProfile.total; Signatures = $sigs }
    }
    $r = Invoke-BaselineRatchet -SuiteResults $suiteResults -Baseline $baseline -BaselineRaw ([string]$row.baselineRaw) `
        -RawStamp ([string]$row.rawStamp) -TestWip $testWip -ReadTestWip $readWip -ReadGit $readGit `
        -IntComplete ([string]$row.intComplete) -EngineProfile $engineProfile `
        -Reestablish ([bool]$row.reestablish) -NoBaselineUpdate:([bool]$row.noBaselineUpdate) `
        -Recompiled ([bool]$row.recompiled)
    $tiers = [ordered]@{}
    foreach ($k in $suiteResults.Keys) { $tiers[$k] = $suiteResults[$k].t.tier }
    [pscustomobject]@{ name = [string]$row.name; action = $r.Action; verdict = $r.Verdict; code = $r.Code
                       emit = @($r.Emit); detail = @($r.Detail); write = $r.WriteText; tiers = $tiers }
}

# The gate's wiring, read off the same AST: one definition, one call outside it, and the call's result reaching the
# file write and the verdict. Behaviour is proven by the rows above; this pins the connection to the real script.
$calls = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Invoke-BaselineRatchet' }, $true))
$writes = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -and
    $n.Member.Value -eq 'WriteAllText' }, $true))
$verdictCalls = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
    $n.GetCommandName() -eq 'Complete-Gate' }, $true))
$wiring = [pscustomobject]@{
    defs = $ratchetFn.Count
    calls = $calls.Count
    callOutsideFunction = ($calls.Count -eq 1 -and
        ($calls[0].Extent.StartOffset -lt $ratchetFn[0].Extent.StartOffset -or
         $calls[0].Extent.EndOffset -gt $ratchetFn[0].Extent.EndOffset))
    params = @('SuiteResults', 'Baseline', 'BaselineRaw', 'RawStamp', 'TestWip', 'ReadTestWip', 'ReadGit',
               'IntComplete', 'Reestablish', 'Recompiled') | ForEach-Object {
        ($calls.Count -eq 1) -and ($calls[0].Extent.Text -match ('-' + $_ + '\b')) }
    writeUsesResult = @($writes | Where-Object { $_.Extent.Text -match '\$ratchet\.WriteText' }).Count
    verdictUsesResult = @($verdictCalls | Where-Object { $_.Extent.Text -match '\$ratchet\.Verdict' }).Count
}

# The live cases: the gate's own call-site block, lifted verbatim from the AST and run against a real git checkout -
# once on a commit whose deletions explain the drop, once with a stamp git cannot resolve. The gate's terminals
# (Add-Detail, Emit, Complete-Gate) are recorded rather than performed: the claim is that the block calls the real
# function, writes its returned text and completes its verdict. Starting the whole gate needs a project fixture and
# an engine run, which the base harness has none of; that end-to-end row stays the project's held-gate proof.
$live = @()
if ($env:LIVE_SPEC) {
    $specs = @(Get-Content -LiteralPath $env:LIVE_SPEC -Raw | ConvertFrom-Json -AsHashtable)
    $src = [IO.File]::ReadAllText('@GATE@')
    $ratchetAssign = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $n.Left.Extent.Text -eq '$ratchet' }, $true))
    if ($ratchetAssign.Count -ne 1) { throw "expected one `$ratchet assignment, found $($ratchetAssign.Count)" }
    $wipAssign = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        $n.Left.Extent.Text -eq '$testWip' -and $n.Extent.EndOffset -le $ratchetAssign[0].Extent.StartOffset }, $true) |
        Sort-Object { $_.Extent.StartOffset })[-1]
    if (-not $wipAssign) { throw 'no $testWip assignment before the ratchet call' }
    $passCall = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
        $n.GetCommandName() -eq 'Complete-Gate' -and $n.Extent.StartOffset -gt $ratchetAssign[0].Extent.EndOffset -and
        $n.Extent.Text -match "'PASS' 0" }, $true))[0]
    if (-not $passCall) { throw 'no completing Complete-Gate PASS 0 after the ratchet call' }
    $block = $src.Substring($wipAssign.Extent.StartOffset, $passCall.Extent.EndOffset - $wipAssign.Extent.StartOffset)

    function Add-Detail { param([string] $Text) $script:LiveDetail.Add($Text) }
    function Emit { param([string] $Text) $script:LiveEmit.Add($Text) }
    function Complete-Gate { param([string] $Verdict, [int] $Code)
        if ($null -eq $script:LiveVerdict) { $script:LiveVerdict = $Verdict; $script:LiveCode = $Code }
        throw 'GATE_COMPLETE' }   # the real one exits; the first verdict terminates the block the same way
    function Invoke-LiveCase($Spec) {
        $script:LiveDetail = [System.Collections.Generic.List[string]]::new()
        $script:LiveEmit = [System.Collections.Generic.List[string]]::new()
        $script:LiveVerdict = $null; $script:LiveCode = $null
        $repo = [string]$Spec['repo']
        $baselineRaw = [IO.File]::ReadAllText([string]$Spec['baselinePath'])
        $baseline = $baselineRaw | ConvertFrom-Json
        $rawStamp = [string]$Spec['rawStamp']
        $intComplete = [string]$Spec['intComplete']
        $suiteResults = [ordered]@{}
        foreach ($s in $Spec['suites']) {
            $suiteResults[[string]$s['key']] = @{ r = [pscustomobject]@{ passed = [int]$s['passed']; failed = [int]$s['failed'] }
                                                  t = @{ tier = [string]$s['tier']; note = '' } }
        }
        $engineProfile = $null
        $stampTrust = 'OK'
        $NoBaselineUpdate = $false
        $script:Recompiled = $true
        $baselineP = [string]$Spec['baselineOut']
        $script:BaselineAction = 'unknown'
        Remove-Item -LiteralPath $baselineP -Force -ErrorAction SilentlyContinue
        try { . ([scriptblock]::Create($block)) } catch { if ("$_" -ne 'GATE_COMPLETE') { throw } }
        [pscustomobject]@{
            name = [string]$Spec['name']
            block = $block
            action = $script:BaselineAction
            verdict = $script:LiveVerdict
            code = $script:LiveCode
            emit = @($script:LiveEmit)
            detail = @($script:LiveDetail)
            write = $(if (Test-Path $baselineP) { Get-Content -LiteralPath $baselineP -Raw } else { $null })
        }
    }
    $live = @($specs | ForEach-Object { Invoke-LiveCase $_ })
}

[IO.File]::WriteAllText($env:RESULTS, (ConvertTo-Json -InputObject ([pscustomobject]@{
    rows = @($results); wiring = $wiring; live = $live }) -Depth 10), [Text.UTF8Encoding]::new($false))
"""


def baseline(passed, engine=None):
    obj = {
        "schema_version": 1,
        "description": "ratchet proof",
        "notes": "café — ✓",
        "updated_at": "2026-01-01T00:00:00Z",
        "updated_on_commit": "a" * 40,
        "updated_on_branch": "main",
        "suites": {k: {"passed": n, "failed": 0} for k, n in passed.items()},
        "silent_skip_sentinels": {f"{k}_min": 1 for k in passed},
        "drift_thresholds": {"warn_ratio": 0.9, "hard_fail_ratio": 0.7},
    }
    if engine is not None:
        obj["engine_errors"] = engine
    return obj, json.dumps(obj, indent=2)


PATCH_DEL2 = [
    "diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs",
    "--- a/Tests/Logic/Cases.cs",
    "+++ b/Tests/Logic/Cases.cs",
    "@@ -1,5 +1,2 @@",
    " class Cases {",
    "-    [TestCase(1)]",
    "-    [TestCase(2)]",
    "     public void Gone(int n) { }",
    " }",
]
PATCH_NET1 = [
    "diff --git a/Tests/Logic/Cases.cs b/Tests/Logic/Cases.cs",
    "--- a/Tests/Logic/Cases.cs",
    "+++ b/Tests/Logic/Cases.cs",
    "@@ -1,5 +1,4 @@",
    " class Cases {",
    "-    [TestCase(1)]",
    "-    [TestCase(2)]",
    "+    [TestCase(3)]",
    "+    public void New(int n) { }",
    "     public void Gone(int n) { }",
    " }",
]
PATCH_BINARY = ["Binary files a/Tests/Logic/Cases.cs and b/Tests/Logic/Cases.cs differ"]

BASE = {"Logic": 10, "Integration": 30, "Sanity": 10}
HEAD = "b" * 40


def suites(logic, integ=30, sanity=10, logic_tier="PASS", tiers=None):
    t = {"Logic": logic_tier, "Integration": "PASS", "Sanity": "PASS"}
    t.update(tiers or {})
    return [{"key": k, "passed": v, "failed": 0, "tier": t[k]}
            for k, v in (("Logic", logic), ("Integration", integ), ("Sanity", sanity))]


def git(base_ok=True, base_text="a" * 40, diff_ok=True, diff_lines=None, head=HEAD, branch="fixture-branch",
        diff_ok_by_suite=None):
    patch = PATCH_DEL2 if diff_lines is None else diff_lines
    diffs = {"default": {"ok": diff_ok, "lines": patch}}
    for k, ok in (diff_ok_by_suite or {}).items():
        diffs[k] = {"ok": ok, "lines": patch}
    return {"base": {"ok": base_ok, "text": base_text},
            "diff": diffs,
            "head": {"ok": bool(head), "text": head},
            "branch": {"ok": bool(branch), "text": branch}}


def row(name, suite_rows, base_passed, *, wip=(True, ()), rwip=(True, ()), git_reads=None, int_complete="OK",
        engine=None, reestablish=False, no_update=False, recompiled=True, raw_stamp=None, raw=None):
    base, raw_text = baseline(base_passed, engine)
    return {
        "name": name,
        "suiteResults": suite_rows,
        "baseline": base,
        "baselineRaw": raw if raw is not None else raw_text,
        "rawStamp": raw_stamp if raw_stamp is not None else "a" * 40,
        "testWip": {"readable": wip[0], "paths": list(wip[1])},
        "ratchetWip": {"readable": rwip[0], "paths": list(rwip[1])},
        "git": git_reads or git(),
        "intComplete": int_complete,
        "engineProfile": engine,
        "reestablish": reestablish,
        "noBaselineUpdate": no_update,
        "recompiled": recompiled,
    }


def rows():
    dirty = (" M Tests/Logic/Peer.cs",)
    engine = {"total": 1, "signatures": {"ERROR: planted <path>": 1}}
    return [
        row("explained WARN-band drop lowers and writes", suites(8, sanity=11, logic_tier="WARN"), BASE),
        row("explained PARTIAL-band drop lowers", suites(9, logic_tier="PARTIAL"), BASE),
        row("unexplained drop stays WARN with net-deletion evidence", suites(8, logic_tier="WARN"), BASE,
            git_reads=git(diff_lines=PATCH_NET1)),
        row("uncountable diff stays unexplained", suites(8, logic_tier="WARN"), BASE,
            git_reads=git(diff_lines=PATCH_BINARY)),
        row("a partial Integration total is not compared to the baseline",
            suites(10, integ=29, tiers={"Integration": "PARTIAL"}), BASE, int_complete="SHORTFALL"),
        row("uncommitted tests at drop evaluation skip the lower", suites(8, logic_tier="WARN"), BASE, wip=(True, dirty)),
        row("uncommitted tests before the write refuse the lower write", suites(8, logic_tier="WARN"), BASE,
            rwip=(True, dirty)),
        row("unreadable tests before the write refuse the lower write", suites(8, logic_tier="WARN"), BASE,
            rwip=(False, ())),
        row("uncommitted tests block a growth write but still pass", suites(12), BASE, rwip=(True, dirty)),
        row("unreadable tests block a growth write", suites(12), BASE, rwip=(False, ())),
        row("unreadable baseline commit stays WARN", suites(8, logic_tier="WARN"), BASE,
            git_reads=git(base_ok=False, base_text=None), raw_stamp="unreadable"),
        row("unreadable test diff stays WARN", suites(8, logic_tier="WARN"), BASE,
            git_reads=git(diff_ok=False)),
        row("unreadable test status stays WARN", suites(8, logic_tier="WARN"), BASE, wip=(False, ())),
        row("no change leaves the baseline alone", suites(10), BASE),
        row("growth updates the baseline", suites(12), BASE),
        row("an untrusted stamp is re-established", suites(10), BASE, reestablish=True),
        row("the no-update flag writes nothing", suites(12), BASE, no_update=True),
        row("engine errors alone update the baseline", suites(10), BASE, engine=engine),
        row("a pool that never ran cannot reach the ratchet", suites(10), BASE, recompiled=False),
        row("an unparseable edited baseline aborts the write", suites(12), BASE, raw='{"suites":'),
    ]


def _git(repo, *args):
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout.strip()


def build_live_repo(tmp):
    """A real checkout whose second commit deletes two [TestCase] attributes: the one live read boundary."""
    repo = os.path.join(tmp, "live-repo")
    os.makedirs(os.path.join(repo, "Tests", "Logic"))
    path = os.path.join(repo, "Tests", "Logic", "Cases.cs")

    def write(cases):
        body = "class Cases {\n" + "".join(
            f"    [TestCase({n})]\n    public void Case{n}(int n) {{ }}\n" for n in cases) + "}\n"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)

    write((1, 2))
    subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    write(())
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "delete")
    return repo, base, _git(repo, "rev-parse", "HEAD"), _git(repo, "branch", "--show-current")


def live_spec(tmp):
    repo, base, head, branch = build_live_repo(tmp)
    obj, _ = baseline({"Logic": 10, "Integration": 30, "Sanity": 10})
    obj["updated_on_commit"] = base
    path = os.path.join(tmp, "live-baseline.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    suites = [{"key": "Logic", "passed": 8, "failed": 0, "tier": "WARN"},
              {"key": "Integration", "passed": 30, "failed": 0, "tier": "PASS"},
              {"key": "Sanity", "passed": 10, "failed": 0, "tier": "PASS"}]
    cases = [
        {"name": "explained drop lowers against a real checkout", "repo": repo, "baselinePath": path,
         "baselineOut": os.path.join(tmp, "live-out-lower.json"), "rawStamp": base, "intComplete": "OK",
         "suites": suites},
        {"name": "a stamp git cannot resolve stays WARN", "repo": repo, "baselinePath": path,
         "baselineOut": os.path.join(tmp, "live-out-warn.json"), "rawStamp": "f" * 40, "intComplete": "OK",
         "suites": suites},
    ]
    return {"head": head, "branch": branch, "cases": cases}


def main(tmp):
    rows_path = os.path.join(tmp, "rows.json")
    results_path = os.path.join(tmp, "results.json")
    spec = live_spec(tmp)
    live_path = os.path.join(tmp, "live.json")
    with open(rows_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows(), f, ensure_ascii=False)
    with open(live_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(spec["cases"], f, ensure_ascii=False)
    driver = os.path.join(tmp, "driver.ps1")
    with open(driver, "w", encoding="utf-8", newline="\n") as f:
        f.write(DRIVER.replace("@GATE@", GATE.replace("'", "''"))
                      .replace("@CSHARP@", CSHARP.replace("'", "''"))
                      .replace("@ENGINE@", ENGINE.replace("'", "''")))
    rc, out, err = pwsh(["-File", driver], tmp,
                        {"ROWS": rows_path, "RESULTS": results_path, "LIVE_SPEC": live_path})
    if not os.path.exists(results_path):
        check("the ratchet proof ran", False, f"rc={rc} out={out!r} err={err[-800:]!r}")
        return
    with open(results_path, encoding="utf-8") as f:
        results = json.load(f)
    got = {r["name"]: r for r in results["rows"]}
    checks = []

    def want(name, **expect):
        r = got.get(name)
        checks.append((name, r, expect))

    want("explained WARN-band drop lowers and writes", action="lowered Sanity+1", verdict=None, code=None,
         emit=["BASELINE action=lowered suite=Logic drop=2 deleted=2 added=0"], write=True,
         detail=["Count-drop evidence for Logic: drop=2 deleted=2 added=0."], tiers={"Logic": "PASS"})
    want("explained PARTIAL-band drop lowers", action="lowered", verdict=None, code=None,
         emit=["BASELINE action=lowered suite=Logic drop=1 deleted=2 added=0"], write=True)
    want("unexplained drop stays WARN with net-deletion evidence", action=None, verdict="WARN", code=3,
         emit=[], write=False, detail=["drop=2 deleted=2 added=1"],
         tiers={"Logic": "WARN"})
    want("uncountable diff stays unexplained", verdict="WARN", code=3, write=False,
         detail=["reason=binary-test-diff"])
    want("a partial Integration total is not compared to the baseline", verdict="WARN", code=3, write=False,
         detail=["The count drop remains unexplained"], not_detail=["Count-drop evidence for Integration"])
    want("uncommitted tests at drop evaluation skip the lower", action="skipped:uncommitted-test-changes",
         verdict="WARN", code=3, emit=["BASELINE action=skipped reason=uncommitted-test-changes"], write=False,
         not_detail=["Count-drop evidence"])
    want("uncommitted tests before the write refuse the lower write", action="skipped:uncommitted-test-changes",
         verdict="WARN", code=3, emit=["BASELINE action=skipped reason=uncommitted-test-changes"], write=False,
         detail=["Deletion evidence no longer belongs"])
    want("unreadable tests before the write refuse the lower write", action="skipped:unreadable-test-status",
         verdict="WARN", code=3, emit=["BASELINE action=skipped reason=unreadable-test-status"], write=False)
    want("uncommitted tests block a growth write but still pass", action="skipped:uncommitted-test-changes",
         verdict=None, code=None, emit=["BASELINE action=skipped reason=uncommitted-test-changes"], write=False,
         detail=["uncommitted test changes (1 file(s))"])
    want("unreadable tests block a growth write", action="skipped:unreadable-test-status", verdict=None, code=None,
         emit=["BASELINE action=skipped reason=unreadable-test-status"], write=False)
    want("unreadable baseline commit stays WARN", action=None, verdict="WARN", code=3, write=False,
         detail=["unreadable-baseline-commit"])
    want("unreadable test diff stays WARN", action=None, verdict="WARN", code=3, write=False,
         detail=["unreadable-test-diff"])
    want("unreadable test status stays WARN", action=None, verdict="WARN", code=3, write=False,
         detail=["The count drop remains unexplained"], not_detail=["Count-drop evidence"])
    want("no change leaves the baseline alone", action="unchanged", verdict=None, code=None,
         emit=["BASELINE action=unchanged"], write=False)
    want("growth updates the baseline", action="updated Logic+2", verdict=None, code=None,
         emit=[f"BASELINE action=updated Logic+2 stamp={HEAD[:10]}"], write=True)
    want("an untrusted stamp is re-established", action="re-established", verdict=None, code=None,
         emit_prefix=["BASELINE action=re-established"], write=True)
    want("the no-update flag writes nothing", action="skipped:flag", verdict=None, code=None,
         emit=["BASELINE action=skipped reason=flag"], write=False)
    want("engine errors alone update the baseline", action="updated engine_errors", verdict=None, code=None,
         emit=[f"BASELINE action=updated engine_errors stamp={HEAD[:10]}"], write=True,
         write_engine={"total": 1, "signature": "ERROR: planted <path>"})
    want("a pool that never ran cannot reach the ratchet", action=None, verdict="INVALID", code=2,
         emit=[], write=False, detail=[])
    want("an unparseable edited baseline aborts the write", action=None, verdict="PASS", code=0,
         emit=["BASELINE action=ABORTED reason=post-edit-json-invalid"], write=False,
         detail=["Refused to write"])

    for name, r, expect in checks:
        if r is None:
            check("ratchet row: " + name, False, "row produced no result")
            continue
        problems = []
        if "action" in expect:
            action = r["action"]
            if expect["action"] is None:
                ok = action is None
            elif expect["action"] == "lowered" or expect["action"] == "re-established":
                ok = isinstance(action, str) and action.strip() == expect["action"]
            else:
                ok = action == expect["action"]
            if not ok:
                problems.append(f"action={action!r} want={expect['action']!r}")
        for field in ("verdict", "code"):
            if field in expect and r[field] != expect[field]:
                problems.append(f"{field}={r[field]!r} want={expect[field]!r}")
            if field not in expect and r[field] is not None:
                problems.append(f"{field}={r[field]!r} want None")
        if "emit" in expect and r["emit"] != expect["emit"]:
            problems.append(f"emit={r['emit']!r}")
        for prefix in expect.get("emit_prefix", []):
            if not any(e.startswith(prefix) for e in r["emit"]):
                problems.append(f"emit missing prefix {prefix!r}: {r['emit']!r}")
        for token in expect.get("detail", []):
            if not any(token in d for d in r["detail"]):
                problems.append(f"detail missing {token!r}: {r['detail']!r}")
        for token in expect.get("not_detail", []):
            if any(token in d for d in r["detail"]):
                problems.append(f"detail carries {token!r}: {r['detail']!r}")
        if "tiers" in expect:
            for k, tier in expect["tiers"].items():
                if r["tiers"].get(k) != tier:
                    problems.append(f"tier {k}={r['tiers'].get(k)!r} want {tier!r}")
        if "write" in expect:
            has_write = r["write"] is not None
            if has_write != expect["write"]:
                problems.append(f"write={has_write} want={expect['write']}")
        if "write_engine" in expect and r["write"]:
            block = json.loads(r["write"]).get("engine_errors") or {}
            if block.get("total") != expect["write_engine"]["total"] or \
                    expect["write_engine"]["signature"] not in (block.get("signatures") or {}):
                problems.append(f"engine_errors={block!r}")
        check("ratchet row: " + name, not problems, "; ".join(problems))

    # The write's fields, read back from the text the gate would write.
    r = got.get("explained WARN-band drop lowers and writes") or {}
    problems = []
    if not r.get("write"):
        problems.append("no write")
    else:
        w = json.loads(r["write"])
        for k, n in (("Logic", 8), ("Integration", 30), ("Sanity", 11)):
            if (w.get("suites") or {}).get(k, {}).get("passed") != n:
                problems.append(f"suites.{k}.passed={((w.get('suites') or {}).get(k) or {}).get('passed')!r} want {n}")
        if w.get("updated_on_commit") != HEAD:
            problems.append(f"updated_on_commit={w.get('updated_on_commit')!r}")
        if w.get("updated_on_branch") != "fixture-branch":
            problems.append(f"updated_on_branch={w.get('updated_on_branch')!r}")
        when = str(w.get("updated_at") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", when) or when == "2026-01-01T00:00:00Z":
            problems.append(f"updated_at={when!r}")
        if w.get("notes") != "café — ✓":
            problems.append(f"notes={w.get('notes')!r}")
        if "silent_skip_sentinels" not in w or "drift_thresholds" not in w:
            problems.append("an untouched section disappeared")
    check("ratchet write: every field the gate owns is carried and untouched text survives", not problems,
          "; ".join(problems))

    w = results.get("wiring") or {}
    check("the gate defines and calls Invoke-BaselineRatchet exactly once",
          w.get("defs") == 1 and w.get("calls") == 1 and w.get("callOutsideFunction") is True, str(w))
    check("the call site passes every input the decision reads",
          isinstance(w.get("params"), list) and all(w["params"]), str(w.get("params")))
    check("the gate writes the ratchet's returned text and completes its returned verdict",
          w.get("writeUsesResult") == 1 and w.get("verdictUsesResult") == 1, str(w))

    # The live cases: the gate's real call-site block, real git, real Get-TestWorkInProgress, positive and negative.
    live = {c.get("name"): c for c in results.get("live") or []}
    r = live.get("explained drop lowers against a real checkout") or {}
    problems = []
    block = str(r.get("block") or "")
    if "Invoke-BaselineRatchet -SuiteResults" not in block or "$ratchet.WriteText" not in block:
        problems.append("the block is not the gate's call-site text")
    if r.get("verdict") != "PASS" or r.get("code") != 0:
        problems.append(f"verdict={r.get('verdict')!r} code={r.get('code')!r}")
    if str(r.get("action") or "").strip() != "lowered":
        problems.append(f"action={r.get('action')!r}")
    if r.get("emit") != ["BASELINE action=lowered suite=Logic drop=2 deleted=2 added=0"]:
        problems.append(f"emit={r.get('emit')!r}")
    if not any("drop=2 deleted=2 added=0" in d for d in r.get("detail") or []):
        problems.append(f"detail={r.get('detail')!r}")
    if not r.get("write"):
        problems.append("no write")
    else:
        written = json.loads(r["write"])
        if (written.get("suites") or {}).get("Logic", {}).get("passed") != 8:
            problems.append(f"Logic passed={((written.get('suites') or {}).get('Logic') or {}).get('passed')!r}")
        if written.get("updated_on_commit") != spec["head"]:
            problems.append(f"updated_on_commit={written.get('updated_on_commit')!r} want {spec['head']!r}")
        if written.get("updated_on_branch") != spec["branch"]:
            problems.append(f"updated_on_branch={written.get('updated_on_branch')!r} want {spec['branch']!r}")
        if str(written.get("updated_at")) in ("", "None", "2026-01-01T00:00:00Z"):
            problems.append(f"updated_at={written.get('updated_at')!r}")
        if written.get("notes") != "café — ✓":
            problems.append(f"notes={written.get('notes')!r}")
    check("the gate's call-site block lowers and writes against a real checkout", not problems, "; ".join(problems))

    r = live.get("a stamp git cannot resolve stays WARN") or {}
    problems = []
    if r.get("verdict") != "WARN" or r.get("code") != 3:
        problems.append(f"verdict={r.get('verdict')!r} code={r.get('code')!r}")
    if not any("unreadable-baseline-commit" in d for d in r.get("detail") or []):
        problems.append(f"detail={r.get('detail')!r}")
    if r.get("write") is not None:
        problems.append("wrote a baseline")
    check("the gate's call-site block stays WARN when git cannot resolve the stamp", not problems,
          "; ".join(problems))


if __name__ == "__main__":
    run_proof(main, "regression_gate_ratchet-proof-")
