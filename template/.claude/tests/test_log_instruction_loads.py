#!/usr/bin/env python3
"""Re-runnable proof for hooks/log_instruction_loads.py: one InstructionsLoaded payload in,
one JSONL line under `<CLAUDE_PROJECT_DIR>/logs/instructions_loaded.jsonl` out, via the shared
`_hook_state.append_jsonl_rotating` (rotation proven there by size).

    python3 .claude/tests/test_log_instruction_loads.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "log_instruction_loads.py")


def main():
    project = tempfile.mkdtemp(prefix="instrlog_")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=project)
    payload = {"hook_event_name": "InstructionsLoaded", "files": ["CLAUDE.md"]}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, timeout=60, env=env)
    log = os.path.join(project, "logs", "instructions_loaded.jsonl")
    cases = [("hook exits 0 and prints {}", r.returncode == 0 and r.stdout.strip() == "{}")]
    lines = []
    if os.path.exists(log):
        with open(log, encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
    cases.append(("one record with the payload lands in logs/instructions_loaded.jsonl",
                  len(lines) == 1 and lines[0].get("payload") == payload
                  and lines[0].get("event") == "InstructionsLoaded"))

    # Rotation: pre-fill past the cap, fire once more, expect the tail to survive plus the new line.
    with open(log, "w", encoding="utf-8") as fh:
        for i in range(3000):
            fh.write(json.dumps({"i": i, "pad": "x" * 800}) + "\n")
    subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                   text=True, timeout=60, env=env)
    with open(log, encoding="utf-8") as fh:
        after = [json.loads(ln) for ln in fh if ln.strip()]
    cases.append(("past 2 MB the log rotates to its tail and keeps the newest record",
                  len(after) <= 501 and after[-1].get("event") == "InstructionsLoaded"
                  and after[0].get("i", 0) >= 2500))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
