"""Proof for hooks/provenance_label_guard.py: loaded guidance states rules, never their provenance.
An ADDED line carrying "(owner decision)" / "by owner decision" in a skill, command, rule or
CLAUDE*.md is blocked; history homes, memory and plans pass, and so does workshop-pending prose
("Awaits user decision") and a label the edit leaves untouched.

    python3 .claude/tests/test_provenance_label_guard.py
"""
import json
import os
import subprocess
import sys

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
sys.path.insert(0, HOOKS)
import provenance_label_guard as guard  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HOOKS))
FAILURES = []
LABEL = "**Standing authorization (owner decision):** a pinned Workflow needs no opt-in."
BY = "Sidecars launch with bypassPermissions by owner decision, hooks as their guards."


def edit(rel, new, old="placeholder"):
    return {"tool_name": "Edit", "session_id": "prov-proof",
            "tool_input": {"file_path": os.path.join(ROOT, *rel.split("/")), "old_string": old,
                           "new_string": new}}


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def blocked(payload):
    return "block" in (guard.process(payload) or {})


check("a skill gaining a (owner decision) label is blocked", blocked(edit(".claude/skills/x/SKILL.md", LABEL)))
check("CLAUDE.md gaining 'by owner decision' is blocked", blocked(edit(".claude/CLAUDE.core.md", BY)))
check("a rule gaining '(user decision 2026-09-15)' is blocked",
      blocked(edit(".claude/rules/r.md", "Use X (user decision 2026-09-15).")))
check("a Write of a command carrying a label is blocked",
      "block" in (guard.process({"tool_name": "Write", "tool_input": {
          "file_path": os.path.join(ROOT, ".claude", "commands", "c.md"), "content": LABEL}}) or {}))
msg = (guard.process(edit(".claude/skills/x/SKILL.md", LABEL)) or {}).get("block", "")
check("the block names the fix (state the rule, provenance to commit or memory)",
      "commit" in msg and "memory" in msg, msg)

for rel in (".claude/skills/failure_archaeology/SKILL.md", ".claude/auto-memory/f.md",
            ".claude/plans/p.md", ".claude/reference/model_ladder_evidence.md", "Docs/notes.md"):
    check("%s may carry the label" % rel, not blocked(edit(rel, LABEL)))
check("workshop-pending prose passes", not blocked(edit(".claude/skills/x/SKILL.md",
                                                        "| workshop-pending | Awaits user decision |")))
check("a label the edit leaves untouched passes", not blocked(edit(".claude/skills/x/SKILL.md",
                                                                   LABEL + " More.", old=LABEL)))
check("a quoted or backticked mention of the label passes (a rule describing it)",
      not blocked(edit(".claude/skills/x/SKILL.md",
                       'No "(owner decision)" or `by user decision` label goes in a rule.')))
check("a label outside the quotes on the same line is still blocked",
      blocked(edit(".claude/skills/x/SKILL.md", 'Use "x" here (owner decision).')))
check("a plain skill line passes", not blocked(edit(".claude/skills/x/SKILL.md", "Dispatch it without asking.")))

dispatcher = os.path.join(HOOKS, "pre_edit_dispatch.py")
r = subprocess.run([sys.executable, dispatcher], input=json.dumps(edit(".claude/rules/r.md", BY)),
                   capture_output=True, text=True, timeout=60)
check("the live dispatcher blocks the label (exit 2)", r.returncode == 2 and "provenance" in r.stderr,
      r.stderr or r.stdout)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
