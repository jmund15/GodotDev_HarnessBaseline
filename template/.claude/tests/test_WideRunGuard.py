"""Proof: WideRunGuard.ps1 asks the real wide-run guard about the command its caller runs

Drives the tool through `pwsh -NoProfile` on a planted checkout under its own temp root: the real
`hooks/wide_test_guard.py` and its imports, one `gate_cadence` table naming the three runners, and one
declared test class. A sidecar caller's wide run is denied (exit 2), a narrow class filter and an exempt
flag are admitted, a non-sidecar caller never reaches the guard, and a guard that exits 1 reports the lost
check and admits. Nothing launches Godot, dotnet or a test run.
"""
import json
import os
import shutil

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "WideRunGuard.ps1")
HOOKS = ("wide_test_guard.py", "_wide_runs.py", "_command_text.py", "_shell_lex.py", "_git_commit.py", "_git_run.py")
TOOLS = ("adaptation.py", "sidecar_argv.py")
DECLARED = {"arg": "Filter", "glob": "Tests/**/*.cs", "decl": [r"class\s+(\w+)"], "admit": []}
CADENCE = {"slice": [], "merge": [], "close": [], "wide": [
    {"runner": r"(?:\S*/)?(?:run_test_suite|verify)\.ps1\b", "exempt": [], "narrow": {"declared": DECLARED}},
    {"runner": r"(?:\S*/)?regression_gate\.ps1\b", "exempt": [r"(?i)^-?staticonly$"], "narrow": None},
]}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def plant(tmp, tool):
    """A checkout under tmp/repo holding the tool under test, the real guard and one declared test class."""
    claude = os.path.join(tmp, "repo", ".claude")
    os.makedirs(os.path.join(claude, "scripts"))
    shutil.copy(tool, os.path.join(claude, "scripts", "WideRunGuard.ps1"))
    for name in HOOKS:
        os.makedirs(os.path.join(claude, "hooks"), exist_ok=True)
        shutil.copy(os.path.join(REPO, ".claude", "hooks", name), os.path.join(claude, "hooks", name))
    for name in TOOLS:
        os.makedirs(os.path.join(claude, "tools"), exist_ok=True)
        shutil.copy(os.path.join(REPO, ".claude", "tools", name), os.path.join(claude, "tools", name))
    write(os.path.join(claude, "skills", "project_subsystems", "adaptation.pure.defaults.json"),
          json.dumps({"gate_cadence": CADENCE}))
    write(os.path.join(tmp, "repo", "Tests", "Logic", "FooTests.cs"), "public class FooTests {}\n")
    return claude


def main(tmp, tool=TOOL):
    claude = plant(tmp, tool)
    planted = os.path.join(claude, "scripts", "WideRunGuard.ps1")
    guard = os.path.join(claude, "hooks", "wide_test_guard.py")
    sidecar = {"CLAUDE_CODE_SIDECAR": "1"}

    def run(call, env=sidecar):
        return _pwsh_drive.drive(call + '; "admitted=$env:HARNESS_WIDE_RUN_ADMITTED"', tmp, planted, env)

    rc, out, _ = run("Assert-WideRunAllowed -Entry 'run_test_suite.ps1'")
    check("a sidecar run with no filter is denied", rc == 2 and "Wide test run refused" in out and "admitted=" not in out,
          f"rc={rc} {out!r}")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'run_test_suite.ps1' -Filter 'FullyQualifiedName~Tests.Logic.'")
    check("a sidecar namespace filter is denied", rc == 2 and "admitted=" not in out, f"rc={rc} {out!r}")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'verify.ps1' -Scope 'Logic','Lint'")
    check("a sidecar verify run with a scope and no filter is denied", rc == 2, f"rc={rc} {out!r}")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'regression_gate.ps1' -Flags 'Detach'")
    check("a sidecar gate run is denied", rc == 2, f"rc={rc} {out!r}")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'run_test_suite.ps1' -Filter 'FullyQualifiedName~Tests.Logic.FooTests'")
    check("a sidecar narrow class filter is admitted", rc == 0 and "admitted=1" in out and "SKIPPED" not in out,
          f"rc={rc} {out!r}")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'regression_gate.ps1' -Flags 'StaticOnly'")
    check("a sidecar gate run with an exempt flag is admitted", rc == 0 and "admitted=1" in out and "SKIPPED" not in out,
          f"rc={rc} {out!r}")

    write(guard, "import sys\nopen(sys.argv[0] + '.ran', 'w').close()\nprint('stub')\nsys.exit(2)\n")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'run_test_suite.ps1'", {})
    check("outside a delegate nothing is checked and the guard is not called",
          rc == 0 and "admitted=" in out and "admitted=1" not in out and not os.path.exists(guard + ".ran"),
          f"rc={rc} {out!r}")
    write(guard, "import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)\n")
    rc, out, _ = run("Assert-WideRunAllowed -Entry 'run_test_suite.ps1'")
    check("a guard that exits 1 reports the lost check and admits",
          rc == 0 and "admitted=1" in out and "WIDE_RUN_GUARD=SKIPPED" in out and "guard exited 1" in out, f"rc={rc} {out!r}")


if __name__ == "__main__":
    run_proof(main, "WideRunGuard-proof-")
