"""Proof: RuntimeSlots.ps1 names the runtime slots and maps a crash exit to an overlap signature

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: failure outcomes
(a deny or a non-zero exit) and success outcomes. Nothing launches Godot, dotnet or a test run.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

import _pwsh_drive

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "RuntimeSlots.ps1")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    quiet = "$k = Get-OverlapSignatureKind -ExitCode 0; \"kind=[$k]\"; if (-not $k) { exit 3 }"
    rc, out, _ = drive(quiet, tmp)
    check("a clean exit has no overlap signature, exit 3", rc == 3 and "kind=[]" in out, f"rc={rc} {out!r}")
    rc, out, _ = drive("\"kind=$(Get-OverlapSignatureKind -ExitCode -1073741795)\"", tmp)
    check("a native crash exit maps to its signature", rc == 0 and "kind=testhost-native-crash" in out, f"rc={rc} {out!r}")
    for exit_code, platform, want in ((132, "linux", "testhost-native-crash"), (132, "windows", ""),
                                      (-1073741795, "linux", ""), (139, "linux", ""), (1, "linux", "")):
        rc, out, _ = drive(f"\"kind=[$(Get-OverlapSignatureKind -ExitCode {exit_code} -Platform {platform})]\"", tmp)
        check(f"exit {exit_code} on {platform} maps to [{want}]", rc == 0 and f"kind=[{want}]" in out, f"rc={rc} {out!r}")
    with open(os.path.join(REPO, ".claude", "scripts", "run_test_suite.ps1"), encoding="utf-8") as f:
        check("run_test_suite.ps1 holds no native-crash exit literal of its own", "-1073741795" not in f.read())
    rc, out, _ = drive("$n = @(Get-RuntimeSlotNames); \"count=$($n.Count) unique=$(@($n | Sort-Object -Unique).Count)\"", tmp)
    check("slot names are distinct", rc == 0 and "count=8 unique=8" in out, f"rc={rc} {out!r}")
    isolation(tmp)
    wait_run_lock(tmp)


HOLDER = """param([string] $Name, [string] $Own)
$m = [System.Threading.Mutex]::new($false, $Name)
if ($Own -eq '1' -and -not $m.WaitOne(0)) { 'BUSY'; exit 3 }
'HELD'
[Console]::Out.Flush()
Start-Sleep -Seconds 300
"""


def hold(tmp, name, own=True):
    """A child pwsh that owns the named mutex until it is killed (own=False: only keeps the named object open, as a
    waiting process does). Ownership is per process, so it cannot be planted."""
    script = os.path.join(tmp, "holder.ps1")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOLDER)
    child = subprocess.Popen(["pwsh", "-NoProfile", "-File", script, name, "1" if own else "0"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
    line = child.stdout.readline().strip()
    return child if line == "HELD" else None


def wait_run_lock(tmp):
    """Wait-RunLock over real cross-process holders: the first free handle by index, a timeout, and an abandoned holder."""
    tag = uuid.uuid4().hex[:10]
    names = [f"Global\\harness-proof-{tag}-{n}" for n in "abc"]
    env = {"NAME_A": names[0], "NAME_B": names[1], "NAME_C": names[2]}
    body = ("$h = @('NAME_A', 'NAME_B', 'NAME_C' | ForEach-Object { [System.Threading.Mutex]::new($false, [Environment]::GetEnvironmentVariable($_)) }); "
            "$sw = [Diagnostics.Stopwatch]::StartNew(); $r = Wait-RunLock -Handles $h -TimeoutMs %d; $sw.Stop(); "
            "'IDX=' + $r.Index; 'ABANDONED=' + $r.Abandoned; 'MS=' + $sw.ElapsedMilliseconds")
    holders = []
    try:
        holders = [hold(tmp, names[0]), hold(tmp, names[1])]
        check("the holder children own their mutexes", all(holders), f"{holders!r}")
        rc, out, _ = drive(body % 3000, tmp, env)
        got = kv(out)
        check("Wait-RunLock grants the first free handle by index while others are held",
              rc == 0 and got.get("IDX") == "2" and got.get("ABANDONED") == "False", f"rc={rc} {out!r}")
        holders.append(hold(tmp, names[2]))
        holders.append(hold(tmp, names[0], own=False))  # a waiter's open handle: it keeps the object alive past its owner
        rc, out, _ = drive(body % 700, tmp, env)
        got = kv(out)
        check("Wait-RunLock returns -1 after the timeout when every handle is held",
              rc == 0 and got.get("IDX") == "-1" and 600 <= int(got.get("MS", "0")) < 3000, f"rc={rc} {out!r}")
        holders[0].kill()
        holders[0].wait()
        rc, out, _ = drive(body % 3000, tmp, env)
        got = kv(out)
        check("a killed holder's mutex is granted as abandoned, at its index",
              rc == 0 and got.get("IDX") == "0" and got.get("ABANDONED") == "True", f"rc={rc} {out!r}")
    finally:
        for child in holders:
            if child is not None:
                child.kill()
                child.wait()


def kv(out):
    return dict(l.strip().split("=", 1) for l in out.splitlines() if "=" in l)


def isolation(tmp):
    """The per-run user dir and its inline VSTest key, each platform from planted roots."""
    repo = os.path.join(tmp, "repo")
    local, xdg = os.path.join(tmp, "local"), os.path.join(tmp, "xdg")
    body = "$i = Get-TestIsolation -Repo '%s' -Platform %s -Tag abc; ('DIR=' + $i.AppData); ('ARG=' + $i.AppDataArg)"
    rc, out, _ = drive(body % (repo, "windows"), tmp, {"LOCALAPPDATA": local})
    lines = dict(l.split("=", 1) for l in out.splitlines() if l.startswith(("DIR=", "ARG=")))
    want = os.path.join(local, "{{PROJECT_NAME}}", "test-appdata")
    check("Windows isolation keeps the APPDATA key under LOCALAPPDATA",
          rc == 0 and lines.get("DIR", "").startswith(want) and lines.get("ARG", "").startswith("RunConfiguration.EnvironmentVariables.APPDATA="),
          f"rc={rc} {out!r}")
    rc, out, _ = drive(body % (repo, "linux"), tmp, {"XDG_DATA_HOME": xdg})
    lines = dict(l.split("=", 1) for l in out.splitlines() if l.startswith(("DIR=", "ARG=")))
    arg = lines.get("ARG", "")
    check("Linux isolation takes the XDG_DATA_HOME key under XDG_DATA_HOME",
          rc == 0 and arg.startswith("RunConfiguration.EnvironmentVariables.XDG_DATA_HOME=") and "\\" not in arg
          and arg.split("=", 1)[1].startswith(xdg.replace("\\", "/")) and arg.endswith("/test-appdata/" + lines.get("DIR", "").replace("\\", "/").rsplit("/", 1)[-1]),
          f"rc={rc} {out!r}")
    sys.path.insert(0, os.path.join(REPO, ".claude", "scripts"))
    import engine_userdata
    for platform, host in (("windows", "win32"), ("linux", "linux")):
        want = engine_userdata.isolation(repo, env={"LOCALAPPDATA": local}, platform=host)
        rc, out, _ = drive("$i = Get-TestIsolation -Repo '%s' -Platform %s -Tag abc; ('KEY=' + $i.Key); ('USERDIR=' + $i.UserDir)" % (repo, platform),
                           tmp, {"LOCALAPPDATA": local, "XDG_DATA_HOME": xdg})
        got = kv(out)
        key = next(k for k in want if k != "USERDIR")
        check("the %s isolation key and engine user dir tail equal engine_userdata's" % platform,
              rc == 0 and got.get("KEY") == key and got.get("USERDIR", "").endswith(want["USERDIR"][len(want[key]):]),
              f"rc={rc} {out!r} {want!r}")
    temp, tmpdir = os.path.join(tmp, "t"), os.path.join(tmp, "t")
    rc, out, _ = drive("'R=' + (Get-RuntimeSlotRecordDir)", tmp, {"TEMP": temp, "TMPDIR": tmpdir})
    got = out.strip().split("R=", 1)[-1].replace("\\", "/")
    check("the slot record dir sits under the host's temp root",
          rc == 0 and got == os.path.join(tmp, "t", "harness-activity", "runtime-slots").replace("\\", "/"), f"rc={rc} {out!r}")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="RuntimeSlots-proof-") as tmp:
        main(tmp)
    print("FAILED: " + ", ".join(failures) if failures else "all passed")
    sys.exit(1 if failures else 0)
