#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook: PreToolUse Read — an unbounded Read of a very large file gets its head and a way on.

Class: process (advisory). It never denies.

Why: the native Read refuses a file past 25,000 tokens with an error and no content, so the agent pays
a round trip and then guesses a window. At that size the head plus a Grep serves better than the whole
file, and the head arrives in the same call.

What it decides, on the file's size and nothing else: an unbounded Read (no offset, no limit) of a file
of CLAMP_BYTES or more is rewritten to the whole lines of its first HEAD_BYTES, and the note gives the
file's size, the lines shown and the offset to page on from. Below CLAMP_BYTES the read proceeds; a
clamp there only added a call, since the agent paged straight on.

Left alone: a bounded Read, a missing file (Read words its own error), a format Read handles itself
(READ_NATIVE), and a file with no line break in its first HEAD_BYTES (single-line JSON: no line range
fits, and the native refusal covers a file this large).

Contract: `process(input_data)` returns `{"rewrite": <tool_input>, "note": <str>}` or None. The
dispatcher (`pre_read_dispatch.py`) is the one caller and emits it as `updatedInput` + `additionalContext`.
"""

import os

# ~100 KB is about where the native refusal starts (25,000 tokens at 3-4 bytes per token).
CLAMP_BYTES = 100 * 1024
# The head shown: about 10,000 tokens, well inside what one Read returns.
HEAD_BYTES = 40 * 1024

# Formats Read renders or paginates itself, so byte size does not measure their context cost.
READ_NATIVE = frozenset({
    ".pdf", ".ipynb", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".heic",
    ".ico", ".avif",
})


def process(input_data: dict) -> dict | None:
    if input_data.get("tool_name") != "Read":
        return None
    tool_input = input_data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not path or tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return None
    if os.path.splitext(path)[1].lower() in READ_NATIVE:
        return None
    try:
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        if size < CLAMP_BYTES:
            return None
        with open(path, "rb") as fh:
            lines = fh.read(HEAD_BYTES).count(b"\n")
    except OSError:
        return None
    if lines == 0:
        return None
    return {
        "rewrite": dict(tool_input, limit=lines),
        "note": "[file-size] %s is ~%d KB; this read shows lines 1-%d. Grep it for what you need, "
                "or Read on with offset=%d." % (os.path.basename(path), size // 1024, lines, lines + 1),
    }
