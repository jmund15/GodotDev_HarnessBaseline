"""Proof that a watcher wake carries each ended job's result head, report path and verification counts.

Fixtures under tests/fixtures/wake_digest/ are trimmed captures of real producer output: a sidecar
`<record>.claims.json`, a Workflow host record and journal, a background shell's `<task-notification>`
transcript row and the output file it names (failed and clean). Cases plant them under a temp root and drive
`wake_digest` and the real `watch_records.py --session --wake`.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "wake_digest"
WATCH = ROOT / "tools" / "watch_records.py"
SID = "wakedigest-0000-0000-0000-000000000000"
SHELL_OFFSET_KEY = "sidecar_wake_shell_offset"
PASS = 0
# A hang bound, never a wait: every wait is for an observable event, and one poll of a live watcher under a
# 12-wide proof pool can take tens of seconds (its roster and pid probes spawn Git Bash and `ps`).
HANG_BOUND = 120
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "hooks"))
import _hook_state  # noqa: E402
import wake_digest  # noqa: E402
import watch_records  # noqa: E402


def check(condition, label, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s: %s" % (label, detail))
    PASS += 1
    print("PASS", label)


def stamp(pid):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


def notice_row(name, output_path):
    """The captured transcript row for `name`, with its output file path pointed at the planted copy."""
    row = json.loads((FIX / ("notice_%s.jsonl" % name)).read_text(encoding="utf-8"))
    recorded = wake_digest._background_tasks.ended_notices(row)[0]["output_file"]
    row["content"] = row["content"].replace(recorded, str(output_path))
    return json.dumps(row)


with tempfile.TemporaryDirectory(prefix="wake_digest_") as temp:
    root = Path(temp)

    # ---- sidecar: the captured claims file
    record = root / "job.record.json"
    shutil.copy(FIX / "sidecar.claims.json", str(record) + ".claims.json")
    captured = json.loads((FIX / "sidecar.claims.json").read_text(encoding="utf-8"))
    lines = wake_digest.sidecar_lines("job", record)
    text = "\n".join(lines)
    check(captured["result"][:40].split()[0] in text and "result:" in lines[0],
          "a sidecar digest opens with the claims file's result head", text[:300])
    counts = captured["verification"]["counts"]
    check("verification: %s" % captured["verification"]["status"] in text
          and "VERIFIED %d" % counts["VERIFIED"] in text and "UNVERIFIED %d" % counts["UNVERIFIED"] in text,
          "a sidecar digest carries the verification status and counts", text[:400])
    check(("report: %s.claims.json" % record) in text, "a direct launch's report is its claims file", text[-200:])
    check(len(lines[0]) <= wake_digest.HEAD_CHARS + 40, "the result head is bounded", str(len(lines[0])))
    bare = wake_digest.sidecar_lines("none", root / "absent.record.json")
    check("UNCHECKED" in "\n".join(bare) and "none" in bare[0], "no claims file reads as UNCHECKED, never a pass", str(bare))
    fan = root / "fan"
    fan.mkdir()
    (fan / "lens.md").write_text("# Lens report\nFINDING-ALPHA: the fan-out deliverable\n", encoding="utf-8")
    lens = wake_digest.sidecar_lines("lens", fan / "lens.record.json", fan)
    check("FINDING-ALPHA" in lens[0] and "report: %s" % (fan / "lens.md") in "\n".join(lens),
          "a fan-out job's digest reads its <label>.md and names it as the report", str(lens))
    # A merged seat's label carries `+`; the driver sanitizes it into the deliverable's name.
    (fan / "a_b.md").write_text("MERGED-SEAT: the sanitized deliverable\n", encoding="utf-8")
    merged = wake_digest.sidecar_lines("a+b", fan / "a+b.record.json", fan)
    check("MERGED-SEAT" in merged[0], "a merged seat's digest reads the deliverable under the driver's sanitized name",
          str(merged))
    # The fan-out driver writes <label>.md after the child's .exit (measured 0.2-0.6 s later), so a wake can read first.
    # The grace's own clock lands the deliverable during one poll, so no thread scheduler and no loaded machine decides it.
    late_text = "LATE-DELIVERABLE: written after the wake read\n"
    clock, real_monotonic, real_sleep = [0.0], wake_digest.time.monotonic, wake_digest.time.sleep

    def write_while_waiting(seconds):
        clock[0] += seconds
        (fan / "late.md").write_text(late_text, encoding="utf-8")

    wake_digest.time.monotonic, wake_digest.time.sleep = lambda: clock[0], write_while_waiting
    try:
        late = wake_digest.sidecar_lines("late", fan / "late.record.json", fan)
    finally:
        wake_digest.time.monotonic, wake_digest.time.sleep = real_monotonic, real_sleep
    check("LATE-DELIVERABLE" in late[0] and "report: %s" % (fan / "late.md") in "\n".join(late),
          "a fan-out digest waits a bounded grace for a <label>.md the driver is still writing", str(late))
    # A relaunch (`<label>.attempt2.record.json`) leaves the first attempt's <label>.md in place until the driver
    # rewrites it, so a deliverable older than this attempt's launch (its journal's `started` row) is not yet
    # this attempt's. The launch time is the row, never a `.pid` mtime.
    (fan / "redo.md").write_text("STALE-ATTEMPT-1: usage limit\n", encoding="utf-8")
    os.utime(fan / "redo.md", (time.time() - 60, time.time() - 60))
    with open(str(fan / "redo.attempt2.record.json") + ".journal.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "schema": 1, "event": "started", "pid": 1}) + "\n")
    clock[0] = 0.0

    def rewrite_while_waiting(seconds):
        clock[0] += seconds
        (fan / "redo.md").write_text("FRESH-ATTEMPT-2\n", encoding="utf-8")

    wake_digest.time.monotonic, wake_digest.time.sleep = lambda: clock[0], rewrite_while_waiting
    try:
        redo = wake_digest.sidecar_lines("redo", fan / "redo.attempt2.record.json", fan)
    finally:
        wake_digest.time.monotonic, wake_digest.time.sleep = real_monotonic, real_sleep
    check("FRESH-ATTEMPT-2" in redo[0], "a relaunch's digest waits for its own <label>.md, never the earlier attempt's",
          str(redo))
    wake_digest.DELIVERABLE_GRACE_SEC = 0.2
    clock[0] = 0.0

    def quiet_poll(seconds):
        clock[0] += seconds

    wake_digest.time.monotonic, wake_digest.time.sleep = lambda: clock[0], quiet_poll
    try:
        never = wake_digest.sidecar_lines("never", fan / "never.record.json", fan)
    finally:
        wake_digest.time.monotonic, wake_digest.time.sleep = real_monotonic, real_sleep
    check("none" in never[0] and clock[0] <= 0.3,
          "a fan-out job that writes no <label>.md reads none after the grace, not a hang", str(never))

    # ---- Workflow: the captured host record and journal
    host = json.loads((FIX / "workflow_host.json").read_text(encoding="utf-8"))
    journal_dir = root / "run"
    journal_dir.mkdir()
    shutil.copy(FIX / "workflow_journal.jsonl", journal_dir / "journal.jsonl")
    events = watch_records.read_journal(journal_dir)["events"]
    host["args"] = {"spillDir": str(root / "spill")}
    lines = wake_digest.workflow_lines(host, events)
    text = "\n".join(lines)
    check(lines[0].startswith("  result: ") and "fixes" in lines[0], "a Workflow digest opens with the host record's result head", text[:300])
    check("report: %s" % (root / "spill") in text, "a Workflow digest names the run's spillDir as its report", text[-200:])
    nohost = wake_digest.workflow_lines({"status": "completed"}, events)
    check("none on the host record" in nohost[0] and "journal" in nohost[0],
          "a host record without a result falls back to the journal's agent results", str(nohost))
    review = wake_digest.workflow_lines({"status": "completed", "result": {"findings": [], "counts": {"raw": 26, "merged": 23}}}, [])
    check("counts: raw 26, merged 23" in "\n".join(review), "a result's counts are the run's verification counts", str(review))

    # ---- shell: the captured notification rows and output files
    out_fail, out_ok = root / "bqx16aliu.output", root / "bn8d5fbv1.output"
    shutil.copy(FIX / "shell_fail.output", out_fail)
    shutil.copy(FIX / "shell_ok.output", out_ok)
    transcript = root / "transcript.jsonl"
    transcript.write_text('{"type":"user","content":"filler"}\n' + notice_row("fail", out_fail) + "\n"
                          + notice_row("ok", out_ok) + "\n", encoding="utf-8")
    scan = wake_digest.new_shell_scan()
    notices, end = wake_digest.shell_notices(str(transcript), scan, 0)
    check([n["task_id"] for n in notices] == ["bqx16aliu"],
          "a shell ending in the watcher's own terminal line is not a notice", str(notices))
    check(notices[0]["exit_code"] == 1 and notices[0]["status"] == "failed", "the notice parser reads status and exit code", str(notices[0]))
    shown = wake_digest.shell_text(notices[0])
    check("SHELL bqx16aliu failed exit=1" in shown and "[exited with code 1]" in shown and "1/1 exited 0" in shown,
          "a shell digest names its exit and its output tail", shown)
    long_out = root / "long.output"
    long_out.write_text("FAILED tests/test_x.py::case - AssertionError\n" + "".join("noise %d\n" % n for n in range(40))
                        + (FIX / "shell_fail.output").read_text(encoding="utf-8"), encoding="utf-8")
    long_shown = wake_digest.shell_text(dict(notices[0], output_file=str(long_out)))
    check("failure lines above the tail:" in long_shown and "FAILED tests/test_x.py::case" in long_shown
          and "noise 5" not in long_shown and "[exited with code 1]" in long_shown,
          "a failure line that scrolled above the tail is kept apart from the noise", long_shown)
    check(end == transcript.stat().st_size, "the scan resumes at the end of the last whole line", "%s %s" % (end, transcript.stat().st_size))
    again, end2 = wake_digest.shell_notices(str(transcript), scan, end)
    check(again == [] and end2 == end, "a scan from the saved offset reads nothing twice")
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(notice_row("fail", out_fail).replace("bqx16aliu", "bpartial00")[:-30])
    partial, end3 = wake_digest.shell_notices(str(transcript), scan, end)
    check(partial == [] and end3 == end, "a row still being written is left for the next scan")
    check(wake_digest.shell_notices(str(transcript), scan, 10 ** 9)[1] == transcript.stat().st_size,
          "an offset past the file's end (a replaced transcript) restarts at its end")
    gone = dict(notices[0], output_file=str(root / "missing.output"))
    check("output: unreadable" in wake_digest.shell_text(gone), "a missing output file reads as unreadable")

    # ---- running shells: one scan serves the notices and the launched set
    def launch(task, use, when):
        return json.dumps({"type": "user", "timestamp": when, "message": {"content": [
            {"type": "tool_result", "tool_use_id": use, "content": "started"}]}, "toolUseResult": {"backgroundTaskId": task}})

    def stopped(task, use):
        return json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": use, "content": "{}"}]}, "toolUseResult": {
                "message": "Successfully stopped task: %s (sleep 9)" % task, "task_id": task, "task_type": "local_bash"}})

    fresh = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - wake_digest.SHELL_LIVE_SECONDS - 600))
    live_path = root / "live.jsonl"
    live_path.write_text("\n".join([launch("bkeep0001", "u1", fresh), launch("bstop0001", "u2", fresh),
                                    stopped("bstop0001", "u3"), launch("bold00001", "u4", old)]) + "\n", encoding="utf-8")
    live_scan = wake_digest.new_shell_scan()
    check(wake_digest.running_shells(str(live_path), live_scan) == {"bkeep0001"},
          "a TaskStop result ends its shell and a launch older than the age cap is no live work")
    def call(use, command):
        return json.dumps({"type": "assistant", "timestamp": fresh, "message": {"content": [
            {"type": "tool_use", "id": use, "name": "Bash", "input": {"command": command}}]}})

    named_path = root / "named.jsonl"
    named_path.write_text("\n".join([
        call("w1", "cd /repo && python3 .claude/tools/watch_records.py --session --wake"), launch("bwatch001", "w1", fresh),
        call("p1", "python3 .claude/scripts/harness_tests.py --proofs-for .claude/tools/watch_records.py"),
        launch("bproof001", "p1", fresh)]) + "\n", encoding="utf-8")
    check(wake_digest.running_shells(str(named_path), wake_digest.new_shell_scan()) == {"bproof001"},
          "a shell that runs the watcher is no work; one that only names watch_records.py is")

    stale = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - wake_digest.PENDING_CALL_SECONDS - 60))
    pending_path = root / "pending.jsonl"
    pending_path.write_text("\n".join([
        call("w2", "python3 .claude/tools/watch_records.py --session --wake"),
        call("l1", "python3 .claude/tools/sidecar_fanout.py jobs.json --detach"),
        call("d1", "git status"), json.dumps({"type": "user", "timestamp": fresh, "message": {"content": [
            {"type": "tool_result", "tool_use_id": "d1", "content": "clean"}]}}),
        call("o1", "sleep 1").replace(fresh, stale)]) + "\n", encoding="utf-8")
    pending_scan = wake_digest.new_shell_scan()
    check(wake_digest.pending_calls(str(pending_path), pending_scan) == {"l1"},
          "a main-session call with no result yet is work in flight; a watcher's call, an answered call and one older "
          "than the foreground cap are not")
    with open(pending_path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(launch("bfan00001", "l1", fresh) + "\n")
    check(wake_digest.pending_calls(str(pending_path), pending_scan) == set()
          and wake_digest.running_shells(str(pending_path), pending_scan) == {"bfan00001"},
          "the call's result ends it as a pending call, and a background task it started is a running shell")

    before = live_path.stat().st_size
    with open(live_path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(notice_row("fail", out_fail) + "\n")
    found, _ = wake_digest.shell_notices(str(live_path), live_scan, before)
    check([n["task_id"] for n in found] == ["bqx16aliu"] and live_scan["offset"] == live_path.stat().st_size,
          "the notices and the running set come from one scan of the transcript")

    # ---- unrated labels: the captured Workflow host record as an unrated run in a session dir
    cfg = root / "cfg"
    session = cfg / "projects" / "proj" / SID
    (session / "workflows").mkdir(parents=True)
    (cfg / "projects" / "proj" / (SID + ".jsonl")).write_text('{"type":"user","content":"x"}\n', encoding="utf-8")
    probe = json.loads((FIX / "workflow_host.json").read_text(encoding="utf-8"))
    probe["runId"] = "wf_wakeprobe-000"
    (session / "workflows" / "wf_wakeprobe-000.json").write_text(json.dumps(probe), encoding="utf-8")
    saved = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg)
    try:
        line = wake_digest.unrated_line(SID)
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR") if saved is None else os.environ.__setitem__("CLAUDE_CONFIG_DIR", saved)
    check(line is not None and line.startswith("UNRATED ") and "wf_wakeprobe-000:fix:anchor-timeout" in line
          and "--rate-batch" in line, "the session's unrated dispatches arrive as one labelled line", str(line))

    # ---- the real watcher
    def env_for(case, extra=None):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, HARNESS_HOOK_STATE_DIR=str(case / "state"),
                   WATCH_RECORDS_POLL_SEC="0.05", CLAUDE_PROJECT_DIR=str(case),
                   WATCH_RECORDS_POLL_MARK=str(case / "polls.log"),
                   CLAUDE_CONFIG_DIR=str(case / "cfg"), BENCH_ROOTS=json.dumps({"BenchmarkArms": str(case / "no_arms")}))
        env.update(extra or {})
        return env

    def poll_count(case):
        """The polls a watcher on `case` has finished: the watcher appends one line per poll to the mark file."""
        try:
            return len((case / "polls.log").read_text(encoding="utf-8").splitlines())
        except OSError:
            return 0

    def wait_polls(case, count, proc=None, seconds=HANG_BOUND):
        """True once the watcher has finished `count` polls; False early when `proc` exits first."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if poll_count(case) >= count:
                return True
            if proc is not None and proc.poll() is not None:
                return False
            time.sleep(0.02)
        return poll_count(case) >= count

    def put_state(case, entries, extra=None):
        (case / "state").mkdir(exist_ok=True)
        _hook_state.write_json_atomic(str(case / "state" / (SID[:8] + ".json")),
                                      dict({"dispatch_table_pending": entries}, **(extra or {})))

    def state_of(case):
        # A live watcher renames a new copy over this file; an open during the rename is refused, and the hooks'
        # reader returns {} then.
        return _hook_state.read_json_salvage(str(case / "state" / (SID[:8] + ".json")))

    def arm(case, extra=None):
        (case / "polls.log").unlink(missing_ok=True)
        return subprocess.run([sys.executable, str(WATCH), "--session", "--wake"], capture_output=True, text=True,
                              timeout=HANG_BOUND, env=env_for(case, extra))

    def arm_quiet(case, extra=None):
        """A session watcher with no launch grace: with nothing live it prints what it owes and exits NONE."""
        return arm(case, dict({"DISPATCH_TABLE_LAUNCH_WAIT": "0", "DISPATCH_TABLE_UNRESOLVED_WAIT": "0"}, **(extra or {})))

    def sidecar_entry(label, rec, fanout=None):
        return {"kind": "sidecar", "id": str(rec), "record": str(rec), "fanout_dir": str(fanout) if fanout else None,
                "inventory": None, "launched_at": time.time(), "issued_at": time.time() - 1,
                "jobs": [{"label": label, "launcher": "proof"}]}

    def planted_transcript(case, *rows):
        path = case / "cfg" / "projects" / "proj" / (SID + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"type":"user","content":"filler"}\n' + "".join(r + "\n" for r in rows), encoding="utf-8")
        return path

    # a direct sidecar launch that ended: DONE line, then its claims digest
    case = root / "w_sidecar"
    case.mkdir()
    rec = case / "job.record.json"
    (case / "job.record.json.pid").write_text(stamp(99999999) + "\n")
    (case / "job.record.json.exit").write_text("0")
    shutil.copy(FIX / "sidecar.claims.json", str(rec) + ".claims.json")
    put_state(case, [sidecar_entry("job", rec)])
    done = arm(case)
    first = done.stdout.splitlines()
    check(done.returncode == 0 and first and first[0] == "DONE job exit=0", "the sidecar wake still opens with its DONE line", done.stdout[:300] + done.stderr[:300])
    check(first[1].startswith("  result: ") and any(l.startswith("  verification: pass (VERIFIED") for l in first)
          and any(l == "  report: %s.claims.json" % rec for l in first),
          "the wake prints the sidecar's result head, verification counts and report path under its DONE line", done.stdout[:600])
    check(done.stdout.rstrip().splitlines()[-1] in ("NONE", "STILL RUNNING 0"), "the wake still closes with its terminal line", done.stdout[-80:])
    again = arm_quiet(case)
    check(again.stdout.strip() == "NONE",
          "a clean job's digest prints once: the next arm omits it and exits NONE", again.stdout + again.stderr[:300])

    # a fan-out: each job's digest names its <label>.md
    case = root / "w_fanout"
    fan = case / "fan"
    fan.mkdir(parents=True)
    entries = []
    for label in ("la", "lb"):
        rec = fan / ("%s.record.json" % label)
        (fan / ("%s.record.json.pid" % label)).write_text(stamp(99999999) + "\n")
        (fan / ("%s.record.json.exit" % label)).write_text("0")
        (fan / ("%s.md" % label)).write_text("DELIVERABLE-%s first line\nmore\n" % label.upper(), encoding="utf-8")
        entries.append(dict(sidecar_entry(label, rec, fan), id="%s:%s:call" % (fan, label)))
    inventory = fan / "fanout-proof.inventory.json"
    inventory.write_text(json.dumps({"parentSessionId": SID, "fanoutId": "proof", "fanoutProcess": stamp(99999999),
                                     "jobs": [{"recordPath": str(fan / ("%s.record.json" % l)), "label": l} for l in ("la", "lb")]}))
    for entry in entries:
        entry["inventory"] = str(inventory)
    put_state(case, entries)
    done = arm(case)
    check("DELIVERABLE-LA" in done.stdout and "DELIVERABLE-LB" in done.stdout
          and "report: %s" % (fan / "la.md") in done.stdout and "report: %s" % (fan / "lb.md") in done.stdout,
          "a fan-out wake prints every job's deliverable head and path", done.stdout[:800] + done.stderr[:300])

    # a Workflow run that ended
    case = root / "w_workflow"
    session_dir = case / "sess"
    run_dir = session_dir / "subagents" / "workflows" / "wf_c9e94863-f44"
    run_dir.mkdir(parents=True)
    shutil.copy(FIX / "workflow_journal.jsonl", run_dir / "journal.jsonl")
    (session_dir / "workflows").mkdir()
    shutil.copy(FIX / "workflow_host.json", session_dir / "workflows" / "wf_c9e94863-f44.json")
    put_state(case, [{"kind": "workflow", "id": "wf_c9e94863-f44", "task_id": "t-wf", "launched_at": time.time() - 125,
                      "name": "proofflow", "run_dir": str(run_dir), "reported": [], "declared": {}, "jobs": []}])
    done = arm_quiet(case)
    check("DONE workflow proofflow wf_c9e94863-f44 status=completed" in done.stdout and "  result: " in done.stdout
          and "fixes" in done.stdout and done.stdout.rstrip().endswith("NONE"),
          "an ended Workflow run's arm carries its status and result head, then exits NONE", done.stdout[:700] + done.stderr[:300])

    # the digest is acknowledged when a wake exits, so "once" holds across a wake: a clean job's end wakes with it
    case = root / "w_workflow_wake"
    run_dir = case / "sess" / "subagents" / "workflows" / "wf_c9e94863-f44"
    run_dir.mkdir(parents=True)
    shutil.copy(FIX / "workflow_journal.jsonl", run_dir / "journal.jsonl")
    (case / "sess" / "workflows").mkdir()
    shutil.copy(FIX / "workflow_host.json", case / "sess" / "workflows" / "wf_c9e94863-f44.json")
    rec = case / "job.record.json"
    (case / "job.record.json.pid").write_text(stamp(99999999) + "\n")
    (case / "job.record.json.exit").write_text("0")
    put_state(case, [{"kind": "workflow", "id": "wf_c9e94863-f44", "task_id": "t-wf", "launched_at": time.time() - 125,
                      "name": "proofflow", "run_dir": str(run_dir), "reported": [], "declared": {}, "jobs": []},
                     sidecar_entry("job", rec)])
    done = arm(case)
    check(done.returncode == 0 and "DONE job exit=0" in done.stdout and "DONE workflow proofflow" in done.stdout
          and "  result: " in done.stdout, "a wake that exits carries the ended Workflow run's digest", done.stdout[:700] + done.stderr[:300])
    again = arm_quiet(case)
    check(again.stdout.strip() == "NONE", "an ended Workflow run's digest prints once: the next arm omits it",
          again.stdout[:500])

    # background shells: planted rows from the captured notification, resumed from the saved offset
    case = root / "w_shell"
    case.mkdir()
    out_fail = case / "bqx16aliu.output"
    shutil.copy(FIX / "shell_fail.output", out_fail)
    rec = case / "gate.record.json"
    (case / "gate.record.json.pid").write_text(stamp(99999999) + "\n")
    (case / "gate.record.json.exit").write_text("1")
    transcript = planted_transcript(case, notice_row("fail", out_fail))
    put_state(case, [sidecar_entry("gate", rec)], {SHELL_OFFSET_KEY: 0})
    done = arm(case)
    check("SHELL bqx16aliu failed exit=1" in done.stdout and "  tail:" in done.stdout and "[exited with code 1]" in done.stdout,
          "a shell that ended since the saved offset arrives in the wake with its output tail", done.stdout[:900] + done.stderr[:300])
    check(state_of(case).get(SHELL_OFFSET_KEY) == transcript.stat().st_size,
          "the wake saves the transcript offset it scanned to", str(state_of(case).get(SHELL_OFFSET_KEY)))
    check("SHELL" not in arm_quiet(case).stdout, "the next wake does not repeat that shell")

    case = root / "w_shell_fresh"
    case.mkdir()
    out_fail = case / "bqx16aliu.output"
    shutil.copy(FIX / "shell_fail.output", out_fail)
    rec = case / "gate.record.json"
    (case / "gate.record.json.pid").write_text(stamp(99999999) + "\n")
    (case / "gate.record.json.exit").write_text("0")
    planted_transcript(case, notice_row("fail", out_fail))
    put_state(case, [sidecar_entry("gate", rec)])
    done = arm(case)
    check("SHELL" not in done.stdout and SHELL_OFFSET_KEY in state_of(case),
          "a first arm with no saved offset starts at the transcript's end instead of scanning its history", done.stdout)

    # a shell ending while the watcher runs is held and printed with the wake that follows
    case = root / "w_shell_live"
    case.mkdir()
    out_fail = case / "bqx16aliu.output"
    shutil.copy(FIX / "shell_fail.output", out_fail)
    rec = case / "gate.record.json"
    (case / "gate.record.json.pid").write_text(stamp(os.getpid()) + "\n")
    transcript = planted_transcript(case)
    put_state(case, [sidecar_entry("gate", rec)], {SHELL_OFFSET_KEY: transcript.stat().st_size})
    (case / "polls.log").unlink(missing_ok=True)
    watcher = subprocess.Popen([sys.executable, str(WATCH), "--session", "--wake"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env_for(case))
    check(wait_polls(case, 2, proc=watcher), "the watcher polled from the saved transcript offset")
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(notice_row("fail", out_fail) + "\n")
    check(wait_polls(case, 4, proc=watcher), "the watcher polled twice after the shell ending landed")
    check(watcher.poll() is None, "a shell ending alone does not wake the watcher")
    (case / "gate.record.json.exit").write_text("2")
    stdout, stderr = watcher.communicate(timeout=HANG_BOUND)
    check("DONE gate exit=2" in stdout and "SHELL bqx16aliu failed exit=1" in stdout,
          "the shell held during the watch prints with the wake that ends it", stdout[:900] + stderr[:300])

    # a running Bash background task keeps the watcher live, so its check-in fires with no records at all
    def launch_rows(command=None):
        use, result = (FIX / "launch_bash.jsonl").read_text(encoding="utf-8").splitlines()
        now_stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        return [(use.replace("sleep 900", command) if command else use).replace("2026-10-10T15:31:21.312Z", now_stamp),
                result.replace("2026-10-10T15:33:21.977Z", now_stamp)]

    case = root / "w_shell_running"
    case.mkdir()
    put_state(case, [])
    planted_transcript(case, *launch_rows())
    done = arm(case, {"WATCH_RECORDS_CHECKIN_SEC": "0.3"})
    check("CHECKIN after" in done.stdout and "STILL RUNNING 1" in done.stdout
          and done.stdout.rstrip().rsplit("\n", 1)[-1].startswith("RE-ARM NOW:"),
          "a running Bash task with no records arms a check-in wake", done.stdout[-400:] + done.stderr[:300])

    case = root / "w_shell_running_watcher"
    case.mkdir()
    put_state(case, [])
    planted_transcript(case, *launch_rows("python3 .claude/tools/watch_records.py --session --wake"))
    done = arm_quiet(case, {"WATCH_RECORDS_CHECKIN_SEC": "0.3"})
    check(done.stdout.strip() == "NONE", "a watcher's own shell is not live work: no check-in, the watcher exits NONE",
          done.stdout[-300:] + done.stderr[:300])

    case = root / "w_shell_running_ended"
    case.mkdir()
    put_state(case, [])
    ended = notice_row("fail", case / "bqx16aliu.output").replace("bqx16aliu", "bpkaizzug")
    planted_transcript(case, *launch_rows(), ended)
    done = arm_quiet(case, {"WATCH_RECORDS_CHECKIN_SEC": "0.3"})
    check("CHECKIN" not in done.stdout and "SHELL" not in done.stdout and done.stdout.rstrip().endswith("NONE"),
          "a Bash task whose end notice arrived is not live work", done.stdout[-300:] + done.stderr[:300])

    # unrated labels close the wake
    case = root / "w_unrated"
    session_dir = case / "cfg" / "projects" / "proj" / SID
    (session_dir / "workflows").mkdir(parents=True)
    (case / "cfg" / "projects" / "proj" / (SID + ".jsonl")).write_text('{"type":"user","content":"x"}\n', encoding="utf-8")
    probe = json.loads((FIX / "workflow_host.json").read_text(encoding="utf-8"))
    probe["runId"] = "wf_wakeprobe-001"
    (session_dir / "workflows" / "wf_wakeprobe-001.json").write_text(json.dumps(probe), encoding="utf-8")
    rec = case / "job.record.json"
    (case / "job.record.json.pid").write_text(stamp(99999999) + "\n")
    (case / "job.record.json.exit").write_text("0")
    put_state(case, [sidecar_entry("job", rec)])
    done = arm(case)
    rows = done.stdout.splitlines()
    check(any(l.startswith("UNRATED ") and "wf_wakeprobe-001:fix:anchor-timeout" in l for l in rows) and rows[-1] in ("NONE", "STILL RUNNING 0"),
          "the wake lists the session's unrated dispatch labels before its terminal line", done.stdout[-700:] + done.stderr[:300])

print("test_wake_digest: %d passed, 0 failed" % PASS)
