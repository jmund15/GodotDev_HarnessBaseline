"""Proof: RuntimeSlots.ps1 names the runtime slots and maps a crash exit to an overlap signature

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: failure outcomes
(a deny or a non-zero exit) and success outcomes. Nothing launches Godot, dotnet or a test run.
The decision cases share one pwsh (`Cases`, a runspace each); the lock cases spawn holder processes because
the lock between processes is what they decide.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid

import _pwsh_drive
from _pwsh_drive import Cases, check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "RuntimeSlots.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    batch = Cases()
    quiet = "$k = Get-OverlapSignatureKind -ExitCode 0; \"kind=[$k]\"; if (-not $k) { exit 3 }"
    batch.add(quiet, lambda rc, out, _: check("a clean exit has no overlap signature, exit 3", rc == 3 and "kind=[]" in out, f"rc={rc} {out!r}"))

    def kind_is(exit_code, platform, want):
        return lambda rc, out, _: check(f"exit {exit_code} on {platform} maps to [{want}]", rc == 0 and f"kind=[{want}]" in out, f"rc={rc} {out!r}")
    for exit_code, platform, want in ((132, "linux", "testhost-native-crash"), (132, "windows", ""),
                                      (-1073741795, "windows", "testhost-native-crash"),
                                      (-1073741795, "linux", ""), (139, "linux", ""), (1, "linux", "")):
        batch.add(f"\"kind=[$(Get-OverlapSignatureKind -ExitCode {exit_code} -Platform {platform})]\"", kind_is(exit_code, platform, want))
    with open(os.path.join(REPO, ".claude", "scripts", "run_test_suite.ps1"), encoding="utf-8") as f:
        check("run_test_suite.ps1 holds no native-crash exit literal of its own", "-1073741795" not in f.read())
    batch.add("$n = @(Get-RuntimeSlotNames); \"count=$($n.Count) unique=$(@($n | Sort-Object -Unique).Count)\"",
              lambda rc, out, _: check("slot names are distinct", rc == 0 and "count=8 unique=8" in out, f"rc={rc} {out!r}"))
    isolation(tmp, batch)
    trim = ("$script:LeaseLogCapBytes = 1; $script:LeaseLogKeepRows = 1; $d = Join-Path $env:PROOF_TMP 'trim'; "
            "foreach ($n in 0..2) { Add-LeaseRow -RecordDir $d -Row ([ordered]@{ id = \"r$n\" }) }; "
            "'ROWS=' + ((Get-Content -LiteralPath (Join-Path $d 'leases.jsonl') | ForEach-Object { ($_ | ConvertFrom-Json).id }) -join ',')")
    batch.add(trim, lambda rc, out, _: check("a runtime history past its cap keeps its newest rows before the next one",
                                            rc == 0 and "ROWS=r1,r2" in out, f"rc={rc} {out!r}"))
    sys.path.insert(0, os.path.join(REPO, ".claude", "tools"))
    import lease_log
    bounds = f"CAP={lease_log.CAP_BYTES} KEEP={lease_log.KEEP_ROWS}"
    batch.add("'CAP=' + $script:LeaseLogCapBytes + ' KEEP=' + $script:LeaseLogKeepRows",
              lambda rc, out, _: check("the runtime history keeps lease_log's cap and row count", rc == 0 and bounds in out,
                                       f"want {bounds}: rc={rc} {out!r}"))
    batch.run(tmp, TOOL)
    wait_run_lock(tmp)
    machine_lane(tmp)
    broker_exit(tmp)


HOLDER = """param([string] $Name, [string] $Own, [string] $Backend, [string] $Tool)
. $Tool
$m = New-RunLock $Name -Backend $Backend
if ($Own -eq '1' -and -not $m.WaitOne(0)) { 'BUSY'; exit 3 }
'HELD'
[Console]::Out.Flush()
Start-Sleep -Seconds 300
"""


def hold(tmp, name, backend, own=True):
    """A child pwsh that owns the named mutex until it is killed (own=False: only keeps the named object open, as a
    waiting process does). Ownership is per process, so it cannot be planted."""
    script = os.path.join(tmp, "holder.ps1")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOLDER)
    child = subprocess.Popen(["pwsh", "-NoProfile", "-File", script, name, "1" if own else "0", backend, TOOL], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True, start_new_session=sys.platform != "win32")
    line = child.stdout.readline().strip()
    return child if line == "HELD" else None


def kill_holder(child):
    """Kill the holder and anything it started: a launcher that does not exec leaves the lock owner alive."""
    if sys.platform == "win32":
        child.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait()


def survivors():
    """Holder processes still running, for a failure message."""
    if sys.platform == "win32":
        return ""
    return subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout.count("holder.ps1")


def wait_run_lock(tmp):
    """Wait-RunLock over real cross-process holders, once per lock backend the host supports: a kernel mutex (Windows
    only; a Linux .NET named mutex is not released when its owner is killed while another process has the name open)
    and the file lock New-RunLock hands out on Linux."""
    for backend in (("mutex", "file") if sys.platform == "win32" else ("file",)):
        wait_run_lock_on(tmp, backend)


def wait_run_lock_on(tmp, backend):
    """The first free handle by index, a timeout, and an abandoned holder."""
    tag = uuid.uuid4().hex[:10]
    names = [f"Global\\harness-proof-{tag}-{n}" for n in "abc"]
    env = {"NAME_A": names[0], "NAME_B": names[1], "NAME_C": names[2], "BACKEND": backend}
    body = ("$h = @('NAME_A', 'NAME_B', 'NAME_C' | ForEach-Object { New-RunLock ([Environment]::GetEnvironmentVariable($_)) -Backend $env:BACKEND }); "
            "$sw = [Diagnostics.Stopwatch]::StartNew(); $r = Wait-RunLock -Handles $h -TimeoutMs %d; $sw.Stop(); "
            "'IDX=' + $r.Index; 'ABANDONED=' + $r.Abandoned; 'MS=' + $sw.ElapsedMilliseconds")
    holders = []
    try:
        holders = [hold(tmp, names[0], backend), hold(tmp, names[1], backend)]
        check(f"the holder children own their mutexes [{backend}]", all(holders), f"{holders!r}")
        rc, out, _ = drive(body % 3000, tmp, env)
        got = kv(out)
        check(f"Wait-RunLock grants the first free handle by index while others are held [{backend}]",
              rc == 0 and got.get("IDX") == "2" and got.get("ABANDONED") == "False", f"rc={rc} {out!r}")
        holders.append(hold(tmp, names[2], backend))
        holders.append(hold(tmp, names[0], backend, own=False))  # a waiter's open handle: it keeps the object alive past its owner
        rc, out, _ = drive(body % 700, tmp, env)
        got = kv(out)
        check(f"Wait-RunLock returns -1 after the timeout when every handle is held [{backend}]",
              rc == 0 and got.get("IDX") == "-1" and 600 <= int(got.get("MS", "0")) < 3000, f"rc={rc} {out!r}")
        held_ck = ("$script:LockWaitNoticeMs = 100; $l = Enter-RunLocks -Repo '%s' -CheckoutLockName $env:NAME_C "
                   "-RecordDir '%s' -TimeoutMs 500; ('R=' + $l.Acquired + ':' + $l.Blocker); Exit-RunLocks $l"
                   % (REPO, os.path.join(tmp, "records-ck-" + backend)))
        if backend == ("mutex" if sys.platform == "win32" else "file"):  # the backend Enter-RunLocks takes on this host
            rc, out, _ = drive(held_ck, tmp, {**env, "HARNESS_SLOT_BUDGET": "1"})
            check(f"a checkout-lock wait past the notice bound prints one line naming the checkout stage [{backend}]",
                  rc == 0 and "R=False:checkout" in out and out.count("LOCKWAIT stage=checkout") == 1, f"rc={rc} {out!r}")
        kill_holder(holders[0])
        rc, out, _ = drive(body % 3000, tmp, env)
        got = kv(out)
        check(f"a killed holder's mutex is granted as abandoned, at its index [{backend}]",
              rc == 0 and got.get("IDX") == "0" and got.get("ABANDONED") == "True", f"rc={rc} {out!r} holder.ps1 processes still running: {survivors()}")
    finally:
        for child in holders:
            if child is not None:
                kill_holder(child)


def kv(out):
    return dict(l.strip().split("=", 1) for l in out.splitlines() if "=" in l)


def isolation(tmp, batch):
    """The per-run user dir and its inline VSTest key, each platform from planted roots."""
    repo = os.path.join(tmp, "repo")
    local, xdg = os.path.join(tmp, "local"), os.path.join(tmp, "xdg")
    body = "$i = Get-TestIsolation -Repo '%s' -Platform %s -Tag abc; ('DIR=' + $i.AppData); ('ARG=' + $i.AppDataArg)"

    def windows(rc, out, _):
        lines = dict(l.split("=", 1) for l in out.splitlines() if l.startswith(("DIR=", "ARG=")))
        want = os.path.join(local, "{{PROJECT_NAME}}", "test-appdata")
        check("Windows isolation keeps the APPDATA key under LOCALAPPDATA",
              rc == 0 and lines.get("DIR", "").startswith(want) and lines.get("ARG", "").startswith("RunConfiguration.EnvironmentVariables.APPDATA="),
              f"rc={rc} {out!r}")
    batch.add(body % (repo, "windows"), windows, {"LOCALAPPDATA": local})

    def linux(rc, out, _):
        lines = dict(l.split("=", 1) for l in out.splitlines() if l.startswith(("DIR=", "ARG=")))
        arg = lines.get("ARG", "")
        check("Linux isolation takes the XDG_DATA_HOME key under XDG_DATA_HOME",
              rc == 0 and arg.startswith("RunConfiguration.EnvironmentVariables.XDG_DATA_HOME=") and "\\" not in arg
              and arg.split("=", 1)[1].startswith(xdg.replace("\\", "/")) and arg.endswith("/test-appdata/" + lines.get("DIR", "").replace("\\", "/").rsplit("/", 1)[-1]),
              f"rc={rc} {out!r}")
    batch.add(body % (repo, "linux"), linux, {"XDG_DATA_HOME": xdg})
    sys.path.insert(0, os.path.join(REPO, ".claude", "scripts"))
    import engine_userdata

    def equals_engine(platform, want):
        def verify(rc, out, _):
            got = kv(out)
            key = next(k for k in want if k != "USERDIR")
            check("the %s isolation key and engine user dir tail equal engine_userdata's" % platform,
                  rc == 0 and got.get("KEY") == key and got.get("USERDIR", "").endswith(want["USERDIR"][len(want[key]):]),
                  f"rc={rc} {out!r} {want!r}")
        return verify
    for platform, host in (("windows", "win32"), ("linux", "linux")):
        want = engine_userdata.isolation(repo, env={"LOCALAPPDATA": local}, platform=host)
        batch.add("$i = Get-TestIsolation -Repo '%s' -Platform %s -Tag abc; ('KEY=' + $i.Key); ('USERDIR=' + $i.UserDir)" % (repo, platform),
                  equals_engine(platform, want), {"LOCALAPPDATA": local, "XDG_DATA_HOME": xdg})
    temp = os.path.join(tmp, "t")

    def record_dir(rc, out, _):
        got = out.strip().split("R=", 1)[-1].replace("\\", "/")
        check("the slot record dir sits under the host's temp root",
              rc == 0 and got == os.path.join(tmp, "t", "harness-activity", "runtime-slots").replace("\\", "/"), f"rc={rc} {out!r}")
    batch.add("'R=' + (Get-RuntimeSlotRecordDir)", record_dir, {"TEMP": temp, "TMPDIR": temp})


def machine_lane(tmp):
    """A runtime lane takes a local-work lane from the common pool before its runtime slots, and spends a parent's loan."""
    sys.path.insert(0, os.path.join(REPO, ".claude", "tools"))
    import machine_slots
    pool = os.path.join(tmp, "pool")
    env = {"HARNESS_SLOT_DIR": pool, "HARNESS_MACHINE_SLOTS": "1", "HARNESS_SLOT_BUDGET": ""}
    body = ("$l = Enter-RunLocks -Repo '%s' -Runtime -SlotCount 2 -SlotNames @('mp-%s-a','mp-%s-b') "
            "-CheckoutLockName 'mp-%s-ck' -RecordDir '%s' -TimeoutMs 1500; ('R=' + $l.Acquired + ':' + $l.Blocker); Exit-RunLocks $l"
            % (REPO, os.getpid(), os.getpid(), os.getpid(), os.path.join(tmp, "records")))
    old = {name: os.environ.get(name) for name in env}
    os.environ.update({"HARNESS_SLOT_DIR": pool, "HARNESS_MACHINE_SLOTS": "1"})
    os.environ.pop("HARNESS_SLOT_BUDGET", None)
    held = machine_slots.take(True)
    # The lane gets what the checkout stage leaves of -TimeoutMs: a slow host spends most of a short budget there,
    # which leaves the lane too little to pass the notice bound.
    notice = "$script:LockWaitNoticeMs = 100; "
    try:
        rc, out, err = drive(notice + body, tmp, env)
        check("a runtime lane waits behind a full common pool and names it", rc == 0 and "R=False:machine-slots" in out, f"rc={rc} {out!r} {err[-200:]!r}")
        # A tailed log otherwise shows the last phase line (module load) for the whole lock queue.
        check("a lane wait past the notice bound prints one line naming the lane stage",
              out.count("LOCKWAIT stage=lane") == 1 and "stage=checkout" not in out, f"{out!r}")
        rc, out, err = drive(notice + body, tmp, {**env, "HARNESS_SLOT_BUDGET": "1"})
        check("a runtime lane under a parent's loan takes no second pool lane", rc == 0 and "R=True:" in out, f"rc={rc} {out!r} {err[-200:]!r}")
        check("a lease taken inside the notice bound prints no wait line", "LOCKWAIT" not in out, f"{out!r}")
    finally:
        machine_slots.release_all(held)
        for name, value in old.items():
            os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)
    lease_rows(tmp, pool)


def broker_exit(tmp):
    """A broker that dies with no output reports its exit code, so a killed broker and a crash read apart."""
    import re
    for code in (7, 143):
        script = os.path.join(tmp, "dead_broker_%d.py" % code)
        with open(script, "w", encoding="utf-8", newline="\n") as f:
            f.write("import sys\nsys.exit(%d)\n" % code)
        body = "try { Enter-MachineWorkLease -TimeoutMs 2000 | Out-Null; 'NOERR' } catch { 'MSG=' + $_.Exception.Message }"
        rc, out, err = drive(body, tmp, {"HARNESS_SLOT_SCRIPT": script, "HARNESS_SLOT_BUDGET": ""})
        check("a broker that exits %d with no output names that exit code" % code,
              rc == 0 and "broker failed" in out and re.search(r"(?<!\d)%d(?!\d)" % code, out) is not None,
              f"rc={rc} {out!r} {err[-200:]!r}")


def read_rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def lease_rows(tmp, pool):
    """machine_lane's two leases in the history: the one that gave up at the lane, and the loaned one, take to release."""
    rows = read_rows(os.path.join(tmp, "records", "leases.jsonl"))
    shape = [(r.get("event"), r.get("ended")) for r in rows]
    check("a lease that gives up at the lane writes one end row; a held lease writes take, then end",
          shape == [("end", "timeout:lane"), ("take", None), ("end", "released")], f"{shape!r}")
    if len(rows) == 3:
        gave_up, take, end = rows
        check("the held lease's rows share an id and name its checkout lock and slot",
              take["id"] == end["id"] and take["checkoutLock"] == "mp-%s-ck" % os.getpid() and take["got"]["slots"] == [1]
              and take["ask"]["runtime"] == "one" and take["ask"]["of"] == 2, f"{take!r} {end!r}")
        check("the lease that gave up records where its wait went", gave_up["wait"]["lane"] >= 0.2 and gave_up["wait"]["checkout"] >= 0,
              f"{gave_up!r}")
    lanes = read_rows(os.path.join(pool, "leases.jsonl"))
    check("the brokered lane that missed its deadline is in the pool history, named for the run it waited for",
          any(r.get("ended") == "deadline" and str(r.get("command", "")).startswith("RuntimeSlots") for r in lanes), f"{lanes!r}")


if __name__ == "__main__":
    run_proof(main, "RuntimeSlots-proof-")
