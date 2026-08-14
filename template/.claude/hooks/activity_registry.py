#!/usr/bin/env python3
"""UserPromptSubmit hook — machine-wide Godot/gate activity registry (advisory).

Bridges the machine-global activity registry at <tempdir>/pp-activity/*.json
(written by regression_gate.ps1, run_test_suite.ps1, and the gate queue
watcher) to the model-visible channel, so a session can name what is blocking
it instead of guessing. Companion doc: .claude/plans/godot-process-identity-
and-gate-queue.md Part 3.

Registry record schema (one file per activity, <kind>-<pid>.json):
    { pid, procStart, kind: gate|suite|watcher, checkout, sessionId, label,
      startedAt, heartbeatAt, expectedSec, ownerRootPid, ownerRootStart }

OWNERSHIP — which records are "mine" vs a concurrent session's. Three axes,
in order, because the first two are frequently unavailable:

  1. sessionId — inert in practice. CLAUDE_SESSION_ID is not exported into the
     shell the harness launches from, so every writer stamps null. Kept for the
     case where it is set; never relied on.
  2. ownerRootPid — the load-bearing axis. The Claude Code process that owns the
     writer's subtree, stamped BY THE WRITER at record-creation time. It must be
     captured then, not derived by a reader later: a detached gate outlives its
     launcher (Claude Code kills a backgrounded wrapper at ~10 minutes), and the
     orphaned process's parent chain then reaches no session at all.
  3. checkout — cannot separate two sessions sharing one working tree, which is
     the common case here. It only distinguishes separate worktrees.

Reporting an own run as a peer's is not cosmetic: the advisory text tells the
reader to treat INCOMPLETE/CONTENTION as a contention artifact, so a session
mislabelled this way is being told to discard its own real verdict, and may wait
on itself. Observed 2026-08-13 — a session's own gate was announced to it as a
peer's, twice, with matching counts.

NOT applied to the reaper in run_test_suite.ps1, deliberately. That path decides
what to KILL, and its conservative rule — spare anything holding a live record —
is correct precisely because it does not need to know who owns it. Teaching it
ownership would let it kill its own session's other runs; the asymmetry is the
point (see gotcha_test_reaper_kills_peer_session_same_checkout.md).

Liveness is PID-verified with a reuse guard, never heartbeat-only: a record
is live only when its PID exists AND (when psutil is available) the
process's creation time matches procStart within 5s. A dead or
PID-reuse-impostor record is garbage — deleted best-effort, never reported.

Modeled on budget_posture.py's conventions: UTF-8 stdout reconfigure,
fail-open main() wrapper, per-session dedupe file in tempfile.gettempdir(),
prune-stale sweep, turn-counted re-emit.
"""

import glob
import json
import os
import platform
import sys
import tempfile
import time

# Windows consoles default stdout to cp1252; injected text carries em-dashes.
sys.stdout.reconfigure(encoding="utf-8")

REGISTRY_DIR = os.path.join(tempfile.gettempdir(), "pp-activity")
PROC_START_TOLERANCE_SEC = 5
HEARTBEAT_STALE_SEC = 10 * 60
TURNS_BETWEEN_EMITS = 10
SUFFIX = (
    "- contention, not a defect: do not kill it, do not report it as an "
    "editor problem or a regression; gate INCOMPLETE/CONTENTION verdicts "
    "while this is live are contention artifacts."
)


def _pid_exists(pid: int) -> bool:
    """PID-existence-only liveness — the degraded path when psutil is absent.

    Windows: OpenProcess with a query-only access right; a valid handle means
    the PID is live. POSIX: os.kill(pid, 0) raises if the PID is gone.
    Degraded because it cannot check process-creation-time, so a PID reused
    by an unrelated process within the same session reads as "live" until a
    psutil-equipped reader next sweeps it — accepted, matches the plan's
    "garbage is swept by the next reader" invariant rather than blocking here.
    """
    if platform.system() == "Windows":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_live(pid, proc_start) -> bool:
    """PID exists AND (psutil available) creation time matches within tolerance."""
    if not isinstance(pid, int):
        return False
    try:
        import psutil
        try:
            proc = psutil.Process(pid)
            if not isinstance(proc_start, (int, float)):
                return True  # PID alive; no procStart to cross-check against
            return abs(proc.create_time() - proc_start) <= PROC_START_TOLERANCE_SEC
        except psutil.NoSuchProcess:
            return False
        except Exception:
            return False
    except ImportError:
        return _pid_exists(pid)


SESSION_PROC_NAMES = ("node.exe", "node", "claude.exe", "claude")
OWNER_WALK_LIMIT = 12


def _proc_table_windows():
    """{pid: (ppid, exe_name_lower)} from one CreateToolhelp32Snapshot.

    Stdlib-only on purpose: **psutil is not installed on this machine** (verified
    2026-08-13), and the ownership axis needs a parent walk. Depending on psutil
    here would make the walk silently unreachable — and because this hook fails
    open, the symptom of that is indistinguishable from the bug the walk exists
    to fix. The snapshot carries no creation time; that degradation is handled by
    is_own_record, which falls back to a PID-only match.
    """
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return {}
    table = {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                entry.szExeFile.decode("mbcs", "replace").lower(),
            )
            ok = kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return table


def session_root(pid: int):
    """(pid, create_time_or_None) of the Claude Code process owning `pid`'s subtree.

    The nearest `node`/`claude` ancestor. A harness script and this hook are both
    spawned by that same process, so it is the identity that separates "my
    session's run" from a concurrent session's — including when the two share one
    checkout, where the checkout axis cannot distinguish them at all.

    Returns None when the chain cannot be walked; callers treat that as "cannot
    prove ownership" and report the record as a peer, which is the safe direction
    for advisory output.
    """
    try:
        import psutil  # preferred when present: carries creation time
        proc = psutil.Process(pid)
        for _ in range(OWNER_WALK_LIMIT):
            proc = proc.parent()
            if proc is None:
                return None
            if (proc.name() or "").lower() in SESSION_PROC_NAMES:
                try:
                    return (proc.pid, proc.create_time())
                except Exception:
                    return (proc.pid, None)
        return None
    except ImportError:
        pass
    except Exception:
        return None

    if platform.system() != "Windows":
        return None
    try:
        table = _proc_table_windows()
        cur = pid
        for _ in range(OWNER_WALK_LIMIT):
            row = table.get(cur)
            if not row:
                return None
            ppid, _name = row
            prow = table.get(ppid)
            if not prow or ppid <= 0:
                return None
            if prow[1] in SESSION_PROC_NAMES:
                return (ppid, None)
            cur = ppid
    except Exception:
        return None
    return None


def is_own_record(rec, my_root) -> bool:
    """True when `rec` was written by a process under this session's Claude Code root.

    Reads `ownerRootPid` off the record rather than re-walking the record process's
    ancestry now, because ancestry is not durable: a detached gate outlives its
    launcher (Claude Code kills a backgrounded wrapper at its ~10-minute ceiling),
    and the orphaned process's chain no longer reaches any session. The writer
    stamps this while the chain is still intact.
    """
    if not my_root:
        return False
    rec_root = rec.get("ownerRootPid")
    if not isinstance(rec_root, int) or rec_root != my_root[0]:
        return False
    rec_root_start = parse_ts(rec.get("ownerRootStart"))
    if rec_root_start is None or my_root[1] is None:
        return True  # PID matched; no start time on one side to cross-check
    return abs(rec_root_start - my_root[1]) <= PROC_START_TOLERANCE_SEC


def normalize_checkout(path: str) -> str:
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(path.rstrip("\\/")))


def load_registry():
    """Read every record in the registry dir; delete and skip garbage."""
    live_records = []
    try:
        paths = glob.glob(os.path.join(REGISTRY_DIR, "*.json"))
    except Exception:
        return live_records
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        pid = rec.get("pid")
        proc_start = rec.get("procStart")
        if not is_live(pid, proc_start):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        rec["_path"] = path
        live_records.append(rec)
    return live_records


def parse_ts(value):
    """Registry timestamps are ISO-8601 UTC strings (the PowerShell writers use
    `Get-Date -AsUTC -Format 'yyyy-MM-ddTHH:mm:ssZ'` or .NET round-trip 'o');
    epoch numbers are accepted too. Returns epoch seconds or None."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            import datetime
            return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def format_fragment(rec) -> str:
    kind = rec.get("kind", "activity")
    label = rec.get("label", "(no label)")
    checkout = rec.get("checkout", "")
    checkout_base = os.path.basename(checkout.rstrip("\\/")) or checkout
    started_ts = parse_ts(rec.get("startedAt"))
    since = "?"
    if started_ts is not None:
        try:
            since = time.strftime("%H:%M", time.localtime(started_ts))
        except Exception:
            since = "?"
    expected_sec = rec.get("expectedSec")
    typical = ""
    if isinstance(expected_sec, (int, float)) and expected_sec > 0:
        typical = f" (~{max(1, round(expected_sec / 60))}min typical)"
    fragment = f"peer {kind} live: {label} on {checkout_base} since {since}{typical}"
    heartbeat_ts = parse_ts(rec.get("heartbeatAt"))
    if heartbeat_ts is not None and (time.time() - heartbeat_ts) > HEARTBEAT_STALE_SEC:
        fragment += " (heartbeat stale - possibly hung)"
    return fragment


def dedupe_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(tempfile.gettempdir(), f"cc-activity-{safe or 'unknown'}.json")


def prune_stale(keep_days=7):
    """One dedupe file per session accumulates otherwise; swept on each
    session's first write, mirroring budget_posture.py's cc-budgetposture sweep."""
    cutoff = time.time() - keep_days * 86400
    try:
        tmp = tempfile.gettempdir()
        for name in os.listdir(tmp):
            if not (name.startswith("cc-activity-") and name.endswith(".json")):
                continue
            full = os.path.join(tmp, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    session_id = payload.get("session_id", "unknown")
    cwd = payload.get("cwd") or os.getcwd()
    my_checkout = normalize_checkout(cwd)
    my_pid = os.getpid()
    my_root = session_root(my_pid)

    records = load_registry()

    peers = []
    for rec in records:
        rec_session = rec.get("sessionId")
        rec_checkout = normalize_checkout(rec.get("checkout", ""))
        if rec_checkout == my_checkout:
            if rec_session and rec_session == session_id:
                continue  # own record, by session id
            if is_own_record(rec, my_root):
                continue  # own record, by Claude Code session root
            peers.append(rec)
        else:
            # Different checkout is always a peer — worktrees are separate
            # directories, so this is the only axis that sees cross-checkout activity.
            peers.append(rec)

    if not peers:
        return

    # Dedupe state — per session, same lifecycle as budget_posture.py's.
    dpath = dedupe_path(session_id)
    if not os.path.exists(dpath):
        prune_stale()
    try:
        with open(dpath, encoding="utf-8") as fh:
            dstate = json.load(fh)
    except Exception:
        dstate = {}
    turns = dstate.get("turns_since_emit", TURNS_BETWEEN_EMITS) + 1

    fingerprint = sorted(f"{r.get('kind')}:{r.get('pid')}:{r.get('checkout')}" for r in peers)
    should_emit = turns >= TURNS_BETWEEN_EMITS or dstate.get("fingerprint") != fingerprint

    if should_emit:
        fragments = [format_fragment(r) for r in peers]
        line = "[activity] " + "; ".join(fragments) + " " + SUFFIX
        print(line)
        dstate = {"fingerprint": fingerprint, "turns_since_emit": 0}
    else:
        dstate["turns_since_emit"] = turns

    try:
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump(dstate, fh)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail open: advisory telemetry never blocks a prompt
    sys.exit(0)
