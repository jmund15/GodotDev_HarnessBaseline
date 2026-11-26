"""Proof: StaticGuards.ps1 reports a missing guard as unavailable and a passing set as OK

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
Every case plants its own repo and hooks folder and shares one pwsh (`Cases`, a runspace each).
"""
import os
import sys

from _pwsh_drive import Cases, check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "StaticGuards.ps1")

GUARDS = ("tres_nullstrip_guard.py", "tool_cascade_audit.py", "tres_script_strip_guard.py",
          "test_suite_gate_coverage_guard.py", "duplicate_test_double_guard.py", "refcounted_free_guard.py")
RUN = ("$r = Invoke-StaticGuards -Repo $env:PROOF_TMP -Hooks (Join-Path $env:PROOF_TMP 'hooks') -Py $env:PROOF_PY; "
       "\"results=$($r.Results -join ',') unavailable=$($r.Unavailable) blocked=$($r.Blocked -join ',')\"; "
       "if ($r.Unavailable -or $r.Blocked.Count -gt 0) { exit 3 }")


def main(tmp):
    batch = Cases()
    number = 0

    def case(code_for, body, verify, domain_map=False):
        """A repo of its own with the guards `code_for` names planted; `PROOF_TMP` for the case is that repo."""
        nonlocal number
        number += 1
        repo = os.path.join(tmp, f"repo{number}")
        hooks = os.path.join(repo, "hooks")
        os.makedirs(hooks)
        for name in GUARDS:
            code = code_for(name)
            if code is not None:
                with open(os.path.join(hooks, name), "w", encoding="utf-8", newline="\n") as f:
                    f.write(f"def main():\n    return {code}\n")
        if domain_map:
            map_path = os.path.join(repo, ".claude", "scripts", "gate_domain_map.json")
            os.makedirs(os.path.dirname(map_path))
            with open(map_path, "w", encoding="utf-8", newline="\n") as f:
                f.write('{"domains": {}, "excluded_namespaces": {}}')
        batch.add(body, verify, {"PROOF_TMP": repo, "PROOF_PY": sys.executable})

    def missing(rc, out, _):
        check("a missing guard stops the run, exit 3", rc == 3 and "nullstrip=UNAVAILABLE" in out and "unavailable=True" in out, f"rc={rc} {out!r}")
    case(lambda name: None, RUN, missing)

    def failing(rc, out, _):
        check("a failing guard is blocked by name, exit 3", rc == 3 and "tool_cascade=FAIL" in out and "blocked=tool_cascade" in out, f"rc={rc} {out!r}")
    case(lambda name: 1 if name == "tool_cascade_audit.py" else 0, RUN, failing)

    def passing(rc, out, _):
        check("six passing guards are all OK", rc == 0 and out.count("=OK") == 6, f"rc={rc} {out!r}")
    case(lambda name: 0, RUN, passing)

    def targeted(rc, out, _):
        check("every guard receives the target as --repo", rc == 0 and out.count("=OK") == 6, f"rc={rc} {out!r}")
    case(lambda name: "(0 if __import__('sys').argv[1:3] == ['--repo', __import__('os').environ['PROOF_TMP']] else 4)", RUN, targeted)

    # The gate's own domain map is the full gate's check: a scope run leaves it out, the full gate runs it.
    results = ("$r = Invoke-StaticGuards -Repo $env:PROOF_TMP -Hooks (Join-Path $env:PROOF_TMP 'hooks') "
               "-Py $env:PROOF_PY%s; \"results=$($r.Results -join ',')\"")

    def scope_run(rc, out, _):
        check("a scope run leaves the domain map check out", rc == 0 and "results=" in out and "domain_map" not in out, f"rc={rc} {out!r}")
    case(lambda name: 0, results % "", scope_run, domain_map=True)
    case(lambda name: 0, results % " -FullGate", lambda rc, out, _: check("the full gate runs the domain map check", "domain_map=" in out, f"rc={rc} {out!r}"),
         domain_map=True)
    batch.run(tmp, TOOL)


if __name__ == "__main__":
    run_proof(main, "StaticGuards-proof-")
