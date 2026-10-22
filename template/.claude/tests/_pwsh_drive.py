"""Run a PowerShell tool's functions through `pwsh -NoProfile` on planted input under a proof's own temp root."""
import os
import subprocess
import sys
import tempfile

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_proof(main, prefix):
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)


def pwsh(args, cwd, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SIDECAR", "HARNESS_WIDE_RUN_ADMITTED")}
    env.update(extra_env or {})
    done = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return done.returncode, done.stdout, done.stderr


def drive(body, tmp, tool, extra_env=None):
    driver = os.path.join(tmp, "driver.ps1")
    with open(driver, "w", encoding="utf-8", newline="\n") as f:
        f.write("$ErrorActionPreference = 'Stop'\n. '" + tool.replace("'", "''") + "'\n" + body + "\n")
    return pwsh(["-File", driver], tmp, {"PROOF_TMP": tmp, **(extra_env or {})})
