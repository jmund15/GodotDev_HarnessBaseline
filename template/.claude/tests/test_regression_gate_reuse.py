"""Proof: regression_gate.ps1 REUSE honours -RetryOnly and the import stamp; the ledger record carries importStamp.

The import stamp is the TREE's import state, not one checkout's directory history: for every tracked
``*.import``, its ``dest_files`` artifact is present and the artifact's ``.md5`` sidecar records the
``source_md5`` it was imported from. Two checkouts of one tree whose caches are both current therefore
stamp the same however their directories accumulated artifacts; a stale, missing or never-imported cache
stamps differently.

Extracts Get-Prop, Get-ImportStamp, Invoke-LedgerReuse and Complete-Gate verbatim from the shipped
regression_gate.ps1 (PowerShell AST, never retyped) and drives them in a child pwsh on a planted git
checkout with planted ledger records. Each case is its own child process. Nothing runs the gate, Godot
or a test suite.
"""
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from _case_pool import _budget, _jobs
from _pwsh_drive import check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(REPO, ".claude", "scripts", "regression_gate.ps1")
TREE = os.path.join(REPO, ".claude", "scripts", "TreeDigest.ps1")

LIB = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('@GATE@', [ref]$tokens, [ref]$errs)
foreach ($name in @('Get-Prop', 'Get-ImportStamp', 'Invoke-LedgerReuse', 'Complete-Gate', 'Write-VerdictRow', 'Get-SharedLedgerDir', 'Enter-Stage', 'Stop-Stage', 'Get-StageSummary')) {
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
$sharedLedgerDir = Get-SharedLedgerDir -Repo $repo
$script:Stages = [System.Collections.Generic.List[object]]::new()
$script:OpenStage = $null
$detailPath = Join-Path $repo 'detail.md'
$logDir = $repo
function Write-Rec([hashtable] $r) {
    New-Item -ItemType Directory -Force -Path $ledgerDir | Out-Null
    $rec = [ordered]@{ mode = 'full'; treeDigest = (Get-TreeDigest -Repo $repo).digest; finishedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'); exitCode = 0; sessionId = 's'; baselineAction = 'none'; detail = 'd' }
    foreach ($k in $r.Keys) { $rec[$k] = $r[$k] }
    Set-Content -LiteralPath (Join-Path $ledgerDir ($rec['id'] + '.result.json')) -Value ($rec | ConvertTo-Json -Depth 5) -Encoding utf8
}
"""


_TEMPLATE = {"path": None}
_TEMPLATE_LOCK = threading.Lock()


def make_repo(path):
    """A fresh repository of one commit at `path`. Every case plants its own, copied from one repository built on
    first use under the proof's temp root (`_TEMPLATE["root"]`, set by `main`): the copy keeps the files, the index
    and the commit, and costs no git process."""
    with _TEMPLATE_LOCK:
        if _TEMPLATE["path"] is None:
            _TEMPLATE["path"] = os.path.join(_TEMPLATE["root"], "_template")
            build_repo(_TEMPLATE["path"])
    shutil.copytree(_TEMPLATE["path"], path)


def build_repo(path):
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


HASH_A = "a" * 32
HASH_B = "b" * 32
SRC_A = "1" * 32
SRC_B = "2" * 32


def plant_import(repo, dest_hash=HASH_A, src_md5=SRC_A, artifact=b"CTEX" * 20, sidecar=True, commit=False,
                 src_ext="png"):
    """A tracked .import naming one dest artifact, plus that artifact's .godot/imported cache entry."""
    os.makedirs(os.path.join(repo, "Art"), exist_ok=True)
    dest = f"a.{src_ext}-{dest_hash}.ctex"
    with open(os.path.join(repo, "Art", f"a.{src_ext}.import"), "w", encoding="utf-8", newline="\n") as f:
        f.write('[remap]\n\nimporter="texture"\ntype="CompressedTexture2D"\n\n[deps]\n\n'
                f'source_file="res://Art/a.{src_ext}"\ndest_files=["res://.godot/imported/{dest}"]\n')
    gd = os.path.join(repo, ".godot", "imported")
    os.makedirs(gd, exist_ok=True)
    if artifact is not None:
        with open(os.path.join(gd, dest), "wb") as f:
            f.write(artifact)
    if sidecar:
        with open(os.path.join(gd, f"a.{src_ext}-{dest_hash}.md5"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_md5="{src_md5}"\ndest_md5="{"d" * 32}"\n')
    if commit:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "-m", "import"], check=True)


REUSE_OR_NOT = "Invoke-LedgerReuse\nWrite-Output 'NOREUSE'"
STAMP_NOW = "; importStamp = (Get-ImportStamp -Repo $repo)"


def main(tmp):
    _TEMPLATE["root"] = tmp
    # Every step is a pwsh spawn, and each case or group of cases plants its own repos under its own names, so they
    # all run beside each other, as many at once as the machine pool lends.
    groups = (cross_checkout, unimported_source_type, stage_times, failed_tests_on_stdout)
    for folder in [group.__name__ for group in groups] + ["import_state"]:
        os.makedirs(os.path.join(tmp, folder))
    with _budget(_jobs(None)) as width, ThreadPoolExecutor(max_workers=width) as pool:
        futures = [pool.submit(task) for task in import_state_tasks(os.path.join(tmp, "import_state"))]
        futures += [pool.submit(group, os.path.join(tmp, group.__name__)) for group in groups]
        futures += main_cases(tmp, pool.submit)
    for future in futures:
        future.result()


def main_cases(tmp, submit):
    """The G5 and G9 cases, one task each; returns their futures."""
    futures = []
    idx = 0

    def case(retry, status, want_reuse, label, extra=STAMP_NOW, pre="", records=None):
        nonlocal idx
        idx += 1
        number = idx
        recs = records if records is not None else [rec("p1", status, extra)]

        def run():
            rc, out, _ = run_case(tmp, number, retry, pre, recs, REUSE_OR_NOT)
            reused = "REUSE from=p1" in out
            ok = rc == 0 and reused == want_reuse and ((not reused) == ("NOREUSE" in out))
            check(label, ok, f"rc={rc} out={out!r}")
        futures.append(submit(run))

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
    def stamp_alone():
        rc, out, _ = run_case(tmp, 90, False, "", [], "Write-Output \"STAMP=$(Get-ImportStamp -Repo $repo)\"")
        stamp = next((ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("STAMP=")), "")
        check("G9 stamp: a repo with no import manifest stamps as one 40-hex value",
              rc == 0 and len(stamp) == 40, f"rc={rc} {out!r}")
    futures.append(submit(stamp_alone))

    # G9: the ledger record Complete-Gate writes carries the stamp at write time.
    def record_carries_stamp():
        pre = "New-Item -ItemType Directory -Force -Path (Join-Path $repo '.godot\\imported') | Out-Null\n" \
              "Set-Content -LiteralPath (Join-Path $repo '.godot\\imported\\a.import') -Value 'a' -Encoding utf8"
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
    futures.append(submit(record_carries_stamp))
    return futures


def driver_text(repo, body, run_id="run_current"):
    lib = (LIB.replace("@GATE@", GATE).replace("@TREE@", TREE).replace("@REPO@", repo).replace("@RETRY@", "$false")
              .replace("$runId = 'run_current'", f"$runId = '{run_id}'"))
    return lib + "\n" + body + "\n"


def run_driver(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return pwsh(["-File", path], tmp)


def cross_checkout(tmp):
    """Change 1: a verdict recorded by one checkout is reused from another checkout of the same repository."""
    import json
    a = os.path.join(tmp, "xa")
    make_repo(a)
    b = os.path.join(tmp, "xb")
    subprocess.run(["git", "-C", a, "worktree", "add", "-q", "--detach", b], check=True)
    rc, out, err = run_driver(tmp, "xa.ps1", driver_text(a, "$script:RunTree = Get-TreeDigest -Repo $repo\nComplete-Gate 'PASS' 0"))
    check("S0 producer run in checkout A completes", rc == 0, f"rc={rc} {out!r} {err!r}")
    rc, out, err = run_driver(tmp, "xb.ps1", driver_text(b, REUSE_OR_NOT, "run_b"))
    line = next((ln for ln in out.splitlines() if ln.startswith("REUSE from=")), "")
    check("S0 checkout B reuses the PASS checkout A recorded", rc == 0 and "REUSE from=run_current" in line and "NOREUSE" not in out, f"rc={rc} {out!r} {err!r}")
    check("S0 the reuse line names the producing checkout", f"checkout={a}" in line, line)
    common = subprocess.run(["git", "-C", a, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    shared = os.path.join(common, "harness", "gate_ledger", "run_current.result.json")
    check("S0 the shared record sits under the git common dir", os.path.exists(shared), shared)
    if os.path.exists(shared):
        with open(shared, encoding="utf-8-sig") as f:
            r = json.load(f)
        check("S0 the shared record names its producer checkout and run id",
              os.path.normcase(str(r.get("checkout"))) == os.path.normcase(a) and r.get("id") == "run_current", str(r.get("checkout")))
        check("S0 the shared record omits the 3 MB snapshot", "fullGreenSnapshot" not in r)
    body = "Set-Content -LiteralPath (Join-Path $repo 'tracked.txt') -Value 'changed'\n" + REUSE_OR_NOT
    rc, out, _ = run_driver(tmp, "xc.ps1", driver_text(b, body, "run_c"))
    check("S0 a different tree digest is not reused across checkouts", rc == 0 and "NOREUSE" in out and "REUSE from=" not in out, out)


def import_state_tasks(tmp):
    """S0b: the stamp is the tree's import state, so two checkouts of one tree can match. Returns the independent
    cases as zero-argument tasks; each plants its own pair of checkouts under its own index.

    The real pair this serves: the main checkout and the land-runner worktree gate one tree, and their
    .godot/imported directories never match (2026-11-10: 3,027 vs 2,328 files), so a directory stamp
    can never carry a verdict between them.
    """
    def fresh_pair(idx):
        a = os.path.join(tmp, f"ia{idx}")
        make_repo(a)
        plant_import(a, commit=True)
        b = os.path.join(tmp, f"ib{idx}")
        subprocess.run(["git", "-C", a, "worktree", "add", "-q", "--detach", b], check=True)
        return a, b

    def produce(a):
        return run_driver(tmp, f"prod_{os.path.basename(a)}.ps1",
                          driver_text(a, "$script:RunTree = Get-TreeDigest -Repo $repo\nComplete-Gate 'PASS' 0"))

    def scan(b):
        rc, out, err = run_driver(tmp, f"scan_{os.path.basename(b)}.ps1", driver_text(b, REUSE_OR_NOT, "run_b"))
        return rc, out + err

    def case(idx, b_pre, want, label, a_post=None):
        a, b = fresh_pair(idx)
        b_pre(a, b)
        rc, out, err = produce(a)
        if a_post:
            a_post(a)
        rc2, out2 = scan(b)
        reused = "REUSE from=run_current" in out2
        ok = rc == 0 and rc2 == 0 and reused == want
        check(label, ok, f"produce rc={rc} out={out!r} {err!r} scan rc={rc2} out={out2!r}")

    def history_differs(a, b):
        plant_import(b, artifact=b"A MUCH LONGER CURRENT ARTIFACT" * 7)
        with open(os.path.join(b, ".godot", "imported", f"gone.png-{HASH_B}.ctex"), "wb") as f:
            f.write(b"artifact of an asset the tree no longer has")

    def broken_state_is_not_served():
        # G9 across checkouts: a record written while the import state was broken is never served, even
        # after both checkouts are repaired.
        a, b = fresh_pair(5)
        os.remove(os.path.join(a, ".godot", "imported", f"a.png-{HASH_A}.ctex"))
        rc, out, err = produce(a)  # A's cache was missing the manifest's artifact when it ran
        plant_import(a, artifact=b"CTEX" * 20)
        rc2, out2 = scan(b)
        check("S0b a record made with a broken import state is not served to a repaired checkout",
              rc == 0 and rc2 == 0 and "REUSE from=" not in out2, f"produce rc={rc} {out!r} {err!r} scan {out2!r}")

    def stamps_move_with_the_import_state():
        # The stamp itself: equal for two current checkouts, different for a stale one.
        def stamp(repo, tag):
            rc, out, err = run_driver(tmp, f"stp_{tag}.ps1",
                                      driver_text(repo, 'Write-Output "STAMP=$(Get-ImportStamp -Repo $repo)"'))
            return next((ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("STAMP=")), "")
        a, b = fresh_pair(6)
        history_differs(a, b)
        sa, sb = stamp(a, "ia6"), stamp(b, "ib6")
        plant_import(b, src_md5=SRC_B)
        sb_stale = stamp(b, "ib6s")
        check("S0b the stamp is equal across current checkouts and moves for a stale one",
              len(sa) == 40 and sa == sb and sb_stale != sa, f"a={sa!r} b={sb!r} stale={sb_stale!r}")

    return [
        lambda: case(1, history_differs, True,
                     "S0b a current import state is reused across checkouts whose caches differ in history"),
        lambda: case(2, lambda a, b: plant_import(b, artifact=None), False,
                     "S0b a checkout missing the manifest's artifact does not reuse"),
        lambda: case(3, lambda a, b: plant_import(b, sidecar=False), False,
                     "S0b a checkout missing the artifact's sidecar does not reuse"),
        lambda: case(4, lambda a, b: plant_import(b, src_md5=SRC_B), False,
                     "S0b a checkout whose sidecar records an older source does not reuse"),
        broken_state_is_not_served,
        stamps_move_with_the_import_state,
    ]


def unimported_source_type(tmp):
    """S0c: a dest no headless import produces is editor history, not import state.

    The real pair this serves: 7 ``.aseprite`` dests sit in the main checkout's cache from a March
    editor run with the nklbdev.importality addon enabled, while the land runner's cache never holds
    one, because project.godot's ``[editor_plugins]`` enabled list omits the addon and
    ``--headless --import`` therefore never opens a ``.aseprite`` source. Folding those dests made
    the two current caches stamp differently forever, so the pair could never reuse.
    """
    def stamp(repo, tag):
        rc, out, err = run_driver(tmp, f"uo_{tag}.ps1",
                                  driver_text(repo, 'Write-Output "STAMP=$(Get-ImportStamp -Repo $repo)"'))
        return next((ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("STAMP=")), "")

    a = os.path.join(tmp, "ua")
    make_repo(a)
    plant_import(a, commit=True)
    plant_import(a, dest_hash=HASH_B, src_md5=SRC_B, src_ext="aseprite", commit=True)
    b = os.path.join(tmp, "ub")
    subprocess.run(["git", "-C", a, "worktree", "add", "-q", "--detach", b], check=True)
    plant_import(b)  # b holds the texture's artifact and sidecar, and no .aseprite dest at all
    sa, sb = stamp(a, "ua"), stamp(b, "ub")
    check("S0c a dest only an editor-plugin importer writes does not move the stamp",
          len(sa) == 40 and sa == sb, f"a={sa!r} b={sb!r}")
    rc, out, err = run_driver(tmp, "ua_prod.ps1",
                              driver_text(a, "$script:RunTree = Get-TreeDigest -Repo $repo\nComplete-Gate 'PASS' 0"))
    rc2, out2, err2 = run_driver(tmp, "ub_scan.ps1", driver_text(b, REUSE_OR_NOT, "run_b"))
    check("S0c a checkout without the editor-written dest reuses the producer's PASS",
          rc == 0 and rc2 == 0 and "REUSE from=run_current" in out2,
          f"produce rc={rc} {out!r} {err!r} scan rc={rc2} {out2!r} {err2!r}")


def failed_tests_on_stdout(tmp):
    """The verdict a main check reads: stdout carries one FAILED_TEST line per id the ledger record's failedTests lists,
    whichever suite failed it (a Logic suite's, not only an Integration batch's) and a reused run's."""
    repo = os.path.join(tmp, "fts")
    make_repo(repo)
    body = "\n".join((
        "$suiteResults['Logic'] = @{ r = [pscustomobject]@{ failedTests = @('P.Tests.Logic.Math.Add_Fails') } }",
        "$suiteResults['Sanity'] = @{ r = [pscustomobject]@{ failedTests = $null } }",
        "$script:ReusedFailedTests = @('P.Tests.Integration.Reused.Still_Fails')",
        "$script:RunTree = Get-TreeDigest -Repo $repo",
        "Complete-Gate 'FAIL' 1"))
    rc, out, err = run_driver(tmp, "fts.ps1", driver_text(repo, body))
    ids = sorted(ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("FAILED_TEST="))
    check("F1 stdout lists a Logic suite's failed test and a reused run's, as the ledger does",
          rc == 1 and ids == ["P.Tests.Integration.Reused.Still_Fails", "P.Tests.Logic.Math.Add_Fails"],
          f"rc={rc} ids={ids} {out!r} {err!r}")
    repo = os.path.join(tmp, "fts_pass")
    make_repo(repo)
    body = "$script:RunTree = Get-TreeDigest -Repo $repo" + "\n" + "Complete-Gate 'PASS' 0"
    rc, out, err = run_driver(tmp, "fts_pass.ps1", driver_text(repo, body))
    check("F1 a PASS prints no FAILED_TEST line", rc == 0 and "FAILED_TEST=" not in out, f"rc={rc} {out!r}")


def stage_times(tmp):
    """Change 2: the ledger record carries a start and end per stage, and the output one summary line."""
    import json
    repo = os.path.join(tmp, "stg")
    make_repo(repo)
    body = ("Enter-Stage 'guards'\nStart-Sleep -Milliseconds 50\nEnter-Stage 'build'\nStart-Sleep -Milliseconds 50\n"
            "$script:RunTree = Get-TreeDigest -Repo $repo\nComplete-Gate 'BLOCKED' 4")
    rc, out, err = run_driver(tmp, "stg.ps1", driver_text(repo, body))
    summary = next((ln for ln in out.splitlines() if ln.startswith("STAGES ")), "")
    check("S0 one STAGES summary line names each stage", rc == 4 and "guards=" in summary and "build=" in summary, f"rc={rc} {out!r} {err!r}")
    path = os.path.join(repo, ".claude", "scratch", "gate_ledger", "run_current.result.json")
    stages = None
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            stages = json.load(f).get("stages")
    names = [s.get("name") for s in stages or []]
    check("S0 the record lists the stages in order, the open one closed at the verdict", names == ["guards", "build"], str(names))
    ok = bool(stages) and all(s.get("start") and s.get("end") and s["end"] >= s["start"] for s in stages)
    check("S0 each stage has a wall-clock start and end", ok, str(stages))


if __name__ == "__main__":
    run_proof(main, "regression_gate_reuse-proof-")
