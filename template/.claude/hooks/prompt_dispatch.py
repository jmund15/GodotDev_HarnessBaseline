#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: UserPromptSubmit dispatcher. One process runs the members in order, each through its own `main()`.

CHAIN holds the shared members; the project's own members are named in `adaptation.json`
`hook_members.prompt` (`_optional_hooks.chain`). A member stays a standalone script: it reads the payload on
stdin and prints on stdout and stderr, and `_inprocess_hook.run_hook` swaps those streams for the call. A
member is run in sequence, not in parallel: v2 keeps two shared members, so a thread per member and a
timing log would cost more than they save. A member that hangs is cut by this entry's settings timeout.

Output: each member's stdout is normalized (empty and `{}` dropped; a JSON object yields its
hookSpecificOutput.additionalContext, dropped with a stderr note when absent; anything else verbatim), joined
in order with a newline and printed; `{}` when nothing remains (Claude Code #10463). A fault (an exception is exit 1 with its traceback) or a non-zero
exit is named on stderr and drops that member's output (a crashing standalone hook delivers none); it never
blocks the prompt or erases another member's output. A member whose file is absent is skipped silently.

Wired in: settings.json hooks.UserPromptSubmit.
"""

import importlib
import json
import os
import sys
import traceback

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import _inprocess_hook  # noqa: E402
import _optional_hooks  # noqa: E402

CHAIN = (
    ("prompt_git_state_delta", None),
    ("overnight_ask_guard", None),
)


def _normalize(text):
    """(text for the model, True when a JSON object with no additionalContext was dropped)."""
    text = text.strip()
    if not text or text == "{}":
        return "", False
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return text, False
        if isinstance(parsed, dict):
            specific = parsed.get("hookSpecificOutput")
            context = specific.get("additionalContext") if isinstance(specific, dict) else None
            if isinstance(context, str):
                return context.strip(), False
            return "", True
    return text, False


def dispatch(payload_text, chain=None):
    """Run the members over one payload. Returns (exit_code, stdout_text, stderr_text)."""
    parts, notes = [], []
    for name, _unused in (chain if chain is not None else _optional_hooks.chain("prompt", CHAIN, None)):
        try:
            module = importlib.import_module(name)
        except BaseException:
            notes.append("[prompt-dispatch] %s fault (import) %s\n" % (name, traceback.format_exc().rstrip()))
            continue
        code, out, err = _inprocess_hook.run_hook(module, [], payload_text)
        if code or err.strip():
            notes.append("[prompt-dispatch] %s exit%d%s\n" % (name, code, ": " + err.rstrip() if err.strip() else ""))
        if code:
            continue
        text, dropped = _normalize(out)
        if dropped:
            notes.append("[prompt-dispatch] %s emitted JSON without additionalContext; dropped\n" % name)
        if text:
            parts.append(text)
    return 0, "\n".join(parts) or "{}", "".join(notes)


def _read_payload():
    raw = getattr(sys.stdin, "buffer", None)
    return raw.read().decode("utf-8", errors="replace") if raw is not None else sys.stdin.read()


def _write_real(stream, text):
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode("utf-8", errors="replace"))
            buffer.flush()
        else:
            stream.write(text)
            stream.flush()
    except Exception:
        pass


def main():
    if sys.argv[1:]:
        sys.stderr.write("usage: prompt_dispatch.py  (reads the payload from stdin)\n")
        sys.exit(2)
    try:
        _code, stdout, stderr = dispatch(_read_payload())
    except Exception:
        stdout, stderr = "{}", "[prompt-dispatch] dispatcher fault %s\n" % traceback.format_exc()
    _write_real(sys.stdout, stdout)
    if stderr:
        _write_real(sys.stderr, stderr)


if __name__ == "__main__":
    main()
