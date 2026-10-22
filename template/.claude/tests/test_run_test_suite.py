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


if __name__ == "__main__":
    run_proof(main, "run_test_suite-proof-")
