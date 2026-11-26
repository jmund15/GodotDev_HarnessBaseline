"""Proof: run_test_suite.ps1 refuses a held run without a results root and prints the route otherwise

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
The two entry cases spawn `pwsh -File`, the shape a caller uses; the filter-to-mode cases share one pwsh (`Cases`,
a runspace each) and pass the script's exit code on with `exit $LASTEXITCODE`.
"""
import os

from _pwsh_drive import Cases, check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "run_test_suite.ps1")


def call(*args):
    quoted = " ".join("'" + a.replace("'", "''") + "'" if not a.startswith("-") else a for a in args)
    return f"& '{TOOL}' {quoted}; exit $LASTEXITCODE"


def main(tmp):
    rc, out, _ = pwsh(["-File", TOOL, "-Filter", "x", "-HeldCheckout", "-Repo", tmp], tmp)
    check("-HeldCheckout without -ResultsRoot is refused with 122",
          rc == 122 and "STATUS=HELD_REFUSED" in out and "held-checkout-without-results-root" in out, f"rc={rc} {out!r}")
    rc, out, err = pwsh(["-File", TOOL, "-Filter", "x", "-RouteOnly", "-IgnoreEditor", "-Repo", tmp], tmp)
    route = out.strip().split("ROUTE=", 1)[-1].strip().splitlines()[0] if "ROUTE=" in out else ""
    check("-RouteOnly prints the checkout and exits 0",
          rc == 0 and route and os.path.samefile(route, tmp), f"rc={rc} {out!r} {err!r}")
    batch = Cases()
    mode_cases(tmp, batch)
    batch.run(tmp)


def planted_class(tmp, name, tagged):
    path = os.path.join(tmp, "Tests", "Integration", "Visual", name + ".cs")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tag = "[RequiresRenderer]\n" if tagged else ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"namespace Sample.Game.Tests.Integration.Visual;\n[TestSuite]\n{tag}public partial class {name} {{ }}\n")
    return "Sample.Game.Tests.Integration.Visual." + name


def mode_cases(tmp, batch):
    glow = planted_class(tmp, "GlowRenderTests", True)
    plain = planted_class(tmp, "LogicOnlyTests", False)

    def route_mode(filter_text, *extra, label, want, mixed=False):
        def verify(rc, out, err):
            lines = out.splitlines()
            modes, skipped, raw = [l for l in lines if l.startswith("MODE=")], [l for l in lines if l.startswith("RENDER_SKIPPED")], out + err
            ok = rc == 0 and modes == [want]
            ok = ok and (len(skipped) == 1 and glow in skipped[0] and plain not in skipped[0] if mixed else not skipped)
            check(label, ok, raw)
        batch.add(call("-Filter", filter_text, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp, *extra), verify)
    route_mode("FullyQualifiedName~Tests.Integration.Visual.GlowRenderTests", label="a filter whose every class is renderer-tagged runs windowed", want="MODE=windowed")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", label="an untagged filter runs headless", want="MODE=headless")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.", label="a mixed filter runs headless and prints one line naming the tagged class it will not render",
               want="MODE=headless", mixed=True)
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", "-Windowed", label="-Windowed runs windowed whatever the filter selects", want="MODE=windowed")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.GlowRenderTests", "-RunMode", "headless",
               label="a caller's -RunMode headless is taken as decided, never re-derived from a renderer-tagged filter", want="MODE=headless")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", "-RunMode", "windowed",
               label="a caller's -RunMode windowed runs windowed for an untagged filter", want="MODE=windowed")


if __name__ == "__main__":
    run_proof(main, "run_test_suite-proof-")
