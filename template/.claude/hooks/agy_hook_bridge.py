#!/usr/bin/env python3
"""Hook: the Antigravity CLI's `.agents/hooks.json` entry point. It runs this project's Claude Code hooks on agy's
events, so every hook registered in `.claude/settings.json` reaches agy with no second list.

    agy_hook_bridge.py PreToolUse      stdin: agy payload (toolCall.name/args); stdout: agy decision JSON
    agy_hook_bridge.py PostToolUse     stdin: agy payload (toolCall, error); stdout: {}
    agy_hook_bridge.py SessionStart    stdout: the startup SessionStart hooks' context as one system message
    agy_hook_bridge.py Stop            stdin: agy payload (continuationActive); stdout: {"decision": "continue", ...} or {}
    agy_hook_bridge.py PreInvocation   stdout: queued hook advisories as a system message, then cleared

PreToolUse maps the agy tool to its Claude tool (TOOLS), runs each registered hook whose matcher matches, and
denies on the first block (exit 2, a `deny`/`ask` permissionDecision or a `block` decision: headless agy cannot
ask). An `updatedInput` maps back into `overwrite`. An `additionalContext` advisory has no agy channel at tool
time, so it queues and reaches the model at the next PreInvocation. PostToolUse runs the PostToolUse hooks the same
way after the call, so state they record (a full SKILL.md read loads that skill for the edit gate) reaches agy too;
the call has run, so their decisions and failures change nothing and only their advisories queue. An unmapped agy tool is allowed here: the seat's
settings and its stop-on-ungoverned-tool run (`tools/agy_seat.py`) govern it. A hook that cannot start, times out
or exits outside {0, 2} denies the call.

Class: floor. It carries every floor guard settings.json registers, so its own failure denies.
Registered in `.agents/hooks.json` by `tools/agy_workspace.py`.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shell_lex import lex  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = os.path.join(ROOT, ".claude", "settings.json")
DEFAULT_TIMEOUT = 60  # Claude Code's own default for a command hook that sets none


def _path(args):
    for key in ("TargetFile", "AbsolutePath", "Path", "FilePath", "File", "file_path", "path"):
        if isinstance(args.get(key), str):
            return args[key]
    return ""


def _bash(args):
    return {"command": args.get("CommandLine") or args.get("Command") or ""}


def _read(args):
    """A line range maps to offset/limit, so a ranged read never counts as a whole-file read."""
    out = {"file_path": _path(args)}
    start, end = args.get("StartLine", args.get("Offset")), args.get("EndLine")
    if start is not None:
        out["offset"] = start
    if end is not None or args.get("Limit") is not None:
        out["limit"] = args.get("Limit") if end is None else max(0, int(end) - int(start or 1) + 1)
    return out


def _write(args):
    return {"file_path": _path(args), "content": args.get("CodeContent", args.get("Content", ""))}


def _pair(d):
    return (str(d.get("Target", d.get("TargetContent", ""))), str(d.get("Replacement", d.get("ReplacementContent", ""))))


def _edit(args):
    """agy 1.5.14 runtime `edit_file` sends Path/Target/Replacement (live capture, 2026-11-21); the documented
    replace tools send TargetContent/ReplacementContent, one pair or a ReplacementChunks list."""
    chunks = args.get("ReplacementChunks")
    pairs = [_pair(c) for c in chunks if isinstance(c, dict)] if isinstance(chunks, list) and chunks else [_pair(args)]
    return {"file_path": _path(args), "old_string": "\n".join(p[0] for p in pairs),
            "new_string": "\n".join(p[1] for p in pairs)}


# agy tool -> (Claude tool, input mapper, the agy arg a mapped `updatedInput` key writes back to)
TOOLS = {
    "run_command": ("Bash", _bash, {"command": "CommandLine"}),
    "read_file": ("Read", _read, {}), "view_file": ("Read", _read, {}),
    "write_to_file": ("Write", _write, {}), "write_file": ("Write", _write, {}),
    "replace_file_content": ("Edit", _edit, {}), "multi_replace_file_content": ("Edit", _edit, {}),
    "sed_file": ("Edit", _edit, {}), "edit_file": ("Edit", _edit, {}),
}


def _registered(tool, event="PreToolUse"):
    """(command line, timeout) for each hook settings.json registers for `event` on `tool`, in order."""
    settings = json.load(open(SETTINGS, encoding="utf-8"))
    out = []
    for group in settings.get("hooks", {}).get(event, []):
        matcher = group.get("matcher") or "*"
        if matcher != "*" and not re.fullmatch(matcher, tool):
            continue
        for hook in group.get("hooks", []):
            if hook.get("type", "command") == "command" and hook.get("command"):
                out.append((hook["command"], hook.get("timeout") or DEFAULT_TIMEOUT))
    return out


def _argv(command):
    native = ROOT.replace("\\", "/")
    text = command.replace("$CLAUDE_PROJECT_DIR", native).replace("${CLAUDE_PROJECT_DIR}", native)
    return [word.value for segment in lex(text) for word in segment.words]


def _queue(conversation):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", conversation or "none")
    return os.path.join(tempfile.gettempdir(), "agy_hook_bridge", safe + ".txt")


def _note(conversation, text):
    path = _queue(conversation)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text.strip() + "\n\n")


def _system(text):
    return {"injectSteps": [{"systemMessage": {"systemMessage": text}}]}


def _session(payload, event, **fields):
    """The Claude hook payload for an agy event: one agy conversation is one Claude session with no transcript."""
    workspace = (payload.get("workspacePaths") or [ROOT])[0]
    return dict({"session_id": "agy-" + str(payload.get("conversationId", "")), "transcript_path": "",
                 "cwd": workspace, "hook_event_name": event, "model": payload.get("modelName", "")}, **fields)


def _tool_session(payload, event, **fields):
    """(Claude tool, write-back map, Claude payload) for an agy tool event, or None for an unmapped tool."""
    call = payload.get("toolCall") or {}
    name, args = call.get("name", ""), call.get("args") or {}
    if name not in TOOLS:
        return None
    tool, mapper, back = TOOLS[name]
    claude = _session(payload, event, tool_name=tool, tool_input=mapper(args), **fields)
    if args.get("Cwd"):
        claude["cwd"] = args["Cwd"]
    return tool, back, claude


def _run(command, timeout, claude):
    """(exit code, stdout, stderr) of one hook, or None when it cannot start or times out."""
    try:
        run = subprocess.run(_argv(command), input=json.dumps(claude), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", env=dict(os.environ, CLAUDE_PROJECT_DIR=ROOT), cwd=claude["cwd"],
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.returncode, run.stdout, run.stderr


def _json(text):
    try:
        out = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def pre_tool_use(payload):
    mapped = _tool_session(payload, "PreToolUse")
    if not mapped:
        return {"decision": "allow"}
    tool, back, claude = mapped
    overwrite = {}
    for command, timeout in _registered(tool):
        label = os.path.basename(_argv(command)[1]) if len(_argv(command)) > 1 else command
        got = _run(command, timeout, claude)
        if got is None:
            return {"decision": "deny", "reason": f"project guard {label} did not run (could not start or timed out); "
                                                  "the call is denied until it can"}
        rc, stdout, stderr = got
        if rc == 2:
            return {"decision": "deny", "reason": (stderr or stdout).strip() or f"denied by {label}"}
        if rc != 0:
            return {"decision": "deny", "reason": f"project guard {label} failed (exit {rc}); the call is "
                                                  "denied until it runs clean"}
        out = _json(stdout)
        spec = out.get("hookSpecificOutput") or {}
        if spec.get("permissionDecision") in ("deny", "ask") or out.get("decision") == "block":
            return {"decision": "deny",
                    "reason": spec.get("permissionDecisionReason") or out.get("reason") or f"denied by {label}"}
        if spec.get("additionalContext"):
            _note(payload.get("conversationId"), spec["additionalContext"])
        for key, value in (spec.get("updatedInput") or {}).items():
            if key in back:
                overwrite[back[key]] = value
                claude["tool_input"][key] = value
    return {"decision": "allow", "overwrite": overwrite} if overwrite else {"decision": "allow"}


def post_tool_use(payload):
    mapped = _tool_session(payload, "PostToolUse", tool_response={"is_error": bool(payload.get("error"))})
    if not mapped:
        return {}
    tool, _, claude = mapped
    for command, timeout in _registered(tool, "PostToolUse"):
        got = _run(command, timeout, claude)
        spec = (_json(got[1]).get("hookSpecificOutput") or {}) if got and got[0] == 0 else {}
        if isinstance(spec, dict) and spec.get("additionalContext"):
            _note(payload.get("conversationId"), spec["additionalContext"])
    return {}


def session_start(payload):
    """The startup SessionStart hooks' context (a delegate's rails among it), injected once as a system message."""
    claude = _session(payload, "SessionStart", source="startup")
    parts = []
    for command, timeout in _registered("startup", "SessionStart"):
        got = _run(command, timeout, claude)
        if not got or got[0] != 0 or not got[1].strip():
            continue
        out = _json(got[1])
        text = (out.get("hookSpecificOutput") or {}).get("additionalContext", "") if out else got[1]
        if text and text.strip():
            parts.append(text.strip())
    return _system("\n\n".join(parts)) if parts else {}


def stop(payload):
    """The Stop hooks, run as at a Claude child's stop: a block keeps agy going with its reason. A hook that cannot
    run lets the job stop, since a broken check that kept the loop alive would spend quota with no end."""
    claude = _session(payload, "Stop", stop_hook_active=bool(payload.get("continuationActive")))
    for command, timeout in _registered("", "Stop"):
        got = _run(command, timeout, claude)
        if not got:
            continue
        rc, out, err = got
        if rc == 2:
            return {"decision": "continue", "reason": (err or out).strip() or "a project Stop hook blocked the stop"}
        decided = _json(out) if rc == 0 else {}
        if decided.get("decision") == "block":
            return {"decision": "continue", "reason": decided.get("reason") or "a project Stop hook blocked the stop"}
    return {}


def pre_invocation(payload):
    path = _queue(payload.get("conversationId"))
    try:
        text = open(path, encoding="utf-8").read().strip()
        os.remove(path)
    except OSError:
        return {}
    return _system("Project hook notes:\n" + text) if text else {}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if event == "PreToolUse":
        try:
            result = pre_tool_use(payload)
        except Exception as exc:  # a broken bridge must not wave a call through
            result = {"decision": "deny", "reason": f"agy_hook_bridge failed ({exc.__class__.__name__}: {exc})"}
    elif event == "PostToolUse":
        try:
            result = post_tool_use(payload)
        except Exception:  # the call already ran; a broken record step must not fail agy's turn
            result = {}
    elif event == "PreInvocation":
        result = pre_invocation(payload)
    elif event == "SessionStart":
        try:
            result = session_start(payload)
        except Exception:  # context is lost, but the job still runs under its PreToolUse guards
            result = {}
    elif event == "Stop":
        try:
            result = stop(payload)
        except Exception:
            result = {}
    else:
        result = {}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
