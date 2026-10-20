#!/usr/bin/env python3
"""Proof for tools/dispatch_result.py: every dispatch job's full deliverable at `<outDir>/<label>.md`.

A Workflow run's jobs that could not spill (no-write agent types, or a run without spillDir) have
their final message only in the run's `journal.jsonl`. The tool writes it beside the spilled ones,
leaves a job that spilled alone, and never writes a spill digest in place of a missing spill file.
A sidecar fan-out dir gets the same file from each job's captured stdout. Every case runs the CLI
through a subprocess against a planted session layout.

Run: python3 .claude/tests/test_dispatch_result.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "dispatch_result.py")
TMP = tempfile.mkdtemp(prefix="dispatch_result_")


def cli(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr


def plant_run(name, events, record=None):
    """<TMP>/<name>/<sid>/subagents/workflows/<runId>/journal.jsonl plus the sibling run record
    <TMP>/<name>/<sid>/workflows/<runId>.json, the layout the Workflow runtime writes."""
    sid = os.path.join(TMP, name, "sid")
    run = os.path.join(sid, "subagents", "workflows", "wf_test-1")
    os.makedirs(run)
    with open(os.path.join(run, "journal.jsonl"), "w", encoding="utf-8") as fh:
        fh.write('{"type":"launched"}\n')
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    if record is not None:
        os.makedirs(os.path.join(sid, "workflows"))
        json.dump(record, open(os.path.join(sid, "workflows", "wf_test-1.json"), "w", encoding="utf-8"))
    return run


def started(label, agent):
    return {"type": "started", "key": "k-" + agent, "agentId": agent, "label": label, "phase": "Dispatch"}


def result(agent, text):
    return {"type": "result", "key": "k-" + agent, "agentId": agent, "result": text}


def read(path):
    return open(path, encoding="utf-8").read() if os.path.isfile(path) else None


CASES = []


def case(name, fn):
    CASES.append((name, fn))


# ---- a Workflow run with a spillDir ------------------------------------------------------------
SPILL = os.path.join(TMP, "spill")
os.makedirs(SPILL)
open(os.path.join(SPILL, "author.md"), "w", encoding="utf-8").write("the author's own full spill\n")
RUN = plant_run("wf", [
    started("scout", "a1"), result("a1", "scout full deliverable\nline 2"),
    started("author", "a2"), result("a2", "digest only\ncouldNotSatisfy: none\nFULL: " + SPILL + "/author.md"),
    started("lost", "a3"), result("a3", "digest\nFULL: " + SPILL + "/lost.md"),
    started("odd label/x", "a4"), result("a4", {"structured": True}),
    started("dead", "a5"),
], record={"runId": "wf_test-1", "args": {"spillDir": SPILL}, "result": {"spillDir": SPILL}})
_code, _out, _err = cli(RUN)

case("a no-write job's journal result lands at <spillDir>/<label>.md",
     lambda: read(os.path.join(SPILL, "scout.md")) == "scout full deliverable\nline 2")
case("a job that spilled is left alone",
     lambda: read(os.path.join(SPILL, "author.md")) == "the author's own full spill\n")
case("a spill digest is never written in place of a missing spill file",
     lambda: not os.path.exists(os.path.join(SPILL, "lost.md")) and "lost" in _out)
case("a label is sanitized the way dispatch.js names its spill file",
     lambda: os.path.isfile(os.path.join(SPILL, "odd_label_x.md")))
case("a structured result is written as JSON",
     lambda: json.loads(read(os.path.join(SPILL, "odd_label_x.md")) or "null") == {"structured": True})
case("a job with no result is reported, not written",
     lambda: not os.path.exists(os.path.join(SPILL, "dead.md")) and "dead" in _out)
case("the tool prints each written path",
     lambda: os.path.join(SPILL, "scout.md").replace("\\", "/") in _out.replace("\\", "/"))
case("a job left without a deliverable exits 1",
     lambda: _code == 1)

# ---- a run without spillDir needs --out-dir ----------------------------------------------------
BARE = plant_run("bare", [started("solo", "b1"), result("b1", "solo text")],
                 record={"runId": "wf_test-1", "args": {}, "result": {"solo": "solo text"}})
_bare_code, _bare_out, _bare_err = cli(BARE)
case("a run with no spillDir and no --out-dir is unusable (exit 2)",
     lambda: _bare_code == 2 and "--out-dir" in _bare_err)
BARE_OUT = os.path.join(TMP, "bare_out")
_bo_code, _bo_out, _ = cli(BARE, "--out-dir", BARE_OUT)
case("--out-dir writes every job and exits 0",
     lambda: _bo_code == 0 and read(os.path.join(BARE_OUT, "solo.md")) == "solo text")
_again_code, _again_out, _ = cli(BARE, "--out-dir", BARE_OUT)
case("a second run keeps the written file and still exits 0",
     lambda: _again_code == 0 and read(os.path.join(BARE_OUT, "solo.md")) == "solo text")

# ---- a sidecar fan-out dir ---------------------------------------------------------------------
FAN = os.path.join(TMP, "fan")
os.makedirs(FAN)
with open(os.path.join(FAN, "a.out.json"), "w", encoding="utf-8") as fh:
    fh.write('{"type":"system"}\n{"type":"result","subtype":"success","result":"fan a final"}\n')
with open(os.path.join(FAN, "b.out.json"), "w", encoding="utf-8") as fh:
    fh.write('{"type":"result","result":"fan b final"}')
open(os.path.join(FAN, "b.md"), "w", encoding="utf-8").write("already here")
_fan_code, _fan_out, _ = cli(FAN)
case("a fan-out dir gets <label>.md from each job's stdout result",
     lambda: _fan_code == 0 and read(os.path.join(FAN, "a.md")) == "fan a final")
case("a fan-out job's existing <label>.md is kept",
     lambda: read(os.path.join(FAN, "b.md")) == "already here")

EMPTY = os.path.join(TMP, "empty")
os.makedirs(EMPTY)
_empty = cli(EMPTY)
case("a dir with neither a journal nor job stdout is unusable (exit 2, named)",
     lambda: _empty[0] == 2 and "journal.jsonl" in _empty[2])
_missing = cli(os.path.join(TMP, "nope"))
case("a missing dir is unusable (exit 2, named)",
     lambda: _missing[0] == 2 and "not a directory" in _missing[2])

# ---- a write that dies midway never leaves a deliverable that reads as finished ----------------
sys.path.insert(0, os.path.dirname(TOOL))
import dispatch_result  # noqa: E402

ATOMIC = os.path.join(TMP, "atomic")


def _write_dies(label, text="torn \ud800 text"):
    try:
        dispatch_result.write_deliverable(ATOMIC, label, text)
    except UnicodeEncodeError:
        return True
    return False


dispatch_result.write_deliverable(ATOMIC, "kept", "complete earlier text\n")
_dies_over = _write_dies("kept")
_dies_new = _write_dies("fresh")
case("a failed rewrite leaves the earlier deliverable intact",
     lambda: _dies_over and read(os.path.join(ATOMIC, "kept.md")) == "complete earlier text\n")
case("a failed first write leaves no <label>.md for a later pass to report as kept",
     lambda: _dies_new and not os.path.exists(os.path.join(ATOMIC, "fresh.md")))
case("a failed write leaves no temp file behind",
     lambda: sorted(os.listdir(ATOMIC)) == ["kept.md"])
case("a successful write is UTF-8 with LF endings",
     lambda: dispatch_result.write_deliverable(ATOMIC, "lf", "a\nb\n") and
     open(os.path.join(ATOMIC, "lf.md"), "rb").read() == b"a\nb\n")


# ---- check: fenced JSON Lines blocks in a spill ------------------------------------------------
CHECKS = os.path.join(TMP, "checks")
os.makedirs(CHECKS)


def plant_spill(name, text):
    path = os.path.join(CHECKS, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


_FENCE = "`" * 3
_CLEAN = ("prose\n" + _FENCE + "rules\n" + '{"id":"a","line":1}\n\n' + '{"id":"b","line":2}\n' + _FENCE + "\n"
          + _FENCE + "numbers\n" + '{"token":"5"}\n' + _FENCE + "\n")
_ARGS = ["--block", "rules=id,line", "--block", "numbers=token"]
_clean_path = plant_spill("clean.md", _CLEAN)
_clean = cli("check", _clean_path, *_ARGS)
_short = cli("check", plant_spill("short.md", _CLEAN.replace('{"id":"b","line":2}', '{"id":"b"}')), *_ARGS)
_nojson = cli("check", plant_spill("nojson.md", _CLEAN.replace('{"id":"b","line":2}', "not json")), *_ARGS)
_noblock = cli("check", plant_spill("noblock.md", _CLEAN.split(_FENCE + "numbers")[0]), *_ARGS)
_nonobj = cli("check", plant_spill("nonobj.md", _CLEAN.replace('{"token":"5"}', "[1]")), *_ARGS)
_twice = cli("check", plant_spill("twice.md", _CLEAN + _FENCE + "rules\n" + '{"id":"c","line":3}\n' + _FENCE + "\n"), *_ARGS)
_absent = cli("check", os.path.join(CHECKS, "absent.md"), *_ARGS)
_badspec = cli("check", _clean_path, "--block", "rules")
_noargs = cli("check", _clean_path)

case("a clean spill exits 0 with a row count per block",
     lambda: _clean[0] == 0 and "rules: 2 rows" in _clean[1] and "numbers: 1 rows" in _clean[1])
case("a row missing a key exits 1 naming block, row and key",
     lambda: _short[0] == 1 and "rules row 2: missing ['line']" in _short[1])
case("a row that is not JSON exits 1 naming the row",
     lambda: _nojson[0] == 1 and "rules row 2: not JSON" in _nojson[1])
case("a row that is JSON but not an object exits 1",
     lambda: _nonobj[0] == 1 and "numbers row 1: not a JSON object" in _nonobj[1])
case("a missing block exits 1 naming it",
     lambda: _noblock[0] == 1 and "MISSING block numbers" in _noblock[1])
case("a block name fenced twice is read as one block",
     lambda: _twice[0] == 0 and "rules: 3 rows" in _twice[1])
case("an unreadable spill exits 2",
     lambda: _absent[0] == 2)
case("a --block without keys and a check with no --block exit 2",
     lambda: _badspec[0] == 2 and _noargs[0] == 2)


# ---- consume: one label of a Workflow run, its checks, changed paths and diff ------------------
FIXTURE_SESSION = os.path.join(HERE, "fixtures", "workflow_replay", "session")
FIXTURE_RUN_ID = "wf_7c3b636b-5e2"
START_MS = 1791559396108  # the replay fixture's startedAt for its first agent


def script(name, body):
    """A planted Python file a check runs, as the posix path its bash shell needs."""
    path = os.path.join(TMP, "scripts", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return path.replace("\\", "/")


def plant_consume(name, text, task, *, label="w1", status="completed", brief_age=-120, brief_body=None):
    """A Workflow run dir built from the replay fixture's run record: one dispatch.js job `label`
    whose brief holds `task` and whose journal result is `text`. The brief's mtime sits
    `brief_age` seconds from the run start. Returns (run dir, spill dir, brief path)."""
    root = os.path.join(TMP, name)
    sid = os.path.join(root, "sid")
    run = os.path.join(sid, "subagents", "workflows", FIXTURE_RUN_ID)
    os.makedirs(run)
    spill = os.path.join(root, "spill")
    os.makedirs(spill)
    brief = os.path.join(root, "brief.md")
    if brief_body is None:
        brief_body = "Brief prose.\n" + ("" if task is None else "```task\n" + json.dumps(task) + "\n```\n")
    with open(brief, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(brief_body)
    os.utime(brief, (START_MS / 1000 + brief_age, START_MS / 1000 + brief_age))
    with open(os.path.join(run, "journal.jsonl"), "w", encoding="utf-8") as fh:
        for ev in ({"type": "launched"}, started(label, "c1"), result("c1", text)):
            fh.write(json.dumps(ev) + "\n")
    with open(os.path.join(FIXTURE_SESSION, "workflows", FIXTURE_RUN_ID + ".json"), encoding="utf-8") as fh:
        record = json.load(fh)
    record.update(status=status, result=None,
                  args={"jobs": [{"label": label, "promptPath": brief, "agentType": "lane-author"}],
                        "spillDir": spill},
                  workflowProgress=[{"type": "workflow_agent", "index": 1, "label": label, "agentId": "c1",
                                     "state": "done", "startedAt": START_MS, "queuedAt": START_MS - 1}])
    os.makedirs(os.path.join(sid, "workflows"))
    with open(os.path.join(sid, "workflows", FIXTURE_RUN_ID + ".json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return run, spill, brief


def consume(run, label, *extra, timeout="20"):
    return cli("consume", run, "--label", label, "--check-timeout", timeout, *extra)


PY_OK = "python3 -c \"print('check ran')\""
_ok_run, _ok_spill, _ = plant_consume("pass", "done\n", {"check": [PY_OK]})
_pass = consume(_ok_run, "w1")
case("consume prints PASS for a passing check and exits 0",
     lambda: _pass[0] == 0 and "CHECK 1 PASS" in _pass[1])
_bare = consume(plant_consume("strcheck", "done\n", {"check": PY_OK})[0], "w1")
case("a check given as one string runs as a one-entry list",
     lambda: _bare[0] == 0 and "CHECK 1 PASS" in _bare[1])
case("consume writes the label's deliverable to the spill dir",
     lambda: read(os.path.join(_ok_spill, "w1.md")) == "done\n")
case("consume's output holds no rating, verdict or commit text",
     lambda: "CHECK 1 PASS" in _pass[1] and not any(word in _pass[1] for word in ("RATED", "clean", "defects", "git commit")))

_boom = script("boom.py", "import sys\nprint('boom')\nsys.exit(3)\n")
_fail = consume(plant_consume("fail", "done\n", {"check": ["python3 " + _boom]})[0], "w1")
case("consume prints FAIL with the output tail for a failing check and exits 1",
     lambda: _fail[0] == 1 and "CHECK 1 FAIL" in _fail[1] and "boom" in _fail[1])

_sleep = script("sleep30.py", "import time\ntime.sleep(30)\n")
_timeout = consume(plant_consume("timeout", "done\n", {"check": ["python3 " + _sleep]})[0], "w1", timeout="2")
case("a check past --check-timeout reports TIMEOUT, never a pass, and exits 1",
     lambda: _timeout[0] == 1 and "TIMEOUT" in _timeout[1] and "PASS" not in _timeout[1])

GRAND_MARK = os.path.join(TMP, "grandchild.marker")
_grand_child = script("grand.py", "import time\ntime.sleep(4)\nopen(%r, 'w').write('x')\n" % GRAND_MARK)
_spawner = script("spawn.py", "import subprocess, sys, time\nsubprocess.Popen([sys.executable, %r])\n"
                  "time.sleep(30)\n" % _grand_child)
_grand = consume(plant_consume("grand", "done\n", {"check": ["python3 " + _spawner]})[0], "w1", timeout="2")
time.sleep(5)
case("a check whose grandchild outlives the deadline leaves no process that writes after it",
     lambda: _grand[0] == 1 and "TIMEOUT" in _grand[1] and not os.path.exists(GRAND_MARK))

HELD_MARK = os.path.join(TMP, "held.marker")
_held_mark = script("held_mark.py", "open(%r, 'w').write('x')\n" % HELD_MARK)
_held = consume(plant_consume("held", "done\n", {"check": ["python3 " + _held_mark]}, brief_age=120)[0], "w1")
case("a brief written after the run start is HELD: nothing runs, exit 1",
     lambda: _held[0] == 1 and "HELD" in _held[1] and not os.path.exists(HELD_MARK))

_no_fence = consume(plant_consume("nofence", "done\n", None)[0], "w1")
_empty_check = consume(plant_consume("emptycheck", "done\n", {"check": []})[0], "w1")
_two_fences = consume(plant_consume("twofence", "done\n", None, brief_body=
                                    '```task\n{"check": ["x"]}\n```\n```task\n{"check": ["y"]}\n```\n')[0], "w1")
case("a brief with no task block, an empty check or two task blocks is refused (exit 2)",
     lambda: _no_fence[0] == 2 and "no task block" in _no_fence[2] and _empty_check[0] == 2
     and "empty check" in _empty_check[2] and _two_fences[0] == 2 and "one task block" in _two_fences[2])

FLOOR_MARK = os.path.join(TMP, "floor.marker")
_floor_mark = script("floor_mark.py", "open(%r, 'w').write('x')\n" % FLOOR_MARK)
_floor = []
for _n, _cmd in enumerate(("git add .claude/scratch/floor.txt", "git commit -m refused", "git push origin main")):
    _floor.append(consume(plant_consume("floor%d" % _n, "done\n", {"check": ["python3 " + _floor_mark, _cmd]})[0], "w1"))
case("a check that writes the index, commits or pushes is refused before any check runs (exit 2)",
     lambda: all(r[0] == 2 and "floor-guarded" in r[2] for r in _floor) and not os.path.exists(FLOOR_MARK))

import importlib.util

_spec = importlib.util.spec_from_file_location("dispatch_result_tool", TOOL)
_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tool)
_KILL_ALLOWED = ("grep -n kill notes.txt", "git log --grep=kill", "echo taskkill is refused",
                 "python3 .claude/tools/reap.py kill --pid 1")
_KILL_REFUSED = ("taskkill /F /PID 1", "echo reap.py; taskkill /F /PID 1", "kill -9 1234",
                 "ps | xargs kill", "pwsh -Command Stop-Process -Id 1", "python3 other.py && taskkill /PID 1")
case("a check that only names kill, or runs the reaper, is not a process kill",
     lambda: all(_tool.floor_reason(c) is None for c in _KILL_ALLOWED))
case("a check that runs taskkill, kill or Stop-Process is refused even beside a mention of reap.py",
     lambda: all(_tool.floor_reason(c) == "kills a process outside tools/reap.py" for c in _KILL_REFUSED))
_DISCARD_REFUSED = ("git reset --hard HEAD", "git clean -fd", "cd x && git checkout -- notes.txt")
_DISCARD_ALLOWED = ("git clean -n", "git status --short", "git diff --stat")
case("a check that git_guardrails would block as a discard is refused; a dry run or a read is not",
     lambda: all(_tool.floor_reason(c) == "discards work git_guardrails blocks" for c in _DISCARD_REFUSED)
     and all(_tool.floor_reason(c) is None for c in _DISCARD_ALLOWED))

_changed = consume(plant_consume("changed",'result\nchangedPaths: ["inside.txt", "outside.txt"]\n',
                                 {"check": [PY_OK], "writeSet": ["inside.txt"]})[0], "w1")
case("a returned changedPaths entry outside writeSet is flagged outside-writeSet",
     lambda: _changed[0] == 0 and any("outside.txt" in l and "outside-writeSet" in l for l in _changed[1].splitlines()))

_running = consume(plant_consume("running", "done\n", {"check": [PY_OK]}, status="running")[0], "w1")
_absent = consume(_ok_run, "no-such-label")
case("a run that is not terminal, or a label absent from the run, is refused (exit 2)",
     lambda: _running[0] == 2 and "not terminal" in _running[2] and _absent[0] == 2 and "no-such-label" in _absent[2])

# ---- consume: a gitignored file under writeSet is a changed path too ---------------------------
GIT_ROOT = os.path.join(TMP, "gitroot")
os.makedirs(os.path.join(GIT_ROOT, "ignored"))
subprocess.run(["git", "init", "-q", GIT_ROOT], check=True)
open(os.path.join(GIT_ROOT, ".gitignore"), "w", encoding="utf-8", newline="\n").write("ignored/\n")
open(os.path.join(GIT_ROOT, "ignored", "planted.txt"), "w", encoding="utf-8").write("planted\n")
_older = os.path.join(GIT_ROOT, "ignored", "older.txt")
open(_older, "w", encoding="utf-8").write("older\n")
os.utime(_older, (START_MS / 1000 - 100, START_MS / 1000 - 100))
open(os.path.join(GIT_ROOT, "fresh.txt"), "w", encoding="utf-8").write("untracked body\n")
_gi_run, _gi_spill, _ = plant_consume("gitignored", "done\n", {"check": [PY_OK], "writeSet": ["ignored", "fresh.txt"]})
_gi = dispatch_result.consume(_gi_run, "w1", GIT_ROOT, 20)
case("a gitignored new file under a writeSet directory is listed as !! inside writeSet",
     lambda: any(row["status"] == "!!" and row["path"] == "ignored/planted.txt" and not row["outside"]
                 for row in _gi["changed"]))
case("the diff carries the content of a new untracked file and of a new gitignored file",
     lambda: "+untracked body" in _gi.get("diff", "") and "+planted" in _gi.get("diff", "")
     and "older" not in _gi.get("diff", ""))
case("a gitignored file older than the run start is not listed",
     lambda: not any(row["path"].endswith("older.txt") for row in _gi["changed"]))

# ---- consume: a sidecar source (a -R record or a fan-out out dir) with --brief -------------------
def plant_sidecar(name, task):
    """A sidecar -R record `w1.record.json` with its .exit, .pid and captured .out, and a brief whose
    mtime sits before the record's .pid (the sidecar run start). Returns (record, brief, folder)."""
    folder = os.path.join(TMP, name)
    os.makedirs(folder)
    record = os.path.join(folder, "w1.record.json")
    with open(record, "w", encoding="utf-8") as fh:
        json.dump({"label": "w1", "requestedModel": "proof-model-a", "effort": "low",
                   "servedModel": "proof-model-a", "transport": "anthropic", "exitCode": 0}, fh)
    with open(record + ".exit", "w", encoding="utf-8") as fh:
        fh.write("0")
    with open(record + ".pid", "w", encoding="utf-8") as fh:
        fh.write("pid=1 launch=proof\n")
    with open(record + ".out", "w", encoding="utf-8") as fh:
        fh.write('{"type":"result","result":"sidecar final"}\n')
    brief = os.path.join(folder, "brief.md")
    with open(brief, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("Brief prose.\n```task\n" + json.dumps(task) + "\n```\n")
    stamp = time.time()
    os.utime(brief, (stamp - 120, stamp - 120))
    os.utime(record + ".pid", (stamp - 60, stamp - 60))
    return record, brief, folder


_sc_record, _sc_brief, _sc_dir = plant_sidecar("sc_pass", {"check": [PY_OK]})
_sc_pass = cli("consume", _sc_record, "--label", "w1", "--brief", _sc_brief, "--check-timeout", "20")
_sc_nobrief = cli("consume", _sc_record, "--label", "w1", "--check-timeout", "20")
case("a sidecar -R record with --brief runs its checks: CHECK PASS, exit 0",
     lambda: _sc_pass[0] == 0 and "CHECK 1 PASS" in _sc_pass[1])
case("a sidecar -R record without --brief is refused (exit 2) and the message names --brief",
     lambda: _sc_nobrief[0] == 2 and "--brief" in _sc_nobrief[2])
case("a sidecar deliverable is <label>.md beside its record, from the captured final message",
     lambda: read(os.path.join(_sc_dir, "w1.md")) == "sidecar final")
case("consume writes <label>.manifest.json for a sidecar source, requested fields from its record",
     lambda: json.loads(read(os.path.join(_sc_dir, "w1.manifest.json")) or "{}")
     .get("jobs", [{}])[0].get("requested", {}).get("model") == "proof-model-a")
open(os.path.join(_sc_dir, "w1.out.json"), "w", encoding="utf-8").write('{"type":"result","result":"sidecar final"}\n')
_fan_side = cli("consume", _sc_dir, "--label", "w1", "--brief", _sc_brief, "--check-timeout", "20")
case("a fan-out out dir with --brief runs its checks: CHECK PASS, exit 0",
     lambda: _fan_side[0] == 0 and "CHECK 1 PASS" in _fan_side[1])

# ---- consume: the claims check on a sidecar fan-out out dir -------------------------------------
def plant_fanout_claims(name, claims_by_label):
    """A fan-out out dir with one terminal job per label; a label's value is its claims file body, or None for none."""
    folder = os.path.join(TMP, name)
    os.makedirs(folder)
    brief = os.path.join(folder, "brief.md")
    with open(brief, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("Brief prose.\n```task\n" + json.dumps({"check": ["claims"]}) + "\n```\n")
    stamp = time.time()
    os.utime(brief, (stamp - 120, stamp - 120))
    for label, body in claims_by_label.items():
        record = os.path.join(folder, label + ".record.json")
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"label": label, "requestedModel": "proof-model-a", "effort": "low"}, fh)
        for suffix, text in ((".exit", "0"), (".pid", "pid=1\n")):
            with open(record + suffix, "w", encoding="utf-8") as fh:
                fh.write(text)
        os.utime(record + ".pid", (stamp - 60, stamp - 60))
        with open(os.path.join(folder, label + ".out.json"), "w", encoding="utf-8") as fh:
            fh.write('{"type":"result","result":"final of %s"}\n' % label)
        if body is not None:
            with open(record + ".claims.json", "w", encoding="utf-8") as fh:
                json.dump(body, fh)
    return folder, brief


_claims_dir, _claims_brief = plant_fanout_claims("fan_claims", {
    "good": {"claims": [], "verification": {"status": "pass", "counts": {"VERIFIED": 1}}},
    "bad": {"claims": [], "verification": {"status": "fail", "counts": {"VERIFIED": 0, "UNVERIFIED": 2}}},
    "none": None})
_claims = {lab: cli("consume", _claims_dir, "--label", lab, "--brief", _claims_brief, "--check-timeout", "20")
           for lab in ("good", "bad", "none")}
case("a sidecar claims file with status pass reads PASS, exit 0",
     lambda: _claims["good"][0] == 0 and "CHECK 1 PASS" in _claims["good"][1])
case("a sidecar claims file with status fail reads FAIL, exit 1, with the counts",
     lambda: _claims["bad"][0] == 1 and "CHECK 1 FAIL" in _claims["bad"][1] and "UNVERIFIED 2" in _claims["bad"][1])
case("a sidecar job with no claims file reads UNCHECKED, never PASS",
     lambda: "CHECK 1 UNCHECKED" in _claims["none"][1] and "CHECK 1 PASS" not in _claims["none"][1]
     and _claims["none"][0] != 0)
case("the claims check on a sidecar source is no longer refused",
     lambda: all("Workflow run; a sidecar" not in r[1] + r[2] for r in _claims.values()))

# ---- consume: ordered steps (a failed step ends the run; later steps print NOT-RUN) ------------
STEP_MARK = os.path.join(TMP, "step3.marker")
_step3 = script("step3_mark.py", "open(%r, 'w').write('x')\n" % STEP_MARK)
_steps = consume(plant_consume("steps", "done\n", {"check": [PY_OK, "python3 " + _boom, "python3 " + _step3]})[0], "w1")
case("a failed step ends the run: PASS, FAIL, NOT-RUN in order, exit 1, the later step never runs",
     lambda: _steps[0] == 1 and "CHECK 1 PASS" in _steps[1] and "CHECK 2 FAIL" in _steps[1]
     and "CHECK 3 NOT-RUN" in _steps[1] and not os.path.exists(STEP_MARK))


# ---- consume: a dispatch_chains.js run dir, each lane's last job resolved by label -----------
def plant_chains(name, lanes, texts):
    """A dispatch_chains.js run dir in the replay fixture's layout: the journal holds `started` and `result`
    rows per job, and the run record's args hold `chains` (each lane's jobs in order, as {label, promptPath,
    model, effort, agentType}) and `spillDir`. `lanes` maps a lane name to [(label, task)]; `texts` maps a
    label to its journal result. Returns (run dir, spill dir)."""
    root = os.path.join(TMP, name)
    sid = os.path.join(root, "sid")
    run = os.path.join(sid, "subagents", "workflows", FIXTURE_RUN_ID)
    os.makedirs(run)
    spill = os.path.join(root, "spill")
    os.makedirs(spill)
    chains, progress, events = [], [], [{"type": "launched"}]
    for lane, jobs in lanes.items():
        rows = []
        for label, task in jobs:
            brief = os.path.join(root, label + ".brief.md")
            with open(brief, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("Brief prose.\n```task\n" + json.dumps(task) + "\n```\n")
            os.utime(brief, (START_MS / 1000 - 120, START_MS / 1000 - 120))
            rows.append({"label": label, "promptPath": brief, "model": "proof-model-a", "effort": "low",
                         "agentType": "lane-executor"})
            agent = "ag-" + label
            events += [started(label, agent), result(agent, texts[label])]
            progress.append({"type": "workflow_agent", "index": len(progress) + 1, "label": label,
                             "agentId": agent, "state": "done", "startedAt": START_MS, "queuedAt": START_MS - 1})
        chains.append({"name": lane, "jobs": rows})
    with open(os.path.join(run, "journal.jsonl"), "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    with open(os.path.join(FIXTURE_SESSION, "workflows", FIXTURE_RUN_ID + ".json"), encoding="utf-8") as fh:
        record = json.load(fh)
    record.update(status="completed", result=None, args={"chains": chains, "spillDir": spill}, workflowProgress=progress)
    os.makedirs(os.path.join(sid, "workflows"))
    with open(os.path.join(sid, "workflows", FIXTURE_RUN_ID + ".json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return run, spill


_chains_run, _chains_spill = plant_chains(
    "chains", {"gate": [("g1", {"check": [PY_OK]}), ("g2", {"check": [PY_OK]})], "bench": [("b1", {"check": [PY_OK]})]},
    {"g1": "g1 text\n", "g2": "g2 text\n", "b1": "b1 text\n"})
_c_g2 = consume(_chains_run, "g2")
_c_b1 = consume(_chains_run, "b1")
case("a chains run dir resolves each lane's last job by label: its brief's checks run, exit 0",
     lambda: _c_g2[0] == 0 and "CHECK 1 PASS" in _c_g2[1] and _c_b1[0] == 0 and "CHECK 1 PASS" in _c_b1[1])
case("a chains lane's last job writes its deliverable to the chains spill dir",
     lambda: read(os.path.join(_chains_spill, "g2.md")) == "g2 text\n")


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
