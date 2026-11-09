"""Proof: regression_gate.ps1 REUSE honours -RetryOnly and the import stamp; the ledger record carries importStamp.

Extracts Get-Prop, Get-ImportStamp, Invoke-LedgerReuse and Complete-Gate verbatim from the shipped
regression_gate.ps1 (PowerShell AST, never retyped) and drives them in a child pwsh on a planted git
checkout with planted ledger records. Each case is its own child process. Nothing runs the gate, Godot
or a test suite.
"""
import os
import subprocess
import tempfile

from _pwsh_drive import check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(REPO, ".claude", "scripts", "regression_gate.ps1")
TREE = os.path.join(REPO, ".claude", "scripts", "TreeDigest.ps1")

LIB = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('@GATE@', [ref]$tokens, [ref]$errs)
foreach ($name in @('Get-Prop', 'Get-ImportStamp', 'Invoke-LedgerReuse', 'Complete-Gate', 'Write-VerdictRow')) {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if ($fn) { . ([scriptblock]::Create($fn.Extent.Text)) }
}
$vr = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$script:VerdictRows' }, $true)
. ([scriptblock]::Create($vr.Extent.Text))
function Get-EngineProbe { [pscustomobject]@{ ok = $true } }
function Emit { param([string] $Text) Write-Output $Text }
function Add-Detail { param([string] $Text) $script:Detail.Add($Text) }
function Update-ActivityRecord { param($Label) }
function Exit-TwinLock { }
function Exit-GateHeld { }
. '@TREE@'
$script:Detail = [System.Collections.Generic.List[string]]::new()
$script:Mode = 'full'
$script:BaselineAction = 'none'
$script:ReusedFrom = $null
$script:ReusedFailedTests = @()
$script:TwinSource = $null
$script:RunStartedUtc = '2026-10-22T00:00:00Z'
$suiteResults = @{}
$NoReuse = $false
$RetryOnly = @RETRY@
$runId = 'run_current'
$repo = '@REPO@'
$ledgerDir = Join-Path $repo '.claude\scratch\gate_ledger'
$detailPath = Join-Path $repo 'detail.md'
$logDir = $repo
function Write-Rec([hashtable] $r) {
    New-Item -ItemType Directory -Force -Path $ledgerDir | Out-Null
    $rec = [ordered]@{ mode = 'full'; treeDigest = (Get-TreeDigest -Repo $repo).digest; finishedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); exitCode = 0; sessionId = 's'; baselineAction = 'none'; detail = 'd' }
    foreach ($k in $r.Keys) { $rec[$k] = $r[$k] }
    Set-Content -LiteralPath (Join-Path $ledgerDir ($rec['id'] + '.result.json')) -Value ($rec | ConvertTo-Json -Depth 5) -Encoding utf8
}
"""


def make_repo(path):
    os.makedirs(path)
    with open(os.path.join(path, "tracked.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("x\n")
    with open(os.path.join(path, ".gitignore"), "w", encoding="utf-8", newline="\n") as f:
        f.write(".godot/\n")
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], check=True)


def run_case(tmp, idx, retry, pre, records, tail):
    repo = os.path.join(tmp, f"case{idx}")
    make_repo(repo)
    lib = (LIB.replace("@GATE@", GATE).replace("@TREE@", TREE).replace("@REPO@", repo)
              .replace("@RETRY@", "$true" if retry else "$false"))
    driver = os.path.join(tmp, f"driver{idx}.ps1")
    with open(driver, "w", encoding="utf-8", newline="\n") as f:
        f.write(lib + "\n" + pre + "\n" + "\n".join(records) + "\n" + tail + "\n")
    rc, out, err = pwsh(["-File", driver], tmp)
    return rc, out + err, repo


def rec(id_, status, extra=""):
    return f"Write-Rec @{{ id = '{id_}'; status = '{status}'{extra} }}"


REUSE_OR_NOT = "Invoke-LedgerReuse\nWrite-Output 'NOREUSE'"
STAMP_NOW = "; importStamp = (Get-ImportStamp -Repo $repo)"


def main(tmp):
    idx = 0

    def case(retry, status, want_reuse, label, extra=STAMP_NOW, pre="", records=None):
        nonlocal idx
        idx += 1
        recs = records if records is not None else [rec("p1", status, extra)]
        rc, out, _ = run_case(tmp, idx, retry, pre, recs, REUSE_OR_NOT)
        reused = "REUSE from=p1" in out
        ok = rc == 0 and reused == want_reuse and ((not reused) == ("NOREUSE" in out))
        check(label, ok, f"rc={rc} out={out!r}")

    # G5: -RetryOnly serves only a green producer.
    case(True, "FAIL", False, "G5 -RetryOnly: a FAIL producer is not reused")
    case(True, "WARN", False, "G5 -RetryOnly: a WARN producer is not reused")
    case(True, "PASS", True, "G5 -RetryOnly: a PASS producer is reused")
    case(True, "STATIC_PASS", True, "G5 -RetryOnly: a STATIC_PASS producer is reused")
    case(False, "FAIL", True, "G5 no -RetryOnly: a FAIL producer is still reused (unchanged)")

    # G9: reuse requires the producer's importStamp to equal the current one.
    case(False, "PASS", False, "G9 a record with no importStamp is not reused",
         records=["Write-Rec @{ id = 'p1'; status = 'PASS' }"])
    case(False, "PASS", False, "G9 a record with a stale importStamp is not reused",
         extra="; importStamp = ('0' * 40)")
    case(False, "PASS", True, "G9 a record with the current importStamp is reused")

    # G9: the stamp itself.
    rc, out, _ = run_case(tmp, 90, False, "", [], "Write-Output \"STAMP=$(Get-ImportStamp -Repo $repo)\"")
    check("G9 stamp: a repo with no .godot/imported stamps as the literal none", rc == 0 and "STAMP=none" in out, f"rc={rc} {out!r}")

    pre = "New-Item -ItemType Directory -Force -Path (Join-Path $repo '.godot\\imported') | Out-Null\n" \
          "Set-Content -LiteralPath (Join-Path $repo '.godot\\imported\\a.import') -Value 'a' -Encoding utf8"
    tail = ("$s1 = Get-ImportStamp -Repo $repo\n$s1b = Get-ImportStamp -Repo $repo\n"
            "Set-Content -LiteralPath (Join-Path $repo '.godot\\imported\\b.import') -Value 'bb' -Encoding utf8\n"
            "$s2 = Get-ImportStamp -Repo $repo\n"
            "Write-Output \"SAME=$($s1 -eq $s1b) MOVED=$($s1 -ne $s2) LEN=$($s1.Length)\"")
    rc, out, _ = run_case(tmp, 91, False, pre, [], tail)
    check("G9 stamp: stable for an unchanged import dir, moves when a file is added (40 hex)",
          rc == 0 and "SAME=True MOVED=True LEN=40" in out, f"rc={rc} {out!r}")

    # G9: the ledger record Complete-Gate writes carries the stamp at write time.
    tail = ("Write-Output \"EXPECT=$(Get-ImportStamp -Repo $repo)\"\n"
            "$script:RunTree = Get-TreeDigest -Repo $repo\nComplete-Gate 'PASS' 0")
    rc, out, repo = run_case(tmp, 92, False, pre, [], tail)
    expect = next((ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("EXPECT=")), None)
    got = None
    record = os.path.join(repo, ".claude", "scratch", "gate_ledger", "run_current.result.json")
    if os.path.exists(record):
        import json
        with open(record, encoding="utf-8") as f:
            got = json.load(f).get("importStamp")
    check("G9 record: Complete-Gate writes importStamp equal to the stamp at write time",
          rc == 0 and expect and got == expect, f"rc={rc} expect={expect!r} got={got!r} out={out!r}")


if __name__ == "__main__":
    run_proof(main, "regression_gate_reuse-proof-")
