"""The proof record of the paths a `baseline_sync.py pull` wrote.

`baseline_sync.py` writes a result per pulled path around its scoped proof run: `pending` before the run,
then `green`, `red`, or `unbound` (no proof binds the path). `git_guardrails.py` asks `blocked` before a
commit. A path blocks only while its recorded bytes are still the bytes on disk: an edit after the pull is
not the pulled tree, and its own commit is judged on its own.

The record is also the pull's journal: each entry carries the baseline `pin` it came from and the time, and
`open_paths` lists the paths still red or pending and uncommitted. The next `pull`, `baseline_sync.py check` and
`session_end_check.py` read it, so rows a red pull left in the tree stay named until a proof passes or a commit
takes them.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import update_json_locked  # noqa: E402

RECORD = os.path.join(".claude", "logs", "baseline_pull_proof.json")
_BLOCKING = ("pending", "red")


def record_path(root):
    return os.path.join(str(root), RECORD)


def _digest(root, rel):
    try:
        with open(os.path.join(str(root), rel), "rb") as source:
            return hashlib.sha256(source.read()).hexdigest()
    except OSError:
        return None


class Unreadable(Exception):
    """The record exists but does not read as a `paths` object. Readers treat it as unknown, never as empty."""


def _entries(root):
    path = record_path(root)
    if not os.path.exists(path):
        return {}
    return _entries_at(path)


def _entries_at(path):
    try:
        with open(path, encoding="utf-8") as source:
            doc = json.load(source)
    except (OSError, ValueError) as exc:
        raise Unreadable("%s does not parse: %s" % (RECORD, exc))
    paths = doc.get("paths") if isinstance(doc, dict) else None
    if not isinstance(paths, dict):
        raise Unreadable("%s has no paths object" % RECORD)
    return paths


def _strict_read(path):
    """The record as `write` reads it: an unreadable one raises, so a write never replaces its content."""
    if not os.path.exists(path):
        return {}
    _entries_at(path)
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def quarantine(root):
    """Move an unreadable record aside with its bytes intact and return the new path; None when it reads."""
    try:
        _entries(root)
    except Unreadable:
        pass
    else:
        return None
    path = record_path(root)
    aside = "%s.unreadable-%d" % (path, int(time.time() * 1000))
    os.replace(path, aside)  # bare-rename: a fresh name no other process holds
    return aside


def write(root, results, pin=None):
    """Record `results` ({relpath: pending|green|red|unbound}) against each path's bytes now, with the baseline
    commit `pin` the pull took them from and the time of the write. An unreadable record raises `Unreadable`
    and stays as it is; `quarantine` moves it aside first."""
    os.makedirs(os.path.dirname(record_path(root)), exist_ok=True)
    now = int(time.time())
    stamped = {rel: {"sha": _digest(root, rel), "result": result, "pin": pin, "at": now}
               for rel, result in results.items()}

    def update(doc):
        paths = doc.get("paths") if isinstance(doc.get("paths"), dict) else {}
        paths.update(stamped)
        doc.clear()
        doc.update({"ts": time.time(), "paths": paths})

    update_json_locked(record_path(root), update, raising=True, reader=_strict_read)


def blocked(root, relpaths):
    """The relpaths whose recorded result is pending or red and whose bytes are still the recorded bytes."""
    entries = _entries(root)
    out = []
    for rel in relpaths:
        entry = entries.get(rel)
        if not isinstance(entry, dict) or entry.get("result") not in _BLOCKING:
            continue
        if entry.get("sha") == _digest(root, rel):
            out.append(rel)
    return out


def unresolved(root):
    """Every recorded path still blocked, for a pull that reruns its proofs."""
    return blocked(root, sorted(_entries(root)))


def _git_paths(root, args):
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                         timeout=30)
    if out.returncode != 0:
        raise OSError(out.stderr)
    return out.stdout.split("\0")


def open_paths(root):
    """The journal's open rows: unresolved paths still uncommitted, the rows an earlier pull left in the tree with
    no green proof. A path git reports clean was committed, so it left the journal; a path git does not track
    (never committed, or ignored) stays. When git cannot answer every unresolved path stays, the alarming side."""
    pending = unresolved(root)
    if not pending:
        return []
    try:
        tracked = set(_git_paths(root, ["ls-files", "-z", "--", *pending]))
        dirty = {entry[3:] for entry in _git_paths(root, ["status", "--porcelain", "-z", "--", *pending])
                 if len(entry) > 3}
    except (OSError, subprocess.SubprocessError):
        return pending
    return [rel for rel in pending if rel not in tracked or rel in dirty]
