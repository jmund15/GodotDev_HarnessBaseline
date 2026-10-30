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
tracks whether the model still has the text.

Fail posture: every function is best-effort and total. A read never raises — it returns
`{}` when there is nothing recoverable. A write never raises — it returns False. State
that cannot be written must degrade to a missing nudge, never to a crashed hook.
"""

import fnmatch
import importlib.util
import io
import json
import os
import re
import tempfile
import time

__all__ = [
    "HARNESS_DIRS",
    "CODE_EXT",
    "SETTINGS_FILES",
    "is_settings_file",
    "is_harness_code",
    "in_harness_dirs",
    "SETUP_SURFACE_FILES",
    "read_json_salvage",
    "write_json_atomic",
    "update_json_locked",
    "state_path",
    "fire_once",
    "fire_once_machine",
    "fire_once_since_compaction",
    "mark_ladder_slice",
    "ladder_slice_tiers",
    "clear_compaction_keys",
    "is_subagent_payload",
    "log_path",
    "append_jsonl_rotating",
]

# The harness code set: what `scripts/harness_tests.py` hashes into the stamp and what
# `hooks/git_guardrails.py`'s skill advisory counts as code. One home so the two never disagree.
HARNESS_DIRS = (
    ".claude/hooks",
    ".claude/tools",
    ".claude/scripts",
    ".claude/workflows",
    ".claude/tests",
    ".claude/settings.json",
    ".claude/settings.*.json",
)

# Which harness paths are code (a script or a settings file): the proof runner's binding and the fold
# checker's untested-code rule read this one predicate. A settings file is `settings.json` or any
# `settings.<name>.json` (base, project, a layer overlay) except the user's gitignored `settings.local.json`.
CODE_EXT = (".py", ".ps1", ".sh", ".js", ".mjs")
_SETTINGS_RE = re.compile(r"\.claude/settings(?:\.(?!local\.json$)[\w-]+)?\.json")


def is_settings_file(rel):
    return _SETTINGS_RE.fullmatch(rel.replace(os.sep, "/")) is not None


def _settings_files():
    claude_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        names = sorted(os.listdir(claude_dir))
    except OSError:
        return ()
    return tuple(".claude/" + n for n in names if is_settings_file(".claude/" + n))


SETTINGS_FILES = _settings_files()


def is_harness_code(rel):
    rel = rel.replace(os.sep, "/")
    return is_settings_file(rel) or rel.lower().endswith(CODE_EXT)


def in_harness_dirs(rel):
    """True when `rel` is a `HARNESS_DIRS` entry or sits under one; an entry may be a glob, as git reads it."""
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatchcase(rel, d) or rel.startswith(d + "/") for d in HARNESS_DIRS)


# Files outside HARNESS_DIRS whose change must still run their bound proofs (pins, and a cloud script
# that copies them). The list is project data, `reference/setup_surfaces.txt`, so this shared module
# names no project file; absent file -> no surfaces. Read by `scripts/harness_tests.py` (staging and
# the stamped tree) and `hooks/git_guardrails.py` (the stamp requirement).
def _setup_surfaces():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "reference", "setup_surfaces.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            return tuple(line.strip() for line in fh if line.strip() and not line.startswith("#"))
    except OSError:
        return ()


SETUP_SURFACE_FILES = _setup_surfaces()

# One state file per session, shared by every hook. HARNESS_HOOK_STATE_DIR redirects it so a
# test never writes the real one.
_DEFAULT_STATE_DIR = os.path.expanduser("~/.claude/.routing_state")

_ONCE_KEY = "fired_once"
_COMPACTION_KEY = "fired_since_compaction"
# The client cuts a re-attached skill body at this many characters (`tools/load_census.py` reports bodies over it).
SKILL_REATTACH_CHARS = 20_000
_COMPACTED_AT_KEY = "compacted_at"
LADDER_SLICE_KEY = "ladder_slice_tiers"
BRIEF_TABLE_KEY = "brief_table_fits"
_COMPACTION_CLEAR_KEYS = (_COMPACTION_KEY, "nudge_targets_seen", "model_ladder_ready", LADDER_SLICE_KEY,
                          BRIEF_TABLE_KEY)
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
    return bool(first) if written else on_failure


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


def _agent_suffix(agent_id):
    """A subagent shares its parent's session_id; its payload `agent_id` keeps its context markers its own."""
    agent_id = str(agent_id or "").strip()
    return "@" + agent_id if agent_id else ""


def mark_skill_in_context(session_id, name, agent_id=""):
    """Record that skill `name` is in this context window (the subagent's when `agent_id` is given); a
    compaction clears it."""
    fire_once_since_compaction(session_id, "skill_in_context:" + name + _agent_suffix(agent_id))


def skill_in_context(session_id, name, agent_id="", transcript_path=None):
    """True when skill `name` was loaded since the session started or last compacted, in the subagent's own
    context when `agent_id` is given. With the main thread's `transcript_path`, a skill the client re-attached
    whole after the latest compaction counts too (`_compaction_record.reattached_whole`); the first such read
    marks every whole skill of that attachment. An attachment not yet on disk, or an unreadable transcript,
    counts as not re-attached."""
    key = "skill_in_context:" + name + _agent_suffix(agent_id)
    state = read_json_salvage(state_path(session_id))
    fired = state.get(_COMPACTION_KEY)
    if isinstance(fired, dict) and fired.get(key):
        return True
    if agent_id or not transcript_path:
        return False
    try:
        import _compaction_record
        whole = _compaction_record.reattached_whole(transcript_path, state.get(_COMPACTED_AT_KEY))
    except Exception:
        return False
    if not whole:
        return False
    for skill in whole:
        mark_skill_in_context(session_id, skill)
    return name in whole


def mark_ladder_slice(session_id, tiers):
    """Record that `model_registry.py ladder-slice` printed these tiers into this context window; a compaction
    clears them. Accumulates across runs. True when the record was written."""
    def update(state):
        have = state.get(LADDER_SLICE_KEY)
        state[LADDER_SLICE_KEY] = sorted({*(have if isinstance(have, list) else []), *tiers})
        return True

    written, _ = update_json_locked(state_path(session_id), update)
    return bool(written)


def ladder_slice_tiers(session_id):
    """The tiers whose ladder slice is in this context window, else []. Reads only."""
    have = read_json_salvage(state_path(session_id)).get(LADDER_SLICE_KEY)
    return sorted(t for t in have if isinstance(t, str)) if isinstance(have, list) else []


def brief_table_seen(session_id, tier, fits):
    """The fit snapshot `{row id: fit verdict}` stored for `tier` when its full table printed in this context
    window, or None when none is stored: the caller then prints the full table, and `fits` is stored with it.
    A compaction clears the record, so the table prints again. One locked read-and-set, fail-open to None."""
    def update(state):
        tables = state.get(BRIEF_TABLE_KEY)
        if not isinstance(tables, dict):
            tables = {}
        earlier = tables.get(tier)
        if not isinstance(earlier, dict):
            tables[tier] = fits
            state[BRIEF_TABLE_KEY] = tables
            return None
        return earlier

    written, earlier = update_json_locked(state_path(session_id), update)
    return earlier if written else None


def fire_once_since_compaction(session_id, key):
    """True on the first ask for `key` since the session started or last compacted.

    A compacted session lost the earlier emission from its context, so the advisory has to
    land again; `clear_compaction_keys` is what re-arms it.
    """
    return _fire_once_under(session_id, key, _COMPACTION_KEY)


# The runtime auto-compacts a context only near the end of its window (1M at 700k, 200k near 167k),
# so an `auto` PreCompact while the parent sits under half its window cannot be the parent's. A set
# `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` moves that end, and the floor stays at half of it.
PARENT_AUTOCOMPACT_FLOOR_PCT = 50
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


def _autocompact_floor_pct():
    try:
        override = float(os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or "")
    except ValueError:
        return PARENT_AUTOCOMPACT_FLOOR_PCT
    return min(PARENT_AUTOCOMPACT_FLOOR_PCT, override / 2) if override > 0 else PARENT_AUTOCOMPACT_FLOOR_PCT


def _parent_window_tokens(session_id):
    """The parent's context window in tokens, from the status line's `cc-cachestat-<sid>.json`:
    its `last` call's cache-write:cache-read:input tokens over that call's `context_pct`. None when
    the file is absent, unreadable or lacks either field. Subagents never write this file."""
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    path = os.path.join(os.environ.get("CC_CACHESTAT_DIR") or tempfile.gettempdir(),
                        "cc-cachestat-%s.json" % (safe or "unknown"))
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        pct = state.get("context_pct")
        tokens = sum(int(part) for part in str(state["last"]).split(":"))
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return None
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or pct <= 0 or tokens <= 0:
        return None
    return tokens * 100 / pct


def _parent_pending_tokens(transcript_path):
    """The parent's context as its next call would send it: the input tokens of its last recorded
    API call plus everything written after that call. None when the transcript is unreadable or
    holds no call. Subagent turns live in their own transcripts, so none of theirs count."""
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
        usage = ((entry.get("message") or {}).get("usage") if isinstance(entry, dict)
                 and entry.get("type") == "assistant" and not entry.get("isSidechain") else None)
        if isinstance(usage, dict):
            sent = sum(int(usage.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            return sent + (len(raw) + after) // _TRANSCRIPT_BYTES_PER_TOKEN
        after += len(raw)
    return None


def is_subagent_payload(payload):
    """True when a PreCompact payload comes from a dispatched subagent, whose `session_id` and
    `transcript_path` are the parent's. Every PreCompact hook calls this and does nothing for a
    subagent: its compaction is not the parent's. A non-empty `agent_id` marks one; the client may
    send none, so an `auto` trigger while the parent's pending context sits under the floor of its
    window marks one too. A `manual` trigger, an unreadable gauge or transcript, and any payload that
    is not an object count as the parent, so an unknown keeps the hooks running.
    """
    try:
        if str(payload.get("agent_id") or "").strip():
            return True
        if payload.get("trigger") != "auto":
            return False
        window = _parent_window_tokens(payload.get("session_id"))
        pending = _parent_pending_tokens(payload.get("transcript_path"))
        if window is None or pending is None:
            return False
        return pending * 100 / window < _autocompact_floor_pct()
    except Exception:
        return False


def clear_compaction_keys(session_id):
    """Re-arm every compaction-scoped advisory key, skill markers included, and stamp the clear's time. Called
    from the PreCompact hook. A skill the client re-attaches whole regains its marker from the attachment, which
    the client writes after this clear (`skill_in_context`); the stamp keeps an older attachment from counting.
    True when a key was cleared."""
    path = state_path(session_id)

    def update(state):
        fired = state.get(_COMPACTION_KEY)
        changed = any(key in state for key in _COMPACTION_CLEAR_KEYS if key != _COMPACTION_KEY)
        changed = changed or (isinstance(fired, dict) and bool(fired))
        for key in _COMPACTION_CLEAR_KEYS:
            state.pop(key, None)
        state[_COMPACTED_AT_KEY] = time.time()
        return changed

    written, changed = update_json_locked(path, update)
    return bool(written and changed)


def read_json_salvage(path):
    """Return the dict at `path`. Salvages a torn file; returns {} when unrecoverable."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
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
            replace_path(tmp_path, path)
        with io.open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError):
        return False


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
