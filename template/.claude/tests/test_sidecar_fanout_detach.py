#!/usr/bin/env python3
"""RED-first proof for E3 of `.claude/plans/sidecar-detach.md`: `tools/sidecar_fanout.py` launches
its fan-out children DETACHED (`-X`), so killing the fan-out's own process stops no child, and a
relaunched job never collides with a prior attempt's files.

Every fixture here is a FAKE launcher (a small bash script this file writes into its own temp
dir) -- never `.claude/scripts/lib/sidecar_common.sh` -- so this proof does not depend on lane
E-A's concurrent edits to that shared lib, per this lane's brief. The fake mimics only the two
shapes `run_jobs` must tell apart: `-X` present hands off in milliseconds and finishes the job in
a disowned background subshell; `-X` absent runs the job in the foreground for its whole duration,
which is exactly HEAD's behavior today (HEAD's `plan_job` never emits `-X` at all).

Run: python3 .claude/tests/test_sidecar_fanout_detach.py
"""
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "..", "tools", "sidecar_fanout.py")

# Fast, deterministic polling: read BEFORE the module executes, since POLL_INTERVAL_SEC is a
# module-level constant evaluated at import time.
os.environ.setdefault("SIDECAR_FANOUT_POLL_SEC", "0.05")

spec = importlib.util.spec_from_file_location("sf_detach", MOD)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_detach_")
PROMPT = os.path.join(TMP, "p.md")
open(PROMPT, "w", encoding="utf-8").write("lens body\n")

# ---- a fake bash launcher, NOT sidecar_common.sh -----------------------------------------------
FAKE_LAUNCHER = os.path.join(TMP, "fake_x_launcher.sh")
with open(FAKE_LAUNCHER, "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "record=\"\"; sleep_s=\"0\"; body=\"\"; rc=\"0\"; detach=0; crash=0\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -X) detach=1; shift ;;\n"
        "    -R) record=\"$2\"; shift 2 ;;\n"
        "    -sleep) sleep_s=\"$2\"; shift 2 ;;\n"
        "    -body) body=\"$2\"; shift 2 ;;\n"
        "    -rc) rc=\"$2\"; shift 2 ;;\n"
        "    -crash) crash=1; shift ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "pidf=\"${record}.pid\"\n"
        "outf=\"${record}.out\"\n"
        "exitf=\"${record}.exit\"\n"
        "if [ \"$crash\" = 1 ]; then\n"
        "  ( sleep 0.05 ) </dev/null >/dev/null 2>&1 &\n"
        "  disown\n"
        "  cpid=$!\n"
        "  echo \"pid=$cpid\" > \"$pidf\"\n"
        "  echo \"DETACHED pid=$cpid\"\n"
        "  exit 0\n"
        "fi\n"
        "run_job() {\n"
        "  sleep \"$sleep_s\"\n"
        "  cat \"$body\" > \"$outf\"\n"
        "  printf '%s' \"$rc\" > \"$exitf\"\n"
        "}\n"
        "if [ \"$detach\" = 1 ]; then\n"
        "  ( trap '' HUP\n"
        "    run_job\n"
        "  ) </dev/null >/dev/null 2>&1 &\n"
        "  disown\n"
        "  child=$!\n"
        "  echo \"pid=$child\" > \"$pidf\"\n"
        "  echo \"DETACHED pid=$child\"\n"
        "  exit 0\n"
        "else\n"
        "  echo \"pid=$$\" > \"$pidf\"\n"
        "  run_job\n"
        "  exit \"$rc\"\n"
        "fi\n"
    )

DATA = {
    "transports": {"faketx": {"costModel": "plan-quota", "launcher": sf._posix(FAKE_LAUNCHER)}},
    "models": [{"transport": "faketx", "id": "fake-1", "alias": "faker", "roles": ["sonnet"]}],
}


def plan(label, extra_args=None, out_dir=TMP):
    job = {"label": label, "alias": "faker", "promptFile": PROMPT, "kind": "scout"}
    if extra_args:
        job["extraArgs"] = extra_args
    return sf.plan_job(job, DATA, out_dir, False)


def rval(argv, flag_name):
    return argv[argv.index(flag_name) + 1]


CASES = []


def case(name, fn):
    CASES.append((name, fn))


# ---- children are launched with -X --------------------------------------------------------------
_, _XARGV, _ = plan("xcheck")
case("every fan-out child is launched with -X",
     lambda: "-X" in _XARGV)
case("...and -R still carries the record path -X needs",
     lambda: rval(_XARGV, "-R").endswith("xcheck.record.json"))


# ---- a normal run's summary and output files are unchanged ---------------------------------------
NORMAL_BODY = os.path.join(TMP, "normal_body.txt")
open(NORMAL_BODY, "w", encoding="utf-8").write("the real deliverable\n")
_label_n, _argv_n, _out_n = plan("normaljob", ["-body", sf._posix(NORMAL_BODY), "-rc", "3",
                                               "-sleep", "0"])
_res_n = sf.run_jobs([(_label_n, _argv_n, _out_n)], 1, TMP)[_label_n]

case("a detached run's reported exit is the REAL job's code, not the handoff's own 0",
     lambda: _res_n["exit"] == 3)
case("...and its meaning is looked up from the shared LAUNCHER_EXITS table",
     lambda: _res_n["meaning"] == sf.LAUNCHER_EXITS[3])
case("...and the archival out_path holds the job's real body, copied from <record>.out",
     lambda: open(_out_n, encoding="utf-8").read() == "the real deliverable\n")
case("...and frozenInput bookkeeping still runs -- an unrelated existing contract",
     lambda: "frozenInput" in _res_n and "held" in _res_n["frozenInput"])


# ---- a relaunch uses a new attempt path; the old attempt stays byte-identical ---------------------
RELAUNCH_DIR = tempfile.mkdtemp(prefix="sfanout_relaunch_")
_, _argv_r1, _ = plan("relaunchjob", out_dir=RELAUNCH_DIR)
_r1 = rval(_argv_r1, "-R")
case("attempt 1 keeps the plain <label>.record.json name",
     lambda: _r1.endswith("relaunchjob.record.json"))

# Mark attempt 1 "used" -- the same signal -X's own refusal checks (a `.out`/`.exit` sibling).
open(_r1 + ".exit", "w", encoding="utf-8").write("0")
open(_r1 + ".out", "w", encoding="utf-8").write("first attempt output\n")

_, _argv_r2, _ = plan("relaunchjob", out_dir=RELAUNCH_DIR)
_r2 = rval(_argv_r2, "-R")
case("a relaunch of the same label picks a NEW attempt path",
     lambda: _r2 != _r1 and _r2.endswith("relaunchjob.attempt2.record.json"))
case("...and the previous attempt's files are never touched, let alone deleted",
     lambda: open(_r1 + ".out", encoding="utf-8").read() == "first attempt output\n")
case("...and a THIRD relaunch steps to attempt 3, never re-using attempt 2 until it too is used",
     lambda: rval(plan("relaunchjob", out_dir=RELAUNCH_DIR)[1], "-R") == _r2)


# ---- a child whose detached pid dies without an exit file is reported as died --------------------
_die_record = sf._posix(os.path.join(TMP, "diejob.record.json"))
_argv_die = [sf.sidecar_launch.git_bash(), sf._posix(FAKE_LAUNCHER), "-X", "-crash",
             "-R", _die_record]
_res_die = sf.run_jobs([("diejob", _argv_die, os.path.join(TMP, "diejob.out.json"))], 1, TMP)["diejob"]

case("a detached pid that dies without writing .exit is reported as died, not waited on forever",
     lambda: _res_die["exit"] is None and "died" in (_res_die.get("error") or "").lower())
case("...and it names the record path that never got its .exit file",
     lambda: _die_record in (_res_die.get("error") or ""))


# ---- a live Git Bash (MSYS) pid reads as alive ----------------------------------------------------
# Launchers write `$BASHPID`/`$!`, which on Git Bash are MSYS pids, not Windows pids. OpenProcess on
# one found nothing, so every detached job read as dead after one grace poll (16/25 fake runs
# reported "died" on 2026-09-23).
if platform.system() == "Windows":
    _sleeper = subprocess.run([sf.sidecar_launch.git_bash(), "-c", "(sleep 4) </dev/null >/dev/null 2>&1 & disown; echo $!"],
                              capture_output=True, text=True, timeout=30)
    _msys_pid = int(_sleeper.stdout.strip())
    _msys_alive = sf._pid_alive(_msys_pid)  # read now: cases run at the end, after the sleep ends
    case("a live MSYS pid from a Git Bash launcher reads as alive",
         lambda: _msys_alive is True)
    case("...and a pid no process holds reads as dead",
         lambda: sf._pid_alive(987654321) is False)


# ---- an .exit caught mid-write (empty) is not yet written ---------------------------------------
# A writer that opens then writes (the fake launcher's `printf > exitf`) exposes an empty file for a
# moment; reading it as "no exit code" reported a real run as exit None (flaky under a loaded battery).
import threading  # noqa: E402
_EMPTY_REC = sf._posix(os.path.join(TMP, "emptyexit.record.json"))
open(_EMPTY_REC + ".exit", "w", encoding="utf-8").close()
threading.Timer(0.3, lambda: open(_EMPTY_REC + ".exit", "w", encoding="utf-8").write("4")).start()
_empty_res = sf._wait_detached(_EMPTY_REC, poll_interval=0.05)
case("an empty .exit is polled again until its code lands",
     lambda: _empty_res == (4, False))


# ---- --status shows the LATEST attempt, not the first -----------------------------------------
STATUS_DIR = tempfile.mkdtemp(prefix="sfanout_status_")
BODY1 = os.path.join(TMP, "body1.txt")
open(BODY1, "w", encoding="utf-8").write("attempt one\n")
BODY2 = os.path.join(TMP, "body2.txt")
open(BODY2, "w", encoding="utf-8").write("attempt two\n")

_label_s1, _argv_s1, _out_s1 = plan("statusjob", ["-body", sf._posix(BODY1), "-rc", "5",
                                                  "-sleep", "0"], out_dir=STATUS_DIR)
_r_s1 = rval(_argv_s1, "-R")
sf.run_jobs([(_label_s1, _argv_s1, _out_s1)], 1, STATUS_DIR)
case("attempt 1 of statusjob dispatches and completes",
     lambda: os.path.isfile(_r_s1 + ".exit"))

_label_s2, _argv_s2, _out_s2 = plan("statusjob", ["-body", sf._posix(BODY2), "-rc", "6",
                                                  "-sleep", "0"], out_dir=STATUS_DIR)
_r_s2 = rval(_argv_s2, "-R")
case("the relaunch's record path differs from attempt 1's",
     lambda: _r_s2 != _r_s1)
sf.run_jobs([(_label_s2, _argv_s2, _out_s2)], 1, STATUS_DIR)

case("latest_attempt_record_path resolves the SECOND attempt once it exists",
     lambda: sf._posix(sf.latest_attempt_record_path(STATUS_DIR, "statusjob")) == _r_s2)

def _status_lines(result):
    """{label: (state, whole line)} from `--status` output."""
    lines = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            lines[parts[0]] = (parts[1], line)
    return lines


_r_status = subprocess.run([sys.executable, MOD, "--status", STATUS_DIR],
                           capture_output=True, text=True, timeout=30, encoding="utf-8")
_st = _status_lines(_r_status)
case("the CLI --status flag reports the LATEST attempt's exit code and dispatches nothing",
     lambda: _r_status.returncode == 0 and "statusjob" in _st and "exit=6" in _st["statusjob"][1]
             and "exit=5" not in _r_status.stdout)

open(os.path.join(STATUS_DIR, "neverran.stream"), "w", encoding="utf-8").write("")
_r_status_none = subprocess.run([sys.executable, MOD, "--status", STATUS_DIR],
                                capture_output=True, text=True, timeout=30, encoding="utf-8")
_st_none = _status_lines(_r_status_none)
case("--status lists a job with a stream but no attempt, without an exit code",
     lambda: _r_status_none.returncode == 0 and "neverran" in _st_none
             and "exit=" not in _st_none["neverran"][1] and "exit=6" in _st_none["statusjob"][1])

# A live launcher whose stream ended on a result long ago: --status and the session watcher read it the same.
_LINGER_DIR = tempfile.mkdtemp(prefix="sfanout_status_linger_")
_linger_rec = os.path.join(_LINGER_DIR, "linger.record.json")
_linger_stream = os.path.join(_LINGER_DIR, "linger.stream")
_live_pid = _live_winpid = os.getpid()
if platform.system() == "Windows":  # a launcher's .pid holds an MSYS pid; watch_records reads its winpid
    _live = subprocess.run([sf.sidecar_launch.git_bash(), "-c",
                            "(sleep 30) </dev/null >/dev/null 2>&1 & disown; echo $! $(cat /proc/$!/winpid)"],
                           capture_output=True, text=True, timeout=30).stdout.split()
    _live_pid, _live_winpid = int(_live[0]), int(_live[1])
_stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
open(_linger_rec + ".pid", "w", encoding="utf-8").write(
    "%s launch=proof winpid=%d pid=%d\n" % (_stamp, _live_winpid, _live_pid))
open(_linger_stream, "w", encoding="utf-8").write(
    json.dumps({"type": "assistant", "session_id": "s1"}) + "\n"
    + json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": "s1"}) + "\n")
_then = time.time()
os.utime(_linger_rec + ".pid", (_then - 900, _then - 900))
os.utime(_linger_stream, (_then - 600, _then - 600))
_r_linger = subprocess.run([sys.executable, MOD, "--status", _LINGER_DIR],
                           capture_output=True, text=True, timeout=30, encoding="utf-8")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import watch_records
case("--status reads a live launcher past its stream's result as lingering, as the session watcher does",
     lambda: _status_lines(_r_linger).get("linger", ("", ""))[0] == "lingering"
             and watch_records.result_lingering(_linger_rec) is not None)

_EMPTY_DIR = tempfile.mkdtemp(prefix="sfanout_status_empty_")
_r_status_empty = subprocess.run([sys.executable, MOD, "--status", _EMPTY_DIR],
                                 capture_output=True, text=True, timeout=30, encoding="utf-8")
case("--status on a directory with no job exits 2 and names it",
     lambda: _r_status_empty.returncode == 2 and "no job records or streams" in _r_status_empty.stderr)


# ---- a memory wait stays visible ahead of a stale stream -------------------------------------
import launch_memory
wait_lines = []
launch_memory.wait_for_headroom(iter([(1, 2), (20, 10)]).__next__, lambda _: None,
                               wait_lines.append, launch_memory.Floors(12, 8, 1), 'waiting',
                               read_holders=lambda: '')
_wait_record = os.path.join(STATUS_DIR, 'waiting.record.json')
open(_wait_record + '.journal.jsonl', 'w', encoding='utf-8').write(
    json.dumps({"event": "waiting-memory", "message": wait_lines[0]}) + '\n')
open(os.path.join(STATUS_DIR, 'waiting.stream'), 'w', encoding='utf-8').write('')
_wait_status = subprocess.run([sys.executable, MOD, '--status', STATUS_DIR],
                              capture_output=True, text=True, timeout=30, encoding='utf-8')
_wait_by = _status_lines(_wait_status)
case('--status shows waiting-memory and readings before stale progress',
     lambda: _wait_by.get('waiting', ('', ''))[0] == 'waiting-memory'
             and 'physical 2.00 GiB' in _wait_by['waiting'][1])
open(_wait_record + '.exit', 'w', encoding='utf-8').write('12')
_wait_done = subprocess.run([sys.executable, MOD, '--status', STATUS_DIR],
                            capture_output=True, text=True, timeout=30, encoding='utf-8')
case('terminal exit 12 wins over a leftover memory marker',
     lambda: _status_lines(_wait_done).get('waiting', ('', ''))[0] == 'finished'
             and 'exit=12' in _wait_done.stdout)

# ---- killing the fan-out's own python process leaves every child's exit file to appear -----------
# The DRIVER runs `run_jobs` as its own OS process, so it -- and only it -- can be killed without
# touching the test process itself. `time.sleep(0.5)` before the kill gives the fake launcher's own
# bash.exe (the driver's DIRECT, live child) ample time to have already handed off and exited: by
# then the only thing the driver still holds is a read-only poll loop, never a live child process.
_KILL_MARGIN_SEC = 0.5
_JOB_SLEEP_SEC = 1.5


def _spawn_driver(argv, label, out_path, out_dir):
    argv_json = os.path.join(out_dir, label + ".argv.json")
    json.dump(argv, open(argv_json, "w", encoding="utf-8"))
    driver_path = os.path.join(out_dir, label + ".driver.py")
    driver_src = (
        "import importlib.util, json, os, sys\n"
        "os.environ.setdefault('SIDECAR_FANOUT_POLL_SEC', '0.05')\n"
        "spec = importlib.util.spec_from_file_location('sf_driver', %r)\n"
        "sf = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(sf)\n"
        "argv = json.load(open(%r, encoding='utf-8'))\n"
        "sf.run_jobs([(%r, argv, %r)], 1, %r)\n"
    ) % (MOD, argv_json, label, out_path, out_dir)
    open(driver_path, "w", encoding="utf-8").write(driver_src)
    return subprocess.Popen([sys.executable, driver_path])


def _kill_tree(pid):
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


KILL_DIR = tempfile.mkdtemp(prefix="sfanout_kill_")
KILL_BODY = os.path.join(TMP, "kill_body.txt")
open(KILL_BODY, "w", encoding="utf-8").write("REAL DETACHED OUTPUT\n")

_label_k, _argv_k, _out_k = plan("killjob", ["-body", sf._posix(KILL_BODY), "-rc", "9",
                                             "-sleep", str(_JOB_SLEEP_SEC)], out_dir=KILL_DIR)
_record_k = rval(_argv_k, "-R")
_driver_k = _spawn_driver(_argv_k, _label_k, _out_k, KILL_DIR)
time.sleep(_KILL_MARGIN_SEC)
_kill_tree(_driver_k.pid)
_driver_k.wait(timeout=10)
time.sleep(_JOB_SLEEP_SEC + 0.5)

case("killing the fan-out's own python process does not stop the -X'd child",
     lambda: os.path.isfile(_record_k + ".exit")
             and open(_record_k + ".exit", encoding="utf-8").read().strip() == "9"
             and open(_record_k + ".out", encoding="utf-8").read() == "REAL DETACHED OUTPUT\n")

# Contrast: WITHOUT -X (HEAD's own shape today), the identical kill DOES stop the job -- proving
# the case above is -X doing the work, not a timing coincidence.
_label_fg, _argv_fg_x, _out_fg = plan("killjob_fg", ["-body", sf._posix(KILL_BODY), "-rc", "9",
                                                     "-sleep", str(_JOB_SLEEP_SEC)],
                                      out_dir=KILL_DIR)
_argv_fg = [a for a in _argv_fg_x if a != "-X"]
_record_fg = rval(_argv_fg, "-R")
_driver_fg = _spawn_driver(_argv_fg, _label_fg, _out_fg, KILL_DIR)
time.sleep(_KILL_MARGIN_SEC)
_kill_tree(_driver_fg.pid)
_driver_fg.wait(timeout=10)
time.sleep(_JOB_SLEEP_SEC + 0.5)

case("...and WITHOUT -X the same kill DOES stop the job (the mechanism, not a coincidence)",
     lambda: not os.path.isfile(_record_fg + ".exit"))

# ---- the foreground --detach call surfaces the dispatch-doctrine deny, never the detached child -----
import contextlib
import io

sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

_DOCTRINE_JOBS = os.path.join(TMP, "doctrine_jobs.json")
with open(_DOCTRINE_JOBS, "w", encoding="utf-8") as _fh:
    json.dump([{"label": "doc1", "alias": "sonnet", "effort": "medium", "kind": "author",
                "promptFile": PROMPT}], _fh)


def _detach_main(session, mark=False):
    """(exit code, stdout, detach_driver calls) of an in-process `--detach` call under a fresh hook state."""
    state = tempfile.mkdtemp(prefix="sfanout_doctrine_state_")
    saved = {k: os.environ.get(k) for k in ("HARNESS_HOOK_STATE_DIR", "CLAUDE_CODE_SESSION_ID",
                                            "SIDECAR_DISPATCH_OWNER")}
    os.environ["HARNESS_HOOK_STATE_DIR"], os.environ["CLAUDE_CODE_SESSION_ID"] = state, session
    os.environ.pop("SIDECAR_DISPATCH_OWNER", None)
    calls, real = [], sf.detach_driver
    sf.detach_driver = lambda *a, **k: calls.append(a) or 0
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)  # sidecar_fanout reconfigures its stdout
    try:
        import _hook_state
        if mark:
            _hook_state.mark_skill_in_context(session, "orchestration")
            _hook_state.mark_ladder_slice(session, ["executor"])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            code = sf.main([_DOCTRINE_JOBS, "--detach", "--out-dir", os.path.join(TMP, "doctrine_out_" + session)])
    finally:
        sf.detach_driver = real
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code, raw.getvalue().decode("utf-8"), calls


_REFUSED = _detach_main("fgdoc001")
case("a --detach call with the doctrine unmet refuses in the foreground and detaches nothing",
     lambda: _REFUSED[0] != 0 and not _REFUSED[2])
case("...printing the deny with the slice for the job's tier on stdout",
     lambda: "Dispatch DENIED" in _REFUSED[1] and "## Role definitions" in _REFUSED[1])
_ALLOWED = _detach_main("fgdoc002", mark=True)
case("a --detach call with the doctrine met detaches as before",
     lambda: _ALLOWED[0] == 0 and len(_ALLOWED[2]) == 1 and "DENIED" not in _ALLOWED[1])


def main():
    failed = 0
    total = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        total += 1
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
