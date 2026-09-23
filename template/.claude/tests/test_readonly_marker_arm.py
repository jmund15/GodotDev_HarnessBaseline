#!/usr/bin/env python3
"""Proof for hooks/readonly_marker_arm.py: a Workflow dispatch arms the read-only marker exactly
when its script file declares the `// READONLY-FANOUT-ENGINE` line.

    python3 .claude/tests/test_readonly_marker_arm.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
HOOK = REPO / ".claude" / "hooks" / "readonly_marker_arm.py"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def armed(home: Path, tool_input: dict, tool_name: str = "Workflow") -> bool:
    """Run the hook in a fresh HOME and report whether it wrote the session marker."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PROJECT_DIR=str(REPO))
    payload = {"tool_name": tool_name, "tool_input": tool_input, "session_id": "armtest1",
               "cwd": str(REPO)}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)
    marker = home / ".claude" / ".routing_state" / "readonly-armtest1.json"
    ok = proc.returncode == 0 and marker.is_file()
    if marker.is_file():
        marker.unlink()
    return ok


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        engine = home / "custom_engine.js"
        engine.write_text("// header\n// READONLY-FANOUT-ENGINE\nexport default 1\n", encoding="utf-8")
        plain = home / "plain.js"
        plain.write_text("// mentions READONLY-FANOUT-ENGINE inside prose only\n", encoding="utf-8")

        check("a shipped read-only engine arms by relative scriptPath",
              armed(home, {"scriptPath": ".claude/workflows/review_fanout.js"}))
        check("dispatch.js does not arm", not armed(home, {"scriptPath": ".claude/workflows/dispatch.js"}))
        check("any script file declaring the marker arms by absolute path",
              armed(home, {"scriptPath": str(engine)}))
        check("a script that only mentions the marker in prose does not arm",
              not armed(home, {"scriptPath": str(plain)}))
        check("an inline script body is not inspected",
              not armed(home, {"script": "// READONLY-FANOUT-ENGINE\n"}))
        check("a missing script file does not arm",
              not armed(home, {"scriptPath": str(home / "absent.js")}))
        check("a non-Workflow tool does not arm",
              not armed(home, {"scriptPath": ".claude/workflows/review_fanout.js"}, tool_name="Agent"))
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
