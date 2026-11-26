#!/usr/bin/env python3
"""Reader of the machine-wide gate/suite activity registry at <tempdir>/harness-activity/*.json.

The registry is written by regression_gate.ps1 and run_test_suite.ps1; the gate names a live peer at the call it
affects (WAIT, BUSY, PEERS and CONTENTION lines, via scripts/PeerActivity.ps1). This module is the Python reader
for the consumers that decide on a live run: `tree_write_blockers` (a working-tree repair defers while a run digests
the tree) and `proc_table_windows` (shell_census's process snapshot). It is imported, never run as a hook.

Record schema (one file per activity, <kind>-<pid>.json):
    { pid, procStart, kind: gate|suite|waiter, checkout, sessionId, label,
      startedAt, heartbeatAt, expectedSec, ownerRootPid, ownerRootStart }

Liveness is PID-verified with a reuse guard, never heartbeat-only: a record is live only when its PID exists AND
(when psutil is available) the process's creation time matches procStart within 5s. A dead or PID-reuse-impostor
record is garbage, deleted best-effort and never reported.
"""

import glob
import json
import os
import platform
import tempfile

REGISTRY_DIR = os.path.join(tempfile.gettempdir(), "harness-activity")
PROC_START_TOLERANCE_SEC = 5


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


def proc_table_windows():
    """{pid: (ppid, exe_name_lower)} from one CreateToolhelp32Snapshot; {} where the snapshot fails.

    Stdlib-only on purpose: it must work where psutil is absent. The snapshot
    carries no creation time. Its one caller is shell_census.aged_candidates.
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


def tree_write_blockers(checkout: str):
    """Live runs on `checkout` that a working-tree write would invalidate.

    A gate and a suite run both digest the tree at run start and compare mid-run; any write
    that lands between those points is read as "a concurrent session edited the tree" and
    voids the whole run. That check cannot tell a peer's edit from the harness repairing the
    tree underneath it, so the write side has to yield. Every hook that writes to the working
    tree outside a user-initiated edit MUST consult this first and defer while it returns
    non-empty (measured 2026-08-20: a SessionStart .tres repair landed mid-Integration-suite
    and cost a 16-minute queue wait plus a full gate, verdict INVALID, zero test signal).

    `waiter` records are not blockers — a waiting gate holds nothing and has digested nothing. Returns the blocking records, newest first, or [] when a write is safe.
    """
    target = normalize_checkout(checkout)
    if not target:
        return []
    blockers = []
    for rec in load_registry():
        if rec.get("kind") not in ("gate", "suite"):
            continue
        if normalize_checkout(rec.get("checkout", "")) != target:
            continue
        blockers.append(rec)
    blockers.sort(key=lambda r: parse_ts(r.get("startedAt")) or 0, reverse=True)
    return blockers
