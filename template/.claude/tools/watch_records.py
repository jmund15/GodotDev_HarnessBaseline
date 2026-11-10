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
DIED (not one this session stopped with `reap.py`, listed in `sidecar_stopped_launches`: it prints `STOPPED <label>` at the next wake, and a unit whose every job was stopped wakes nobody); a detached job's `waiting-memory` journal row (`WAITING-MEMORY` with its readings) prints as context, since the
launcher resumes by itself; a record's `stalled` journal row appearing while its upstream traffic is stale (`UPSTREAM_LIVE_S`); a record's `usage-limit` journal row prints as context
(`USAGE-LIMIT <label> <transport> resets <UTC time|unknown>`, the launcher's first usage-limit retry; the job's DONE is the wake); a running record whose stream (`<record>.stream`, else `<label>.stream`) has sat on a result written
after its launch for `sidecar_resume_check.RESULT_LINGER_S` (`LINGERING <label>`, the reading `sidecar_fanout.py --status`
gives the same run); a fan-out's own process gone while it still lists jobs
that will never start; or every record of one dispatch unit (one `wakeWith` group or ungrouped job of a fan-out, one benchmark campaign, or one
direct launch) ending; or, while work still runs, `CHECKIN_SECONDS` after the later of the arm and the session transcript's last write (50 min by default, inside the
orchestrator's one-hour prompt cache and before its idle compaction (`_hook_state.ORCHESTRATOR_CHECKIN_SECONDS`); `WATCH_RECORDS_CHECKIN_SEC` overrides it), printing a
`RUNNING` line per job with its age and last write, a `progress:` clause line under it (`agent_progress.clause`: the measures its
stream shows, `progress unreadable` when the stream cannot be parsed) and `CHECKIN_RULE`, the sentence that says how to judge
each clause. `--digest <record|stream|transcript>` prints the whole-run view the clause summarizes (`agent_progress.digest`).
The live set also holds this session's running
Bash/PowerShell background tasks (a main-session launch row with no `<task-notification>` yet, read by `wake_digest.running_shells`; a task that ran this watcher is skipped), each printed as `RUNNING shell <id>`, and its running
Workflow runs (a pending `workflow` entry whose host record `<session>/workflows/<id>.json` is absent or not
in `DONE_STATES`, or a run dir under `<session>/subagents/workflows/` with no entry, no host record and a write
within `DISCOVERED_RUN_SECONDS`: the launch hook's entry can land after the arm), each printed as `RUNNING workflow <name> <runId> <pins> for <n> min, agents <k> of <m> done`;
a run's end wakes nobody, and a session watcher with nothing live stays armed. It ends only when nothing can receive its wake: the shell that carries its output (its parent) or the session host that owns that shell is gone (`HOST GONE: <reason>`, read from the client's session registry; `WATCH_RECORDS_HOST_SEC` sets the check interval), which also ends a duplicate still waiting to take over. A running
seat that compacts (a `compact_boundary` row in its transcript: a Workflow agent's `agent-<id>.jsonl`, a Claude sidecar's
`<record>.stream`, a live roster's running bench cell's `<cell>.progress.jsonl`) adds `<label> compacted <n> (<m> min compacting)` to its check-in line, and one that compacted again after
re-reading a file it had read before the previous compaction, with no file written since that compaction (the cycle: a seat
that loses what it gathered, refills its window and compacts again), wakes the session with
`COMPACTING [workflow <runId>] <label> compacted <n>, re-read <file>`, once per new compaction count. A seat that wrote
its findings before compacting lost nothing, so its compaction stays a check-in clause. A running Workflow seat whose `agent-<id>.jsonl` has not grown for `WORKFLOW_STALL_SECONDS`
(`WATCH_RECORDS_STALL_SEC` overrides it) wakes with `STALLED workflow <runId> <label> idle=<n> min`, once per stall: the
client's request for that seat never returned, and no engine watchdog ends it. A `--session --wake` call made while
another live session-wide watcher is registered waits to take over without exiting. A bare `--await-launch`
(or `--await-launch=<seconds>`) makes an explicit record watch that finds nothing running wait that long
before it exits `NONE`; the bare form waits `dispatch_table.launch_grace()`. Session watches stay armed without it.
A child no call of this session issued (a scoring daemon, a study's ruler, a delegate's own test launch: no `call` or `issued_at` on its entry, no fan-out, no bench campaign) ending cleanly wakes nobody: its parent reports its own end, and a failed child still wakes, except a memory-admission refusal (exit 12), a re-pay refusal (exit 15) and a plan-capacity stop (exit 10), which its caller retries or holds.
A zero-cost end before a result written while its transport's usage-limit marker was live (`refused_while_exhausted`) prints
as context and wakes nobody: the job that hit the limit spent and woke, and the unit's end still wakes.
A wake prints, under each ended job's `DONE`/`DONE workflow` line, the lines of `wake_digest.py`: the result head, the
verification status with its counts and the report path of a sidecar, fan-out job or Workflow run. A background shell
that ended since the transcript byte offset saved in session hook state (`sidecar_wake_shell_offset`; a session with none starts at the transcript's end) arrives as
`SHELL <id> <status> exit=<n>` with its output path, failure-marker lines and tail; a shell that ran this watcher is skipped.
A shell end whose task-notification already appears as a delivered main-session attachment is omitted. If that
leaves no work or unreported event, the armed watcher stays idle: exiting it would send a duplicate notification.
Ended Workflow runs and undelivered shells never trigger a decision wake: they print with the next wake, once. The last lines before `STILL RUNNING`
or `NONE` are `UNRATED <n>: <run:label>, ...` when the session has unrated dispatches.
Usage: python3 .claude/tools/watch_records.py --pane. The owner's live view: a background shell that polls the session's
`checkin_lines` every `PANE_SECONDS` (`WATCH_RECORDS_PANE_SEC` overrides it) and appends a frame, closed by a stamp line,
when they change or every `PANE_HEARTBEAT_SECONDS`. It appends, never rewrites: the desktop client reads
only the bytes past its saved offset of the output file. It does not exit while its session lives and no wake reads it,
and it ends `HOST GONE` when its session's host or parent shell is gone, like the session watcher;
the session's shells that ran this tool are not live work. It registers under `sidecar_pane`; a second pane beside a
live one exits COVERED. While work is live and no pane runs, a session wake's last lines carry `PANE none: ...`.
Any other flag is a usage error (exit 2), never a record path. A fan-out job that ends cleanly wakes the session on its own, once,
unless another job of its `wakeWith` group (the group its inventory row records) still runs, or its fan-out still runs an
after-seat stage; the group's last end wakes. A fan-out job with no group is its own unit.
Any other successful job whose unit still has running jobs wakes nobody.
A benchmark campaign's cells (direct launches labelled `<campaign>-<date>:<task>:<model>:r<n>`) are one unit: while
`<logs>/<campaign>/grid.log` has a `CELL` line after its last terminal `GRID` line and `grid.pid` is alive, the grid
has cells no pending entry names yet, so the cells that ended wake nobody and the watcher stays live. The unit
ends on `GRID COMPLETE|PAUSED|HALTED|LOCKED|ABORT` or a dead grid pid; a failed cell, DIED, stall and usage-limit
wake at once. A roster of several campaigns is one unit per campaign. A bench process tree registers none of
its seats (`SIDECAR_DISPATCH_OWNER=bench`), so `--session` also watches each live roster whose worker process
carries this session's `CLAUDE_CODE_SESSION_ID`: one `roster` unit per roster, live while its log is open, waking
on `ROSTER <state> <name>: <last line>` when it ends, except a clean end (`DONE`) while another roster of this session is live, which prints at the next wake (a roster not live at arm is `bench.py wait`'s). A detached bench job the session launched (`DETACHED <kind> pid=<n> log=<path>`: succession, probes, score) is a roster-shaped unit too: its launch writes `<logs>/detached/<pid>.json` with the launcher's `CLAUDE_CODE_SESSION_ID`, it is live while its pid lives, and it wakes on its log's `END` line, its pid's death with none (`DEAD`) or a park line as the log's last line (`HELD`). Its launch prints no `WAIT-CMD`, so this watcher owns its wait and check-in. An event already present
at arm prints once; only an event acknowledged in session hook state suppresses the exit. A wake acknowledges
the decision events it reports, so a watcher armed in any later turn does not exit on them (it still prints
them as context), and every clean `DONE <label> exit=0` line it prints, which a later watcher omits.
An explicit record watch with nothing that can still progress prints `NONE` and exits 0; a session watch stays
armed until a new decision event or a live-work check-in. A job whose fan-out process
is gone is not "queued", because it will never start. An entry still unresolved
`UNRESOLVED_GRACE_SECONDS` after its launch prints `UNRESOLVED <label>` and wakes the session. A fan-out entry
that started nothing — a refusal file in its out dir names its label, or a later-issued call for the same label
allocated a record — is that call's leftover and prints nothing (`superseded`); the out dir plays no part, because
a corrected relaunch may write elsewhere.
A launch whose spend-ledger finish row (lib/sidecar_common.sh `sc_write_finish_row`) says
`ended-before-result` has ended: it prints `ENDED <label> exit=<n> <reason>` once and wakes the
session like a non-zero DONE; a `finished-without-record` row ends it as `DONE <label> exit=<n>`.
The Stop hook removes a pending entry whose `.exit` exists and tells the user only, so a watcher armed afterwards
finds no entry. The watcher then reads the ledger: a failed finish row of this session (`ended-before-result` or a
non-zero exit) that no pending entry carries and that is younger than `dispatch_table.STALE_SECONDS` prints the same
line (`unreported_ends`). A pending entry of that label that began a later attempt suppresses the line, because the
end was a refusal its relaunch answers. A failed end that a later clean spend row of its label supersedes (that relaunch finished its result) is consumed and prints nothing. The end has one identity (`ended_identity`) whichever reader finds it, so an end that woke
the session through its entry is not reported again, and an acknowledged one stays a quiet line until its row ages out.
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _hook_state  # noqa: E402
import exhausted_marker  # noqa: E402

POLL_SECONDS = float(os.environ.get("WATCH_RECORDS_POLL_SEC", "0.5"))
# How often a waiting watcher tests whether anything can still receive its wake. The arm-time probe lists each
# login's `sessions` folder (11 small rows on this machine) and measured 0.92 ms; later probes test two pids,
# 0.04 ms (measured 2026-11-10). 10 s bounds a dead session's watcher to that long at under 0.001% of a core.
HOST_CHECK_SECONDS = float(os.environ.get("WATCH_RECORDS_HOST_SEC", "10"))
# The store glob that answers a missing transcript walked 4513 project folders in 0.34 s (measured 2026-11-10).
# A session's transcript exists before any command in it runs, so a miss is a fixture or a typo, not a race.
TRANSCRIPT_RETRY_SECONDS = 10.0
# The orchestrator's prompt cache lives one hour: a wake after it rewrites the whole context, a wake
# before it reads the cache at a tenth of the input price. `_hook_state.ORCHESTRATOR_CHECKIN_SECONDS` owns the value.
CHECKIN_SECONDS = float(os.environ.get("WATCH_RECORDS_CHECKIN_SEC", _hook_state.ORCHESTRATOR_CHECKIN_SECONDS))
UNRESOLVED_GRACE_SECONDS = 300
# A false "never started" tells the owner a launched fan-out failed; a late notice costs only the wait. So the
# window is a multiple of the slowest measured preflight (52 s over 57 fan-out runs, issue to inventory write),
# not its typical time: 600 s is over 10x that. A change in the preflight (process-table scan, machine lock)
# needs a re-measure before this moves.
FANOUT_PREFLIGHT_S = 600
# A Workflow seat whose transcript has not grown for this long while its run is alive is a request that never
# returned. Basis: a completed Haiku seat compaction, the longest observed gap between rows of a healthy seat,
# took 3-6 min (A2b rail250 replay, 2026-10-14); the stalled seats of that run and of H3 sat 31+ min.
WORKFLOW_STALL_SECONDS = float(os.environ.get("WATCH_RECORDS_STALL_SEC", "600"))
# A run found on disk with no table entry counts as alive until nothing in it has been written for twice the stall
# window: the first window wakes the session with STALLED, the second presumes a crash that left no end record.
DISCOVERED_RUN_SECONDS = 2 * WORKFLOW_STALL_SECONDS
ENDED = "ended-before-result"
# lib/sidecar_common.sh exit 12: memory admission refused before anything ran. A background child's caller (the
# scoring daemon, the grid's bench_supervised_launch) retries it, so its end is context; a called launch still wakes.
ADMISSION_REFUSED = "12"
# Exit 15: the re-pay check refused finished work before anything ran; the scoring daemon holds the cell and reports it.
REPAY_REFUSED = "15"
# Exit 10: a plan-capacity stop (dispatch floor, spent allowance, usage limit); a background child's caller (the
# scoring daemon's quota wait) sleeps to the reset and retries, so each child's refusal is context.
CAPACITY_REFUSED = "10"
CALLER_HANDLED = frozenset({ADMISSION_REFUSED, REPAY_REFUSED, CAPACITY_REFUSED})
ARM_COMMAND = "python3 .claude/tools/watch_records.py --session --wake"
# The terminal line of a watcher whose session can no longer receive its wake; `%s` is the reason.
HOST_GONE = "HOST GONE: %s; nothing can receive this wake, so the watcher exits"
ARM_CALL = "as its own Bash call with run_in_background: true and timeout 7200000"
PANE_COMMAND = "python3 .claude/tools/watch_records.py --pane"
PANE_CALL = "as its own Bash call with run_in_background: true and timeout 86400000"
PANE_KEY = "sidecar_pane"  # the live pane's process line, in session hook state
PANE_TITLE = "sidecar pane"
PANE_SECONDS = float(os.environ.get("WATCH_RECORDS_PANE_SEC", "20"))
PANE_HEARTBEAT_SECONDS = 600  # an unchanged pane reprints so its stamp shows it is alive
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
    "`watch_records.py --digest <record or transcript>` prints a job's whole course. Then re-arm (the RE-ARM NOW line).")


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


# How much later than its stamp a live pid's creation may read before the pid counts as reused. The stamp is
# floored to a second. Windows reads an exact creation time; Linux derives it from /proc's whole-second boot
# time, which also moves a second when the wall clock slews, so a launcher stamped just after it started
# can read nearly two seconds late. A reused pid starts after the old process died, far later than that.
START_SLACK_S = 1 if os.name == "nt" else 3


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
    return started is None or (stamp > 0 and started > stamp + START_SLACK_S)


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


def _registry_started(row):
    """A session-registry row's process start (`procStart`, 100 ns units since 1601) as epoch seconds, or 0.0."""
    try:
        return int(row["procStart"]) / 1e7 - 11644473600
    except (KeyError, TypeError, ValueError):
        return 0.0


def _registry_rows():
    """Every session-registry row this machine's logins hold: `<login>/sessions/<pid>.json`."""
    import model_registry
    try:
        logins = model_registry.login_dirs()
    except Exception:  # an unreadable model registry must never end a watcher: see host_anchor's posture
        return
    for login in logins:
        folder = os.path.join(login, "sessions")
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json"):
                continue
            row = _hook_state.read_json_salvage(os.path.join(folder, name))
            if isinstance(row, dict):
                yield row


def host_anchor(session_id):
    """(pid, creation epoch) of a live process the session registry names for `session_id`, else None.

    The client writes one row per live host into its login's `sessions/` folder: the host's pid, the session id
    and its process start. A row whose pid is gone, or whose pid now runs a process that started later, anchors
    nothing. No row at all means the host is not observable (an old client, or a proof fixture), and the caller
    enforces nothing: only a positive registry row ever ends a watcher."""
    try:
        for row in _registry_rows():
            pid = row.get("pid")
            if row.get("sessionId") != session_id or not isinstance(pid, int):
                continue
            live = _process_started(pid)
            if live is None:
                continue
            started = _registry_started(row)
            if started and live and abs(live - started) > START_SLACK_S:
                continue
            return pid, started or live
    except Exception:  # fail open: nothing observed means nothing enforced, never an ended watcher
        return None
    return None


class Lifetime:
    """Whether anything can still receive this watcher's wake.

    A wake lands only if two processes live: the shell that caught this watcher's output (its parent, opened by
    the session's Bash tool) and the session host behind that shell. The host is read once from the session
    registry at arm, then both are tested for liveness `HOST_CHECK_SECONDS` apart. A live pid whose process
    start no longer matches is a reused pid, not a survivor.
    """

    def __init__(self, session_id, now=None):
        self.session = session_id
        self.parent = os.getppid()
        self.parent_start = _process_started(self.parent) if self.parent else None
        self.anchor = host_anchor(session_id)
        self.checked = time.time() if now is None else now
        self.gone = None

    def verdict(self, now=None):
        """None while a receiver may still exist, else the reason this watcher cannot be heard."""
        now = time.time() if now is None else now
        if self.gone is None and now - self.checked >= HOST_CHECK_SECONDS:
            self.checked = now
            self.gone = self._check()
        return self.gone

    def _check(self):
        if self.parent:
            started = _process_started(self.parent)
            if started is None or (self.parent_start and started
                                   and abs(started - self.parent_start) > START_SLACK_S):
                return "the shell that carries its output is gone (pid %d)" % self.parent
        if self.anchor is None:
            self.anchor = host_anchor(self.session)
            return None
        pid, started = self.anchor
        live = _process_started(pid)
        if live is None or (started and live and abs(live - started) > START_SLACK_S):
            return "no live session host serves session %s (host pid %d)" % (self.session[:8], pid)
        return None


_TRANSCRIPTS = {}  # session id -> (path or None, the time a miss may be retried)


def _transcript_path(session_id):
    """The session's transcript file, resolved once and then kept; a miss is retried TRANSCRIPT_RETRY_SECONDS
    apart instead of on every poll."""
    path, retry = _TRANSCRIPTS.get(session_id, (None, 0.0))
    if path and os.path.isfile(path):
        return path
    if time.time() < retry:
        return None
    path = _digest().transcript_path(session_id)
    _TRANSCRIPTS[session_id] = (path, time.time() + TRANSCRIPT_RETRY_SECONDS)
    return path


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


def journal_rows(record):
    """The parsed rows of `<record>.journal.jsonl`, oldest first. An unparseable line is skipped."""
    rows = []
    try:
        with open(str(record) + ".journal.jsonl", encoding="utf-8", errors="replace") as fh:
            for text in fh:
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def current_run(record):
    """The journal rows since the last `started` row: one launch's transitions. A relaunch on the same
    record appends after its own `started` row, so an earlier run's `waiting-memory`, `stalled` or
    `usage-limit` row is never read as this run's. Rows with no `started` are one unnamed run."""
    rows = journal_rows(record)
    for index in range(len(rows) - 1, -1, -1):
        if rows[index].get("event") == "started":
            return rows[index:]
    return rows


def ended_code(record):
    """The exit code a launch recorded, or None while it runs. The last `ended` row of `<record>.journal.jsonl`
    is the end; `<record>.exit` is the fallback for a record its launcher wrote before the journal existed. An
    `.exit` caught empty mid-write reads as "" (ended, code not yet written)."""
    ended = [row for row in journal_rows(record) if row.get("event") == "ended"]
    if ended:
        return str(ended[-1].get("code", ""))
    try:
        with open(str(record) + ".exit", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _journal_process_line(row):
    """A `started` row's identity in the `<stamp> [winpid=N ]pid=N` form _process_line_gone reads."""
    pid = row.get("pid")
    if os.name == "nt":
        return "%s winpid=%s pid=%s" % (row.get("ts", ""), row.get("winpid", pid), pid)
    return "%s pid=%s" % (row.get("ts", ""), pid)


def record_state(record, inventory=None):
    """This run's rung on the one "has it ended" ladder: (state, code, found_by).

    `state` is DONE, DIED, WATCH or NO-IDENTITY. `found_by` names the signal that decided it —
    `journal` (the launcher's `<record>.journal.jsonl`: an `ended` row, or the last `started` row's
    process), `exit`, `pid` or `inventory`, else "" — so a caller reading a different vocabulary for the
    same question maps this tuple instead of walking the files again, and a DIED or WATCH from the
    `pid` rung stays distinct from one found through the inventory.
    """
    rows = journal_rows(record)
    ended = [row for row in rows if row.get("event") == "ended"]
    if ended:
        return "DONE", str(ended[-1].get("code", "")), "journal"
    started = [row for row in rows if row.get("event") == "started"]
    if started:
        gone = _process_line_gone(_journal_process_line(started[-1]))
        if gone is not None:
            return ("DIED", None, "journal") if gone else ("WATCH", None, "journal")
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
    """The launcher's current admission-wait reading, or None when this run has none standing. The
    launcher writes one `waiting-memory` row per launch; its own next transition (the post-spawn
    `started`) puts that row behind the run's current slice once admission finishes."""
    for row in reversed(current_run(record)):
        event = row.get("event")
        if event in ("started", "ended"):
            return None
        if event == "waiting-memory":
            message = str(row.get("message") or "")
            return message if message.startswith(('WAIT memory headroom', 'STILL WAITING')) else None
    return None


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


_LEDGER_READS = {}  # (ledger path, session id) -> {"file": (st_dev, st_ino), "offset": int, "finish", "clean"}


def _ledger_rows(session_id):
    """This session's (finish rows, clean launch rows), oldest first.

    The ledger is append-only and shared by every session on the machine: at 34 MB over 16k rows, one poll that
    re-read it cost 0.25-0.53 s of CPU every 0.5 s (measured on this repository 2026-11-10). The first read parses
    the file; each later one parses only the bytes appended since, at a saved offset. A file that shrank or was
    replaced (its dev/inode changed) restarts from zero, and a half-written last line is left for the next call.
    """
    path = ledger_path()
    key = (path, session_id)
    read = _LEDGER_READS.get(key)
    try:
        info = os.stat(path)
        size, ident = info.st_size, (info.st_dev, info.st_ino)
    except OSError:
        _LEDGER_READS.pop(key, None)
        return [], []
    if read is None or read["file"] != ident or size < read["offset"]:
        read = _LEDGER_READS[key] = {"file": ident, "offset": 0, "finish": [], "clean": []}
    if size > read["offset"]:
        try:
            with open(path, "rb") as fh:
                fh.seek(read["offset"])
                data = fh.read(size - read["offset"])
        except OSError:
            return list(read["finish"]), list(read["clean"])
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            text = raw.decode("utf-8", "replace")
            if '"parentSessionId"' not in text:
                continue
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("parentSessionId") != session_id:
                continue
            if '"outcome"' in text:
                if row.get("outcome"):
                    read["finish"].append(row)
            elif row.get("label") and row.get("timestamp") and str(row.get("exitCode")) == "0":
                read["clean"].append(row)
        read["offset"] += end
    return list(read["finish"]), list(read["clean"])


def finish_rows(session_id):
    """This session's launcher finish rows (rows carrying `outcome`), oldest first; the incremental ledger read."""
    return _ledger_rows(session_id)[0]


def clean_launch_rows(session_id):
    """This session's spend rows of launches that finished clean (no `outcome`, exit 0), oldest first; the same
    incremental ledger read. A failed launch writes no spend row, so a clean row of a label is a later launch of
    it that ran to its result."""
    return _ledger_rows(session_id)[1]


_INVENTORY_WRITTEN = {}  # inventory path -> the creation stamp of its last successful read


def inventory_written(entry):
    """When the fan-out created the inventory that allocated `entry`'s record, as epoch seconds; None for a
    direct launch or an inventory no poll has read. It is the document's `writtenAt`, which the fan-out stamps
    once and every later save keeps, so saving the same inventory for another job does not move it; an inventory
    written before that field existed falls back to the file's mtime. The fan-out saves by renaming a temp file
    over this path, so a poll can be refused mid-rename: the last successful read answers, never the relocated
    file's mtime, which would move the attempt past the finish row of its own end. That attempt's finish row is
    written after it, so an earlier attempt's row never answers for a relaunch."""
    path = entry.get("inventory")
    if not path:
        return None
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (TypeError, OSError, ValueError, AttributeError):
            if attempt < 2:
                time.sleep(0.005)
            continue
        written = epoch(data.get("writtenAt"))
        if not written:
            try:
                written = os.path.getmtime(path)
            except (OSError, TypeError):
                return None
        if len(_INVENTORY_WRITTEN) > 128:
            _INVENTORY_WRITTEN.pop(next(iter(_INVENTORY_WRITTEN)))
        _INVENTORY_WRITTEN[path] = written
        return written
    return _INVENTORY_WRITTEN.get(path)


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


def attempt_start(entry):
    """When the entry's current attempt began: the latest of its launch, its record's inventory and its record's latest
    launch line, as epoch seconds; None when none of them parses."""
    return max((t for t in (_launch_time(entry), inventory_written(entry), record_launched(entry.get("record")))
                if t is not None), default=None)


def finish_row(entry, rows):
    """The last finish row for this entry's label written at or after its launch, its record's
    inventory and its record's latest launch line, else None."""
    label, found = (entry.get("jobs") or [{}])[0].get("label"), None
    since = attempt_start(entry)
    for row in rows:
        if row.get("label") == label and (since is None or epoch(row.get("timestamp")) >= since):
            found = row
    return found


def finish_text(label, row):
    """`ENDED <label> exit=<n> <reason>` for a launch that ended before a result, else its DONE line."""
    if row.get("outcome") == ENDED:
        return ("ENDED %s exit=%s %s" % (label, row.get("exitCode"), row.get("reason") or "")).rstrip()
    return "DONE %s exit=%s" % (label, row.get("exitCode"))


def _entry_label(entry):
    return (entry.get("jobs") or [{}])[0].get("label")


REFUSAL_GLOB = "fanout-*.refused.json"


def job_identity(entry):
    """The job a pending entry names, whichever out dir its call wrote: its first label. One identity
    for the Stop hook, the watcher and the rearm hook, so a relaunch into any out dir is the same job."""
    return _entry_label(entry)


def refusal_cover(entry):
    """The epoch time of the refusal file in this entry's out dir that names its label and was written
    at or after its call was issued, else None. A fan-out writes one when it refuses or crashes before
    starting any seat, so the entries its call registered can leave every reader's view."""
    out_dir, label, issued = entry.get("fanout_dir"), job_identity(entry), _launch_time(entry)
    if not out_dir or not label or issued is None:
        return None
    for path in glob.glob(os.path.join(glob.escape(str(out_dir)), REFUSAL_GLOB)):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            written = float(data.get("time"))
            labels = data.get("labels")
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if isinstance(labels, list) and str(label) in [str(item) for item in labels] and issued <= written:
            return written
    return None


def refused_launch(entry):
    """True when a refusal file in the entry's out dir proves its call started nothing."""
    return refusal_cover(entry) is not None


def call_open(entry, live_ids=frozenset(), now=None):
    """True while the call that registered `entry` may still launch its job: it is a live background
    task, or `FANOUT_PREFLIGHT_S` has not passed since its issue. The Stop hook's launched-nothing
    verdict reads it; `superseded` does not, because a later call's record already proves the job is
    watched and a foreground call cannot be re-issued before it returns."""
    now = time.time() if now is None else now
    if entry.get("task_id") and str(entry["task_id"]) in live_ids:
        return True
    issued = _launch_time(entry)
    return now - (issued if issued is not None else now) < FANOUT_PREFLIGHT_S


def superseded(entry, pending, live_ids=frozenset(), resolve=None):
    """True when nothing watches `entry` any more, so every reader drops it: a refusal file in its out
    dir names its label at or after its issue (the launch refused or crashed before any seat ran), or
    it allocated no record, its call is closed, and a later-issued entry of the same label has a
    record, in any out dir. The out dir plays no part, since a corrected relaunch may write elsewhere.
    An entry younger than `UNRESOLVED_GRACE_SECONDS` is never superseded: its call may still be
    starting, so a sibling's record proves nothing yet. A live background task's entry is kept too."""
    if entry.get("record"):
        return False
    if refused_launch(entry):
        return True
    if entry.get("task_id") and str(entry["task_id"]) in live_ids:
        return False
    issued, label = _launch_time(entry), job_identity(entry)
    if issued is None or not label or not entry.get("fanout_dir"):
        return False
    if time.time() - issued <= UNRESOLVED_GRACE_SECONDS:
        return False
    for other in pending:
        if other is entry or job_identity(other) != label:
            continue
        other_issued = _launch_time(other)
        record = (resolve or (lambda item: item.get("record")))(other)
        if record and other_issued is not None and other_issued > issued:
            return True
    return False


def failed_finish(row):
    return row.get("outcome") == ENDED or str(row.get("exitCode")) != "0"


def refused_while_exhausted(row):
    """True for a zero-cost end before a result written while its transport's usage-limit marker
    (sidecar_common.sh `sc_exhausted_marker`: <SIDECAR_EXHAUSTED_DIR or ~/.claude/sidecar-exhausted>/<transport>.json)
    named an expiry past the row's time: a queued job the launcher refused because the plan was already out. The job
    that hit the limit spent, so it still wakes; each refusal behind it repeats that fact and is context."""
    if row.get("outcome") != ENDED or not row.get("transport"):
        return False
    try:
        if float(row.get("costUSD") or 0) != 0:
            return False
        expires = (exhausted_marker.read(row["transport"]) or {}).get("expiresAt")
    except (TypeError, ValueError):
        return False
    return isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires > epoch(row.get("timestamp"))


def ended_identity(label, row):
    """The one event identity of a finish row, whichever reader finds it: the pending entry that still lists the job,
    or the ledger alone once the Stop hook consumed the entry. One identity is what keeps the second reader quiet."""
    return ("finish", label, row.get("timestamp"))


def unreported_ends(pending, rows, acknowledged, stale_seconds, now=None, launches=()):
    """One pending-shaped entry per failed finish row of this session that no pending entry carries, younger than
    `stale_seconds`. The Stop hook consumes a pending entry whose `.exit` exists and tells the user only, so a
    watcher armed afterwards finds nothing; the launcher's finish row is the end's remaining trace. `reported`
    marks a row whose identity is acknowledged: it stays in the poll as a quiet line so the acknowledgment is
    not pruned, and never wakes twice. `launches` (`clean_launch_rows`) answers for a consumed failure: a clean
    launch of its label whose spend row is later than the failure already took its job's result, so the failure is superseded."""
    now = time.time() if now is None else now
    carried = {id(entry["finish"]) for entry in pending if entry.get("finish")}
    watching = [entry for entry in pending if not superseded(entry, pending)]
    out = []
    for row in rows:
        label = row.get("label")
        if id(row) in carried or not label or not failed_finish(row) or now - epoch(row.get("timestamp")) > stale_seconds:
            continue
        if str(row.get("exitCode")) in CALLER_HANDLED:
            continue  # no entry carries it, so a child launch made it (a scoring daemon's seat), and its caller handles it

        if any(job_identity(entry) == label and (attempt_start(entry) or 0.0) > epoch(row.get("timestamp"))
               for entry in watching):
            continue
        if any(launch.get("label") == label and epoch(launch.get("timestamp")) > epoch(row.get("timestamp"))
               for launch in launches):
            continue  # consumed: a later clean launch of this label finished its result
        out.append({"kind": "sidecar", "id": "finish:%s:%s" % (label, row.get("timestamp")), "record": None,
                    "fanout_dir": None, "task_id": None, "jobs": [{"label": label}], "finish": row,
                    "reported": _wake_event_key(ended_identity(label, row)) in acknowledged})
    return out


def pending_sidecars(session_id, acknowledged=frozenset()):
    """This session's unfinished sidecar entries, each with its `record` resolved, minus a refused call's leftover
    (`superseded`), plus one entry per failed end the Stop hook consumed (`unreported_ends`).

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
    kept = [entry for entry in pending if not superseded(entry, pending)]
    return kept + unreported_ends(kept, rows, acknowledged, dispatch_table.STALE_SECONDS,
                                  launches=clean_launch_rows(session_id))


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


def running_writers(session_id, repo=None, own_launch=None):
    """Live author/fold dispatches for close; no session means no wait. With `repo`, only dispatches
    with a job writing in that checkout: a writer in another checkout cannot change the commits it proves.
    With `own_launch`, the dispatch whose run record names that launch id is the caller itself: it never waits on itself."""
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
        if own_launch and entry.get("record") and record_launch(entry["record"]) == own_launch:
            continue
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


WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _wrote(row):
    """True when one assistant transcript row calls a file-writing tool."""
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return any(isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in WRITE_TOOLS
               for block in (content if isinstance(content, list) else []))


def agent_compaction(path):
    """One agent transcript (a Workflow `agent-<id>.jsonl` or a sidecar `<record>.stream`):
    {'count': compact_boundary rows, 'minutes': their summed duration or None when no row carries one,
    'reread': basename of a file read after the previous boundary that an earlier segment also read, taken from
    the segment the LAST boundary closed, else None, 'saved': whether that same segment wrote a file}. A non-None
    'reread' is the compaction cycle: the seat lost its subject and fetched it again. A cycle with 'saved' kept what
    it found in a file before compacting, so the compaction lost nothing. Unparseable lines (a partly written last
    line) are skipped; a missing file is zero compactions. Cached per (size, mtime): a poll re-reads only a
    transcript that grew."""
    out = {"count": 0, "minutes": None, "reread": None, "saved": False}
    try:
        stat = os.stat(path)
    except OSError:
        return out
    stamp = (stat.st_size, stat.st_mtime_ns)
    cached = _COMPACTION_CACHE.get(path)
    if cached and cached[0] == stamp:
        return dict(cached[1])
    earlier, segment, again, wrote, duration = set(), set(), None, False, None
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b"compact_boundary" not in raw and b'"tool_use"' not in raw:
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
                    out["saved"] = wrote
                    earlier |= segment
                    segment, again, wrote = set(), None, False
                elif row.get("type") == "assistant":
                    for read in _read_paths(row):
                        if os.path.normcase(read) in earlier and again is None:
                            again = os.path.basename(read)
                        segment.add(os.path.normcase(read))
                    wrote = wrote or _wrote(row)
    except OSError:
        return {"count": 0, "minutes": None, "reread": None, "saved": False}
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
    `GRID` line and the grid.pid process is alive. A missing log, a dead pid, a pid no probe has seen
    alive, or any error reads False, so the unit ends and wakes. A probe that cannot answer (`ps` timing
    out on a loaded machine) keeps the pid's last answer and retries next window. The watcher polls this at POLL_SECONDS, so the log is
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
                answer, last = bench_lib._pid_alive(pid), _GRID_SEEN.get(log)
                if answer is None and last and last[2] == pid:
                    alive, probed = last[3], last[4]
                else:
                    alive, probed = answer is True, time.monotonic()
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
            block = subprocess.run([shell, "-c", "/usr/bin/cat /proc/%d/environ" % pid], capture_output=True,
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


DETACHED_PARK_RE = re.compile(r"^(?:\[[^\]]*\]\s+)?WAIT (?!ING)\S")
# A dead job's registry row outlives every watcher that could still report its end: a session that resumes after
# a week reads the job's log, not its row.
DETACHED_KEEP_S = 7 * 86400


def _still_for(path, seconds):
    """True when the file at `path` is missing or unmodified for `seconds`."""
    try:
        return time.time() - os.path.getmtime(path) >= seconds
    except (OSError, TypeError):
        return True


def _bench_lib():
    """The project's bench library, or None where the checkout carries no benchmark campaign."""
    scripts = os.path.join(os.path.dirname(HERE), "scripts", "benchmark_campaign", "scoring")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import bench_lib
    except ImportError:
        return None
    return bench_lib


def detached_row(entry, alive):
    """The roster-shaped row (`name`, `state`, `last`) of one detached bench job's registry entry, or None when its
    log is missing. The run is the log's current `START` run: it ends on that name's `END ok|halt|failed`
    (DONE or HALTED), else on the worker's death (DEAD) or a park line as the last line (HELD, the same stop
    `bench wait` exits 3 on). Otherwise it is LIVE."""
    try:
        with open(entry["log"], encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return None
    _owner, _at, end = _bench_lib().run_bounds(lines)
    last = lines[-1][:120] if lines else "-"
    if end:
        state = "DONE" if end["status"] == "ok" else "HALTED"
        last = end.group(0)
    elif alive is False:
        state = "DEAD"
    elif DETACHED_PARK_RE.match(lines[-1] if lines else ""):
        state = "HELD"
    else:
        state = "LIVE"
    return {"name": "%s-%s" % (entry["kind"], entry["pid"]), "state": state, "last": last, "age": 0,
            "terminal_status": end["status"] if end else None, "terminal_line": end.group(0) if end else None}


def session_detached(session_id, bench_lib, pid_alive=None):
    """`{"roster:<kind>-<pid>": unit}` for each detached bench job (succession, probes, score) this session launched.

    The launch (`bench_lib.announce_detached`) writes `<logs>/detached/<pid>.json` with the launching session's
    `CLAUDE_CODE_SESSION_ID`; a row of another session is not this one's. A job is one roster-shaped unit, so it
    renders, stays live and wakes as a roster does. A row of any session whose worker is dead and whose log has
    been still for `DETACHED_KEEP_S` is deleted here, so the registry stays bounded. A shared log (the probes log,
    a campaign's score log, a resumed study's run log) holds only its latest launch's run, so a row whose log a
    later-started row also names is superseded whatever its pid answers (Windows reuses pids): it prints nothing,
    as a fan-out's dead entry does once a later launch of its label runs."""
    try:
        import daemon_lib
    except ImportError:
        return {}
    probe = pid_alive or daemon_lib.native_pid_alive
    entries = []
    for path in glob.glob(os.path.join(bench_lib.detached_registry(), "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
            alive = probe(int(entry["pid"]))
            if alive is False and _still_for(entry.get("log"), DETACHED_KEEP_S):
                os.remove(path)
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        entries.append((entry, alive))
    latest = {}
    for entry, _alive in entries:
        key = os.path.normcase(os.path.abspath(str(entry.get("log"))))
        latest[key] = max(latest.get(key, float("-inf")), float(entry.get("started") or 0))
    units = {}
    for entry, alive in entries:
        if entry.get("session") != session_id:
            continue
        key = os.path.normcase(os.path.abspath(str(entry.get("log"))))
        if float(entry.get("started") or 0) < latest[key]:
            continue
        try:
            row = detached_row(entry, alive)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if row:
            units["roster:" + row["name"]] = {"roster": row, "records": [], "cells": []}
    return units


def session_rosters(session_id, pid_alive=None, verdicts=None):
    """A poll function returning `{"roster:<name>": unit}` for each bench roster this session started.

    A roster belongs to the session whose id its worker process (`<logs>/<name>.pid`) carries; the
    environment is read once per name and pid. A roster found live stays listed after it ends, so its end
    wakes the watcher once. The roster logs are re-read at most once per `ROSTER_RECHECK_S`. A caller that
    polls once per process (the Stop gate) passes `verdicts` to keep each "<name>:<pid>" ownership read
    across polls, and `pid_alive` to share one liveness probe.
    """
    owned, checked, last = {}, ({} if verdicts is None else verdicts), [None, {}]

    def poll():
        now = time.monotonic()
        if last[0] is not None and now - last[0] < ROSTER_RECHECK_S:
            return last[1]
        try:
            bench_lib = _bench_lib()
            if bench_lib is None:
                last[:] = [now, {}]
                return last[1]
            logs = bench_lib.roots.logs_dir()
            rows = bench_lib.roster_states(logs, pid_alive=pid_alive)
            units = session_detached(session_id, bench_lib, pid_alive)
            for row in rows:
                name = row["name"]
                if name not in owned and row["state"] == "LIVE":
                    pid = bench_lib._roster_pid(os.path.join(logs, name + ".pid"))
                    key = "%s:%s" % (name, pid)
                    if pid is not None and key not in checked:
                        owner = process_session(pid)
                        # An unreadable owner is asked again next Stop; only a definite answer is kept.
                        if owner is not None:
                            checked[key] = owner == session_id
                    if pid is not None and checked.get(key):
                        owned[name] = pid
                if name in owned:
                    live = row["state"] in ROSTER_LIVE_STATES
                    units["roster:" + name] = {"roster": row, "records": [],
                                               "cells": bench_lib.roster_running_cells(logs, name) if live else []}
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
    if not fanout_dir:
        return "id:" + str(entry.get("id"))
    return _fanout_key(fanout_dir, entry.get("call"))


def _fanout_key(fanout_dir, call):
    """A fan-out unit is one launch: its spill dir plus the tool call that issued it, since a later launch
    may reuse a spill dir an earlier, finished fan-out of the session wrote to."""
    return str(_norm_record(fanout_dir)) + ("|call:%s" % call if call else "")


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
                                   "bench": bench_campaign(entry), "called": False, "stopped": set(),
                                   "call": entry.get("call") if entry.get("fanout_dir") else None})
        if entry.get("call") or entry.get("issued_at") is not None:
            unit["called"] = True
        if entry.get("reported"):
            unit["reported"] = True
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


def group_holds(unit, label):
    """True while a job that shares `label`'s `wakeWith` group in this fan-out's inventory is provably still running.

    The group is a fact the fan-out wrote on each inventory row; it is never inferred. A member the ladder cannot
    place (`NO-IDENTITY`) does not hold, so an unreadable job never silences a finished result."""
    jobs = _inventory(unit)[1]
    group = next((j.get("wakeWith") for j in jobs if j.get("label") == label), None)
    if not (isinstance(group, str) and group):
        return False
    return any(record_state(j["recordPath"])[0] == "WATCH" for j in jobs
               if j.get("label") != label and j.get("wakeWith") == group and j.get("recordPath"))


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
             and ended_code(job["recordPath"]) is None]
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
    """The idle seconds this run's latest `stalled` row recorded, or None when no stall stands. A
    `resumed` row or the run's end clears it."""
    for row in reversed(current_run(record)):
        event = row.get("event")
        if event == "stalled":
            return str(row.get("idle", "?"))
        if event in ("resumed", "ended"):
            return None
    return None


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
    """`USAGE-LIMIT <label> <transport> resets <time|unknown>` from this run's `usage-limit` row, or None."""
    row = next((row for row in reversed(current_run(record)) if row.get("event") == "usage-limit"), None)
    if row is None:
        return None
    reset = row.get("resetsAt")
    when = (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset))
            if isinstance(reset, (int, float)) and reset > 0 else "unknown")
    return "USAGE-LIMIT %s %s resets %s" % (label, row.get("transport") or "unknown", when)


POLL_MARK_ENV = "WATCH_RECORDS_POLL_MARK"


def _mark_poll(mark):
    """Append one line to the proof's poll mark: `WATCH_RECORDS_POLL_MARK` names a file a proof reads to know a
    poll's reads finished, instead of sleeping. Unset in a session; nothing the watcher prints changes."""
    if mark:
        try:
            with open(mark, "a", encoding="utf-8") as fh:
                fh.write(".\n")
        except OSError:
            pass


def _stream(pending):
    """Each record's ending, once, then ALL REPORTED."""
    warned = set()
    mark = os.environ.get(POLL_MARK_ENV)
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
        _mark_poll(mark)
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
                    if agent["reread"] and not agent["saved"]:
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
        if unit.get("running_shell"):
            live += 1
            continue
        if unit.get("shell"):
            lines.append(("shell", ("shell", unit["shell"]["task_id"]), _digest().shell_text(unit["shell"]), False))
            continue
        if unit.get("roster"):
            row = unit["roster"]
            if row["state"] in ROSTER_LIVE_STATES:
                live += 1
                for cell, stream in unit.get("cells") or []:
                    compaction = agent_compaction(stream)
                    if compaction["reread"] and not compaction["saved"]:
                        lines.append(("compacting", ("compacting", stream, compaction["count"]),
                                      "COMPACTING roster %s %s compacted %d, re-read %s"
                                      % (row["name"], cell, compaction["count"], compaction["reread"]), True))
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
                identity = ("end", record)
                if state == "DONE" and finish and finish.get("outcome") == ENDED:
                    text = finish_text(label, finish)
                    identity = ended_identity(label, finish)
                # A job the session stopped with reap.py is context; a DIED carrier still alive stays a wake below.
                stopped = _norm_record(record) in (unit.get("stopped") or ())
                failed = state != "DONE" or code != "0"
                if stopped:
                    text = "STOPPED %s (stopped by this session)" % label
                elif state == "DONE" and not (finish and finish.get("outcome") == ENDED):
                    brief = next((j.get("brief") for j in _inventory(unit)[1] if j.get("label") == label), None)
                    text = "\n".join([text] + _digest().sidecar_lines(label, record, unit.get("fanout_dir"), brief))
                retried = (_background_child(unit) and code in CALLER_HANDLED) or bool(finish and refused_while_exhausted(finish))
                # A fan-out job's clean end wakes unless its fold stage is still to run or a member of its
                # `wakeWith` group runs: its result is actionable alone.
                alone = (state == "DONE" and not failed and not stopped and bool(unit.get("fanout_dir"))
                         and not unit.get("folding") and not group_holds(unit, label))
                lines.append(("end", identity, text, "once" if alone else failed and not stopped and not retried))
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
                lines.append(("usage", ("usage", record), usage, False))
            lingering = result_lingering(record, now)
            if lingering is not None:
                lines.append(("lingering", ("lingering", record),
                              "LINGERING %s: its stream ended on a result %d min ago and its launcher still runs "
                              "(a background task can re-wake the child for billed turns); stop it with tools/reap.py"
                              % (label, lingering // 60), True))
            stream = record_stream(record)
            compaction = agent_compaction(stream) if stream else None
            if compaction and compaction["reread"] and not compaction["saved"]:
                lines.append(("compacting", ("compacting", record, compaction["count"]),
                              "COMPACTING %s compacted %d, re-read %s"
                              % (label, compaction["count"], compaction["reread"]), True))
        key = _unit_key_of(unit) if unit["records"] or unit.get("fanout_dir") else "unit:%s" % ukey
        for label, row in unit.get("finished") or []:
            ended += 1
            lines.append(("end", ended_identity(label, row), finish_text(label, row),
                          failed_finish(row) and not unit.get("reported") and not refused_while_exhausted(row)
                          and not (_background_child(unit) and str(row.get("exitCode")) in CALLER_HANDLED)))
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
            lines.append(("complete", ("complete", key), "",
                          not _background_child(unit) and not all_stopped and not unit.get("reported")))
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
        if unit.get("running_shell"):
            out.append("RUNNING shell %s" % unit["running_shell"])
            continue
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
                for cell, stream in unit.get("cells") or []:
                    # A bench arm is judged like any job: its stream's progress clause, then its compaction.
                    clause = progress_clause(stream, now)
                    if clause:
                        out.append("  %s: %s" % (cell, clause))
                    compaction = agent_compaction(stream)
                    if compaction["count"]:
                        out.append("  %s %s" % (cell, compaction_clause(compaction)))
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
    if unit.get("fanout_dir"):
        return _fanout_key(unit["fanout_dir"], unit.get("call"))
    return "records:" + ",".join(
        sorted(str(_norm_record(record)) for _, record in unit["records"]))


def _wake_event_key(identity):
    incarnation = None
    if identity[0] in ('end', 'stall', 'usage', 'identity', 'memory', 'lingering', 'compacting'):
        try:
            incarnation = os.stat(str(identity[1]) + '.pid').st_mtime_ns
        except OSError:
            pass
        if identity[0] == 'usage':
            row = next((row for row in reversed(current_run(identity[1]))
                        if row.get("event") == "usage-limit"), None)
            incarnation = [incarnation, row.get("ts") if row else None]
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


def _still_running(live, settle=None):
    """The wake's last lines. This watcher exits as it wakes, so with work still live nothing watches until the
    orchestrator arms the next one: the command closes the output, where the reader acts. `settle` runs between
    the two lines and ends everything that keeps this watcher registered, so the re-arm the second line asks
    for is not refused as COVERED."""
    print("STILL RUNNING %d" % live, flush=True)
    if settle:
        settle()
    if live:
        print("RE-ARM NOW: %s %s" % (ARM_COMMAND, ARM_CALL), flush=True)


def _idle_since(source, armed):
    """When the orchestrator last spent its prompt cache: the later of the arm and its transcript's last write.
    A turn taken after the arm (an owner prompt) already refreshed the cache, so the check-in waits from that turn."""
    path = getattr(getattr(source, "shells", None), "path", None)
    try:
        return max(armed, os.path.getmtime(path)) if path else armed
    except OSError:
        return armed


def wake_loop(source, *, ack_path=None, await_launch=0.0, closing=None, unregister=None, lifetime=None):
    """Poll `source()` (a `{key: unit}` map) and apply the wake rule. Returns the exit code.

    A wake that ends in `STILL RUNNING` finishes its state writes and calls `unregister()` before it prints
    `RE-ARM NOW`, so the line never outlives the watcher's registration.

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
    non-triggering lines of the same once-only kind. `closing(live)`, when given, returns the lines printed after the
    held lines at every wake and before its terminal line (the session's unrated dispatches, the pane hint).
    """
    seen, standing_stalls, deferred, first = set(), set(), [], True
    armed, printed, said, reported = time.time(), [], [], _acknowledged(ack_path) if ack_path else set()
    mark = os.environ.get(POLL_MARK_ENV)

    def say(kind, identity, text, triggers):
        print(text, flush=True)
        said.append(identity)
        if kind in REPORTED_ONCE_KINDS and triggers in (False, "once"):
            printed.append(identity)

    def leave(code=0):
        _acknowledge(ack_path, printed)
        shells = getattr(source, "shells", None)
        if shells is not None:
            shells.persist()
        return code

    def rearm(live):
        def settle():
            leave()
            if unregister:
                unregister()
        _still_running(live, settle)
        return 0

    def close(live=0):
        for text in (closing(live) if closing else ()):
            print(text, flush=True)

    def release(held, lines, standing, live=0):
        """Print the held lines at a wake. A held reading of a standing condition (a memory wait, a stall,
        a usage retry) prints the condition's latest line, and one whose condition has cleared is dropped:
        the first sample of a wait that ended is a misreport."""
        latest = {identity: text for _, identity, text, _ in lines}
        for kind, identity, text, triggers in held:
            if identity in standing:
                say(kind, identity, latest.get(identity, text), triggers)
            elif kind not in ("memory", "stall", "usage"):
                say(kind, identity, text, triggers)
        close(live)
    while True:
        polled = time.time()
        if lifetime:
            gone = lifetime.verdict(polled)
            if gone:
                print(HOST_GONE % gone, flush=True)
                return leave()
        grouped = source()
        lines, live, standing = _wake(grouped)
        _mark_poll(mark)
        current = {identity for _, identity, _, _ in lines}
        deferred = [line for line in deferred if line[0] != "shell" or line[1] in current]
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
            if kind in REPORTED_ONCE_KINDS and triggers in (False, "once") and _wake_event_key(identity) in reported:
                continue
            if triggers and _wake_event_key(identity) in fresh:
                woke = True
            if text:
                if triggers or first:
                    say(kind, identity, text, triggers)
                else:
                    deferred.append((kind, identity, text, triggers))
        if not live and not woke and getattr(source, "shells", None) is not None:
            # A session watch exits only with a decision event; an idle exit is itself a paid notification.
            first = False
            time.sleep(POLL_SECONDS)
            continue
        if first and not woke and live == 0 and polled - armed < await_launch:
            pass  # a poll that began inside the grace re-polls, so a launch landing during a slow poll is still seen
        elif first and not woke:
            first = False
            if live == 0:
                if said:
                    close()
                print("NONE", flush=True)
                return leave()
        elif woke:
            release(deferred, lines, standing, live)
            return rearm(live)
        elif live and time.time() - _idle_since(source, armed) >= CHECKIN_SECONDS:
            release(deferred, lines, standing, live)
            for text in checkin_lines(grouped):
                print(text, flush=True)
            print("CHECKIN after %d min: %s" % ((time.time() - _idle_since(source, armed)) // 60, CHECKIN_RULE), flush=True)
            return rearm(live)
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
    notification. Delivered ends are removed even when delivery follows an earlier poll; `delivered` records
    that an idle exit would duplicate the client's notification. `wake_digest` reads each appended byte once."""

    def __init__(self, session_id, state_path):
        self.session_id, self.state_path = session_id, state_path
        self.path, self.notices, self.delivered = None, {}, False
        self.scan = _digest().new_shell_scan()
        saved = (_hook_state.read_json_salvage(state_path) or {}).get(SHELL_OFFSET_KEY) if state_path else None
        self.offset = saved if isinstance(saved, int) and not isinstance(saved, bool) else None

    def poll(self):
        if self.path is None:
            self.path = _transcript_path(self.session_id)
        if self.path is None:
            return self.notices
        if self.offset is None:
            self.offset = os.path.getsize(self.path)
        found, self.offset = _digest().shell_notices(self.path, self.scan, self.offset)
        for notice in found:
            self.notices.setdefault(notice["task_id"], notice)
        for task_id in self.scan["delivered"].intersection(self.notices):
            self.delivered = True
            del self.notices[task_id]
        return self.notices

    def running(self):
        if self.path is None:
            self.path = _transcript_path(self.session_id)
        return _digest().running_shells(self.path, self.scan) if self.path else set()

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
            path = _transcript_path(session_id)
            if path is None:
                return {}
            session_dir.append(path[:-len(".jsonl")])
        return discovered_workflows(session_dir[0], known)

    def source():
        grouped = units(pending_sidecars(session_id, _acknowledged(state_path) if state_path else frozenset()))
        grouped.update(pending_workflows(session_id))
        grouped.update(discovered(grouped))
        grouped.update(rosters())
        grouped.update({"shell:%s" % task_id: {"shell": notice, "records": [], "fanout_dir": None}
                        for task_id, notice in shells.poll().items()})
        grouped.update({"running-shell:%s" % task_id: {"running_shell": task_id, "records": [], "fanout_dir": None}
                        for task_id in shells.running()})
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


def pane_frame(lines, now=None):
    """One frame: the check-in lines, or idle, closed by a stamp line counting the running jobs. The stamp closes the
    frame because a viewer shows the output's tail."""
    now = time.time() if now is None else now
    lines = list(lines)
    jobs = sum(1 for line in lines if line and not line[0].isspace())
    stamp = "%s %s: %s" % (PANE_TITLE, time.strftime("%H:%M:%S", time.localtime(now)),
                           "%d running" % jobs if lines else "idle")
    return "\n" + "\n".join((lines or ["idle: no sidecar, Workflow, roster or shell running"]) + [stamp]) + "\n"


def pane_due(key, last_key, last_at, now):
    """True when a frame prints: its lines changed, or PANE_HEARTBEAT_SECONDS passed since the last frame."""
    return key != last_key or now - last_at >= PANE_HEARTBEAT_SECONDS


def _pane_live(state):
    return _process_line_gone((state or {}).get(PANE_KEY)) is False


def pane_hint(session_id, live):
    """The wake line that asks for the pane: only while work is live and no live pane is registered."""
    if not live or not session_id:
        return None
    if _pane_live(_hook_state.read_json_salvage(_hook_state.state_path(session_id))):
        return None
    return "PANE none: the owner has no live progress view. Start `%s` %s; never read its output." % (
        PANE_COMMAND, PANE_CALL)


def session_closing(session_id):
    """The session wake's closing lines: the unrated dispatches and the pane hint, given the live count."""
    return lambda live: [t for t in [_digest().unrated_line(session_id), pane_hint(session_id, live)] if t]


def pane_main(session_id):
    """Poll the check-in lines every PANE_SECONDS, appending a frame when `pane_due`. Exits COVERED beside a live pane,
    and exits HOST GONE when no session can receive its frames: the pane is unread by design while its session
    lives, but a pane nobody can read is the same orphan the session watcher ends on."""
    path, identity, covering = _hook_state.state_path(session_id), process_line(), []

    def claim(state):
        if _pane_live(state):
            covering.append(state[PANE_KEY])
        else:
            state[PANE_KEY] = identity

    _hook_state.update_json_locked(path, claim)
    if covering:
        print("COVERED by %s: a live pane is registered" % covering[0], flush=True)
        return 0
    source, last_key, last_at = _session_source(session_id), None, 0.0
    lifetime = Lifetime(session_id)
    try:
        while True:
            if lifetime.verdict():
                print(HOST_GONE % lifetime.gone, flush=True)
                return 0
            try:
                lines = checkin_lines(source())
            except Exception as exc:
                lines = ["pane read failed: %s: %s" % (type(exc).__name__, exc)]
            now = time.time()
            if pane_due(tuple(lines), last_key, last_at, now):
                sys.stdout.write(pane_frame(lines, now))
                sys.stdout.flush()
                last_key, last_at = tuple(lines), now
            time.sleep(PANE_SECONDS)
    finally:
        _hook_state.update_json_locked(
            path, lambda state: state.pop(PANE_KEY, None) if state.get(PANE_KEY) == identity else None)


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
    if "--pane" in argv:
        if len(argv) != 1 or not os.environ.get("CLAUDE_CODE_SESSION_ID"):
            print("Usage: watch_records.py --pane (alone, with CLAUDE_CODE_SESSION_ID set)", file=sys.stderr)
            return 2
        return pane_main(os.environ["CLAUDE_CODE_SESSION_ID"])
    unknown = [a for a in argv if a.startswith("-") and a not in ("--session", "--wake")
               and a != "--await-launch" and not a.startswith("--await-launch=")]
    if unknown:
        if "--covered" in unknown:
            print("--covered is retired: arm `--session --wake` directly; beside a live watcher it waits to take over.", file=sys.stderr)
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
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    lifetime = Lifetime(session_id) if session_wide else None
    path, identity, covering = registration(pending, session_wide=session_wide)
    while covering and session_wide:
        time.sleep(POLL_SECONDS)
        if lifetime.verdict():
            print(HOST_GONE % lifetime.gone, flush=True)
            return 0
        # A read-only look each poll: the takeover wait is minutes to hours long, and a locked rewrite of the
        # session's state file every 0.5 s of it writes 7 KB that no reader asked for.
        if not session_watcher_live(_hook_state.read_json_salvage(path) or {}):
            path, identity, covering = registration(pending, session_wide=True)
    if covering:
        print("COVERED by %s: a live session watcher is registered, so nothing to do" % covering, flush=True)
        return 0
    try:
        source = _session_source(session_id, path) if session_wide else _records_source(pending)
        closing = session_closing(session_id) if session_wide else None
        return wake_loop(source, ack_path=path, await_launch=await_launch, closing=closing,
                         unregister=lambda: deregister(path, identity), lifetime=lifetime) \
            if wake or any(is_run_dir(p) for p in pending) else _stream(pending)
    finally:
        deregister(path, identity)


if __name__ == "__main__":
    sys.exit(main())
