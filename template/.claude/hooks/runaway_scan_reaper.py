#!/usr/bin/env python3
"""Hook: UserPromptSubmit and PostToolUse — kill orphaned runaway search processes.

Coverage: `check(payload)` runs in-process inside pre_bash_dispatch (Bash, PowerShell, Monitor),
post_read_dispatch (read family, Write, WebFetch, WebSearch) and post_edit_dispatch (Write, Edit).
This file's own PostToolUse registration uses the anchored matcher
`^(?:Agent|Workflow|TaskStop|TaskOutput|Skill|ToolSearch|LSP|NotebookEdit|mcp__.*)$` for the
long-running tools no dispatcher covers. Short coordination tools (AskUserQuestion, SendMessage,
the Task list, worktree, cron and notification tools) stay uncovered; UserPromptSubmit is their
backstop.

`unbounded_scan_guard.py` denies recursive grep before it runs, but only for commands that pass
through this project's hooks in the shapes it parses. This reaper contains what slips through.
Measured 2026-09-14: orphaned Git greps reached 14.3 GB and 21.5 GB private memory and starved
the machine.

Selection, all required:
- image is `grep.exe`/`egrep.exe`/`fgrep.exe`/`find.exe` under an MSYS `\\usr\\bin\\` (Git's), or
  `rg.exe`; `C:\\Windows\\System32\\find.exe` never qualifies;
- the parent pid is gone: absent from the process table, or reused by a process created after
  the child. A parent that exists but cannot be opened counts as alive;
- private memory >= MIN_PRIVATE_MB or CPU time >= MIN_CPU_SEC.
Before the kill the pid is opened again and its image, command line, creation time and dead
parent are re-verified on that handle; TerminateProcess acts on the same handle, so a reused pid
is never hit. One pid, never a tree. One advisory line per reaped pid; silent otherwise.

Throttle: at most one process snapshot per THROTTLE_SEC machine-wide. The fast path is one
`os.stat` of `.claude/.cache/runaway-scan-reaper.stamp`; a winner claims the window through a
`_file_lock` lock (`acquire(lock, 0)`) before touching the stamp. A held lock skips this pass
silently; the lock is never reclaimed by age, only when its holder's handle is gone. Autonomous drives send no prompts for hours, hence
tool-call coverage as well as UserPromptSubmit. A tool two entries cover (a Write, or an mcp__
tool both post_read_dispatch and the narrow registration match) runs the reaper twice, which the
throttle absorbs.

This is a separate module rather than a mode of `shell_census.py`: the census snapshot is a
PowerShell CIM query (about 1 s) cached for rendering shell ownership, and it carries no image
path, private memory or CPU time. The reaper reads those through Win32 calls in milliseconds.

Test-only environment overrides: HARNESS_REAPER_CACHE_DIR, HARNESS_REAPER_THROTTLE_SEC,
HARNESS_REAPER_MIN_PRIVATE_MB, HARNESS_REAPER_MIN_CPU_SEC, and HARNESS_REAPER_ONLY_PIDS (comma-separated pids;
nothing outside the list is considered).

Fail posture: open and silent. Any error exits 0 with no output; a tool call is never blocked.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _file_lock  # noqa: E402

MIN_PRIVATE_MB = 1024
MIN_CPU_SEC = 600
THROTTLE_SEC = 120
STAMP_NAME = "runaway-scan-reaper.stamp"
LOCK_NAME = "runaway-scan-reaper.lock"

MSYS_SEARCH_IMAGES = {"grep.exe", "egrep.exe", "fgrep.exe", "find.exe"}
ANY_PATH_SEARCH_IMAGES = {"rg.exe"}
_CANDIDATE_NAMES = MSYS_SEARCH_IMAGES | ANY_PATH_SEARCH_IMAGES


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def throttle_sec():
    return _env_float("HARNESS_REAPER_THROTTLE_SEC", THROTTLE_SEC)


def cache_dir():
    override = os.environ.get("HARNESS_REAPER_CACHE_DIR")
    if override:
        return override
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, ".claude", ".cache")


def throttled(directory, window, now):
    try:
        return now - os.stat(os.path.join(directory, STAMP_NAME)).st_mtime < window
    except OSError:
        return False


def claim(directory, window, now):
    """True when this process wins the snapshot window across every session."""
    os.makedirs(directory, exist_ok=True)
    handle = _file_lock.acquire(os.path.join(directory, LOCK_NAME), 0)
    if handle is None:
        return False
    try:
        if throttled(directory, window, now):
            return False
        stamp = os.path.join(directory, STAMP_NAME)
        with open(stamp, "a", encoding="utf-8"):
            pass
        os.utime(stamp, (now, now))
        return True
    finally:
        _file_lock.release(handle)


# ---------------------------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------------------------

_WIN = None


def _win():
    global _WIN
    if _WIN is not None:
        return _WIN
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
                                            wintypes.DWORD]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    _WIN = {"ct": ctypes, "wt": wintypes, "k32": k32, "ntdll": ntdll, "PE": PROCESSENTRY32W,
            "PMC": PROCESS_MEMORY_COUNTERS_EX, "US": UNICODE_STRING}
    return _WIN


QUERY_LIMITED = 0x1000
PROCESS_TERMINATE = 0x0001


def snapshot():
    """{pid: {"ppid", "name"}} for every process. Raises OSError when the table is unreadable."""
    w = _win()
    ct, k32 = w["ct"], w["k32"]
    handle = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not handle or handle == ct.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    table = {}
    try:
        entry = w["PE"]()
        entry.dwSize = ct.sizeof(entry)
        ok = k32.Process32FirstW(handle, ct.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = {"ppid": int(entry.th32ParentProcessID),
                                               "name": entry.szExeFile}
            ok = k32.Process32NextW(handle, ct.byref(entry))
    finally:
        k32.CloseHandle(handle)
    if not table:
        raise OSError("empty process table")
    return table


def _read(handle):
    """{"path", "cmd", "created", "cpu_sec", "private_mb"} from an open handle, or None."""
    w = _win()
    ct, wt, k32 = w["ct"], w["wt"], w["k32"]
    size = wt.DWORD(1024)
    buf = ct.create_unicode_buffer(1024)
    if not k32.QueryFullProcessImageNameW(handle, 0, buf, ct.byref(size)):
        return None
    created, exited, kernel, user = (ct.c_ulonglong() for _ in range(4))
    if not k32.GetProcessTimes(handle, ct.byref(created), ct.byref(exited), ct.byref(kernel), ct.byref(user)):
        return None
    pmc = w["PMC"]()
    pmc.cb = ct.sizeof(pmc)
    if not k32.K32GetProcessMemoryInfo(handle, ct.byref(pmc), pmc.cb):
        return None
    return {"path": buf.value, "cmd": _cmdline(handle), "created": created.value,
            "cpu_sec": (kernel.value + user.value) / 1e7, "private_mb": pmc.PrivateUsage / (1024 * 1024)}


def _cmdline(handle):
    w = _win()
    ct, wt = w["ct"], w["wt"]
    need = wt.ULONG(0)
    w["ntdll"].NtQueryInformationProcess(handle, 60, None, 0, ct.byref(need))
    if not need.value:
        return ""
    raw = ct.create_string_buffer(need.value)
    if w["ntdll"].NtQueryInformationProcess(handle, 60, raw, need.value, ct.byref(need)) != 0:
        return ""
    us = w["US"].from_buffer(raw)
    return ct.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""


def _open(pid, access):
    return _win()["k32"].OpenProcess(access, False, pid)


def _close(handle):
    if handle:
        _win()["k32"].CloseHandle(handle)


def is_search_image(path):
    lowered = (path or "").lower()
    base = lowered.replace("/", "\\").rsplit("\\", 1)[-1]
    if base in ANY_PATH_SEARCH_IMAGES:
        return True
    system_root = (os.environ.get("SystemRoot") or "C:\\Windows").lower().rstrip("\\") + "\\"
    return base in MSYS_SEARCH_IMAGES and "\\usr\\bin\\" in lowered and not lowered.startswith(system_root)


def parent_dead(table, ppid, child_created):
    if ppid not in table:
        return True
    handle = _open(ppid, QUERY_LIMITED)
    if not handle:
        return False
    try:
        info = _read(handle)
    finally:
        _close(handle)
    return info is not None and info["created"] > child_created


def _only_pids():
    raw = os.environ.get("HARNESS_REAPER_ONLY_PIDS")
    if raw is None:
        return None
    return {int(p) for p in re.findall(r"-?\d+", raw)}


def _tail(cmd, n=120):
    cmd = re.sub(r"\s+", " ", cmd or "").strip()
    return cmd if len(cmd) <= n else "…" + cmd[-n:]


def reap():
    """Kill every selected orphan; return one advisory line per reaped pid."""
    min_mb = _env_float("HARNESS_REAPER_MIN_PRIVATE_MB", MIN_PRIVATE_MB)
    min_cpu = _env_float("HARNESS_REAPER_MIN_CPU_SEC", MIN_CPU_SEC)
    only = _only_pids()
    table = snapshot()
    lines = []
    for pid, row in table.items():
        if row["name"].lower() not in _CANDIDATE_NAMES or (only is not None and pid not in only):
            continue
        handle = _open(pid, QUERY_LIMITED)
        if not handle:
            continue
        try:
            first = _read(handle)
        finally:
            _close(handle)
        if not first or not is_search_image(first["path"]):
            continue
        if not parent_dead(table, row["ppid"], first["created"]):
            continue
        if first["private_mb"] < min_mb and first["cpu_sec"] < min_cpu:
            continue
        line = _kill_verified(pid, row["ppid"], first)
        if line:
            lines.append(line)
    return lines


def _kill_verified(pid, ppid, first):
    handle = _open(pid, QUERY_LIMITED | PROCESS_TERMINATE)
    if not handle:
        return None
    try:
        again = _read(handle)
        if not again or any(again[k] != first[k] for k in ("path", "cmd", "created")):
            return None
        fresh = snapshot()
        row = fresh.get(pid)
        if not row or row["ppid"] != ppid or not parent_dead(fresh, ppid, again["created"]):
            return None
        if not _win()["k32"].TerminateProcess(handle, 1):
            return None
        return ("[runaway-scan-reaper] killed orphaned search pid %d (parent %d gone) | private %d MB | "
                "CPU %d s | %s" % (pid, ppid, again["private_mb"], again["cpu_sec"], _tail(again["cmd"])))
    finally:
        _close(handle)


def check(payload=None):
    """Advisory lines for this call's reaps; [] when throttled, off Windows, or on any error.

    The in-process entry for pre_bash_dispatch, post_read_dispatch and post_edit_dispatch, which
    append the lines to their own additionalContext. Throttled like main() and never raises.
    """
    try:
        if os.name != "nt":
            return []
        now = time.time()
        directory, window = cache_dir(), throttle_sec()
        if throttled(directory, window, now) or not claim(directory, window, now):
            return []
        return reap()
    except Exception:
        return []


def main():
    try:
        if os.name != "nt":
            return 0
        if throttled(cache_dir(), throttle_sec(), time.time()):
            return 0
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        lines = check(payload)
        if lines:
            event = (payload or {}).get("hook_event_name") or "PostToolUse"
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": "\n".join(lines)}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
