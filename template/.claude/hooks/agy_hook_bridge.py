#!/usr/bin/env python3
"""Hook: the Antigravity CLI's `.agents/hooks.json` entry point. It runs the loss floor on agy's shell calls.

    agy_hook_bridge.py PreToolUse   stdin: agy payload (toolCall.name/args); stdout: agy decision JSON
    agy_hook_bridge.py <other>      stdout: {}

PreToolUse maps agy's `run_command` to Claude's `Bash` and runs the floor's one PreToolUse entry, `pre_bash.py`,
on the mapped payload. Its deny comes back to agy as a deny (headless agy cannot ask); an `ask` is a deny too.
A floor entry that cannot start, times out or exits non-zero denies the call. Any other agy tool is allowed: the
floor's matcher names the shell tools only (T1). The other events answer {}: v2 registers no non-floor hook for
agy, and a seat's rails ride its brief (T4).

Class: floor. Registered in `.agents/hooks.json` by `tools/agy_workspace.py`.
"""
import json
import os
import subprocess
import sys

FLOOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pre_bash.py")
TIMEOUT = 60  # agy waits for the bridge; the floor decides in well under this
SHELL = {"run_command": "Bash"}


def _mapped(payload, event):
    """The Claude hook payload for one agy shell call, or None for a tool the floor does not judge."""
    call = payload.get("toolCall") or {}
    tool = SHELL.get(call.get("name", ""))
    if not tool:
        return None
    args = call.get("args") or {}
    cwd = args.get("Cwd") or (payload.get("workspacePaths") or [os.getcwd()])[0]
    return {"session_id": "agy-" + str(payload.get("conversationId", "")), "transcript_path": "",
            "cwd": cwd, "hook_event_name": event, "model": payload.get("modelName", ""),
            "tool_name": tool, "tool_input": {"command": args.get("CommandLine") or args.get("Command") or ""}}


def _floor(claude):
    """(exit code, stdout, stderr) of the floor entry, or None when it cannot start or times out."""
    try:
        run = subprocess.run([sys.executable, FLOOR], input=json.dumps(claude), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=claude["cwd"], timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return run.returncode, run.stdout, run.stderr


def pre_tool_use(payload):
    claude = _mapped(payload, "PreToolUse")
    if claude is None:
        return {"decision": "allow"}
    got = _floor(claude)
    if got is None:
        return {"decision": "deny", "reason": "the loss floor (hooks/pre_bash.py) did not run (could not start or "
                                              "timed out); the call is denied until it can"}
    rc, stdout, stderr = got
    if rc != 0:
        return {"decision": "deny", "reason": "the loss floor (hooks/pre_bash.py) failed (exit %d): %s"
                                              % (rc, (stderr or stdout).strip()[-400:])}
    spec = (json.loads(stdout).get("hookSpecificOutput") or {}) if stdout.strip() else {}
    if spec.get("permissionDecision") in ("deny", "ask"):
        return {"decision": "deny", "reason": spec.get("permissionDecisionReason") or "denied by the loss floor"}
    return {"decision": "allow"}


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
    else:
        result = {}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
