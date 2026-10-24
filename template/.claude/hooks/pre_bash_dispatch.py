#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Bash/PowerShell/Monitor tool family.

One settings.json entry replacing twelve separate hook commands. Each sub-hook keeps its own
`main()` and CLI contract (stdin JSON payload, stdout JSON / stderr + exit 2); this module runs
them in-process with stdin/stdout/stderr/argv swapped per hook, so one interpreter spawn serves
every guard instead of twelve. Measured: twelve cold Python starts per Bash call on Windows cost
more than any guard's own work (`pre_read_dispatch.py` is the read-family sibling).

Order is commit normalizers, then guards, approvers and advisories; the loop STOPS at the first hard
block (exit 2 or `permissionDecision: deny`), so a blocked command never waits on
`gate_cadence_guard`'s queue wait. Native parallel hooks would report every block at once; this
reports the first. Merge otherwise follows the native aggregation: deny > ask > allow, every
`additionalContext` concatenated.

Sub-hook self-gating on `tool_name` makes the union matcher safe: a PowerShell call reaches the
Bash-only hooks and they exit 0 silently, exactly as they did on their own matcher.

Fail posture per sub-hook: an exception or a non-2 non-zero exit is reported on stderr and in
`additionalContext`, naming the lost check and repair; the next hook runs. Floor metadata is
available before member imports; import faults use independent stubs. A floor member's fault
blocks the call. Bootstrap import faults deny every Bash/PowerShell call and stay silent for
other tools. dangerous_shell_guard is the one destructive-command check and a floor member in every
profile, loaded outside `_optional_hooks`; its import fault denies through `_floor_stub`.

The chain runs inside `_git_commit.memoized()`: every sub-hook reads the same repo state, so a
read-only git query they repeat spawns git once per tool call.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor", timeout = the
largest sub-hook timeout (gate_cadence_guard's queue wait).
"""

import importlib
import io
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    _bootstrap = '_git_commit'
    import _git_commit  # noqa: E402

    _bootstrap = 'sidecar_argv'  # _git_commit tolerates its absence; the shell guards need it
    import sidecar_argv  # noqa: E402,F401

    _bootstrap = '_optional_hooks'
    import _optional_hooks  # noqa: E402
except Exception as exc:
    try:
        _payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _payload = None
    if isinstance(_payload, dict) and _payload.get('tool_name') in ('Bash', 'PowerShell'):
        sys.stderr.write(
            'BLOCKED shell call -- %s failed to import (%s: %s). Repair %s/%s.py; '
            'no shell call runs until it imports cleanly.\n' %
            (_bootstrap, type(exc).__name__, exc,
             'tools' if _bootstrap == 'sidecar_argv' else 'hooks', _bootstrap))
        sys.exit(2)
    sys.exit(0)


class _ImportStub:
    """Stands in for a sub-hook that failed to import, so the dispatcher keeps running every other guard."""

    def __init__(self, name, error):
        self.__name__ = name
        self.__file__ = name + ".py"
        self.error = error


def _advisory_stub(name, error, lost):
    """A failed adviser acts on nothing; the dispatcher reports its exit 1 through
    stderr and additionalContext. `lost` names what stops until it imports."""
    stub = _ImportStub(name, error)

    def main():
        sys.stderr.write("%s failed to import (%s); %s until hooks/%s.py imports cleanly.\n" % (name, error, lost, name))
        return 1
    stub.main = main
    return stub


def _floor_stub(name, error, matches, action):
    """A floor guard that failed to import denies each command `matches(command, cwd)` selects (the action the
    guard judges, never its noun) and names the import error. A matcher that raises denies."""
    stub = _ImportStub(name, error)
    stub.FAIL_CLOSED = True

    def main():
        try:
            payload = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, ValueError):
            return 0
        if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
            return 0
        command = (payload.get("tool_input") or {}).get("command") or ""
        try:
            if not matches(command, payload.get("cwd") or "."):
                return 0
        except Exception:  # noqa: BLE001 - an unparseable command denies
            pass
        sys.stderr.write("BLOCKED %s -- %s failed to import (%s). Repair hooks/%s.py; no %s runs until it "
                         "imports cleanly.\n" % (action, name, error, name, action))
        return 2
    stub.main = main
    return stub


def _runs_git(command, cwd):
    return bool(_git_commit.git_invocations(command, cwd))


def _commits(command, cwd):
    return bool(_git_commit.commit_invocations(command, cwd))


def _import_error(exc):
    return "%s: %s" % (type(exc).__name__, exc)


# Metadata must survive a member's failed import.
_FLOOR_MEMBERS = {
    'git_guardrails': (_runs_git, 'git call'),
    'baseline_classification_guard': (_commits, 'git commit'),
    'dangerous_shell_guard': (lambda _command, _cwd: True, 'destructive-command check'),
    'editor_live_build_guard': (lambda command, _cwd: 'dotnet' in command or '-ignoreeditor' in command.lower(),
                                'build into a checkout'),
}
_IMPORT_ERRORS = {}


def _failed_import(name, exc):
    error = _import_error(exc)
    _IMPORT_ERRORS[name] = error
    if name in _FLOOR_MEMBERS:
        matches, action = _FLOOR_MEMBERS[name]
        return _floor_stub(name, error, matches, action)
    return _advisory_stub(name, error, 'its checks do not run')


def _load(name):
    try:
        return importlib.import_module(name)
    except Exception as exc:
        return _failed_import(name, exc)


# Import with the real stdout bound; some members reconfigure it on import.
bash_backslash_fidelity = _load('bash_backslash_fidelity')
compound_cd_approver = _load('compound_cd_approver')
git_guardrails = _load('git_guardrails')
editor_live_build_guard = _optional_hooks.load('editor_live_build_guard', on_error=_failed_import)
rail_probe_guard = _optional_hooks.load('rail_probe_guard', on_error=_failed_import)
sidecar_dispatch_context = _load('sidecar_dispatch_context')
hand_pricing_advisory = _optional_hooks.load('hand_pricing_advisory', on_error=_failed_import)
dangerous_shell_guard = _load('dangerous_shell_guard')
(cloud_test_enforcer, gate_cadence_guard, prototype_containment_guard,
 provisional_totality_guard, tres_nullstrip_guard, tres_script_strip_guard) = _optional_hooks.load(
    'cloud_test_enforcer', 'gate_cadence_guard', 'prototype_containment_guard',
    'provisional_totality_guard', 'tres_nullstrip_guard', 'tres_script_strip_guard', on_error=_failed_import)
approving_gates = _load('approving_gates')
unbounded_scan_guard = _load('unbounded_scan_guard')
harness_shell_write_guard = _load('harness_shell_write_guard')
foreground_wait_guard = _optional_hooks.load('foreground_wait_guard', on_error=_failed_import)
wide_test_guard = _optional_hooks.load('wide_test_guard', on_error=_failed_import)
run_call_router = _optional_hooks.load('run_call_router', on_error=_failed_import)
triage_commit_check = _load('triage_commit_check')
duplicate_commit_check = _load('duplicate_commit_check')
baseline_classification_guard = _load('baseline_classification_guard')
rule_number_guard = _load('rule_number_guard')
heredoc_quoting_advisory = _load('heredoc_quoting_advisory')
_APPROVING_GATES_IMPORT_ERROR = _IMPORT_ERRORS.get('approving_gates')
_TRIAGE_CHECK_IMPORT_ERROR = _IMPORT_ERRORS.get('triage_commit_check')
_DUPLICATE_CHECK_IMPORT_ERROR = _IMPORT_ERRORS.get('duplicate_commit_check')
_BASELINE_GUARD_IMPORT_ERROR = _IMPORT_ERRORS.get('baseline_classification_guard')


# (module, argv tail). Normalizers first, then guards, approvers and advisories.
HOOKS = ((rail_probe_guard, ()),) if rail_probe_guard else ()  # inert unless a rail-battery run is armed
HOOKS += (
    (_git_commit, ()),
    (cloud_test_enforcer, ()),
    (foreground_wait_guard, ()),
    (wide_test_guard, ()),
    (baseline_classification_guard, ()),
    (dangerous_shell_guard, ()),
    (git_guardrails, ()),
    (editor_live_build_guard, ()),
    (tres_script_strip_guard, ("--hook",)),
    (tres_nullstrip_guard, ("--hook",)),
    (prototype_containment_guard, ("--hook",)),
    (provisional_totality_guard, ("--hook",)),
    (gate_cadence_guard, ()),
    (compound_cd_approver, ()),
    (unbounded_scan_guard, ()),
    (harness_shell_write_guard, ()),
    (triage_commit_check, ()),
    (duplicate_commit_check, ()),
    (rule_number_guard, ()),
    (hand_pricing_advisory, ()),
    (sidecar_dispatch_context, ()),
    (approving_gates, ()),
    (heredoc_quoting_advisory, ()),
    # Last: it rewrites the command through updatedInput, so every deny above must win first.
    (bash_backslash_fidelity, ()),
)
HOOKS = tuple((module, argv_tail) for module, argv_tail in HOOKS if module is not None)

_DECISION_RANK = {"deny": 3, "ask": 2, "allow": 1}
_NOTHING_RAN = "Nothing in this command ran."


def _denied(text):
    """A deny reason with the no-partial-run line once: a denied call runs no part of its chain."""
    return text if text.rstrip().endswith(_NOTHING_RAN) else text.rstrip() + "\n" + _NOTHING_RAN


class _Capture(io.StringIO):
    """StringIO that tolerates the stream calls a hook makes on a real console handle."""

    def reconfigure(self, **_kwargs):
        pass


def run_hook(module, argv_tail, raw_payload):
    """Run one sub-hook's main() in-process. Returns (exit_code, stdout, stderr)."""
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    out, err = _Capture(), _Capture()
    sys.stdin = io.StringIO(raw_payload)
    sys.stdout, sys.stderr = out, err
    sys.argv = [getattr(module, "__file__", module.__name__)] + list(argv_tail)
    code = 0
    try:
        ret = module.main()
        if isinstance(ret, int):
            code = ret
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception:
        code = 1
        err.write(traceback.format_exc())
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def _hook_output(stdout_text):
    """The hookSpecificOutput dict a sub-hook printed, or {} when it printed nothing usable."""
    text = stdout_text.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    hso = parsed.get("hookSpecificOutput")
    return hso if isinstance(hso, dict) else {}


def dispatch(raw_payload, hooks=HOOKS):
    """Run the chain and return (exit_code, stdout_text, stderr_text) for the dispatcher process."""
    with _git_commit.memoized():
        return _dispatch(raw_payload, hooks)


def _dispatch(raw_payload, hooks):
    decision, reason, contexts, extra, user_stderr = None, None, [], {}, []
    payload = json.loads(raw_payload)
    try:
        updated, routing_context = run_call_router.route(payload) if run_call_router is not None else ({}, [])
    except Exception as exc:
        # A routing fault costs only the rewrite; the guard chain below still decides.
        if isinstance(run_call_router, _ImportStub):
            exc = run_call_router.error
        updated, routing_context = {}, []
        user_stderr.append("run_call_router failed, call left unrouted: %s" % exc)
        contexts.append("[pre_bash_dispatch] run_call_router fault: %s; call left unrouted. "
                        "Repair .claude/hooks/run_call_router.py." % exc)
    if updated:
        payload['tool_input'] = updated
        raw_payload = json.dumps(payload)
        extra['updatedInput'] = updated
        contexts.extend(routing_context)
    for module, argv_tail in hooks:
        code, out, err = run_hook(module, argv_tail, raw_payload)
        if code == 2:
            block = err.rstrip() if err.strip() else "%s blocked the call." % module.__name__
            return 2, "", _denied(block)
        floor = getattr(module, 'FAIL_CLOSED', False) or (
            module.__name__ in _FLOOR_MEMBERS and payload.get('tool_name') in ('Bash', 'PowerShell'))
        if code != 0 and floor:
            return 2, "", _denied("%s is a floor guard and failed (exit %s), so the call is blocked until it runs "
                                  "cleanly. Fix the guard; its error:\n%s"
                                  % (module.__name__, code, err.strip()[-1500:]))
        if code != 0:
            fault = "[pre_bash_dispatch] %s exited %s: %s" % (module.__name__, code, err.strip()[-1500:])
            user_stderr.append(fault)
            contexts.append(fault + "; its checks did not run. Repair .claude/hooks/%s.py." % module.__name__)
            continue
        hso = _hook_output(out)
        ctx = hso.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            contexts.append(ctx)
        pd = hso.get("permissionDecision")
        if pd in _DECISION_RANK and _DECISION_RANK[pd] > _DECISION_RANK.get(decision, 0):
            decision, reason = pd, hso.get("permissionDecisionReason")
        if isinstance(hso.get('updatedInput'), dict):
            payload['tool_input'] = dict(payload.get('tool_input') or {}, **hso['updatedInput'])
            raw_payload = json.dumps(payload)
            extra['updatedInput'] = payload['tool_input']
        for key, value in hso.items():
            if key not in ("hookEventName", "permissionDecision", "permissionDecisionReason",
                           "additionalContext", "updatedInput") and key not in extra:
                extra[key] = value
        if decision == "deny":
            break

    # Throttled orphaned-search reaper. After the chain so a hard block (exit 2, above) never
    # spends a snapshot whose advisory it could not deliver, and skipped on a deny so nothing
    # runs between the deny decision and its output.
    if decision != "deny":
        try:
            import runaway_scan_reaper
            lines = runaway_scan_reaper.check(raw_payload)
            if lines:
                contexts.append("\n".join(lines))
        except Exception:
            pass

    hso = {"hookEventName": "PreToolUse"}
    if decision:
        hso["permissionDecision"] = decision
        if reason:
            hso["permissionDecisionReason"] = _denied(reason) if decision == "deny" else reason
    if contexts:
        hso["additionalContext"] = "\n\n".join(contexts)
    hso.update(extra)
    stdout_text = json.dumps({"hookSpecificOutput": hso}) if len(hso) > 1 else ""
    return 0, stdout_text, "\n".join(user_stderr)


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        sys.exit(0)
    code, out, err = dispatch(raw)
    if out:
        sys.stdout.write(out)
    if err:
        sys.stderr.write(err + ("\n" if not err.endswith("\n") else ""))
    sys.exit(code)


if __name__ == "__main__":
    main()
