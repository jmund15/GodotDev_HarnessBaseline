#!/usr/bin/env python3
"""PreToolUse SendMessage guard: a message reaches a local peer session or subagent only while its cache is warm.

Home: CLAUDE.core.md §Core Principles. The recipient resolves through the runtime's local
registry in every declared login (`model_registry.login_dirs`; <login>/sessions/<pid>.json: name, sessionId,
messagingSocketPath, status) by name, `name [ref]` sessionId prefix, or socket path. A single match passes while
it is `busy` or its transcript (<login>/projects/*/<sessionId>.jsonl) was written within WARM_SEC; any
other single match is denied. A recipient that is this session's subagent id passes while its transcript
(<login>/projects/*/<session_id>/subagents/agent-<id>.jsonl) was written within SUBAGENT_WARM_SEC.
Any other recipient (a subagent addressed by name, a remote session) or an ambiguous name passes.
Sidecar children register no inbox, so no message reaches them.
`--ages` prints each local peer's status, minutes since its last turn and warm/cold, the fact the rule asks an
agent to weigh before a send (ListAgents shows no last-turn time).
Malformed input and hook errors fail open. Proof: tests/test_peer_message_guard.py.
"""
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import ORCHESTRATOR_CHECKIN_SECONDS, SUBAGENT_CACHE_WARM_SECONDS  # noqa: E402

# An idle one-hour-cache session is compacted at about 54 min (_hook_state), so its cache is warm until the
# watcher's 50-minute check-in.
WARM_SEC = ORCHESTRATOR_CHECKIN_SECONDS
SUBAGENT_WARM_SEC = SUBAGENT_CACHE_WARM_SECONDS
_AGENT_ID = re.compile(r"^a[0-9a-f]{8,}$")

_REF = re.compile(r"^(?P<name>.*?)\s*\[(?P<ref>[0-9a-f]+)\]\s*$")

REASON = (
    "{name} is {status} and its last turn ended {age}, so its 1h prompt cache is not known warm. A message wakes it "
    "and re-reads its whole context uncached. Leave the fact in the artifact it reads when it next "
    "acts on the subject (its worklog item, plan file or Close doc). Rule: CLAUDE.core.md §Core Principles."
)

SUBAGENT_REASON = (
    "Subagent {agent} last wrote {age} min ago, so its 5m prompt cache is not known warm. A message resumes it and "
    "re-reads its whole context uncached. Put the follow-up in a fresh job's brief with the evidence "
    "path. Rule: CLAUDE.core.md §Core Principles."
)


def _logins():
    """Every declared login directory; none when the registry cannot load, so the guard fails open."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from model_registry import login_dirs
        return [Path(p) for p in login_dirs()]
    except Exception:
        return []


def _registry():
    rows = []
    for path in (p for login in _logins() for p in login.glob("sessions/*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("name"):
            rows.append(doc)
    return rows


def resolve(to, rows):
    to = (to or "").strip()
    if not to:
        return None
    match = _REF.match(to)
    if match:
        hits = [r for r in rows if str(r.get("sessionId", "")).startswith(match.group("ref"))]
    else:
        socket = to[4:] if to.startswith("uds:") else to
        hits = [r for r in rows if r.get("messagingSocketPath") == socket] or \
               [r for r in rows if r.get("name") == to]
    return hits[0] if len(hits) == 1 else None


def idle_seconds(session_id):
    """Seconds since the peer's transcript was last written; None when no transcript is found."""
    if not session_id:
        return None
    stamps = [p.stat().st_mtime for login in _logins()
              for p in login.glob(f"projects/*/{glob.escape(session_id)}.jsonl")]
    return time.time() - max(stamps) if stamps else None


def subagent_idle_seconds(parent_session, agent_id):
    """Seconds since this session's subagent transcript was last written; None when it is not one."""
    if not parent_session or not _AGENT_ID.match(agent_id or ""):
        return None
    stamps = [p.stat().st_mtime for login in _logins()
              for p in login.glob(f"projects/*/{glob.escape(parent_session)}/subagents/agent-{agent_id}.jsonl")]
    return time.time() - max(stamps) if stamps else None


def ages():
    """One line per local peer: name, status, minutes since its last turn, warm or cold."""
    for row in sorted(_registry(), key=lambda r: str(r.get("name"))):
        idle = idle_seconds(str(row.get("sessionId") or ""))
        warm = row.get("status") == "busy" or (idle is not None and idle < WARM_SEC)
        minutes = "?" if idle is None else str(int(idle // 60))
        print(f"{row['name']}  {row.get('status') or '?'}  last turn {minutes} min ago  {'warm' if warm else 'cold'}")
    return 0


def main():
    if sys.argv[1:] == ["--ages"]:
        return ages()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") != "SendMessage":
            return 0
        to = str((payload.get("tool_input") or {}).get("to") or "").strip()
        peer = resolve(to, _registry())
        if peer is None:
            idle = subagent_idle_seconds(str(payload.get("session_id") or ""), to)
            if idle is not None and idle >= SUBAGENT_WARM_SEC:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": SUBAGENT_REASON.format(agent=to, age=int(idle // 60))}}))
            return 0
        if peer.get("status") == "busy":
            return 0
        idle = idle_seconds(str(peer.get("sessionId") or ""))
        if idle is not None and idle < WARM_SEC:
            return 0
        age = "at an unknown time" if idle is None else f"{int(idle // 60)} min ago"
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(name=peer["name"], status=peer.get("status") or "not busy", age=age)}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
