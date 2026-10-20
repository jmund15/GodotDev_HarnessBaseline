#!/usr/bin/env python3
"""PreToolUse SendMessage guard: a message reaches a local peer session only while it is busy.

Home: CLAUDE.core.md §Core Principles. The recipient resolves through the runtime's local
registry (~/.claude/sessions/<pid>.json: name, sessionId, messagingSocketPath, status) by name,
`name [ref]` sessionId prefix, or socket path. Only a single match whose status is not `busy` is
denied; an unresolved recipient (a subagent id, a remote session) or an ambiguous name passes.
Malformed input and hook errors fail open. Proof: tests/test_peer_message_guard.py.
"""
import json
import re
import sys
from pathlib import Path

_REF = re.compile(r"^(?P<name>.*?)\s*\[(?P<ref>[0-9a-f]+)\]\s*$")

REASON = (
    "{name} is {status}. A message wakes it and re-reads its whole context at uncached cost, "
    "most likely on finished work. Leave the fact in the artifact it reads when it next acts on "
    "the subject (its worklog item, plan file or Close doc). Rule: CLAUDE.core.md §Core Principles."
)


def _registry():
    rows = []
    for path in (Path.home() / ".claude" / "sessions").glob("*.json"):
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


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("tool_name") != "SendMessage":
            return 0
        peer = resolve((payload.get("tool_input") or {}).get("to"), _registry())
        if peer is None or peer.get("status") == "busy":
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(name=peer["name"], status=peer.get("status") or "not busy")}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
