#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse dispatcher for the Bash/PowerShell/Monitor/TaskStop tool family, behind the floor entry.

`hooks/pre_bash.py` is the registered entry: it imports this module and calls `decide_text`, and any fault here
(a file that does not parse, an import that raises) denies the call there. Run as a script, this module keeps its
v1 stdin/exit-code contract.

One settings.json entry replacing twelve separate hook commands. Each sub-hook keeps its own
`main()` and CLI contract (stdin JSON payload, stdout JSON / stderr + exit 2); this module runs
them in-process with stdin/stdout/stderr/argv swapped per hook, so one interpreter spawn serves
every guard instead of twelve. Measured: twelve cold Python starts per Bash call on Windows cost
more than any guard's own work (`pre_read_dispatch.py` is the read-family sibling).

Order is commit normalizers, then guards, approvers and advisories; the loop STOPS at the first hard
block (exit 2 or `permissionDecision: deny`), and either one denies with the blocker's text alone: no earlier
member's rewrite or context rides on it, and their once-keys go back. Native parallel hooks would report every block at once;
this reports the first. Merge otherwise follows the native aggregation: deny > ask > allow, every
`additionalContext` concatenated.

Sub-hook self-gating on `tool_name` makes the union matcher safe: a PowerShell call reaches the
Bash-only hooks and they exit 0 silently, exactly as they did on their own matcher.

Fail posture per sub-hook: an exception or a non-2 non-zero exit is reported on stderr and in
`additionalContext`, naming the lost check and repair; the next hook runs. Floor metadata is
available before member imports; import faults use independent stubs. A loaded floor member's fault blocks the
call, whatever it is: the guard crashed while judging it. Its `_FLOOR_MEMBERS` matcher scopes only a failed import
(sidecar_dispatch_context's: a sidecar launch, since its doctrine and pin checks stand in front of paid spend).
Bootstrap import faults deny every Bash/PowerShell call and stay silent for other tools. dangerous_shell_guard is the one destructive-command check and a floor member in every
profile, loaded outside `_optional_hooks`; its import fault denies through `_floor_stub`. It holds every git
loss check (the v1 `git_guardrails.py` and `_peer_loss.py` cases are its table rows). kill_guard is the stop
check: a TaskStop call runs it alone, and its fault denies a TaskStop and any shell call its stop words select.
A floor member whose git reads run past `BUDGET_S` from the dispatch's start denies (a hook the client kills on its
timeout would let the call run unjudged).

The chain runs inside `_git_commit.memoized()`: every sub-hook reads the same repo state, so a
read-only git query they repeat spawns git once per tool call.

Wired in: settings.json hooks.PreToolUse, matcher "Bash|PowerShell|Monitor|TaskStop", through hooks/pre_bash.py.
"""

import contextlib
import importlib
import io
import json
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    _bootstrap = '_git_commit'
    import _git_commit  # noqa: E402

    _bootstrap = '_shell_lex'  # _git_commit tolerates its absence; no shell call can be judged without it
    if _git_commit.LEXER_ERROR:
        raise ImportError("_shell_lex: " + _git_commit.LEXER_ERROR)

    _bootstrap = 'sidecar_argv'  # _git_commit tolerates its absence; the shell guards need it
    import sidecar_argv  # noqa: E402,F401

    _bootstrap = '_optional_hooks'
    import _optional_hooks  # noqa: E402

    _bootstrap = '_git_run'
    import _git_run  # noqa: E402
except Exception as exc:
    if __name__ != "__main__":
        raise  # imported by the floor entry (hooks/pre_bash.py), which denies on this fault
    if sys.argv[1:2] == ["--commit-msg"]:  # git's commit-msg hook: a fault refuses the commit
        sys.stderr.write('BLOCKED commit -- %s failed to import (%s: %s), so the commit-time checks cannot run. '
                         'Repair %s/%s.py, then commit again.\n' % (_bootstrap, type(exc).__name__, exc,
                                                                     'tools' if _bootstrap == 'sidecar_argv' else
                                                                     'hooks', _bootstrap))
        sys.exit(1)
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


try:
    import _hook_state
    from _hook_state import give_back
except Exception:  # noqa: BLE001 - without it a dropped line keeps its once-key spent; no guard depends on it
    _hook_state = None

    def give_back(start, stop):
        return None


def _spent_mark():
    return _hook_state.spent_mark() if _hook_state else 0


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
        if not isinstance(payload, dict):
            return 0
        if payload.get("tool_name") == "TaskStop":
            if name not in _STOP_MEMBERS:
                return 0
        elif payload.get("tool_name") not in ("Bash", "PowerShell"):
            return 0
        else:
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


# What a broken kill_guard would have judged on a shell call: a stop, a window close, or a scratch script it reads.
_STOP_WORDS = re.compile(r"kill|stop-process|spps|wmic|terminate|closemainwindow|wm_close|sc_close|0xf060"
                         r"|win32_process|remove-(?:cim|wmi)(?:instance|object)"
                         r"|(?:post|send)message|\.claude[/\\]scratch[/\\]", re.I)
# Members that judge TaskStop calls; a TaskStop runs these alone.
_STOP_MEMBERS = frozenset({'kill_guard'})


def _stops(command, _cwd):
    return bool(_STOP_WORDS.search(command))


def _commits(command, cwd):
    return bool(_git_commit.commit_invocations(command, cwd))


def _import_error(exc):
    """The error and the file it was raised in, so a deny names the module to repair (a broken shared helper fails
    the import of every member that loads it)."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = getattr(exc, "filename", None) if isinstance(exc, SyntaxError) else (frames[-1].filename if frames else None)
    return "%s: %s%s" % (type(exc).__name__, exc, " (in %s)" % os.path.basename(where) if where else "")


# Metadata must survive a member's failed import.
_FLOOR_MEMBERS = {
    'baseline_classification_guard': (_commits, 'git commit'),
    'dangerous_shell_guard': (lambda _command, _cwd: True, 'destructive-command check'),
    'kill_guard': (_stops, 'stop/kill check'),
    'editor_live_build_guard': (lambda command, _cwd: 'dotnet' in command or '-ignoreeditor' in command.lower(),
                                'build into a checkout'),
    # Paid spend: the dispatch-doctrine and pin checks stand in front of every sidecar launch.
    'sidecar_dispatch_context': (lambda command, _cwd: bool(sidecar_argv.launcher_invocations(command)),
                                 'sidecar launch'),
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
editor_live_build_guard = _optional_hooks.load('editor_live_build_guard', on_error=_failed_import)
sidecar_dispatch_context = _load('sidecar_dispatch_context')
dangerous_shell_guard = _load('dangerous_shell_guard')
kill_guard = _load('kill_guard')
(cloud_test_enforcer, prototype_containment_guard,
 provisional_totality_guard, tres_nullstrip_guard, tres_script_strip_guard) = _optional_hooks.load(
    'cloud_test_enforcer', 'prototype_containment_guard',
    'provisional_totality_guard', 'tres_nullstrip_guard', 'tres_script_strip_guard', on_error=_failed_import)
approving_gates = _load('approving_gates')
unbounded_scan_guard = _load('unbounded_scan_guard')
harness_shell_write_guard = _load('harness_shell_write_guard')
rule_number_guard = _load('rule_number_guard')
foreground_wait_guard = _optional_hooks.load('foreground_wait_guard', on_error=_failed_import)
run_call_router = _optional_hooks.load('run_call_router', on_error=_failed_import)
baseline_classification_guard = _load('baseline_classification_guard')
bash_length_guard = _load('bash_length_guard')
correction_record_guard = _load('correction_record_guard')
_APPROVING_GATES_IMPORT_ERROR = _IMPORT_ERRORS.get('approving_gates')
_BASELINE_GUARD_IMPORT_ERROR = _IMPORT_ERRORS.get('baseline_classification_guard')


# The members whose verdict reads the index a commit publishes, with their argv tail: each judges the last commit of
# a Bash payload from the staged content (`_commit_scope.publication`). The chain below runs them on the index as the
# call finds it; git's commit-msg hook runs the same list on the index git is writing (`commit_time`), so an index
# write earlier in the same call is judged there (D-IW). dangerous_shell_guard's own index checks have their
# commit-time half in `commit_msg_check`, which `commit_time` runs first. A new member that reads the index joins
# this list, and both points run it.
COMMIT_JUDGES = (
    ("baseline_classification_guard", ()),
    ("tres_script_strip_guard", ("--hook",)),
    ("tres_nullstrip_guard", ("--hook",)),
    ("prototype_containment_guard", ("--hook",)),
    ("provisional_totality_guard", ("--hook",)),
)
_JUDGES = tuple((module, argv) for module, argv in (
    ({"baseline_classification_guard": baseline_classification_guard,
      "tres_script_strip_guard": tres_script_strip_guard, "tres_nullstrip_guard": tres_nullstrip_guard,
      "prototype_containment_guard": prototype_containment_guard,
      "provisional_totality_guard": provisional_totality_guard}[name], argv) for name, argv in COMMIT_JUDGES)
    if module is not None)

# (module, argv tail). Normalizers first, then guards and approvers.
HOOKS = (
    (_git_commit, ()),
    (cloud_test_enforcer, ()),
    (foreground_wait_guard, ()),
    (dangerous_shell_guard, ()),
    (kill_guard, ()),
    (editor_live_build_guard, ()),
    *_JUDGES,
    (rule_number_guard, ()),
    (compound_cd_approver, ()),
    (unbounded_scan_guard, ()),
    (bash_length_guard, ()),
    (correction_record_guard, ()),
    (harness_shell_write_guard, ()),
    (sidecar_dispatch_context, ()),
    (approving_gates, ()),
    # Last: it rewrites the command through updatedInput, so every deny above must win first.
    (bash_backslash_fidelity, ()),
)
HOOKS = tuple((module, argv_tail) for module, argv_tail in HOOKS if module is not None)

# Hooks the v1 M1 cut unwired from this dispatcher. Their files stay until the cutover deletes them; naming them here
# keeps `tools/layer_closure.py`'s registration check seeing each as reached. Nothing loads or runs them.
UNWIRED = (
    "duplicate_commit_check",
    "gate_cadence_guard",
    "hand_pricing_advisory",
    "routing_audit",
    "triage_commit_check",
    "wide_test_guard",
)

_DECISION_RANK = {"deny": 3, "ask": 2, "allow": 1}
_NOTHING_RAN = "Nothing in this command ran."
# The floor's git budget from the dispatch's start, below the registered 75 s hook timeout with room for one Python
# start and the process-table reads kill_guard bounds itself. Only the floor members' git reads are bounded: a member
# outside the floor (a sidecar preflight's approver after it, say) keeps its own budget, as before the port.
BUDGET_S = 40.0
OUT_OF_TIME = ("BLOCKED call (CLAUDE.core.md §Shell Discipline) — the loss floor's git reads ran past its %d s budget, "
               "so the call was not judged (a loaded machine or a stalled git). Re-issue the same call; if it "
               "repeats, find what stalls git in that checkout.")
UNREADABLE = ("BLOCKED call (CLAUDE.core.md §Shell Discipline) — the loss floor could not read its input. "
              "Re-issue the same call; if it repeats, report it.")


def _denied(text):
    """A deny reason with the no-partial-run line once: a denied call runs no part of its chain."""
    return text if text.rstrip().endswith(_NOTHING_RAN) else text.rstrip() + "\n" + _NOTHING_RAN


class _Capture(io.StringIO):
    """StringIO that tolerates the stream calls a hook makes on a real console handle."""

    def reconfigure(self, **_kwargs):
        pass


def run_hook(module, argv_tail, raw_payload):
    """Run one sub-hook's main() in-process. Returns (exit_code, stdout, stderr). The process directory comes back
    after it: a member that moves into the repository a commit targets moves no later member."""
    saved = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    try:
        here = os.getcwd()
    except OSError:
        here = None
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
        if here is not None:
            try:
                os.chdir(here)
            except OSError:
                pass
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
    """Run the chain and return (exit_code, stdout_text, stderr_text) for the dispatcher process. A floor member whose
    git reads ran out of `BUDGET_S` (counted from here) denies the call it left unjudged."""
    with _git_commit.memoized():
        return _dispatch(raw_payload, hooks, time.monotonic() + BUDGET_S)


def _bounded(module):
    """True for a member whose verdict the floor must not lose: the normalizer, a FAIL_CLOSED member, a floor member."""
    return module is _git_commit or getattr(module, 'FAIL_CLOSED', False) or module.__name__ in _FLOOR_MEMBERS


def _dispatch(raw_payload, hooks, ends):
    decision, reason, contexts, extra, user_stderr = None, None, [], {}, []
    started = _spent_mark()  # before the router: its once-lines drop with every other hook's on a block
    payload = json.loads(raw_payload)
    stop = payload.get('tool_name') == 'TaskStop'
    if stop:
        hooks = tuple((module, argv) for module, argv in hooks if module.__name__ in _STOP_MEMBERS)
    try:
        updated, routing_context = (run_call_router.route(payload) if run_call_router is not None and not stop
                                    else ({}, []))
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
        before = _spent_mark()
        with (_git_run.deadline(max(0.0, ends - time.monotonic())) if _bounded(module)
              else contextlib.nullcontext()) as clock:
            code, out, err = run_hook(module, argv_tail, raw_payload)
        if clock is not None and clock.spent:
            give_back(started, before)
            return 2, "", _denied(OUT_OF_TIME % BUDGET_S)
        if code == 2:
            block = err.rstrip() if err.strip() else "%s blocked the call." % module.__name__
            give_back(started, before)
            return 2, "", _denied(block)
        floor = getattr(module, 'FAIL_CLOSED', False) or module.__name__ in _FLOOR_MEMBERS
        if code != 0 and floor:
            give_back(started, before)
            return 2, "", _denied("%s is a floor guard and failed (exit %s), so the call is blocked until it runs "
                                  "cleanly. Fix the guard; its error:\n%s"
                                  % (module.__name__, code, err.strip()[-1500:]))
        if code != 0:
            fault = "[pre_bash_dispatch] %s exited %s: %s" % (module.__name__, code, err.strip()[-1500:])
            user_stderr.append(fault)
            contexts.append(fault + "; its checks did not run. Repair .claude/hooks/%s.py." % module.__name__)
            continue
        hso = _hook_output(out)
        pd = hso.get("permissionDecision")
        if pd == "deny":
            # As an exit-2 block: the call runs nothing, so no earlier rewrite or context rides on the deny.
            give_back(started, before)
            block = hso.get("permissionDecisionReason")
            block = block if isinstance(block, str) and block.strip() else "%s denied the call." % module.__name__
            return 0, json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": _denied(block)}}), ""
        ctx = hso.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            contexts.append(ctx)
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

    # Throttled orphaned-search reaper. After the chain so a block or deny (above) never spends a snapshot whose
    # advisory it could not deliver, and nothing runs between a deny decision and its output.
    if not stop:
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
            hso["permissionDecisionReason"] = reason
    if contexts:
        hso["additionalContext"] = "\n\n".join(contexts)
    hso.update(extra)
    stdout_text = json.dumps({"hookSpecificOutput": hso}) if len(hso) > 1 else ""
    return 0, stdout_text, "\n".join(user_stderr)


def decide(payload):
    """The hook output for one PreToolUse payload (a dict), or None to allow: the floor entry's call. A block is a
    deny carrying the blocking hook's text."""
    return _decide(payload)[0]


def decide_text(raw):
    """(hook output or None, stderr text for the user) for the stdin text of a hook call; text that is not a JSON
    object denies."""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    return _decide(payload)


def _decide(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input", {}), dict):
        return _deny_output(UNREADABLE), ""
    code, out, err = dispatch(json.dumps(payload), HOOKS)
    if code == 2:
        return _deny_output(err.strip() or "The loss floor blocked the call. " + _NOTHING_RAN), ""
    try:
        parsed = json.loads(out) if out.strip() else None
    except ValueError:
        parsed = None
    return (parsed if isinstance(parsed, dict) else None), err


def commit_time(message_path, environ=None, cwd=None):
    """The refusal for the commit git is about to write, or None: git's commit-msg hook calls it through
    `.claude/githooks/commit-msg`. The floor's own index checks run first (`dangerous_shell_guard.commit_msg_check`),
    then every `COMMIT_JUDGES` member judges `git commit` of the index git is writing (its `GIT_INDEX_FILE`), as the
    chain judges it, so an index write earlier in the same Bash call cannot reach a commit unjudged (D-IW). The commit
    that closes a merge passes, as `commit_msg_check` passes it. A member's fault refuses where the chain would deny
    on it (a floor member), and is a note otherwise."""
    environ = os.environ if environ is None else environ
    cwd = cwd or os.getcwd()
    if isinstance(dangerous_shell_guard, _ImportStub):
        return ("BLOCKED commit: dangerous_shell_guard failed to import (%s), so the peer-revert check cannot run. "
                "Repair .claude/hooks/dangerous_shell_guard.py, then commit again." % dangerous_shell_guard.error)
    why = dangerous_shell_guard.commit_msg_check(message_path, environ, cwd)
    if why:
        return why
    index = environ.get("GIT_INDEX_FILE") or ""
    index = os.path.abspath(os.path.join(cwd, index)) if index else ""
    if dangerous_shell_guard.merge_in_progress(cwd, {"GIT_INDEX_FILE": index} if index else None):
        return None
    command = ("GIT_INDEX_FILE='%s' git commit" % index.replace("\\", "/").replace("'", "'\\''")) if index \
        else "git commit"
    raw = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd,
                      "hook_event_name": "PreToolUse"})
    with _git_commit.memoized():
        for module, argv_tail in _JUDGES:
            code, out, err = run_hook(module, argv_tail, raw)
            hso = _hook_output(out)
            if code == 2 or hso.get("permissionDecision") == "deny":
                return (err if code == 2 else hso.get("permissionDecisionReason") or "").strip() or (
                    "BLOCKED commit: %s denied it." % module.__name__)
            if code != 0 and (getattr(module, 'FAIL_CLOSED', False) or module.__name__ in _FLOOR_MEMBERS):
                return ("BLOCKED commit: the commit-time check %s failed (exit %s), so the commit is refused until it "
                        "runs cleanly. Repair .claude/hooks/%s.py; its error:\n%s"
                        % (module.__name__, code, module.__name__, err.strip()[-1500:]))
            if code != 0:
                sys.stderr.write("[commit-msg] %s exited %s, so its commit-time check did not run: %s\n"
                                 % (module.__name__, code, err.strip()[-500:]))
    return None


def _commit_msg_main(argv):
    try:
        why = commit_time(argv[0] if argv else "")
    except Exception as exc:  # noqa: BLE001 - a floor check: a fault refuses the commit
        why = ("BLOCKED commit: the commit-time checks crashed (%s: %s). Repair .claude/hooks/pre_bash_dispatch.py "
               "commit_time until .claude/tests/floor/test_floor_commit_msg.py passes, then commit again."
               % (type(exc).__name__, exc))
    if why:
        sys.stderr.write(why.rstrip() + "\n")
        return 1
    return 0


def _deny_output(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main():
    if sys.argv[1:2] == ["--commit-msg"]:
        sys.exit(_commit_msg_main(sys.argv[2:]))
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
