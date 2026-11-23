"""Which transcript files hold this session's rows: one answer for every hook that proves "this session made it".

A hook's `transcript_path` names one file, while one `claude.exe` process can span several: a `/clear` or a
compaction starts a new session id and a new transcript in the same process, and the new file's `derived-from` row
names where it came from. `lineage(transcript_path)` walks those rows and yields `(path, until)`, newest first. The
first pair is the hook's own file with `until` None; each ancestor's `until` is the epoch time of its child's first
timestamped row, and a reader counts only ancestor rows stamped before it, so rows a later process appended to the
ancestor (a peer resuming it) never count.

A `derived-from` row is `{"type": "derived-from", "sessionId": <self>, "sessions": [{"id": ..., "via": ...}, ...]}`,
cumulative and newest last. The walk reads each file's LAST such row (matched by `type`: a quoted mention is not a
row), scans its entries newest to oldest and stops at the first whose `via` is not in `SAME_PROCESS`, self entries
included. The values seen on this machine's transcripts: `clear` and `compact` keep the process; `resume` starts a
new process (the harness marks the earlier process's tasks stopped, and an in-place resume appends a row listing only
the session itself), and `subprocess` is another process's session; neither is this session's. An unknown value is
not followed either.

Fail closed: an ancestor is yielded only when the child has a top-level timestamped row (without it no bound could
keep a peer's rows out) and its own last row reaches no boundary entry (a resumed file mixes an earlier process's rows
with this one's, and its `derived-from` row carries no stamp to split them by). An id is followed only when it is a
plain session id, a missing ancestor file skips that entry, a cycle ends, and `max_depth` ancestors bound the walk
(`cut` is set when a same-process ancestor file exists and the limit blocks it; the longest real chain on this
machine is two hops). A file is read only when the caller asks for the pair that needs it. Standard library only:
the floor imports it.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

SAME_PROCESS = frozenset({"clear", "compact"})
_ID = re.compile(r"[A-Za-z0-9_-]+")
_memo: dict[str, tuple[tuple[int, int], tuple[list | None, float | None, str | None]]] = {}


def row_stamp(row) -> float | None:
    """The epoch time of a row's top-level `timestamp`, else None."""
    try:
        return datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _scan(path: str) -> tuple[list | None, float | None, str | None]:
    """(entries of the file's last `derived-from` row or None, first top-level timestamp or None, that row's
    `sessionId`). One streaming pass: a resume row can sit mid-file, so it cannot stop early. Memoized by path,
    size and mtime; an unreadable file reads as nothing."""
    try:
        info = os.stat(path)
        key = (info.st_size, info.st_mtime_ns)
        cached = _memo.get(path)
        if cached and cached[0] == key:
            return cached[1]
        entries, first, own = None, None, None
        with open(path, "rb") as handle:
            for line in handle:
                wants_stamp = first is None and b'"timestamp"' in line
                if not wants_stamp and b'"derived-from"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if wants_stamp:
                    first = row_stamp(row)
                if row.get("type") == "derived-from":
                    sessions = row.get("sessions")
                    entries = sessions if isinstance(sessions, list) else []
                    own = row.get("sessionId") if isinstance(row.get("sessionId"), str) else None
    except (OSError, ValueError):
        return None, None, None
    _memo[path] = (key, (entries, first, own))
    return entries, first, own


def _reach(path: str) -> tuple[list[str], bool, float | None]:
    """(ids of the same-process entries of the file's last `derived-from` row, newest first; whether the scan hit a
    boundary entry; the file's first timestamp)."""
    entries, first, own = _scan(path)
    mine = {os.path.splitext(os.path.basename(path))[0], own}
    kept = []
    for entry in reversed(entries or []):
        if not isinstance(entry, dict) or entry.get("via") not in SAME_PROCESS:
            return kept, True, first
        sid = entry.get("id")
        if sid not in mine and isinstance(sid, str) and _ID.fullmatch(sid):
            kept.append(sid)
    return kept, False, first


class Lineage:
    """The walk, iterable once per `for`; `cut` reads True after a walk the depth limit blocked."""

    def __init__(self, transcript_path: str | None, max_depth: int):
        self._path, self._max_depth, self.cut = transcript_path, max_depth, False

    def __iter__(self):
        self.cut = False
        if not self._path:
            return
        yield self._path, None
        yield from self._ancestors(self._path, {self._key(self._path)}, 0)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def _ancestors(self, path: str, seen: set, depth: int):
        kept, _bounded, first = _reach(path)
        if first is None:
            return
        for sid in kept:
            parent = os.path.join(os.path.dirname(path), sid + ".jsonl")
            if self._key(parent) in seen or not os.path.isfile(parent):
                continue
            if depth >= self._max_depth:
                self.cut = True
                continue
            seen.add(self._key(parent))
            if _reach(parent)[1]:
                continue
            yield parent, first
            yield from self._ancestors(parent, seen, depth + 1)


def lineage(transcript_path: str | None, max_depth: int = 16) -> Lineage:
    """`(path, until)` for the hook's own transcript and each ancestor of the same process, newest first."""
    return Lineage(transcript_path, max_depth)
