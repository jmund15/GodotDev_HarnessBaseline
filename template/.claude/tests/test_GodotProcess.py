"""Proof: GodotProcess.ps1 refuses a missing repo and resolves an existing one

Drives the tool through `pwsh -NoProfile` on planted input under its own temp root: one failure outcome
(a deny or a non-zero exit) and one success outcome. Nothing launches Godot, dotnet or a test run.
"""
import json
import os
import re
import sys

import _pwsh_drive
from _pwsh_drive import check, run_proof

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, ".claude", "scripts", "GodotProcess.ps1")


def drive(body, tmp, extra_env=None, tool=TOOL):
    return _pwsh_drive.drive(body, tmp, tool, extra_env)


def main(tmp):
    rc, _, err = drive("Resolve-TestRepo -Repo (Join-Path $env:PROOF_TMP 'missing') -ScriptsRoot $env:PROOF_TMP", tmp)
    check("a missing -Repo throws", rc != 0, f"rc={rc}")
    rc, out, _ = drive("'RESOLVED=' + (Resolve-TestRepo -Repo $env:PROOF_TMP -ScriptsRoot $env:PROOF_TMP)", tmp)
    check("an existing -Repo resolves to itself",
          rc == 0 and os.path.samefile(out.strip().split("RESOLVED=", 1)[-1], tmp), f"rc={rc} {out!r}")
    rc, out, _ = drive("'LABEL=' + (Get-SafeRunLabel -Label 'a b/c' -Repo $env:PROOF_TMP)", tmp)
    check("a label is made path-safe", rc == 0 and "LABEL=a_b_c" in out, f"rc={rc} {out!r}")
    # A split segment's remainder unit (AI) and its sub-units (AI.Steering, AI.Ports) all run under Integration.AI.
    os.makedirs(os.path.join(tmp, "Tests"), exist_ok=True)
    with open(os.path.join(tmp, "Tests", "integration_batch_durations.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write('{"units": {"AI": {"seconds": 57.5}, "AI.Steering": {"seconds": 107.0}, '
                '"AI.Ports": {"seconds": 42.1}, "Other": {"seconds": 10.0}}}')
    rc, out, _ = drive("'T=' + (Get-SuiteTimeoutMs -Repo $env:PROOF_TMP -Filter 'FullyQualifiedName~Tests.Integration.AI')"
                       " + ' H=' + (Get-HeadroomMs -ExpectedSec 206.6 -ExtraMs $script:SuiteDurationExtraMs)", tmp)
    timeout_ms, headroom_ms = (int(float(part.split("=", 1)[1])) for part in out.split()[:2])
    check("a split segment's timeout sums its sub-units",
          rc == 0 and timeout_ms >= headroom_ms, f"rc={rc} {out!r}")
    platform_roots(tmp)
    linux_proc(tmp)
    process_tree(tmp)
    launch_seams(tmp)
    start_stamp(tmp)


FIXTURE = os.path.join(os.path.dirname(__file__), "agent_fixtures", "linux_proc", "dotnet_test_capture.json")
ROOT = "/home/dev/work"


def kv(out):
    return dict(l.strip().split("=", 1) for l in out.splitlines() if "=" in l)


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def retime(stat, ppid=None, start=None):
    """Rewrite the parent and start-tick fields of a captured stat line (comm may hold spaces and parens)."""
    head, rest = stat.rsplit(")", 1)
    fields = rest.split()
    if ppid is not None:
        fields[1] = str(ppid)
    if start is not None:
        fields[19] = str(start)
    return head + ") " + " ".join(fields)


def plant_proc_root(tmp, name, fix, edits=None):
    """A /proc tree on disk: the captured stat and cmdline files, plus the entries a real /proc has that are not a process."""
    root = os.path.join(tmp, name)
    os.makedirs(os.path.join(root, "self"), exist_ok=True)
    os.makedirs(os.path.join(root, "9999"), exist_ok=True)  # a process that exited between listing and reading: no stat
    with open(os.path.join(root, "stat"), "w", encoding="utf-8", newline="\n") as f:
        f.write("cpu  1 2 3 4\nbtime %d\n" % fix["btime"])
    for pid, rec in fix["procs"].items():
        stat, cmdline = rec["stat"], rec["cmdline"]
        if edits and pid in edits:
            stat, cmdline = edits[pid](stat, cmdline)
        os.makedirs(os.path.join(root, pid), exist_ok=True)
        with open(os.path.join(root, pid, "stat"), "w", encoding="utf-8", newline="\n") as f:
            f.write(stat + "\n")
        with open(os.path.join(root, pid, "cmdline"), "wb") as f:
            f.write(cmdline.encode("utf-8"))
    return root.replace("\\", "/")


def linux_proc(tmp):
    """The Linux process table: snapshot members, test host and runner classification, root attribution and the orphan rule."""
    fix = load_fixture()
    clk = fix["clk_tck"]
    root = plant_proc_root(tmp, "procroot", fix)
    load = "$m = Get-LinuxProcSnapshot -ProcRoot '%s' -ClkTck %d; " % (root, clk)
    rc, out, _ = drive(load + "foreach ($id in ($m.Keys | Sort-Object)) { $p = $m[$id]; 'P|' + (@($p.ProcessId, $p.ParentProcessId, $p.Name, $p.CommandLine, "
                       "$p.ExecutablePath, [DateTimeOffset]::new($p.CreationDate).ToUnixTimeMilliseconds()) -join '|') }", tmp)
    rows = {int(l.split("|")[1]): l.split("|") for l in out.splitlines() if l.startswith("P|")}
    check("the snapshot holds the captured processes and skips self, a missing stat and non-process entries",
          rc == 0 and sorted(rows) == sorted(int(p) for p in fix["procs"]), f"rc={rc} {sorted(rows)} {out[-300:]!r}")
    th = rows.get(736, [])
    stat_736 = fix["procs"]["736"]["stat"].rsplit(")", 1)[1].split()
    check("a process reads its parent, comm, command line and local creation time",
          len(th) == 7 and th[2] == "700" and th[3] == "dotnet" and "testhost.dll --port" in th[4] and " --role client" in th[4]
          and int(th[6]) == round((fix["btime"] + int(stat_736[19]) / clk) * 1000), f"{th!r}")
    check("a comm cut to fifteen characters is kept as the kernel gives it", rows.get(1931, [None] * 4)[3] == "Godot_v4.7.1-st", f"{rows.get(1931)!r}")
    odd = retime(fix["procs"]["476"]["stat"]).replace("(sleep)", "(we ird) x)")
    odd_root = plant_proc_root(tmp, "procroot_odd", fix, {"476": lambda s, c: (odd, c)})
    rc, out, _ = drive("$m = Get-LinuxProcSnapshot -ProcRoot '%s' -ClkTck %d; 'N=' + $m[476].Name + '|' + $m[476].ParentProcessId" % (odd_root, clk), tmp)
    check("a comm holding spaces and a closing paren parses on the last paren", rc == 0 and f"N=we ird) x|{fix['reaper_pid']}" in out, f"rc={rc} {out!r}")

    classify = (load + "'TH=' + (((736, 700, 649, 1027) | ForEach-Object { [string](Test-IsTestHostProc $m[$_]) }) -join ',');"
                "'BUILD=' + (Test-IsTestHostProc ([pscustomobject]@{ Name = 'dotnet'; CommandLine = 'dotnet build Game.csproj' }));"
                "'ROLE=' + (((1931, 1027) | ForEach-Object { Get-GodotRole -Proc $m[$_] -Map $m }) -join ',');"
                "'MINE=' + (Get-ProcVerdict $m[1931] $m '%s' 3) + ' PEER=' + (Get-ProcVerdict $m[1931] $m '/home/dev/other' 3)" % ROOT)
    rc, out, _ = drive(classify, tmp)
    got = kv(out)
    check("the test host is the dotnet process running testhost.dll or vstest.console.dll, or dotnet test",
          rc == 0 and got.get("TH") == "True,True,True,False" and got.get("BUILD") == "False", f"rc={rc} {out!r}")
    check("a Godot under the test host is a TestRunner", rc == 0 and got.get("ROLE") == "TestRunner,TestRunner", f"rc={rc} {out!r}")
    check("a runner is MINE for the checkout its test host names and PEER for another",
          rc == 0 and got.get("MINE") == "MINE PEER=PEER", f"rc={rc} {out!r}")

    nest = "/.claude/worktrees/w1"

    def into_worktree(stat, cmdline):
        return stat, cmdline.replace(ROOT + "/", ROOT + nest + "/")
    nested_root = plant_proc_root(tmp, "procroot_nested", fix, {p: into_worktree for p in ("649", "700", "736", "1027", "1931")})
    rc, out, _ = drive("$m = Get-LinuxProcSnapshot -ProcRoot '%s' -ClkTck %d; "
                       "'OUTER=' + (Get-ProcVerdict $m[1931] $m '%s' 3) + ' INNER=' + (Get-ProcVerdict $m[1931] $m '%s%s' 3);"
                       "'CASE=' + (Test-UnderRoot '%s/x' '/home/dev/WORK')" % (nested_root, clk, ROOT, ROOT, nest, ROOT), tmp)
    got = kv(out)
    check("a process under <root>/.claude/worktrees/<x>/ in forward-slash form is a PEER of <root> and MINE for the worktree",
          rc == 0 and got.get("OUTER") == "PEER INNER=MINE", f"rc={rc} {out!r}")
    check("a Linux path compares with its case", rc == 0 and got.get("CASE") == "False", f"rc={rc} {out!r}")

    reaper = fix["reaper_pid"]
    gone = plant_proc_root(tmp, "procroot_reparented", fix, {
        "1931": lambda s, c: (retime(s, ppid=reaper, start=int(fix["procs"][str(reaper)]["stat"].rsplit(")", 1)[1].split()[19]) + 500), c)})
    reap = ("$m = Get-LinuxProcSnapshot -ProcRoot '%s' -ClkTck %d; "
            "'LIVE=' + ((Get-ReapableGodot -Checkout '%s' -Map $m -Platform linux | ForEach-Object { $_.ProcessId }) -join ',');"
            "'OWN=' + ((Get-ReapableGodot -Checkout '%s' -Map $m -OwnRootPid 649 -Platform linux | ForEach-Object { $_.ProcessId } | Sort-Object) -join ',')")
    rc, out, _ = drive(reap % (root, clk, ROOT, ROOT), tmp)
    got = kv(out)
    check("a runner whose test host is alive is spared, and reaped when it hangs under the root this run owns",
          rc == 0 and got.get("LIVE") == "" and got.get("OWN") == "1027,1931", f"rc={rc} {out!r}")
    rc, out, _ = drive(reap % (gone, clk, ROOT, ROOT), tmp)
    check("a runner reparented to a session relay is an orphan even though its new parent is alive",
          rc == 0 and kv(out).get("LIVE") == "1931", f"rc={rc} {out!r}")

    if sys.platform != "win32":
        rc, out, _ = drive("$m = Get-ProcSnapshot; $p = $m[$PID]; 'SELF=' + (@($p.ProcessId, $p.Name, $p.ExecutablePath, ($p.CommandLine -ne ''), "
                           "($p.CreationDate -le (Get-Date)), ($p.CreationDate -gt (Get-Date).AddDays(-30))) -join '|')", tmp)
        parts = (value(out, "SELF") or "").split("|")
        check("on a Linux host the live table has this process with all six members",
              rc == 0 and len(parts) == 6 and parts[1] and parts[2] and parts[3:] == ["True", "True", "True"], f"rc={rc} {out!r}")


def value(out, key):
    return out.strip().split(key + "=", 1)[-1] if key + "=" in out else None


def platform_roots(tmp):
    """The host seam: platform name, scratch root and data root, each on both platforms from planted environment."""
    host = "windows" if sys.platform == "win32" else "linux"
    rc, out, _ = drive("'P=' + (Get-HostPlatform)", tmp)
    check("Get-HostPlatform names the host", rc == 0 and value(out, "P") == host, f"rc={rc} {out!r}")
    rc, _, _ = drive("Get-HarnessTempRoot -Platform macos", tmp)
    check("a platform other than windows or linux is refused", rc != 0, f"rc={rc}")
    win_temp, lin_temp = os.path.join(tmp, "win-temp"), os.path.join(tmp, "lin-temp")
    rc, out, _ = drive("'R=' + (Get-HarnessTempRoot -Platform windows)", tmp, {"TEMP": win_temp})
    check("the Windows temp root is TEMP", rc == 0 and value(out, "R") == win_temp, f"rc={rc} {out!r}")
    rc, out, _ = drive("'R=' + (Get-HarnessTempRoot -Platform linux)", tmp, {"TMPDIR": lin_temp + "/"})
    check("the Linux temp root is TMPDIR without its trailing slash", rc == 0 and value(out, "R") == lin_temp, f"rc={rc} {out!r}")
    rc, out, _ = drive("'R=' + (Get-HarnessTempRoot -Platform linux)", tmp, {"TMPDIR": ""})
    check("the Linux temp root falls back to /tmp with no TMPDIR", rc == 0 and value(out, "R") == "/tmp", f"rc={rc} {out!r}")
    local = os.path.join(tmp, "local")
    rc, out, _ = drive("'R=' + (Get-HarnessDataRoot -Platform windows)", tmp, {"LOCALAPPDATA": local})
    check("the Windows data root is LOCALAPPDATA", rc == 0 and value(out, "R") == local, f"rc={rc} {out!r}")
    xdg, home = os.path.join(tmp, "xdg"), os.path.join(tmp, "home")
    rc, out, _ = drive("'R=' + (Get-HarnessDataRoot -Platform linux)", tmp, {"XDG_DATA_HOME": xdg, "HOME": home})
    check("the Linux data root is XDG_DATA_HOME when set", rc == 0 and value(out, "R") == xdg, f"rc={rc} {out!r}")
    rc, out, _ = drive("'R=' + (Get-HarnessDataRoot -Platform linux)", tmp, {"XDG_DATA_HOME": "", "HOME": home})
    check("the Linux data root falls back to HOME/.local/share",
          rc == 0 and value(out, "R") is not None and value(out, "R").replace("\\", "/") == home.replace("\\", "/") + "/.local/share",
          f"rc={rc} {out!r}")
    leaks = scratch_root_leaks()
    check("no script outside GodotProcess.ps1 reads $env:TEMP or $env:LOCALAPPDATA", not leaks, "; ".join(leaks[:6]))


def scratch_root_leaks():
    """Code lines (comments dropped) of the other .claude/scripts/*.ps1 that name a platform scratch root directly."""
    return code_leaks(r"\$env:(TEMP|LOCALAPPDATA)\b")


def code_leaks(pattern):
    scripts = os.path.join(REPO, ".claude", "scripts")
    leaks = []
    for name in sorted(os.listdir(scripts)):
        if not name.endswith(".ps1") or name == "GodotProcess.ps1":
            continue
        with open(os.path.join(scripts, name), encoding="utf-8") as f:
            text = re.sub(r"<#.*?#>", lambda m: "\n" * m.group(0).count("\n"), f.read(), flags=re.S)
        for number, line in enumerate(text.splitlines(), 1):
            code = re.sub(r"(^|\s)#.*$", "", line)
            if re.search(pattern, code, re.I):
                leaks.append(f"{name}:{number}")
    return leaks


RESPAWNER = """$mark = $env:PROOF_MARK
while ($true) {
    Start-Process pwsh -ArgumentList '-NoProfile', '-Command', "Start-Sleep 300 # $mark" -NoNewWindow `
        -RedirectStandardOutput "$env:PROOF_TMP/child.out" -RedirectStandardError "$env:PROOF_TMP/child.err"
    Start-Sleep -Milliseconds 150
}
"""

TREE_BODY = """
$root = Start-Process pwsh -ArgumentList '-NoProfile', '-File', $env:PROOF_RESPAWN -PassThru -NoNewWindow `
    -RedirectStandardOutput "$env:PROOF_TMP/root.out" -RedirectStandardError "$env:PROOF_TMP/root.err"
function Get-Marked { @((Get-ProcSnapshot).Values | Where-Object { $_.CommandLine -like "*$($env:PROOF_MARK)*" }) }
try {
    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Marked).Count -lt 3 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    'BEFORE=' + (Get-Marked).Count
    $out = @(Stop-ProcessTree -ProcessId $root.Id)
    [void] $root.WaitForExit(5000)
    Start-Sleep -Seconds 1
    'EMITTED=' + $out.Count
    'AFTER=' + (Get-Marked).Count
    'ROOT_EXITED=' + $root.HasExited
    $none = @(Stop-ProcessTree -ProcessId 2147483000)
    'MISSING=' + $none.Count
}
finally {
    Stop-Process -Id $root.Id -Force -ErrorAction SilentlyContinue
    foreach ($m in (Get-Marked)) { Stop-Process -Id $m.ProcessId -Force -ErrorAction SilentlyContinue }
}
"""


def start_stamp(tmp):
    """A process's identity stamp: the kernel's start tick on Linux, which no clock adjustment moves, and StartTime on Windows."""
    fix = load_fixture()
    clk = fix["clk_tck"]
    root = plant_proc_root(tmp, "procroot_stamp", fix)
    ticks = int(fix["procs"]["736"]["stat"].rsplit(")", 1)[1].split()[19])
    rc, out, _ = drive("$s = Get-ProcessStartStamp -ProcessId 736 -Platform linux -ProcRoot '%s' -ClkTck %d; "
                       "'KIND=' + $s.Kind; 'MS=' + [DateTimeOffset]::new($s).ToUnixTimeMilliseconds()" % (root, clk), tmp)
    got = kv(out)
    check("on Linux the stamp is the start tick since boot as a UTC time, whatever the wall clock says",
          rc == 0 and got.get("KIND") == "Utc" and got.get("MS") == str(ticks * 1000 // clk), f"rc={rc} {out!r}")
    rc, out, _ = drive("$p = Get-Process -Id $PID; $s = Get-ProcessStartStamp -ProcessId $PID -Platform windows; "
                       "'SAME=' + ($s -eq $p.StartTime.ToUniversalTime())", tmp)
    check("on Windows the stamp is the process StartTime in UTC", rc == 0 and kv(out).get("SAME") == "True", f"rc={rc} {out!r}")
    rc, out, _ = drive("$s = Get-ProcessStartStamp -ProcessId 2147483000 -Platform linux -ProcRoot '%s' -ClkTck %d; 'NULL=' + ($null -eq $s)" % (root, clk), tmp)
    check("a process that is gone has no stamp", rc == 0 and kv(out).get("NULL") == "True", f"rc={rc} {out!r}")
    uses = code_leaks(r"\.StartTime\b")
    check("no script outside GodotProcess.ps1 reads a process StartTime as its identity", not uses, "; ".join(uses[:12]))


def launch_seams(tmp):
    """What a Linux host launches differently: the virtual display wrapper, the shell, and the interactive-session probes."""
    wrap = ("$w = Get-HeadlessDisplayArgv -FilePath 'dotnet' -Arguments @('test', '--filter', 'X') -Platform %s -XvfbRun '%s'; "
            "'FILE=' + $w.FilePath; 'ARGS=' + ($w.Arguments -join ' ')")
    rc, out, _ = drive(wrap % ("linux", "/usr/bin/xvfb-run"), tmp)
    got = kv(out)
    check("on Linux the runtime launch goes through xvfb-run with a free server number",
          rc == 0 and got.get("FILE") == "/usr/bin/xvfb-run" and got.get("ARGS") == "--auto-servernum dotnet test --filter X", f"rc={rc} {out!r}")
    rc, out, _ = drive(wrap % ("linux", ""), tmp)
    got = kv(out)
    check("on Linux with no xvfb-run the launch is unchanged", rc == 0 and got.get("FILE") == "dotnet" and got.get("ARGS") == "test --filter X", f"rc={rc} {out!r}")
    rc, out, _ = drive(wrap % ("windows", "/usr/bin/xvfb-run"), tmp)
    got = kv(out)
    check("on Windows the launch is unchanged", rc == 0 and got.get("FILE") == "dotnet" and got.get("ARGS") == "test --filter X", f"rc={rc} {out!r}")
    with open(os.path.join(REPO, ".claude", "scripts", "run_test_suite.ps1"), encoding="utf-8") as f:
        suite = f.read()
    check("run_test_suite.ps1 asks for the display on a runtime launch, and only on one",
          re.search(r"Start-IsolatedNativeProcess -FilePath 'dotnet'[^\n]*-Display:\(-not \$NoGodotRuntime\)", suite) is not None,
          "the dotnet test launch does not pass -Display:(-not $NoGodotRuntime)")
    if sys.platform != "win32":
        fake = os.path.join(tmp, "fakebin")
        os.makedirs(fake, exist_ok=True)
        with open(os.path.join(fake, "xvfb-run"), "w", encoding="utf-8", newline="\n") as f:
            f.write('#!/bin/sh\necho "XVFB $*" >> "$PROOF_TMP/xvfb.log"\nshift\nexec "$@"\n')
        os.chmod(os.path.join(fake, "xvfb-run"), 0o755)
        launch = ("$p = Start-IsolatedNativeProcess -FilePath 'echo' -Arguments @('hello world') -OutPath '%s/o.txt' -ErrPath '%s/e.txt' %s; "
                  "$null = $p.Handle; [void] $p.WaitForExit(20000); 'EXIT=' + $p.ExitCode")
        path_env = {"PATH": fake + os.pathsep + os.environ.get("PATH", "")}
        rc, out, _ = drive(launch % (tmp.replace("\\", "/"), tmp.replace("\\", "/"), "-Display"), tmp, path_env)
        log = open(os.path.join(tmp, "xvfb.log"), encoding="utf-8").read() if os.path.exists(os.path.join(tmp, "xvfb.log")) else ""
        shown = open(os.path.join(tmp, "o.txt"), encoding="utf-8").read().strip() if os.path.exists(os.path.join(tmp, "o.txt")) else ""
        check("-Display runs the command under xvfb-run and keeps its output and exit code",
              rc == 0 and "XVFB --auto-servernum echo hello world" in log and shown == "hello world" and kv(out).get("EXIT") == "0",
              f"rc={rc} log={log!r} out={shown!r} {out!r}")
        os.remove(os.path.join(tmp, "xvfb.log"))
        rc, out, _ = drive(launch % (tmp.replace("\\", "/"), tmp.replace("\\", "/"), ""), tmp, path_env)
        check("without -Display the command runs bare", rc == 0 and not os.path.exists(os.path.join(tmp, "xvfb.log")) and kv(out).get("EXIT") == "0",
              f"rc={rc} {out!r}")
        rc, out, _ = drive("'BASH=' + (Get-GitBash); 'LOCKED=' + (Test-InteractiveSessionLocked); 'WINDOW=' + (Test-HasMainWindow -ProcessId $PID)", tmp)
        got = kv(out)
        check("on a Linux host the shell is bash, the session is never locked and no process has a main window",
              rc == 0 and os.path.basename(got.get("BASH", "")) == "bash" and os.path.exists(got.get("BASH", "")) and got.get("LOCKED") == "False"
              and got.get("WINDOW") == "False", f"rc={rc} {out!r}")


def process_tree(tmp):
    """Stop-ProcessTree on a live respawning parent: nothing survives, nothing reaches the success stream, a missing pid is quiet."""
    respawn = os.path.join(tmp, "respawn.ps1")
    with open(respawn, "w", encoding="utf-8", newline="\n") as f:
        f.write(RESPAWNER)
    mark = "harness-s3-" + os.path.basename(tmp).replace(" ", "_")
    rc, out, _ = drive(TREE_BODY, tmp, {"PROOF_MARK": mark, "PROOF_RESPAWN": respawn})
    got = kv(out)
    check("the respawning tree is up before the stop", rc == 0 and int(got.get("BEFORE", "0")) >= 3, f"rc={rc} {out!r}")
    check("Stop-ProcessTree leaves no descendant, kills the root and emits nothing",
          rc == 0 and got.get("AFTER") == "0" and got.get("ROOT_EXITED") == "True" and got.get("EMITTED") == "0", f"rc={rc} {out!r}")
    check("a pid that is gone stops quietly", rc == 0 and got.get("MISSING") == "0", f"rc={rc} {out!r}")
    if sys.platform != "win32":
        fake = os.path.join(tmp, "killbin")
        os.makedirs(fake, exist_ok=True)
        log = os.path.join(tmp, "kill.log")
        with open(os.path.join(fake, "kill"), "w", encoding="utf-8", newline="\n") as f:
            f.write('#!/bin/sh\necho "$*" >> "$PROOF_TMP/kill.log"\n[ "$1" = "-KILL" ] && exit 1\nexit 0\n')
        os.chmod(os.path.join(fake, "kill"), 0o755)
        drive("Stop-ProcessTree -ProcessId 2147483000 -Platform linux", tmp, {"PATH": fake + os.pathsep + os.environ.get("PATH", "")})
        calls = [line.strip() for line in open(log, encoding="utf-8").read().splitlines()] if os.path.exists(log) else []
        check("on Linux a process whose kill failed is resumed, not left stopped",
              "-KILL 2147483000" in calls and "-CONT 2147483000" in calls, f"{calls!r}")
    kills = code_leaks(r"\btaskkill\b")
    check("no script outside GodotProcess.ps1 calls taskkill", not kills, "; ".join(kills[:12]))


if __name__ == "__main__":
    run_proof(main, "GodotProcess-proof-")
