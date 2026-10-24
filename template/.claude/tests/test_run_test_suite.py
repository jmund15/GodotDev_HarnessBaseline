"""Proof: run_test_suite.ps1 refuses a held run without a results root and prints the route otherwise

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

from _pwsh_drive import check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "run_test_suite.ps1")


def main(tmp):
    rc, out, _ = pwsh(["-File", TOOL, "-Filter", "x", "-HeldCheckout", "-Repo", tmp], tmp)
    check("-HeldCheckout without -ResultsRoot is refused with 122",
          rc == 122 and "STATUS=HELD_REFUSED" in out and "held-checkout-without-results-root" in out, f"rc={rc} {out!r}")
    rc, out, err = pwsh(["-File", TOOL, "-Filter", "x", "-RouteOnly", "-IgnoreEditor", "-Repo", tmp], tmp)
    route = out.strip().split("ROUTE=", 1)[-1].strip().splitlines()[0] if "ROUTE=" in out else ""
    check("-RouteOnly prints the checkout and exits 0",
          rc == 0 and route and os.path.samefile(route, tmp), f"rc={rc} {out!r} {err!r}")
    mode_cases(tmp)


def planted_class(tmp, name, tagged):
    path = os.path.join(tmp, "Tests", "Integration", "Visual", name + ".cs")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tag = "[RequiresRenderer]\n" if tagged else ""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"namespace Sample.Game.Tests.Integration.Visual;\n[TestSuite]\n{tag}public partial class {name} {{ }}\n")
    return "Sample.Game.Tests.Integration.Visual." + name


def route_mode(tmp, filter_text, *extra):
    rc, out, err = pwsh(["-File", TOOL, "-Filter", filter_text, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp, *extra], tmp)
    lines = out.splitlines()
    return rc, [l for l in lines if l.startswith("MODE=")], [l for l in lines if l.startswith("RENDER_SKIPPED")], out + err


def mode_cases(tmp):
    glow = planted_class(tmp, "GlowRenderTests", True)
    plain = planted_class(tmp, "LogicOnlyTests", False)
    rc, modes, skipped, raw = route_mode(tmp, "FullyQualifiedName~Tests.Integration.Visual.GlowRenderTests")
    check("a filter whose every class is renderer-tagged runs windowed", rc == 0 and modes == ["MODE=windowed"] and not skipped, raw)
    rc, modes, skipped, raw = route_mode(tmp, "FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests")
    check("an untagged filter runs headless", rc == 0 and modes == ["MODE=headless"] and not skipped, raw)
    rc, modes, skipped, raw = route_mode(tmp, "FullyQualifiedName~Tests.Integration.Visual.")
    check("a mixed filter runs headless and prints one line naming the tagged class it will not render",
          rc == 0 and modes == ["MODE=headless"] and len(skipped) == 1 and glow in skipped[0] and plain not in skipped[0], raw)
    rc, modes, skipped, raw = route_mode(tmp, "FullyQualifiedName~Tests.Integration.Visual.LogicOnlyTests", "-Windowed")
    check("-Windowed runs windowed whatever the filter selects", rc == 0 and modes == ["MODE=windowed"] and not skipped, raw)


if __name__ == "__main__":
    run_proof(main, "run_test_suite-proof-")
