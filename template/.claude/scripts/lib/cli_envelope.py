"""One reader for the Claude CLI output envelope.

`-o json` prints one result object, or, under the user setting `"verbose": true`, a JSON array of
every event; `-o stream-json` prints JSONL. The shape depends on flags and user settings, so every
consumer reads all three through `events`.
"""
import json
import uuid


def events(raw):
    """Object -> [obj]; array -> the list; JSONL -> its parsed dict lines; anything else -> []."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        doc = None
    else:
        if isinstance(doc, dict):
            return [doc]
        if isinstance(doc, list):
            return doc
        return []
    rows = []
    for line in (raw or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def result_event(evts):
    """The last `{"type": "result"}` event, or the single object, else None."""
    for row in reversed(evts):
        if isinstance(row, dict) and row.get("type") == "result":
            return row
    if len(evts) == 1 and isinstance(evts[0], dict):
        return evts[0]
    return None


def _uuid(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def session_id(evts):
    """The one distinct UUID-valid session_id across system and result rows; None when absent,
    invalid or ambiguous. Session-like text inside a model's answer is never read."""
    ids = {_uuid(row.get("session_id")) for row in evts
           if isinstance(row, dict) and row.get("type") in ("system", "result")}
    ids.discard(None)
    return next(iter(ids)) if len(ids) == 1 else None
