#!/usr/bin/env python3
"""Hook: an auto-mode classifier denial the session left unresolved teaches the next move before the turn ends.

`--record` (PermissionDenied): files `{tag, tool, id, command, paths}` in the session state. A PermissionDenied
hook can only return `retry`, so it cannot speak to the model itself.
`--coach` (Stop): clears the filed denials and blocks the stop once with each unresolved denial's move. A denial
is resolved when the transcript shows a later successful call on its target: the same file for an edit, a file
the denied command names, or the same command. An unreadable transcript resolves nothing. `stop_hook_active`
clears without blocking, so it never loops. No-verdict denials are skipped: Claude Code's own message already
says retry later or move on.

Class: process advisory (it blocks a stop once, never a tool call).
Wired in: settings.base.json hooks.PermissionDenied and hooks.Stop (composed into settings.json).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import state_path, update_json_locked  # noqa: E402
try:
    import approving_gates  # noqa: E402
except Exception:
    approving_gates = None
try:
    import overnight_ask_guard  # noqa: E402
except Exception:
    overnight_ask_guard = None

KEY = "classifier_denials"
_TAG = re.compile(r"\[([^\]]+)\]")
_PATHISH = re.compile(r"[\w.:\\/-]*[\\/][\w.\\/-]*\.\w{1,8}\b")
EDITS = frozenset(("Edit", "Write", "MultiEdit", "NotebookEdit"))
MOVES = {
    "Self-Modification": "the classifier reads the user's messages and CLAUDE.md, not your findings. If this "
                         "edit does what the user asked, the user's answer to a question naming the exact change "
                         "clears it. Otherwise drop consent or provenance text and keep ask-the-user steps.",
    "Security Test Removal": "running or editing a proof is ordinary test work; ask the user one question that "
                             "names the command and says it is a proof run.",
    "Irreversible Local Destruction": "name the exact path, or copy it aside first; never a glob or variable.",
    "Auto-Mode Bypass": "do not retry it another way: drop it, or ask the user one question naming the action.",
}
GENERIC = ("if the owner asked for this action, ask them one question naming it and re-issue it on their yes; "
           "otherwise drop it (CLAUDE.core.md §Shell Discipline, Processes and permissions).")
OVERNIGHT = ("/overnight is armed, so no one can answer: follow commands/overnight.md §Step 2 "
             "to decide or park with the actual blocker and default; the classifier denial still "
             "stands. Record the exact denied command in the Q row evidence; do not ask or bypass it.")


def _record(payload):
    match = _TAG.search(payload.get("reason") or "")
    if not match or not payload.get("session_id"):
        return
    entry = {"tag": match.group(1), "tool": payload.get("tool_name") or "tool", "id": payload.get("tool_use_id")}
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = tool_input.get("command")
    if isinstance(command, str):
        entry["command"] = command
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    entry["paths"] = [target] if isinstance(target, str) else _PATHISH.findall(command or "")
    try:
        entry["gate"] = approving_gates.explain(payload) if approving_gates is not None else None
    except Exception:
        entry["gate"] = None

    def add(state):
        state.setdefault(KEY, []).append(entry)
        del state[KEY][:-20]
    update_json_locked(state_path(payload["session_id"]), add)


def _overnight_armed(payload):
    try:
        return overnight_ask_guard is not None and overnight_ask_guard.armed(
            payload.get("cwd") or None, payload["session_id"])
    except Exception:
        return False


def _norm(path):
    path = str(path).replace("\\", "/").lower().strip("\"'")
    return path[2:] if path.startswith("./") else path


def _same_file(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a.endswith("/" + b) or b.endswith("/" + a))


def _rows_since(transcript_path, tool_use_id):
    """The transcript rows after the call `tool_use_id`, read backward in doubling chunks so the cost follows the
    distance to the denial, not the transcript's size; None when the file or the call cannot be read. The call is
    found by its unescaped `"id":"<id>"` field: a later output that quotes the id carries it escaped."""
    try:
        marker = ('"id":"%s"' % tool_use_id).encode("utf-8")
        size = os.path.getsize(transcript_path)
        chunk = 1 << 20
        with open(transcript_path, "rb") as fh:
            while True:
                start = max(0, size - chunk)
                fh.seek(start)
                data = fh.read()
                at = data.find(marker)
                if at >= 0:
                    break
                if start == 0:
                    return None
                chunk *= 2
    except (OSError, TypeError, AttributeError):
        return None
    rows = []
    for line in data[data.find(b"\n", at) + 1:].splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _later_successes(rows):
    """(name, input) of each tool call in `rows` whose result is not an error."""
    calls, failed = {}, set()
    for row in rows:
        content = (row.get("message") or {}).get("content")
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                calls[part.get("id")] = (part.get("name"), part.get("input") or {})
            elif part.get("type") == "tool_result" and part.get("is_error"):
                failed.add(part.get("tool_use_id"))
    return [call for tid, call in calls.items() if tid not in failed]


def _resolved(entry, transcript_path):
    rows = _rows_since(transcript_path, entry.get("id")) if transcript_path and entry.get("id") else None
    if rows is None:
        return False
    for name, tool_input in _later_successes(rows):
        if name in EDITS and any(_same_file(tool_input.get("file_path") or tool_input.get("notebook_path") or "", p)
                                 for p in entry.get("paths") or ()):
            return True
        if entry.get("command") is not None and tool_input.get("command") == entry["command"]:
            return True
    return False


def _shown(command):
    first = command.strip().splitlines()[0] if command.strip() else ""
    return first[:120] + ("…" if len(first) > 120 or "\n" in command.strip() else "")


def _coach(payload):
    if not payload.get("session_id"):
        return None
    _, pending = update_json_locked(state_path(payload["session_id"]), lambda s: s.pop(KEY, None))
    if not pending or payload.get("stop_hook_active"):
        return None
    pending = [entry for entry in pending if not _resolved(entry, payload.get("transcript_path"))]
    if not pending:
        return None
    unattended = _overnight_armed(payload)
    seen, lines = set(), []
    for entry in pending:
        tag = entry.get("tag") or "?"
        gate = entry.get("gate")
        command = entry.get("command")
        key = (tag, gate, command)
        if key not in seen:
            seen.add(key)
            move = OVERNIGHT if unattended else gate or MOVES.get(tag, GENERIC)
            denied = " Denied: `%s`." % _shown(command) if command is not None else ""
            lines.append("[%s] on %s:%s %s" % (tag, entry.get("tool"), denied, move))
    return {"decision": "block", "reason": "Auto mode denied, and nothing since resolved it: " + " ".join(lines)}


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    if not isinstance(payload, dict):
        sys.exit(0)
    if "--record" in sys.argv:
        _record(payload)
    elif "--coach" in sys.argv:
        out = _coach(payload)
        if out:
            sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # advisory: a broken coach never blocks a stop
