"""Proof for hooks/composed_output_guard.py: a Write/Edit on a composed file is denied with the
input to edit instead, because `baseline_sync.py compose` regenerates the file and silently
drops a direct edit (settings.json, 2026-09-22). Inputs and ordinary files pass.

    python3 .claude/tests/test_composed_output_guard.py
"""
import json
import os
import subprocess
import sys

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS)
import composed_output_guard as guard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HOOKS))
FAILURES = []


def edit(rel, tool="Edit"):
    path = os.path.join(ROOT, *rel.split("/"))
    tool_input = {"file_path": path, "old_string": "a", "new_string": "b"} if tool == "Edit" \
        else {"file_path": path, "content": "{}"}
    return {"tool_name": tool, "tool_input": tool_input, "session_id": "composed-proof"}


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


for rel, tool in ((".claude/settings.json", "Edit"), (".claude/settings.json", "Write"),
                  (".claude/reference/memory_domains.md", "Edit")):
    got = guard.process(edit(rel, tool)) or {}
    check("%s %s is blocked" % (tool, rel), "block" in got, got)
    check("%s %s block names an input and compose" % (tool, rel),
          "compose" in got.get("block", "") and ".base." in got.get("block", ""), got)

for rel in (".claude/settings.project.json", ".claude/settings.base.json", ".claude/settings.local.json",
            ".claude/reference/memory_domains.base.md", ".claude/hooks/x.py"):
    check("Edit %s passes" % rel, not guard.process(edit(rel)), rel)
check("a worktree's composed settings.json is blocked too",
      "block" in (guard.process(edit(".claude/worktrees/wt/.claude/settings.json")) or {}))
check("a Bash payload is ignored", not guard.process({"tool_name": "Bash", "tool_input": {"command": "x"}}))
check("the cheap name prefilter lists exactly the composed files baseline_sync owns",
      guard.COMPOSED_NAMES == {os.path.basename(k) for k in guard._composed_inputs()},
      getattr(guard, "COMPOSED_NAMES", None))

dispatcher = os.path.join(HOOKS, "pre_edit_dispatch.py")
r = subprocess.run([sys.executable, dispatcher], input=json.dumps(edit(".claude/settings.json")),
                   capture_output=True, text=True, timeout=60)
check("the live dispatcher blocks a settings.json edit (exit 2)", r.returncode == 2 and "compose" in r.stderr,
      r.stderr or r.stdout)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
