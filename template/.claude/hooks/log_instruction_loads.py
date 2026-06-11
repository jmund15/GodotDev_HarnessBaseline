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

    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "instructions_loaded.jsonl"
        # Size-gated rotation: keep the most recent ~500 events once the file
        # passes 2 MB (append-only logs here grow unbounded otherwise).
        try:
            if log_file.exists() and log_file.stat().st_size > 2_000_000:
                tail = log_file.read_text(encoding="utf-8").splitlines(True)[-500:]
                log_file.write_text("".join(tail), encoding="utf-8")
        except Exception:
            pass
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Never break the harness. Silent failure on log write is acceptable;
        # the hook's job is observation, not enforcement.
        pass

    # Empty JSON object on stdout signals success per Hook_Gotchas convention.
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
