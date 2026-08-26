#!/usr/bin/env python3
"""Shared queued-gate result surfacing — the single owner of "has this session
been told about that queued run yet?".

WHY A MODULE AND NOT TWO COPIES
  A run handed to the gate queue finishes in a detached watcher process that has
  no channel back to any session. The result file is therefore the ONLY evidence
  it happened, and a rule that decides who has seen it must be identical in every
  reader or the run is announced twice, or not at all. Two readers exist:
    - session_context_loader.py  (SessionStart) — catches runs finished between sessions
    - activity_registry.py       (UserPromptSubmit) — catches runs finished MID-session,
      which nothing surfaced before this module existed

PER-SESSION MARKER, NOT A SHARED ONE
  The retired mechanism was a single <queueDir>/.last_surfaced timestamp. With two
  sessions on one checkout — the normal case here — whichever one read it first
  advanced it, and the other never learned its own queued gate had finished. Seen
  state is a property of a READER, so it lives per session in tempdir and every
  session independently learns about every result exactly once.

  Consequence worth stating: a fresh session surfaces anything inside RECENT_SEC it
  has not personally seen. That is the intended bias — a duplicate announcement
  costs a line, a dropped one costs a commit gated on a verdict nobody read.

INLINE RECORDS ARE FILTERED OUT
  Complete-Gate writes a result record for EVERY run, inline runs included. Only the
  queued-finish signal has no other channel; announcing inline records would relabel
  a session's own foreground runs as queued news.
"""

import json
import os
import tempfile
import time

# A result older than this is history, not news — the watcher keeps 20 runs, so
# without a floor a fresh session would announce week-old verdicts.
RECENT_SEC = 24 * 3600
MAX_LINES = 3
# Bounded so the marker cannot grow without limit across a long-lived session.
MAX_SEEN = 200


def _marker_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return os.path.join(tempfile.gettempdir(), f"cc-gatequeue-{safe or 'unknown'}.json")


def _prune_stale(keep_days=7):
    cutoff = time.time() - keep_days * 86400
    try:
        tmp = tempfile.gettempdir()
        for name in os.listdir(tmp):
            if not (name.startswith("cc-gatequeue-") and name.endswith(".json")):
                continue
            full = os.path.join(tmp, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass
    except Exception:
        pass


def surface_lines(repo_root, session_id):
    """Return one line per queued-gate result this session has not been shown yet,
    and record them as seen. Never raises: surfacing is advisory telemetry."""
    try:
        queue_dir = os.path.join(str(repo_root), ".claude", "scratch", "gate_queue")
        if not os.path.isdir(queue_dir):
            return []

        mpath = _marker_path(session_id)
        if not os.path.exists(mpath):
            _prune_stale()
        try:
            with open(mpath, encoding="utf-8") as fh:
                seen = list(json.load(fh).get("seen", []))
        except Exception:
            seen = []
        seen_set = set(seen)

        cutoff = time.time() - RECENT_SEC
        candidates = []
        for name in os.listdir(queue_dir):
            if not name.endswith(".result.json"):
                continue
            full = os.path.join(queue_dir, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    continue
                result = json.loads(open(full, encoding="utf-8").read())
            except Exception:
                continue
            if result.get("source") == "inline":
                continue
            rid = result.get("id") or name[: -len(".result.json")]
            if rid in seen_set:
                continue
            candidates.append((os.path.getmtime(full), name, rid, result))

        if not candidates:
            return []

        candidates.sort(reverse=True)
        # Everything examined is marked seen, not just what fits under MAX_LINES —
        # otherwise an overflow result re-announces on every turn forever. The
        # overflowed ids stay readable via `/regression_gate --queue-status`.
        fresh = list(dict.fromkeys(rid for _, _, rid, _ in candidates))
        lines = []
        for _, name, rid, result in candidates[:MAX_LINES]:
            lines.append(
                f"Queued gate result: {rid} {result.get('status', 'unknown')} "
                f"exit={result.get('exitCode', '?')} treeDelta={result.get('treeDelta', '?')} "
                f"— read .claude/scratch/gate_queue/{name}; treeDelta=true means the tree "
                'changed since the run and it cannot back a "Verified" claim.'
            )
        try:
            merged = (seen + fresh)[-MAX_SEEN:]
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump({"seen": merged}, fh)
        except Exception:
            pass

        return lines
    except Exception:
        return []
