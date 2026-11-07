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
  0. expand_args_file           `args: {argsFile: <path>}` expands from a repo-relative JSON file under the
                               session's own `.claude/scratch/`, so every later member reads the real args. A
                               Workflow script has no filesystem, so the hook, not the engine, reads it.
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
  jobsFile        a Workflow call whose `args.jobsFile` names a file under .claude/scratch/ is expanded first (the
                  engines cannot read files); a refused file denies alone, since no member can read the call.
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
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _inprocess_hook import hook_output, run_hook  # noqa: E402

_ROLE_GATE = "subagent_dispatch_guard"
_RUNS_WHEN_ALLOWED = "readonly_marker_arm"
_FAIL_CLOSED_ON_WORKFLOW = "workflow_provider_guard"
_DECISION_RANK = {"deny": 3, "ask": 2, "allow": 1}
_ARGS_FILE_NEXT = ("write the run's args with `python3 .claude/tools/lens_briefs.py prepare --spec <spec.json> "
                   "--out <run dir>`, then pass only `{argsFile: \"<run dir>/args.json\"}`")


def _session_root(payload):
    """The checkout the session runs in: its own `cwd` first, so a lane worktree reads its own scratch."""
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).resolve() if env else Path(__file__).resolve().parents[2]


def _args_deny(reason):
    return {"permissionDecision": "deny", "permissionDecisionReason": reason}


def expand_args_file(payload, root=None):
    """Expand `tool_input.args = {argsFile: <path>}` to the file's JSON object, or deny.

    Returns None when the call does not use the member, so a call without `argsFile` is untouched. The path
    is repository-relative under the session's own `.claude/scratch/`; containment is decided after
    `realpath` and `normcase`, so a junction or symlink that resolves outside scratch denies and a
    case-different spelling of a real scratch file passes."""
    if payload.get("tool_name") != "Workflow":
        return None
    tool_input = payload.get("tool_input") or {}
    args = tool_input.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(args, dict) or "argsFile" not in args:
        return None
    given = args.get("argsFile")
    if set(args) != {"argsFile"}:
        return _args_deny(f"`args` carries argsFile beside other keys ({', '.join(sorted(set(args) - {'argsFile'}))}); "
                          f"pass argsFile alone. {_ARGS_FILE_NEXT}")
    if not isinstance(given, str) or not given.strip():
        return _args_deny(f"argsFile must be a non-empty repository-relative path. {_ARGS_FILE_NEXT}")
    if "\\" in given:
        return _args_deny(f"argsFile {given!r} uses backslashes; write it repository-relative with forward "
                          f"slashes. {_ARGS_FILE_NEXT}")
    if Path(given).is_absolute() or (len(given) > 1 and given[1] == ":"):
        return _args_deny(f"argsFile {given!r} is absolute; it must be relative to the session root. {_ARGS_FILE_NEXT}")
    base = Path(root).resolve() if root else _session_root(payload)
    scratch = (base / ".claude" / "scratch").resolve()
    target = (base / given).resolve()
    inside = os.path.normcase(str(target)).startswith(os.path.normcase(str(scratch)) + os.sep)
    if not inside:
        return _args_deny(f"argsFile {given!r} resolves to {target.as_posix()}, outside the session's own "
                          f"{scratch.as_posix()}; an args file lives under this checkout's .claude/scratch/. "
                          f"{_ARGS_FILE_NEXT}")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _args_deny(f"argsFile {given!r} is unreadable: {exc}. {_ARGS_FILE_NEXT}")
    try:
        expanded = json.loads(text)
    except ValueError as exc:
        return _args_deny(f"argsFile {given!r} holds invalid JSON: {exc}. {_ARGS_FILE_NEXT}")
    if not isinstance(expanded, dict):
        return _args_deny(f"argsFile {given!r} holds a {type(expanded).__name__}, not a JSON object. "
                          f"{_ARGS_FILE_NEXT}")
    return {"updatedInput": {"args": expanded}}


class _ExpandArgsFile:
    """The first member: expand `argsFile` before every later member reads `args`."""

    __name__ = "expand_args_file"
    __file__ = "pre_workflow_dispatch.py"

    def main(self):
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        if not isinstance(payload, dict):
            return 0
        result = expand_args_file(payload)
        if result:
            sys.stdout.write(json.dumps({"hookSpecificOutput": dict(result, hookEventName="PreToolUse")}))
        return 0


EXPAND_ARGS_MEMBER = _ExpandArgsFile()



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
    (EXPAND_ARGS_MEMBER, ()),
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


def _jobs_file(tool_input):
    """(refusal, None) when `args.jobsFile` cannot be expanded; (None, args) with the file's jobs in place of
    the path, in the form the call carried them (a JSON string stays a string); (None, None) without one."""
    try:
        import _dispatch_pins
        reason = _dispatch_pins.jobs_file_refusal(tool_input)
        expanded = None if reason else _dispatch_pins.expanded_args(tool_input)
    except Exception as exc:
        return "args.jobsFile could not be checked (%s: %s). Nothing was dispatched." % (type(exc).__name__, exc), None
    if reason or expanded is None:
        return reason, None
    return None, json.dumps(expanded) if isinstance(tool_input.get("args"), str) else expanded


def dispatch(raw_payload, hooks=HOOKS):
    """Run the chain and return (exit_code, stdout_text, stderr_text) for the dispatcher process."""
    payload = json.loads(raw_payload)
    reasons, contexts, user_stderr = [], [], []
    decision, rewritten = None, None
    if payload.get("tool_name") == "Workflow":
        refusal, expanded = _jobs_file(payload.get("tool_input") or {})
        if refusal:
            return 0, json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": refusal + " Correct that and re-issue the call."}}), ""
        if expanded is not None:
            rewritten = dict(payload["tool_input"], args=expanded)
            payload["tool_input"] = rewritten
            raw_payload = json.dumps(payload)
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
