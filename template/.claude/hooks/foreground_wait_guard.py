#!/usr/bin/env python3
"""
Hook: PreToolUse (Bash|PowerShell). The seat launcher arms it through the floor entry, hooks/pre_bash.py (tools/launch_check.py stage).

A never-woken caller (a Workflow subagent, a `claude -p` child, a seat) gets no completion wake, so a backgrounded
job never wakes it. Two checks keep its foreground waits inside its prompt cache:
1. Clamp: a foreground call whose timeout exceeds SUBAGENT_WAIT_MS (270 s) is rewritten to that bound. A Bash command
   gets `echo "EXIT $?"` appended, so the output file the client names carries the caller's done-test.
2. Hand-back: the clamped time of this caller's wait loops, totalled since its last non-wait call, past HANDBACK_MS
   (30 min) denies the next wait with the hand-back text.
A woken caller (the interactive session, an Agent-tool subagent) passes untouched. `never_woken` tells them apart and
run_call_router reads it. Rule homes: guards/rails.md, CLAUDE.core.md §Shell Discipline.
State: the shared per-session file (_hook_state), key `wait_loops`, one entry per caller, swept past STALE_SECONDS.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_state  # noqa: E402
from _shell_lex import shell_code  # noqa: E402

SUBAGENT_WAIT_MS = _hook_state.SUBAGENT_CACHE_WARM_SECONDS * 1000
HANDBACK_MS = 1800000  # wait_log.py HANDBACK_S: past it the parent's one wake costs less than a child's trickle polls
BASH_DEFAULT_MS = 120000
STATE_KEY = "wait_loops"
STALE_SECONDS = 3600  # the longest prompt cache: a caller idle this long re-reads uncached either way
EXIT_LINE = 'echo "EXIT $?"'
# Shell syntax: a bash `until|while|for <cond>; do ... sleep ... done`, or a PowerShell `while (...) { ... Start-Sleep }`.
_LOOP_SLEEP = re.compile(
    r"\b(?:until|while|for)\b[^\n;]*;\s*do\b(?:(?!\bdone\b).)*?\bsleep\b"
    r"|\bwhile\s*\([^\n]*\)\s*\{(?:(?!\}).)*?\bStart-Sleep\b",
    re.IGNORECASE | re.DOTALL,
)
CLAMP_NOTE = (
    "[wait] Clamped this foreground call's timeout from %d to %d ms: a longer call returns to an expired 5-minute "
    "prompt cache and rewrites your whole context (guards/rails.md)."
)
CLAMP_WAIT = (
    " If it is still running then, the client moves it to the background and names its output file; this call now "
    "ends with an `EXIT <code>` line there, so wait in `until grep -q '^EXIT ' <that file>; do sleep 30; done` calls."
)
HANDBACK_REASON = (
    "BLOCKED wait: this caller's foreground loop waits total %d minutes since its last non-wait call, past the %d minutes "
    "after which the parent's one wake costs less than these polls, and no wake reaches a subagent or `claude -p` child. "
    "Return now with the log path, pid, expected end and the steps left after it, so the parent waits for it "
    "(guards/rails.md; the same hand-back `bench wait` gives)."
)


def _caller(payload):
    return str(payload.get("agent_id") or "").strip()


def _agent_tool_subagent(payload):
    """True when the caller's .meta.json sits where the runtime keeps an Agent-tool subagent's:
    <session>/subagents/agent-<id>.meta.json beside the orchestrator's <session>.jsonl. A Workflow job's sits under
    <session>/subagents/workflows/<run>/, and its agent_type is the job's agentType, so the type alone cannot tell
    the two apart."""
    transcript = str(payload.get("transcript_path") or "")
    if not transcript.endswith(".jsonl"):
        return False
    meta = os.path.join(transcript[:-len(".jsonl")], "subagents", "agent-%s.meta.json" % _caller(payload))
    return os.path.isfile(meta)


def never_woken(payload):
    """True when no completion notification reaches this caller for a backgrounded job."""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli":
        return True
    if not _caller(payload):
        return False
    kind = str(payload.get("agent_type") or "").strip()
    return kind in ("", "workflow-subagent") or not _agent_tool_subagent(payload)


def _wait_total_ms(payload, add_ms):
    """Track an unwoken caller's clamped loop-wait time: `add_ms` is added when this call is a loop wait, and any other
    call resets the total. Returns the total BEFORE this call. Fails open to 0 when the state cannot be locked."""
    key = _caller(payload) or "main"
    path = _hook_state.state_path(payload.get("session_id"))
    if not add_ms:
        existing = _hook_state.read_json_salvage(path) if os.path.exists(path) else {}
        if key not in (existing.get(STATE_KEY) or {}):
            return 0

    def update(state):
        loops = state.setdefault(STATE_KEY, {})
        now = time.time()
        for other in [k for k, v in loops.items() if now - float((v or {}).get("at") or 0) > STALE_SECONDS]:
            del loops[other]
        prior = int((loops.get(key) or {}).get("ms") or 0)
        if not add_ms:
            loops.pop(key, None)
        elif prior <= HANDBACK_MS:
            loops[key] = {"ms": prior + add_ms, "at": now}
        return prior

    written, prior = _hook_state.update_json_locked(path, update)
    return prior if written else 0


def _deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    return 0


def _budget(tool_input):
    try:
        return int(tool_input.get("timeout") or BASH_DEFAULT_MS)
    except (TypeError, ValueError):
        return BASH_DEFAULT_MS


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell") or not never_woken(payload):
        return 0
    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if tool_input.get("run_in_background"):
        _wait_total_ms(payload, 0)
        return 0
    budget = _budget(tool_input)
    if _LOOP_SLEEP.search(shell_code(command)):
        spent = _wait_total_ms(payload, min(budget, SUBAGENT_WAIT_MS))
        if spent > HANDBACK_MS:
            return _deny(HANDBACK_REASON % (spent // 60000, HANDBACK_MS // 60000))
    else:
        _wait_total_ms(payload, 0)
    if budget <= SUBAGENT_WAIT_MS:
        return 0
    bash = payload.get("tool_name") == "Bash"
    updated = dict(tool_input, timeout=SUBAGENT_WAIT_MS,
                   command=command.rstrip() + "\n" + EXIT_LINE if bash else command)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "updatedInput": updated,
        "additionalContext": CLAMP_NOTE % (budget, SUBAGENT_WAIT_MS) + (CLAMP_WAIT if bash else ""),
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
