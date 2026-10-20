#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Workflow|Agent tool family. Class: process guard (each member keeps its own).

One settings entry replacing five. The client shows one deny reason per tool call, so five hooks that
each denied for their own precondition showed the first and left the rest for the retry
(instruction_quality section 3: a guard with several preconditions names every unmet one in a single deny).
This module runs the members in-process, like `pre_bash_dispatch.py` and `pre_read_dispatch.py`, but
collects every deny instead of stopping at the first.

Members, in order (each keeps its own `main()` and CLI contract, so its own proof stays standalone):
  1. subagent_dispatch_guard   a delegate or sidecar child may not dispatch. A deny here ends the chain: the
                               orchestrator's preconditions below are not the delegate's to meet.
  2. workflow_script_lf_guard  rewrites a harness script's CRLF to LF before the permission handler reads it.
  3. dispatch_mechanism_guard  nested fan-out in a brief; a script typing its own `evidence`; dispatch
                               doctrine not in context.
  4. workflow_provider_guard   tool profile, vocabulary, provider capacity, tier, five-hour width; its
                               `updatedInput` injects `args.__transport` and the rails.
  5. readonly_marker_arm       arms the read-only fan-out marker. Runs only when nothing denied, since a denied
                               dispatch starts no lens for the marker to cover.

Merge:
  deny            every member's reason, each once, in member order. One reason is emitted verbatim; several
                  are numbered under a one-line header.
  additionalContext  every note, each once, in member order.
  updatedInput    chained: after a member rewrites the input, later members receive the rewritten call, so a
                  later rewrite is a superset of an earlier one. A partial rewrite overlays the call's own
                  `tool_input` keys. A denied call carries none.

Fail posture: a member exception, import fault or exit outside {0, 2} is reported on stderr and in
`additionalContext`, naming the lost check and repair; the next member runs. The exception is
`workflow_provider_guard` on a Workflow call: without its `args.__transport` injection every engine refuses
to run, so its fault denies with the error, as the guard's own `_unchecked` does. An Agent call keeps the
advisory. A member's exit 2 joins the deny with its stderr as the reason.

Wired in: settings.json hooks.PreToolUse, matcher "Workflow|Agent"; timeout covers the members run in
sequence (the provider guard bounds its own work at DEADLINE_SECONDS).
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _inprocess_hook import hook_output, run_hook  # noqa: E402

_ROLE_GATE = "subagent_dispatch_guard"
_RUNS_WHEN_ALLOWED = "readonly_marker_arm"
_FAIL_CLOSED_ON_WORKFLOW = "workflow_provider_guard"
_DECISION_RANK = {"deny": 3, "ask": 2, "allow": 1}


class _ImportStub:
    """Stands in for a member that failed to import, so the others still run and the fault reaches the caller."""

    def __init__(self, name, error):
        self.__name__ = name
        self.__file__ = name + ".py"
        self.error = error

    def main(self):
        sys.stderr.write("%s failed to import (%s)\n" % (self.__name__, self.error))
        return 1


def _failed_import(name, exc):
    return _ImportStub(name, "%s: %s" % (type(exc).__name__, exc))


def _load(name):
    try:
        return importlib.import_module(name)
    except Exception as exc:
        return _failed_import(name, exc)


# Import with the real stdout bound; some members reconfigure it on import.
subagent_dispatch_guard = _load("subagent_dispatch_guard")
workflow_script_lf_guard = _load("workflow_script_lf_guard")
dispatch_mechanism_guard = _load("dispatch_mechanism_guard")
workflow_provider_guard = _load("workflow_provider_guard")
readonly_marker_arm = _load("readonly_marker_arm")

HOOKS = (
    (subagent_dispatch_guard, ()),
    (workflow_script_lf_guard, ()),
    (dispatch_mechanism_guard, ()),
    (workflow_provider_guard, ()),
    (readonly_marker_arm, ()),
)


def _once(items):
    seen, kept = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            kept.append(item)
    return kept


def _fail_closed_reason(name, code, err):
    return ("%s failed (exit %s), so it could not inject `args.__transport`, the model vocabulary every engine "
            "refuses to run without. Nothing was dispatched. Re-issue the same call once; if it fails again, "
            "run `python3 .claude/tests/test_%s.py` and read logs/%s.jsonl (orchestration section 5). "
            "Its error:\n%s" % (name, code, name, name, err.strip()[-1500:]))


def _joined(reasons):
    if len(reasons) == 1:
        return reasons[0]
    return ("Dispatch denied: %d preconditions are unmet. Clear all of them, then re-issue the call.\n%s"
            % (len(reasons), "\n".join("%d. %s" % (i, r) for i, r in enumerate(reasons, 1))))


def dispatch(raw_payload, hooks=HOOKS):
    """Run the chain and return (exit_code, stdout_text, stderr_text) for the dispatcher process."""
    payload = json.loads(raw_payload)
    reasons, contexts, user_stderr = [], [], []
    decision, rewritten = None, None
    for module, argv_tail in hooks:
        name = module.__name__
        if name == _RUNS_WHEN_ALLOWED and reasons:
            continue
        code, out, err = run_hook(module, argv_tail, raw_payload)
        before = len(reasons)
        if code == 2:
            reasons.append(err.strip() or "%s blocked the call." % name)
        elif code != 0:
            user_stderr.append("[pre_workflow_dispatch] %s exited %s: %s" % (name, code, err.strip()[-1500:]))
            if name == _FAIL_CLOSED_ON_WORKFLOW and payload.get("tool_name") == "Workflow":
                reasons.append(_fail_closed_reason(name, code, err))
            else:
                contexts.append("[pre_workflow_dispatch] %s exited %s: %s; its checks did not run. "
                                "Repair .claude/hooks/%s.py." % (name, code, err.strip()[-1500:], name))
        else:
            hso = hook_output(out)
            note = hso.get("additionalContext")
            if isinstance(note, str) and note.strip():
                contexts.append(note)
            verdict = hso.get("permissionDecision")
            if verdict == "deny":
                reasons.append(hso.get("permissionDecisionReason") or "%s denied the call." % name)
            elif verdict in _DECISION_RANK and _DECISION_RANK[verdict] > _DECISION_RANK.get(decision, 0):
                decision = verdict
            if isinstance(hso.get("updatedInput"), dict):
                rewritten = dict(payload.get("tool_input") or {}, **hso["updatedInput"])
                payload["tool_input"] = rewritten
                raw_payload = json.dumps(payload)
        if name == _ROLE_GATE and len(reasons) > before:
            break

    reasons = _once(reasons)
    result = {"hookEventName": "PreToolUse"}
    if reasons:
        result["permissionDecision"] = "deny"
        result["permissionDecisionReason"] = _joined(reasons)
    else:
        if decision:
            result["permissionDecision"] = decision
        if rewritten is not None:
            result["updatedInput"] = rewritten
    contexts = _once(contexts)
    if contexts:
        result["additionalContext"] = "\n\n".join(contexts)
    stdout_text = json.dumps({"hookSpecificOutput": result}) if len(result) > 1 else ""
    return 0, stdout_text, "\n".join(user_stderr)


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Workflow", "Agent"):
        sys.exit(0)
    code, out, err = dispatch(raw)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err + ("\n" if not err.endswith("\n") else ""))
    sys.exit(code)


if __name__ == "__main__":
    main()
