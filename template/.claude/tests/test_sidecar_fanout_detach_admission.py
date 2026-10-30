#!/usr/bin/env python3
"""RED-first proof: a `--detach` fan-out tells its caller, before it returns, whether each job was admitted or
refused, and a capacity refusal that only the detached child found moves the job to its next plan.

The captured failure (2026-10-26): one haiku job detached on plan anthropic-secondary; the detached child
refused before any model call (`.exit` 10, `[sidecar] REFUSING haiku (claude-haiku-5-5): anthropic-secondary
five_hour window 98% used, at its 98% dispatch floor`), and the caller, told only `fan-out detached`, said the
job was running. The admission observable is `<label>.stream`: every launcher truncates its `-P` file right
after the last gate and before the model child, so a stream written since the launch means every pre-spend check
passed, and an `.exit` in PLAN_REFUSAL_EXITS with no such stream means nothing ran.

Fakes only: a fake launcher script plants `.pid`/`.err`/`.exit`/`.stream` as a detached wrapper would, and the
driver's liveness answer is planted. No process is a real launcher and nothing is billed.

Run: python3 .claude/tests/test_sidecar_fanout_detach_admission.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("sf_detach_admission", os.path.join(HERE, "..", "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_admit_")
REFUSAL = ("[sidecar] REFUSING haiku (claude-haiku-5-5): anthropic-secondary five_hour window 98% used, "
           "at its 98% dispatch floor")
PID_LINE = "2026-10-26T20:00:00Z launch=1 pid=4242\n"

FAKE = os.path.join(TMP, "fake_launcher.py")
with open(FAKE, "w", encoding="utf-8") as fh:
    fh.write(
        "import os, sys\n"
        "a = sys.argv\n"
        "rec = a[a.index('-R') + 1]\n"
        "mode = os.environ.get('FAKE_MODE') or os.path.basename(a[0]).split('.')[0]\n"
        "open(rec + '.pid', 'w').write('2026-10-26T20:00:00Z pid=4242\\n')\n"
        "open(os.path.join(os.path.dirname(rec), 'calls.log'), 'a').write(os.path.basename(a[0]) + ' ' + mode + '\\n')\n"
        "if mode == 'refuse':\n"
        "    open(rec + '.err', 'w').write(%r + '\\n')\n"
        "    open(rec + '.exit', 'w').write('10\\n')\n"
        "elif mode == 'limit':\n"
        "    open(a[a.index('-P') + 1], 'w').write('{}\\n')\n"
        "    open(rec + '.exit', 'w').write('10\\n')\n"
        "else:\n"
        "    open(a[a.index('-P') + 1], 'w').write('{}\\n')\n"
        "    open(rec + '.exit', 'w').write('0\\n')\n" % REFUSAL
    )


def launcher(name, mode):
    path = os.path.join(TMP, name + "_sidecar.sh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("import os, runpy, sys\nos.environ['FAKE_MODE'] = %r\nsys.argv[0] = %r\nrunpy.run_path(%r, run_name='__main__')\n"
                 % (mode, path, FAKE))
    return path


REFUSER = launcher("anthropic_secondary", "refuse")
LIMITER = launcher("anthropic_limit", "limit")
SPARE = launcher("anthropic", "admit")


def job_argv(out_dir, label, script):
    return [sys.executable, script, "-m", "haiku", "-e", "high", "-l", label,
            "-R", sf._posix(os.path.join(out_dir, label + ".record.json")),
            "-P", sf._posix(os.path.join(out_dir, label + ".stream")), "-X"]


def fresh():
    d = tempfile.mkdtemp(dir=TMP)
    out = os.path.join(d, "out")
    os.makedirs(out)
    return out


def drive(label, script, fallbacks):
    """sf.run_jobs over one detached job whose launcher is a fake; (result, stderr text, calls.log text)."""
    out = fresh()
    planned = [(label, job_argv(out, label, script), os.path.join(out, label + ".out.json"))]
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        results = sf.run_jobs(planned, 1, out, fallbacks={label: fallbacks} if fallbacks else None)
    calls = os.path.join(out, "calls.log")
    return results[label], err.getvalue(), open(calls, encoding="utf-8").read() if os.path.exists(calls) else ""


class Driver:
    pid = 1

    def poll(self):
        return None


def plant(out, label, *, pid=True, exit_code=None, err=None, stream=False, rec=None):
    rec = rec or os.path.join(out, label + ".record.json")
    if pid:
        open(rec + ".pid", "w").write(PID_LINE)
    if exit_code is not None:
        open(rec + ".exit", "w").write("%s\n" % exit_code)
    if err:
        open(rec + ".err", "w").write(err + "\n")
    if stream:
        open(os.path.join(out, label + ".stream"), "w").write("{}\n")
    return rec


def inventory(out, jobs):
    with open(os.path.join(out, "fanout-t.inventory.json"), "w") as fh:
        json.dump({"schemaVersion": 1, "jobs": [{"label": l, "recordPath": os.path.abspath(r)} for l, r in jobs]}, fh)


def watch(out, labels, fallbacks=None, alive=True, wait=1.5, **kw):
    """(printed text, outcome dict, seconds) for sf.await_admission over `labels` planted in `out`."""
    planned = [(l, job_argv(out, l, REFUSER), os.path.join(out, l + ".out.json")) for l in labels]
    real = sf._pid_alive
    sf._pid_alive = lambda pid: alive
    buf = io.StringIO()
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(buf):
            outcome = sf.await_admission(Driver(), out, planned, fallbacks or {}, 4, time.time() - 5,
                                         wait=wait, poll=0.05, settle=0.3, **kw)
    finally:
        sf._pid_alive = real
    return buf.getvalue(), outcome, time.time() - t0


CASES = []


def case(name, fn):
    CASES.append((name, fn))


def refused_child_with_spare_moves():
    res, err, calls = drive("job", REFUSER, [("anthropic", SPARE)])
    return (res["exit"] == 0 and "paying with plan anthropic instead" in err and res["preSpawnRefusal"] is False
            and calls.count("admit") == 1 and calls.count("refuse") == 1)


def refused_child_without_spare_stays_refused():
    res, err, calls = drive("job", REFUSER, [])
    return res["exit"] == 10 and res["preSpawnRefusal"] is True and calls.count("refuse") == 1 and "paying with" not in err


def usage_limit_after_spend_is_never_moved():
    res, err, calls = drive("job", LIMITER, [("anthropic", SPARE)])
    return res["exit"] == 10 and "paying with" not in err and calls.count("admit") == 0


def refused_child_prints_its_refusal_line():
    out = fresh()
    plant(out, "job", exit_code=10, err=REFUSAL)
    inventory(out, [("job", os.path.join(out, "job.record.json"))])
    text, outcome, _ = watch(out, ["job"])
    return ("[fanout] job: refused on anthropic-secondary, nothing ran: " + REFUSAL) in text and outcome["job"] == "refused"


def admitted_when_stream_exists_and_pid_lives():
    out = fresh()
    plant(out, "job", stream=True)
    inventory(out, [("job", os.path.join(out, "job.record.json"))])
    text, outcome, _ = watch(out, ["job"])
    return "[fanout] job: admitted on anthropic-secondary (haiku, high)" in text and outcome["job"] == "admitted"


def undecided_job_ends_the_wait_in_bound():
    out = fresh()
    plant(out, "job")
    inventory(out, [("job", os.path.join(out, "job.record.json"))])
    text, outcome, took = watch(out, ["job"], wait=0.6)
    return outcome["job"] == "undecided" and "admitted" not in text and "undecided" in text and took < 2.0


def stale_stream_before_launch_is_not_admission():
    out = fresh()
    plant(out, "job", stream=True)
    os.utime(os.path.join(out, "job.stream"), (1, 1))
    inventory(out, [("job", os.path.join(out, "job.record.json"))])
    _text, outcome, _ = watch(out, ["job"], wait=0.4)
    return outcome["job"] == "undecided"


def moved_job_is_reported_refused_then_admitted():
    out = fresh()
    first = plant(out, "job", exit_code=10, err=REFUSAL)
    inventory(out, [("job", first)])
    second = os.path.join(out, "job.record.2.json")

    def move():
        time.sleep(0.3)
        plant(out, "job", stream=True, rec=second)
        inventory(out, [("job", second)])

    threading.Thread(target=move).start()
    text, outcome, _ = watch(out, ["job"], fallbacks={"job": [("anthropic", SPARE)]}, wait=3)
    return (("refused on anthropic-secondary, nothing ran: " + REFUSAL) in text and "moved to anthropic" in text
            and "[fanout] job: admitted on anthropic " in text and outcome["job"] == "admitted")


def queued_job_says_why():
    out = fresh()
    plant(out, "a", stream=True)
    inventory(out, [("a", os.path.join(out, "a.record.json")), ("b", os.path.join(out, "b.record.json"))])
    text, outcome, took = watch(out, ["a", "b"], wait=5)
    return outcome == {"a": "admitted", "b": "queued"} and "[fanout] b: queued" in text and "--max-parallel 4" in text and took < 4


def dead_driver_is_reported():
    out = fresh()

    class Dead(Driver):
        def poll(self):
            return 3

    planned = [("job", job_argv(out, "job", REFUSER), os.path.join(out, "job.out.json"))]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        outcome = sf.await_admission(Dead(), out, planned, {}, 4, time.time() - 5, wait=2, poll=0.05, settle=0.1)
    return outcome["job"] == "undecided" and "driver exited 3" in buf.getvalue()


case("(b) a child refusal with no model call and a spare plan relaunches on the spare, and the driver log says so", refused_child_with_spare_moves)
case("a child refusal with no spare plan is not relaunched and stays a pre-spawn refusal", refused_child_without_spare_stays_refused)
case("exit 10 after the stream exists (usage limit mid-run) is never moved to another plan", usage_limit_after_spend_is_never_moved)
case("(a) a child's .exit 10 with a refusal in .err is reported `refused ... nothing ran: <line>`", refused_child_prints_its_refusal_line)
case("(c) a stream written since launch with a live pid is reported admitted, with plan, model and effort", admitted_when_stream_exists_and_pid_lives)
case("(d) a job that never decides ends the wait within its bound and is reported undecided, never admitted", undecided_job_ends_the_wait_in_bound)
case("a stream older than the launch is not an admission", stale_stream_before_launch_is_not_admission)
case("(b) a refused job the driver moved prints `moved to <plan>` and then its admission on the new plan", moved_job_is_reported_refused_then_admitted)
case("a job the driver has not started is reported queued with why, without waiting out the bound", queued_job_says_why)
case("a driver that exited before deciding is reported with its exit code", dead_driver_is_reported)

failed = []
for name, fn in CASES:
    try:
        ok = bool(fn())
    except Exception as exc:  # a crash is a failure, never a pass
        ok = False
        name += "  [%s: %s]" % (type(exc).__name__, exc)
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failed.append(name)
print("%d/%d passed" % (len(CASES) - len(failed), len(CASES)))
sys.exit(1 if failed else 0)
