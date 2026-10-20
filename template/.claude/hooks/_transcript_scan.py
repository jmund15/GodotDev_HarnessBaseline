#!/usr/bin/env python3
"""Resume a transcript reader from its cached byte offset instead of re-reading from line 1.

A Claude Code transcript is append-only JSONL. A reader written as a `Scanner` folds each line into a
JSON-native state. `scan()` saves that state with the byte offset after the last complete line, and
the next call on the same file seeks there and feeds only the lines appended since. The cache stores
the line count with the offset, so `index` (1-based) is the line number a cold
`enumerate(open(path, encoding=...), 1)` gives: owner U-ids are these numbers and never shift.
Lines are split and decoded as text mode does (universal newlines, the scanner's `errors`).

The cache is dropped and the scan restarts at line 1 when the file is shorter than the cached offset,
when the first or last FINGERPRINT_BYTES of the cached prefix changed (a truncated, rewritten or
replaced file), or when the scanner's name, `params` or source files changed: the code fingerprint
covers every file in `sources`, so an edit to a reader's rules drops what the old rules cached.
A last line with no newline yet (a write in progress) is fed into this call's result and never saved.
A feed that raises propagates and saves nothing past the previous save.

Caches: one JSON file per (transcript path, scanner) under `<_hook_state.state_dir()>/transcript_scan/`,
written atomically. The first save in a process sweeps cache files whose transcript is gone or that
went SWEEP_AFTER_SEC untouched, and temp files a killed writer left. `STATS` lists each scan this
process ran (resumed offset, bytes read, seconds) for the callers' run logs.
"""
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import state_dir, write_json_atomic  # noqa: E402

FINGERPRINT_BYTES = 65536
SWEEP_AFTER_SEC = 30 * 86400  # Claude Code's default transcript retention (cleanupPeriodDays)
TEMP_AFTER_SEC = 3600
CACHE_DIR = "transcript_scan"
STATS = []
_swept = False
_code_prints = {}
_PATH_KEY = re.compile(rb'^\{"path": ("(?:[^"\\]|\\.)*")')


class Scanner:
    """A fold over transcript lines: `initial()` returns a fresh JSON-native state; `feed(state, index,
    line)` folds one line (newline kept, as text mode yields it) into that state in place. `sources`
    are the files whose code decides the fold; `params` is any other input it depends on."""

    def __init__(self, name, initial, feed, sources, errors="strict", params=None):
        self.name, self.initial, self.feed = name, initial, feed
        self.sources, self.errors, self.params = tuple(str(s) for s in sources), errors, params

    def code_print(self):
        key = (self.name,) + self.sources
        if key not in _code_prints:
            digest = hashlib.sha256(self.name.encode())
            for source in self.sources:
                with open(source, "rb") as fh:
                    digest.update(fh.read())
            _code_prints[key] = digest.hexdigest()
        return _code_prints[key]


def cache_path(path, scanner):
    key = hashlib.sha256(os.path.normcase(os.path.abspath(str(path))).encode("utf-8")).hexdigest()[:16]
    return os.path.join(state_dir(), CACHE_DIR, f"{key}.{scanner.name}.json")


def _text_lines(raw, errors):
    """The lines text mode yields for one raw chunk: `\\r\\n` and a lone `\\r` both end a line as `\\n`."""
    text = raw.decode("utf-8", errors)
    if "\r" not in text:
        return [text]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [part + "\n" for part in text.split("\n")[:-1]]
    return lines + ([text.rsplit("\n", 1)[-1]] if not text.endswith("\n") else [])


def _fingerprint(fh, end):
    fh.seek(0)
    head = hashlib.sha256(fh.read(min(end, FINGERPRINT_BYTES))).hexdigest()
    start = max(0, end - FINGERPRINT_BYTES)
    fh.seek(start)
    tail = hashlib.sha256(fh.read(end - start)).hexdigest()
    return [head, tail]


def _load(path, cache_file, scanner, fh, size):
    """(offset, line count, state) from a cache that still describes this file's prefix, else None."""
    try:
        with open(cache_file, encoding="utf-8") as cf:
            record = json.load(cf)
        offset, lines = record["offset"], record["lines"]
        if (record.get("path") != path or record.get("code") != scanner.code_print()
                or record.get("params") != scanner.params or not isinstance(offset, int)
                or not isinstance(lines, int) or not 0 < offset <= size):
            return None
        if _fingerprint(fh, offset) != record.get("fingerprint"):
            return None
        return offset, lines, record["state"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _sweep(directory):
    now = time.time()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        full = os.path.join(directory, name)
        try:
            age = now - os.path.getmtime(full)
            if name.startswith(".tmp_"):
                if age > TEMP_AFTER_SEC:
                    os.unlink(full)
                continue
            if age > SWEEP_AFTER_SEC:
                os.unlink(full)
                continue
            with open(full, "rb") as fh:
                match = _PATH_KEY.match(fh.read(4096))
            if match and not os.path.exists(json.loads(match.group(1))):
                os.unlink(full)
        except (OSError, ValueError):
            continue


def _save(cache_file, record):
    global _swept
    if not _swept:
        _swept = True
        _sweep(os.path.dirname(cache_file))
    write_json_atomic(cache_file, record)


def scan(path, scanner, fresh=False):
    """The scanner's state over every line of `path`, resumed from the cache when it is valid.
    `fresh` starts at line 1 regardless, for a caller whose own check found the cached state unusable."""
    path = os.path.abspath(str(path))
    cache_file = cache_path(path, scanner)
    started = time.perf_counter()
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        cached = None if fresh else _load(path, cache_file, scanner, fh, size)
        offset, count, state = cached if cached else (0, 0, scanner.initial())
        start = offset
        fh.seek(offset)
        partial = b""
        for raw in fh:
            if not raw.endswith(b"\n"):
                partial = raw
                break
            for line in _text_lines(raw, scanner.errors):
                count += 1
                scanner.feed(state, count, line)
            offset += len(raw)
        if offset > start:
            _save(cache_file, {"path": path, "code": scanner.code_print(), "params": scanner.params,
                               "offset": offset, "lines": count, "fingerprint": _fingerprint(fh, offset),
                               "state": state})
    if partial:
        for line in _text_lines(partial, scanner.errors):
            count += 1
            scanner.feed(state, count, line)
    STATS.append({"scanner": scanner.name, "resumed_at": start, "read_bytes": offset - start + len(partial),
                  "seconds": round(time.perf_counter() - started, 3)})
    return state
