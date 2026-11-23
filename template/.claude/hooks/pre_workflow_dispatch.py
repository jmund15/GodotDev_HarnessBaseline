#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: the one PreToolUse entry for the Workflow|Agent tools. Class: process guard.

Steps, each reading the call the step before left (a rewritten call reaches every later step):
  1. subagent_dispatch_guard   a delegate or sidecar child may not dispatch. A deny ends the chain.   (Workflow, Agent)
  2. expand_args_file          `args: {argsFile: <path>}` reads a JSON object under the session's .claude/scratch/.
  3. expand_jobs_file          `args.jobsFile` reads the jobs (and chains) from .claude/scratch/; the engines cannot
                               read files. A deny ends the chain.                                       (Workflow)
  4. launch checks             tools/launch_check.py, the module `seat.sh` also calls: each job's brief and inputs
                               resolve, a read-only job names no other target, then the project's
                               `dispatch.pre_launch` hook runs for the job.                             (Workflow)
  5. transport                 hooks/workflow_transport.py injects the keys the engines read. It fails closed: a
                               file it cannot read denies the call, since no engine can run without its pins. (Workflow)
  6. workflow_script_lf_guard  rewrites a harness script's CRLF to LF before the permission handler reads it. (Workflow)

PostToolUse(Workflow), the same file: `tools/launch_check.py`'s `dispatch.post_return` hook runs once per job on
route `native` with the job's deliverable (`<spillDir>/<label>.md`), the arguments `seat.sh` passes. A refusal comes
back as `decision: block` naming that job. Nothing runs when the project sets no hook.

Merge: deny reasons, every one in step order, each once; one reason is emitted verbatim, several are numbered.
updatedInput is the call after the last rewrite. A step that raises denies the call and names the step, so a fault
never lets an unchecked Workflow through.

Wired in: settings.json hooks.PreToolUse, matcher "Workflow|Agent"; hooks.PostToolUse, matcher "Workflow".
"""

import importlib
import json
import os
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(CLAUDE_DIR / "tools"))

from _inprocess_hook import hook_output, run_hook  # noqa: E402
import launch_check  # noqa: E402
import workflow_transport  # noqa: E402

JOBS_FILE_ROOT = ".claude/scratch"
ROUTE = "native"  # the route name both project hooks receive; `seat.sh` passes the seat name in its place
OWNER_LIFTS = ("currency", "currencyReason")
ARGS_FILE_NEXT = ("write the run's args to a file in the run dir, "
                  "then pass only `{argsFile: \"<run dir>/args.json\"}`")


def _session_root(payload):
    """The checkout the session runs in: the one its `cwd` lies in first, so a lane worktree reads its own scratch."""
    from _checkout_root import session_checkout
    root = session_checkout(payload)
    if root:
        return Path(root).resolve()
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd).resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).resolve() if env else CLAUDE_DIR.parent


def _args_of(tool_input):
    """(args dict or None, whether the call carried them as a JSON string)."""
    raw = tool_input.get("args")
    if isinstance(raw, str):
        try:
            return json.loads(raw), True
        except ValueError:
            return None, True
    if raw is None:
        return {}, False
    return (raw if isinstance(raw, dict) else None), False


def _with_args(tool_input, args, as_text):
    return dict(tool_input, args=json.dumps(args) if as_text else args)


def _jobs_of(args):
    """Every job-shaped entry of a call: `jobs`, each `chains[].jobs`, and `lenses` (explore)."""
    return workflow_transport.jobs_of(args)


def expand_args_file(payload, root):
    """(reasons, tool_input). `args` carrying only `argsFile` is replaced by that file's JSON object."""
    tool_input = payload.get("tool_input") or {}
    args, as_text = _args_of(tool_input)
    if args is None or "argsFile" not in args:
        return [], None
    given = args.get("argsFile")
    if set(args) != {"argsFile"}:
        return ["`args` carries argsFile beside other keys (%s); pass argsFile alone. %s"
                % (", ".join(sorted(set(args) - {"argsFile"})), ARGS_FILE_NEXT)], None
    if not isinstance(given, str) or not given.strip():
        return ["argsFile must be a non-empty repository-relative path. " + ARGS_FILE_NEXT], None
    if "\\" in given:
        return ["argsFile %r uses backslashes; write it repository-relative with forward slashes. %s"
                % (given, ARGS_FILE_NEXT)], None
    if Path(given).is_absolute() or (len(given) > 1 and given[1] == ":"):
        return ["argsFile %r is absolute; it must be relative to the session root. %s" % (given, ARGS_FILE_NEXT)], None
    scratch = (root / ".claude" / "scratch").resolve()
    target = (root / given).resolve()
    if not os.path.normcase(str(target)).startswith(os.path.normcase(str(scratch)) + os.sep):
        return ["argsFile %r resolves to %s, outside the session's %s; an args file lives under this checkout's "
                ".claude/scratch/. %s" % (given, target.as_posix(), scratch.as_posix(), ARGS_FILE_NEXT)], None
    try:
        expanded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ["argsFile %r could not be read as a JSON object (%s: %s). %s"
                % (given, type(exc).__name__, exc, ARGS_FILE_NEXT)], None
    if not isinstance(expanded, dict):
        return ["argsFile %r holds a %s, not a JSON object. %s" % (given, type(expanded).__name__, ARGS_FILE_NEXT)], None
    return [], _with_args(tool_input, expanded, as_text)


def _jobs_file_load(args, root):
    """(expanded args, None) or (None, refusal) for a call whose args name a `jobsFile`."""
    path = args.get("jobsFile")
    if not isinstance(path, str) or not path.strip():
        return None, "args.jobsFile must be a non-empty path string."
    beside = [k for k in ("jobs", "chains") if k in args]
    if beside:
        return None, ("args.jobsFile sits beside args.%s: a call carries its jobs in the file or inline, never both. "
                      "Send only {jobsFile}." % "/args.".join(beside))
    base = os.path.realpath(str(root))
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    scratch = os.path.normcase(os.path.join(base, *JOBS_FILE_ROOT.split("/"))) + os.sep
    if not os.path.normcase(full).startswith(scratch):
        return None, "args.jobsFile %r is outside %s of this checkout; the fan-out writes it there." % (path, JOBS_FILE_ROOT)
    try:
        with open(full, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "args.jobsFile %r could not be read as JSON (%s: %s)." % (path, type(exc).__name__, exc)
    if not isinstance(loaded, dict):
        return None, "args.jobsFile %r must hold a JSON object of Workflow args." % path
    for holder in [loaded] + _jobs_of(loaded):
        for key in holder:
            if key.startswith("__") or key in OWNER_LIFTS:
                return None, ("args.jobsFile %r holds %r. Injected keys and the currency lifts stay inline and are "
                              "the owner's to give." % (path, key))
    return dict(loaded, **{k: v for k, v in args.items() if k != "jobsFile"}), None


def expand_jobs_file(payload, root):
    tool_input = payload.get("tool_input") or {}
    args, as_text = _args_of(tool_input)
    if args is None or "jobsFile" not in args:
        return [], None
    expanded, refusal = _jobs_file_load(args, root)
    if refusal:
        return [refusal + " Correct that and re-issue the call."], None
    return [], _with_args(tool_input, expanded, as_text)


def _deliverable(job, args, root):
    """The file a job's deliverable lands in: `<spillDir>/<label>.md`, the engine's own spill path. A call with no
    usable spillDir returns inline, so the scratch root stands in and a read-only job may name no target at all."""
    label = job.get("label") or job.get("key") or "(unlabeled)"
    spill = args.get("spillDir")
    spill = spill.replace("\\", "/").rstrip("/") if isinstance(spill, str) else ""
    if not spill.startswith(JOBS_FILE_ROOT) or ".." in spill.split("/"):
        spill = JOBS_FILE_ROOT
    out = "%s/%s.md" % (spill, re.sub(r"[^A-Za-z0-9._-]", "_", str(label)))
    (Path(root) / out).parent.mkdir(parents=True, exist_ok=True)
    return out


def _writes(job):
    """"yes" when the job's kind row writes (`writes` in guards/tool_profiles.json); an unknown kind is read-only."""
    row = workflow_transport.kind_row(job.get("kind"), CLAUDE_DIR)
    return "yes" if row.get("writes") else "no"


def launch_checks(payload, root):
    """Each job's launch checks, then its project pre-launch hook: the same module `seat.sh` calls for a seat."""
    args, _as_text = _args_of(payload.get("tool_input") or {})
    if not args:
        return [], None
    reasons = []
    for job in _jobs_of(args):
        brief = job.get("promptPath")
        label = job.get("label") or job.get("key") or "(unlabeled)"
        if not isinstance(brief, str) or not brief:
            continue
        out = _deliverable(job, args, root)
        causes = launch_check.check(brief, out, str(root), _writes(job))
        if causes:
            reasons.append("%s: %s" % (label, causes[0]))
            continue
        rc, err = launch_check.run_hook(launch_check.PRE_KEY, [ROUTE, brief, out, label], str(root))
        if rc:
            reasons.append("%s: dispatch.pre_launch refused: %s" % (label, err.strip() or "exit %s" % rc))
    return reasons, None


def tool_deltas(payload, root):
    """A job's `tools` delta no variant row covers (or that is malformed) is refused here, before any agent starts."""
    args, _as_text = _args_of(payload.get("tool_input") or {})
    return (workflow_transport.delta_refusals(args, CLAUDE_DIR) if args else []), None


def post_return(payload, root):
    """PostToolUse(Workflow): the project's `dispatch.post_return` hook, once per job, free and deterministic.
    A refusal is that job's failure; the reason reaches the session through `decision: block`. The payload carries the
    call's original `args`, so a file form is expanded the way the pre step expands it."""
    reasons = []
    for expand in (expand_args_file, expand_jobs_file):
        refused, update = expand(payload, root)
        if refused:
            return refused
        if update is not None:
            payload = dict(payload, tool_input=update)
    args, _as_text = _args_of(payload.get("tool_input") or {})
    for job in _jobs_of(args or {}):
        brief = job.get("promptPath")
        label = job.get("label") or job.get("key") or "(unlabeled)"
        if not isinstance(brief, str) or not brief:
            continue
        rc, err = launch_check.run_hook(launch_check.POST_KEY, [ROUTE, brief, _deliverable(job, args, root), label], str(root))
        if rc:
            reasons.append("%s: dispatch.post_return refused: %s" % (label, err.strip() or "exit %s" % rc))
    return reasons


def transport(payload, root):
    tool_input = payload.get("tool_input") or {}
    args, as_text = _args_of(tool_input)
    if args is None:
        return ["the Workflow call's args are not a JSON object; nothing was dispatched."], None
    try:
        injected = workflow_transport.inject(args, cwd=str(root))
    except workflow_transport.TransportError as exc:
        return ["%s. Nothing was dispatched; repair it, then re-issue the call." % exc], None
    return [], _with_args(tool_input, injected, as_text)


def _member(name):
    return importlib.import_module(name)


def _helper(name, payload):
    """A V16 helper run in process: (reasons, tool_input). Its exit 2 is a deny; any other fault denies the call."""
    code, out, err = run_hook(_member(name), [], json.dumps(payload))
    if code == 2:
        return [err.strip() or "%s blocked the call." % name], None
    if code != 0:
        return ["%s failed (exit %s): %s. Repair .claude/hooks/%s.py, then re-issue the call."
                % (name, code, err.strip()[-400:], name)], None
    hso = hook_output(out)
    if hso.get("permissionDecision") == "deny":
        return [hso.get("permissionDecisionReason") or "%s denied the call." % name], None
    update = hso.get("updatedInput")
    # A partial rewrite overlays the call's own tool_input keys.
    return [], (dict(payload.get("tool_input") or {}, **update) if isinstance(update, dict) else None)


def subagent_gate(payload, root):
    return _helper("subagent_dispatch_guard", payload)


def lf_guard(payload, root):
    return _helper("workflow_script_lf_guard", payload)


WORKFLOW_STEPS = (expand_args_file, expand_jobs_file, launch_checks, tool_deltas, transport, lf_guard)
STOP_ON_DENY = (expand_args_file, expand_jobs_file)


def _step(fn, payload, root):
    try:
        return fn(payload, root)
    except Exception as exc:  # a fault denies the call: it never lets an unchecked dispatch through
        return ["%s failed (%s: %s); nothing was dispatched. Repair .claude/hooks/pre_workflow_dispatch.py."
                % (fn.__name__, type(exc).__name__, exc)], None


def _joined(reasons):
    if len(reasons) == 1:
        return reasons[0]
    return ("Dispatch denied: %d preconditions are unmet. Clear all of them, then re-issue the call.\n%s"
            % (len(reasons), "\n".join("%d. %s" % (i, r) for i, r in enumerate(reasons, 1))))


def dispatch(raw_payload):
    """Run the steps and return (exit_code, stdout_text, stderr_text) for the dispatcher process."""
    payload = json.loads(raw_payload)
    root = _session_root(payload)
    if payload.get("hook_event_name") == "PostToolUse":
        return 0, _post_emit(post_return(payload, root)) if payload.get("tool_name") == "Workflow" else "", ""
    reasons, _none = _step(subagent_gate, payload, root)
    if reasons:
        return 0, _emit(reasons, None), ""
    if payload.get("tool_name") != "Workflow":
        return 0, "", ""
    rewritten = None
    for fn in WORKFLOW_STEPS:
        step_reasons, update = _step(fn, payload, root)
        reasons += step_reasons
        if update is not None:
            payload = dict(payload, tool_input=update)
            rewritten = update
        if step_reasons and fn in STOP_ON_DENY:
            break
    return 0, _emit(reasons, rewritten), ""


def _emit(reasons, rewritten):
    result = {"hookEventName": "PreToolUse"}
    if reasons:
        result["permissionDecision"] = "deny"
        result["permissionDecisionReason"] = _joined(reasons)
    elif rewritten is not None:
        result["updatedInput"] = rewritten
    return json.dumps({"hookSpecificOutput": result}) if len(result) > 1 else ""


def _post_emit(reasons):
    return json.dumps({"decision": "block", "reason": _joined(reasons)}) if reasons else ""


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
