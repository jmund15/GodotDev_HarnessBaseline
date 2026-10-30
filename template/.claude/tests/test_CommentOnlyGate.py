"""Drive the shipped comment-route classifier and static completion, never a gate/build/engine.

Fixtures use real files and the production snapshot writer. Git writes only inside the temp
real-git fixture; the live-tree observation reads this checkout through the production TreeDigest.
"""
import json
import subprocess
from pathlib import Path

from _pwsh_drive import check, pwsh, run_proof

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / ".claude/scripts/regression_gate.ps1"
HELPER = ROOT / ".claude/scripts/CommentOnlyGate.ps1"
TREE = ROOT / ".claude/scripts/TreeDigest.ps1"

LIB = r"""
$ErrorActionPreference = 'Stop'
$VerbosePreference = 'Continue'
Set-StrictMode -Version Latest
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('@GATE@', [ref]$t, [ref]$e)
if ($e.Count) { throw ($e | Out-String) }
foreach ($name in @('Get-Prop', 'Get-ImportStamp', 'Complete-Gate', 'Exit-IfStaticRoute', 'Invoke-LedgerReuse')) {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if ($fn) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
if (-not (Test-Path -LiteralPath '@HELPER@') -or -not (Get-Command Exit-IfStaticRoute -ErrorAction SilentlyContinue)) {
    Write-Output 'FEATURE=missing'; exit 0
}
. '@TREE@'
. '@HELPER@'
function Emit { param([string] $Text) Write-Output $Text }
function Add-Detail { param([string] $Text) $script:Detail.Add($Text) }
function Update-ActivityRecord { param($Label) }
function Exit-TwinLock { }
function Exit-GateHeld { }
function Get-EngineProbe { [pscustomobject]@{ ok = $true } }
$script:Detail = [System.Collections.Generic.List[string]]::new()
$script:Mode = 'full'
$script:BaselineAction = 'none'
$script:ReusedFrom = $null
$script:ReusedFailedTests = @()
$script:TwinSource = $null
$script:CommentOnlyEvidence = $null
$script:RunStartedUtc = (Get-Date -AsUTC -Format 'o')
$suiteResults = @{}
$StaticOnly = $false
$NoReuse = $false
$RetryOnly = $false
$runId = 'current'
$repo = '@REPO@'
$ledgerDir = Join-Path $repo '.claude/scratch/gate_ledger'
$detailPath = Join-Path $ledgerDir 'detail.md'
$logDir = $ledgerDir
New-Item -ItemType Directory -Force -Path $ledgerDir | Out-Null
function Get-FixtureTree {
    param([string] $Root = $repo)
    $entries = [string[]]@(Get-ChildItem -LiteralPath $Root -File | ForEach-Object {
        $b = [IO.File]::ReadAllBytes($_.FullName)
        $prefix = [Text.Encoding]::UTF8.GetBytes("blob $($b.Length)`0")
        $hash = [Convert]::ToHexString([Security.Cryptography.SHA1]::HashData([byte[]]($prefix + $b))).ToLowerInvariant()
        "$($_.Name)|$hash"
    })
    if (Test-Path -LiteralPath (Join-Path $Root 'Paired')) {
        $sub = Get-FixtureTree -Root (Join-Path $Root 'Paired')
        $entries += @($sub.entries | ForEach-Object { "Paired/$_" })
    }
    [Array]::Sort($entries, [StringComparer]::Ordinal)
    $digest = [Convert]::ToHexString([Security.Cryptography.SHA1]::HashData([Text.Encoding]::UTF8.GetBytes(($entries -join "`n")))).ToLowerInvariant()
    [pscustomobject]@{ head = 'not-a-comparison-base'; digest = $digest; entries = $entries }
}
if (-not $env:COMMENT_GATE_REAL_TREE) { function Get-TreeDigest { param($Repo) Get-FixtureTree -Root $Repo } }
function Assert-TreeStable {
    param($Phase)
    if (-not (Test-DigestMatch (Get-TreeDigest -Repo $repo).digest $script:RunTree.digest)) { Complete-Gate 'INVALID' 2 }
}
function Save-Green {
    param($Id = 'producer', $Status = 'PASS', $Mode = 'full')
    $out = & pwsh -NoProfile -NonInteractive -File $PSCommandPath --seed $Id $Status $Mode 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($out | Out-String) }
    $rec = Get-Content -LiteralPath (Join-Path $ledgerDir "$Id.result.json") -Raw | ConvertFrom-Json
    if ($Status -eq 'PASS' -and -not $rec.fullGreenSnapshot) { throw ('positive-control snapshot failed: ' + ($out | Out-String)) }
}
if ($args.Count -and $args[0] -eq '--seed') {
    $runId = $args[1]; $script:Mode = $args[3]
    $script:RunTree = Get-TreeDigest -Repo $repo
    Complete-Gate $args[2] 0
}
"""


def main(tmp):
    number = 0

    def case(label, before, after, *, routed, pre="", post="", extra=None, static=False, seed_base=True):
        nonlocal number
        number += 1
        repo = Path(tmp) / f"case{number}"
        repo.mkdir()
        (repo / "Sample.cs").write_text(before, encoding="utf-8", newline="\n")
        body = LIB.replace("@GATE@", str(GATE)).replace("@HELPER@", str(HELPER)).replace("@TREE@", str(TREE)).replace("@REPO@", str(repo))
        body += "\n" + pre + "\n"
        if seed_base:
            body += "Save-Green\n"
        body += "[IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), " + "'" + after.replace("'", "''") + "')\n"
        if extra:
            for name, value in extra.items():
                body += f"[IO.File]::WriteAllText((Join-Path $repo '{name}'), '{value}')\n"
        body += post + "\n$script:RunTree = Get-FixtureTree\n"
        if static:
            body += "$StaticOnly = $true; $script:Mode = 'static-only'\n"
        body += "$route = Get-CommentOnlyRoute -Repo $repo -Tree $script:RunTree -LedgerDir $ledgerDir\n"
        body += "Write-Output ('ROUTE=' + ($route | ConvertTo-Json -Depth 8 -Compress))\n"
        body += "Exit-IfStaticRoute -Route $route\nWrite-Output 'FULL_GATE'\n"
        driver = Path(tmp) / f"driver{number}.ps1"
        driver.write_text(body, encoding="utf-8", newline="\n")
        rc, out, err = pwsh(["-File", str(driver)], tmp)
        text = out + err
        got_routed = "VERDICT=PASS route=comment-only" in out
        full = "FULL_GATE" in out
        ok = rc == 0 and "FEATURE=missing" not in out and got_routed == routed and full == (not routed and not static)
        if routed:
            ok = ok and 'Sample.cs' in out and 'reason=tokens-identical' in out
            record = repo / '.claude/scratch/gate_ledger/current.result.json'
            if record.exists():
                rec = json.loads(record.read_text(encoding='utf-8-sig'))
                ok = ok and rec['status'] == 'PASS' and rec['coverage'] == 'comment-only' and not rec['fullGreenSnapshot']
            else:
                ok = False
        check(label, ok, f"rc={rc} output={text!r}")
        return repo, text

    base = 'class Sample { int Value = 1; }\n'
    first_repo, _ = case('D1 comment-only -> accepted static PASS with file list', base, base + '// note\n', routed=True)
    case('D2 whitespace-only -> accepted static PASS', base, '\nclass Sample\n{\n  int Value = 1;\n}\n', routed=True)
    case('D3 string containing // changes -> full', 'class Sample { string S = "a//b"; }', 'class Sample { string S = "a//c"; }', routed=False)
    case('D4 identifier rename -> full', base, base.replace('Value', 'Renamed'), routed=False)
    case('D5 no prior green base -> full', base, base + '// note', routed=False, seed_base=False)
    case('D6 tokenizer error -> full', base, base + ' /* unterminated', routed=False)
    case('XML documentation and block comments -> accepted', base, '/// <summary>A.</summary>\nclass Sample { /*note*/ int Value = 1; }', routed=True)
    case('string spaces remain significant', 'class Sample { string S = "a b"; }', 'class Sample { string S = "ab"; }', routed=False)
    for name, literal, changed in [
        ('char', "'/'", "'x'"),
        ('verbatim', '@"a//b"', '@"a//c"'),
        ('interpolated', '$"a//{1}"', '$"b//{1}"'),
        ('interpolated verbatim', '$@"a//{1}"', '$@"b//{1}"'),
        ('raw', '"""a//b"""', '"""a//c"""'),
        ('interpolated raw', '$$"""a//{{1}}"""', '$$"""b//{{1}}"""'),
    ]:
        case(f'{name} literal change -> full', f'class Sample {{ object S = {literal}; }}', f'class Sample {{ object S = {changed}; }}', routed=False)
    case('separated tokens cannot merge', 'class Sample { int X = + +1; }', 'class Sample { int X = ++1; }', routed=False)
    case('non-C# resource change -> full', base, base + '// note', routed=False, extra={'scene.tscn': 'changed'})
    case('added C# file -> full', base, base + '// note', routed=False, extra={'Added.cs': 'class Added {}'})
    case('deleted C# file -> full', base, base + '// note', routed=False, post="Remove-Item -LiteralPath (Join-Path $repo 'Sample.cs')")
    case('unreadable snapshot -> full', base, base + '// note', routed=False,
         post="Get-ChildItem $ledgerDir -Filter '*.result.json' | ForEach-Object { $r = Get-Content $_.FullName -Raw | ConvertFrom-Json; $r.fullGreenSnapshot = $null; $r | ConvertTo-Json -Depth 12 | Set-Content $_.FullName }")
    case('snapshot digest mismatch -> full', base, base + '// note', routed=False,
         post="Get-ChildItem $ledgerDir -Filter '*.result.json' | ForEach-Object { $r = Get-Content $_.FullName -Raw | ConvertFrom-Json; $r.treeDigest = 'bad'; $r | ConvertTo-Json -Depth 12 | Set-Content $_.FullName }")
    case('last full green wins, not older convenient base', base, base + '// note', routed=False,
         post="[IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), 'class Sample { int Other = 2; }'); Start-Sleep -Milliseconds 30; Save-Green -Id 'newest'; [IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), 'class Sample { int Value = 1; }// note')")
    case('manual StaticOnly with code change remains insufficient', base, base.replace('1', '2'), routed=False, static=True)
    case('manual StaticOnly may prove qualifying comments', base, base + '// note', routed=True, static=True)
    case('directive change -> full', '#define A\n' + base, '#define B\n' + base, routed=False)
    case('directive comment/spacing-only -> static', '#if true\n' + base + '#endif\n', '#if   true // note\n' + base + '#endif\n', routed=True)
    case('inactive C# comment-only -> static', '#if false\nclass Hidden {}\n#endif\n' + base, '#if false\nclass Hidden { /* note */ }\n#endif\n' + base, routed=True)
    case('inactive string literal change -> full', '#if false\nclass Hidden { string S = "a//b"; }\n#endif\n' + base, '#if false\nclass Hidden { string S = "a//c"; }\n#endif\n' + base, routed=False)
    case('whitespace inside literal stays significant', 'class Sample { string S = @"a b"; }', 'class Sample { string S = @"a  b"; }', routed=False)
    case('lexer throws -> full', base, base + '// note', routed=False,
         post="function Initialize-CommentLexer { throw 'planted-lexer-failure' }")
    case('newest full green missing snapshot -> full', base, base + '// note', routed=False,
         post="Save-Green -Id 'latest'; $p = Join-Path $ledgerDir 'latest.result.json'; $r = Get-Content $p -Raw | ConvertFrom-Json; $r.fullGreenSnapshot = $null; $r | ConvertTo-Json -Depth 12 | Set-Content $p")
    case('non-green full run is never the base', base, base + '// note', routed=True,
         post="[IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), 'class Broken {}'); Save-Green -Id 'later-red' -Status 'FAIL'; [IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), 'class Sample { int Value = 1; }// note')")
    sub_pre = "New-Item -ItemType Directory -Path (Join-Path $repo 'Paired') | Out-Null; [IO.File]::WriteAllText((Join-Path $repo 'Paired/Sub.cs'), 'class Sub {}')"
    _, sub_out = case('Paired comment-only alongside root comments -> static', base, base + '// note', routed=True,
                     pre=sub_pre, extra={'Paired/Sub.cs': 'class Sub {} // note'})
    check('Paired changed file is named in verdict', 'Paired/Sub.cs' in sub_out)
    case('Paired identifier change -> full', base, base + '// note', routed=False,
         pre=sub_pre, extra={'Paired/Sub.cs': 'class Renamed {}'})

    reuse_driver = Path(tmp) / 'reuse.ps1'
    reuse_body = LIB.replace('@GATE@', str(GATE)).replace('@HELPER@', str(HELPER)).replace('@TREE@', str(TREE)).replace('@REPO@', str(first_repo))
    reuse_driver.write_text(reuse_body + "\n$runId = 'reused'; Invoke-LedgerReuse; Write-Output 'NOREUSE'\n", encoding='utf-8', newline='\n')
    rc, out, err = pwsh(['-File', str(reuse_driver)], tmp)
    check('digest-matched reuse keeps comment reason and file list', rc == 0 and 'REUSE from=current' in out and 'VERDICT=PASS route=comment-only' in out and 'Sample.cs' in out, out + err)
    reused = first_repo / '.claude/scratch/gate_ledger/reused.result.json'
    check('reused comment verdict never becomes a full-green base', reused.exists() and json.loads(reused.read_text(encoding='utf-8-sig'))['fullGreenSnapshot'] is None)

    gate_text = GATE.read_text(encoding='utf-8-sig')
    docs_at = gate_text.index("Emit 'DOCS=OK'")
    route_at = gate_text.index('$commentRoute = Get-CommentOnlyRoute')
    import_at = gate_text.index("Assert-TreeStable -Phase 'import-gate'")
    check('default gate computes route after DOCS and before import/suites', docs_at < route_at < import_at and 'Exit-IfStaticRoute -Route $commentRoute' in gate_text[route_at:import_at])

    # The production Get-TreeDigest over a real repo with a real submodule: Get-FixtureTree above holds
    # only the author's belief about its entry format.
    real, sub_src = Path(tmp) / 'real', Path(tmp) / 'real-sub-src'

    def git(cwd, *args):
        subprocess.run(['git', '-c', 'protocol.file.allow=always', '-C', str(cwd), *args], check=True, capture_output=True)

    for d in (real, sub_src):
        d.mkdir()
        git(d, 'init', '-q')
        git(d, 'config', 'user.email', 'proof@example.invalid')
        git(d, 'config', 'user.name', 'proof')
    (sub_src / 'Sub.cs').write_text('class Sub { int A = 1; }\n', encoding='utf-8', newline='\n')
    git(sub_src, 'add', '.')
    git(sub_src, 'commit', '-qm', 'sub')
    (real / 'Sample.cs').write_text('class Sample { int Value = 1; }\n', encoding='utf-8', newline='\n')
    (real / '.gitignore').write_text('.claude/\n', encoding='utf-8', newline='\n')
    git(real, 'add', '.')
    git(real, 'commit', '-qm', 'root')
    git(real, 'submodule', 'add', '-q', str(sub_src), 'Paired')
    git(real, 'commit', '-qm', 'submodule')
    real_body = LIB.replace('@GATE@', str(GATE)).replace('@HELPER@', str(HELPER)).replace('@TREE@', str(TREE)).replace('@REPO@', str(real))
    real_body += ("\nif (-not ($args.Count -and $args[0] -eq '--seed')) {\n"
                  "Save-Green\n"
                  "function Probe { param($Label) $script:RunTree = Get-TreeDigest -Repo $repo; $r = Get-CommentOnlyRoute -Repo $repo -Tree $script:RunTree -LedgerDir $ledgerDir; Write-Output (\"$Label=\" + $r.eligible + '|' + $r.reason + '|' + ($r.files -join ',')) }\n"
                  "[IO.File]::WriteAllText((Join-Path $repo 'Sample.cs'), \"class Sample { int Value = 1; } // note`n\")\n"
                  "[IO.File]::WriteAllText((Join-Path $repo 'Paired/Sub.cs'), \"class Sub { int A = 1; } // note`n\")\n"
                  "Probe 'COMMENTS'\n"
                  "[IO.File]::WriteAllText((Join-Path $repo 'Paired/Sub.cs'), \"class Sub { int A = 2; }`n\")\n"
                  "Probe 'CODE'\n}\n")
    driver = Path(tmp) / 'real_git.ps1'
    driver.write_text(real_body, encoding='utf-8', newline='\n')
    rc, out, err = pwsh(['-File', str(driver)], tmp, {'COMMENT_GATE_REAL_TREE': '1'})
    check('real TreeDigest and submodule: comment edits in both repos route', rc == 0 and 'COMMENTS=True|tokens-identical|' in out
          and 'Sample.cs' in out and 'Paired/Sub.cs' in out, out + err)
    check('real TreeDigest and submodule: a submodule code edit runs the full gate', rc == 0 and 'CODE=False|' in out, out + err)

    # A real repo read, not a built replica. No assertion on mutable counts or verdict.
    if HELPER.exists():
        driver = Path(tmp) / 'live.ps1'
        driver.write_text("$ErrorActionPreference = 'Stop'\n. '" + str(TREE) + "'\n. '" + str(HELPER) + "'\n"
                          + "$tree = Get-TreeDigest -Repo '" + str(ROOT) + "'\n"
                          + "$route = Get-CommentOnlyRoute -Repo '" + str(ROOT) + "' -Tree $tree -LedgerDir '" + str(ROOT / '.claude/scratch/gate_ledger') + "'\n"
                          + "Write-Output ('LIVE=' + ($route | ConvertTo-Json -Depth 5 -Compress))\n", encoding='utf-8', newline='\n')
        rc, out, err = pwsh(['-File', str(driver)], tmp)
        check('classifier runs on actual checkout read-only', rc == 0 and 'LIVE=' in out, out + err)
        print(out.strip())


if __name__ == '__main__':
    run_proof(main, 'gate-comments-proof-')
