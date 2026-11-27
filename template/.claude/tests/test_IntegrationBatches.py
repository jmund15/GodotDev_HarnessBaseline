"""Proof: IntegrationBatches.ps1 falls back on malformed adaptation.json and reads a valid key

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
Every case shares one pwsh (`Cases`, a runspace each).
"""
import os

from _pwsh_drive import Cases, check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "IntegrationBatches.ps1")


def main(tmp):
    batch = Cases()
    body = "$v = Get-AdaptationValue -Repo $env:ADAPT_REPO -Key k -Default dflt; \"value=$v\"; if ($v -eq 'dflt') { exit 3 }"
    for name, text in (("bad", "{bad"), ("good", '{"k": "val"}')):
        cfg = os.path.join(tmp, name, ".claude", "skills", "project_subsystems")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "adaptation.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def malformed(rc, out, err):
        check("malformed json yields the default and names the file", rc == 3 and "value=dflt" in out and "unparseable" in err, f"rc={rc} {out!r} {err!r}")
    batch.add(body, malformed, {"ADAPT_REPO": os.path.join(tmp, "bad")})
    batch.add(body, lambda rc, out, _: check("a valid string key is returned", rc == 0 and "value=val" in out, f"rc={rc} {out!r}"),
              {"ADAPT_REPO": os.path.join(tmp, "good")})
    reader_and_mode(tmp, batch)
    silence_budget_cases(tmp, batch)
    plan_cases(tmp, batch)
    trx_cases(tmp, batch)
    batch.run(tmp, TOOL)


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


def mode_body(repo, filter_text, windowed=False):
    return (f"$m = Resolve-RunMode -Repo '{repo}' -Filter '{filter_text}'" + (" -Windowed" if windowed else "") +
            "; \"mode=$($m.mode) skipped=$(@($m.skipped) -join ',')\"")


def reader_and_mode(tmp, batch):
    repo = plant_repo(tmp)

    def reader(rc, out, err):
        got = sorted(out.strip().split(",")) if out.strip() else []
        check("the reader returns exactly the tagged classes, namespaced from file-scoped and block declarations",
              rc == 0 and got == sorted(TAGGED), f"rc={rc} {out!r} {err!r}")
        check("the reader ignores an attribute in a comment, a doc comment or a string", bool(out.strip()) and not any(c in out for c in ("CommentedTests", "StringTests", "FakeTests", "PlainTests")), out)
    batch.add(f"@(Get-RendererTaggedClasses -Repo '{repo}') -join ','", reader)

    def mode_case(label, filter_text, want, windowed=False):
        batch.add(mode_body(repo, filter_text, windowed), lambda rc, out, _: check(label, out.strip() == want, f"rc={rc} {out!r}"))
    v = FQN + "Visual."
    mode_case("a filter selecting only tagged classes runs windowed", "FullyQualifiedName~Tests.Integration.Visual.TaggedFileScopedTests", "mode=windowed skipped=")
    mode_case("a filter selecting an untagged class runs headless", "FullyQualifiedName~Tests.Integration.Visual.PlainTests", "mode=headless skipped=")
    mode_case("a mixed filter runs headless and names the tagged class it will not render",
              "(FullyQualifiedName~Tests.Integration.Visual.TaggedFileScopedTests|FullyQualifiedName~Tests.Integration.Visual.PlainTests)",
              f"mode=headless skipped={v}TaggedFileScopedTests")
    mode_case("a bare term is vstest's FullyQualifiedName~ shorthand: a bare tagged class runs windowed", "TaggedFileScopedTests", "mode=windowed skipped=")
    mode_case("a bare untagged class runs headless", "PlainTests", "mode=headless skipped=")
    mode_case("a bare mixed filter names the tagged class it will not render", "TaggedFileScopedTests|PlainTests", f"mode=headless skipped={v}TaggedFileScopedTests")
    mode_case("-Windowed forces windowed", "FullyQualifiedName~Tests.Integration.Visual.PlainTests", "mode=windowed skipped=", windowed=True)
    mode_case("a filter selecting no class fails closed to headless", "FullyQualifiedName~Tests.Integration.Nothing.Here.", "mode=headless skipped=")
    mode_case("a filter on another property fails closed to headless", "TestCategory=Render", "mode=headless skipped=")
    mode_case("the Render unit's own filter (OR of tagged classes, trailing quarantine slot) runs windowed",
              "(" + "|".join(f"FullyQualifiedName~{t}." for t in TAGGED) + ")&", "mode=windowed skipped=")
    mode_case("a segment filter that excludes every tagged class runs headless",
              "FullyQualifiedName~Tests.Integration.Visual.&" + "&".join(f"FullyQualifiedName!~{t}." for t in TAGGED), "mode=headless skipped=")
    for windowed, want in ((False, "|--headless --fixed-fps 60|"), (True, "|--audio-driver Dummy|")):
        def engine_args(rc, out, _, windowed=windowed, want=want):
            check(f"a {'windowed' if windowed else 'headless'} run passes the engine {want.strip('|')}: no test run plays sound, headless runs at a fixed 60 fps",
                  out.strip() == want, f"rc={rc} {out!r}")
        batch.add(f"$m = Resolve-RunMode -Repo '{repo}' -Filter 'FullyQualifiedName~Tests.Integration.Visual.PlainTests'"
                  + (" -Windowed" if windowed else "") + "; \"|$($m.engineArgs)|\"", engine_args)


def plan_cases(tmp, batch):
    repo = plant_repo(tmp, "planrepo")
    tagged = ",".join(f"'{t}'" for t in TAGGED)
    body = (f"$man = Read-BatchManifest -Path '{os.path.join(tmp, 'none.json')}'\n"
            f"$plan = @(New-IntegrationPlan -Segments @('Visual','Playtest') -Manifest $man -TargetBatchSec 60 -MaxBatchTests 380 -RenderClasses @({tagged}))\n"
            "foreach ($p in $plan) { \"ROW $($p.label) $($p.filter)\" }\n"
            "foreach ($p in $plan) { \"WIN $($p.label) $([bool] $p.windowed)\" }\n"
            f"foreach ($p in $plan) {{ $m = Resolve-RunMode -Repo '{repo}' -Filter $p.filter; \"MODE $($p.label) $($m.mode)\" }}\n")

    def planned(rc, out, err):
        rows = [l.split(" ", 2) for l in out.splitlines() if l.startswith("ROW ")]
        windowed = {l.split(" ")[1]: l.split(" ")[2] for l in out.splitlines() if l.startswith("WIN ")}
        check("the planner marks the Render unit windowed and every batch headless",
              windowed.get("Render") == "True" and all(v == "False" for k, v in windowed.items() if k != "Render") and len(windowed) >= 2,
              f"rc={rc} {out!r}")
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
    batch.add(body, planned)
    body2 = (f"$man = Read-BatchManifest -Path '{os.path.join(tmp, 'none.json')}'\n"
             "$plan = @(New-IntegrationPlan -Segments @('Visual') -Manifest $man -TargetBatchSec 60 -MaxBatchTests 380)\n"
             "\"labels=$(($plan | ForEach-Object label) -join ',')\"")
    batch.add(body2, lambda rc, out, _: check("with no tagged class the plan has no Render row", out.strip() == "labels=Integration_B1", f"rc={rc} {out!r}"))


def trx_cases(tmp, batch):
    trx = os.path.join(tmp, "render.trx")
    with open(trx, "w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?><TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
                '<UnitTestResult testName="Sample.Game.Tests.Integration.Visual.TaggedFileScopedTests.A" duration="00:00:02"/>'
                '<UnitTestResult testName="Sample.Game.Tests.Integration.Playtest.TaggedBlockTests.B" duration="00:00:03"/>'
                '</Results></TestRun>')
    body = (f"$plan = @([pscustomobject]@{{ label = 'Render'; segs = @('Render'); status = 'GREEN'; ran = $true; trx = '{trx}' }})\n"
            "$f = Get-FreshDurations -Plan $plan\n"
            "\"keys=$(($f.durations.Keys | Sort-Object) -join ',') tests=$($f.durations['Render'].tests) sec=$($f.durations['Render'].seconds) complete=$($f.complete)\"")
    batch.add(body, lambda rc, out, err: check("the Render row's measured durations are keyed Render, never the Visual or Playtest batches' keys",
                                               out.strip() == "keys=Render tests=2 sec=5 complete=True", f"rc={rc} {out!r} {err!r}"))
    man = os.path.join(tmp, "m.json")
    body = (f"$fresh = [pscustomobject]@{{ durations = @{{ Render = [pscustomobject]@{{ seconds = 5.0; tests = 2 }} }}; complete = $true; why = '' }}\n"
            f"$plan = @([pscustomobject]@{{ label = 'Render'; segs = @('Render'); status = 'GREEN'; workWall = 6.0 }})\n"
            f"[void](Save-BatchManifest -Path '{man}' -Plan $plan -Counters @{{ Render = 0 }} -Fresh $fresh -AllSegments @('Visual'))\n"
            f"$m = Read-BatchManifest -Path '{man}'; \"units=$(($m.seconds.Keys) -join ',') wall=$($m.batchWall['Render'])\"")
    batch.add(body, lambda rc, out, err: check("the manifest keeps the Render unit though it is not a Tests/Integration segment",
                                               out.strip() == "units=Render wall=6", f"rc={rc} {out!r} {err!r}"))


BURST = """namespace Sample.Game.Tests.Stress.Trail;
using GdUnit4;
[TestSuite]
[RequireGodotRuntime]
public partial class BurstTests
{
    [TestCase(Timeout = 300000)]
    public async Task Nominal() { }

    [TestCase(Timeout = 120000)]
    [TestCase(Timeout = 180000)]
    public async Task Extreme() { }

    // [TestCase(Timeout = 900000)]
    public void Commented() { }

    private const string Doc = "[TestCase(Timeout = 900000)]";

    [TestCase(Timeout = 2000), RequireGodotRuntime]
    public void Quick() { }
}
"""
UNTIMED = """namespace Sample.Game.Tests.Stress.Trail;
[TestSuite]
public partial class UntimedTests
{
    [TestCase]
    public void Plain() { }
}
"""


def silence_budget_cases(tmp, batch):
    repo = os.path.join(tmp, "budgetrepo")
    for name, body in (("BurstTests.cs", BURST), ("UntimedTests.cs", UNTIMED)):
        path = os.path.join(repo, "Tests", "Stress", "Trail", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)

    def budget(label, filter_text, want):
        batch.add(f"[int64] (Get-FilterSilenceBudgetMs -Repo '{repo}' -Filter '{filter_text}')",
                  lambda rc, out, err: check(label, out.strip() == want, f"rc={rc} {out!r} {err!r}"))

    ns = "FullyQualifiedName~Tests.Stress.Trail."
    budget("a class filter's silence budget is the longest declared case timeout of the class, ignoring a commented or quoted one",
           ns + "BurstTests", "300000")
    budget("a method filter's budget is that method's longest timeout, so the slow neighbour does not widen it", ns + "BurstTests.Extreme", "180000")
    budget("a class declaring no timeout owes no silence budget", ns + "UntimedTests", "0")
    budget("a mixed selection takes the longest budget among the selected methods only", f"({ns}BurstTests.Quick|{ns}UntimedTests)", "2000")
    budget("a filter outside the readable grammar owes no budget: the stall bound stays at its base", "TestCategory=Stress", "0")
    budget("a filter selecting no timed class owes no budget", "FullyQualifiedName~Tests.Nothing.Here.", "0")


if __name__ == "__main__":
    run_proof(main, "IntegrationBatches-proof-")
