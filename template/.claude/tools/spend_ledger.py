#!/usr/bin/env python3
"""spend_ledger.py — the sidecar spend ledger's segments: one bound for the file, one reader for every consumer.

    python3 .claude/tools/spend_ledger.py rotate [--path LEDGER] [--limit BYTES]

The ledger (`~/.claude/sidecar_ledger.jsonl`, `SIDECAR_LEDGER_PATH` overrides it) is append-only and shared by every
session. A launcher that writes a row first calls `rotate`: a ledger past `ROTATE_BYTES` is renamed to
`<ledger>.seg-<UTC stamp>` and the next append starts a fresh file. No row is rewritten, copied or dropped.

A segment's stamp is the second its rotation ran, which is an upper bound on its rows' timestamps (a row is stamped
when written). So a reader that needs only rows stamped at or after `since` skips every segment whose stamp is
earlier, exactly, and a reader that needs history reads them all. `paths` lists the files in order; `iter_lines`
reads them, and a rotation that lands mid-pass loses and repeats no row.
"""
import argparse
import glob
import os
import re
import sys
import time
from datetime import datetime, timezone

SEGMENT = ".seg-"
STAMP = re.compile(r"\.seg-(\d{8}T\d{6}Z)$")
# Basis: a fresh process parsed the 34.7 MB ledger in 0.06-0.3 s (2026-11-10), and the Stop hook and the watcher read the
# live file each turn; 4 MiB is about a ninth of that, so a window read stays under 0.04 s.
ROTATE_BYTES = 4 * 1024 * 1024


def default_path():
    return os.environ.get("SIDECAR_LEDGER_PATH") or os.path.join(os.path.expanduser("~"), ".claude", "sidecar_ledger.jsonl")


def segment_epoch(segment_path):
    """The rotation stamp of a segment as epoch seconds, or None when the name is not a segment's."""
    found = STAMP.search(str(segment_path))
    if not found:
        return None
    return datetime.strptime(found.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()


def segments(path):
    """The ledger's segment files, oldest rotation first."""
    found = [(segment_epoch(p), p) for p in glob.glob(glob.escape(str(path)) + SEGMENT + "*")]
    return [p for stamp, p in sorted((s, p) for s, p in found if s is not None)]


def paths(path, since=None):
    """Every file that may hold a row stamped at or after `since` (epoch seconds; None means all), segments first, the
    live ledger last. The live path is listed whether or not it exists: it is absent between a rotation and the next append.
    A row stamped `t` is written no later than its segment's rotation second, so a segment stamped `s` holds rows of
    `since` or later only when `s + 1 > since`."""
    chosen = [p for p in segments(path) if since is None or segment_epoch(p) + 1 > since]
    return chosen + [str(path)]


def iter_lines(path, since=None, needle=None):
    """The ledger's lines, segments oldest first and the live file last; a line without `needle` (a substring) is skipped
    before its caller parses it. A missing live ledger holds nothing; a file that exists and cannot be read raises OSError.

    The live file is opened first, then the segments are listed: a rotation after the open leaves this handle on the
    renamed file, which the listing then names too, and it is read once, through the handle."""
    try:
        live = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        live = None
    try:
        held = None
        if live is not None:
            info = os.fstat(live.fileno())
            held = (info.st_dev, info.st_ino)
        for file in paths(path, since)[:-1]:
            info = os.stat(file)
            if held == (info.st_dev, info.st_ino):
                continue
            with open(file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if needle is None or needle in line:
                        yield line
        if live is not None:
            for line in live:
                if needle is None or needle in line:
                    yield line
    finally:
        if live is not None:
            live.close()


def rotate_if_large(path, limit=ROTATE_BYTES, now=None):
    """Rename a ledger past `limit` bytes to its segment name and return that name, else None. Windows refuses a rename
    while any process holds the file open, and a name already taken is never overwritten: both leave the ledger to the
    next writer's call."""
    path = str(path)
    try:
        if os.path.getsize(path) <= limit:
            return None
    except OSError:
        return None
    target = "%s%s%s" % (path, SEGMENT, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now)))
    if os.path.exists(target):
        return None
    try:
        os.rename(path, target)  # bare-rename: a fresh segment name, proven absent above; the OS refuses it while a reader holds the file
    except OSError:
        return None
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rot = sub.add_parser("rotate")
    rot.add_argument("--path")
    rot.add_argument("--limit", type=int, default=ROTATE_BYTES)
    args = parser.parse_args(argv)
    rotated = rotate_if_large(args.path or default_path(), args.limit)
    if rotated:
        print(rotated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
