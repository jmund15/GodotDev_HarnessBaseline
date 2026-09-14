#!/usr/bin/env python3
"""PreToolUse(Workflow): rewrite the `scriptPath` file to LF before the permission handler inlines it.

The handler reads the file at `scriptPath` into a `script` field to render its approval dialog and
rejects every `\\r` as a "control character that would be hidden in the approval dialog" — so a
CRLF workflow script fails BEFORE any agent runs, with an error that names `script`, not the file.
Git is not the writer (`.gitattributes` pins `eol=lf`; a fresh `checkout-index` emits LF — measured
2026-09-05): CRLF arrives from Windows text-mode writers — Python `write_text()`/`open('w')` without
`newline="\\n"`, PowerShell `Set-Content`/`Out-File`. Every patch script that touches a `.js` engine
re-creates the fault, which is why it "kept coming back" across sessions. Healing at the one
consumer that breaks ends the recurrence regardless of the writer.

Fail-open: any exception exits 0 with no output; a hook crash must never block a dispatch. Only
`.claude/**` scripts are rewritten — a path outside the harness is left alone and reported.
Memory: gotcha_crlf_scriptpath_blocks_workflow_dispatch.
"""
import json
import os
import sys


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("tool_name") != "Workflow":
            return 0
        path = str((payload.get("tool_input") or {}).get("scriptPath") or "")
        if len(path) > 3 and path[0] == "/" and path[2] == "/" and path[1].isalpha():
            path = path[1].upper() + ":" + path[2:]  # MSYS /c/... -> C:/... (Windows Python cannot open the former)
        if not path or not os.path.isfile(path):
            return 0
        norm = path.replace("\\", "/")
        if "/.claude/" not in norm and not norm.startswith(".claude/"):
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
