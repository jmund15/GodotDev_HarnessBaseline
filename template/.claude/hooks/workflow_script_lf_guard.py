#!/usr/bin/env python3
"""PreToolUse(Workflow): rewrite the `scriptPath` file to LF before the permission handler inlines it.

The permission handler rejects carriage returns while inlining `scriptPath`; this hook
normalizes harness scripts to LF before that handler reads them.
Fail-open: any exception exits 0 with no output; a hook crash must never block a dispatch. Only
`.claude/**` scripts are rewritten; paths outside the harness are left alone.
"""
import json
import os
import sys

from _claude_scope import harness_tail
from _win_path import native_path


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("tool_name") != "Workflow":
            return 0
        path = str((payload.get("tool_input") or {}).get("scriptPath") or "")
        path = native_path(path)
        if not path or not os.path.isfile(path):
            return 0
        if harness_tail(path) is None:
            return 0
        with open(path, "rb") as fh:
            data = fh.read()
        crs = data.count(b"\r")
        if not crs:
            return 0
        with open(path, "wb") as fh:
            fh.write(data.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": ("[workflow-lf-guard] rewrote %d CRLF line ending(s) to LF in %s so the "
                                  "permission handler can inline it. Whatever wrote this file used Windows "
                                  "text mode — Python writers pass newline=\"\\n\"." % (crs, os.path.basename(path))),
        }}))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
