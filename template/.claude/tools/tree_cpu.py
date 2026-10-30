#!/usr/bin/env python3
"""Print the CPU seconds (user + kernel) a process and all its live descendants have used.

Usage: tree_cpu.py <pid>
  <pid> is a native pid: a Git Bash caller passes `cat /proc/<msys-pid>/winpid`, which Windows Python cannot read.

Prints one number, or nothing with exit 1 when the root is gone or the platform will not say. A
descendant that exits between two readings drops out of the sum, so a caller compares readings for
growth, never for an exact total.
"""
import os
import sys
from pathlib import Path


def _windows_tree_cpu(root):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL('kernel32', use_last_error=True)

    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_char * 260)]

    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.OpenProcess.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (None, wintypes.HANDLE(-1).value):
        return None
    children = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = k.Process32First(snap, ctypes.byref(entry))
        while more:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            more = k.Process32Next(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)

    def cpu(pid, live_only=False):
        handle = k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            # An exited process stays openable while any handle to it lives; STILL_ACTIVE (259) marks a live one.
            code = wintypes.DWORD()
            if live_only and (not k.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259):
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(handle, *map(ctypes.byref, times)):
                return None
            return sum((t.dwHighDateTime << 32 | t.dwLowDateTime) / 1e7 for t in times[2:])
        finally:
            k.CloseHandle(handle)

    own = cpu(root, live_only=True)
    if own is None:
        return None
    total, seen, stack = own, {root}, list(children.get(root, ()))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += cpu(pid) or 0.0
        stack.extend(children.get(pid, ()))
    return total


def _proc_tree_cpu(root):
    tick = os.sysconf('SC_CLK_TCK')
    stats = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                stats[int(entry.name)] = (int(fields[1]), (int(fields[11]) + int(fields[12])) / tick)
            except (OSError, ValueError, IndexError):
                continue
    if root not in stats:
        return None
    total, stack = 0.0, [root]
    while stack:
        pid = stack.pop()
        total += stats[pid][1]
        stack.extend(p for p, (parent, _) in stats.items() if parent == pid)
    return total


def tree_cpu(pid):
    return _windows_tree_cpu(pid) if os.name == 'nt' else _proc_tree_cpu(pid)


def main(argv):
    if not argv or not argv[0].isdigit():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    used = tree_cpu(int(argv[0]))
    if used is None:
        return 1
    print('%.3f' % used)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
