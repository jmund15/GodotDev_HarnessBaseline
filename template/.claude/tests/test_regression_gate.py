"""Proof: regression_gate.ps1 refuses a missing repo and prints the route for an existing one

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os

from _pwsh_drive import check, pwsh, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "regression_gate.ps1")


def main(tmp):
    rc, _, _ = pwsh(["-File", TOOL, "-RouteOnly", "-IgnoreEditor", "-Repo", os.path.join(tmp, "missing")], tmp)
    check("a missing -Repo exits non-zero", rc != 0, f"rc={rc}")
    rc, out, err = pwsh(["-File", TOOL, "-RouteOnly", "-IgnoreEditor", "-Repo", tmp], tmp)
    route = out.strip().split("ROUTE=", 1)[-1].strip()
    check("-RouteOnly prints the checkout and exits 0",
          rc == 0 and "ROUTE=" in out and os.path.samefile(route, tmp), f"rc={rc} {out!r} {err!r}")


if __name__ == "__main__":
    run_proof(main, "regression_gate-proof-")
