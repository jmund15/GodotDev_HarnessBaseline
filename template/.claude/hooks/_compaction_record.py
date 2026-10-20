#!/usr/bin/env python3
"""What a session transcript records about its compactions, folded incrementally (`_transcript_scan`).

The client writes each compaction as one batch after the SessionStart:compact hooks finish: the
`compact_boundary` row (stamped last), the continuation summary, the `invoked_skills` attachment and the hook
result rows. A SessionStart:compact hook therefore usually sees only the earlier boundaries; a later hook sees
the whole batch. `position` tells the two apart: a boundary followed by a SessionStart hook row or a main-thread
assistant row is a previous one; a boundary followed by neither is the current one, already written.

`invoked_skills` holds the skills the client re-attached after the boundary, each as `{name, path, content}`; a
copy the client cut ends with a notice containing TRUNCATION_NOTICE. Only a whole copy puts the skill back in the
main thread's context; a subagent's context is never this attachment.

A dispatch is a main-thread Workflow call or a Bash call that `sidecar_dispatch_context` reads as a sidecar or
fan-out launch: the calls `model_ladder_gate.doctrine_check` gates.
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
for _path in (HERE, TOOLS):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import _transcript_scan  # noqa: E402

TRUNCATION_NOTICE = "truncated for compaction"
_NOTICE_TAIL_CHARS = 300
_launch_checks = []


def _initial():
    return {"count": 0, "times": [], "settled": True, "attached": False, "whole": [], "dispatches": 0}


def _bare(name):
    return str(name or "").split(":")[-1]


def _whole(skill):
    content = skill.get("content") if isinstance(skill, dict) else None
    return isinstance(content, str) and TRUNCATION_NOTICE not in content.rstrip()[-_NOTICE_TAIL_CHARS:]


def _is_launch(command):
    if not _launch_checks:
        import sidecar_dispatch_context as sdc
        _launch_checks.extend((sdc.sidecar_launch, sdc.fanout_launch))
    return any(check(command) for check in _launch_checks)


def _dispatches(entry):
    content = (entry.get("message") or {}).get("content")
    count = 0
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") == "Workflow":
            count += 1
        elif block.get("name") == "Bash":
            command = (block.get("input") or {}).get("command")
            if isinstance(command, str) and ("sidecar" in command) and _is_launch(command):
                count += 1
    return count


def _feed(state, _index, line):
    boundary = "compact_boundary" in line
    attachment = "invoked_skills" in line
    hook = not state["settled"] and "SessionStart" in line and "hook_" in line
    assistant = '"assistant"' in line and ((not state["settled"]) or ("tool_use" in line and (
        '"Workflow"' in line or "sidecar" in line)))
    if not (boundary or attachment or hook or assistant):
        return
    try:
        entry = json.loads(line)
    except ValueError:
        return
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return
    if boundary and entry.get("subtype") == "compact_boundary":
        state.update(count=state["count"] + 1, times=(state["times"] + [entry.get("timestamp")])[-2:],
                     settled=False, attached=False, whole=[])
        return
    kind = (entry.get("attachment") or {}).get("type") if entry.get("type") == "attachment" else None
    if kind == "invoked_skills":
        skills = entry["attachment"].get("skills") or []
        state.update(attached=True, whole=sorted({_bare(s.get("name")) for s in skills if _whole(s)} - {""}))
        return
    if isinstance(kind, str) and kind.startswith("hook_") and (
            entry["attachment"].get("hookEvent") == "SessionStart"
            or str(entry["attachment"].get("hookName") or "").startswith("SessionStart")):
        state["settled"] = True
        return
    if entry.get("type") == "assistant":
        state["settled"] = True
        state["dispatches"] += _dispatches(entry)


SCANNER = _transcript_scan.Scanner(
    "compaction_record", _initial, _feed,
    sources=(os.path.abspath(__file__), os.path.join(HERE, "sidecar_dispatch_context.py"),
             os.path.join(TOOLS, "sidecar_argv.py")))


def record(transcript_path):
    """The folded state of the transcript at `transcript_path`. Raises OSError when it cannot be read."""
    return _transcript_scan.scan(transcript_path, SCANNER)


def _epoch(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def position(transcript_path):
    """(ordinal of the compaction in progress or just written, the previous boundary's timestamp or None,
    main-thread dispatches recorded)."""
    state = record(transcript_path)
    count, times = state["count"], state["times"]
    if count == 0:
        return 1, None, state["dispatches"]
    if state["settled"]:
        return count + 1, times[-1], state["dispatches"]
    return count, (times[-2] if len(times) > 1 else None), state["dispatches"]


def reattached_whole(transcript_path, after_epoch=None):
    """Skill names the client re-attached whole after the latest written boundary, or None when no attachment
    for a boundary stamped after `after_epoch` (the PreCompact clear) is on disk yet."""
    state = record(transcript_path)
    if not state["count"] or not state["attached"]:
        return None
    stamp = _epoch(state["times"][-1])
    if after_epoch is not None and (stamp is None or stamp < after_epoch):
        return None
    return list(state["whole"])
