#!/usr/bin/env python3
"""Re-runnable proof for hooks/tool_routing_nudge.py's vault-write advisory.

A doc-sized direct `Write` into the Obsidian vault must surface an advisory asking the model to
name the doc's class (judgment-dense → direct is right; templated → write_doc). It fires ONCE per
path per session, never on small touch-ups, never outside the vault, and never as a block.

State is redirected with HARNESS_HOOK_STATE_DIR — this never touches ~/.claude/.routing_state/.

    python3 .claude/tests/test_tool_routing_nudge_vault_write.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "tool_routing_nudge.py")
SID = "trvw0001"
VAULT = "C:/Users/{{USER}}/Documents/ObsidianVault/DevProjects/{{PROJECT_NAME}}/Claude/Review.md"


def call(tool_input, env, tool_name="Write"):
    r = subprocess.run([sys.executable, HOOK],
                       input=json.dumps({"tool_name": tool_name, "session_id": SID, "tool_input": tool_input}),
                       capture_output=True, text=True, timeout=60, env=env)
    if r.returncode not in (0, 2):
        return "CRASH rc=%d %s" % (r.returncode, (r.stderr or "")[-300:])
    if r.returncode == 2:
        return "BLOCKED " + (r.stderr or "")
    out = (r.stdout or "").strip()
    if not out:
        return ""
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")


def main():
    tmp = tempfile.mkdtemp(prefix="trvw_")
    env = dict(os.environ, HARNESS_HOOK_STATE_DIR=tmp, PYTHONIOENCODING="utf-8")
    cases = []

    first = call({"file_path": VAULT, "content": "# Review\n" + "x" * 5000}, env)
    cases.append(("doc-sized vault Write fires the class advisory", "Name its class" in first))
    cases.append(("the advisory is advisory — exit 0, never a block", not first.startswith("BLOCKED") and not first.startswith("CRASH")))

    again = call({"file_path": VAULT, "content": "# Review\n" + "y" * 5000}, env)
    cases.append(("the same path is not nudged twice in one session", again == ""))

    small = call({"file_path": VAULT.replace("Review", "Other"), "content": "one line"}, env)
    cases.append(("a small vault write (touch-up) is silent", small == ""))

    outside = call({"file_path": "C:/repo/.claude/scratch/spill.md", "content": "x" * 9000}, env)
    cases.append(("a large write outside the vault is silent", outside == ""))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
