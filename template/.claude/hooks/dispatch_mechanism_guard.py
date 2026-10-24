#!/usr/bin/env python3
"""PreToolUse(Workflow): deny until the dispatch doctrine is in context; deny a nested fan-out.

model_ladder_gate.doctrine_check names each unmet doctrine (the orchestration skill, the ladder: read
in full, or printed as the slice of each tier the call pins) with its load route; both are observed
state that skill_load_marker, post_read_dispatch and `model_registry.py ladder-slice` record, and a
compaction clears them. The route and pin those documents inform stay the agent's.
The Agent tool is denied in settings.base.json, so no Agent call reaches this hook.
The size line is a step before the call: `/delegate` runs `tools/dispatch_size.py`.

A brief instruction to fan out through an engine or a command granting Workflow
still denies: a delegate lacks those tools (orchestration §0). The instruction grammar and
quoted-data exclusions belong to tools/tool_profiles.py. Brief files use the inherited
512 KiB scan cap; an oversized file names its unscanned tail rather than denying on size.
A nested fan-out in its scanned prefix still denies.

An inline or uncommitted script whose schema types its own `evidence` property denies: the claims
verifier reads `evidence` as the evidence contract (workflow_authoring.md: a findings script embeds
EVIDENCE-SSOT), so every such claim returns UNCHECKED after the run has spent its agents. A committed
script under `.claude/workflows/` or `.claude/scripts/` is outside this check; its contract is
test_native_caller_contracts.py's census, and graders there use a free-text `evidence` field.

CHANNEL: hookSpecificOutput.permissionDecision=deny naming every unmet precondition (nested
fan-out, own evidence schema, doctrine) in one reason; additionalContext for notes; exit 0.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_ladder_gate  # noqa: E402
from _dispatch_pins import script_blobs  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import tool_profiles  # noqa: E402  (the dispatch grammar and quoted-data rule; a tool never imports a hook)

UNVERIFIABLE_REASON = (
    "Dispatch check could not read its input; the call is allowed without that check. "
    "Repair hooks/dispatch_mechanism_guard.py if this repeats."
)


# ---------------------------------------------------------------------------------------------
# Second check — a NESTED fan-out: a brief that tells its delegate to dispatch lenses through an
# engine, or to run a command whose `allowed-tools` grant Workflow or Agent. A delegate cannot run
# that fan-out (orchestration §0 states which tools each kind of delegate holds), so those lenses
# would run unpinned or not at all; the orchestrator materializes them (tools/lens_briefs.py) and
# dispatches them itself.
#
# The grammar and the fan-out set live in tools/tool_profiles.py (`nested_fanout_hit`,
# `nested_fanout_hits`), which the sidecar launcher's `--brief` check reads too. Matched on the ACTION,
# not the noun (rules/harness_tooling.md): a dispatch verb within the same line span of the target
# denies; a descriptive mention, a negated clause, quoted data and a fenced block never do. The generated lens briefs
# strip the caller-facing spawn rules for this reason (lens_briefs.lens_facing).
MAX_BRIEF_BYTES = 512 * 1024
BRIEF_SCAN_NOTE = "Brief {path}: scanned the first 512 KiB (inherited scan cap); unscanned tail was not checked for nested fan-out."

NESTED_REASON = (
    "Dispatch denied: this brief tells its delegate to dispatch a fan-out ({hit}). Delegates have "
    "no Workflow tool, so those lenses would run unpinned or not at all (orchestration §0). "
    "Materialize them with tools/lens_briefs.py and dispatch them from this session through "
    "dispatch.js, plus a consolidator job."
)


def _read_capped(path: str, notes=None):
    try:
        with open(path, "rb") as fh:
            content = fh.read(MAX_BRIEF_BYTES + 1)
        if len(content) > MAX_BRIEF_BYTES and notes is not None:
            notes.append(BRIEF_SCAN_NOTE.format(path=path))
        return content[:MAX_BRIEF_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return ""  # unreadable brief: dispatch.js itself will fail loudly on it — nothing to judge here


def _entries(value):
    return value if isinstance(value, list) else []


def brief_texts(payload: dict, notes=None) -> list:
    """Every text a delegate will receive as instructions, from the tool input."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return []
    args = ti.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return [args]
    if not isinstance(args, dict):
        return []
    texts = []
    jobs = list(_entries(args.get("jobs")))
    for chain in _entries(args.get("chains")):
        if isinstance(chain, dict):
            jobs.extend(_entries(chain.get("jobs")))
    for j in jobs:
        if not isinstance(j, dict):
            continue
        p = j.get("promptPath")
        if p:
            texts.append(_read_capped(str(p), notes))
    for key in ("agents", "lenses"):
        for entry in _entries(args.get(key)):
            if not isinstance(entry, dict):
                continue
            if entry.get("prompt"):
                texts.append(str(entry["prompt"]))
            if entry.get("promptPath"):
                texts.append(_read_capped(str(entry["promptPath"]), notes))
    return texts


def nested_fanout_hit(payload: dict, notes=None):
    """The first nested fan-out instruction across the payload's briefs, or None.

    Commands resolve against $CLAUDE_PROJECT_DIR's `.claude/commands`, else this checkout's
    (tool_profiles._commands_dir), so a worktree session reads its own grants.
    """
    root = os.environ.get("CLAUDE_PROJECT_DIR") or None
    for text in brief_texts(payload, notes):
        hit = tool_profiles.nested_fanout_hit(text, root)
        if hit:
            return hit
    return None


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OWN_EVIDENCE_RE = re.compile(r"""\bevidence['"]?\s*:\s*\{\s*['"]?type['"]?\s*:""")
OWN_EVIDENCE_REASON = (
    "Dispatch denied: {label} types its own `evidence` schema, so the claims verifier can check none of "
    "its claims and each returns UNCHECKED. Dispatch the pinned jobs through `/delegate`, which owns the "
    "jobs-file format and the paying plan (orchestration section 0). A hand-written script is for an engine "
    "the fan-out lacks (skills/orchestration/reference/workflow_authoring.md); the claims fence a job "
    "returns is reference/evidence_contract.md."
)


def own_evidence_hit(payload):
    """The label of the first inline or uncommitted script whose schema types `evidence` itself, or None."""
    path = str((payload.get("tool_input") or {}).get("scriptPath") or "")
    committed = None
    if path:
        root = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or REPO)
        full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        if full.startswith(tuple(os.path.join(root, ".claude", d) + os.sep for d in ("workflows", "scripts"))):
            committed = os.path.basename(path)
    blobs, _incomplete = script_blobs(payload)
    for label, text in blobs:
        if label != committed and OWN_EVIDENCE_RE.search(text):
            return label
    return None


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def _advise(context):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": context,
    }}))


def _call_tiers(payload):
    """The tiers the call's pins declare or imply, else None (the gate then asks for the full ladder). Loaded only
    when the full ladder Read is absent, since workflow_provider_guard is the heavier import."""
    try:
        import workflow_provider_guard
        return workflow_provider_guard.call_tiers(payload)
    except Exception:
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        _advise(UNVERIFIABLE_REASON)
        return 0
    if payload.get("tool_name") != "Workflow":
        return 0
    notes = []
    hit = nested_fanout_hit(payload, notes)
    reasons = [NESTED_REASON.format(hit=hit)] if hit else []
    own = own_evidence_hit(payload)
    if own:
        reasons.append(OWN_EVIDENCE_REASON.format(label=own))
    doctrine, fault = model_ladder_gate.doctrine_check(payload, tiers=lambda: _call_tiers(payload),
                                                       embed_slice=True)
    if doctrine:
        reasons.append(doctrine)
    if reasons:
        _deny(" ".join(reasons))
        return 0
    if fault:
        notes.append(fault)
    if notes:
        _advise("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
