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

Also here: the two cadence gates every advisory hook shares. `fire_once` delivers an advisory
once per session; `fire_once_since_compaction` re-arms when `transcript_backup.py` calls
`clear_compaction_keys` on PreCompact, because a compacted session lost the first delivery from
its context. Both replace turn-count heartbeats and wall-clock re-fire windows, neither of which
tracks whether the model still has the text.

Fail posture: every function is best-effort and total. A read never raises — it returns
`{}` when there is nothing recoverable. A write never raises — it returns False. State
that cannot be written must degrade to a missing nudge, never to a crashed hook.
"""

import io
import json
import os
import tempfile

__all__ = [
    "HARNESS_DIRS",
    "read_json_salvage",
    "write_json_atomic",
    "state_path",
    "fire_once",
    "fire_once_since_compaction",
    "clear_compaction_keys",
    "log_path",
    "append_jsonl_rotating",
]

# The harness code set: what `scripts/harness_tests.py` hashes into the stamp and what
# `hooks/git_guardrails.py` demands a stamp for. One home so the two never disagree.
HARNESS_DIRS = (
    ".claude/hooks",
    ".claude/tools",
    ".claude/scripts",
    ".claude/workflows",
    ".claude/tests",
    ".claude/settings.json",
)

# One state file per session, shared by every hook. HARNESS_HOOK_STATE_DIR redirects it so a
# test never writes the real one.
_DEFAULT_STATE_DIR = os.path.expanduser("~/.claude/.routing_state")

_ONCE_KEY = "fired_once"
_COMPACTION_KEY = "fired_since_compaction"


def state_dir():
    return os.environ.get("HARNESS_HOOK_STATE_DIR") or _DEFAULT_STATE_DIR


def state_path(session_id):
    """The shared per-session state file. Same `<sid[:8]>.json` shape every hook uses."""
    return os.path.join(state_dir(), f"{(session_id or 'default')[:8]}.json")


def _fire_once_under(session_id, key, bucket):
    """True the first time `key` is asked for under `bucket`, False afterwards.

    Fail-open toward firing: a state file that cannot be read or written yields True, so a
    broken state layer costs a repeated advisory rather than a silent one that never fires.
    """
    path = state_path(session_id)
    state = read_json_salvage(path)
    fired = state.get(bucket)
    if not isinstance(fired, dict):
        fired = {}
    if fired.get(key):
        return False
    fired[key] = True
    state[bucket] = fired
    write_json_atomic(path, state)
    return True


def fire_once(session_id, key):
    """True on the first ask for `key` this session — nothing clears it."""
    return _fire_once_under(session_id, key, _ONCE_KEY)


def fire_once_since_compaction(session_id, key):
    """True on the first ask for `key` since the session started or last compacted.

    A compacted session lost the earlier emission from its context, so the advisory has to
    land again; `clear_compaction_keys` is what re-arms it.
    """
    return _fire_once_under(session_id, key, _COMPACTION_KEY)


def clear_compaction_keys(session_id):
    """Re-arm every `fire_once_since_compaction` key. Called from the PreCompact hook."""
    path = state_path(session_id)
    state = read_json_salvage(path)
    if _COMPACTION_KEY not in state:
        return False
    state.pop(_COMPACTION_KEY, None)
    return write_json_atomic(path, state)


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


def log_path(name, env_override=None):
    """`<CLAUDE_PROJECT_DIR or cwd>/logs/<name>`, or the path in `env_override` when that
    variable is set (proofs redirect their log this way)."""
    override = os.environ.get(env_override) if env_override else None
    if override:
        return override
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, "logs", name)


def append_jsonl_rotating(path, records, max_bytes=2_000_000, keep_lines=500):
    """Append one JSON line per record; once the file passes `max_bytes`, keep the newest
    `keep_lines` first (tail rotation via tempfile + rename). Best-effort: returns False on
    any failure, never raises — every caller is an observer hook."""
    if not records:
        return True
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-keep_lines:]
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".jsonl", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
            os.replace(tmp_path, path)
        with io.open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError):
        return False


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
