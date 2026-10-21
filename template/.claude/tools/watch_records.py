#!/usr/bin/env python3
"""Report each record once, then ALL REPORTED; register the watch in session hook state.

Usage: python3 .claude/tools/watch_records.py <record> [<record> ...]. A record with no
identity warns once but remains watched. Poll interval is 0.5 s: a measured poll over
six records took 0.981 ms on this host; observed .exit-to-stdout-report delay was 350 ms.
WATCH_RECORDS_POLL_SEC overrides the interval for deterministic proofs.

Usage: python3 .claude/tools/watch_records.py <run dir> [<record> ...]. A positional argument that is a Workflow run dir
(`<session>/subagents/workflows/<id>`, holding `journal.jsonl`) is watched as a workflow unit with the wake rule below, whether
or not any dispatch table lists it (a `claude -p` child's run): its running test, check-in line and `COMPACTING` event are a
listed run's, its age counts from the dir's creation, and its end prints `NONE`.

Usage: python3 .claude/tools/watch_records.py --session --wake. `--session` re-reads this
session's `dispatch_table_pending` sidecar entries every poll and re-resolves each record, so a
sidecar launched after arming is covered without a second watcher. `--wake` exits — waking the
session — on the first unacknowledged decision event, including at the first poll: a record reaching DONE with a non-zero code, or
DIED (not one this session stopped with `reap.py`, listed in `sidecar_stopped_launches`: it prints `STOPPED <label>` at the next wake, and a unit whose every job was stopped wakes nobody); a detached job's `.memory` wait (`WAITING-MEMORY` with its readings) prints as context, since the
launcher resumes by itself; a record's `.stall` marker appearing while its upstream traffic is stale (`UPSTREAM_LIVE_S`); a record's `.usage` marker appearing (the launcher's
first usage-limit retry: `USAGE-LIMIT <label> <transport> resets <UTC time|unknown>`, while the run
keeps retrying); a running record whose stream (`<record>.stream`, else `<label>.stream`) has sat on a result written
after its launch for `sidecar_resume_check.RESULT_LINGER_S` (`LINGERING <label>`, the reading `sidecar_fanout.py --status`
gives the same run); a fan-out's own process gone while it still lists jobs
that will never start; or every record of one dispatch unit (one fan-out, one benchmark campaign, or one
direct launch) ending; or, while work still runs, `CHECKIN_SECONDS` after arm (50 min by default, inside the
orchestrator's one-hour prompt cache and before its idle compaction (`_hook_state.ORCHESTRATOR_CHECKIN_SECONDS`); `WATCH_RECORDS_CHECKIN_SEC` overrides it), printing a
`RUNNING` line per job with its age and last write, a `progress:` clause line under it (`agent_progress.clause`: the measures its
stream shows, `progress unreadable` when the stream cannot be parsed) and `CHECKIN_RULE`, the sentence that says how to judge
each clause. `--digest <record|stream|transcript>` prints the whole-run view the clause summarizes (`agent_progress.digest`).
The live set also holds this session's running
Workflow runs (a pending `workflow` entry whose host record `<session>/workflows/<id>.json` is absent or not
in `DONE_STATES`, or a run dir under `<session>/subagents/workflows/` with no entry, no host record and a write
within `DISCOVERED_RUN_SECONDS`: the launch hook's entry can land after the arm), each printed as `RUNNING workflow <name> <runId> <pins> for <n> min, agents <k> of <m> done`;
a run's end wakes nobody, and a watcher with nothing live exits `NONE`. A running
seat that compacts (a `compact_boundary` row in its transcript: a Workflow agent's `agent-<id>.jsonl`, a Claude sidecar's
`<record>.stream`) adds `<label> compacted <n> (<m> min compacting)` to its check-in line, and one that compacted again after
re-reading a file it had read before the previous compaction (the cycle: a seat that loses its subject, refills its window and
compacts again) wakes the session with `COMPACTING [workflow <runId>] <label> compacted <n>, re-read <file>`, once per
new compaction count. A running Workflow seat whose `agent-<id>.jsonl` has not grown for `WORKFLOW_STALL_SECONDS`
(`WATCH_RECORDS_STALL_SEC` overrides it) wakes with `STALLED workflow <runId> <label> idle=<n> min`, once per stall: the
client's request for that seat never returned, and no engine watchdog ends it. A `--session --wake` call made while
another live session-wide watcher is registered prints `COVERED by <process>` and exits 0. A bare `--await-launch`
(or `--await-launch=<seconds>`) makes an arm that finds nothing running wait that long for this session's launch entry
before it exits `NONE`; the bare form waits `dispatch_table.launch_grace()`.
A child no call of this session issued (a scoring daemon, a study's ruler, a delegate's own test launch: no `call` or `issued_at` on its entry, no fan-out, no bench campaign) ending cleanly wakes nobody: its parent reports its own end, and a failed child still wakes.
A wake prints, under each ended job's `DONE`/`DONE workflow` line, the lines of `wake_digest.py`: the result head, the
verification status with its counts and the report path of a sidecar, fan-out job or Workflow run. A background shell
that ended since the transcript byte offset saved in session hook state (`sidecar_wake_shell_offset`; a session with none starts at the transcript's end) arrives as
`SHELL <id> <status> exit=<n>` with its output path, failure-marker lines and tail; a shell that ran this watcher is skipped.
Ended Workflow runs and shells never wake the watcher: they print with the next wake, once. The last lines before `STILL RUNNING`
or `NONE` are `UNRATED <n>: <run:label>, ...` when the session has unrated dispatches.
Any other flag is a usage error (exit 2), never a record path. A successful job whose unit still has running jobs wakes nobody.
A benchmark campaign's cells (direct launches labelled `<campaign>-<date>:<task>:<model>:r<n>`) are one unit: while
`<logs>/<campaign>/grid.log` has a `CELL` line after its last terminal `GRID` line and `grid.pid` is alive, the grid
has cells no pending entry names yet, so the cells that ended wake nobody and the watcher stays live. The unit
ends on `GRID COMPLETE|PAUSED|HALTED|LOCKED|ABORT` or a dead grid pid; a failed cell, DIED, stall and usage-limit
wake at once. A roster of several campaigns is one unit per campaign. A bench process tree registers none of
its seats (`SIDECAR_DISPATCH_OWNER=bench`), so `--session` also watches each live roster whose worker process
carries this session's `CLAUDE_CODE_SESSION_ID`: one `roster` unit per roster, live while its log is open, waking
on `ROSTER <state> <name>: <last line>` when it ends, except a clean end (`DONE`) while another roster of this session is live, which prints at the next wake (a roster not live at arm is `bench.py wait`'s). An event already present
at arm prints once; only an event acknowledged in session hook state suppresses the exit. A wake acknowledges
the decision events it reports, so a watcher armed in any later turn does not exit on them (it still prints
them as context), and every clean `DONE <label> exit=0` line it prints, which a later watcher omits; when
nothing else remains it prints `NONE`. When nothing at arm can still progress — nothing running, queued or
unresolved within its grace — the ended lines and `NONE` print and the watcher exits 0; a job whose fan-out process
is gone is not "queued", because it will never start. An entry still unresolved
`UNRESOLVED_GRACE_SECONDS` after its launch prints `UNRESOLVED <label>` and wakes the session.
A launch whose spend-ledger finish row (lib/sidecar_common.sh `sc_write_finish_row`) says
`ended-before-result` has ended: it prints `ENDED <label> exit=<n> <reason>` once and wakes the
session like a non-zero DONE; a `finished-without-record` row ends it as `DONE <label> exit=<n>`.
A record that DIED, or a job of a fan-out whose process is gone that DIED, whose launch id (the
`launch=` in `<record>.pid`, else the record's or its inventory's `launchId`) a live process other
than a dispatch server still carries prints `ORPHAN <label> launch=<id> pids=<...>` and wakes the
session even when it stood at arm: an orphaned seat keeps spending until someone stops it
(`tools/reap.py <pids> --reason orphan`). Each record's process check runs once per watcher.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import _hook_state  # noqa: E402

POLL_SECONDS = float(os.environ.get("WATCH_RECORDS_POLL_SEC", "0.5"))
# The orchestrator's prompt cache lives one hour: a wake after it rewrites the whole context, a wake
# before it reads the cache at a tenth of the input price. `_hook_state.ORCHESTRATOR_CHECKIN_SECONDS` owns the value.
CHECKIN_SECONDS = float(os.environ.get("WATCH_RECORDS_CHECKIN_SEC", _hook_state.ORCHESTRATOR_CHECKIN_SECONDS))
UNRESOLVED_GRACE_SECONDS = 300
# A Workflow seat whose transcript has not grown for this long while its run is alive is a request that never
# returned. Basis: a completed Haiku seat compaction, the longest observed gap between rows of a healthy seat,
# took 3-6 min (A2b rail250 replay, 2026-10-14); the stalled seats of that run and of H3 sat 31+ min.
WORKFLOW_STALL_SECONDS = float(os.environ.get("WATCH_RECORDS_STALL_SEC", "600"))
# A run found on disk with no table entry counts as alive until nothing in it has been written for twice the stall
# window: the first window wakes the session with STALLED, the second presumes a crash that left no end record.
DISCOVERED_RUN_SECONDS = 2 * WORKFLOW_STALL_SECONDS
ENDED = "ended-before-result"
STOPPED_LAUNCHES_KEY = "sidecar_stopped_launches"  # the launch ids reap.py stopped for this session
SHELL_OFFSET_KEY = "sidecar_wake_shell_offset"  # the transcript byte offset the last wake scanned to for ended shells
REPORTED_ONCE_KINDS = ("end", "shell")  # a non-triggering line of these kinds prints once, then is acknowledged
PROJECT_ROOT = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(HERE))
# The check-in's judge-and-act rule, printed with every check-in so it is in context where the judgment is made.
CHECKIN_RULE = (
    "Judge each job now from its progress clause. Converging: new commits, new units reached, results improving. "
    "Not converging: the same unit reworked, results flat, a new cause each cycle. A job not converging gets, in order "
    "of cost kept: an amend file for its brief (the brief's full file name plus `.amend.md`), its finished items split off, or a stop through "
    "tools/reap.py with these facts in hand. A job still progressing stops only on the owner's word. "
    "`watch_records.py --digest <record or transcript>` prints a job's whole course. Then re-arm.")


def _proc_started_linux(pid):
    """Creation epoch of `pid` from /proc (boot time plus the stat start tick), else None."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            ticks = int(fh.read().decode("utf-8", "replace").rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="utf-8") as fh:
            boot = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _process_started(pid):
    """Creation epoch of a live process; None if gone, 0.0 if creation time is unavailable."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except OSError:
            pass
        return _proc_started_linux(pid) or 0.0
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 0.0 if ctypes.get_last_error() == 5 else None
    try:
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return 0.0
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return ticks / 1e7 - 11644473600
    finally:
        kernel.CloseHandle(handle)


def _process_line_gone(line):
    """True if a stamped process is gone or its PID reused; None without a usable identity."""
    key = "winpid=" if os.name == "nt" else "pid="
    fields = str(line).split()
    try:
        pid = int(next(field for field in fields if field.startswith(key))[len(key):])
    except (StopIteration, ValueError):
        return None
    from datetime import datetime
    try:
        stamp = datetime.fromisoformat(fields[0].replace("Z", "+00:00")).timestamp()
    except ValueError:
        stamp = 0.0
    started = _process_started(pid)
    return started is None or (stamp > 0 and started > stamp + 1)


def _launcher_gone(record):
    """True only when the last launcher's stamped PID is proven gone."""
    if not record:
        return False
    key = "winpid=" if os.name == "nt" else "pid="
    try:
        with open(record + ".pid", encoding="utf-8", errors="replace") as fh:
            lines = [line for line in fh if key in line]
    except OSError:
        return False
    return _process_line_gone(lines[-1]) is True if lines else False


def process_line():
    pid = os.getpid()
    return "%s %spid=%d" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "winpid=%d " % pid if os.name == "nt" else "", pid)


def _record_inventory(record, path=None):
    """The fan-out inventory listing this exact allocated record, or None.

    `path` is an inventory a caller already resolved; it is tried first, and a file that names no
    identity falls through to the directory scan.
    """
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        if data.get("fanoutProcess"):
            return data
    for candidate in glob.glob(os.path.join(os.path.dirname(_norm_record(record)), "fanout-*.inventory.json")):
        try:
            with open(candidate, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if any(_norm_record(job.get("recordPath")) == _norm_record(record) for job in data.get("jobs") or []):
            return data
    return None


def inventory_process(record, path=None):
    """The fan-out identity for this record; an already resolved inventory is tried first."""
    return (_record_inventory(record, path) or {}).get("fanoutProcess")


def record_state(record, inventory=None):
    """This run's rung on the one "has it ended" ladder: (state, code, found_by).

    `state` is DONE, DIED, WATCH or NO-IDENTITY. `found_by` names the signal that decided it —
    `exit`, `pid` or `inventory`, else "" — so a caller reading a different vocabulary for the
    same question maps this tuple instead of walking the files again, and a DIED or WATCH from the
    `pid` rung stays distinct from one found through the inventory.
    """
    exit_file = record + ".exit"
    try:
        with open(exit_file, encoding="utf-8") as fh:
            code = fh.read().strip()
        if code:
            return "DONE", code, "exit"
    except OSError:
        pass
    if os.path.exists(record + ".pid"):
        return ("DIED", None, "pid") if _launcher_gone(record) else ("WATCH", None, "pid")
    fanout = inventory_process(record, inventory)
    if fanout:
        return (("DIED", None, "inventory") if _process_line_gone(fanout) is True
                else ("WATCH", None, "inventory"))
    return "NO-IDENTITY", None, ""


def memory_wait(record):
    """The launcher's current admission-wait reading, or None when it has no WAIT marker."""
    try:
        with open(str(record) + '.memory', encoding='utf-8', errors='replace') as fh:
            line = fh.read(8192).strip()
    except OSError:
        return None
    return line if line.startswith(('WAIT memory headroom', 'STILL WAITING')) else None


def _norm_record(path):
    """One comparable form for a record path. A pending entry keeps the raw `-R` text, which is
    often relative, while a watcher registers the path it was handed."""
    if not path:
        return None
    text = str(path)
    if not os.path.isabs(text):
        text = os.path.join(PROJECT_ROOT, text)
    return os.path.normcase(os.path.abspath(text))


def _covered(record, watchers):
    """True when a live watcher covers `record`: a session-wide watcher, or one listing the record."""
    target = _norm_record(record)
    for watcher in watchers or []:
        if _process_line_gone(watcher.get("process")) is not False:
            continue
        if watcher.get("session"):
            return True
        if target is not None and any(
                _norm_record(other) == target for other in watcher.get("records") or []):
            return True
    return False


def session_watcher_live(state):
    """True when `state` (a session's hook state) lists a live session-wide watcher."""
    return _covered(None, state.get("sidecar_watchers") or [])


def registration(records, session_wide=False):
    """(state path, identity, covering process line). A session-wide watcher registers only when no
    live session-wide watcher is registered; the check and the add share one locked update."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return None, None, None
    path = _hook_state.state_path(session)
    identity = process_line()
    watch = {"process": identity, "records": records}
    if session_wide:
        watch["session"] = True
    covering = []

    def add(state):
        watches = state.get("sidecar_watchers") or []
        if session_wide:
            live = next((w for w in watches if w.get("session") and _covered(None, [w])), None)
            if live:
                covering.append(live.get("process"))
                return
        state["sidecar_watchers"] = [*watches, watch][-50:]

    _hook_state.update_json_locked(path, add)
    return path, identity, covering[0] if covering else None


def deregister(path, identity):
    if path:
        def remove(state):
            state["sidecar_watchers"] = [w for w in state.get("sidecar_watchers") or []
                                         if w.get("process") != identity]
        _hook_state.update_json_locked(path, remove)


def ledger_path():
    """The spend ledger; SIDECAR_LEDGER_PATH overrides it for the launchers and every reader."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import transcript_usage
    return transcript_usage.sidecar_ledger_path()


def epoch(stamp):
    """ISO-8601 `2026-09-23T01:41:08Z` (fractional seconds allowed) -> epoch seconds; 0 when unparseable."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def finish_rows(session_id):
    """This session's launcher finish rows (rows carrying `outcome`), oldest first; one ledger read."""
    rows = []
    try:
        with open(ledger_path(), encoding="utf-8") as fh:
            for text in fh:
                if '"outcome"' not in text:
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("outcome") and row.get("parentSessionId") == session_id:
                    rows.append(row)
    except OSError:
        pass
    return rows


def inventory_written(entry):
    """When the fan-out inventory that allocated `entry`'s record was written (it is written once),
    as epoch seconds; None for a direct launch or an unreadable inventory. That attempt's finish row
    is written after it, so an earlier attempt's row never answers for a relaunch."""
    try:
        return os.path.getmtime(entry["inventory"])
    except (KeyError, TypeError, OSError):
        return None


def record_launched(record):
    """When the record's latest launch line in `<record>.pid` was stamped, as epoch seconds; None when
    it has none. A relaunch into the same out dir reuses the record path, and this is its only trace."""
    try:
        with open(str(record) + ".pid", encoding="utf-8", errors="replace") as fh:
            stamps = re.findall(r"^(\S+)\s+launch=", fh.read(), re.MULTILINE)
    except (OSError, TypeError):
        return None
    times = [t for t in (epoch(s) for s in stamps) if t is not None]
    return max(times) if times else None


def finish_row(entry, rows):
    """The last finish row for this entry's label written at or after its launch, its record's
    inventory and its record's latest launch line, else None."""
    label, found = (entry.get("jobs") or [{}])[0].get("label"), None
    since = max((t for t in (_launch_time(entry), inventory_written(entry), record_launched(entry.get("record")))
                 if t is not None), default=None)
    for row in rows:
        if row.get("label") == label and (since is None or epoch(row.get("timestamp")) >= since):
            found = row
    return found


def finish_text(label, row):
    """`ENDED <label> exit=<n> <reason>` for a launch that ended before a result, else its DONE line."""
    if row.get("outcome") == ENDED:
        return ("ENDED %s exit=%s %s" % (label, row.get("exitCode"), row.get("reason") or "")).rstrip()
    return "DONE %s exit=%s" % (label, row.get("exitCode"))


def pending_sidecars(session_id):
    """This session's unfinished sidecar entries, each with its `record` resolved.

    `dispatch_table` is imported HERE and not at module top: it imports this module at module top, so
    a module-level import is a cycle, and the Stop hook that imports it would crash open.
    """
    import dispatch_table
    state = dispatch_table.read_table(session_id)
    pending = [e for e in state.get("dispatch_table_pending") or []
               if isinstance(e, dict) and e.get("kind") == "sidecar"]
    stopped = set(state.get(STOPPED_LAUNCHES_KEY) or [])
    rows = finish_rows(session_id)
    for entry in pending:
        dispatch_table.resolve_sidecar_record(entry, session_id, pending)
        entry["finish"] = finish_row(entry, rows)
        entry["stopped"] = bool(stopped and entry.get("record")) and record_launch(entry["record"]) in stopped
    return pending


def fanout_launcher_running(entry):
    """A live fan-out still holds finished seats only while it runs a stage after them (a review's
    post-check and fold, a refold); a legacy inventory that does not say keeps waiting."""
    if not entry.get("fanout_dir") or not entry.get("record"):
        return False
    inventory = _record_inventory(entry.get("record"), entry.get("inventory")) or {}
    if inventory.get("afterSeats") is False:
        return False
    process = inventory.get("fanoutProcess")
    return bool(process) and _process_line_gone(process) is False


def writer_launcher_running(entry):
    """A writer waits for its live fan-out while that fan-out runs a stage after its seats, or says nothing."""
    return bool(entry.get("writes")) and fanout_launcher_running(entry)


def _same_dir(a, b):
    return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))


def _listed_workdir(path, label, key):
    """The `workdir` that the JSON file at `path` lists for `label`: the list at the top-level `key`, else
    `path`'s own object, with a jobs file's `chains` flattened to their jobs; None when nothing matches."""
    try:
        with open(path or "", encoding="utf-8") as fh:
            spec = json.load(fh)
        listed = spec.get(key) if isinstance(spec, dict) and key in spec else spec
        if isinstance(spec, dict) and "chains" in spec:
            listed = [j for c in spec["chains"] for j in (c.get("jobs") or [])]
        return next((j.get("workdir") for j in listed or []
                     if isinstance(j, dict) and j.get("label") == label), None)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def writer_checkouts(entry):
    """The checkouts a dispatch's jobs write in: each job's `workdir` from its jobs file, else from the
    fan-out inventory (a detached fan-out's per-job entry carries no jobs file), else the project checkout."""
    out = []
    for job in entry.get("jobs") or []:
        workdir = (_listed_workdir(job.get("jobs_path"), job.get("label"), "jobs")
                   or _listed_workdir(entry.get("inventory"), job.get("label"), "jobs"))
        out.append(workdir or PROJECT_ROOT)
    return out or [PROJECT_ROOT]


def running_writers(session_id, repo=None):
    """Live author/fold dispatches for close; no session means no wait. With `repo`, only dispatches
    with a job writing in that checkout: a writer in another checkout cannot change the commits it proves."""
    if not session_id:
        return []
    import dispatch_table
    pending = dispatch_table.read_table(session_id).get("dispatch_table_pending") or []
    rows = finish_rows(session_id)
    now, out = time.time(), []
    for entry in pending:
        if not isinstance(entry, dict) or not entry.get("writes"):
            continue
        kind = entry.get("kind")
        if kind == "workflow":
            live = workflow_running(entry)
        elif kind == "sidecar":
            dispatch_table.resolve_sidecar_record(entry, session_id, pending)
            record = entry.get("record")
            live = writer_launcher_running(entry) or (finish_row(entry, rows) is None and (
                record_state(record, entry.get("inventory"))[0] == "WATCH" if record else
                bool(entry.get("deferred"))))
        else:
            continue
        if not live:
            continue
        if repo and not any(_same_dir(c, repo) for c in writer_checkouts(entry)):
            continue
        jobs = entry.get("jobs") or []
        models = list(dict.fromkeys(str(j.get("model") or "?") for j in jobs))
        out.append({"name": entry.get("name") or kind,
                    "labels": [str(j.get("label")) for j in jobs if j.get("label")],
                    "model": ", ".join(models) or "?",
                    "elapsed_s": max(now - float(entry.get("launched_at") or now), 0)})
    return out


def _workflow_host(entry):
    """The host record path of a Workflow run (`<session>/workflows/<id>.json`), from its run dir
    `<session>/subagents/workflows/<id>`; None when the run dir has another shape."""
    run_dir = entry.get("run_dir")
    if not run_dir:
        return None
    parts = os.path.normpath(str(run_dir)).split(os.sep)
    if len(parts) < 4 or parts[-2] != "workflows" or parts[-3] != "subagents":
        return None
    return os.path.join(os.sep.join(parts[:-3]) or os.sep, "workflows", "%s.json" % entry.get("id"))


def read_journal(run_dir):
    """One Workflow run's `journal.jsonl`: {'state': 'ok'|'absent'|'unreadable at line 1', 'bad': first
    unparseable line number or None, 'events': [{line, type, agentId, key, label, phase}]}. Blank and
    unparseable lines yield no event. A caller counts the identity its question needs: `agentId` for
    attempts, `key` for jobs."""
    out = {"state": "ok", "bad": None, "events": []}
    path = os.path.join(str(run_dir), "journal.jsonl")
    if not os.path.isfile(path):
        out["state"] = "absent"
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        out["state"] = "unreadable at line 1"
        return out
    for number, text in enumerate(lines, 1):
        if not text.strip():
            continue
        try:
            row = json.loads(text)
        except ValueError:
            row = None
        if not isinstance(row, dict):
            out["bad"] = out["bad"] or number
            continue
        out["events"].append({"line": number, "type": row.get("type"), "agentId": row.get("agentId"),
                              "key": row.get("key"), "label": row.get("label"), "phase": row.get("phase")})
    return out


def workflow_running(entry):
    """True while the run has no host record or its status is outside `DONE_STATES`; the record is
    written only when the run ends, and an unreadable one is still being written."""
    host = _workflow_host(entry)
    if host is None or not os.path.isfile(host):
        return True
    try:
        with open(host, encoding="utf-8") as fh:
            status = (json.load(fh) or {}).get("status")
    except (OSError, ValueError, AttributeError):
        return True
    import dispatch_table
    return not dispatch_table.workflow_finished(status)


def workflow_progress(entry):
    """(labels whose latest journal attempt has a `result` or `failed` row, listed job count or None)."""
    labels = {str(j.get("label")) for j in entry.get("jobs") or [] if isinstance(j, dict) and j.get("label")}
    latest, finished = {}, set()
    for row in read_journal(entry.get("run_dir"))["events"]:
        if row["type"] == "started" and row["label"]:
            latest[str(row["label"])] = row["key"]
        elif row["type"] in ("result", "failed") and row["key"]:
            finished.add(row["key"])
    done = sum(1 for label, key in latest.items() if key in finished and (not labels or label in labels))
    return done, len(labels) or None


_COMPACTION_CACHE = {}


def _read_paths(row):
    """The `file_path` of each `Read` tool_use block in one assistant transcript row."""
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Read":
            path = (block.get("input") or {}).get("file_path")
            if path:
                yield str(path).replace("\\", "/")


def agent_compaction(path):
    """One agent transcript (a Workflow `agent-<id>.jsonl` or a sidecar `<record>.stream`):
    {'count': compact_boundary rows, 'minutes': their summed duration or None when no row carries one,
    'reread': basename of a file read after the previous boundary that an earlier segment also read, taken from
    the segment the LAST boundary closed, else None}. A non-None 'reread' is the compaction cycle: the seat lost
    its subject and fetched it again. Unparseable lines (a partly written last line) are skipped; a missing file
    is zero compactions. Cached per (size, mtime): a poll re-reads only a transcript that grew."""
    out = {"count": 0, "minutes": None, "reread": None}
    try:
        stat = os.stat(path)
    except OSError:
        return out
    stamp = (stat.st_size, stat.st_mtime_ns)
    cached = _COMPACTION_CACHE.get(path)
    if cached and cached[0] == stamp:
        return dict(cached[1])
    earlier, segment, again, duration = set(), set(), None, None
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b"compact_boundary" not in raw and b'"Read"' not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("subtype") == "compact_boundary":
                    meta = row.get("compactMetadata") or row.get("compact_metadata") or {}
                    ms = meta.get("durationMs", meta.get("duration_ms")) if isinstance(meta, dict) else None
                    if isinstance(ms, (int, float)):
                        duration = (duration or 0) + ms
                    out["count"] += 1
                    out["reread"] = again
                    earlier |= segment
                    segment, again = set(), None
                elif row.get("type") == "assistant":
                    for read in _read_paths(row):
                        if os.path.normcase(read) in earlier and again is None:
                            again = os.path.basename(read)
                        segment.add(os.path.normcase(read))
    except OSError:
        return {"count": 0, "minutes": None, "reread": None}
    out["minutes"] = None if duration is None else duration / 60000.0
    _COMPACTION_CACHE[path] = (stamp, dict(out))
    return out


def compaction_clause(compaction):
    """`compacted <n> (<m> min compacting)`; the parenthesis is absent when no boundary carried a duration."""
    text = "compacted %d" % compaction["count"]
    if compaction["minutes"] is not None:
        text += " (%s min compacting)" % ("%.1f" % compaction["minutes"]).rstrip("0").rstrip(".")
    return text


def workflow_agents(entry):
    """The run's agents with no `result`/`failed` row for their journal key yet, each
    `{'agentId', 'label', **agent_compaction(agent-<id>.jsonl)}`. The label is the journal's, else the
    agent's `.meta.json` description."""
    run_dir = str(entry.get("run_dir"))
    started, finished = {}, set()
    for row in read_journal(run_dir)["events"]:
        if row["type"] == "started" and row["agentId"]:
            started[row["agentId"]] = row
        elif row["type"] in ("result", "failed") and row["key"]:
            finished.add(row["key"])
    out = []
    for agent_id, row in started.items():
        if row["key"] in finished:
            continue
        label = row["label"]
        if not label:
            try:
                with open(os.path.join(run_dir, "agent-%s.meta.json" % agent_id), encoding="utf-8") as fh:
                    label = json.load(fh).get("description")
            except (OSError, ValueError, AttributeError):
                pass
        path = os.path.join(run_dir, "agent-%s.jsonl" % agent_id)
        try:
            stat = os.stat(path)
            mtime, size = stat.st_mtime, stat.st_size
        except OSError:
            mtime, size = None, None
        out.append(dict(agent_compaction(path), agentId=agent_id, label=str(label or agent_id), mtime=mtime, size=size))
    return out


def pending_workflows(session_id):
    """This session's unfinished Workflow entries, as unit groups keyed by run id."""
    import dispatch_table
    state = dispatch_table.read_table(session_id)
    return {"workflow:%s" % e.get("id"): {"workflow": e, "records": [], "fanout_dir": None}
            for e in state.get("dispatch_table_pending") or []
            if isinstance(e, dict) and e.get("kind") == "workflow"}


def _digest():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import wake_digest
    return wake_digest


def workflow_end_text(entry):
    """`DONE workflow <name> <runId> status=<status>` for an ended run, then its `wake_digest.workflow_lines`."""
    host = _workflow_host(entry)
    try:
        with open(host, encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, TypeError, ValueError):
        record = {}
    record = record if isinstance(record, dict) else {}
    head = "DONE workflow %s %s status=%s" % (entry.get("name") or "Workflow", entry.get("id"), record.get("status") or "?")
    return "\n".join([head] + _digest().workflow_lines(record, read_journal(entry.get("run_dir"))["events"]))


def _progress():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import agent_progress
    return agent_progress


def progress_clause(stream, now, age_min=None, writes=None, with_history=True):
    """`progress: ...` for the stream at `stream` (see `agent_progress.clause`), `progress unreadable` for one it
    cannot parse, None when the file is absent or empty. A sidecar's duration history is its same-shape finished runs."""
    ap = _progress()
    state = ap.read(stream)
    if state is None:
        return None
    shape = hist = None
    if with_history and state.readable:
        writer = state.writer if state.writer is not None else writes
        shape = "writer" if writer else "read-only"
        hist = ap.history(shape)
    return ap.clause(state, now=now, window=CHECKIN_SECONDS, age_min=age_min, shape=shape, history=hist, writes=writes)


def workflow_line(entry, now):
    """`RUNNING workflow <name> <runId> <pins> for <n> min, agents <k> of <m> done` for one run."""
    import dispatch_table
    pins = dispatch_table.workflow_pins(entry.get("jobs"))
    done, listed = workflow_progress(entry)
    minutes = int(max(now - float(entry.get("launched_at") or now), 0) // 60)
    parts = ["RUNNING workflow %s %s" % (entry.get("name") or "Workflow", entry.get("id"))]
    if pins:
        parts.append(pins)
    parts.append("for %d min, agents %d%s done" % (minutes, done, " of %d" % listed if listed else ""))
    clauses = ["%s %s" % (a["label"], compaction_clause(a)) for a in workflow_agents(entry) if a["count"]]
    return " ".join(parts) + "".join("; " + clause for clause in clauses)


# A benchmark cell is a direct launch labelled `<campaign>-<date>:<task>:<model>:r<n>`; its campaign's grid
# (`<logs>/<campaign>/grid.log`, `grid.pid`) launches the campaign's cells one after another.
BENCH_LABEL = re.compile(r"^(?P<campaign>[^:\s]+):[^:\s]+:[^:\s]+:r\d+$")
GRID_TERMINAL = re.compile(r"^GRID (COMPLETE|PAUSED|HALTED|LOCKED|ABORT)\b")


def bench_campaign(entry):
    """The campaign a benchmark cell's label names, or None for any other launch."""
    if entry.get("fanout_dir"):
        return None
    found = BENCH_LABEL.match(str((entry.get("jobs") or [{}])[0].get("label") or ""))
    return found["campaign"] if found else None


GRID_RECHECK_S = 15.0
_GRID_SEEN = {}  # grid.log path -> (log stat, cells pending, pid, pid alive, monotonic probe time)


def bench_grid_running(campaign):
    """True while the campaign's grid has cells to run: grid.log has a CELL line after its last terminal
    `GRID` line and the grid.pid process is alive. A missing log, a dead or unprobeable pid, or any
    error reads False, so the unit ends and wakes. The watcher polls this at POLL_SECONDS, so the log is
    parsed only when its size or mtime changes and the pid is probed at most once per GRID_RECHECK_S."""
    try:
        scripts = os.path.join(os.path.dirname(HERE), "scripts", "benchmark_campaign", "scoring")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import bench_lib
        logs = bench_lib.roots.logs_dir()
        for tag in dict.fromkeys([campaign, re.sub(r"-\d{4}-\d{2}-\d{2}$", "", campaign)]):
            folder = os.path.join(logs, tag)
            log = os.path.join(folder, "grid.log")
            if not os.path.isfile(log):
                continue
            stat = os.stat(log)
            stamp = (stat.st_size, stat.st_mtime_ns)
            seen = _GRID_SEEN.get(log)
            if seen and seen[0] == stamp:
                pending, pid = seen[1], seen[2]
            else:
                with open(log, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
                last_cell = max((i for i, text in enumerate(lines) if text.startswith("CELL ")), default=-1)
                last_end = max((i for i, text in enumerate(lines) if GRID_TERMINAL.match(text)), default=-1)
                pending, pid, seen = last_cell > last_end, bench_lib._roster_pid(os.path.join(folder, "grid.pid")), None
            if not (pending and pid):
                _GRID_SEEN[log] = (stamp, pending, pid, False, time.monotonic())
                return False
            if seen and seen[2] == pid and time.monotonic() - seen[4] < GRID_RECHECK_S:
                alive, probed = seen[3], seen[4]
            else:
                alive, probed = bench_lib._pid_alive(pid) is True, time.monotonic()
            _GRID_SEEN[log] = (stamp, pending, pid, alive, probed)
            return alive
    except Exception:
        pass
    return False


ROSTER_RECHECK_S = float(os.environ.get("WATCH_RECORDS_ROSTER_RECHECK_SEC", "15"))
ROSTER_LIVE_STATES = ("LIVE", "OPEN")


def process_session(pid):
    """The `CLAUDE_CODE_SESSION_ID` in the environment of the process a roster's `<name>.pid` names, or
    None when it cannot be read. Git Bash writes an MSYS pid there, so Windows reads `/proc/<pid>/environ`
    through bash, the way reap.py reads it."""
    try:
        if os.name == "nt":
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import reap
            shell = reap.git_bash()
            if not shell:
                return None
            block = subprocess.run([shell, "-c", "cat /proc/%d/environ" % pid], capture_output=True,
                                   timeout=15).stdout
        else:
            with open("/proc/%d/environ" % pid, "rb") as fh:
                block = fh.read()
    except Exception:
        return None
    for item in block.split(b"\0"):
        if item.startswith(b"CLAUDE_CODE_SESSION_ID="):
            return item.split(b"=", 1)[1].decode("utf-8", "replace")
    return None


def session_rosters(session_id):
    """A poll function returning `{"roster:<name>": unit}` for each bench roster this session started.

    A roster belongs to the session whose id its worker process (`<logs>/<name>.pid`) carries; the
    environment is read once per name and pid. A roster found live stays listed after it ends, so its end
    wakes the watcher once. The roster logs are re-read at most once per `ROSTER_RECHECK_S`.
    """
    owned, checked, last = {}, {}, [None, {}]

    def poll():
        now = time.monotonic()
        if last[0] is not None and now - last[0] < ROSTER_RECHECK_S:
            return last[1]
        try:
            scripts = os.path.join(os.path.dirname(HERE), "scripts", "benchmark_campaign", "scoring")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            import bench_lib
            logs = bench_lib.roots.logs_dir()
            rows = bench_lib.roster_states(logs)
            units = {}
            for row in rows:
                name = row["name"]
                if name not in owned and row["state"] == "LIVE":
                    pid = bench_lib._roster_pid(os.path.join(logs, name + ".pid"))
                    if pid is not None and (name, pid) not in checked:
                        checked[(name, pid)] = process_session(pid) == session_id
                    if pid is not None and checked[(name, pid)]:
                        owned[name] = pid
                if name in owned:
                    units["roster:" + name] = {"roster": row, "records": []}
            last[:] = [now, units]
        except Exception:
            last[0] = now
        return last[1]
    return poll


def _background_child(unit):
    """True for a unit nobody here dispatched: no bench campaign, no fan-out, and no entry carries a tool call
    of this session (`called`). A scoring daemon, a study's ruler or a delegate's own test launch registers at
    the shared launcher with no call; its parent (the study, the delegate) reports its own end, so a clean end
    would otherwise wake the session once per child. A failed child still wakes (its `end` line triggers)."""
    return bool(unit["records"]) and not (unit.get("bench") or unit.get("fanout_dir") or unit.get("called"))


def _unit_key(entry):
    campaign = bench_campaign(entry)
    if campaign:
        return "bench:" + campaign
    fanout_dir = entry.get("fanout_dir")
    return _norm_record(fanout_dir) if fanout_dir else "id:" + str(entry.get("id"))


def _launch_time(entry):
    """The call's issue time, else the registration time, as epoch seconds; None when neither parses."""
    for key in ("issued_at", "launched_at"):
        try:
            return float(entry[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def units(pending):
    """`{key: {"records": [(label, path)], "fanout_dir": path|None}}` — one entry per dispatch unit.

    The unit is what the wake rule measures: a fan-out's jobs share `fanout_dir`, a benchmark
    campaign's cells share one unit, and every other direct launch is a unit of its own.
    """
    grouped = {}
    for entry in pending:
        unit = grouped.setdefault(_unit_key(entry),
                                  {"records": [], "inventories": [], "fanout_dir": entry.get("fanout_dir"),
                                   "unresolved": [], "launched": None, "finished": [], "finish": {},
                                   "bench": bench_campaign(entry), "called": False, "stopped": set()})
        if entry.get("call") or entry.get("issued_at") is not None:
            unit["called"] = True
        if entry.get("writes"):
            unit["writes"] = True
        launched = _launch_time(entry)
        if launched is not None and (unit["launched"] is None or launched < unit["launched"]):
            unit["launched"] = launched
        finish = entry.get("finish")
        name = (entry.get("jobs") or [{}])[0].get("label") or str(entry.get("id"))
        if finish and entry.get("record"):
            unit["finish"][_norm_record(entry["record"])] = finish
        elif finish:
            unit["finished"].append((name, finish))
        elif not entry.get("record"):
            unit["unresolved"].append(name)
        if entry.get("inventory"):
            unit["inventories"].append(entry["inventory"])
        if fanout_launcher_running(entry):
            unit["folding"] = True
        record = entry.get("record")
        if record and _norm_record(record) not in {_norm_record(r) for _, r in unit["records"]}:
            label = (entry.get("jobs") or [{}])[0].get("label") or os.path.basename(str(record))
            unit["records"].append((label, record))
        if entry.get("stopped") and record:
            unit["stopped"].add(_norm_record(record))
    return grouped


def _mtime(path):
    """`path`'s mtime, or None once it is gone: fan-outs drop inventories and the proxy rotates traffic
    files between a listing and its stat."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _inventory(unit):
    """The unit's (fanout process line, jobs) from its inventory; (None, []) for a direct launch."""
    if not unit.get("fanout_dir"):
        return None, []
    paths = list(unit.get("inventories") or [])
    stamped = [(stamp, path) for path in glob.glob(os.path.join(unit["fanout_dir"], "fanout-*.inventory.json"))
               for stamp in [_mtime(path)] if stamp is not None]
    paths += [path for _, path in sorted(stamped, reverse=True)]
    for path in dict.fromkeys(paths):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        return data.get("fanoutProcess"), [j for j in data.get("jobs") or [] if isinstance(j, dict)]
    return None, []


def orphan_line(unit):
    """`ORPHANED <labels>` when the unit's fan-out process is gone but still lists jobs that never
    started; None otherwise."""
    process, jobs = _inventory(unit)
    if not process or _process_line_gone(process) is not True:
        return None
    known = {_norm_record(record) for _, record in unit["records"]}
    stuck = [str(job.get("label") or "?") for job in jobs if job.get("recordPath")
             and _norm_record(job["recordPath"]) not in known
             and not os.path.exists(str(job["recordPath"]) + ".pid")
             and not os.path.exists(str(job["recordPath"]) + ".exit")]
    return "ORPHANED %s" % ", ".join(stuck) if stuck else None


def record_launch(record):
    """The record's launch id: the last `launch=` in `<record>.pid`, else the record JSON's `launchId`,
    else the `launchId` a fan-out inventory lists for it; None when none names one."""
    try:
        with open(record + ".pid", encoding="utf-8", errors="replace") as fh:
            found = re.findall(r"\blaunch=(\S+)", fh.read())
        if found:
            return found[-1]
    except OSError:
        pass
    try:
        with open(record, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("launchId"):
            return data["launchId"]
    except (OSError, ValueError):
        pass
    for candidate in glob.glob(os.path.join(os.path.dirname(_norm_record(record)), "fanout-*.inventory.json")):
        try:
            with open(candidate, encoding="utf-8") as fh:
                jobs = json.load(fh).get("jobs") or []
        except (OSError, ValueError, AttributeError):
            continue
        for job in jobs:
            if isinstance(job, dict) and _norm_record(job.get("recordPath")) == _norm_record(record) and job.get("launchId"):
                return job["launchId"]
    return None


def live_carriers(launch):
    """Live pids carrying SIDECAR_LAUNCH_ID=`launch` other than dispatch servers (tools/reap.py reads the
    table and the blocks); [] when none, or when the process table is unreadable."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import reap
    table = reap.process_table()
    return [pid for pid in reap.launch_census(table).get(launch, [])
            if pid in table and not reap.is_server_image(table[pid])]


_ORPHANS = {}


def orphan_processes(label, record, launch=None):
    """`ORPHAN <label> launch=<id> pids=<...>` once a DIED run's launch id is still carried by a live
    process that is not a dispatch server; None otherwise. Read once per record per watcher."""
    key = _norm_record(record)
    if key not in _ORPHANS:
        launch = launch or record_launch(record)
        pids = live_carriers(launch) if launch else []
        _ORPHANS[key] = ("ORPHAN %s launch=%s pids=%s" % (label, launch, ",".join(map(str, pids)))
                         if pids else None)
    return _ORPHANS[key]


def gone_fanout_orphans(unit):
    """ORPHAN lines for the jobs of a fan-out whose process is gone: each job the unit does not already
    watch whose run DIED while a live process still carries its launch id."""
    process, jobs = _inventory(unit)
    if not process or _process_line_gone(process) is not True:
        return []
    known = {_norm_record(record) for _, record in unit["records"]}
    out = []
    for job in jobs:
        record = job.get("recordPath")
        if not record or _norm_record(record) in known or record_state(str(record))[0] != "DIED":
            continue
        text = orphan_processes(str(job.get("label") or os.path.basename(str(record))), str(record), job.get("launchId"))
        if text:
            out.append(text)
    return out


def stall_idle(record):
    """The idle seconds in `<record>.stall`, or None when the marker is absent or unreadable."""
    try:
        with open(record + ".stall", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    found = re.search(r"idle=(\d+)", text)
    return found.group(1) if found else "?"


# The proxy writes one traffic file per upstream reasoning item, every 20-40 s while a turn reasons (2026-09-30
# sol high), so a file younger than this bound means the provider is still working on the turn.
UPSTREAM_LIVE_S = 180


def record_stream(record):
    """The run's -P stream: `<record>.stream` (the -R default) or `<label>.stream` (a fan-out's, which its
    attempts share); None when neither exists."""
    record = str(record)
    base = record[:-len(".record.json")] if record.endswith(".record.json") else record
    for path in (record + ".stream", re.sub(r"\.attempt\d+$", "", base) + ".stream"):
        if os.path.isfile(path):
            return path
    return None


def stream_precedes_launch(record, stream):
    """True when the stream's last write predates this run's launch: `<record>.pid` is missing (the launch is
    not recorded yet) or newer than the stream, so the stream holds an earlier attempt's ending. One owner for
    `result_lingering` and `upstream_age`."""
    try:
        return os.path.getmtime(stream) < os.path.getmtime(str(record) + ".pid")
    except OSError:
        return True


def _stream_check():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import sidecar_resume_check
    return sidecar_resume_check


def result_lingering(record, now=None):
    """Seconds the run's stream has sat on its terminal result, when that is `RESULT_LINGER_S` or more and
    the result was written after this run's launch (`<record>.pid`); else None. The caller has established
    that the launcher still runs: then the child outlived its result, and a background task it started can
    re-wake it for billed turns. One owner for the session watcher and `sidecar_fanout.py --status`."""
    stream = record_stream(record)
    if not stream:
        return None
    if stream_precedes_launch(record, stream):
        return None
    check = _stream_check()
    age = check.terminal_result_age(stream, time.time() if now is None else now)
    return age if age is not None and age >= check.RESULT_LINGER_S else None


def upstream_age(record, traffic_root=None, now=None):
    """Seconds since the proxy last wrote traffic for this record's session, or None when unknown.

    The record's stream (`record_stream`) names the child's session in its rows; a fan-out's attempts share
    one stream, so the session is the last one the stream names, and None while the stream has not been
    written since this run's launch (`stream_precedes_launch`). The proxy keeps that
    session's upstream traffic under `<traffic_root>/<session id>/`, newest request directory last."""
    stream = record_stream(record)
    if not stream:
        return None
    if stream_precedes_launch(record, stream):
        return None
    session = None
    try:
        with open(stream, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
        for found in re.finditer(r'"session_id"\s*:\s*"([^"]+)"', tail):
            session = found.group(1)
    except OSError:
        return None
    if not session:
        return None
    if traffic_root is None:
        lib = os.path.join(os.path.dirname(HERE), "scripts", "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        from ccp_probe import TRAFFIC_ROOT as traffic_root
    base = os.path.join(traffic_root, session)
    try:
        newest_dir = max(os.listdir(base))
    except (OSError, ValueError):
        return None
    stamps = [stamp for dirpath, _dirs, files in os.walk(os.path.join(base, newest_dir)) for name in files
              for stamp in [_mtime(os.path.join(dirpath, name))] if stamp is not None]
    if not stamps:
        return None
    return (time.time() if now is None else now) - max(stamps)


def stall_text(label, idle, age):
    """The STALLED line, naming a turn the provider is still reasoning when its traffic is fresh."""
    text = "STALLED %s idle=%s" % (label, idle)
    if age is not None and age < UPSTREAM_LIVE_S:
        text += " (upstream traffic %ds ago: the provider is still reasoning this turn)" % age
    return text


def usage_line(record, label):
    """`USAGE-LIMIT <label> <transport> resets <time|unknown>` from `<record>.usage`, or None when absent."""
    try:
        with open(record + ".usage", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    reset = data.get("resetsAt")
    when = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset))
            if isinstance(reset, (int, float)) and reset > 0 else "unknown")
    return "USAGE-LIMIT %s %s resets %s" % (label, data.get("transport") or "unknown", when)


def _stream(pending):
    """Each record's ending, once, then ALL REPORTED."""
    warned = set()
    while pending:
        for record in pending[:]:
            state, code, _ = record_state(record)
            label = os.path.basename(record).replace(".record.json", "")
            if state == "NO-IDENTITY":
                if record not in warned:
                    print("NO-IDENTITY %s" % label, flush=True)
                    warned.add(record)
            elif state in ("DONE", "DIED"):
                print("DONE %s exit=%s" % (label, code) if state == "DONE" else
                      "DIED %s" % label, flush=True)
                pending.remove(record)
        if pending:
            time.sleep(POLL_SECONDS)
    print("ALL REPORTED", flush=True)
    return 0


def _wake(grouped, now=None):
    """The wake rule over one poll: (lines, live, standing_stalls).

    A line is `(kind, identity, text, triggers)`: `identity` is what makes one event unique across
    polls, `triggers` whether that event exits the watcher. `live` counts the work that can still
    progress: records that have not ended, plus entries whose record is not resolved yet and whose
    launch is younger than `UNRESOLVED_GRACE_SECONDS`. Past it the entry is `UNRESOLVED`: a launch
    that failed before allocating its record would otherwise hold the watcher forever.
    """
    now = time.time() if now is None else now
    lines, live, standing = [], 0, set()
    for ukey, unit in grouped.items():
        if unit.get("workflow"):
            entry = unit["workflow"]
            if workflow_running(entry):
                live += 1
                for agent in workflow_agents(entry):
                    seat = "workflow:%s:%s" % (entry.get("id"), agent["agentId"])
                    if agent["reread"]:
                        lines.append(("compacting", ("compacting", seat, agent["count"]),
                                      "COMPACTING workflow %s %s compacted %d, re-read %s"
                                      % (entry.get("id"), agent["label"], agent["count"], agent["reread"]), True))
                    idle = now - agent["mtime"] if agent.get("mtime") is not None else None
                    if idle is not None and idle >= WORKFLOW_STALL_SECONDS:
                        # Identity carries the transcript size: a seat that grows and stalls again is a new event.
                        lines.append(("stalled", ("stalled", seat, agent["size"]),
                                      "STALLED workflow %s %s idle=%d min: its transcript stopped growing while the run "
                                      "is alive, so its request has not returned; stop it with tools/reap.py or wait for "
                                      "the client's retry" % (entry.get("id"), agent["label"], int(idle // 60)), True))
            else:
                lines.append(("end", ("end", _workflow_host(entry) or str(entry.get("run_dir"))),
                              workflow_end_text(entry), False))
            continue
        if unit.get("shell"):
            lines.append(("shell", ("shell", unit["shell"]["task_id"]), _digest().shell_text(unit["shell"]), False))
            continue
        if unit.get("roster"):
            row = unit["roster"]
            if row["state"] in ROSTER_LIVE_STATES:
                live += 1
            else:
                # DONE is the roster's `END <name> ok`; a clean end waits while another roster of this session runs.
                others_live = any(other.get("roster") and other["roster"]["state"] in ROSTER_LIVE_STATES
                                  for other in grouped.values())
                lines.append(("roster", ("roster", row["name"], row["last"]),
                              "ROSTER %s %s: %s" % (row["state"], row["name"], row["last"]),
                              not (row["state"] == "DONE" and others_live)))
            continue
        ended = 0
        for label, record in unit["records"]:
            state, code, _ = record_state(record)
            if state in ("DONE", "DIED"):
                ended += 1
                finish = (unit.get("finish") or {}).get(_norm_record(record))
                text = ("DONE %s exit=%s" % (label, code) if state == "DONE" else "DIED %s" % label)
                if state == "DONE" and finish and finish.get("outcome") == ENDED:
                    text = finish_text(label, finish)
                # A job the session stopped with reap.py is context; a DIED carrier still alive stays a wake below.
                stopped = _norm_record(record) in (unit.get("stopped") or ())
                failed = state != "DONE" or code != "0"
                if stopped:
                    text = "STOPPED %s (stopped by this session)" % label
                elif state == "DONE" and not (finish and finish.get("outcome") == ENDED):
                    text = "\n".join([text] + _digest().sidecar_lines(label, record, unit.get("fanout_dir")))
                lines.append(("end", ("end", record), text, failed and not stopped))
                orphan = orphan_processes(label, record) if state == "DIED" else None
                if orphan:
                    lines.append(("orphan-process", ("orphan-process", _norm_record(record)), orphan, "always"))
                continue
            live += 1
            if state == "NO-IDENTITY":
                lines.append(("identity", ("identity", record),
                              "NO-IDENTITY %s" % label, False))
                continue
            waiting = memory_wait(record)
            if waiting:
                standing.add(('memory', record))
                lines.append(('memory', ('memory', record), 'WAITING-MEMORY %s: %s' % (label, waiting), False))
                continue
            idle = stall_idle(record)
            if idle is not None:
                standing.add(("stall", record))
                age = upstream_age(record)
                # Fresh provider traffic means a long reasoning turn; its marker clears and returns between writes.
                lines.append(("stall", ("stall", record), stall_text(label, idle, age),
                              age is None or age >= UPSTREAM_LIVE_S))
            usage = usage_line(record, label)
            if usage is not None:
                standing.add(("usage", record))
                lines.append(("usage", ("usage", record), usage, True))
            lingering = result_lingering(record, now)
            if lingering is not None:
                lines.append(("lingering", ("lingering", record),
                              "LINGERING %s: its stream ended on a result %d min ago and its launcher still runs "
                              "(a background task can re-wake the child for billed turns); stop it with tools/reap.py"
                              % (label, lingering // 60), True))
            stream = record_stream(record)
            compaction = agent_compaction(stream) if stream else None
            if compaction and compaction["reread"]:
                lines.append(("compacting", ("compacting", record, compaction["count"]),
                              "COMPACTING %s compacted %d, re-read %s"
                              % (label, compaction["count"], compaction["reread"]), True))
        key = _unit_key_of(unit) if unit["records"] or unit.get("fanout_dir") else "unit:%s" % ukey
        for label, row in unit.get("finished") or []:
            ended += 1
            lines.append(("end", ("finish", key, label, row.get("timestamp")), finish_text(label, row),
                          row.get("outcome") == ENDED or str(row.get("exitCode")) != "0"))
        settled = len(unit["records"]) + len(unit.get("finished") or [])
        if settled and ended == settled and not unit.get("unresolved"):
            if unit.get("folding"):
                live += 1  # its seats ended and its launcher still runs its after-seat stage: dispatch_table's Stop check holds it too
                continue
            if unit.get("bench") and bench_grid_running(unit["bench"]):
                live += 1  # its campaign's grid has cells to run that no pending entry names yet
                continue
            record_keys = {_norm_record(r) for _, r in unit["records"]}
            all_stopped = bool(record_keys) and record_keys <= (unit.get("stopped") or set())
            lines.append(("complete", ("complete", key), "", not _background_child(unit) and not all_stopped))
            continue
        for text in gone_fanout_orphans(unit):
            lines.append(("orphan-process", ("orphan-process", text), text, "always"))
        orphan = orphan_line(unit)
        if orphan:
            lines.append(("orphan", ("orphan", key), orphan, True))
        elif not unit["records"]:
            launched = unit.get("launched")
            if launched is None or now - launched <= UNRESOLVED_GRACE_SECONDS:
                live += 1  # unresolved: its record is not allocated yet, so it may still be dispatching
            else:
                for label in unit["unresolved"]:
                    lines.append(("unresolved", ("unresolved", key, label),
                                  "UNRESOLVED %s (no record after %ds)" % (label, UNRESOLVED_GRACE_SECONDS),
                                  True))
    return lines, live, standing


def checkin_lines(grouped, now=None):
    """One `RUNNING <label> for <n> min, last write <m> min ago` line per record still running: its
    age from its `.pid`, its last write the newest other file in its directory named for its label and
    not for a longer label of the same unit, then its `  progress: ...` clause line. A job only its fan-out
    inventory lists is `QUEUED <label>`. A running Workflow run adds one clause line per running agent."""
    now = time.time() if now is None else now
    out = []
    for unit in grouped.values():
        if unit.get("workflow"):
            if workflow_running(unit["workflow"]):
                out.append(workflow_line(unit["workflow"], now))
                for agent in workflow_agents(unit["workflow"]):
                    clause = progress_clause(
                        os.path.join(str(unit["workflow"].get("run_dir")), "agent-%s.jsonl" % agent["agentId"]),
                        now, with_history=False)
                    if clause:
                        out.append("  %s: %s" % (agent["label"], clause))
            continue
        if unit.get("roster"):
            row = unit["roster"]
            if row["state"] in ROSTER_LIVE_STATES:
                out.append("RUNNING roster %s: %s" % (row["name"], row["last"]))
            continue
        for label, record in unit["records"]:
            state, _, found_by = record_state(record)
            if state in ("DONE", "DIED", "NO-IDENTITY"):
                continue
            waiting = memory_wait(record)
            if waiting:
                out.append('WAITING-MEMORY %s: %s' % (label, waiting))
                continue
            if found_by == "inventory":
                out.append("QUEUED %s" % label)
                continue
            folder = os.path.dirname(record)
            longer = tuple(other + c for other, _ in unit["records"] if other != label and other.startswith(label)
                           for c in ".-")
            try:
                started = os.path.getmtime(record + ".pid")
                written = max(os.path.getmtime(os.path.join(folder, name)) for name in os.listdir(folder)
                              if (name.startswith(label + ".") or name.startswith(label + "-"))
                              and not name.startswith(longer) and not name.endswith(".pid"))
            except (OSError, ValueError):
                out.append("RUNNING %s (age unreadable)" % label)
                continue
            stream = record_stream(record)
            compaction = agent_compaction(stream) if stream else None
            out.append("RUNNING %s for %d min, last write %d min ago%s"
                       % (label, (now - started) // 60, (now - written) // 60,
                          ", " + compaction_clause(compaction) if compaction and compaction["count"] else ""))
            clause = (progress_clause(stream, now, age_min=(now - started) / 60.0, writes=unit.get("writes"))
                      if stream else None)
            if clause:
                out.append("  " + clause)
    return out


def _unit_key_of(unit):
    if unit.get("bench"):
        return "bench:" + unit["bench"]
    return _norm_record(unit.get("fanout_dir")) or "records:" + ",".join(
        sorted(str(_norm_record(record)) for _, record in unit["records"]))


def _wake_event_key(identity):
    incarnation = None
    if identity[0] in ('end', 'stall', 'usage', 'identity', 'memory', 'lingering', 'compacting'):
        try:
            incarnation = os.stat(str(identity[1]) + '.pid').st_mtime_ns
        except OSError:
            pass
        if identity[0] == 'usage':
            try:
                with open(str(identity[1]) + '.usage', encoding='utf-8') as stream:
                    incarnation = [incarnation, json.load(stream).get('firstSeen')]
            except (OSError, ValueError, AttributeError):
                pass
    return json.dumps([identity, incarnation], separators=(',', ':'))


def _new_wake_events(path, identities, active):
    keys = {_wake_event_key(identity) for identity in identities}
    if not path:
        return keys
    active_keys = {_wake_event_key(identity) for identity in active}
    fresh = set()

    def acknowledge(state):
        old = [key for key in state.get('sidecar_wake_seen') or [] if key in active_keys]
        fresh.clear()
        fresh.update(keys - set(old))
        state['sidecar_wake_seen'] = list(dict.fromkeys(old + sorted(keys)))
    _hook_state.update_json_locked(path, acknowledge)
    return fresh


def _acknowledged(path):
    """The event keys session hook state records as acknowledged; empty when unreadable. Reads only."""
    try:
        with open(path, encoding="utf-8") as fh:
            return set(json.load(fh).get("sidecar_wake_seen") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def _acknowledge(path, identities):
    """Record `identities` as reported: the watcher that printed them has exited, so the session has them."""
    keys = [_wake_event_key(identity) for identity in identities]
    if path and keys:
        def add(state):
            state["sidecar_wake_seen"] = list(dict.fromkeys((state.get("sidecar_wake_seen") or []) + keys))
        _hook_state.update_json_locked(path, add)


def wake_loop(source, *, ack_path=None, await_launch=0.0, closing=None):
    """Poll `source()` (a `{key: unit}` map) and apply the wake rule. Returns the exit code.

    Unacknowledged decision events trigger even at the first poll. After that a non-triggering
    line (a job that succeeded while its unit still runs) is held and printed at the next wake, so
    a successful job wakes nobody. A stall or usage identity is forgotten once its marker clears, so
    a second marker on the same record (a relaunch on that path) is a new event.

    A clean-success line (`DONE <label> exit=0`) is reported once: every one a watcher prints is
    acknowledged as the watcher exits, and a watcher armed in any later turn omits it. A failure or
    ENDED line is a decision event: it stays context on every re-arm, and its acknowledgment only stops
    it from waking again.

    An ended job's line carries its `wake_digest` lines (result head, verification counts, report path); an ended
    Workflow run and a background shell that ended since the saved transcript offset (`ShellEnds`) are
    non-triggering lines of the same once-only kind. `closing()`, when given, returns the lines printed after the
    held lines at every wake and before its terminal line (the session's unrated dispatches).
    """
    seen, standing_stalls, deferred, first = set(), set(), [], True
    armed, printed, said, reported = time.time(), [], [], _acknowledged(ack_path) if ack_path else set()

    def say(kind, identity, text, triggers):
        print(text, flush=True)
        said.append(identity)
        if kind in REPORTED_ONCE_KINDS and not triggers:
            printed.append(identity)

    def leave(code=0):
        _acknowledge(ack_path, printed)
        shells = getattr(source, "shells", None)
        if shells is not None:
            shells.persist()
        return code

    def close():
        for text in (closing() if closing else ()):
            print(text, flush=True)

    def release(held, lines, standing):
        """Print the held lines at a wake. A held reading of a standing condition (a memory wait, a stall,
        a usage retry) prints the condition's latest line, and one whose condition has cleared is dropped:
        the first sample of a wait that ended is a misreport."""
        latest = {identity: text for _, identity, text, _ in lines}
        for kind, identity, text, triggers in held:
            if identity in standing:
                say(kind, identity, latest.get(identity, text), triggers)
            elif kind not in ("memory", "stall", "usage"):
                say(kind, identity, text, triggers)
        close()
    while True:
        grouped = source()
        lines, live, standing = _wake(grouped)
        cleared = standing_stalls - standing
        seen = {item for item in seen if item[0] not in cleared}
        standing_stalls = standing
        candidates = [identity for _, identity, _, triggers in lines
                      if triggers and (identity, bool(triggers)) not in seen]
        fresh = (_new_wake_events(ack_path, candidates, [identity for _, identity, _, _ in lines])
                 if first or candidates or cleared else set())
        woke = False
        for kind, identity, text, triggers in lines:
            observation = identity, bool(triggers)
            if observation in seen:
                continue
            seen.add(observation)
            if kind in REPORTED_ONCE_KINDS and not triggers and _wake_event_key(identity) in reported:
                continue
            if triggers and _wake_event_key(identity) in fresh:
                woke = True
            if text:
                if triggers or first:
                    say(kind, identity, text, triggers)
                else:
                    deferred.append((kind, identity, text, triggers))
        if first and not woke and live == 0 and time.time() - armed < await_launch:
            pass  # nothing runs yet: this session's launch entry may still be on its way
        elif first and not woke:
            first = False
            if live == 0:
                if said:
                    close()
                print("NONE", flush=True)
                return leave()
        elif woke:
            release(deferred, lines, standing)
            print("STILL RUNNING %d" % live, flush=True)
            return leave()
        elif live and time.time() - armed >= CHECKIN_SECONDS:
            release(deferred, lines, standing)
            for text in checkin_lines(grouped):
                print(text, flush=True)
            print("CHECKIN after %d min: %s" % ((time.time() - armed) // 60, CHECKIN_RULE), flush=True)
            print("STILL RUNNING %d" % live, flush=True)
            return leave()
        elif not live:
            release(deferred, lines, standing)
            print("NONE", flush=True)
            return leave()
        time.sleep(POLL_SECONDS)


class ShellEnds:
    """The background shells that ended in this session's transcript since a saved byte offset.

    The offset lives in session hook state (`SHELL_OFFSET_KEY`) and is saved when a wake prints, so a shell that
    ended between two watchers arrives with the next wake. A session with no saved offset starts at the
    transcript's end: shells that ended before the first arm reached the orchestrator through their own
    notification. `wake_digest.shell_notices` reads only the bytes appended since."""

    def __init__(self, session_id, state_path):
        self.session_id, self.state_path = session_id, state_path
        self.path, self.notices = None, {}
        saved = (_hook_state.read_json_salvage(state_path) or {}).get(SHELL_OFFSET_KEY) if state_path else None
        self.offset = saved if isinstance(saved, int) and not isinstance(saved, bool) else None

    def poll(self):
        if self.path is None:
            self.path = _digest().transcript_path(self.session_id)
        if self.path is None:
            return self.notices
        if self.offset is None:
            self.offset = os.path.getsize(self.path)
        found, self.offset = _digest().shell_notices(self.path, self.offset)
        for notice in found:
            self.notices.setdefault(notice["task_id"], notice)
        return self.notices

    def persist(self):
        if self.state_path and self.offset is not None:
            offset = self.offset
            _hook_state.update_json_locked(self.state_path, lambda state: state.__setitem__(SHELL_OFFSET_KEY, offset))


def _newest_write(run_dir):
    """The latest mtime of a run dir's journal and agent transcripts, or None when it holds none."""
    stamps = []
    for path in [os.path.join(run_dir, "journal.jsonl")] + glob.glob(os.path.join(glob.escape(run_dir), "agent-*.jsonl")):
        try:
            stamps.append(os.path.getmtime(path))
        except OSError:
            continue
    return max(stamps) if stamps else None


def discovered_workflows(session_dir, known, now=None):
    """This session's running Workflow runs that `known` (unit keys) does not list, found on disk.

    A run's dir and `journal.jsonl` exist from its launch, and its host record only when it ends, so a run
    with no record that was written within DISCOVERED_RUN_SECONDS is alive. The launch hook's table entry
    lands up to `launch_grace()` after the call returns; a watcher armed before it, or before a denied call is
    re-issued, finds nothing there, and a lost entry would leave the run unwatched for good."""
    now = time.time() if now is None else now
    root = os.path.join(session_dir, "subagents", "workflows")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}
    found = {}
    for name in names:
        run_dir = os.path.join(root, name)
        key = "workflow:%s" % name
        if key in known or not is_run_dir(run_dir):
            continue
        entry = foreign_workflow(run_dir)
        newest = _newest_write(run_dir)
        if workflow_running(entry) and newest is not None and now - newest <= DISCOVERED_RUN_SECONDS:
            found[key] = {"workflow": entry, "records": [], "fanout_dir": None}
    return found


def _session_source(session_id, state_path=None):
    rosters = session_rosters(session_id)
    shells = ShellEnds(session_id, state_path)
    session_dir = []

    def discovered(known):
        if not session_dir:
            path = _digest().transcript_path(session_id)
            if path is None:
                return {}
            session_dir.append(path[:-len(".jsonl")])
        return discovered_workflows(session_dir[0], known)

    def source():
        grouped = units(pending_sidecars(session_id))
        grouped.update(pending_workflows(session_id))
        grouped.update(discovered(grouped))
        grouped.update(rosters())
        grouped.update({"shell:%s" % task_id: {"shell": notice, "records": [], "fanout_dir": None}
                        for task_id, notice in shells.poll().items()})
        return grouped
    source.shells = shells
    return source


def is_run_dir(path):
    """True for a Workflow run dir (`<session>/subagents/workflows/<id>`): a directory holding `journal.jsonl`."""
    return os.path.isfile(os.path.join(str(path), "journal.jsonl"))


def foreign_workflow(run_dir):
    """A dispatch-table-shaped entry for a run dir no dispatch table lists. Its journal has no per-row time, so
    `launched_at` is the dir's creation time (the run's start), not the journal's mtime, which moves with each row."""
    stat = os.stat(run_dir)
    return {"kind": "workflow", "id": os.path.basename(run_dir.rstrip("/\\")), "name": "Workflow", "run_dir": run_dir,
            "launched_at": getattr(stat, "st_birthtime", stat.st_ctime), "jobs": []}


def _records_source(pending):
    runs = [path for path in pending if is_run_dir(path)]
    records = [path for path in pending if path not in runs]

    def source():
        grouped = {"workflow:%s" % entry["id"]: {"workflow": entry, "records": [], "fanout_dir": None}
                   for entry in map(foreign_workflow, runs)}
        if records:
            grouped["records:" + ",".join(records)] = {
                "records": [(os.path.basename(r).replace(".record.json", ""), r) for r in records],
                "fanout_dir": None}
        return grouped
    return source


def launch_grace():
    """Seconds an arm waits for a launch entry: `dispatch_table.launch_grace()`."""
    import dispatch_table
    return dispatch_table.launch_grace()


def digest_main(target):
    """Print `agent_progress.digest` for a record (its `-P` stream), a stream or a transcript; 1 when unreadable."""
    ap = _progress()
    path = record_stream(target) if str(target).endswith(".record.json") else target
    state = ap.read(path) if path else None
    if state is None or not state.readable:
        print("progress unreadable %s" % target, flush=True)
        return 1
    print(ap.digest(state, label=os.path.basename(str(path))), flush=True)
    return 0


def main(args=None):
    argv = list(sys.argv[1:] if args is None else args)
    if "--digest" in argv:
        rest = argv[argv.index("--digest") + 1:]
        if len(rest) != 1 or rest[0].startswith("--") or len(argv) != 2:
            print("Usage: watch_records.py --digest <record|stream|transcript>", file=sys.stderr)
            return 2
        return digest_main(rest[0])
    unknown = [a for a in argv if a.startswith("-") and a not in ("--session", "--wake")
               and a != "--await-launch" and not a.startswith("--await-launch=")]
    if unknown:
        if "--covered" in unknown:
            print("--covered is retired: arm `--session --wake` directly; beside a live watcher it prints COVERED and exits.", file=sys.stderr)
        print("Usage: watch_records.py <record> [<record> ...] | --session --wake [--await-launch[=<seconds>]]"
              " | --digest <record|stream|transcript> (unknown: %s)" % " ".join(unknown), file=sys.stderr)
        return 2
    session_wide, wake = "--session" in argv, "--wake" in argv
    await_launch = 0.0
    for flag in (a for a in argv if a == "--await-launch" or a.startswith("--await-launch=")):
        try:
            await_launch = launch_grace() if flag == "--await-launch" else float(flag.split("=", 1)[1])
        except ValueError:
            print("Usage: --await-launch=<seconds> takes a number (%s)" % flag, file=sys.stderr)
            return 2
    pending = list(dict.fromkeys(os.path.abspath(path)
                                 for path in argv if not path.startswith("--")))
    if session_wide and not os.environ.get("CLAUDE_CODE_SESSION_ID"):
        print("watch_records.py --session needs CLAUDE_CODE_SESSION_ID", file=sys.stderr)
        return 2
    if session_wide and not wake:
        # A backgrounded watcher's output arrives only when it exits, so a session watcher that
        # never exits delivers nothing; its only useful form is the wake rule.
        print("watch_records.py --session needs --wake", file=sys.stderr)
        return 2
    if not pending and not session_wide:
        print("Usage: watch_records.py <record> [<record> ...] | --session [--wake]", file=sys.stderr)
        return 2
    path, identity, covering = registration(pending, session_wide=session_wide)
    if covering:
        print("COVERED by %s: a live session watcher is registered, so nothing to do" % covering, flush=True)
        return 0
    try:
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        source = _session_source(session_id, path) if session_wide else _records_source(pending)
        closing = (lambda: [t for t in [_digest().unrated_line(session_id)] if t]) if session_wide else None
        return wake_loop(source, ack_path=path, await_launch=await_launch, closing=closing) \
            if wake or any(is_run_dir(p) for p in pending) else _stream(pending)
    finally:
        deregister(path, identity)


if __name__ == "__main__":
    sys.exit(main())
