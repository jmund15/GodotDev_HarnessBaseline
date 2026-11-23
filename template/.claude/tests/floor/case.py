"""The one call a floor table row makes: a PreToolUse payload through `_floor.decide`, read back as a Decision.

    decide(payload)                      -> Decision(kind, reason, updated)
    bash(command, cwd=None, **payload)   -> Decision for a Bash call
    taskstop(task_id, transcript)        -> Decision for a TaskStop call

`kind` is "allow" (no output), "deny", "ask" or "rewrite" (an `updatedInput` the client runs instead).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str = ""
    updated: dict | None = None

    @property
    def denied(self):
        return self.kind == "deny"


def derived(sid, *entries):
    """A `derived-from` transcript row: `entries` are (session id, via) pairs, oldest first."""
    return {"type": "derived-from", "v": 1, "sessionId": sid,
            "sessions": [{"type": "claude_code_session", "id": i, "via": v} for i, v in entries]}


def read(output):
    """A Decision from the hook output dict `_floor.decide` returns (None is allow)."""
    if not output:
        return Decision("allow")
    hso = output.get("hookSpecificOutput") or {}
    decision = hso.get("permissionDecision")
    if decision in ("deny", "ask"):
        return Decision(decision, hso.get("permissionDecisionReason") or "")
    if hso.get("updatedInput") is not None:
        return Decision("rewrite", "", hso["updatedInput"])
    return Decision("allow", hso.get("additionalContext") or "")


def decide(payload):
    import _floor
    return read(_floor.decide(payload))


def bash(command, cwd=None, tool="Bash", **extra):
    payload = {"tool_name": tool, "tool_input": {"command": command}, "hook_event_name": "PreToolUse"}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    payload.update({k: (str(v) if k in ("transcript_path",) and v is not None else v) for k, v in extra.items()})
    return decide(payload)


def taskstop(task_id, transcript_path):
    return decide({"tool_name": "TaskStop", "tool_input": {"task_id": task_id},
                   "transcript_path": None if transcript_path is None else str(transcript_path),
                   "hook_event_name": "PreToolUse"})
