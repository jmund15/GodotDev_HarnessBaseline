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

    def route_mode(filter_text, *extra, label, want):
        def verify(rc, out, err):
            modes = [l for l in out.splitlines() if l.startswith("MODE=")]
            check(label, rc == 0 and modes == [want] and "RENDER_SKIPPED" not in out, out + err)
        batch.add(call("-Filter", filter_text, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp, *extra), verify)

    def refused(filter_text, label):
        def verify(rc, out, err):
            raw = out + err
            check(label, rc == 126 and "STATUS=MIXED_RENDER_FILTER" in raw and glow in raw and plain not in raw
                  and "MODE=" not in raw and "ROUTE=" not in raw, raw)
        batch.add(call("-Filter", filter_text, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp), verify)
    route_mode("FullyQualifiedName~Tests.Integration.Visual.GlowRenderTests", label="a filter whose every class is renderer-tagged runs windowed", want="MODE=windowed")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", label="an untagged filter runs headless", want="MODE=headless")
    refused("FullyQualifiedName~Tests.Integration.Visual.", "a mixed filter is refused before any run: exit 126, the tagged class named, no mode chosen")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.", "-Windowed", label="-Windowed runs a mixed filter windowed whole, with nothing refused", want="MODE=windowed")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.", "-RunMode", "headless",
               label="a caller that decided the mode (the pool) is never refused on a mixed filter", want="MODE=headless")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", "-Windowed", label="-Windowed runs windowed whatever the filter selects", want="MODE=windowed")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.GlowRenderTests", "-RunMode", "headless",
               label="a caller's -RunMode headless is taken as decided, never re-derived from a renderer-tagged filter", want="MODE=headless")
    route_mode("FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", "-RunMode", "windowed",
               label="a caller's -RunMode windowed runs windowed for an untagged filter", want="MODE=windowed")


if __name__ == "__main__":
    run_proof(main, "run_test_suite-proof-")
