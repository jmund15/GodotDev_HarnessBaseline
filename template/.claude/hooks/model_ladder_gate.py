#!/usr/bin/env python3
"""Compaction-scoped ladder markers, and the doctrine check both dispatch hooks deny on.

dispatch_mechanism_guard (Workflow) and sidecar_dispatch_context (sidecar launches) deny a dispatch until the
orchestration skill and the ladder are in this context. Which other skills a brief's job follows is the briefing
session's judgment (orchestration §0 Governing skills); no reading of the brief decides it. The ladder is read in full, or printed as the per-tier slice
(`model_registry.py for-role <tier>...`) for every tier the call implies; a deny that reaches the session
carries the whole `for-role` text for every tier the registry claims and records them all in one write, and
names each missing tier with its dispatch consumer. A slice credits when the run's own stdout appears whole
inside the delivered output: the Bash PostToolUse entry (`--bash-credit`) compares against the text the run
staged, so a command joined with `&&` earns credit and one cut by a pipe, filter or redirect earns none.
Each recorded load carries the digest of the text it delivered (the skill's body, the ladder file), so a session
holding a copy the file has since left behind is denied until it reloads (`held_ladder`, `_hook_state.skill_in_context`).
Class: process guard, blocking because the advisory that replaced it let the skip recur (instruction_quality §3)."""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import (LADDER_DIGEST_KEY, fire_once_since_compaction, LADDER_READY_KEY, LADDER_SLICE_KEY, adopt_ladder_digest,  # noqa: E402
                         mark_ladder_slice, read_json_salvage, state_path, update_json_locked, skill_in_context, consume_ladder_receipt, fail_ladder_receipt,
                         ladder_receipt_failure, normalize_stdout, stdout_sha256)

STATE_KEY = LADDER_READY_KEY
SLICE_COMMAND = "python3 .claude/tools/model_registry.py for-role"
RELATIVE_PATH = Path(".claude") / "reference" / "model_ladder_evidence.md"
NOTICE_TAG = "[dispatch-prep]"
NOTICE_ONCE = "model_ladder_gate:advance_notice"


def _root(payload):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()


def expected_path(payload):
    """The absolute ladder path a Read must hit; denials print it so a worktree cwd cannot misresolve it."""
    return os.path.realpath(_root(payload if isinstance(payload, dict) else {}) / RELATIVE_PATH)


def ladder_digest(payload):
    """The digest of the ladder file the session's next Read would deliver (line endings normalized), else None
    when there is none: a load records it, and a session whose recorded digest differs holds older text."""
    try:
        with open(expected_path(payload if isinstance(payload, dict) else {}), "rb") as fh:
            return stdout_sha256(fh.read().decode("utf-8", errors="replace"))
    except OSError:
        return None


def held_ladder(state, payload=None):
    """(full ladder Read held, tiers a slice holds) from a session `state` dict, for the ladder file as it is now: a
    record made against other text (the digest it carries differs) holds nothing, so the session is denied until it
    reloads. A record with no digest cannot be told stale and stands."""
    ready, tiers = state.get(STATE_KEY) is True, state.get(LADDER_SLICE_KEY)
    tiers = {t for t in tiers if isinstance(t, str)} if isinstance(tiers, list) else set()
    recorded = state.get(LADDER_DIGEST_KEY)
    if recorded and recorded != ladder_digest(payload):
        return False, set()
    return ready, tiers


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
    ladder, or a `for-role` run whose staged stdout appears whole inside the delivered output. A Bash result
    that was piped, filtered, redirected, persisted, interrupted or failed credits nothing, and records why for
    the next deny."""
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

    digest = ladder_digest(payload)

    def update(state):
        adopt_ladder_digest(state, digest)
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
    run_text = normalize_stdout(receipt.get("text"))
    if run_text:
        if run_text not in delivered:
            return "the run's output is not in the delivered text (piped, filtered or redirected)"
        return None
    if stdout_sha256(delivered) != receipt.get("sha256"):
        return "the delivered text differs from the run's own output (piped, filtered or redirected)"
    return None


def _credit_delivered_slice(payload):
    """Credit the tiers a staged `for-role` receipt names, only for the stdout that reached context."""
    session_id = payload.get("session_id") or ""
    if not session_id or not _invokes_for_role((payload.get("tool_input") or {}).get("command")):
        return False
    response = payload.get("tool_response")
    delivered = response.get("stdout") if isinstance(response, dict) else ""
    receipt = consume_ladder_receipt(session_id, delivered or "")
    if not receipt:
        return False
    reason = _delivery_failure(payload.get("tool_response"), receipt)
    if reason:
        fail_ladder_receipt(session_id, reason)
        return False
    tiers = [tier for tier in receipt.get("tiers") or [] if isinstance(tier, str) and tier]
    return bool(tiers and mark_ladder_slice(session_id, tiers, clear_receipt_failure=True,
                                            digest=ladder_digest(payload)))


FAULT_NOTE = ("Dispatch doctrine check could not read its session state, so this call is allowed without it. "
              "Repair hooks/model_ladder_gate.py if this repeats.")


# The load steps the deny names and the post-compaction rule (`doctrine_rule`) states: one string per step.
# One string names both routes, since a caller cannot always tell which it holds: a delegate has no Skill tool, and
# a full SKILL.md Read records the same load (`skill_load_marker.py`).
SKILL_STEP = ('invoke Skill(skill="{name}"), or without the Skill tool Read `{path}` in full with the Read tool, '
              'no offset or limit ({rule})')
SLICE_STEP = 'print the doctrine for {which}: `' + SLICE_COMMAND + ' {tiers}` (orchestration §5)'
SLICE_BELOW_STEP = 'read the ladder slice for {which} below (orchestration §5)'
READ_STEP = 'Read `{path}` in full, no offset or limit (orchestration §5)'


def skill_step(name, payload, rule):
    """The load step for skill `name`, both routes named, its SKILL.md path absolute so a worktree cwd cannot
    misresolve it."""
    path = os.path.realpath(_root(payload if isinstance(payload, dict) else {}) / '.claude' / 'skills' / name / 'SKILL.md')
    return SKILL_STEP.format(name=name, path=path, rule=rule)


def _tier_phrase(tier, consumers):
    """`executor (fold)`: the missing tier and the call's labels that pin it, when the caller named them."""
    labels = consumers.get(tier) or []
    return "%s (%s)" % (tier, ", ".join(labels)) if labels else tier


def doctrine_rule(payload):
    """The whole dispatch-doctrine demand `doctrine_check` applies, for a line printed before any dispatch."""
    return ('Before the next dispatch, load each skill governing its brief and artifacts in this context '
            '(orchestration §0 Governing skills). The dispatch doctrine must also be in this context: the orchestration skill (a '
            'copy re-attached whole and still current with its SKILL.md on disk after the compaction counts), else '
            + skill_step('orchestration', payload, 'orchestration §0') + '; and, since each '
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


def _embedded_slice(session_id, payload):
    """The whole `for-role` text for every tier the registry claims, recorded as loaded for all of them in one
    write, else None when it cannot be produced or recorded (the deny then names the command and records
    nothing). One write means a later call pinning a different tier is already covered. When the plan cannot be
    resolved or the ladder cut fails, the ladder alone is embedded: the same doctrine, minus the room lines, and
    the only text an unidentified session can be given."""
    try:
        tools = str(Path(__file__).resolve().parents[1] / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import model_registry
        claimed = model_registry.claimed_tiers()
    except Exception:
        return None
    text = None
    try:
        text, ladder_error = model_registry.for_role_text(claimed)
        if ladder_error:
            text = None
    except Exception:
        text = None
    if text is None:
        try:
            text = model_registry.ladder_slice(claimed)
        except Exception:
            return None
    return text if mark_ladder_slice(session_id, claimed, digest=ladder_digest(payload)) else None


def _skill_held(session_id, payload):
    return skill_in_context(session_id, 'orchestration', agent_id=payload.get('agent_id') or '',
                            transcript_path=payload.get('transcript_path'), root=str(_root(payload)))


def advance_notice(payload):
    """The earlier surface of `doctrine_check` (instruction_quality §3): one line naming the loads a dispatch will be
    denied for, so the deny is not the first place the agent meets them. Empty when the orchestration skill and a ladder
    slice or full Read are both in context, for an unidentified session, and after the first line since the session
    started or last compacted. The caller supplies the moment (the first dispatch-prep signal of a window)."""
    session_id = payload.get('session_id') or ''
    if not session_id:
        return ''
    ready, tiers = held_ladder(read_json_salvage(state_path(session_id)), payload)
    unmet = []
    if not _skill_held(session_id, payload):
        unmet.append(skill_step('orchestration', payload, 'orchestration §0'))
    if not (ready or tiers):
        unmet.append(SLICE_STEP.format(which='each tier the jobs pin', tiers='<tier>...'))
    if not unmet or not fire_once_since_compaction(session_id, NOTICE_ONCE):
        return ''
    return '%s a dispatch is denied until you %s.' % (NOTICE_TAG, ' and '.join(unmet))


def doctrine_check(payload, tiers=None, embed_slice=False):
    """(deny reason, fault note) for a pinned dispatch. The reason names each doctrine not in this context, by its
    one load route: the orchestration skill (orchestration §0) and the ladder (orchestration §5). The step after
    the load is to check the call against them and re-issue it, because they exist to change the call. Both are
    observed state, never the route or pin they inform. A compaction clears the ladder, and the skill unless the
    client re-attached it whole (`_hook_state.skill_in_context` reads that attachment from the payload's transcript). A full SKILL.md Read marks the skill loaded
    too (skill_load_marker.py), so a caller without the Skill tool (`has_skill_tool`) is told that Read and one
    with it the Skill call: each deny names the one load its caller can perform. State the lock cannot write fails open with the fault note
    (instruction_quality §16): no load could be recorded.

    The ladder is met by a full Read or by `model_registry.py for-role` for every tier the call implies.
    `tiers` is a zero-argument callable returning those canonical tiers, evaluated only when the full Read is
    absent. When it names none (absent, failing or empty) the call's tiers are unknown, so only the full Read meets
    the ladder; with tiers known and `embed_slice` True, the deny ends with the whole `for-role` text for every tier
    the registry claims and records them all in one write, so the re-issued call and a later call pinning a second
    tier both pass. Only a caller whose deny text reaches the session's context passes
    True: the record is a side effect, and a slice recorded but never delivered lets the next call pass without the
    doctrine in context. By default, and when the slice cannot be produced, the deny names the slice command and
    records nothing."""
    session_id = payload.get('session_id') or ''
    written, state = update_json_locked(state_path(session_id), dict)
    if not written:
        return None, FAULT_NOTE
    ladder_ready, covered = held_ladder(state, payload)
    unmet, slice_text = [], None
    if not _skill_held(session_id, payload):
        unmet.append(skill_step('orchestration', payload, 'orchestration §0'))
    if not ladder_ready:
        needed = _call_tiers(tiers)
        if needed is None:
            unmet.append(READ_STEP.format(path=expected_path(payload)))
        else:
            needed, consumers = needed
            missing = [t for t in needed if t not in covered]
            if missing:
                named = ', '.join(_tier_phrase(tier, consumers) for tier in missing)
                slice_text = _embedded_slice(session_id, payload) if embed_slice else None
                unmet.append(SLICE_BELOW_STEP.format(which=named) if slice_text
                             else SLICE_STEP.format(which=named, tiers=' '.join(missing)))
    if not unmet:
        return None, None
    reason = ('Dispatch DENIED: the dispatch doctrine is not in context since the session started or last compacted. '
              'Load it: ' + '; '.join(unmet) + '. Then check this call against it (route, jobs, '
              'models, efforts, reading) and re-issue it with any change it calls for.')
    failure = ladder_receipt_failure(session_id) if session_id else None
    if failure:
        reason += ' The last for-role run did not reach context: %s.' % failure
    if slice_text:
        reason += '\n\n' + slice_text
    return reason, None


def ladder_held(session_id):
    """True while any of the ladder doctrine is in this context: a full Read, or a slice of any tier, of the ladder
    text now on disk. Unreadable state reads as not held."""
    ready, tiers = held_ladder(read_json_salvage(state_path(session_id or "")))
    return bool(ready or tiers)


def tier_in_context(session_id, tier):
    """True while the ladder rows claiming `tier` are in this context: a full Read, or a slice that named the tier,
    of the ladder text now on disk. Unreadable state reads as not in context."""
    ready, tiers = held_ladder(read_json_salvage(state_path(session_id or "")))
    return ready or tier in tiers


def claim_loaded(session_id):
    """True while the ladder is in context; compaction clears it. Unreadable state reads as not loaded."""
    written, state = update_json_locked(state_path(session_id or ""), dict)
    return bool(written and held_ladder(state)[0])


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
