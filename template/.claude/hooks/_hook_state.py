"""
Library (not a hook): crash-safe JSON state for the files hooks share per session.

Why this exists — measured 2026-08-20. Hooks share `~/.claude/.routing_state/<sid8>.json`.
Seven of them wrote it as `open(path, "w")` + `json.dump`, which truncates first and
fills after. Hooks fire on the same tool call, so two can sit inside that window at
once and their writes interleave: the file ended up holding one complete JSON document
followed by a fragment of another.

What that cost: `json.load` raised, a hook that read the file saw no state, and its gate stayed shut for
the rest of the session while its remedy could not work, because the writer hit the same parse error.

Three properties close that:

  write_json_atomic  — same-directory tempfile + `os.replace`. A reader sees either the
                       old file or the new one, never a half-written one.
  read_json_salvage  — recovers the leading JSON document from a file that is already
                       torn, instead of returning empty.
  update_json_locked — serializes read-modify-write so sibling hooks cannot replace each
                       other's fields from stale snapshots.

Also here: the two cadence gates every advisory hook shares. `fire_once` delivers an advisory
once per session; `fire_once_since_compaction` re-arms when `transcript_backup.py` calls
`clear_compaction_keys` on PreCompact, because a compacted session lost the first delivery from
its context. Both replace turn-count heartbeats and wall-clock re-fire windows, neither of which
tracks whether the model still has the text. A key is spent when asked, so a line a dispatcher then
drops (a later hook blocks the call) gives its keys back with `unspend`: told means delivered.

Fail posture: every function is best-effort and total. A read never raises — it returns
`{}` when there is nothing recoverable. A write never raises — it returns False. State
that cannot be written must degrade to a missing nudge, never to a crashed hook.
"""

import importlib.util
import io
import json
import os
import re
import tempfile
import time

__all__ = [
    "read_json_salvage",
    "write_json_atomic",
    "update_json_locked",
    "state_path",
    "fire_once",
    "fire_once_machine",
    "fire_once_since_compaction",
    "spent_mark",
    "unspend",
    "clear_compaction_keys",
    "is_subagent_payload",
    "log_path",
    "replace_path",
    "append_jsonl_rotating",
]


# One state file per session, shared by every hook. HARNESS_HOOK_STATE_DIR redirects it so a
# test never writes the real one.
_DEFAULT_STATE_DIR = os.path.expanduser("~/.claude/.routing_state")

_ONCE_KEY = "fired_once"
_COMPACTION_KEY = "fired_since_compaction"


_COMPACTED_AT_KEY = "compacted_at"
_LOCK_WAIT_SECONDS = 2.0


def _load_file_lock():
    # By sibling path, not sys.path: tools load this module by file path from any cwd. A tree
    # without the sibling still imports, and every locked write then returns False.
    try:
        spec = importlib.util.spec_from_file_location(
            "_file_lock", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_file_lock.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


_file_lock = _load_file_lock()


def state_dir():
    return os.environ.get("HARNESS_HOOK_STATE_DIR") or _DEFAULT_STATE_DIR


def state_path(session_id):
    """The shared per-session state file. Same `<sid[:8]>.json` shape every hook uses."""
    return os.path.join(state_dir(), f"{(session_id or 'default')[:8]}.json")


def _fire_once_under(session_id, key, bucket, on_failure=True):
    """True the first time `key` is asked for under `bucket`, False afterwards.

    Fail-open toward firing: a state file that cannot be read or written yields `on_failure`
    (True), so a broken state layer costs a repeated advisory rather than a silent one that never fires.
    """
    path = state_path(session_id)

    def update(state):
        fired = state.get(bucket)
        if not isinstance(fired, dict):
            fired = {}
        if fired.get(key):
            return False
        fired[key] = True
        state[bucket] = fired
        return True

    written, first = update_json_locked(path, update)
    if written and first:
        _SPENT.append((session_id, key, bucket))
    return bool(first) if written else on_failure


# Every key this process newly spent, in order: a dispatcher that drops a hook's line reads it to give the keys back.
_SPENT = []


def spent_mark():
    """The journal position now: keys spent after it are the ones a later `unspend(mark)` gives back."""
    return len(_SPENT)


def unspend(start, stop=None):
    """Give back the once-keys spent between journal positions `start` and `stop` (default: now), so a line that
    never reached the agent is told again on the retry. Best-effort: an unwritable state leaves the keys spent."""
    spent = _SPENT[start:stop]
    del _SPENT[start:stop]
    by_session = {}
    for session_id, key, bucket in spent:
        by_session.setdefault(session_id, []).append((key, bucket))
    for session_id, keys in by_session.items():
        def update(state, keys=keys):
            for key, bucket in keys:
                fired = state.get(bucket)
                if isinstance(fired, dict):
                    fired.pop(key, None)
            return True
        update_json_locked(state_path(session_id), update)


def give_back(start, stop):
    """A dispatcher's block or deny drops the context of every hook that ran before the blocker, so the once-keys those
    hooks spent between `start` and `stop` go back and the retry carries their lines. The blocker's own keys, spent
    after `stop`, stay. A lost give-back costs a repeated line, never a wrong verdict, so it never raises."""
    try:
        unspend(start, stop)
    except Exception:  # noqa: BLE001
        pass


def fire_once(session_id, key, on_failure=True):
    """True on the first ask for `key` this session — nothing clears it.

    `on_failure` is the answer when the state cannot be written; a caller whose firing denies passes None.
    """
    return _fire_once_under(session_id, key, _ONCE_KEY, on_failure)


def fire_once_machine(key):
    """Claim `<client-version>:<platform>` once across sessions; retire other versions."""
    path = os.path.join(state_dir(), "machine.json")
    version = key.rsplit(":", 1)[0]

    def claim(state):
        fired = state.get(_ONCE_KEY)
        fired = fired if isinstance(fired, dict) else {}
        first = not fired.get(key)
        state[_ONCE_KEY] = {k: v for k, v in fired.items() if k.rsplit(":", 1)[0] == version}
        state[_ONCE_KEY][key] = True
        return first

    written, first = update_json_locked(path, claim)
    return bool(first) if written else True


def fire_once_since_compaction(session_id, key):
    """True on the first ask for `key` since the session started or last compacted.

    A compacted session lost the earlier emission from its context, so the advisory has to
    land again; `clear_compaction_keys` is what re-arms it.
    """
    return _fire_once_under(session_id, key, _COMPACTION_KEY)


# Bytes of a transcript line per token of context it adds. Lines carry JSON framing, so this
# over-counts the pending tail, which errs toward the parent.
_TRANSCRIPT_BYTES_PER_TOKEN = 3
# How long an orchestrator may stay idle: Claude Code compacts an idle one-hour-cache session at 0.9 of
# the TTL (about 54 min, a server flag), so the session watcher's check-in and the longest background
# wait both end at 50 min, before that compaction and well inside the cache.
ORCHESTRATOR_CHECKIN_SECONDS = 3000
# A subagent's prompt cache lives 5 minutes (auto-memory archive_subagent_cache_ttl_measurement.md); 30 s of
# margin for the turn itself. A delegate's foreground wait and a message to a subagent both end inside it.
SUBAGENT_CACHE_WARM_SECONDS = 270
_TRANSCRIPT_TAIL_BYTES = 8_000_000
# A compacting context writes the row that triggers its compaction just before PreCompact fires (measured: 0 s
# at both Workflow-agent compactions), so a subagent transcript last written longer ago than
# this is not the one compacting.
SUBAGENT_LIVE_SECONDS = 15
# A parent mid-turn writes a row every few seconds; one silent this long is waiting, not compacting on its
# own trigger (measured: 130 s and 1,654 s of parent quiet at the two Workflow-agent compactions).
PARENT_QUIET_SECONDS = 30
# A live subagent this many times the parent's pending context marks the compaction as its own even while
# the parent writes, so a parent at its window beside a subagent near the same size stays the parent's.
SUBAGENT_OVER_PARENT = 1.25


def _pending_tokens(transcript_path, sidechain=False):
    """A context as its next call would send it: the input tokens of its last recorded API call, or the
    `postTokens` of a later `compact_boundary`, plus everything written after that row. None when the
    transcript is unreadable or holds neither. `sidechain=True` reads a subagent's own transcript, whose
    rows are all sidechain rows; the parent's count only its own rows."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _TRANSCRIPT_TAIL_BYTES))
            lines = fh.read().splitlines()
    except (OSError, TypeError, ValueError):
        return None
    after = 0
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            after += len(raw)
            continue
        if not isinstance(entry, dict) or bool(entry.get("isSidechain")) != sidechain:
            after += len(raw)
            continue
        post = ((entry.get("compactMetadata") or {}).get("postTokens")
                if entry.get("subtype") == "compact_boundary" else None)
        if isinstance(post, int) and not isinstance(post, bool):
            return post + after // _TRANSCRIPT_BYTES_PER_TOKEN
        usage = (entry.get("message") or {}).get("usage") if entry.get("type") == "assistant" else None
        if isinstance(usage, dict):
            sent = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            return sent + (len(raw) + after) // _TRANSCRIPT_BYTES_PER_TOKEN
        after += len(raw)
    return None


def _live_subagents(transcript_path, now):
    """(last write time, path) of each of the session's subagent transcripts (`<session>/subagents/**/
    agent-*.jsonl` beside the parent's transcript) written in the last SUBAGENT_LIVE_SECONDS."""
    root = os.path.join(os.path.splitext(str(transcript_path))[0], "subagents")
    live = []
    for directory, _, names in os.walk(root):
        for name in names:
            if name.startswith("agent-") and name.endswith(".jsonl"):
                path = os.path.join(directory, name)
                try:
                    written = os.path.getmtime(path)
                except OSError:
                    continue
                if now - written <= SUBAGENT_LIVE_SECONDS:
                    live.append((written, path))
    return live


def is_subagent_payload(payload):
    """True when a PreCompact payload comes from a dispatched subagent, whose `session_id` and
    `transcript_path` are the parent's. Every PreCompact hook calls this and does nothing for a
    subagent: its compaction is not the parent's. A non-empty `agent_id` marks one. The client may send
    none, so an `auto` trigger also marks one when a subagent transcript was written after the parent's,
    within SUBAGENT_LIVE_SECONDS, and either the parent has been quiet PARENT_QUIET_SECONDS or that
    subagent holds over SUBAGENT_OVER_PARENT times the parent's pending context. A compacting context
    writes its trigger row just before PreCompact, and a parent mid-turn keeps writing. The context
    window is not a test: the `autoCompactWindow` setting puts the parent's compaction anywhere from 100k
    to the model's window, a subagent's model may have another, and the status-line gauge measures against
    the model's window. A `manual` trigger (`/compact` acts on the main conversation), no live subagent,
    an unreadable transcript, and any payload that is not an object count as the parent, so an unknown
    keeps the parent's hooks running.
    """
    try:
        if str(payload.get("agent_id") or "").strip():
            return True
        transcript = payload.get("transcript_path")
        if payload.get("trigger") != "auto" or not transcript:
            return False
        now = time.time()
        parent_written = os.path.getmtime(transcript)
        newer = [path for written, path in _live_subagents(transcript, now) if written > parent_written]
        if not newer:
            return False
        if now - parent_written > PARENT_QUIET_SECONDS:
            return True
        parent = _pending_tokens(transcript)
        return parent is not None and any(
            (_pending_tokens(path, sidechain=True) or 0) > parent * SUBAGENT_OVER_PARENT for path in newer)
    except Exception:
        return False


def clear_compaction_keys(session_id):
    """Re-arm every compaction-scoped advisory key, skill markers included, and stamp the clear's time. Called
    from the PreCompact hook. A skill the client re-attaches whole and still current with its `SKILL.md` on disk
    regains its marker from the attachment, which the client writes after this clear (`skill_in_context`); the
    stamp keeps an older attachment from counting. True when a key was cleared."""
    path = state_path(session_id)

    def update(state):
        fired = state.get(_COMPACTION_KEY)
        changed = isinstance(fired, dict) and bool(fired)
        state.pop(_COMPACTION_KEY, None)
        state[_COMPACTED_AT_KEY] = time.time()
        return changed

    written, changed = update_json_locked(path, update)
    return bool(written and changed)


def read_json_salvage(path, wait_s=1.0):
    """Return the dict at `path`. Salvages a torn file; returns {} when unrecoverable. A PermissionError (Windows,
    while another writer renames the file into place) is retried until `wait_s` passes: read as {}, live state such
    as an armed watcher vanished from the check that read it."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            with io.open(path, encoding="utf-8") as fh:
                raw = fh.read()
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                return {}
            time.sleep(0.02)
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


def replace_path(source, target, wait_s=10.0):
    """os.replace that waits out a reader holding the target: Windows refuses a rename onto a path another process
    holds open (WinError 5). Retry until `wait_s` passes, then raise."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def append_jsonl_rotating(path, records, max_bytes=2_000_000, keep_lines=500):
    """Append one JSON line per record under the file's lock; once the file passes `max_bytes`
    (None: never), keep the newest `keep_lines` first (tail rotation via tempfile + rename).
    The lock is load-bearing: Windows emulates append mode with a seek then a write, so two
    unlocked appenders overwrite each other's tail and tear a row. Best-effort: returns False
    on any failure or a lock timeout, never raises — every caller is an observer hook."""
    if not records:
        return True
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    lock = _acquire_json_lock(path)
    if lock is None and _file_lock is not None:
        return False
    try:
        if max_bytes is not None and os.path.exists(path) and os.path.getsize(path) > max_bytes:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-keep_lines:]
            fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".jsonl", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(tail)
            replace_path(tmp_path, path)
        with io.open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError):
        return False
    finally:
        _release_json_lock(lock)


def _acquire_json_lock(path):
    if _file_lock is None:
        return None
    return _file_lock.acquire(path + ".lock", _LOCK_WAIT_SECONDS)


def _release_json_lock(handle):
    if _file_lock is not None:
        _file_lock.release(handle)


def _dump(obj, fh, pretty):
    if pretty:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    else:
        json.dump(obj, fh, ensure_ascii=True)


def _write_json_atomic_unlocked(path, obj, raising=False, pretty=False):
    directory = os.path.dirname(path) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            _dump(obj, fh, pretty)
        replace_path(tmp_path, path)
        return True
    except (OSError, ValueError, TypeError):
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if raising:
            raise
        return False


def _locked_or_raise(path, raising):
    lock_path = _acquire_json_lock(path)
    if not lock_path and raising:
        if _file_lock is None:
            raise RuntimeError("hooks/_file_lock.py did not load; %s cannot be locked" % path)
        raise TimeoutError("%s.lock held past %ss" % (path, _LOCK_WAIT_SECONDS))
    return lock_path


def update_json_locked(path, updater, raising=False, pretty=False, reader=None):
    """Run `updater(state)` under a per-file lock and atomically save its changes.

    Returns `(written, result)`. Failures return `(False, None)` and never raise, unless `raising`: then a lock
    timeout raises TimeoutError, and a write or updater error propagates. `pretty` is `write_json_atomic`'s format
    knob, for an authored file a reader opens: indent 2, non-ASCII kept, trailing newline. Without it the compact
    default replaces the file's own format on the first locked write. `reader(path)` supplies a strict
    transactional read when given; hooks keep the default salvage read. A reader error propagates without a write.
    """
    lock_path = _locked_or_raise(path, raising)
    if not lock_path:
        return False, None
    try:
        state = (reader or read_json_salvage)(path)
        try:
            result = updater(state)
        except Exception:
            if raising:
                raise
            return False, None
        if not _write_json_atomic_unlocked(path, state, raising, pretty):
            return False, None
        return True, result
    finally:
        _release_json_lock(lock_path)


def write_json_atomic(path, obj, raising=False, pretty=False):
    """Write `obj` to `path` via same-dir tempfile + rename. True on success.

    `raising` turns a lock timeout into TimeoutError and a write failure into its OSError. `pretty` is the record
    format: indent 2, non-ASCII kept, trailing newline.
    """
    lock_path = _locked_or_raise(path, raising)
    if not lock_path:
        return False
    try:
        return _write_json_atomic_unlocked(path, obj, raising, pretty)
    finally:
        _release_json_lock(lock_path)
