#!/usr/bin/env python3
"""Re-runnable proof for hooks/indexed_reference_guard.py — a whole-file Read of an index-served
reference is blocked (exit 2 + stderr); bounded reads, other files and other tools pass.

    python3 .claude/tests/test_indexed_reference_guard.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HOOK = os.path.join(ROOT, ".claude", "hooks", "indexed_reference_guard.py")
sys.path.insert(0, os.path.join(ROOT, ".claude", "hooks"))
import indexed_reference_guard as guard  # noqa: E402

CATALOG = os.path.join(ROOT, ".claude", "commands", "agents", "plan_check_agents.md")


def run(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    r = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    return r.returncode, r.stderr or ""


def main():
    cases = []
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": CATALOG}})
    cases.append(("whole-file Read of an indexed catalog is blocked on the real channel (exit 2 + stderr)",
                  rc == 2 and "BLOCKED" in err and "lens.py" in err))
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": CATALOG, "offset": 10, "limit": 40}})
    cases.append(("bounded Read of the same catalog passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": os.path.join(ROOT, ".claude", "CLAUDE.md")}})
    cases.append(("a non-indexed file passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Grep", "tool_input": {"pattern": "plc-", "path": CATALOG}})
    cases.append(("another tool on the catalog passes", rc == 0 and not err))
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": CATALOG.replace("\\", "/")}})
    cases.append(("forward-slash path still matches the suffix", rc == 2))
    variant = os.path.join(ROOT, ".CLAUDE", "commands", "agents", "..", "agents",
                           "PLAN_CHECK_AGENTS.MD")
    rc, err = run({"tool_name": "Read", "tool_input": {"file_path": variant}})
    cases.append(("Windows case and dot-segment variants cannot bypass the same indexed file",
                  rc == 2 and "BLOCKED" in err))
    rc, err = run("{not json")
    cases.append(("malformed payload fails open (exit 0)", rc == 0))
    rc, err = run('"a json string, not an object"')
    cases.append(("a non-object JSON payload fails open, never crashes", rc == 0))
    cases.append(("the guard's message names the accessor for every registered reference",
                  all("python3" in cmd or "grep" in cmd for cmd, _ in guard.INDEXED_REFERENCES.values())))
    cases.append(("every registered reference exists on disk or is known project-local content",
                  all(s in guard.LOCAL_ONLY_TARGETS or os.path.isfile(os.path.join(ROOT, ".claude", s))
                      for s in guard.INDEXED_REFERENCES)))
    cases.append(("process() returns None for a payload without tool_input",
                  guard.process({"tool_name": "Read"}) is None))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
