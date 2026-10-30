#!/usr/bin/env python3
"""Proof: a sidecar launch refuses beside a live copy of itself, and every launch carries its id.

Cases:
  1-4. the real codex launcher refuses (exit 13) when a live launcher already runs its job (same prompt
       file under the same -l label or -R record) or resumes its session, naming the PID and the reap
       command; a -X launch refuses before it detaches; another job on the same prompt, or another
       prompt, proceeds to the next gate (the planted memory floor, exit 12).
  5-6. the launch id is exported before any process starts: a proxy, a shim and the child started
       the way the launchers start them all carry it, and <record>.pid names it. A launch nested in
       a seat takes a fresh id; one a fan-out assigned keeps it.
  7.   opencode_sidecar.sh takes the memory sample before its proxy.
  8.   sidecar_fanout refuses a job a live launcher already runs (same prompt under the same -l label or -R
       record), and names exit 13.
  9.   every launch that ends leaves one spend-ledger row: a refusal (13, 12, a usage error once
       its label is parsed) writes `outcome: ended-before-result` with its exit code and the line
       it printed and `costUSD: 0`, since no provider run started (a credit plan's balance reads every
       row after its anchor), and with -R the `.exit`; a run that publishes keeps its one row; a run without
       -R writes `finished-without-record`.
The live launchers in 1-4 are sleeping stand-ins named *_sidecar.sh; nothing is dispatched.
Run: python3 .claude/tests/test_sidecar_duplicate_launch.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = HERE.parent
SCRATCH = tempfile.mkdtemp(prefix="dup_launch_scratch_")
atexit.register(shutil.rmtree, SCRATCH, True)
sys.path.insert(0, str(CLAUDE / "tools"))
CODEX = CLAUDE / "scripts" / "codex_proxy_sidecar.sh"
OPENCODE = CLAUDE / "scripts" / "opencode_sidecar.sh"
LIB = CLAUDE / "scripts" / "lib" / "sidecar_common.sh"
ran = fail = 0


def ok(name):
    global ran
    ran += 1
    print("PASS", name)


def bad(name, detail=""):
    global ran, fail
    ran += 1
    fail = 1
    print("FAIL", name, ("\n     " + detail) if detail else "")


def check(cond, name, detail=""):
    ok(name) if cond else bad(name, detail)


def git_bash():
    for cand in (os.environ.get("SIDECAR_PROOF_BASH", ""), os.path.join(os.environ.get("EXEPATH", ""), "bash.exe"),
                 "C:/Program Files/Git/bin/bash.exe", shutil.which("bash") or ""):
        if cand and Path(cand).is_file():
            try:
                r = subprocess.run([cand, "-c", "uname -s"], capture_output=True, text=True, timeout=30)
            except Exception:
                continue
            if r.returncode == 0 and ("MINGW" in r.stdout or "MSYS" in r.stdout or "CYGWIN" in r.stdout):
                return cand
    return None


def posix(path):
    text = str(path).replace("\\", "/")
    return "/%s%s" % (text[0].lower(), text[2:]) if len(text) > 1 and text[1] == ":" else text


def launcher_env(tmp):
    env = dict(os.environ)
    env.update({"LAUNCH_MIN_COMMIT_GIB": "1000000", "LAUNCH_MIN_PHYSICAL_GIB": "1000000",
                "PYTHONIOENCODING": "utf-8", "HOME": posix(tmp), "CLAUDE_BIN": posix(tmp / "no-claude.exe"),
                "SIDECAR_EXHAUSTED_DIR": posix(tmp / "exhausted"),
                "SIDECAR_LEDGER_PATH": str(tmp / "ledger.jsonl").replace("\\", "/")})
    for key in ("CLAUDE_CODE_SESSION_ID", "SIDECAR_LAUNCH_ID", "SIDECAR_SEAT_LAUNCH_ID", "SIDECAR_FANOUT_ID",
                "SIDECAR_DETACHED", "SC_SNAPSHOT_OF"):
        env.pop(key, None)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    return env


def ledger_rows(tmp, label):
    path = tmp / "ledger.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if row.get("label") == label]


def ended(rows, code, reason):
    return (len(rows) == 1 and rows[0].get("outcome") == "ended-before-result"
            and rows[0].get("exitCode") == code and str(rows[0].get("reason", "")).startswith(reason)
            and rows[0].get("transport") == "codex" and "timestamp" in rows[0] and "parentSessionId" in rows[0]
            and rows[0].get("costUSD") == 0)


def stand_in(bash, tmp, args):
    """A sleeping process whose command line is a launcher's: `bash <x>_sidecar.sh <args>`."""
    script = tmp / "probe_sidecar.sh"
    script.write_text("sleep 90\n", encoding="utf-8")
    proc = subprocess.Popen([bash, posix(script)] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    return proc


def stop(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    proc.wait(timeout=30)


BASH = git_bash()
if BASH is None:
    bad("git bash", "no MSYS bash found")
    sys.exit(1)

# ---- 1-4. the real codex launcher against a live stand-in
tmp = Path(tempfile.mkdtemp(prefix="dup_launch_", dir=SCRATCH))
brief = tmp / "brief.md"
brief.write_text("review\n", encoding="utf-8")
other = tmp / "other.md"
other.write_text("other\n", encoding="utf-8")
env = launcher_env(tmp)
old_record = tmp / "old.record.json"
live = stand_in(BASH, tmp, ["-m", "model-a", "-f", str(brief).replace("\\", "/"), "-l", "old",
                            "-R", str(old_record).replace("\\", "/")])
try:
    proc = subprocess.run([BASH, posix(CODEX), "-l", "old", "-f", posix(brief)],
                          capture_output=True, text=True, timeout=180, env=env)
    check(proc.returncode == 13, "a relaunch of the job a live launcher runs (same label, same prompt) exits 13",
          "rc=%r err=%r" % (proc.returncode, proc.stderr[-600:]))
    check(str(live.pid) in proc.stderr and "reap.py" in proc.stderr and "age=" in proc.stderr,
          "... printing the live PID, its age and the reap command", proc.stderr[-600:])
    check(ended(ledger_rows(tmp, "old"), 13, "DUPLICATE LAUNCH refused"),
          "the duplicate refusal leaves one ended-before-result ledger row, exit 13", repr(ledger_rows(tmp, "old")))
    proc = subprocess.run([BASH, posix(CODEX), "-l", "new", "-f", posix(brief)],
                          capture_output=True, text=True, timeout=180, env=env)
    check(proc.returncode == 12 and "DUPLICATE" not in proc.stderr,
          "another job on the same prompt (its own label) passes the guard (stops at the planted memory floor, 12)",
          "rc=%r err=%r" % (proc.returncode, proc.stderr[-400:]))
    check(ended(ledger_rows(tmp, "new"), 12, "REFUSED memory headroom for new"),
          "... and leaves its memory refusal row, not a duplicate one", repr(ledger_rows(tmp, "new")))
    proc = subprocess.run([BASH, posix(CODEX), "-l", "new2", "-f", posix(other)],
                          capture_output=True, text=True, timeout=180, env=env)
    check(proc.returncode == 12 and "DUPLICATE" not in proc.stderr,
          "a launch with another prompt passes the guard (stops at the planted memory floor, 12)",
          "rc=%r err=%r" % (proc.returncode, proc.stderr[-400:]))
    check(ended(ledger_rows(tmp, "new2"), 12, "REFUSED memory headroom for new2"),
          "the memory refusal without -R leaves one ended-before-result ledger row, exit 12",
          repr(ledger_rows(tmp, "new2")))
    rec = old_record
    proc = subprocess.run([BASH, posix(CODEX), "-l", "dx", "-X", "-R", posix(rec), "-f", posix(brief)],
                          capture_output=True, text=True, timeout=180, env=env)
    check(proc.returncode == 13 and "DETACHED" not in proc.stdout and Path(str(rec) + ".pid").exists(),
          "a detached (-X) launch of the record a live launcher runs claims it, then refuses before it detaches",
          "rc=%r out=%r err=%r" % (proc.returncode, proc.stdout[-200:], proc.stderr[-400:]))
    exitf = Path(str(rec) + ".exit")
    check(ended(ledger_rows(tmp, "dx"), 13, "DUPLICATE LAUNCH refused")
          and exitf.exists() and exitf.read_text().strip() == "13",
          "... and leaves its .exit (13) and one ended-before-result ledger row",
          "rows=%r exit=%r" % (ledger_rows(tmp, "dx"), exitf.read_text() if exitf.exists() else None))
finally:
    stop(live)

race = r'''
set -uo pipefail
SC_TRANSPORT=codex
. "$LIB"
D="$1"
SC_X_RECORD="$D/race.record.json"; SC_X_PROMPT="$D/brief.md"; SC_X_RESUME=""; SC_X_LABEL=race
sc_check_duplicate_launch() {
  [ -e "$SC_X_RECORD.pid" ] || printf 'pid=999999\n' > "$SC_X_RECORD.pid"
  rc=13; exit 13
}
sc_detach_launch "$D/race_sidecar.sh"
'''
(tmp / "race.sh").write_text(race, encoding="utf-8")
proc = subprocess.run([BASH, posix(tmp / "race.sh"), posix(tmp)], capture_output=True, text=True, timeout=120,
                      env=dict(env, LIB=posix(LIB)))
race_pid = Path(str(tmp / "race.record.json") + ".pid")
race_exit = Path(str(tmp / "race.record.json") + ".exit")
check(not race_exit.exists() or (race_pid.exists() and "pid=999999" not in race_pid.read_text()),
      "a duplicate refusal writes .exit only on a record path its own launch claimed",
      "rc=%r pid=%r exit=%r err=%r" % (proc.returncode, race_pid.read_text() if race_pid.exists() else None,
                                       race_exit.read_text() if race_exit.exists() else None, proc.stderr[-300:]))

resumer = stand_in(BASH, tmp, ["-m", "model-a", "-r", "sess-j3-resume", "-l", "old-r"])
try:
    proc = subprocess.run([BASH, posix(CODEX), "-l", "new-r", "-r", "sess-j3-resume", "--", "go on"],
                          capture_output=True, text=True, timeout=180, env=env)
    check(proc.returncode == 13 and str(resumer.pid) in proc.stderr,
          "a resume of a session a live launcher resumes exits 13", "rc=%r err=%r" % (proc.returncode, proc.stderr[-400:]))
finally:
    stop(resumer)
proc = subprocess.run([BASH, posix(CODEX), "-l", "after", "-f", posix(brief)],
                      capture_output=True, text=True, timeout=180, env=env)
check(proc.returncode == 12, "with the old run stopped the same prompt launches (next gate, 12)",
      "rc=%r err=%r" % (proc.returncode, proc.stderr[-400:]))

broken = r'''
set -uo pipefail
. "$LIB"
D="$1"
SC_ROOT="$D/fakeroot"
mkdir -p "$SC_ROOT/tools"
printf '%s\n' 'import sys' 'sys.stderr.write("CHECKER BROKE\n")' "sys.exit($2)" > "$SC_ROOT/tools/reap.py"
sc_check_duplicate_launch "$D/brief.md" ""
echo LAUNCHING
'''
(tmp / "broken.sh").write_text(broken, encoding="utf-8")
for code, launches in ((1, False), (2, False), (4, True)):
    proc = subprocess.run([BASH, posix(tmp / "broken.sh"), posix(tmp), str(code)], capture_output=True, text=True,
                          timeout=120, env=dict(env, LIB=posix(LIB)))
    want = "the duplicate-launch checker's exit %d %s" % (code, "launches" if launches else "refuses (13) and prints its stderr")
    check((proc.returncode == 0 and "LAUNCHING" in proc.stdout) if launches
          else (proc.returncode == 13 and "LAUNCHING" not in proc.stdout and "CHECKER BROKE" in proc.stderr),
          want, "rc=%r out=%r err=%r" % (proc.returncode, proc.stdout[-200:], proc.stderr[-300:]))

# ---- 5-6. the launch id rides every process of the launch
snippet = r'''
set -uo pipefail
. "$LIB"
D="$1"
sc_parse_flags -l idproof -R "$D/id.record.json"
nohup python3 -c 'import os; print(os.environ.get("SIDECAR_LAUNCH_ID", ""))' > "$D/proxy.txt" 2>/dev/null &
p1=$!
nohup python3 -c 'import os; print(os.environ.get("SIDECAR_LAUNCH_ID", ""))' > "$D/shim.txt" 2>/dev/null &
p2=$!
wait $p1 $p2
sc_scrub_env
env ${SC_SCRUB[@]+"${SC_SCRUB[@]}"} python3 -c 'import os; print(os.environ.get("SIDECAR_LAUNCH_ID", ""))' > "$D/child.txt"
printf '%s\n' "$SC_LAUNCH_ID" > "$D/launch.txt"
'''
idtmp = Path(tempfile.mkdtemp(prefix="dup_launch_id_", dir=SCRATCH))
script = idtmp / "id.sh"
script.write_text(snippet, encoding="utf-8")
base = launcher_env(idtmp)
base["LIB"] = posix(LIB)
proc = subprocess.run([BASH, posix(script), posix(idtmp)], capture_output=True, text=True, timeout=120, env=base)
read = lambda name: (idtmp / name).read_text(encoding="utf-8").strip() if (idtmp / name).exists() else ""  # noqa: E731
launch = read("launch.txt")
check(proc.returncode == 0 and launch and read("proxy.txt") == launch and read("shim.txt") == launch
      and read("child.txt") == launch,
      "a direct launch's proxy, shim and child all carry its launch id",
      "rc=%r launch=%r proxy=%r shim=%r child=%r err=%r" % (proc.returncode, launch, read("proxy.txt"),
                                                            read("shim.txt"), read("child.txt"), proc.stderr[-300:]))
pid_text = read("id.record.json.pid")
check(launch and ("launch=%s" % launch) in pid_text, "<record>.pid names the launch id", repr(pid_text))

nested = r'''
. "$LIB"
sc_parse_flags -l nested
printf '%s\n' "$SC_LAUNCH_ID"
'''
(idtmp / "nested.sh").write_text(nested, encoding="utf-8")
outer, assigned = str(uuid.uuid4()), str(uuid.uuid4())
env_nested = dict(base, SIDECAR_LAUNCH_ID=outer, SIDECAR_SEAT_LAUNCH_ID=outer)
got = subprocess.run([BASH, posix(idtmp / "nested.sh")], capture_output=True, text=True, timeout=60,
                     env=env_nested).stdout.strip()
check(got and got != outer and str(uuid.UUID(got)) == got,
      "a launch nested in a seat takes a fresh id, not its seat's", repr(got))
env_assigned = dict(base, SIDECAR_LAUNCH_ID=assigned, SIDECAR_SEAT_LAUNCH_ID=outer)
got = subprocess.run([BASH, posix(idtmp / "nested.sh")], capture_output=True, text=True, timeout=60,
                     env=env_assigned).stdout.strip()
check(got == assigned, "a launch a fan-out assigned keeps its id", repr(got))

# ---- 7. opencode: the memory sample before the proxy
octmp = Path(tempfile.mkdtemp(prefix="dup_launch_oc_", dir=SCRATCH))
proc = subprocess.run([BASH, posix(OPENCODE), "-l", "oc-mem", "--", "hello"],
                      capture_output=True, text=True, timeout=180, env=launcher_env(octmp))
check(proc.returncode == 12 and "REFUSED memory headroom for oc-mem:" in proc.stderr
      and "starting litellm" not in proc.stderr,
      "an opencode launch below the floor exits 12 before any proxy starts",
      "rc=%r err=%r" % (proc.returncode, proc.stderr[-400:]))

# ---- 9. every launch that ends leaves one finish row
fintmp = Path(tempfile.mkdtemp(prefix="dup_launch_fin_", dir=SCRATCH))
fenv = launcher_env(fintmp)
frec = fintmp / "mem.record.json"
proc = subprocess.run([BASH, posix(CODEX), "-l", "memr", "-R", posix(frec), "--", "hello"],
                      capture_output=True, text=True, timeout=180, env=fenv)
fexit = Path(str(frec) + ".exit")
check(proc.returncode == 12 and ended(ledger_rows(fintmp, "memr"), 12, "REFUSED memory headroom for memr")
      and fexit.exists() and fexit.read_text().strip() == "12",
      "a foreground -R memory refusal writes its .exit (12) and one ended-before-result row",
      "rc=%r rows=%r err=%r" % (proc.returncode, ledger_rows(fintmp, "memr"), proc.stderr[-300:]))
proc = subprocess.run([BASH, posix(CODEX), "-l", "usage1", "-J", "--", "hello"],
                      capture_output=True, text=True, timeout=180, env=fenv)
check(proc.returncode == 2 and ended(ledger_rows(fintmp, "usage1"), 2, "bad usage"),
      "a usage error after the label is parsed leaves one ended-before-result row, exit 2",
      "rc=%r rows=%r" % (proc.returncode, ledger_rows(fintmp, "usage1")))
proc = subprocess.run([BASH, posix(CODEX), "-J", "-l", "usage2", "--", "hello"],
                      capture_output=True, text=True, timeout=180, env=fenv)
check(proc.returncode == 2 and ledger_rows(fintmp, "usage2") == [],
      "a usage error before any label is parsed writes no row", repr(ledger_rows(fintmp, "usage2")))
runs = r'''
set -uo pipefail
SC_TRANSPORT=codex
. "$LIB"
D="$1"; MODE="$2"
if [ "$MODE" = record ]; then
  sc_parse_flags -l published -R "$D/pub.record.json" -m luna -o json
  SC_MODEL=gpt-5.6-luna; SC_COST_MODEL=plan-quota
  OUTPUT='{"type":"result","subtype":"success","result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'
  rc=0
  sc_write_record "$OUTPUT" "$rc"
  exit $rc
fi
sc_parse_flags -l unrecorded -m luna -o json
SC_MEMORY_CHECKED=1
run_ok() { printf '%s\n' '{"type":"result","subtype":"success","result":"ok"}'; }
sc_run_watched run_ok ""
exit $rc
'''
(fintmp / "runs.sh").write_text(runs, encoding="utf-8")
renv = dict(fenv, LIB=posix(LIB))
proc = subprocess.run([BASH, posix(fintmp / "runs.sh"), posix(fintmp), "record"],
                      capture_output=True, text=True, timeout=120, env=renv)
pub = ledger_rows(fintmp, "published")
check(proc.returncode == 0 and len(pub) == 1 and "outcome" not in pub[0] and pub[0].get("exitCode") == 0,
      "a run that publishes leaves exactly its one ledger row, not a second finish row",
      "rc=%r rows=%r err=%r" % (proc.returncode, pub, proc.stderr[-300:]))
proc = subprocess.run([BASH, posix(fintmp / "runs.sh"), posix(fintmp), "plain"],
                      capture_output=True, text=True, timeout=120, env=renv)
unrec = ledger_rows(fintmp, "unrecorded")
check(proc.returncode == 0 and len(unrec) == 1 and unrec[0].get("outcome") == "finished-without-record"
      and unrec[0].get("exitCode") == 0,
      "a run without -R that finished leaves one finished-without-record row",
      "rc=%r rows=%r err=%r" % (proc.returncode, unrec, proc.stderr[-300:]))

# ---- 8. the fan-out refuses before it dispatches
import reap  # noqa: E402

real_exited, real_table = reap.process_exited, reap.process_table
import sidecar_fanout  # noqa: E402

prompt = "C:/x/fan/brief.md"
table = {5001: {"pid": 5001, "ppid": 1, "name": "bash.exe", "start": "2026-09-30 10:00:00",
                "cmd": "bash /c/x/.claude/scripts/codex_proxy_sidecar.sh -m model-a -f %s -l seat-a -X" % prompt}}
reap.process_table = lambda: dict(table)
reap.process_exited = lambda pid, start: False
reap.launch_environ = lambda pid: b"SIDECAR_LAUNCH_ID=old-launch\0" if pid == 5001 else b""
planned = [("seat-a", ["bash", "codex_proxy_sidecar.sh", "-m", "model-a", "-f", prompt, "-l", "seat-a"], "o.md"),
           ("seat-b", ["bash", "codex_proxy_sidecar.sh", "-m", "model-a", "-f", "C:/x/fan/other.md", "-l", "seat-b"], "p.md")]
finder = getattr(sidecar_fanout, "duplicate_launches", None)
text = finder(planned) if finder else None
check(bool(text) and "5001" in text and "seat-a" in text and "seat-b" not in text and "reap.py" in text,
      "the fan-out names a job whose prompt a live launcher runs, with the reap command", repr(text))
check(finder is not None and finder(planned[1:]) == "", "... and names nothing when no live process runs it")
sibling = [("seat-c", ["bash", "codex_proxy_sidecar.sh", "-m", "model-a", "-f", prompt, "-l", "seat-c",
                       "-R", "C:/x/fan/seat-c.record.json"], "q.md")]
check(finder is not None and finder(sibling) == "",
      "... nor a job that shares the live launcher's prompt under its own label and record")
check(sidecar_fanout.LAUNCHER_EXITS.get(13, "").startswith("duplicate launch"),
      "the fan-out's exit table names 13", repr(sidecar_fanout.LAUNCHER_EXITS.get(13)))

# ---- 10. a sibling's short-lived fork is gone by the time the scan inspects it
import contextlib  # noqa: E402
import io  # noqa: E402

fan_cmd = "bash .claude/scripts/anthropic_secondary_sidecar.sh -m sonnet -f %s -l probe-2 -R C:/x/fan/p2.record.json" % prompt
fork = {"pid": 5101, "ppid": 5100, "name": "bash.exe", "start": "2026-10-10 10:15:16", "cmd": fan_cmd}


def scan(exited, peb=b"", args=("--fanout", "fan-a")):
    """live-launch over a table holding one launcher fork whose environment reads `peb`; `exited` is the OS answer."""
    reap.process_table = lambda: {5101: dict(fork)}
    reap.launch_environ = lambda pid: peb
    reap.msys_environ = lambda pid: b""
    reap.process_exited = lambda pid, start: exited
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = reap.live_launch_main(["--prompt", prompt, "--self", "9"] + list(args))
    return code, buf.getvalue()


code, out = scan(True)
check(code == 0, "a sibling's fork that exited after the snapshot, its environment unreadable, is no live launch",
      "rc=%r out=%r" % (code, out[-300:]))
code, out = scan(False)
check(code == 3 and "5101" in out, "... while the same process still running and unreadable refuses",
      "rc=%r out=%r" % (code, out[-300:]))
ids = b"SIDECAR_LAUNCH_ID=l-2\0SIDECAR_FANOUT_ID=fan-a\0"
code, out = scan(False, peb=ids)
check(code == 0, "a live sibling that carries the fan-out id is exempt", "rc=%r out=%r" % (code, out[-300:]))
code, out = scan(False, peb=ids, args=("--fanout", "fan-b"))
check(code == 3 and "5101" in out, "a live launch of another fan-out is a duplicate", "rc=%r out=%r" % (code, out[-300:]))
code, out = scan(False, args=())
check(code == 3 and "5101" in out, "a live launcher outside any fan-out is a duplicate", "rc=%r out=%r" % (code, out[-300:]))

# The OS answer itself, on real processes: running, exited, and a pid the table read under another start time.
reap.process_exited = real_exited
sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
quick = subprocess.Popen([sys.executable, "-c", "pass"])
quick.wait(timeout=60)
try:
    listed = real_table().get(sleeper.pid, {}).get("start")
    check(bool(listed), "the process table lists a running process with its start time", repr(listed))
    check(real_exited(sleeper.pid, listed) is False, "process_exited: a running process has not exited")
    check(real_exited(sleeper.pid, "2001-01-01 00:00:00") is True,
          "process_exited: a pid the table read under another start time is a different process")
    check(real_exited(quick.pid, listed) is True, "process_exited: an exited process has exited")
    check(real_exited(0x7FFFFFF0, listed) is True, "process_exited: a pid no process holds has exited")
finally:
    sleeper.kill()
    sleeper.wait(timeout=30)

if ran < 22:
    bad("case coverage", "only %d cases reached an assertion" % ran)
print("%d cases, %s" % (ran, "all passed" if not fail else "FAILURES above"))
print("PASS test_sidecar_duplicate_launch" if not fail else "FAIL test_sidecar_duplicate_launch")
sys.exit(fail)
