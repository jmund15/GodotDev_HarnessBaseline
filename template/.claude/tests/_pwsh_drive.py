"""Run a PowerShell tool's functions through `pwsh -NoProfile` on planted input under a proof's own temp root."""
import json
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


def _script(body, tool):
    head = "$ErrorActionPreference = 'Stop'\n" + (". '" + tool.replace("'", "''") + "'\n" if tool else "")
    return head + body + "\n"


def drive(body, tmp, tool, extra_env=None):
    driver = os.path.join(tmp, "driver.ps1")
    with open(driver, "w", encoding="utf-8", newline="\n") as f:
        f.write(_script(body, tool))
    return pwsh(["-File", driver], tmp, {"PROOF_TMP": tmp, **(extra_env or {})})


# One pwsh for many cases: each case file runs through `Invoke-Isolated` (`_isolated.ps1`, which states what a case
# keeps to itself and how its code, stdout and stderr match a `pwsh -File` child).
ISOLATED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_isolated.ps1")
_MANY = r"""
$ErrorActionPreference = 'Stop'
. $args[2]
$jobs = @(Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json)
$results = foreach ($job in $jobs) {
    $environment = @{}
    foreach ($e in $job.env.PSObject.Properties) { $environment[$e.Name] = [string]$e.Value }
    Invoke-Isolated -File $job.file -Environment $environment
}
[IO.File]::WriteAllText($args[1], (ConvertTo-Json -InputObject @($results) -Depth 4), [Text.UTF8Encoding]::new($false))
"""


def drive_many(cases, tmp, tool=None, timeout=900):
    """Run each `(body, env)` of `cases` through one pwsh; returns `[(rc, stdout, stderr)]` in order, as `drive` would
    per case. Keep one `drive` case where the proof is about a spawned process itself (its exit code, its pipes)."""
    work, jobs = tempfile.mkdtemp(prefix="many-", dir=tmp), []
    manifest = os.path.join(work, "cases.json")
    for n, (body, env) in enumerate(cases):
        path = os.path.join(work, f"case{n}.ps1")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_script(body, tool))
        jobs.append({"file": path, "env": {"PROOF_TMP": tmp, **(env or {})}})
    with open(manifest, "w", encoding="utf-8", newline="\n") as f:
        json.dump(jobs, f)
    runner, results = os.path.join(work, "runner.ps1"), os.path.join(work, "results.json")
    with open(runner, "w", encoding="utf-8", newline="\n") as f:
        f.write(_MANY)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SIDECAR", "HARNESS_WIDE_RUN_ADMITTED")}
    done = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", runner, manifest, results, ISOLATED],
                          cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    if not os.path.exists(results):
        raise RuntimeError(f"drive_many produced no results (rc={done.returncode}): {done.stdout}{done.stderr}")
    with open(results, encoding="utf-8") as f:
        rows = json.load(f)
    return [(r["rc"], r["out"], r["err"]) for r in rows]


class Cases:
    """Cases registered with their own verdict, run through one pwsh (`drive_many`), then judged in registration order.
    A case plants its files when it is added; its `verify(rc, out, err)` calls `check`. Nothing a case needs may come
    from an earlier case's output."""

    def __init__(self):
        self.rows = []

    def add(self, body, verify, env=None):
        self.rows.append((body, env, verify))

    def run(self, tmp, tool=None):
        results = drive_many([(body, env) for body, env, _ in self.rows], tmp, tool)
        for (_, _, verify), result in zip(self.rows, results):
            verify(*result)
