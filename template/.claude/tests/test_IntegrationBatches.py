"""Proof: IntegrationBatches.ps1 falls back on malformed adaptation.json and reads a valid key

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "IntegrationBatches.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    cfg = os.path.join(tmp, ".claude", "skills", "project_subsystems")
    os.makedirs(cfg)
    path = os.path.join(cfg, "adaptation.json")
    body = "$v = Get-AdaptationValue -Repo $env:PROOF_TMP -Key k -Default dflt; \"value=$v\"; if ($v -eq 'dflt') { exit 3 }"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("{bad")
    rc, out, err = drive(body, tmp)
    check("malformed json yields the default and names the file", rc == 3 and "value=dflt" in out and "unparseable" in err, f"rc={rc} {out!r} {err!r}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('{"k": "val"}')
    rc, out, _ = drive(body, tmp)
    check("a valid string key is returned", rc == 0 and "value=val" in out, f"rc={rc} {out!r}")
    reader_and_mode(tmp)
    plan_cases(tmp)
    trx_cases(tmp)


TAGGED_FILE = """namespace Sample.Game.Tests.Integration.Visual;

using GdUnit4;

[TestSuite, RequireGodotRuntime]
[RequiresRenderer]
public partial class TaggedFileScopedTests
{
}
"""
TAGGED_BLOCK = """using GdUnit4;
namespace Sample.Game.Tests.Integration.Playtest
{
    [TestSuite]
    [RequiresRenderer]
    public partial class TaggedBlockTests
    {
    }
}
"""
TAGGED_COMBINED = """namespace Sample.Game.Tests.Integration.Visual;
[TestSuite, RequiresRendererAttribute, RequireGodotRuntime] public sealed class TaggedCombinedTests { }
"""
PLAIN = """namespace Sample.Game.Tests.Integration.Visual;
[TestSuite]
public partial class PlainTests { }
"""
COMMENTED = """namespace Sample.Game.Tests.Integration.Visual;
// [RequiresRenderer]
/* [RequiresRenderer] */
/// <summary>Needs [RequiresRenderer] one day.</summary>
[TestSuite]
public partial class CommentedTests { }
"""
STRINGY = """namespace Sample.Game.Tests.Integration.Visual;
[TestSuite]
public partial class StringTests
{
    private const string Doc = "[RequiresRenderer] // not a tag";
    private const string Verbatim = @"[RequiresRenderer]
public class FakeTests";
}
"""
FQN = "Sample.Game.Tests.Integration."
TAGGED = [FQN + "Visual.TaggedFileScopedTests", FQN + "Playtest.TaggedBlockTests", FQN + "Visual.TaggedCombinedTests"]


def plant_repo(tmp, name="repo"):
    repo = os.path.join(tmp, name)
    for rel, body in (("Visual/TaggedFileScopedTests.cs", TAGGED_FILE), ("Playtest/TaggedBlockTests.cs", TAGGED_BLOCK),
                      ("Visual/TaggedCombinedTests.cs", TAGGED_COMBINED), ("Visual/PlainTests.cs", PLAIN),
                      ("Visual/CommentedTests.cs", COMMENTED), ("Visual/StringTests.cs", STRINGY)):
        path = os.path.join(repo, "Tests", "Integration", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
    return repo


def mode_of(tmp, repo, filter_text, windowed=False):
    body = (f"$m = Resolve-RunMode -Repo '{repo}' -Filter '{filter_text}'" + (" -Windowed" if windowed else "") +
            "; \"mode=$($m.mode) skipped=$(@($m.skipped) -join ',')\"")
    rc, out, err = drive(body, tmp)
    return rc, out.strip(), err


def reader_and_mode(tmp):
    repo = plant_repo(tmp)
    rc, out, err = drive(f"@(Get-RendererTaggedClasses -Repo '{repo}') -join ','", tmp)
    got = sorted(out.strip().split(",")) if out.strip() else []
    check("the reader returns exactly the tagged classes, namespaced from file-scoped and block declarations",
          rc == 0 and got == sorted(TAGGED), f"rc={rc} {out!r} {err!r}")
    check("the reader ignores an attribute in a comment, a doc comment or a string", bool(out.strip()) and not any(c in out for c in ("CommentedTests", "StringTests", "FakeTests", "PlainTests")), out)

    v = FQN + "Visual."
    rc, out, _ = mode_of(tmp, repo, "FullyQualifiedName~Tests.Integration.Visual.TaggedFileScopedTests")
    check("a filter selecting only tagged classes runs windowed", out == "mode=windowed skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "FullyQualifiedName~Tests.Integration.Visual.PlainTests")
    check("a filter selecting an untagged class runs headless", out == "mode=headless skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "(FullyQualifiedName~Tests.Integration.Visual.TaggedFileScopedTests|FullyQualifiedName~Tests.Integration.Visual.PlainTests)")
    check("a mixed filter runs headless and names the tagged class it will not render",
          out == f"mode=headless skipped={v}TaggedFileScopedTests", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "FullyQualifiedName~Tests.Integration.Visual.PlainTests", windowed=True)
    check("-Windowed forces windowed", out == "mode=windowed skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "FullyQualifiedName~Tests.Integration.Nothing.Here.")
    check("a filter selecting no class fails closed to headless", out == "mode=headless skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "TestCategory=Render")
    check("a filter on another property fails closed to headless", out == "mode=headless skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "(" + "|".join(f"FullyQualifiedName~{t}." for t in TAGGED) + ")&")
    check("the Render unit's own filter (OR of tagged classes, trailing quarantine slot) runs windowed", out == "mode=windowed skipped=", f"rc={rc} {out!r}")
    rc, out, _ = mode_of(tmp, repo, "FullyQualifiedName~Tests.Integration.Visual.&" + "&".join(f"FullyQualifiedName!~{t}." for t in TAGGED))
    check("a segment filter that excludes every tagged class runs headless", out == "mode=headless skipped=", f"rc={rc} {out!r}")
    for windowed, want in ((False, "|--headless|"), (True, "|--audio-driver Dummy|")):
        rc, out, _ = drive(f"$m = Resolve-RunMode -Repo '{repo}' -Filter 'FullyQualifiedName~Tests.Integration.Visual.PlainTests'"
                           + (" -Windowed" if windowed else "") + "; \"|$($m.engineArgs)|\"", tmp)
        check(f"a {'windowed' if windowed else 'headless'} run passes the engine {want.strip('|')}: no test run plays sound",
              out.strip() == want, f"rc={rc} {out!r}")


def plan_cases(tmp):
    repo = plant_repo(tmp, "planrepo")
    tagged = ",".join(f"'{t}'" for t in TAGGED)
    body = (f"$man = Read-BatchManifest -Path '{os.path.join(tmp, 'none.json')}'\n"
            f"$plan = @(New-IntegrationPlan -Segments @('Visual','Playtest') -Manifest $man -TargetBatchSec 60 -MaxBatchTests 380 -RenderClasses @({tagged}))\n"
            "foreach ($p in $plan) { \"ROW $($p.label) $($p.filter)\" }\n"
            f"foreach ($p in $plan) {{ $m = Resolve-RunMode -Repo '{repo}' -Filter $p.filter; \"MODE $($p.label) $($m.mode)\" }}\n")
    rc, out, err = drive(body, tmp)
    rows = [l.split(" ", 2) for l in out.splitlines() if l.startswith("ROW ")]
    modes = {l.split(" ")[1]: l.split(" ")[2] for l in out.splitlines() if l.startswith("MODE ")}
    render = [r for r in rows if r[1] == "Render"]
    others = [r for r in rows if r[1] != "Render"]
    check("the plan holds exactly one Render row beside the segment batches", rc == 0 and len(render) == 1 and len(others) >= 1, f"rc={rc} {out!r} {err!r}")
    check("every segment batch excludes every tagged class",
          bool(others) and all(f"&FullyQualifiedName!~{t}." in r[2] for r in others for t in TAGGED), out)
    check("the Render row selects every tagged class and excludes none",
          bool(render) and all(f"FullyQualifiedName~{t}." in render[0][2] for t in TAGGED) and "!~" not in render[0][2], out)
    check("through the runner's own mode choice the Render row is windowed and every batch headless",
          modes.get("Render") == "windowed" and all(v == "headless" for k, v in modes.items() if k != "Render") and len(modes) == len(rows), f"{modes} {err!r}")
    body2 = (f"$man = Read-BatchManifest -Path '{os.path.join(tmp, 'none.json')}'\n"
             "$plan = @(New-IntegrationPlan -Segments @('Visual') -Manifest $man -TargetBatchSec 60 -MaxBatchTests 380)\n"
             "\"labels=$(($plan | ForEach-Object label) -join ',')\"")
    rc, out, _ = drive(body2, tmp)
    check("with no tagged class the plan has no Render row", out.strip() == "labels=Integration_B1", f"rc={rc} {out!r}")


def trx_cases(tmp):
    trx = os.path.join(tmp, "render.trx")
    with open(trx, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
                '<UnitTestResult testName="Sample.Game.Tests.Integration.Visual.TaggedFileScopedTests.A" duration="00:00:02"/>'
                '<UnitTestResult testName="Sample.Game.Tests.Integration.Playtest.TaggedBlockTests.B" duration="00:00:03"/>'
                '</Results></TestRun>')
    body = (f"$plan = @([pscustomobject]@{{ label = 'Render'; segs = @('Render'); status = 'GREEN'; ran = $true; trx = '{trx}' }})\n"
            "$f = Get-FreshDurations -Plan $plan\n"
            "\"keys=$(($f.durations.Keys | Sort-Object) -join ',') tests=$($f.durations['Render'].tests) sec=$($f.durations['Render'].seconds) complete=$($f.complete)\"")
    rc, out, err = drive(body, tmp)
    check("the Render row's measured durations are keyed Render, never the Visual or Playtest batches' keys",
          out.strip() == "keys=Render tests=2 sec=5 complete=True", f"rc={rc} {out!r} {err!r}")
    man = os.path.join(tmp, "m.json")
    body = (f"$fresh = [pscustomobject]@{{ durations = @{{ Render = [pscustomobject]@{{ seconds = 5.0; tests = 2 }} }}; complete = $true; why = '' }}\n"
            f"$plan = @([pscustomobject]@{{ label = 'Render'; segs = @('Render'); status = 'GREEN'; workWall = 6.0 }})\n"
            f"[void](Save-BatchManifest -Path '{man}' -Plan $plan -Counters @{{ Render = 0 }} -Fresh $fresh -AllSegments @('Visual'))\n"
            f"$m = Read-BatchManifest -Path '{man}'; \"units=$(($m.seconds.Keys) -join ',') wall=$($m.batchWall['Render'])\"")
    rc, out, err = drive(body, tmp)
    check("the manifest keeps the Render unit though it is not a Tests/Integration segment", out.strip() == "units=Render wall=6", f"rc={rc} {out!r} {err!r}")


if __name__ == "__main__":
    run_proof(main, "IntegrationBatches-proof-")
