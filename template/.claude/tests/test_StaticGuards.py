"""Proof: StaticGuards.ps1 reports a missing guard as unavailable and a passing set as OK

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "StaticGuards.ps1")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def pwsh(args, cwd, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SIDECAR", "HARNESS_WIDE_RUN_ADMITTED")}
    env.update(extra_env or {})
    done = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return done.returncode, done.stdout, done.stderr


def drive(body, tmp, extra_env=None, tool=TOOL):
    driver = os.path.join(tmp, "driver.ps1")
    with open(driver, "w", encoding="utf-8", newline="\n") as f:
        f.write("$ErrorActionPreference = 'Stop'\n. '" + tool.replace("'", "''") + "'\n" + body + "\n")
    return pwsh(["-File", driver], tmp, {"PROOF_TMP": tmp, **(extra_env or {})})


GUARDS = ("tres_nullstrip_guard.py", "tool_cascade_audit.py", "tres_script_strip_guard.py",
          "test_suite_gate_coverage_guard.py", "duplicate_test_double_guard.py", "refcounted_free_guard.py")
RUN = ("$r = Invoke-StaticGuards -Repo $env:PROOF_TMP -Hooks (Join-Path $env:PROOF_TMP 'hooks') -Py $env:PROOF_PY; "
       "\"results=$($r.Results -join ',') unavailable=$($r.Unavailable) blocked=$($r.Blocked -join ',')\"; "
       "if ($r.Unavailable -or $r.Blocked.Count -gt 0) { exit 3 }")


def plant(tmp, code_for):
    hooks = os.path.join(tmp, "hooks")
    shutil.rmtree(hooks, ignore_errors=True)
    os.makedirs(hooks)
    for name in GUARDS:
        code = code_for(name)
        if code is not None:
            with open(os.path.join(hooks, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(f"def main():\n    return {code}\n")


def main(tmp):
    env = {"PROOF_PY": sys.executable}
    plant(tmp, lambda name: None)
    rc, out, _ = drive(RUN, tmp, env)
    check("a missing guard stops the run, exit 3", rc == 3 and "nullstrip=UNAVAILABLE" in out and "unavailable=True" in out, f"rc={rc} {out!r}")
    plant(tmp, lambda name: 1 if name == "tool_cascade_audit.py" else 0)
    rc, out, _ = drive(RUN, tmp, env)
    check("a failing guard is blocked by name, exit 3", rc == 3 and "tool_cascade=FAIL" in out and "blocked=tool_cascade" in out, f"rc={rc} {out!r}")
    plant(tmp, lambda name: 0)
    rc, out, _ = drive(RUN, tmp, env)
    check("six passing guards are all OK", rc == 0 and out.count("=OK") == 6, f"rc={rc} {out!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="StaticGuards-proof-") as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)
