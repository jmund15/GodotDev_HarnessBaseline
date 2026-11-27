#!/usr/bin/env python3
"""run_wait.py <log> [--max S] - wait on a test, build or gate run through its log, then print its verdict block.

<log> is the file the run writes: its redirect target, a gate's -Out file, or a never-woken caller's Bash call output
file, which the wait guard ends with an `EXIT <code>` line whether the client moved the call to the background or the
caller started it there. A job that writes no such file (a launcher's record) waits on its own done-test instead
(guards/common.md). The wait blocks until one of:
  0  the run is done: its runner's terminal line is in the log; the verdict block follows
  3  --max seconds passed and the run is still going; call again
  2  the run ended without a terminal line (no process writes the log and none of the lines below is in it: a stopped
     run, or a log no runner here writes), or the log never appeared
--max defaults to the caller's wait clamp less MARGIN_S, so the call returns inside its own Bash timeout.

RUNNERS holds one row per runner, outermost first: its name, the word its terminal line starts with, and a line only it
prints before it starts an inner runner. A log's runner is the first row present (its own line or its terminal line),
so the terminal line an inner runner prints inside an outer runner's log never ends that run. The clamped call's EXIT
row is this file's; every other row lives in the layer that ships its runner, `tools/run_wait.<layer>.json`
(`{"runners": [{"name", "word", "own"?, "tail"?}]}`, outermost first; files load in name order). A new runner is one row.
"""
import argparse
from collections import namedtuple
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))

Runner = namedtuple("Runner", "name word own tail")
# foreground_wait_guard.EXIT_LINE, appended last to a command the client may move to the background
EXIT_ROW = Runner("clamped-call", "EXIT ", None, r"-?\d+\s*$")
ROW_GLOB = "run_wait.*.json"


def load_runners(folder=os.path.dirname(os.path.abspath(__file__))):
    rows = [EXIT_ROW]
    for path in sorted(glob.glob(os.path.join(folder, ROW_GLOB))):
        with open(path, encoding="utf-8") as fh:
            rows += [Runner(r["name"], r["word"], r.get("own"), r.get("tail", "")) for r in json.load(fh)["runners"]]
    return tuple(rows)


RUNNERS = load_runners()
# A runner prints its lines after its terminal line in one pass with nothing to wait on; where the writer cannot be
# seen, this long a quiet log means the pass is over.
SETTLE_S = 2.0
POLL_S = 1.0
# Python start-up and the last read and print on a loaded machine, so the wait returns before its Bash timeout moves it.
MARGIN_S = 15
BLOCK_LINES = 40
LINE_CHARS = 300


def default_max():
    import foreground_wait_guard
    return foreground_wait_guard.wait_clamp_ms() // 1000 - MARGIN_S


def writer_open(path):
    """True while any process holds `path` open for writing, False when none does, None where that cannot be seen.
    Windows refuses a read open that denies write sharing while a write handle is open."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.HANDLE)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(path, 0x80000000, 0x1, None, 3, 0, None)  # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
    if handle == wintypes.HANDLE(-1).value:
        return True if ctypes.get_last_error() == 32 else None  # ERROR_SHARING_VIOLATION
    kernel.CloseHandle(handle)
    return False


def _end(row):
    return re.compile("^" + re.escape(row.word) + row.tail, re.M)


def runner_of(text, runners=None):
    """(the log's runner row or None, whether its terminal line is in the log)."""
    for row in runners or RUNNERS:
        ended = bool(_end(row).search(text))
        if ended or (row.own and re.search(row.own, text, re.M)):
            return row, ended
    return None, False


def block(text, runners=None):
    """Each terminal line of any runner in the log, with the lines after it up to a blank line: the verdict and the
    inner verdicts it summarizes, never the progress lines between them."""
    lines = text.splitlines()
    ends = [_end(r) for r in runners or RUNNERS]
    keep = []
    for i, line in enumerate(lines):
        if any(end.match(line) for end in ends):
            j = i
            while j < len(lines) and lines[j].strip():
                if j not in keep:
                    keep.append(j)
                j += 1
    shown = [lines[i][:LINE_CHARS] for i in sorted(keep)]
    if len(shown) > BLOCK_LINES:
        shown = shown[:BLOCK_LINES] + ["... %d more lines in the log" % (len(shown) - BLOCK_LINES)]
    return shown


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _last(text, n=1):
    lines = [line[:LINE_CHARS] for line in text.splitlines() if line.strip()]
    return lines[-n:]


def wait(log, max_s, poll_s=POLL_S, settle_s=SETTLE_S, writer=writer_open, runners=None):
    runners = runners or RUNNERS
    began = time.monotonic()
    size, changed, idle = None, began, 0
    while True:
        text = _read(log)
        now = time.monotonic()
        if text is not None:
            grew = len(text) != size
            if grew:
                size, changed = len(text), now
            held = writer(log)
            row, ended = runner_of(text, runners)
            if ended and (held is False or now - changed >= settle_s):
                print("RUN-WAIT DONE %s waited=%ds log=%s" % (row.name, now - began, log))
                print("\n".join(block(text, runners)))
                return 0
            # Two looks a poll apart, no writer and no growth: one look can fall between a launch and its redirect.
            idle = idle + 1 if held is False and (idle == 0 or not grew) else 0
            if not ended and idle >= 2:
                print("RUN-WAIT ENDED no process writes the log and it holds no runner's terminal line (%s); its last "
                      "lines:" % ", ".join(r.word.strip() for r in runners))
                print("\n".join(_last(text, 5)))
                return 2
        if now - began >= max_s:
            if text is None:
                print("RUN-WAIT NO-LOG %s never appeared in %ds" % (log, now - began))
                return 2
            row, _ = runner_of(text, runners)
            last = _last(text)
            print("RUN-WAIT RUNNING %s waited=%ds log=%s last=%s"
                  % (row.name if row else "no-runner-line-yet", now - began, log, last[0] if last else ""))
            return 3
        time.sleep(poll_s)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="run_wait.py", description=__doc__.split("\n\n", 1)[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log")
    parser.add_argument("--max", type=float, default=None, help="seconds before exit 3 (default: the caller's clamp less %d)" % MARGIN_S)
    args = parser.parse_args(argv)
    return wait(args.log, args.max if args.max is not None else default_max())


if __name__ == "__main__":
    sys.exit(main())
