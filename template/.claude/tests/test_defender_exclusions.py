"""Re-runnable proof for `.claude/scripts/defender_exclusions.ps1`.

Each case runs the script under `powershell -NoProfile -Command` with stub `Get-MpPreference` / `Add-MpPreference`
functions already defined, so nothing reads or changes Defender and no elevation is needed. The stub keeps the
machine's exclusion list in `$global:have`.

    python3 .claude/tests/test_defender_exclusions.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "defender_exclusions.ps1"))
POWERSHELL = shutil.which("powershell")

STUB_READS_AND_ADDS = r"""
$global:have = @($env:DEF_HAVE -split ';' | Where-Object { $_ })
function Get-MpPreference { [pscustomobject]@{ ExclusionPath = [string[]]$global:have } }
function Add-MpPreference { param([string[]]$ExclusionPath) $global:have = @($global:have) + $ExclusionPath }
"""

STUB_READ_THROWS = r"""
function Get-MpPreference { throw 'defender unavailable' }
function Add-MpPreference { param([string[]]$ExclusionPath) }
"""


def _run(paths, have, stub):
    tmp = tempfile.mkdtemp(prefix="defender_case_")
    result = os.path.join(tmp, "result.json")
    env = dict(os.environ)
    env.update({"DEF_SCRIPT": SCRIPT, "DEF_RESULT": result, "DEF_PATHS": ";".join(paths), "DEF_HAVE": ";".join(have)})
    command = stub + "\n& $env:DEF_SCRIPT -Result $env:DEF_RESULT -Paths $env:DEF_PATHS\n"
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, "powershell exit %d: %s" % (proc.returncode, (proc.stderr or proc.stdout)[-400:])
    with open(result, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def case_adds_only_the_missing_paths():
    """A path the machine already excludes is left alone; a missing one is added."""
    have = [r"C:\Keep"]
    wanted = [r"C:\Keep", r"C:\New1", r"C:\New2"]
    report = _run(wanted, have, STUB_READS_AND_ADDS)
    assert _list(report.get("added")) == [r"C:\New1", r"C:\New2"], "added: %r" % report.get("added")


def case_every_wanted_path_reads_excluded_after_the_add():
    have = [r"C:\Keep"]
    wanted = [r"C:\Keep", r"C:\New1", r"C:\New2"]
    report = _run(wanted, have, STUB_READS_AND_ADDS)
    excluded = report.get("excluded") or {}
    assert set(excluded) == set(wanted), "keys: %r" % sorted(excluded)
    assert all(value is True for value in excluded.values()), "excluded: %r" % excluded


def case_nothing_to_add_when_all_paths_are_present():
    wanted = [r"C:\Keep", r"C:\Other"]
    report = _run(wanted, wanted, STUB_READS_AND_ADDS)
    assert _list(report.get("added")) == [], "added: %r" % report.get("added")
    assert all((report.get("excluded") or {}).values()), "excluded: %r" % report.get("excluded")


def case_a_failing_read_yields_an_error_and_no_added_key():
    report = _run([r"C:\New1"], [r"C:\Keep"], STUB_READ_THROWS)
    assert "error" in report, "report: %r" % report
    assert "added" not in report, "report: %r" % report


CASES = [v for k, v in sorted(globals().items()) if k.startswith("case_") and callable(v)]


def main():
    if not POWERSHELL:
        print("SKIP: powershell is not on PATH (this proof covers Windows only)")
        return 0
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # the proof reports every red, not the first
            failed += 1
            last = (str(exc).strip().splitlines() or ["?"])[-1][:300]
            print("FAIL %s: %s" % (fn.__name__, last))
    print("%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
