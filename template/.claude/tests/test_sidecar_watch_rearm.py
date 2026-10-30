"""Prove killed-task rearm through the standalone hook with planted records."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
MEMBER = ROOT / "hooks" / "sidecar_watch_rearm.py"
SID = "rearmproof-0000-0000-0000-000000000000"
WATCH_COMMAND = "python3 .claude/tools/watch_records.py --session --wake"
GROUP = "running or queued (no live watcher):"
passed = 0


def check(ok, label, detail=""):
    global passed
    if not ok:
        raise AssertionError("%s: %s" % (label, detail))
    passed += 1
    print("PASS", label)


def process(pid):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + (
        "winpid=%d " % pid if os.name == "nt" else "") + "pid=%d" % pid


with tempfile.TemporaryDirectory(prefix="rearm_test_") as temp:
    root = Path(temp)
    state = root / "state"
    state.mkdir()
    timing = root / "timing.jsonl"
    record = root / "running.record.json"
    (root / "running.record.json.pid").write_text(process(os.getpid()))
    state_file = state / (SID[:8] + ".json")
    entry = {"kind": "sidecar", "id": str(record), "record": str(record), "launched_at": time.time(),
             "jobs": [{"label": "running", "launcher": "test"}]}
    state_file.write_text(json.dumps({"dispatch_table_pending": [entry]}))
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(state), HARNESS_PROMPT_DISPATCH_TIMING=str(timing),
               CLAUDE_PROJECT_DIR=str(root), HARNESS_GIT_STATE_CACHE_DIR=str(root / "git-state-cache"))

    def dispatch(status, prompt=None):
        if prompt is None:
            prompt = "<task-notification><status>%s</status></task-notification>" % status
        payload = {"session_id": SID, "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        run = subprocess.run([sys.executable, str(MEMBER)], input=json.dumps(payload), text=True,
                             capture_output=True, env=env, timeout=40)
        check(run.returncode == 0 and "fault" not in run.stderr and "timeout" not in run.stderr,
              "dispatcher stays healthy", run.stderr)
        if not run.stdout.strip():
            return ""
        return json.loads(run.stdout)["hookSpecificOutput"]["additionalContext"]

    # A completed notice wakes the session too: any other sidecar still running without a watcher is advised then.
    for notice in ("killed", "stopped", "completed"):
        output = dispatch(notice)
        check(WATCH_COMMAND in output and GROUP in output and str(record) in output,
              notice + " notice names the uncovered record and the wake route", output)
    check("watch_records.py" not in dispatch("failed"), "a notice status outside the wake set is silent")
    check("watch_records.py" not in dispatch("", "Owner asks a question"), "owner prompt is silent")

    state_file.write_text(json.dumps({"dispatch_table_pending": []}))
    check("watch_records.py" not in dispatch("killed"), "no pending sidecar is silent")

    watched = {"process": process(os.getpid()), "records": [str(record)]}
    state_file.write_text(json.dumps({"dispatch_table_pending": [entry], "sidecar_watchers": [watched]}))
    check("watch_records.py" not in dispatch("stopped"), "live watcher covers record")
    watched["process"] = process(99999999)
    state_file.write_text(json.dumps({"dispatch_table_pending": [entry], "sidecar_watchers": [watched]}))
    check(str(record) in dispatch("stopped"), "dead watcher no longer covers record")
    session_wide = {"process": process(os.getpid()), "records": [], "session": True}
    state_file.write_text(json.dumps({"dispatch_table_pending": [entry], "sidecar_watchers": [session_wide]}))
    check("watch_records.py" not in dispatch("stopped"), "a live session watcher covers the record")

    finished = root / "finished.record.json"
    (root / "finished.record.json.exit").write_text("3")
    dead = root / "dead.record.json"
    (root / "dead.record.json.pid").write_text(process(99999999))
    queued = root / "queued.record.json"
    never = root / "never.record.json"
    unknown = root / "unknown.record.json"
    for path, fanout_pid in ((queued, os.getpid()), (never, 99999999)):
        (root / ("fanout-" + path.stem + ".inventory.json")).write_text(json.dumps({
            "parentSessionId": SID, "fanoutProcess": process(fanout_pid),
            "jobs": [{"label": path.stem, "recordPath": str(path)}]}))
    pending = []
    for path in (finished, dead, queued, never, unknown):
        pending.append({**entry, "id": str(path), "record": str(path),
                        "jobs": [{"label": path.stem, "launcher": "test"}]})
    state_file.write_text(json.dumps({"dispatch_table_pending": pending}))
    output = dispatch("killed")
    for category, path in (("finished", finished), ("queued", queued), ("never started", never),
                           ("died", dead), ("unknown", unknown)):
        check(category in output.lower() and str(path) in output, category + " names its record", output)
    group = output.split(GROUP, 1)[-1]
    check(str(queued) in group and str(never) not in group,
          "only running and queued records enter the uncovered group", output)
    earlier = root / "retry.record.json"
    later = root / "retry.attempt2.record.json"
    (root / "retry.record.json.exit").write_text("0")
    (root / "retry.attempt2.record.json.pid").write_text(process(os.getpid()))
    (root / "fanout-retry.inventory.json").write_text(json.dumps({
        "parentSessionId": SID, "fanoutProcess": process(os.getpid()),
        "jobs": [{"label": "retry", "recordPath": str(later)}]}))
    retry_entry = {**entry, "record": None, "fanout_dir": str(root), "id": "retry-fanout",
                   "launched_at": time.time() - 1, "issued_at": time.time() - 1,
                   "jobs": [{"label": "retry", "launcher": "test"}]}
    state_file.write_text(json.dumps({"dispatch_table_pending": [retry_entry]}))
    retry_output = dispatch("killed")
    check(str(later) in retry_output and str(earlier) not in retry_output,
          "rearm resolves attempt two through the same inventory as Stop", retry_output)
    refused_dir = root / "refused"
    refused_dir.mkdir()
    refused, relaunched = refused_dir / "gate.record.json", refused_dir / "gate.attempt2.record.json"
    refused.write_text("")
    Path(str(refused) + ".exit").write_text("12")
    Path(str(relaunched) + ".pid").write_text(process(os.getpid()))
    issued = time.time() - 60
    for name, path, written in (("first", refused, issued + 1), ("relaunch", relaunched, issued + 30)):
        inventory = refused_dir / ("fanout-%s.inventory.json" % name)
        inventory.write_text(json.dumps({"parentSessionId": SID, "fanoutProcess": process(os.getpid()),
                                         "jobs": [{"label": "gate", "recordPath": str(path)}]}))
        os.utime(inventory, (written, written))
    refused_entry = {**entry, "record": str(refused), "inventory": str(refused_dir / "fanout-first.inventory.json"),
                     "fanout_dir": str(refused_dir), "id": "refused-fanout", "issued_at": issued,
                     "jobs": [{"label": "gate", "launcher": "test"}]}
    state_file.write_text(json.dumps({"dispatch_table_pending": [refused_entry]}))
    refused_output = dispatch("killed")
    check(str(relaunched) in refused_output.split(GROUP, 1)[-1] and str(refused) + " " not in refused_output
          and "exit=12" not in refused_output,
          "rearm follows a refused job's relaunch the way Stop and the watcher do", refused_output)
    check("watch_records.py" not in dispatch("", "hello " + "z" * (9 * 1024 * 1024)),
          "owner prompt does not scan a long transcript")
    state_file.write_text(json.dumps({"dispatch_table_pending": [entry]}) + '{"dispatch_table_pending": [tor')
    check(str(record) in dispatch("killed"), "a torn state file still advises from its first document")
    state_file.write_text(json.dumps({"dispatch_table_pending": [None, "junk", 3, [], entry]}))
    check(str(record) in dispatch("killed"), "non-dict pending entries are skipped, the real one advised")
    state_file.write_text("{not json")
    check("watch_records.py" not in dispatch("killed"), "an unrecoverable state file stays silent")

    launch = [json.loads(line) for line in (ROOT / "tests" / "fixtures" / "sidecar_watch_rearm"
                                             / "workflow_launch.jsonl").read_text(encoding="utf-8").splitlines()]
    result = next(block["content"] for row in launch for block in row["message"]["content"]
                  if isinstance(block, dict) and block.get("type") == "tool_result")
    result = result if isinstance(result, str) else result[0]["text"]
    run_id = re.search(r"Run ID: (\S+)", result).group(1)
    session_dir = root / "session"
    run_dir = session_dir / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "journal.jsonl").write_text("")
    workflow_entry = {"kind": "workflow", "id": run_id, "name": "dispatch", "run_dir": str(run_dir),
                      "launched_at": time.time(), "jobs": []}
    state_file.write_text(json.dumps({"dispatch_table_pending": [workflow_entry]}))
    output = dispatch("killed")
    check(WATCH_COMMAND in output and GROUP in output and str(run_dir) in output,
          "workflow-only session gets the re-arm reminder for its running run", output)
    watched = {"process": process(os.getpid()), "records": [], "session": True}
    state_file.write_text(json.dumps({"dispatch_table_pending": [workflow_entry], "sidecar_watchers": [watched]}))
    check("watch_records.py" not in dispatch("killed"), "a live session watcher covers the running workflow")
    state_file.write_text(json.dumps({"dispatch_table_pending": [workflow_entry]}))
    (session_dir / "workflows").mkdir()
    (session_dir / "workflows" / (run_id + ".json")).write_text(json.dumps({"status": "completed"}))
    check("watch_records.py" not in dispatch("killed"), "a workflow with a terminal host record is silent")

print("test_sidecar_watch_rearm: %d passed, 0 failed" % passed)
