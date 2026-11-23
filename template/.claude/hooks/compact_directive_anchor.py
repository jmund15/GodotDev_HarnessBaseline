#!/usr/bin/env python3
"""The state-file hook: a write to a state file points the session at it; SessionStart(compact) re-prints it.

A state file is `.claude/work/<slug>.md` (T5). PostToolUse(Write|Edit) of one writes the session's pointer,
`.claude/scratch/active_state_<session_id>`, holding the file's project-relative path. SessionStart(compact)
prints the file that pointer names, so its standing facts and slices survive compaction. No pointer, no output.
The client caps one hook's output at 10,000 characters: a long file prints its standing facts first, then as
much as fits, and a line names the file to read in full.
"""
import json
import os
import re
import sys
from pathlib import Path

CAP = 9000
STATE = re.compile(r"\.claude/work/[^/]+\.md")
CLIPPED = "[state-file] clipped at {cap} characters; read {path} for the rest."
FACTS = re.compile(r"(?ms)^## Standing facts\n.*?(?=^## |\Z)")


def project_dir(payload):
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def pointer(root, session_id):
    return root / ".claude" / "scratch" / ("active_state_" + session_id)


def state_path(root, file_path):
    """The project-relative path of a state file `file_path` names under `root`, else ''."""
    path = Path(file_path)
    try:
        rel = (path if path.is_absolute() else root / path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return ""
    return rel if STATE.fullmatch(rel) else ""


def clip(text, path):
    if len(text) <= CAP:
        return text
    facts = FACTS.search(text)
    head = facts.group(0) if facts else ""
    rest = text.replace(head, "", 1)
    room = max(CAP - len(head) - len(CLIPPED) - 2, 0)
    return head + rest[:room] + "\n" + CLIPPED.format(cap=CAP, path=path)


def on_write(payload, root):
    session_id = payload.get("session_id") or ""
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    rel = state_path(root, file_path) if file_path else ""
    if not (re.fullmatch(r"[\w-]+", session_id) and rel):
        return
    pointer(root, session_id).parent.mkdir(parents=True, exist_ok=True)
    pointer(root, session_id).write_text(rel + "\n", encoding="utf-8")


def on_compact(payload, root):
    session_id = payload.get("session_id") or ""
    if not re.fullmatch(r"[\w-]+", session_id):
        return ""
    try:
        rel = pointer(root, session_id).read_text(encoding="utf-8").strip()
        return clip((root / rel).read_text(encoding="utf-8"), rel)
    except OSError:
        return ""


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    root = project_dir(payload)
    event = payload.get("hook_event_name")
    if event == "PostToolUse":
        on_write(payload, root)
    elif event == "SessionStart":
        text = on_compact(payload, root)
        if text:
            sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
