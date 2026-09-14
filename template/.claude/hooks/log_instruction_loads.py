#!/usr/bin/env python3
"""
InstructionsLoaded hook — records which instruction artifacts (CLAUDE.md files,
rules, path-scoped rule files, imports) were loaded during a session boot or
mid-session reload, with timestamps.

Pure observer. Never blocks. Writes one JSONL line per fire to
logs/instructions_loaded.jsonl. Read it with `tail -f` or jq when debugging
"why didn't rule X fire?" — if X isn't in the log, it wasn't loaded.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import append_jsonl_rotating  # noqa: E402


def _log_dir() -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if not root:
        return Path.cwd() / "logs"
    return Path(root) / "logs"


def main() -> None:
    raw = sys.stdin.read()
    payload: object
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"_raw": raw[:2000], "_parse_error": True}

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "InstructionsLoaded",
        "payload": payload,
    }

    # Observation only: a failed write costs a log line, never the harness.
    append_jsonl_rotating(str(_log_dir() / "instructions_loaded.jsonl"), [record])

    # Empty JSON object on stdout signals success per Hook_Gotchas convention.
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
