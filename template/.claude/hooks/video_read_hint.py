#!/usr/bin/env python3
"""Hook: PreToolUse on Read, advise the video_frames tool when the file is a video or GIF.

Class: process, advisory only. It never denies and always exits 0.

A Read of a GIF shows its first frame and a Read of a video shows no motion. For `.mp4 .webm .mov .avi .mkv
.gif` it emits one line as `hookSpecificOutput.additionalContext` that names the skill `video_frames` and
the script that cuts the file into a timestamped contact sheet. Any other tool, extension or malformed
payload gets no output.

Run by pre_read_dispatch.py, the one Read PreToolUse command; `main()` stays for the standalone proof.
Proof: tests/test_video_read_hint.py.
"""
import json
import re
import sys

VIDEO_SUFFIXES = {"mp4", "webm", "mov", "avi", "mkv", "gif"}
SCRIPT = ".claude/scripts/video_frames.py"


def advisory(payload) -> str | None:
    if not isinstance(payload, dict) or payload.get("tool_name") != "Read":
        return None
    tool_input = payload.get("tool_input")
    path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(path, str):
        return None
    name = re.split(r"[\\/]", path)[-1]
    if "." not in name or name.rsplit(".", 1)[1].lower() not in VIDEO_SUFFIXES:
        return None
    return (f"This file is a video or GIF, and Read does not show its motion. Load skill video_frames, run "
            f'`python3 {SCRIPT} "{path}"` (add --help for flags), then Read each sheet path it prints.')


def main() -> None:
    try:
        text = advisory(json.load(sys.stdin))
    except (ValueError, OSError):
        sys.exit(0)
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
    sys.exit(0)


if __name__ == "__main__":
    main()
