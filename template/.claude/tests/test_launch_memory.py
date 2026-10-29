#!/usr/bin/env python3
"""Proof for the launch-memory admission guard (tools/launch_memory.py + its two wiring seams).

Cases 1-5 drive the Python owner directly with an injected reader/sleep/log, so a low reading is
planted rather than waited for (`wait`, the benchmark grid's form). Case 5b drives `check`, the
sidecar form: one sample, exit 3 with a REFUSED line when either floor is short. Case 6 plants a low
floor into the real sidecar watcher (`sc_run_watched`) and asserts it refuses with exit 12 before
the child starts. Case 7 is the counted-engagement control: with the floors off the child must
start, which proves the fixture reaches the guarded path. Case 8 uses the fixture launcher through
sc_detach_launch for the detached memory path, without credentials or network. The real codex
launcher proves foreground memory refusal and synchronous detached preflight refusal under a
no-login HOME; watch_records wakes on the memory path's `<record>.exit`. Detached cases prove
visible WAIT readings, recovery, an opt-in -T bound ending at 12, and one sample when already clear.
Closing stdout after the first detached receipt line must leave .exit unpublished until the stub job
writes its own record and finishes.

Every case must produce output; a zero-match run is a failure, never a pass.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _platform_skip

if sys.platform != "win32":
    _platform_skip.skip_proof("launch-memory admission", "sampling reads Windows commit memory and its seams need Git Bash")

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
TOOL = CLAUDE / "tools" / "launch_memory.py"
LIB = CLAUDE / "scripts" / "lib" / "sidecar_common.sh"

fail = 0
ran = 0


def ok(label):
    global ran
    ran += 1
    print("OK   %s" % label)


def bad(label, why=""):
    global ran, fail
    ran += 1
    fail = 1
    print("FAIL %s%s" % (label, (": " + why) if why else ""))


def load():
    """The module under test, or None with the reason printed."""
    if not TOOL.is_file():
        print("     (%s does not exist)" % TOOL)
        return None
    spec = importlib.util.spec_from_file_location("launch_memory", TOOL)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        print("     (import failed: %r)" % (exc,))
        return None
    return module


def drive(mod, readings, floors, label="cell"):
    """Run wait_for_headroom over a scripted reader; return (reads, sleeps, lines)."""
    state = {"n": 0}
    sleeps, lines = [], []

    def read():
        state["n"] += 1
        value = readings[min(state["n"] - 1, len(readings) - 1)]
        if isinstance(value, Exception):
            raise value
        return value

    mod.wait_for_headroom(read, sleeps.append, lines.append, floors, label)
    return state["n"], sleeps, lines


# ---- 1. sampled wait: repeated low readings, then a clear one
mod = load()
if mod is None:
    bad("sampled wait", "tools/launch_memory.py missing or unimportable")
else:
    reads, sleeps, lines = drive(mod, [(5.0, 3.0), (6.0, 4.0), (14.0, 9.0)],
                                 mod.Floors(12.0, 8.0, 20.0), "cell t4")
    if reads == 3:
        ok("sampled wait re-reads until both floors clear (3 reads)")
    else:
        bad("sampled wait read count", "got %d reads, want 3" % reads)
    if sleeps == [20.0, 20.0]:
        ok("sampled wait sleeps one poll interval per low reading")
    else:
        bad("sampled wait sleeps", "got %r, want [20.0, 20.0]" % (sleeps,))
    if len(lines) == 2:
        ok("sampled wait logs exactly WAIT then READY")
    else:
        bad("sampled wait log line count", "got %d: %r" % (len(lines), lines))
    if lines and lines[0].startswith("WAIT ") and "12.00" in lines[0] and "8.00" in lines[0]:
        ok("WAIT line names both floors")
    else:
        bad("WAIT line", repr(lines[:1]))
    if len(lines) > 1 and lines[1].startswith("READY ") and "14.00" in lines[1]:
        ok("READY line names the clearing reading")
    else:
        bad("READY line", repr(lines[1:2]))

    # ---- 1b. a healthy first sample is silent (this runs per grid cell)
    reads, sleeps, lines = drive(mod, [(100.0, 50.0)], mod.Floors(12.0, 8.0, 20.0))
    if reads == 1 and sleeps == [] and lines == []:
        ok("a clear first sample reads once, sleeps never and logs nothing")
    else:
        bad("clear first sample", "reads=%d sleeps=%r lines=%r" % (reads, sleeps, lines))

    # ---- 2. fail-open on an unreadable sample
    reads, sleeps, lines = drive(mod, [RuntimeError("planted unreadable sample")],
                                 mod.Floors(12.0, 8.0, 20.0))
    if len(lines) == 1 and lines[0].startswith("WARNING ") and "planted unreadable sample" in lines[0]:
        ok("an unreadable sample warns once, naming the cause")
    else:
        bad("fail-open warning", "%r" % (lines,))
    if sleeps == []:
        ok("an unreadable sample does not sleep")
    else:
        bad("fail-open sleeps", "%r" % (sleeps,))
    if reads == 1:
        ok("an unreadable sample returns instead of raising")
    else:
        bad("fail-open read count", "got %d" % reads)

    # ---- 4. a zero floor disables its metric (no invented headroom)
    reads, sleeps, lines = drive(mod, [(0.1, 0.1)], mod.Floors(0.0, 0.0, 1.0))
    if reads == 1 and sleeps == [] and lines == []:
        ok("zero floors disable both metrics (no wait, no log)")
    else:
        bad("zero floors", "reads=%d sleeps=%r lines=%r" % (reads, sleeps, lines))

    # ---- 4b. one disabled metric still admits the other
    reads, sleeps, lines = drive(mod, [(0.1, 99.0)], mod.Floors(0.0, 8.0, 1.0))
    if reads == 1 and lines == []:
        ok("a disabled commit floor still enforces the physical floor")
    else:
        bad("disabled commit floor with a high physical reading", "reads=%d lines=%r" % (reads, lines))

# ---- 2b. a held launch names its cause and never goes silent
if mod is not None:
    holders_calls = []

    def holders():
        holders_calls.append(1)
        return "llama-server 22.8 GiB, claude x24 12.3 GiB"

    lows = [(1.7, 2.6)] * 40 + [(14.0, 9.0)]
    state = {"n": 0}
    sleeps, lines = [], []

    def read():
        state["n"] += 1
        return lows[min(state["n"] - 1, len(lows) - 1)]

    try:
        mod.wait_for_headroom(read, sleeps.append, lines.append, mod.Floors(12.0, 8.0, 20.0), "plans",
                              read_holders=holders)
    except TypeError as exc:
        bad("a held launch names its holders", "no read_holders seam: %s" % exc)
    else:
        waits = [l for l in lines if l.startswith("WAIT") or l.startswith("STILL WAITING")]
        if waits and "llama-server 22.8 GiB" in waits[0]:
            ok("the WAIT line names the top memory holders")
        else:
            bad("the WAIT line names the top memory holders", lines)
        # 40 polls x 20 s = 800 s: one re-emit past the 10-minute mark, not one per poll.
        still = [l for l in lines if l.startswith("STILL WAITING")]
        if len(still) == 1 and "min" in still[0]:
            ok("a wait past 10 minutes re-emits once with its elapsed time")
        else:
            bad("a wait past 10 minutes re-emits once with its elapsed time", lines)
        if len(holders_calls) == 2:
            ok("holders are read only when a line is emitted")
        else:
            bad("holders are read only when a line is emitted", len(holders_calls))

    def broken_holders():
        raise OSError("no process table")

    sleeps, lines = [], []
    mod.wait_for_headroom(iter([(1.0, 1.0), (14.0, 9.0)]).__next__, sleeps.append, lines.append,
                          mod.Floors(12.0, 8.0, 20.0), "plans", read_holders=broken_holders) \
        if "read_holders" in mod.wait_for_headroom.__code__.co_varnames else None
    if lines and lines[0].startswith("WAIT") and "READY" in lines[-1]:
        ok("an unreadable holder list still waits and launches")
    else:
        bad("an unreadable holder list still waits and launches", lines)

# ---- 3. a broken override is a named configuration error, never a fake reading
if mod is None:
    bad("invalid override", "tools/launch_memory.py missing or unimportable")
else:
    bad_overrides = [("LAUNCH_MIN_COMMIT_GIB", "abc"), ("LAUNCH_MEMORY_POLL_SEC", "0"),
                     ("LAUNCH_MIN_COMMIT_GIB", "-1"), ("LAUNCH_MEMORY_POLL_SEC", "nan"),
                     ("LAUNCH_MIN_PHYSICAL_GIB", "inf")]
    saved = {k: os.environ.get(k) for k in
             ("LAUNCH_MIN_COMMIT_GIB", "LAUNCH_MIN_PHYSICAL_GIB", "LAUNCH_MEMORY_POLL_SEC")}
    for var, value in bad_overrides:
        for k, v in saved.items():
            os.environ.pop(k, None)
        os.environ[var] = value
        try:
            mod.floors_from_env()
        except mod.ConfigError as exc:
            if var in str(exc):
                ok("invalid override %s=%s names its variable" % (var, value))
            else:
                bad("invalid override %s=%s" % (var, value), "message does not name the variable: %s" % exc)
        except Exception as exc:
            bad("invalid override %s=%s" % (var, value), "raised %r instead of ConfigError" % (exc,))
        else:
            bad("invalid override %s=%s" % (var, value), "accepted as configuration")
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # and the defaults are the plan's
    for k in saved:
        os.environ.pop(k, None)
    floors = mod.floors_from_env()
    if (floors.commit_gib, floors.physical_gib, floors.poll_sec) == (12.0, 6.0, 20.0):
        ok("defaults are 12 GiB commit, 6 GiB physical, 20s poll")
    else:
        bad("defaults", "%r" % (tuple(floors),))

# ---- 5. production smoke: the real Windows binding, not a stub
if mod is None:
    bad("production read_snapshot", "tools/launch_memory.py missing or unimportable")
elif sys.platform != "win32" or sys.maxsize <= 2 ** 32:
    bad("production read_snapshot", "this runtime is not 64-bit Windows (platform=%s)" % sys.platform)
else:
    try:
        commit, physical = mod.read_snapshot()
    except Exception as exc:
        bad("production read_snapshot", "raised %r" % (exc,))
    else:
        if commit > 0 and physical > 0:
            ok("production read_snapshot samples positive commit (%.2f GiB) and physical (%.2f GiB)"
               % (commit, physical))
        else:
            bad("production read_snapshot", "non-positive readings %.3f / %.3f" % (commit, physical))

# ---- 5b. `check`: one sample, refuse at once (the sidecar form)
def run_check(mod, reading, floors, label="seat-a", holders=lambda: "llama-server 22.8 GiB"):
    """Drive check_headroom over one planted reading; return (code, lines, reads)."""
    state = {"n": 0}
    lines = []

    def read():
        state["n"] += 1
        if isinstance(reading, Exception):
            raise reading
        return reading

    code = mod.check_headroom(read, lines.append, floors, label, read_holders=holders)
    return code, lines, state["n"]


if mod is None or not hasattr(mod, "check_headroom"):
    for label in ("check refuses a low reading with exit 3", "check admits a high reading with exit 0",
                  "check warns and admits on an unreadable sample"):
        bad(label, "launch_memory.check_headroom is missing")
else:
    code, lines, reads = run_check(mod, (1.7, 2.6), mod.Floors(12.0, 8.0, 20.0))
    want = ("REFUSED memory headroom for seat-a: commit 1.70 GiB free, physical 2.60 GiB free; "
            "floors commit 12.00 GiB, physical 8.00 GiB; top holders llama-server 22.8 GiB")
    if code == 3 and lines == [want] and reads == 1:
        ok("check refuses a low reading with exit 3 and one REFUSED line naming both floors and holders")
    else:
        bad("check refuses a low reading with exit 3", "code=%r reads=%r lines=%r" % (code, reads, lines))
    code, lines, reads = run_check(mod, (1.7, 99.0), mod.Floors(12.0, 8.0, 20.0))
    if code == 3 and lines and lines[0].startswith("REFUSED "):
        ok("check refuses when only the commit floor is short")
    else:
        bad("check refuses when only the commit floor is short", "code=%r lines=%r" % (code, lines))
    code, lines, reads = run_check(mod, (100.0, 50.0), mod.Floors(12.0, 8.0, 20.0))
    if code == 0 and lines == [] and reads == 1:
        ok("check admits a high reading with exit 0, silently, after one sample")
    else:
        bad("check admits a high reading with exit 0", "code=%r reads=%r lines=%r" % (code, reads, lines))
    code, lines, reads = run_check(mod, RuntimeError("planted unreadable sample"), mod.Floors(12.0, 8.0, 20.0))
    if code == 0 and len(lines) == 1 and lines[0].startswith("WARNING ") and "planted unreadable sample" in lines[0]:
        ok("check warns once and admits on an unreadable sample")
    else:
        bad("check warns and admits on an unreadable sample", "code=%r lines=%r" % (code, lines))

    def broken_list():
        raise OSError("no process table")
    code, lines, reads = run_check(mod, (1.0, 1.0), mod.Floors(12.0, 8.0, 20.0), holders=broken_list)
    if code == 3 and lines and lines[0].startswith("REFUSED ") and "top holders" not in lines[0]:
        ok("check still refuses when the holder list is unreadable")
    else:
        bad("check still refuses when the holder list is unreadable", "code=%r lines=%r" % (code, lines))


def cli(args, env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("LAUNCH_MIN_COMMIT_GIB", "LAUNCH_MIN_PHYSICAL_GIB", "LAUNCH_MEMORY_POLL_SEC")}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(TOOL)] + args, capture_output=True, text=True,
                          timeout=120, env=env)


proc = cli(["check", "--label", "cli-seat"], {"LAUNCH_MIN_COMMIT_GIB": "abc"})
if proc.returncode == 2 and "LAUNCH_MIN_COMMIT_GIB" in proc.stderr:
    ok("check exits 2 on a broken override, naming the variable")
else:
    bad("check exits 2 on a broken override", "rc=%r err=%r" % (proc.returncode, proc.stderr[-300:]))
proc = cli(["check", "--label", "cli-seat"],
           {"LAUNCH_MIN_COMMIT_GIB": "1000000", "LAUNCH_MIN_PHYSICAL_GIB": "1000000"})
if proc.returncode == 3 and "REFUSED memory headroom for cli-seat:" in proc.stderr:
    ok("check CLI exits 3 with the REFUSED line under an unreachable floor")
else:
    bad("check CLI exits 3 under an unreachable floor", "rc=%r err=%r" % (proc.returncode, proc.stderr[-300:]))
proc = cli(["check", "--label", "cli-seat"], {"LAUNCH_MIN_COMMIT_GIB": "0", "LAUNCH_MIN_PHYSICAL_GIB": "0"})
if proc.returncode == 0 and proc.stderr.strip() == "":
    ok("check CLI exits 0 silently with the floors off")
else:
    bad("check CLI exits 0 with the floors off", "rc=%r err=%r" % (proc.returncode, proc.stderr[-300:]))

# ---- 6/7. the sidecar seam: a launch below the floor refuses before the child starts
DRIVER = r'''
set -uo pipefail
. "$LIB"
SC_FORMAT=json
SC_LABEL=seam
[ -n "${PRECHECKED:-}" ] && SC_MEMORY_CHECKED=1
stub_child() {
  printf 'CHILD-STARTED\n' >> "$MARK"
  printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
}
sc_run_watched stub_child ""
printf 'DONE rc=%s\n' "$rc"
sc_run_watched stub_child ""
printf 'DONE rc=%s\n' "$rc"
'''


def posix(path):
    """A Git Bash path: bash cannot open a native `C:\\...` argument."""
    return str(path).replace("\\", "/")


def git_bash():
    """The MSYS bash this repo's scripts run under.

    A bare `bash` from a native Windows Python resolves through the Windows PATH to
    `C:\\WINDOWS\\system32\\bash.exe`, which is WSL — it reports `/bin/bash: C:/...: No such file
    or directory` for a path that exists. Identify the candidate by `uname -s` instead.
    """
    candidates = [os.environ.get("SIDECAR_PROOF_BASH", ""),
                  os.path.join(os.environ.get("EXEPATH", ""), "bash.exe"),
                  "C:/Program Files/Git/bin/bash.exe",
                  shutil.which("bash") or ""]
    for cand in candidates:
        if not cand or not Path(cand).is_file():
            continue
        try:
            out = subprocess.run([cand, "-c", "uname -s"], capture_output=True, text=True, timeout=20)
        except OSError:
            continue
        if out.returncode == 0 and ("MINGW" in out.stdout or "MSYS" in out.stdout or "CYGWIN" in out.stdout):
            return cand
    return None


BASH = git_bash()


def run_seam(env_extra, timeout=60):
    """Run the driver on a temp file. Returns (mark-text-or-None, stderr, stdout, killed, rc).

    A driver still running at `timeout` (a launcher that waits instead of refusing) has its whole
    tree killed, so a held `launch_memory.py wait` never outlives the proof.
    """
    tmp = Path(tempfile.mkdtemp(prefix="lm_seam_"))
    mark = tmp / "mark.txt"
    driver = tmp / "driver.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SC_") and k != "SIDECAR_DETACHED"}
    env.update({"LIB": posix(LIB), "MARK": posix(mark), "PYTHONIOENCODING": "utf-8"})
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    env.update(env_extra)
    proc = subprocess.Popen([BASH, posix(driver)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    killed = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        killed = True
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        proc.kill()
        out, err = proc.communicate()
    return (mark.read_text(encoding="utf-8") if mark.is_file() else None), err, out, killed, proc.returncode


def fixture_ran(err, out):
    """A driver bash could not even open is a fixture failure, never a passed hold."""
    return "No such file or directory" not in err or "DONE rc=" in out


if not LIB.is_file():
    bad("sidecar seam", "lib missing at %s" % LIB)
elif BASH is None:
    bad("sidecar seam", "no MSYS/MINGW bash found; set SIDECAR_PROOF_BASH to the Git Bash executable")
else:
    # A launch that waits instead of refusing is killed at run_seam's timeout and fails on rc.
    mark_text, err, out, killed, rc = run_seam(
        {"LAUNCH_MIN_COMMIT_GIB": "1000000", "LAUNCH_MIN_PHYSICAL_GIB": "1000000",
         "LAUNCH_MEMORY_POLL_SEC": "1"})
    if not fixture_ran(err, out):
        bad("refused sidecar fixture", "bash never opened the driver: %r" % (err[-300:],))
    elif mark_text is not None:
        bad("refused sidecar child never starts", "the child ran while every floor was far above the sample")
    else:
        ok("refused sidecar child never starts")
    if rc == 12 and "DONE rc=" not in out:
        ok("a launch below the floor exits 12 at once instead of waiting")
    else:
        bad("a launch below the floor exits 12 at once", "rc=%r out=%r" % (rc, out[-200:]))
    if "REFUSED memory headroom for seam:" in err and "WAIT memory headroom" not in err:
        ok("the refused run prints the REFUSED line on its stderr, never a WAIT")
    else:
        bad("refused run REFUSED line", "stderr was %r" % (err[-400:],))

    mark_text, err, out, killed, rc = run_seam(
        {"LAUNCH_MIN_COMMIT_GIB": "1000000", "LAUNCH_MIN_PHYSICAL_GIB": "1000000", "PRECHECKED": "1"})
    if mark_text and mark_text.count("CHILD-STARTED") == 2 and "REFUSED" not in err:
        ok("a launch already admitted is not sampled again (the codex launcher checks before its proxy)")
    else:
        bad("once per launch", "mark=%r rc=%r err=%r" % (mark_text, rc, err[-300:]))

    mark_text, err, out, killed, rc = run_seam(
        {"LAUNCH_MIN_COMMIT_GIB": "0", "LAUNCH_MIN_PHYSICAL_GIB": "0", "LAUNCH_MEMORY_POLL_SEC": "1"})
    if mark_text and mark_text.count("CHILD-STARTED") == 2:
        ok("counted engagement: with the floors off the fixture child starts on every run")
    else:
        bad("counted engagement", "the child never started, so the refusal case proves nothing")
    if "WAIT memory headroom" not in err and "REFUSED" not in err:
        ok("a clear sample logs no WAIT and no REFUSED")
    else:
        bad("clear sample", "stderr has a WAIT line: %r" % (err[-400:],))
    if "DONE rc=" in out:
        ok("the watcher returns normally on the allowed path")
    else:
        bad("watcher return", "stdout was %r" % (out[-300:],))

# ---- 8. the real codex launcher: refusal before any proxy, foreground and detached
CODEX = CLAUDE / "scripts" / "codex_proxy_sidecar.sh"


def codex_env(tmp):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SC_") and k not in ("SIDECAR_DETACHED", "SIDECAR_LAUNCH_ID")}
    env["HARNESS_SIDECAR_PREFLIGHT"] = "1"
    env.update({"LAUNCH_MIN_COMMIT_GIB": "1000000", "LAUNCH_MIN_PHYSICAL_GIB": "1000000",
                "PYTHONIOENCODING": "utf-8",
                # Safety floor for a launcher that skips the memory check: no proxy login under this
                # HOME, so it stops at its credential gate before any capacity read or proxy start.
                "HOME": posix(tmp), "CLAUDE_BIN": posix(tmp / "no-claude.exe"),
                "SIDECAR_EXHAUSTED_DIR": posix(tmp / "exhausted")})
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    return env


if BASH is None or not CODEX.is_file():
    bad("codex launcher refusal", "no MSYS bash or no launcher at %s" % CODEX)
else:
    tmp = Path(tempfile.mkdtemp(prefix="lm_codex_"))
    proc = subprocess.run([BASH, posix(CODEX), "-l", "mem-fg", "--", "hello"],
                          capture_output=True, text=True, timeout=180, env=codex_env(tmp))
    if proc.returncode == 12 and "REFUSED memory headroom for mem-fg:" in proc.stderr:
        ok("a codex launch below the floor exits 12 with the REFUSED line")
    else:
        bad("codex launch below the floor exits 12", "rc=%r err=%r" % (proc.returncode, proc.stderr[-500:]))
    if "starting claude-code-proxy" not in proc.stderr:
        ok("the refused codex launch starts no proxy")
    else:
        bad("the refused codex launch starts no proxy", proc.stderr[-400:])

    denied = tmp / "no-login.record.json"
    proc = subprocess.run([BASH, posix(CODEX), "-A", "-l", "no-login", "-X", "-R", posix(denied), "--", "hello"],
                          capture_output=True, text=True, timeout=60, env=codex_env(tmp))
    if proc.returncode == 2 and "REFUSED at preflight (codex_proxy_sidecar.sh --check exit 3)" in proc.stderr:
        ok("detached no-login launch refuses synchronously at preflight, even with -A")
    else:
        bad("detached no-login preflight refusal", "rc=%r err=%r" % (proc.returncode, proc.stderr[-600:]))
    if ("DETACHED pid=" not in proc.stdout and not Path(str(denied) + ".out").exists()
            and "starting claude-code-proxy" not in proc.stderr
            and len(Path(str(denied) + ".pid").read_text().splitlines()) == 1):
        ok("no-login preflight starts neither detached job nor proxy")
    else:
        bad("no-login preflight starts no child", proc.stdout + proc.stderr)

    # Like the earlier seam driver, this fixture spends nothing. Its non-provider name leaves
    # dispatch preflight to the real-launcher case above, while sc_reexec_snapshot owns -X.
    launcher = tmp / "fixture_launcher.sh"
    launcher.write_text('''#!/usr/bin/env bash
set -uo pipefail
. "$LIB"
echo() {
  builtin echo "$@"
  if [[ "$*" == "DETACHED pid="* ]] && [ -n "${PROOF_PIPE_CLOSED:-}" ]; then
    for ((i=0; i<200; i++)); do
      [ -f "$PROOF_PIPE_CLOSED" ] && break
      sleep 0.05
    done
  fi
}
sc_reexec_snapshot "$@"
sc_parse_flags "$@"
stub_child() {
  printf 'CHILD-STARTED\\n' >> "$MARK"
  if [ -n "${PROOF_JOB_RELEASE:-}" ]; then
    for ((i=0; i<200; i++)); do
      [ -f "$PROOF_JOB_RELEASE" ] && break
      sleep 0.05
    done
    sleep 0.2
    printf '%s\\n' '{"owner":"detached-job"}' > "$SC_RECORD"
  fi
  printf '%s\\n' '{"type":"result","subtype":"success","is_error":false}'
}
sc_run_watched stub_child ""
exit "$rc"
''', encoding="utf-8", newline="\n")
    fixture_env = codex_env(tmp)
    fixture_env.update({"LIB": posix(LIB), "MARK": posix(tmp / "child.txt")})
    # The receipt barrier makes closing after line one deterministic, not a scheduler race.
    piped = tmp / 'closed-pipe.record.json'
    pipe_closed = tmp / 'pipe-closed'
    job_release = tmp / 'job-release'
    pipe_mark = tmp / 'pipe-child.txt'
    pipe_env = dict(fixture_env, LAUNCH_MIN_COMMIT_GIB='0', LAUNCH_MIN_PHYSICAL_GIB='0',
                    PROOF_PIPE_CLOSED=posix(pipe_closed), PROOF_JOB_RELEASE=posix(job_release),
                    MARK=posix(pipe_mark))
    pipe_exit = Path(str(piped) + '.exit')
    with tempfile.TemporaryFile(mode='w+t', encoding='utf-8') as parent_err:
        parent = subprocess.Popen([BASH, posix(launcher), '-l', 'closed-pipe', '-X', '-R', posix(piped)],
                                  stdout=subprocess.PIPE, stderr=parent_err, text=True, env=pipe_env)
        first = parent.stdout.readline()
        parent.stdout.close()
        pipe_closed.touch()
        try:
            parent.wait(timeout=30)
            deadline = time.monotonic() + 10
            while not pipe_mark.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if first.startswith('DETACHED pid=') and pipe_mark.exists() and not piped.exists():
                ok('closed receipt pipe leaves the detached stub running without a record yet')
            else:
                bad('closed receipt pipe reaches a running detached job', 'first=%r child=%s record=%s'
                    % (first, pipe_mark.exists(), piped.exists()))
            if not pipe_exit.exists():
                ok('closed receipt pipe does not publish a launcher exit while the job runs')
            else:
                bad('closed receipt pipe publishes no premature exit', pipe_exit.read_text())
        finally:
            job_release.touch()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if pipe_exit.exists() and piped.exists():
                break
            time.sleep(0.05)
        if (pipe_exit.exists() and pipe_exit.read_text().strip() == '0' and piped.exists()
                and piped.read_text().strip() == '{"owner":"detached-job"}'):
            ok('after a closed receipt pipe the detached job publishes its record and exit 0')
        else:
            parent_err.seek(0)
            bad('detached job owns the final record and exit', parent_err.read()[-600:])

    rec = tmp / "mem-x.record.json"
    proc = subprocess.run([BASH, posix(launcher), "-l", "mem-x", "-T", "2", "-X", "-R", posix(rec), "--", "hello"],
                          capture_output=True, text=True, timeout=60, env=fixture_env)
    exitf = Path(str(rec) + ".exit")
    deadline = time.time() + 120
    while not exitf.is_file() and time.time() < deadline:
        time.sleep(0.25)
    code = exitf.read_text(encoding="utf-8").strip() if exitf.is_file() else None
    errf = Path(str(rec) + ".err")
    err_text = errf.read_text(encoding="utf-8", errors="replace") if errf.is_file() else ""
    if proc.returncode == 0 and "DETACHED pid=" in proc.stdout and code == "12":
        ok("a detached fixture launch below the floor writes 12 to its .exit")
    else:
        bad("detached codex launch writes 12 to .exit", "launch rc=%r out=%r exit=%r err=%r"
            % (proc.returncode, proc.stdout[-200:], code, err_text[-400:]))
    if ("WAIT memory headroom for mem-x:" in err_text and "wait bound" in err_text
            and "starting claude-code-proxy" not in err_text and not (tmp / "child.txt").exists()):
        ok("the detached wait reaches its bound without starting a child or proxy")
    else:
        bad("detached wait reaches its bound", err_text[-600:])
    if code is not None:
        wr_spec = importlib.util.spec_from_file_location("watch_records", CLAUDE / "tools" / "watch_records.py")
        wr = importlib.util.module_from_spec(wr_spec)
        wr_spec.loader.exec_module(wr)
        lines, live, _ = wr._wake({"u": {"records": [("mem-x", str(rec))], "fanout_dir": None}})
        ends = [(text.split("\n", 1)[0], trig) for kind, _, text, trig in lines if kind == "end"]
        if ends == [("DONE mem-x exit=%s" % code, True)] and code == "12":
            ok("watch_records --wake treats the refused launch's .exit as a waking DONE exit=12")
        else:
            bad("watch_records wake on the refusal", "code=%r lines=%r" % (code, lines))
    else:
        bad("watch_records wake on the refusal", "no .exit to read")

    # Plant only the external Windows sampler and holder process, not the admission logic.
    plant = tmp / "plant"
    plant.mkdir()
    reading = tmp / "reading.txt"
    reads_log = tmp / "reads.txt"
    reading.write_text("1 2", encoding="utf-8")
    (plant / "sitecustomize.py").write_text('''import ctypes, os, subprocess
from pathlib import Path
if os.environ.get('PROOF_MEMORY_FILE'):
    def sample(pointer, size):
        commit, physical = map(int, Path(os.environ['PROOF_MEMORY_FILE']).read_text().split())
        with open(os.environ['PROOF_MEMORY_READS'], 'a') as out: out.write('sample\\n')
        info = pointer._obj
        info.PageSize = 1024 ** 3
        info.CommitLimit = 100
        info.CommitTotal = 100 - commit
        info.PhysicalAvailable = physical
        return 1
    ctypes.windll.psapi.GetPerformanceInfo = sample
    real_run = subprocess.run
    def run(args, *rest, **kwargs):
        if args[0] == 'powershell.exe':
            return subprocess.CompletedProcess(args, 0, 'fixture|1|1073741824', '')
        return real_run(args, *rest, **kwargs)
    subprocess.run = run
''', encoding="utf-8", newline="\n")
    recover_env = dict(fixture_env, PYTHONPATH=os.pathsep.join(([os.environ['HARNESS_READ_RECORDER']] if os.environ.get('HARNESS_READ_LOG') and os.environ.get('HARNESS_READ_RECORDER') else []) + [posix(plant)]), PROOF_MEMORY_FILE=str(reading),
                       PROOF_MEMORY_READS=str(reads_log), LAUNCH_MIN_COMMIT_GIB="12",
                       LAUNCH_MIN_PHYSICAL_GIB="8", LAUNCH_MEMORY_POLL_SEC="0.1")
    recover = tmp / "mem-recover.record.json"
    proc = subprocess.run([BASH, posix(launcher), "-l", "mem-recover", "-T", "60", "-X", "-R", posix(recover)],
                          capture_output=True, text=True, timeout=60, env=recover_env)
    marker = Path(str(recover) + ".memory")
    deadline = time.time() + 10
    while not marker.exists() and not Path(str(recover) + ".exit").exists() and time.time() < deadline:
        time.sleep(0.1)
    wait_text = marker.read_text(encoding="utf-8") if marker.exists() else ""
    if proc.returncode == 0 and "commit 1.00 GiB" in wait_text and not (tmp / "child.txt").exists():
        ok("detached planted low reading publishes a visible wait before any child")
    else:
        bad("detached planted wait", "rc=%s marker=%r" % (proc.returncode, wait_text))
    wr_spec = importlib.util.spec_from_file_location("watch_records", CLAUDE / "tools/watch_records.py")
    wr = importlib.util.module_from_spec(wr_spec)
    wr_spec.loader.exec_module(wr)
    groups = {"u": {"records": [("mem-recover", str(recover))], "fanout_dir": None}}
    lines, live, _ = wr._wake(groups)
    # A memory wait clears by itself, so it prints as context and wakes nobody (only an end or a decision wakes).
    if live == 1 and any(kind == "memory" and "physical 2.00 GiB" in text and not trig
                         for kind, _, text, trig in lines):
        ok("session watcher shows the waiting job's memory readings without waking")
    else:
        bad("session watcher memory wait", str(lines))
    status = subprocess.run([sys.executable, str(CLAUDE / "tools/sidecar_fanout.py"), "--status", str(tmp)],
                            capture_output=True, text=True, timeout=60, env=recover_env)
    if status.returncode == 0 and any("mem-recover" in line and "waiting-memory" in line
                                    and "commit 1.00 GiB" in line for line in status.stdout.splitlines()):
        ok("fan-out --status shows the launcher's waiting-memory state and readings")
    else:
        bad("fan-out memory wait status", status.stdout + status.stderr[-400:])
    reading.write_text("20 10", encoding="utf-8")
    end = Path(str(recover) + ".exit")
    deadline = time.time() + 30
    while not end.exists() and time.time() < deadline:
        time.sleep(0.1)
    recovered_err = Path(str(recover) + ".err").read_text(encoding="utf-8", errors="replace")
    if (end.exists() and end.read_text().strip() == "0" and (tmp / "child.txt").exists()
            and "READY memory headroom" in recovered_err and not marker.exists()):
        ok("detached job launches on recovery, clears its wait marker, and exits 0")
    else:
        bad("detached recovery", recovered_err[-600:])
    before = len(reads_log.read_text().splitlines()) if reads_log.exists() else 0
    healthy = tmp / "mem-healthy.record.json"
    proc = subprocess.run([BASH, posix(launcher), "-l", "mem-healthy", "-X", "-R", posix(healthy)],
                          capture_output=True, text=True, timeout=60, env=recover_env)
    healthy_end = Path(str(healthy) + ".exit")
    deadline = time.time() + 30
    while not healthy_end.exists() and time.time() < deadline:
        time.sleep(0.1)
    after = len(reads_log.read_text().splitlines()) if reads_log.exists() else 0
    healthy_err = Path(str(healthy) + ".err").read_text(encoding="utf-8", errors="replace")
    if (proc.returncode == 0 and healthy_end.exists() and healthy_end.read_text().strip() == "0"
            and after - before == 1 and "WAIT memory headroom" not in healthy_err):
        ok("already clear headroom takes one sample and never double-waits")
    else:
        bad("already clear headroom", "reads=%s err=%r" % (after - before, healthy_err[-500:]))

# ---- 9. the floor follows the job: a brief that declares no heavy operation gets the light floor
# The briefs below are cut from real dispatch briefs: LIGHT_BRIEF from the harness author job that
# waited 20 min on 2026-10-28 (it names e2e runs only as the cost it measures, and `--close`/`--all`
# only to forbid them); HEAVY_BRIEF is an e2e fix job's command line.
LIGHT_BRIEF = ("Your task: assess the options below, implement it test-first in the semantic-search idle proxy.\n"
               "- Cost: full-machine CPU that slowed the e2e runs sharing the machine, and RAM.\n"
               "- Run the proofs bound to the changed files: `python3 .claude/scripts/harness_tests.py "
               "--proofs-for <changed paths>`. Do not run `--close` or `--all`.\n")
HEAVY_BRIEFS = {
    "deploy script": "Run `npm run deploy`, then the rows.",
    "e2e script": "Verify with npm run e2e -- finish.spec.ts",
    "playwright": "Run the playwright suite for the finish back.",
    "vitest": "Fix the failing vitest case in src/tasks.test.ts and rerun vitest.",
    "npx": "Run npx tsc -b before you return.",
}

if mod is None or not hasattr(mod, "classify_job"):
    for name in ("light brief classifies light", "each declared heavy operation classifies heavy",
                 "unknown shape, empty and missing brief classify heavy", "light floors are below heavy floors"):
        bad(name, "launch_memory.classify_job is missing")
else:
    if mod.classify_job(LIGHT_BRIEF, "author") == "light" and mod.classify_job(LIGHT_BRIEF, "review") == "light":
        ok("a brief that runs no browser or test runner classifies light for author and review")
    else:
        bad("light brief classifies light", repr((mod.classify_job(LIGHT_BRIEF, "author"),
                                                  mod.classify_job(LIGHT_BRIEF, "review"))))
    wrong = {n: mod.classify_job(t, "author") for n, t in HEAVY_BRIEFS.items()
             if mod.classify_job(t, "author") != "heavy"}
    if not wrong:
        ok("each declared heavy operation (playwright, vitest, npm, npx) classifies heavy")
    else:
        bad("each declared heavy operation classifies heavy", repr(wrong))
    odd = [mod.classify_job(LIGHT_BRIEF, "bench-score"), mod.classify_job(LIGHT_BRIEF, "arm"),
           mod.classify_job(LIGHT_BRIEF, ""), mod.classify_job(LIGHT_BRIEF, None),
           mod.classify_job("", "author"), mod.classify_job(None, "author")]
    if odd == ["heavy"] * 6:
        ok("a shape outside author/review/survey, an empty shape and an empty or missing brief classify heavy")
    else:
        bad("unknown shape, empty and missing brief classify heavy", repr(odd))
    light, heavy = mod.floors_for("light"), mod.floors_for("heavy")
    if (heavy.commit_gib, heavy.physical_gib) == (12.0, 6.0) and mod.floors_for("anything else") == heavy:
        ok("the heavy floors are today's 12 / 6 GiB and an unrecognized class falls back to them")
    else:
        bad("heavy floors", "%r %r" % (tuple(heavy), tuple(mod.floors_for("anything else"))))
    if 0 < light.physical_gib < heavy.physical_gib and 0 < light.commit_gib < heavy.commit_gib:
        ok("light floors are positive and below the heavy floors")
    else:
        bad("light floors are below heavy floors", repr(tuple(light)))
    tool_text = TOOL.read_text(encoding="utf-8")
    if "4.85" in tool_text and "basis" in tool_text.lower() and "commit" in tool_text.lower().split("basis", 1)[1]:
        ok("the docstring states the measured basis of the floors")
    else:
        bad("docstring states the basis", "no measured basis text")

    saved_env = {k: os.environ.pop(k, None) for k in
                 ("LAUNCH_MIN_COMMIT_GIB", "LAUNCH_MIN_PHYSICAL_GIB", "LAUNCH_MEMORY_POLL_SEC")}
    try:
        os.environ["LAUNCH_MIN_COMMIT_GIB"] = "3"
        os.environ["LAUNCH_MIN_PHYSICAL_GIB"] = "2"
        pinned = mod.floors_from_env("light")
        pinned_heavy = mod.floors_from_env("heavy")
        os.environ.pop("LAUNCH_MIN_COMMIT_GIB")
        os.environ.pop("LAUNCH_MIN_PHYSICAL_GIB")
        by_class = (mod.floors_from_env("light"), mod.floors_from_env("heavy"), mod.floors_from_env())
    finally:
        for k, v in saved_env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    if (pinned.commit_gib, pinned.physical_gib) == (3.0, 2.0) == (pinned_heavy.commit_gib, pinned_heavy.physical_gib):
        ok("an env override wins over the class floors")
    else:
        bad("env override over the class", repr((tuple(pinned), tuple(pinned_heavy))))
    if (by_class[0][:2] == light[:2] and by_class[1][:2] == heavy[:2] and by_class[2][:2] == heavy[:2]):
        ok("floors_from_env takes its defaults from the job class, heavy when none is given")
    else:
        bad("floors_from_env by class", repr([tuple(f) for f in by_class]))

    launch_flags = mod.launch_class_args(["bash", "launcher.sh", "-m", "model-a", "-f", "/tmp/brief.md", "-l", "x",
                                          "-X", "-R", "rec.json", "-G", "author", "-d", "/w"])
    if launch_flags == ["--brief", "/tmp/brief.md", "--shape", "author"] and mod.launch_class_args(["bash", "-l", "x"]) == []:
        ok("launch_class_args reads the brief file and shape from a launcher argv, and nothing when absent")
    else:
        bad("launch_class_args", repr(launch_flags))

# CLI: the same planted sample is refused for a heavy brief and admitted for a light one.
PLANT_SITE = '''import ctypes, os, subprocess
from pathlib import Path
if os.environ.get('PROOF_MEMORY_FILE'):
    def sample(pointer, size):
        commit, physical = map(int, Path(os.environ['PROOF_MEMORY_FILE']).read_text().split())
        info = pointer._obj
        info.PageSize = 1024 ** 3
        info.CommitLimit = 100
        info.CommitTotal = 100 - commit
        info.PhysicalAvailable = physical
        return 1
    ctypes.windll.psapi.GetPerformanceInfo = sample
    real_run = subprocess.run
    def run(args, *rest, **kwargs):
        if args[0] == 'powershell.exe':
            return subprocess.CompletedProcess(args, 0, 'fixture|1|1073741824', '')
        return real_run(args, *rest, **kwargs)
    subprocess.run = run
'''
class_tmp = Path(tempfile.mkdtemp(prefix="lm_class_"))
(class_tmp / "plant").mkdir()
(class_tmp / "plant" / "sitecustomize.py").write_text(PLANT_SITE, encoding="utf-8", newline="\n")
(class_tmp / "light.md").write_text(LIGHT_BRIEF, encoding="utf-8", newline="\n")
(class_tmp / "heavy.md").write_text(HEAVY_BRIEFS["deploy script"], encoding="utf-8", newline="\n")
(class_tmp / "reading.txt").write_text("8 4", encoding="utf-8")
plant_env = {"PYTHONPATH": str(class_tmp / "plant"), "PROOF_MEMORY_FILE": str(class_tmp / "reading.txt")}
cases = [("light author brief", ["--brief", str(class_tmp / "light.md"), "--shape", "author"], 0),
         ("heavy author brief", ["--brief", str(class_tmp / "heavy.md"), "--shape", "author"], 3),
         ("no brief", [], 3),
         ("unreadable brief", ["--brief", str(class_tmp / "absent.md"), "--shape", "author"], 3)]
for name, extra, want in cases:
    proc = cli(["check", "--label", "class-cli"] + extra, plant_env)
    if proc.returncode == want:
        ok("check CLI on 8 GiB commit / 4 GiB physical free: %s exits %d" % (name, want))
    else:
        bad("check CLI class: %s" % name, "rc=%r want %d err=%r" % (proc.returncode, want, proc.stderr[-300:]))
proc = cli(["check", "--label", "class-cli", "--brief", str(class_tmp / "heavy.md"), "--shape", "author"], plant_env)
if "floors commit 12.00 GiB, physical 6.00 GiB" in proc.stderr:
    ok("the heavy REFUSED line names today's 12 / 6 GiB floors")
else:
    bad("heavy REFUSED line", proc.stderr[-300:])
(class_tmp / "reading.txt").write_text("2 1", encoding="utf-8")
proc = cli(["check", "--label", "class-cli", "--brief", str(class_tmp / "light.md"), "--shape", "author"], plant_env)
if proc.returncode == 3 and "(light job)" in proc.stderr:
    ok("a light job below its own floors is still refused, and the line says the floors are the light ones")
else:
    bad("light job below its floors", "rc=%r err=%r" % (proc.returncode, proc.stderr[-300:]))

# The fan-out hold (_supervised_launch.launch) and the sidecar seam read the same brief.
SUP = CLAUDE / "tools" / "_supervised_launch.py"
sup_spec = importlib.util.spec_from_file_location("_supervised_launch", SUP)
sup = importlib.util.module_from_spec(sup_spec)
sys.path.insert(0, str(CLAUDE / "tools"))
sup_spec.loader.exec_module(sup)
(class_tmp / "reading.txt").write_text("8 4", encoding="utf-8")
light_argv = ["bash", "launcher.sh", "-f", str(class_tmp / "light.md"), "-G", "author", "-l", "fan-light"]
if hasattr(sup, "memory_args") and sup.memory_args(light_argv) == ["--brief", str(class_tmp / "light.md"), "--shape", "author"]:
    ok("_supervised_launch passes the launch argv's brief and shape to the memory tool")
else:
    bad("_supervised_launch.memory_args", "missing or wrong")
held_env = dict({k: v for k, v in os.environ.items()
                 if k not in ("LAUNCH_MIN_COMMIT_GIB", "LAUNCH_MIN_PHYSICAL_GIB")},
                LAUNCH_MEMORY_POLL_SEC="1", **plant_env)
runs = []
try:
    code = sup.launch("fan-light", lambda: runs.append(1) or 0, held_env, argv=light_argv)
except TypeError as exc:
    bad("fan-out light job is admitted at 8/4 GiB free", "launch takes no argv: %s" % exc)
else:
    if code == 0 and runs == [1]:
        ok("fan-out light job is admitted at 8/4 GiB free instead of holding for the heavy floors")
    else:
        bad("fan-out light job is admitted", "code=%r runs=%r" % (code, runs))
heavy_hold = subprocess.Popen(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, sys.argv[1]); import _supervised_launch as s; "
     "s.launch('fan-heavy', lambda: 0, None, argv=['bash','l.sh','-f',sys.argv[2],'-G','author'])",
     str(CLAUDE / "tools"), str(class_tmp / "heavy.md")],
    env=held_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    heavy_hold.wait(timeout=6)
    bad("fan-out heavy job holds at 8/4 GiB free", "it was admitted: rc=%r" % heavy_hold.returncode)
except subprocess.TimeoutExpired:
    ok("fan-out heavy job still holds at 8/4 GiB free (the heavy floors bind)")
finally:
    if heavy_hold.poll() is None:
        subprocess.run(["taskkill", "/PID", str(heavy_hold.pid), "/T", "/F"], capture_output=True)
    heavy_hold.communicate()

if BASH is not None and LIB.is_file():
    SEAM_CLASS = r'''
set -uo pipefail
. "$LIB"
SC_FORMAT=json
SC_LABEL=seam-class
SC_PROMPT_FILE="$BRIEF"
SC_SHAPE=author
stub_child() { printf 'CHILD-STARTED\n' >> "$MARK"; printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'; }
sc_run_watched stub_child ""
printf 'DONE rc=%s\n' "$rc"
'''
    for name, brief, started in (("light", "light.md", True), ("heavy", "heavy.md", False)):
        tmp2 = Path(tempfile.mkdtemp(prefix="lm_seamclass_"))
        drv = tmp2 / "driver.sh"
        drv.write_text(SEAM_CLASS, encoding="utf-8", newline="\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("SC_") and k != "SIDECAR_DETACHED"
               and k not in ("LAUNCH_MIN_COMMIT_GIB", "LAUNCH_MIN_PHYSICAL_GIB")}
        env.update(plant_env)
        env["PYTHONPATH"] = posix(class_tmp / "plant")
        env.update({"LIB": posix(LIB), "MARK": posix(tmp2 / "mark.txt"), "BRIEF": posix(class_tmp / brief),
                    "PYTHONIOENCODING": "utf-8", "PROOF_MEMORY_FILE": posix(class_tmp / "reading.txt")})
        env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
        proc = subprocess.run([BASH, posix(drv)], env=env, capture_output=True, text=True, timeout=90)
        ran_child = (tmp2 / "mark.txt").exists()
        if ran_child == started and (started or proc.returncode == 12):
            ok("sidecar seam on 8/4 GiB free: a %s brief %s" % (name, "starts its child" if started
                                                                 else "is refused with exit 12"))
        else:
            bad("sidecar seam class: %s brief" % name, "child=%s rc=%r err=%r" % (ran_child, proc.returncode,
                                                                                  proc.stderr[-300:]))

if ran < 36:
    bad("case coverage", "only %d cases reached an assertion; a skipped case is not a pass" % ran)
print("%d cases, %s" % (ran, "all passed" if not fail else "FAILURES above"))
print("PASS test_launch_memory" if not fail else "FAIL test_launch_memory")
sys.exit(fail)
