#!/usr/bin/env python3
"""Compaction-scoped ladder markers, and the doctrine check both dispatch hooks deny on.

dispatch_mechanism_guard (Workflow) and sidecar_dispatch_context (sidecar launches) deny a dispatch until its
observed governing skills, orchestration and the ladder are in this context. skill_dispatch_gate derives the
skills from the actual reading and project artifact ownership. The ladder is read in full, or printed as the per-tier slice
(`model_registry.py for-role <tier>...`) for every tier the call implies; a deny that reaches the session
carries the slice of the missing tiers itself and records them, and names each missing tier with its dispatch
consumer. A slice credits only when its stdout reached context untouched: the Bash PostToolUse entry
(`--bash-credit`) compares the delivered text to the hash the run staged. Class: process guard, blocking
because the advisory that replaced it let the skip recur (instruction_quality §3)."""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import (LADDER_SLICE_KEY, ladder_slice_tiers, mark_ladder_slice, state_path,  # noqa: E402
                         update_json_locked, skill_in_context, consume_ladder_receipt, fail_ladder_receipt,
                         ladder_receipt_failure, normalize_stdout, stdout_sha256)

STATE_KEY = "model_ladder_ready"
SLICE_COMMAND = "python3 .claude/tools/model_registry.py for-role"
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
    """Arm readiness from a delivery that reached context: a successful exact-path Read covering the whole
    ladder, or a `for-role` run whose delivered stdout is the text it staged. A Bash result that was piped,
    filtered, redirected, persisted, interrupted or failed credits nothing, and records why for the next deny."""
    if not isinstance(payload, dict):
        return False
    if payload.get("tool_name") == "Bash":
        return _credit_delivered_slice(payload)
    if payload.get("tool_name") != "Read":
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


_INTERPRETERS = ("python", "python3", "py", "py.exe", "python.exe", "python3.exe")
_MODEL_REGISTRY = re.compile(r"(?:^|[/\\])model_registry\.py$")
_EXIT_KEYS = ("exitCode", "exit_code", "code")


def _invokes_for_role(command):
    """True when a simple command in `command` runs `model_registry.py for-role`: a mention inside a quoted
    argument (`echo "python3 .../model_registry.py for-role"`) is not an invocation, so it cannot spend a
    receipt a real run staged."""
    for part in re.split(r"(?:&&|\|\||[;|&\n])", str(command or "")):
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            tokens = part.split()
        for index in range(len(tokens) - 2):
            if (os.path.basename(tokens[index]).lower() in _INTERPRETERS
                    and _MODEL_REGISTRY.search(tokens[index + 1]) and tokens[index + 2] == "for-role"):
                return True
    return False


def _delivery_failure(response, receipt):
    """The reason this Bash result did not deliver the staged text, or None when it did."""
    if not isinstance(response, dict):
        return "the call returned no result object"
    if response.get("interrupted"):
        return "the call was interrupted"
    if response.get("persistedOutputPath"):
        return "the client persisted the output (persistedOutputPath) instead of delivering it"
    code = next((response[key] for key in _EXIT_KEYS if key in response), None)
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return "for-role exited %d" % code
    if response.get("isTruncated") or response.get("truncated"):
        return "the delivered output was truncated"
    delivered = normalize_stdout(response.get("stdout"))
    if not delivered.strip():
        return "the run delivered no output"
    if stdout_sha256(delivered) != receipt.get("sha256"):
        return "the delivered text differs from the run's own output (piped, filtered or redirected)"
    return None


def _credit_delivered_slice(payload):
    """Credit the tiers a staged `for-role` receipt names, only for the stdout that reached context."""
    session_id = payload.get("session_id") or ""
    if not session_id or not _invokes_for_role((payload.get("tool_input") or {}).get("command")):
        return False
    receipt = consume_ladder_receipt(session_id)
    if not receipt:
        return False
    reason = _delivery_failure(payload.get("tool_response"), receipt)
    if reason:
        fail_ladder_receipt(session_id, reason)
        return False
    tiers = [tier for tier in receipt.get("tiers") or [] if isinstance(tier, str) and tier]
    return bool(tiers and mark_ladder_slice(session_id, tiers, clear_receipt_failure=True))


FAULT_NOTE = ("Dispatch doctrine check could not read its session state, so this call is allowed without it. "
              "Repair hooks/model_ladder_gate.py if this repeats.")


# The load steps the deny names and the post-compaction rule (`doctrine_rule`) states: one string per step.
SKILL_STEP = 'invoke Skill(skill="orchestration") (orchestration §0)'
SLICE_STEP = 'print the doctrine for {which}: `' + SLICE_COMMAND + ' {tiers}` (orchestration §5)'
SLICE_BELOW_STEP = 'read the ladder slice for {which} below (orchestration §5)'
READ_STEP = 'Read `{path}` in full, no offset or limit (orchestration §5)'


def _tier_phrase(tier, consumers):
    """`executor (fold)`: the missing tier and the call's labels that pin it, when the caller named them."""
    labels = consumers.get(tier) or []
    return "%s (%s)" % (tier, ", ".join(labels)) if labels else tier


def doctrine_rule(payload):
    """The whole dispatch-doctrine demand `doctrine_check` applies, for a line printed before any dispatch."""
    return ('Before the next dispatch, load each skill governing its brief and artifacts in this context '
            '(orchestration §0 Governing skills). The dispatch doctrine must also be in this context: the orchestration skill (a '
            'copy re-attached whole and still current with its SKILL.md on disk after the compaction counts), else '
            + SKILL_STEP + '; and, since each '
            'compaction clears the ladder, for a call whose jobs pin tiers, '
            + SLICE_STEP.format(which='each tier it pins', tiers='<tier>...')
            + '; for a call naming no tier, ' + READ_STEP.format(path=expected_path(payload)) + '.')


def _call_tiers(tiers):
    """(sorted tiers, {tier: [consumer label]}) from `tiers()`, which returns tiers or a {tier: labels} map; None when
    the call's tiers are unknown (no callable, an error, or no tier)."""
    try:
        got = tiers() if callable(tiers) else None
        consumers = ({t: [str(label) for label in labels or ()] for t, labels in got.items()}
                     if isinstance(got, dict) else {})
        named = {t for t in got or () if isinstance(t, str) and t}
    except Exception:
        return None
    return (sorted(named), consumers) if named else None


def _embedded_slice(session_id, missing):
    """The ladder slice for `missing`, recorded as loaded for the session, else None when it cannot be produced
    or recorded (the deny then names the command and records nothing)."""
    try:
        tools = str(Path(__file__).resolve().parents[1] / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import model_registry
        text = model_registry.ladder_slice(missing)
    except Exception:
        return None
    return text if mark_ladder_slice(session_id, missing) else None


def doctrine_check(payload, tiers=None, embed_slice=False):
    """(deny reason, fault note) for a pinned dispatch. The reason names each doctrine not in this context, by its
    one load route: the orchestration skill (orchestration §0) and the ladder (orchestration §5). The step after
    the load is to check the call against them and re-issue it, because they exist to change the call. Both are
    observed state, never the route or pin they inform. A compaction clears the ladder, and the skill unless the
    client re-attached it whole (`_hook_state.skill_in_context` reads that attachment from the payload's transcript). A full SKILL.md Read still marks the skill loaded (skill_load_marker.py) for a session without the Skill
    tool; the deny names only the Skill call. State the lock cannot write fails open with the fault note
    (instruction_quality §16): no load could be recorded.

    The ladder is met by a full Read or by `model_registry.py for-role` for every tier the call implies.
    `tiers` is a zero-argument callable returning those canonical tiers, evaluated only when the full Read is
    absent. When it names none (absent, failing or empty) the call's tiers are unknown, so only the full Read meets
    the ladder; with tiers known and `embed_slice` True, the deny ends with the slice of the uncovered ones and
    records them, so the re-issued call passes. Only a caller whose deny text reaches the session's context passes
    True: the record is a side effect, and a slice recorded but never delivered lets the next call pass without the
    doctrine in context. By default, and when the slice cannot be produced, the deny names the slice command and
    records nothing."""
    session_id = payload.get('session_id') or ''
    written, state = update_json_locked(state_path(session_id), lambda state: (
        state.get(STATE_KEY) is True, state.get(LADDER_SLICE_KEY)))
    if not written:
        return None, FAULT_NOTE
    ladder_ready, covered = state
    covered = set(covered) if isinstance(covered, list) else set()
    unmet, slice_text = [], None
    import skill_dispatch_gate
    required, faults = skill_dispatch_gate.required_skills(payload)
    for name in sorted(set(required) | {'orchestration'}):
        if not skill_in_context(session_id, name, agent_id=payload.get('agent_id') or '',
                                transcript_path=payload.get('transcript_path'), root=str(_root(payload))):
            unmet.append(SKILL_STEP if name == 'orchestration' else
                         f'invoke Skill(skill="{name}") (orchestration §0 Governing skills)')
    fault = ('Governing-skill check incomplete: ' + '; '.join(faults)
             + '. Repair the named reading or hooks/skill_dispatch_gate.py before relying on this check.') if faults else None
    if not ladder_ready:
        needed = _call_tiers(tiers)
        if needed is None:
            unmet.append(READ_STEP.format(path=expected_path(payload)))
        else:
            needed, consumers = needed
            missing = [t for t in needed if t not in covered]
            if missing:
                named = ', '.join(_tier_phrase(tier, consumers) for tier in missing)
                slice_text = _embedded_slice(session_id, missing) if embed_slice else None
                unmet.append(SLICE_BELOW_STEP.format(which=named) if slice_text
                             else SLICE_STEP.format(which=named, tiers=' '.join(missing)))
    if not unmet:
        return None, fault
    reason = ('Dispatch DENIED: the dispatch doctrine is not in context since the session started or last compacted. '
              'Load it: ' + '; '.join(unmet) + '. Then check this call against it (route, jobs, '
              'models, efforts, reading) and re-issue it with any change it calls for.')
    failure = ladder_receipt_failure(session_id) if session_id else None
    if failure:
        reason += ' The last for-role run did not reach context: %s.' % failure
    if slice_text:
        reason += '\n\n' + slice_text
    if fault:
        reason += '\n' + fault
    return reason, fault


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


def main(argv=None):
    """PostToolUse entry: `--bash-credit` credits the stdout a `for-role` run delivered, and prints nothing.
    Every other invocation does nothing, so the narrow entry never becomes a second dispatcher."""
    args = sys.argv[1:] if argv is None else list(argv)
    if "--bash-credit" not in args:
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    mark_loaded(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
