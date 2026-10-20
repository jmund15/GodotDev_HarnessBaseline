#!/usr/bin/env python3
"""Compaction-scoped ladder markers, and the doctrine check both dispatch hooks deny on.

dispatch_mechanism_guard (Workflow) and sidecar_dispatch_context (sidecar launches) deny a dispatch until the
orchestration skill and the ladder are in this context: read in full, or printed as the per-tier slice
(`model_registry.py ladder-slice <tier>...`) for every tier the call implies. Class: process guard, blocking
because the advisory that replaced it let the skip recur (instruction_quality §3)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import LADDER_SLICE_KEY, ladder_slice_tiers, state_path, update_json_locked, skill_in_context  # noqa: E402

STATE_KEY = "model_ladder_ready"
SLICE_COMMAND = "python3 .claude/tools/model_registry.py ladder-slice"
RELATIVE_PATH = Path(".claude") / "reference" / "model_ladder_evidence.md"


def _root(payload):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()


def expected_path(payload):
    """The absolute ladder path a Read must hit; denials print it so a worktree cwd cannot misresolve it."""
    return os.path.realpath(_root(payload if isinstance(payload, dict) else {}) / RELATIVE_PATH)


def _full_read_succeeded(payload):
    covered = False
    for key in ('tool_response', 'tool_result'):
        response = payload.get(key)
        if not isinstance(response, dict):
            continue
        if response.get('is_error'):
            return False
        file = response.get('file') or {}
        if isinstance(file, dict):
            start, count, total = (file.get(k) for k in ('startLine', 'numLines', 'totalLines'))
            if all(type(n) is int for n in (start, count, total)):
                if start != 1 or total <= 0 or count < total:
                    return False
                covered = True
    ti = payload.get('tool_input') or {}
    bounded = ti.get('offset') is not None or ti.get('limit') is not None
    return covered or not bounded


def mark_loaded(payload):
    """Arm readiness after a successful exact-path Read covering the whole ladder."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not _full_read_succeeded(payload):
        return False
    raw_path = tool_input.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return False
    root = _root(payload)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        actual = os.path.normcase(os.path.realpath(candidate))
        expected = os.path.normcase(expected_path(payload))
    except OSError:
        return False
    if actual != expected:
        return False

    def update(state):
        state[STATE_KEY] = True
        return True

    written, marked = update_json_locked(state_path(payload.get("session_id") or ""), update)
    return bool(written and marked)


FAULT_NOTE = ("Dispatch doctrine check could not read its session state, so this call is allowed without it. "
              "Repair hooks/model_ladder_gate.py if this repeats.")


# The load steps the deny names and the post-compaction rule (`doctrine_rule`) states: one string per step.
SKILL_STEP = 'invoke Skill(skill="orchestration") (CLAUDE.core.md §9)'
SLICE_STEP = 'print the ladder slice for {which}: `' + SLICE_COMMAND + ' {tiers}` (orchestration §5)'
READ_STEP = 'Read `{path}` in full, no offset or limit (orchestration §5)'


def doctrine_rule(payload):
    """The whole dispatch-doctrine demand `doctrine_check` applies, for a line printed before any dispatch."""
    return ('Before the next dispatch, the dispatch doctrine must be in this context: the orchestration skill (a '
            'copy re-attached whole after the compaction counts), else ' + SKILL_STEP + '; and, since each '
            'compaction clears the ladder, for a call whose jobs pin tiers, '
            + SLICE_STEP.format(which='each tier it pins', tiers='<tier>...')
            + '; for a call naming no tier, ' + READ_STEP.format(path=expected_path(payload)) + '.')


def _call_tiers(tiers):
    """The sorted tiers `tiers()` names, or None when the call's tiers are unknown (no callable, an error, or no tier)."""
    try:
        named = {t for t in (tiers() if callable(tiers) else None) or () if isinstance(t, str) and t}
    except Exception:
        return None
    return sorted(named) or None


def doctrine_check(payload, tiers=None):
    """(deny reason, fault note) for a pinned dispatch. The reason names each doctrine not in this context, by its
    one load route: the orchestration skill (CLAUDE.core.md §9) and the ladder (orchestration §5). The step after
    the load is to check the call against them and re-issue it, because they exist to change the call. Both are
    observed state, never the route or pin they inform. A compaction clears the ladder, and the skill unless the
    client re-attached it whole (`_hook_state.skill_in_context` reads that attachment from the payload's transcript). A full SKILL.md Read still marks the skill loaded (skill_load_marker.py) for a session without the Skill
    tool; the deny names only the Skill call. State the lock cannot write fails open with the fault note
    (instruction_quality §16): no load could be recorded.

    The ladder is met by a full Read or by `model_registry.py ladder-slice` for every tier the call implies.
    `tiers` is a zero-argument callable returning those canonical tiers, evaluated only when the full Read is
    absent. When it names none (absent, failing or empty) the call's tiers are unknown, so only the full Read meets
    the ladder; with tiers known, the deny names the slice command for the uncovered ones."""
    session_id = payload.get('session_id') or ''
    written, state = update_json_locked(state_path(session_id), lambda state: (
        state.get(STATE_KEY) is True, state.get(LADDER_SLICE_KEY)))
    if not written:
        return None, FAULT_NOTE
    ladder_ready, covered = state
    covered = set(covered) if isinstance(covered, list) else set()
    unmet = []
    if not skill_in_context(session_id, 'orchestration', transcript_path=payload.get('transcript_path')):
        unmet.append(SKILL_STEP)
    if not ladder_ready:
        needed = _call_tiers(tiers)
        if needed is None:
            unmet.append(READ_STEP.format(path=expected_path(payload)))
        else:
            missing = [t for t in needed if t not in covered]
            if missing:
                unmet.append(SLICE_STEP.format(
                    which='the tier this call pins' if len(missing) == 1 else 'the tiers this call pins',
                    tiers=' '.join(missing)))
    if not unmet:
        return None, None
    return ('Dispatch DENIED: the dispatch doctrine is not in context since the session started or last compacted. '
            'Load it: ' + '; '.join(unmet) + '. Then check this call against it (route, jobs, '
            'models, efforts, reading) and re-issue it with any change it calls for.'), None


def tier_in_context(session_id, tier):
    """True while the ladder rows claiming `tier` are in this context: a full Read, or a slice that named the tier.
    Unreadable state reads as not in context."""
    return claim_loaded(session_id) or tier in ladder_slice_tiers(session_id)


def claim_loaded(session_id):
    """True while the ladder is in context; compaction clears it. Unreadable state reads as not loaded."""
    def update(state):
        return state.get(STATE_KEY) is True

    written, ready = update_json_locked(state_path(session_id or ""), update)
    return bool(written and ready)
