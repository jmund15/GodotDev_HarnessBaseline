#!/usr/bin/env python3
"""Launch admission on Windows memory headroom — one owner for the sidecar launchers
(scripts/lib/sidecar_common.sh sc_check_launch_memory) and any caller that supervises its own hold.

`check` samples once and refuses at once: a sidecar launch has no supervisor reading its `.err`, so
a launch held in `wait` sat silent (2026-09-30: nine seats held 40-85 min). `wait` blocks until
both floors clear, for a caller whose own supervisor reports the hold. Neither kills a
process. An unreadable or malformed sample logs one WARNING and lets the launch proceed: refusing
every launch on a sampling fault would be worse than the pressure it guards. A broken override is a
configuration error, never a fake healthy sample.

    check --label <label>     exit 0 = proceed; 3 = refused (one REFUSED line); 2 = invalid configuration
    wait --label <label>      exit 0 = proceed; 2 = invalid configuration

Environment: LAUNCH_MIN_COMMIT_GIB (default 12), LAUNCH_MIN_PHYSICAL_GIB (default 6),
LAUNCH_MEMORY_POLL_SEC (default 20, seconds). A floor of 0 disables that metric. The physical
default is the largest measured sidecar process tree (claude -p plus its tool and test processes:
4.85 GiB private bytes, 12 live trees sampled every 5 s) plus 1 GiB.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import sys
import time
from typing import Callable, NamedTuple

COMMIT_ENV = "LAUNCH_MIN_COMMIT_GIB"
PHYSICAL_ENV = "LAUNCH_MIN_PHYSICAL_GIB"
POLL_ENV = "LAUNCH_MEMORY_POLL_SEC"
DEFAULT_COMMIT_GIB = 12.0
DEFAULT_PHYSICAL_GIB = 6.0
DEFAULT_POLL_SEC = 20.0
_GIB = 1024 ** 3


class ConfigError(Exception):
    """A launch-memory override that cannot be used as given."""


class Floors(NamedTuple):
    commit_gib: float
    physical_gib: float
    poll_sec: float


class _PERFORMANCE_INFORMATION(ctypes.Structure):
    # Native alignment matches the Win32 struct: DWORD cb, then nine SIZE_T, then three DWORD.
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_uint32),
        ("ProcessCount", ctypes.c_uint32),
        ("ThreadCount", ctypes.c_uint32),
    ]


def read_snapshot() -> "tuple[float, float]":
    """(available commit GiB, free physical GiB) from ONE GetPerformanceInfo sample.

    Raises RuntimeError when the runtime or the sample cannot be trusted, so the caller fails open
    with a WARNING instead of reading a misaligned struct as headroom.
    """
    if sys.platform != "win32":
        raise RuntimeError("memory sampling needs Windows (platform=%s)" % sys.platform)
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("not a 64-bit Python runtime; PERFORMANCE_INFORMATION would be misread")
    info = _PERFORMANCE_INFORMATION()
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), ctypes.sizeof(info)):
        raise RuntimeError("GetPerformanceInfo reported failure")
    page = int(info.PageSize)
    if page <= 0:
        raise RuntimeError("GetPerformanceInfo reported page size %d" % page)
    commit_pages = int(info.CommitLimit) - int(info.CommitTotal)
    if commit_pages < 0:
        raise RuntimeError("GetPerformanceInfo reported commit over its limit")
    return commit_pages * page / _GIB, int(info.PhysicalAvailable) * page / _GIB


def _float_env(name: str, default: float, *, strictly_positive: bool) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError("%s=%r is not a number" % (name, raw))
    if not math.isfinite(value):
        raise ConfigError("%s=%r is not a finite number" % (name, raw))
    if strictly_positive and value <= 0:
        raise ConfigError("%s=%r must be greater than zero" % (name, raw))
    if not strictly_positive and value < 0:
        raise ConfigError("%s=%r must not be negative" % (name, raw))
    return value


def floors_from_env() -> Floors:
    """The one admission family's configuration. Raises ConfigError, naming the variable."""
    return Floors(
        _float_env(COMMIT_ENV, DEFAULT_COMMIT_GIB, strictly_positive=False),
        _float_env(PHYSICAL_ENV, DEFAULT_PHYSICAL_GIB, strictly_positive=False),
        _float_env(POLL_ENV, DEFAULT_POLL_SEC, strictly_positive=True),
    )


def _floor_text(value: float) -> str:
    return "disabled" if not value else "%.2f GiB" % value


def top_holders(count: int = 3) -> str:
    """The processes holding the most committed memory, grouped by name, or "" when unreadable."""
    import subprocess
    script = ("Get-Process | Group-Object Name | ForEach-Object { '{0}|{1}|{2}' -f $_.Name, $_.Count, "
              "(($_.Group | Measure-Object PagedMemorySize64 -Sum).Sum) }")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return ""
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3 and parts[2].isdigit():
            rows.append((int(parts[2]), parts[0], int(parts[1])))
    rows.sort(reverse=True)
    return ", ".join("%s%s %.1f GiB" % (name, " x%d" % n if n > 1 else "", size / _GIB)
                     for size, name, n in rows[:count])


REEMIT_SEC = 600.0
REFUSED_EXIT = 3


def _sample(read_snapshot: Callable[[], tuple]) -> "tuple[float, float]":
    """One finite (commit, physical) reading; raises on anything else."""
    commit, physical = read_snapshot()
    commit, physical = float(commit), float(physical)
    if not (math.isfinite(commit) and math.isfinite(physical)):
        raise ValueError("non-finite reading")
    return commit, physical


def _clears(floors: Floors, commit: float, physical: float) -> bool:
    return ((not floors.commit_gib or commit >= floors.commit_gib)
            and (not floors.physical_gib or physical >= floors.physical_gib))


def _holders_suffix(read_holders: Callable[[], str], lead: str) -> str:
    try:
        text = read_holders()
    except Exception:
        return ""
    return "%s%s" % (lead, text) if text else ""


def check_headroom(read_snapshot: Callable[[], tuple], log: Callable[[str], None], floors: Floors,
                   label: str, read_holders: Callable[[], str] = top_holders) -> int:
    """One sample: 0 when both floors clear, REFUSED_EXIT after one REFUSED line when either is short.

    An unreadable sample logs one WARNING and returns 0 (the same fail-open posture as the wait).
    A clear sample is silent.
    """
    try:
        commit, physical = _sample(read_snapshot)
    except Exception as exc:
        log("WARNING memory headroom unreadable (%s); launching %s anyway" % (exc, label))
        return 0
    if _clears(floors, commit, physical):
        return 0
    log("REFUSED memory headroom for %s: commit %.2f GiB free, physical %.2f GiB free; "
        "floors commit %s, physical %s%s"
        % (label, commit, physical, _floor_text(floors.commit_gib), _floor_text(floors.physical_gib),
           _holders_suffix(read_holders, "; top holders ")))
    return REFUSED_EXIT


def wait_for_headroom(read_snapshot: Callable[[], tuple], sleep: Callable[[float], None],
                      log: Callable[[str], None], floors: Floors, label: str,
                      read_holders: Callable[[], str] = top_holders) -> None:
    """Block until both floors clear. Never raises, never kills, never sleeps on a bad sample.

    One WAIT line on the first low reading (both floors, both readings and the top memory holders),
    a STILL WAITING line every REEMIT_SEC of waiting so a held launch never looks hung, then one
    READY line. A healthy first sample returns silently: this runs per grid cell, and a line per
    healthy cell is noise. The holder lookup runs only when a line is emitted and fails open.
    """
    def holders() -> str:
        return _holders_suffix(read_holders, "; top holders: ")

    waited = False
    waited_sec = 0.0
    next_emit = REEMIT_SEC
    while True:
        try:
            commit, physical = _sample(read_snapshot)
        except Exception as exc:  # fail OPEN: one warning, no sleep, no raise
            log("WARNING memory headroom unreadable (%s); launching %s anyway" % (exc, label))
            return
        if _clears(floors, commit, physical):
            if waited:
                log("READY memory headroom for %s: commit %.2f GiB free, physical %.2f GiB free; "
                    "floors commit %s, physical %s"
                    % (label, commit, physical, _floor_text(floors.commit_gib),
                       _floor_text(floors.physical_gib)))
            return
        if not waited:
            log("WAIT memory headroom for %s: commit %.2f GiB free, physical %.2f GiB free; "
                "floors commit %s, physical %s; sampling every %gs%s"
                % (label, commit, physical, _floor_text(floors.commit_gib),
                   _floor_text(floors.physical_gib), floors.poll_sec, holders()))
            waited = True
        elif waited_sec >= next_emit:
            log("STILL WAITING %.0f min for memory headroom for %s: commit %.2f GiB free, physical "
                "%.2f GiB free%s" % (waited_sec / 60, label, commit, physical, holders()))
            next_emit += REEMIT_SEC
        sleep(floors.poll_sec)
        waited_sec += floors.poll_sec


def _log(line: str) -> None:
    print(line, file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="launch_memory.py", description="launch admission on Windows memory headroom")
    sub = parser.add_subparsers(dest="command", required=True)
    wait = sub.add_parser("wait", help="block until Windows memory headroom clears the floors")
    wait.add_argument("--label", default="sidecar", help="what is waiting, for the log lines")
    check = sub.add_parser("check", help="sample once; exit 3 with a REFUSED line when a floor is short")
    check.add_argument("--label", default="sidecar", help="what is launching, for the REFUSED line")
    args = parser.parse_args(argv)
    try:
        floors = floors_from_env()
    except ConfigError as exc:
        print("launch_memory: invalid configuration: %s" % exc, file=sys.stderr)
        return 2
    if args.command == "check":
        return check_headroom(read_snapshot, _log, floors, args.label)
    wait_for_headroom(read_snapshot, time.sleep, _log, floors, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
