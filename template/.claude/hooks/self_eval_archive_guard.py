#!/usr/bin/env python3
"""
Hook: PostToolUse on Write|Edit — validates the self-evaluation snapshot and ledger.

The JSON snapshot is checked as one document. Ledger validation scans the snapshot,
all bounded rotations, and the active JSONL, then applies newest-row-wins per session while
rejecting ID ownership conflicts. Other paths emit `{}`.
PostToolUse cannot block; violations use `additionalContext` so the model repairs
manual writes. The archive store validates before publication.
"""

import json
import os
import re
import sys

OUTCOMES = {"clean", "correction", "failure"}
LEGACY_MAX_ID = 266
ENUM_HOMES = ("enum homes: patterns = Self_Evaluate_Themes.patterns in the archive; "
              "outcomes = clean|correction|failure (/self_evaluate §5d)")


def archive_kind(file_path):
    normalized = (file_path or "").replace("\\", "/")
    if normalized.endswith("self_evaluate_archive.json"):
        return "snapshot", file_path
    if re.search(r"self_evaluate_archive\.jsonl(?:\.\d+)?$", normalized):
        archive = re.sub(r"\.jsonl(?:\.\d+)?$", ".json", file_path)
        return "ledger", archive
    return None, None


def rel_path_ok(file_path):
    return archive_kind(file_path)[0] is not None


def check(text, require_session=False):
    """Return a list of `id=<n>: <violation>` strings, or [] when clean."""
    try:
        doc = json.loads(text)
    except ValueError:
        return ["id=?: archive is not valid JSON"]
    if not isinstance(doc, dict):
        return ["id=?: archive root must be an object"]

    violations = []
    themes = doc.get("Self_Evaluate_Themes")
    pattern_defs = themes.get("patterns") if isinstance(themes, dict) else None
    if not isinstance(pattern_defs, dict):
        violations.append("id=?: Self_Evaluate_Themes.patterns must be an object")
        patterns = {None}
    else:
        patterns = set(pattern_defs)
        patterns.add(None)

    entries = doc.get("structured_entries")
    if not isinstance(entries, list):
        violations.append("id=?: structured_entries must be a list")
        return violations
    seen_ids = {}
    seen_sessions = {}

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            violations.append(f"id=?: structured entry {index} must be an object")
            continue
        eid = entry.get("id")
        outcome = entry.get("outcome")
        pattern = entry.get("pattern")
        session_id = entry.get("session_id")

        valid_id = isinstance(eid, int) and not isinstance(eid, bool)
        if not valid_id:
            violations.append(f"id=?: structured entry {index} id must be an integer")
        elif eid in seen_ids:
            violations.append(f"id={eid}: duplicate id")
        else:
            seen_ids[eid] = True

        session_valid = isinstance(session_id, str) and bool(session_id.strip())
        session_required = require_session or (valid_id and eid > LEGACY_MAX_ID)
        if session_required and not session_valid:
            violations.append(
                f"id={eid if valid_id else '?'}: session_id must be a non-empty string"
            )
        elif session_id is not None and not session_valid:
            violations.append(f"id={eid if valid_id else '?'}: session_id must be a non-empty string")
        elif session_valid and session_id in seen_sessions:
            violations.append(f"id={eid}: duplicate session_id {session_id!r}")
        elif session_valid:
            seen_sessions[session_id] = True

        if "outcome" not in entry:
            violations.append(f"id={eid if valid_id else '?'}: missing outcome")
        elif not isinstance(outcome, str) or outcome not in OUTCOMES:
            violations.append(f"id={eid if valid_id else '?'}: outcome {outcome!r} not in {sorted(OUTCOMES)}")

        valid_pattern = pattern is None or isinstance(pattern, str)
        if not valid_pattern:
            violations.append(f"id={eid if valid_id else '?'}: pattern must be a string or null")
        if valid_id and eid > LEGACY_MAX_ID:
            if valid_pattern and pattern not in patterns:
                violations.append(f"id={eid}: pattern {pattern!r} not in enum")
            if pattern is None and outcome in ("correction", "failure"):
                violations.append(f"id={eid}: null pattern with outcome {outcome!r}")

    return violations


LEDGER_ROTATIONS = 6


def _ledger_paths(archive_path):
    active = archive_path[:-5] + ".jsonl" if archive_path.endswith(".json") else archive_path + ".jsonl"
    rotated = [active + f".{index}" for index in range(LEDGER_ROTATIONS, 0, -1)]
    return [path for path in rotated + [active] if os.path.isfile(path)]


def _parse_ledger(text, source):
    entries = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            return [], [f"id=?: ledger row {line_number} in {source} is not valid JSON"]
        if not isinstance(entry, dict):
            return [], [f"id=?: ledger row {line_number} in {source} is not an object"]
        entries.append(entry)
    return entries, []


def _owner(entry, source, index):
    session_id = entry.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return ("session", session_id)
    return (source, index)


def _identity(entry, source, index):
    session_id = entry.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return ("session", session_id)
    if source == "snapshot":
        legacy = ("legacy", entry.get("title"), entry.get("date"))
        try:
            hash(legacy)
            return legacy
        except TypeError:
            pass
    return (source, index)


def check_ledger(text, archive_path, edited_path=None):
    try:
        with open(archive_path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, ValueError):
        return ["id=?: cannot read legacy snapshot for ledger validation"]
    if not isinstance(snapshot, dict):
        return ["id=?: legacy snapshot root must be an object"]

    violations = check(json.dumps(snapshot))
    snapshot_entries = snapshot.get("structured_entries")
    if not isinstance(snapshot_entries, list):
        return violations

    ledger_entries = []
    edited_norm = os.path.normcase(os.path.abspath(edited_path)) if edited_path else None
    paths = _ledger_paths(archive_path)
    if edited_path and edited_norm not in {os.path.normcase(os.path.abspath(path)) for path in paths}:
        paths.append(edited_path)
    for path in paths:
        try:
            if edited_norm == os.path.normcase(os.path.abspath(path)):
                content = text
            else:
                with open(path, encoding="utf-8") as handle:
                    content = handle.read()
        except OSError:
            violations.append(f"id=?: cannot read ledger {path}")
            continue
        rows, errors = _parse_ledger(content, os.path.basename(path))
        violations.extend(errors)
        ledger_entries.extend((row, path) for row in rows)

    themes = snapshot.get("Self_Evaluate_Themes") or {}
    for entry, _source in ledger_entries:
        probe = {"Self_Evaluate_Themes": themes, "structured_entries": [entry]}
        violations.extend(check(json.dumps(probe), require_session=True))

    raw = [(entry, "snapshot") for entry in snapshot_entries if isinstance(entry, dict)]
    raw.extend(ledger_entries)
    id_owners = {}
    owner_ids = {}
    effective = {}
    for index, (entry, source) in enumerate(raw):
        owner = _owner(entry, source, index)
        eid = entry.get("id")
        if isinstance(eid, int) and not isinstance(eid, bool):
            previous_owner = id_owners.get(eid)
            if previous_owner is not None and previous_owner != owner:
                violations.append(f"id={eid}: id ownership conflict between distinct sessions")
            else:
                id_owners[eid] = owner
            previous_id = owner_ids.get(owner)
            if previous_id is not None and previous_id != eid:
                violations.append(
                    f"id={eid}: session_id {entry.get('session_id')!r} changed from id={previous_id}"
                )
            else:
                owner_ids[owner] = eid
        effective[_identity(entry, source, index)] = entry

    probe = {"Self_Evaluate_Themes": themes, "structured_entries": list(effective.values())}
    violations.extend(check(json.dumps(probe)))
    return list(dict.fromkeys(violations))


def process(data):
    """Dispatcher entry: `{"context": report}` when the archive is invalid, else None.

    `post_edit_dispatch.py` calls this; `main()` keeps the standalone channel.
    """
    if data.get("tool_name") not in ("Write", "Edit"):
        return None
    file_path = (data.get("tool_input") or {}).get("file_path")
    kind, archive_path = archive_kind(file_path)
    if kind is None:
        return None

    try:
        with open(file_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        violations = [f"id=?: cannot read archive input: {type(exc).__name__}: {exc}"]
    else:
        violations = check(text) if kind == "snapshot" else check_ledger(
            text, archive_path, file_path
        )
    if not violations:
        return None

    lines = [f"[self-eval-archive-guard] {len(violations)} violation(s) — fix before continuing:"]
    lines.extend(violations[:10])
    lines.append(ENUM_HOMES)
    return {"context": "\n".join(lines)}


def main():
    data = json.load(sys.stdin)
    result = process(data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": result["context"]}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
