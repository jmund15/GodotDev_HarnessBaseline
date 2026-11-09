#!/usr/bin/env python3
"""SessionStart: keep every Anthropic login in step with its route's, repairing drift in the background.

Runs `scripts/sync_plan_config.py --check`, which makes no network call (an elapsed marketplace window
is drift the spawned sync refreshes), and prints at most one line, which SessionStart adds to the session's context:
  exit 0                  nothing
  exit 1                  starts `sync_plan_config.py` detached, its output in
                          `.claude/logs/login_parity_sync_<UTC stamp>.log`, and names what updates, that
                          log and "restart sessions on this login after it ends". A sync this hook started
                          under SPAWN_WINDOW seconds ago is not started again; the line names its log.
                          A spawn that fails releases its claim and names the fault and the command.
  exit 1, deferred        a live benchmark holds the plugin changes back: names the drift and the run,
                          starts nothing
  any other exit, a timeout
                          names the failure and the command to run by hand
A sidecar child (CLAUDE_CODE_SIDECAR set) runs nothing, so no delegate starts a sync mid-run.
A cloud VM (CLAUDE_CODE_REMOTE=true) runs nothing: it has no local logins to keep in step.
Advisory: the hook never blocks a session, and exits 0. Only the newest KEEP_LOGS sync logs are kept.
"""
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_state  # noqa: E402
from _claude_scope import is_cloud  # noqa: E402
from _bounded_run import detach_options, run_bounded  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SYNC = REPO / ".claude" / "scripts" / "sync_plan_config.py"
CHECK_ARGV = [sys.executable, str(SYNC), "--check"]
SYNC_ARGV = [sys.executable, str(SYNC)]
LOGS = REPO / ".claude" / "logs"
MARKER = "login_parity_sync.started.json"
CHECK_TIMEOUT = 100
SPAWN_WINDOW = 60 * 60
KEEP_LOGS = 10
SUMMARY_CHARS = 400


def spawn(argv, log):
    """Start `argv` detached from this session, stdout and stderr appended to `log`."""
    Path(log).parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, close_fds=True,
                         **detach_options())


def _summary(lines):
    text = "; ".join(lines)
    return text if len(text) <= SUMMARY_CHARS else text[:SUMMARY_CHARS].rsplit(";", 1)[0] + "; ..."


def _claim(now, log):
    """None when this session now owns the spawn; else the log of the sync another session started under
    SPAWN_WINDOW ago. The read and the write share one file lock, so two sessions starting together
    start one sync; a lock this session cannot take counts as another session's claim."""
    def updater(doc):
        try:
            if now - float(doc["at"]) < SPAWN_WINDOW:
                return str(doc["log"])
        except (KeyError, TypeError, ValueError):
            pass
        doc.clear()
        doc.update({"at": now, "log": str(log)})
        return None

    written, running = _hook_state.update_json_locked(str(LOGS / MARKER), updater)
    if running:
        return running
    return None if written else "(another session's sync)"


def _release(log):
    """Drop this session's claim on `log` under the claim's lock; a newer session's claim stays."""
    def updater(doc):
        if doc.get("log") == str(log):
            doc.clear()

    _hook_state.update_json_locked(str(LOGS / MARKER), updater)


def _prune_logs():
    logs = sorted(LOGS.glob("login_parity_sync_*.log"), key=lambda p: p.name, reverse=True)
    for old in logs[KEEP_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass


CHECK_COMMAND = "python3 .claude/scripts/sync_plan_config.py --check"
SYNC_COMMAND = "python3 .claude/scripts/sync_plan_config.py"


def _verdict(returncode, stdout, stderr):
    """(line, drift) for one check result: drift is the lines a sync repairs, None when no sync starts;
    line is the message to inject in that case, or None."""
    if returncode == 0:
        return None, None
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    if returncode != 1:
        why = ([ln.strip() for ln in (stderr or "").splitlines() if ln.strip()] or lines or ["no output"])[-1]
        return ("[login parity] the check failed (exit %d: %s); run `%s` and fix what it names."
                % (returncode, why, CHECK_COMMAND)), None
    deferred = [ln for ln in lines if ln.startswith("plugins: deferred")]
    drift = [ln for ln in lines if not ln.startswith("plugins: deferred")]
    if deferred:
        return "[login parity] drift: %s. Nothing started: %s." % (_summary(drift), deferred[0][len("plugins: "):]), None
    return None, drift


def decide():
    """The one line to inject, or None."""
    if os.environ.get("CLAUDE_CODE_SIDECAR") or is_cloud():
        return None
    proc = run_bounded(CHECK_ARGV, CHECK_TIMEOUT)
    if proc.returncode is None:
        return "[login parity] the check ran past %d s; run `%s` by hand." % (CHECK_TIMEOUT, CHECK_COMMAND)
    line, drift = _verdict(proc.returncode, proc.stdout, proc.stderr)
    if drift is None:
        return line
    now = time.time()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = LOGS / ("login_parity_sync_%s.log" % stamp)
    LOGS.mkdir(parents=True, exist_ok=True)
    running = _claim(now, log)
    if running:
        return ("[login parity] drift: %s. A sync started under %d min ago is still the repair; log %s. "
                "restart sessions on this login after it ends." % (_summary(drift), SPAWN_WINDOW // 60, running))
    _prune_logs()
    try:
        spawn(SYNC_ARGV, log)
    except Exception as exc:  # noqa: BLE001  advisory: release the claim, name the fault
        _release(log)
        return ("[login parity] drift: %s. The sync could not start (%s: %s); run `%s` by hand."
                % (_summary(drift), type(exc).__name__, exc, SYNC_COMMAND))
    return ("[login parity] updating in the background: %s. Log %s; restart sessions on this login after it ends."
            % (_summary(drift), log))


def main():
    try:
        sys.stdin.read()
    except (OSError, ValueError):
        pass
    try:
        line = decide()
    except Exception as exc:  # noqa: BLE001  advisory: name the fault, never block the session
        line = "[login parity] the check could not run (%s: %s)." % (type(exc).__name__, exc)
    if line:
        print(line)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
