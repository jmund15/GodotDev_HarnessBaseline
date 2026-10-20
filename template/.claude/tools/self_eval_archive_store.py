#!/usr/bin/env python3
"""Bounded structured-entry store for the self-evaluation archive.

The tracked JSON archive remains the read-only legacy snapshot. New and revised
structured rows use a capped JSONL ledger beside it. Newest rows win by session
identity, so an upsert never rewrites the large historical file.
"""
import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, HOOKS)
from _file_lock import locked  # noqa: E402
from self_eval_archive_guard import (  # noqa: E402
    LEDGER_ROTATIONS, check as validate_document, entry_warnings,
)

ARCHIVE_MAX_BYTES = 256 * 1024
ARCHIVE_MAX_ROTATIONS = LEDGER_ROTATIONS
LOCK_TIMEOUT_SECONDS = 10.0


def ledger_path(archive):
    return archive[:-5] + ".jsonl" if archive.endswith(".json") else archive + ".jsonl"


def ledger_paths(archive):
    active = ledger_path(archive)
    rotated = [active + ".%d" % index for index in range(ARCHIVE_MAX_ROTATIONS, 0, -1)]
    return [path for path in rotated + [active] if os.path.isfile(path)]


def _identity(entry):
    session_id = entry.get("session_id")
    if session_id:
        return ("session", session_id)
    return ("legacy", entry.get("title"), entry.get("date"))


def _read_ledger_entries(archive):
    rows = []
    for path in ledger_paths(archive):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError as exc:
                        raise ValueError("invalid ledger row %s:%d" % (path, line_number)) from exc
                    if not isinstance(entry, dict):
                        raise ValueError("ledger row must be an object: %s:%d" % (path, line_number))
                    rows.append(entry)
        except OSError as exc:
            raise ValueError("cannot read ledger %s: %s" % (path, exc)) from exc
    return rows


def _load_archive_unlocked(archive):
    try:
        with open(archive, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read legacy archive: %s" % exc) from exc
    if not isinstance(document, dict):
        raise ValueError("legacy archive root must be an object")
    entries = document.get("structured_entries")
    if not isinstance(entries, list):
        raise ValueError("legacy structured_entries must be a list")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError("legacy structured entry %d must be an object" % index)
    return document, list(entries) + _read_ledger_entries(archive)


def load_archive(archive):
    with _lock(ledger_path(archive) + ".lock"):
        return _load_archive_unlocked(archive)


def _effective_rows(raw):
    effective = {}
    for entry in raw:
        effective[_identity(entry)] = entry
    return list(effective.values())


def _validate_entry(document, entry):
    themes = document.get("Self_Evaluate_Themes")
    patterns = themes.get("patterns") if isinstance(themes, dict) else None
    if not isinstance(patterns, dict):
        raise ValueError("legacy archive pattern enum must be an object")
    probe = {
        "Self_Evaluate_Themes": themes,
        "structured_entries": [entry],
    }
    violations = validate_document(json.dumps(probe))
    if violations:
        raise ValueError("invalid archive entry: " + "; ".join(violations))


def effective_entries(archive):
    _, raw = load_archive(archive)
    return _effective_rows(raw)


def find_session(archive, session_id):
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("lookup requires exact session_id")
    document, raw = load_archive(archive)
    for entry in reversed(_effective_rows(raw)):
        if entry.get("session_id") == session_id:
            _validate_entry(document, entry)
            return entry
    return None


@contextmanager
def _lock(path):
    with locked(path, LOCK_TIMEOUT_SECONDS):
        yield


def _atomic_replace(path, content):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".self-eval-ledger-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _entry_payload(entry):
    return (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write_ledger_chunks(archive, rows):
    """Replace the whole ledger with `rows`, in order, under the byte cap and rotation bound."""
    chunks = []
    current = b""
    for row in rows:
        payload = _entry_payload(row)
        if len(payload) > ARCHIVE_MAX_BYTES:
            raise ValueError("entry exceeds archive byte cap")
        if current and len(current) + len(payload) > ARCHIVE_MAX_BYTES:
            chunks.append(current)
            current = b""
        current += payload
    if current:
        chunks.append(current)
    if len(chunks) > ARCHIVE_MAX_ROTATIONS + 1:
        raise ValueError("effective archive exceeds bounded ledger capacity; no rows were removed")

    active = ledger_path(archive)
    targets = {}
    for index, content in enumerate(chunks):
        distance = len(chunks) - index - 1
        target = active if distance == 0 else active + ".%d" % distance
        targets[target] = content
    for target, content in targets.items():
        _atomic_replace(target, content)
    for path in [active] + [active + ".%d" % index
                            for index in range(1, ARCHIVE_MAX_ROTATIONS + 1)]:
        if path not in targets:
            try:
                os.unlink(path)
            except OSError:
                pass


def _rewrite_effective_ledger(archive, rows):
    """Compact superseded history without dropping an effective ledger row."""
    effective = {}
    for row in rows:
        effective[_identity(row)] = row
    _write_ledger_chunks(archive, list(effective.values()))


def reid_session(archive, session_id):
    """Give one session's row the next free id, keeping it otherwise intact.

    A duplicate id (two worktrees appending their own `max(id)+1`) makes the whole store fail the
    guard, and hand-editing the ledger is how a repair drifts from the store's contract. The row
    keeps its position and every other field; a row that predates `SPEND_MIN_ID` gains the
    unavailable-reason `spend` object the guard requires of an id above it. The session's earlier
    superseded rows collapse into the one revised row; every other session's rows are untouched.
    Returns the revised row, or None when no row holds that session.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("reid requires exact session_id")
    active = ledger_path(archive)
    with _lock(active + ".lock"):
        document, raw = _load_archive_unlocked(archive)
        snapshot_rows = raw[:len(document["structured_entries"])]
        ledger_rows = raw[len(snapshot_rows):]
        current = None
        for row in ledger_rows:
            if row.get("session_id") == session_id:
                current = row
        if current is None:
            if any(row.get("session_id") == session_id for row in snapshot_rows):
                raise ValueError("session %s lives in the legacy snapshot; re-id it by hand"
                                 % session_id)
            return None
        candidate = dict(current)
        old_id = candidate.get("id")
        candidate["id"] = max(
            (row.get("id", 0) for row in raw if isinstance(row.get("id"), int)
             and not isinstance(row.get("id"), bool)),
            default=0,
        ) + 1
        if "spend" not in candidate:
            try:
                _validate_entry(document, candidate)
            except ValueError:
                candidate["spend"] = {
                    "unavailable": "row predates SPEND_MIN_ID; re-id from duplicate %s" % old_id}
        _validate_entry(document, candidate)
        replaced = []
        placed = False
        for row in ledger_rows:
            if row.get("session_id") == session_id:
                if not placed:
                    replaced.append(candidate)
                    placed = True
                continue
            replaced.append(row)
        _write_ledger_chunks(archive, replaced)
        return candidate


def _session_digest():
    """The digest module, imported lazily: only the facts path needs it (and its transcript parse)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import session_digest
    return session_digest


def derive_facts(session_id, projects_root=None):
    """`facts` for one session: the transcript's own numbers, or the reason there are none.

    The transcript is found exactly as `/session_end`'s digest finds it, so an unknown, ambiguous
    or pruned session is a stated `unavailable` reason rather than an empty 0 the reader would sum.
    `projects_root` overrides the transcript folder (default: the store that holds the session among
    this cwd's checkouts).
    """
    digest = _session_digest()
    if not isinstance(session_id, str) or not session_id.strip():
        return {"unavailable": "no session identity"}
    root = projects_root or digest.session_store(os.getcwd(), session_id)
    try:
        path = digest.pick_transcript(root, session_id, None)
    except SystemExit as exc:
        reason = "" if isinstance(exc.code, int) else str(exc.code).strip()
        # The exit text names every store it searched; those machine paths do not belong in a row
        # that outlives the machine and is counted against the ledger's byte cap.
        reason = reason.split(" under ", 1)[0].strip()
        return {"unavailable": reason or "no transcript for session %s" % session_id}
    try:
        return digest.session_facts(path)
    except (OSError, ValueError) as exc:
        return {"unavailable": "transcript %s unreadable: %s" % (getattr(path, "name", path), exc)}


def _publish_locked(archive, document, raw, candidate):
    """Make `candidate` the effective row of its session: append, or compact at the byte cap."""
    payload = _entry_payload(candidate)
    if len(payload) > ARCHIVE_MAX_BYTES:
        raise ValueError("entry exceeds archive byte cap")
    ledger_rows = raw[len(document["structured_entries"]):]
    active = ledger_path(archive)
    try:
        with open(active, "rb") as handle:
            current_bytes = handle.read()
    except OSError:
        current_bytes = b""
    if not current_bytes or len(current_bytes) + len(payload) <= ARCHIVE_MAX_BYTES:
        _atomic_replace(active, current_bytes + payload)
    else:
        _rewrite_effective_ledger(archive, ledger_rows + [candidate])
    return candidate


def upsert_entry(archive, entry, projects_root=None):
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    session_id = entry.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("entry requires exact session_id")
    # Derived before the lock: the transcript parse is seconds, and the ledger lock is shared.
    candidate = dict(entry)
    candidate["facts"] = derive_facts(session_id, projects_root)
    active = ledger_path(archive)
    with _lock(active + ".lock"):
        document, raw = _load_archive_unlocked(archive)
        effective = {}
        for row in raw:
            effective[_identity(row)] = row
        visible = list(effective.values())
        current = effective.get(_identity(candidate))
        if current and isinstance(current.get("id"), int):
            candidate["id"] = current["id"]
        elif isinstance(candidate.get("id"), int):
            if any(row.get("id") == candidate["id"] for row in visible):
                raise ValueError("entry id already belongs to another session")
        else:
            candidate["id"] = max(
                (row.get("id", 0) for row in visible if isinstance(row.get("id"), int)),
                default=0,
            ) + 1
        _validate_entry(document, candidate)
        if candidate == current:
            return candidate
        return _publish_locked(archive, document, raw, candidate)


def refresh_facts(archive, session_id, projects_root=None):
    """Re-derive one existing row's `facts` and publish it (a row written before the field).

    The row keeps every other field and its id. Returns the revised row, or None when no row holds
    that session.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("refresh requires exact session_id")
    facts = derive_facts(session_id, projects_root)
    active = ledger_path(archive)
    with _lock(active + ".lock"):
        document, raw = _load_archive_unlocked(archive)
        effective = {}
        for row in raw:
            effective[_identity(row)] = row
        current = effective.get(("session", session_id))
        if current is None:
            return None
        candidate = dict(current)
        candidate["facts"] = facts
        _validate_entry(document, candidate)
        if candidate == current:
            return candidate
        return _publish_locked(archive, document, raw, candidate)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=".claude/self_evaluate_archive.json")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--upsert", metavar="ENTRY_JSON")
    action.add_argument("--lookup", metavar="SESSION_ID")
    action.add_argument("--reid", metavar="SESSION_ID",
                        help="give this session's row the next free id (repairs a duplicate id)")
    action.add_argument("--refresh-facts", metavar="SESSION_ID",
                        help="re-derive this row's store-derived facts and publish them")
    args = parser.parse_args(argv)
    try:
        if args.upsert:
            with open(args.upsert, encoding="utf-8") as handle:
                entry = json.load(handle)
            stored = upsert_entry(args.archive, entry)
            for warning in entry_warnings(stored):
                print("WARN " + warning, file=sys.stderr)
            print(json.dumps(stored, ensure_ascii=False))
            return 0
        if args.refresh_facts:
            stored = refresh_facts(args.archive, args.refresh_facts)
            if stored is None:
                return 1
            print(json.dumps(stored, ensure_ascii=False))
            return 0
        if args.reid:
            stored = reid_session(args.archive, args.reid)
            if stored is None:
                return 1
            print(json.dumps(stored, ensure_ascii=False))
            return 0
        stored = find_session(args.archive, args.lookup)
        if stored is None:
            return 1
        print(json.dumps(stored, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TimeoutError) as exc:
        print("REFUSED: " + str(exc))
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
