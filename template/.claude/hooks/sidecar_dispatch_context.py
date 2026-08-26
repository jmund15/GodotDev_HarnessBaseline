#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash — inject `reference/sidecar_dispatch.md` on a sidecar launch.

Fires when the command invokes a `*_sidecar.sh` launcher (not `--check`). Emits the
reference once per session via `additionalContext` (the only model-visible advisory
channel on PreToolUse), then re-emits after REFIRE_AFTER_SECONDS so a compacted session
gets it again. The rule's home is the reference file; this hook only delivers it.

Fail-open: any error exits 0 silently.

Wired in: settings.json hooks.PreToolUse matcher "Bash".
"""

import json
import os
import re
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/.routing_state")
STATE_KEY = "sidecar_dispatch_context_ts"
REFIRE_AFTER_SECONDS = 3600
# A launch passes flags; a mere mention (grep, sed, ls) does not.
LAUNCH_RE = re.compile(r"[\w./\\-]*_sidecar\.sh\s+-")


def _state_path(session_id):
    return os.path.join(STATE_DIR, f"{(session_id or 'default')[:8]}.json")


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not LAUNCH_RE.search(cmd) or "--check" in cmd:
        return
    session_id = data.get("session_id") or ""
    path = _state_path(session_id)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    last = state.get(STATE_KEY, 0)
    if isinstance(last, (int, float)) and time.time() - last < REFIRE_AFTER_SECONDS:
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    ref = os.path.join(root, ".claude", "reference", "sidecar_dispatch.md")
    with open(ref, encoding="utf-8") as fh:
        text = fh.read()
    state[STATE_KEY] = time.time()
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[sidecar dispatch — reference/sidecar_dispatch.md]\n" + text,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
