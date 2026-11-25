#!/usr/bin/env python3
"""The loss floor's one PreToolUse entry, registered for `Bash|PowerShell|Monitor|TaskStop`.

It imports `hooks/pre_bash_dispatch.py` and asks it for the decision. Any failure there (a file that does not parse,
an import or a call that raises) denies the call: a hook that crashed would exit non-zero, which the client treats as
a non-blocking error, so the call would run unjudged. Residual: a fault in this file itself fails open, which is why
it holds nothing else.
"""
import importlib
import json
import os
import sys

DISPATCHER = "pre_bash_dispatch"  # the chain this entry runs (`tools/layer_closure.py` reads the name)


def main():
    stdin = getattr(sys.stdin, "buffer", None)  # a dispatcher running this in process hands it a text stream
    raw = stdin.read().decode("utf-8", "replace") if stdin is not None else sys.stdin.read()
    note = ""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        out, note = importlib.import_module(DISPATCHER).decide_text(raw)
    except BaseException as exc:  # noqa: BLE001 - every fault denies
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
               "permissionDecisionReason": "BLOCKED call — the loss floor (hooks/pre_bash_dispatch.py) failed "
                                           "(%s: %s), so nothing was judged. Repair it, then re-issue the same "
                                           "call." % (type(exc).__name__, exc)}}
    if out:
        sys.stdout.write(json.dumps(out) + "\n")
    if note:
        sys.stderr.write(note + ("\n" if not note.endswith("\n") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
