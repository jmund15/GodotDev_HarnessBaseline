"""
Library (not a hook): crash-safe JSON state for the files hooks share per session.

Why this exists — measured 2026-08-20. Hooks share `~/.claude/.routing_state/<sid8>.json`.
Seven of them wrote it as `open(path, "w")` + `json.dump`, which truncates first and
fills after. Hooks fire on the same tool call, so two can sit inside that window at
once and their writes interleave: the file ended up holding one complete JSON document
followed by a fragment of another.

What that cost: `json.load` raised, `harness_edit_skill_reminder.py` read no state,
concluded `instruction_quality` had never been loaded, and DENIED every harness edit for
the rest of the session. Its remedy ("invoke the Skill and retry") could not work,
because `skill_load_marker.py` hits the same parse error inside `except Exception: pass`
and so never rewrites the marker. A corrupted byte range locked the session out of
harness editing permanently, while telling the reader to do the one thing that cannot help.

Two properties close that, and both are needed:

  write_json_atomic  — same-directory tempfile + `os.replace`. A reader sees either the
                       old file or the new one, never a half-written one. This is what
                       `instruction_quality` §16 requires of shared hook state.
  read_json_salvage  — recovers the leading JSON document from a file that is already
                       torn, instead of returning empty. Atomic writes stop NEW damage;
                       salvage is what keeps damage that predates them (or arrives from
                       an unconverted writer) from being permanent.

Fail posture: every function is best-effort and total. A read never raises — it returns
`{}` when there is nothing recoverable. A write never raises — it returns False. State
that cannot be written must degrade to a missing nudge, never to a crashed hook.
"""

import io
import json
import os
import tempfile

__all__ = ["read_json_salvage", "write_json_atomic"]


def read_json_salvage(path):
    """Return the dict at `path`. Salvages a torn file; returns {} when unrecoverable."""
    try:
        raw = io.open(path, encoding="utf-8").read()
    except (OSError, ValueError):
        return {}

    if not raw.strip():
        return {}

    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except ValueError:
        pass

    # Torn file: an interleaved write leaves a complete document followed by debris.
    # raw_decode stops cleanly at the end of the first one.
    try:
        loaded, _ = json.JSONDecoder().raw_decode(raw)
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json_atomic(path, obj):
    """Write `obj` to `path` via same-dir tempfile + rename. True on success."""
    directory = os.path.dirname(path) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=True)
        os.replace(tmp_path, path)   # same filesystem by construction
        return True
    except (OSError, ValueError, TypeError):
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return False
