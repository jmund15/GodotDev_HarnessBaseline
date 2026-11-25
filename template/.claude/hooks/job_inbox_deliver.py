#!/usr/bin/env python3
"""PostToolUse (every tool): deliver the dispatcher's words to the running job they were sent to.

A dispatcher appends to `.claude/scratch/inbox/<key>.md` with `tools/job_inbox.py send`. The key is the payload's
`agent_id` (a Workflow or Agent-tool job), else the payload's `session_id` when this process is a sidecar child
(CLAUDE_CODE_SIDECAR set); any other caller, the main session included, exits at once. The bytes past the delivered
offset (`<key>.offset`, the size already delivered) arrive as `additionalContext` under HEAD: whole lines, at most
CAP_BYTES a call (one line longer than that is cut), and the rest arrives on the job's next tool call. The inbox is the main checkout's, so a job in
a linked worktree and its dispatcher share it (`main_root`). A seat with no hooks reads `label-<label>.md` itself
(`guards/common.md`).

Fail posture: process class, never a floor. Any error delivers nothing and exits 0, leaving the offset where it was
so the bytes arrive on a later call. Proof: tests/test_job_inbox_deliver.py.
"""
import json
import os
import re
import sys
from pathlib import Path

HEAD = "Message from your dispatcher (binds like your brief):"
# The client injects hook text up to 10,000 characters and previews 2,000 past it (`amend_delivery.HOOK_TEXT_CAP`);
# one character is at most 4 bytes, so a chunk of this many bytes always fits.
CAP_BYTES = 9_000
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def main_root(root):
    """The main checkout holding `root`: `root` itself, or, for a linked worktree (its `.git` file names
    `<main>/.git/worktrees/<name>`), the checkout that owns that `.git`. Read from files, with no git spawn:
    the hook runs after every tool call."""
    root = Path(root)
    try:
        git = root / ".git"
        if git.is_file():
            gitdir = Path(os.path.normpath(root / git.read_text(encoding="utf-8").split(":", 1)[1].strip()))
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent
    except (OSError, IndexError, ValueError):
        pass
    return root


def project_root(environ=None):
    """The checkout whose scratch holds the inboxes: CLAUDE_PROJECT_DIR, else this file's checkout, as `main_root`."""
    environ = os.environ if environ is None else environ
    given = environ.get("CLAUDE_PROJECT_DIR")
    if given:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _win_path import native_path
        return main_root(native_path(given.replace("\\", "/")))
    return main_root(Path(__file__).resolve().parents[2])


def inbox_dir(root):
    return Path(root) / ".claude" / "scratch" / "inbox"


def job_key(payload, environ):
    """The inbox key of the job a payload comes from, or None for a caller that has no inbox."""
    if not isinstance(payload, dict):
        return None
    agent = payload.get("agent_id")
    if agent:
        key = str(agent).strip()
    elif str(environ.get("CLAUDE_CODE_SIDECAR") or "").strip():
        key = str(payload.get("session_id") or "").strip()
    else:
        return None
    return key if _KEY.match(key) else None


def _offset(path):
    try:
        with open(path, encoding="ascii") as fh:
            return max(0, int(fh.read().strip()))
    except (OSError, ValueError):
        return 0


def _chunk(data):
    """The whole lines of `data` that fit one delivery, as bytes; b"" while the last line is unfinished."""
    end = data.rfind(b"\n") + 1
    if end <= CAP_BYTES:
        return data[:end]
    cut = data.rfind(b"\n", 0, CAP_BYTES) + 1
    if cut:
        return data[:cut]
    cut = CAP_BYTES
    while cut and (data[cut] & 0xC0) == 0x80:
        cut -= 1
    return data[:cut]


def take(directory, key):
    """The undelivered text of `<directory>/<key>.md`, or "" when there is none; the offset advances past it."""
    inbox = directory / (key + ".md")
    size = os.stat(inbox).st_size
    offset_path = directory / (key + ".offset")
    if _offset(offset_path) == size:
        return ""
    import _file_lock
    from _hook_state import replace_path
    with _file_lock.locked(str(directory / (key + ".lock")), 2):
        offset = _offset(offset_path)
        if offset > size:
            offset = 0
        with open(inbox, "rb") as fh:
            fh.seek(offset)
            data = fh.read(size - offset)
        chunk = _chunk(data)
        if not chunk:
            return ""
        staging = directory / (key + ".offset.tmp")
        with open(staging, "w", encoding="ascii") as fh:
            fh.write(str(offset + len(chunk)))
        replace_path(str(staging), str(offset_path), wait_s=1.0)
    return chunk.decode("utf-8", errors="replace")


def main():
    try:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        key = job_key(payload, os.environ)
        if not key:
            return 0
        text = take(inbox_dir(project_root()), key)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                      "additionalContext": HEAD + "\n\n" + text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
