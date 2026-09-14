#!/usr/bin/env python3
"""Print the session id when a `claude -p` stream-json run ended ON its compaction summary.

In print mode an auto-compaction ends the run: the child emits `compact_boundary`, answers the
summarize request (an `<analysis>…</analysis><summary>…</summary>` text) and the harness closes the
turn — the SessionStart:compact re-prompt hook fires, but no further turn ever reads it (measured
2026-09-03 on two Luna runs: 0 tool uses after the boundary, `terminal_reason: completed`). The
launcher resumes such a session with the brief as the next user turn. Prints nothing when the run
did not compact, or did real work after the boundary, or carries no result.

Usage: sidecar_compact_end.py < stream.jsonl   (or a path as argv[1])
"""
import json
import sys


def main() -> None:
    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    compacted = False
    work_after = 0
    result_sid = None
    result_text = ""
    for line in src:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t == "system" and d.get("subtype") == "compact_boundary":
            compacted = True
            work_after = 0
        elif t == "assistant" and compacted:
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    work_after += 1
        elif t == "result":
            result_sid = d.get("session_id")
            r = d.get("result", "")
            result_text = r if isinstance(r, str) else ""
    if not compacted or not result_sid or work_after > 0:
        return
    head = result_text.lstrip()[:40].lower()
    if head.startswith("<analysis>") or head.startswith("<summary>"):
        print(result_sid)


if __name__ == "__main__":
    main()
