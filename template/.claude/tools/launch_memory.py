#!/usr/bin/env python3
"""Launch admission on Windows memory headroom — one owner for the sidecar launchers
(scripts/lib/sidecar_common.sh sc_check_launch_memory) and any caller that supervises its own hold.

`check` samples once and refuses at once: a sidecar launch has no supervisor reading its `.err`, so
a launch held in `wait` sat silent (2026-09-30: nine seats held 40-85 min). `wait` blocks until
both floors clear, for a caller whose own supervisor reports the hold. Neither kills a
process. An unreadable or malformed sample logs one WARNING and lets the launch proceed: refusing
every launch on a sampling fault would be worse than the pressure it guards. A broken override is a
configuration error, never a fake healthy sample.

    check --label <label> [--brief F --shape S]   exit 0 = proceed; 3 = refused (one REFUSED line); 2 = invalid configuration
    wait --label <label> [--brief F --shape S]    exit 0 = proceed; 2 = invalid configuration

The floors follow the job, never a field the dispatcher fills in. `--brief <file> --shape <shape>`
(read from the launcher's own `-f`/`-G`) lets `classify_job` read the brief's declared operations:

    light   shape author/review/survey and the brief names no Playwright, vitest, npm/npx
            or tsc command: commit 6.5 GiB, physical 3.25 GiB free
    heavy   everything else, including a missing, unreadable or empty brief and any other shape:
            commit 12 GiB, physical 6 GiB free (the floors before classes existed)

Basis (2026-10-28; a floor is its class's peak plus a 1 GiB margin, the margin the old floor used):
- heavy peak 4.85 GiB: the largest of 12 live sidecar trees (claude -p plus its tool and test processes,
  private bytes, sampled every 5 s). An e2e job fits it: its test tree measured 0.65 GiB for the
  backend plus 1.94 GiB for one Playwright worker (working set), 2.6 GiB, on top of
  claude -p.
- light peak 2.25 GiB is DERIVED, not sampled: 4.85 minus that 2.6 GiB test tree. No direct sample of a
  no-test job was taken (the sampling call was denied in the run that built this). A sample of a
  no-test author or review job above 2.25 GiB private bytes reverses the light floor.
- commit floor is twice the physical floor in both classes. No commit charge was measured per class:
  the old 12 / 6 ratio is kept, so a measured commit peak per class replaces it.
A brief that points at a file which runs those commands reads as light: the brief text is all the
launcher sees. Classes are two because a measured e2e tree lands on the heavy floor.

Environment: LAUNCH_MIN_COMMIT_GIB, LAUNCH_MIN_PHYSICAL_GIB (each replaces that floor for every
class), LAUNCH_MEMORY_POLL_SEC (default 20, seconds). A floor of 0 disables that metric.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import os
import re
import sys
import time
from typing import Callable, NamedTuple

COMMIT_ENV = "LAUNCH_MIN_COMMIT_GIB"
PHYSICAL_ENV = "LAUNCH_MIN_PHYSICAL_GIB"
POLL_ENV = "LAUNCH_MEMORY_POLL_SEC"
DEFAULT_POLL_SEC = 20.0
_GIB = 1024 ** 3
HEAVY = "heavy"
LIGHT = "light"
CLASS_FLOORS = {HEAVY: (12.0, 6.0), LIGHT: (6.5, 3.25)}
LIGHT_SHAPES = frozenset({"author", "review", "survey"})
HEAVY_OPERATION = re.compile(
    r"\bnpm\s+(?:run|test|ci|exec|install|i)\b|\bnpx\b|\bplaywright\b"
    r"|\bvitest\b|\btsc\b", re.IGNORECASE)


class ConfigError(Exception):
    """A launch-memory override that cannot be used as given."""


class Floors(NamedTuple):
    commit_gib: float
    physical_gib: float
    poll_sec: float
    job_class: str = HEAVY


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


def floors_for(job_class: str) -> Floors:
    """The class's floors; an unrecognized class is heavy."""
    job_class = job_class if job_class in CLASS_FLOORS else HEAVY
    commit, physical = CLASS_FLOORS[job_class]
    return Floors(commit, physical, DEFAULT_POLL_SEC, job_class)


def classify_job(brief, shape) -> str:
    """LIGHT only for a brief that declares no heavy operation under a shape that runs none."""
    if shape not in LIGHT_SHAPES or not isinstance(brief, str) or not brief.strip():
        return HEAVY
    return HEAVY if HEAVY_OPERATION.search(brief) else LIGHT


def launch_class_args(argv) -> "list[str]":
    """`--brief`/`--shape` for this tool from a launcher argv's `-f` and `-G`; [] when it has no brief."""
    flags = {tok: argv[i + 1] for i, tok in enumerate(argv[1:-1], 1) if tok in ("-f", "-G")}
    if "-f" not in flags:
        return []
    return ["--brief", flags["-f"]] + (["--shape", flags["-G"]] if "-G" in flags else [])


def job_class_of(brief_path, shape) -> str:
    """classify_job over a brief file; an absent or unreadable one is heavy."""
    if not brief_path:
        return HEAVY
    try:
        with open(brief_path, encoding="utf-8", errors="replace") as fh:
            return classify_job(fh.read(), shape)
    except OSError:
        return HEAVY


def floors_from_env(job_class: str = HEAVY) -> Floors:
    """The one admission family's configuration. Raises ConfigError, naming the variable."""
    base = floors_for(job_class)
    return Floors(
        _float_env(COMMIT_ENV, base.commit_gib, strictly_positive=False),
        _float_env(PHYSICAL_ENV, base.physical_gib, strictly_positive=False),
        _float_env(POLL_ENV, base.poll_sec, strictly_positive=True),
        base.job_class,
    )


def _floor_text(value: float) -> str:
    return "disabled" if not value else "%.2f GiB" % value


def _floors_text(floors: Floors) -> str:
    text = "commit %s, physical %s" % (_floor_text(floors.commit_gib), _floor_text(floors.physical_gib))
    return text if floors.job_class == HEAVY else "%s (%s job)" % (text, floors.job_class)


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
        "floors %s%s"
        % (label, commit, physical, _floors_text(floors), _holders_suffix(read_holders, "; top holders ")))
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
                    "floors %s" % (label, commit, physical, _floors_text(floors)))
            return
        if not waited:
            log("WAIT memory headroom for %s: commit %.2f GiB free, physical %.2f GiB free; "
                "floors %s; sampling every %gs%s"
                % (label, commit, physical, _floors_text(floors), floors.poll_sec, holders()))
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
    for command in (wait, check):
        command.add_argument("--brief", default="", help="the job's brief file; its declared operations pick the floors")
        command.add_argument("--shape", default="", help="the job's guard shape (author, review, survey ...)")
    args = parser.parse_args(argv)
    try:
        floors = floors_from_env(job_class_of(args.brief, args.shape))
    except ConfigError as exc:
        print("launch_memory: invalid configuration: %s" % exc, file=sys.stderr)
        return 2
    if args.command == "check":
        return check_headroom(read_snapshot, _log, floors, args.label)
    wait_for_headroom(read_snapshot, time.sleep, _log, floors, args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
