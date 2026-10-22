"""Proof for hooks/triage_commit_check.py: an unreadable Triage trailer is advised; a commit with no trailer gets a derived one; other commands pass silently.

A planted git repo in a temp root is the project. Real PreToolUse payloads on stdin. 
    python3 .claude/tests/test_triage_commit_check.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "triage_commit_check.py")
FAILURES = []
PROJECT = tempfile.mkdtemp(prefix="triage_commit_proof_")
STATE = tempfile.mkdtemp(prefix="triage_commit_state_")
RECORDS = tempfile.mkdtemp(prefix="triage_commit_records_")


def git(*args):
    subprocess.run(["git", "-C", PROJECT] + list(args), capture_output=True, text=True, check=True)


def run(command, tool="Bash"):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=PROJECT, HARNESS_HOOK_STATE_DIR=STATE, HARNESS_TASK_RECORD_DIR=RECORDS)
    payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": PROJECT, "session_id": "proof-triage-1"}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=120, env=env)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


git("init", "-q")
git("config", "user.email", "proof@example.invalid")
git("config", "user.name", "proof")
with open(os.path.join(PROJECT, "notes.md"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("A proof note.\n")
git("add", "--", "notes.md")

rc, out, err = run('git commit -q -m "proof note"')
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a commit with no trailer gets a derived Triage-Derived trailer",
      rc == 0 and "Triage-Derived:" in output.get("updatedInput", {}).get("command", ""), out + err)

rc, out, err = run('git commit -q -m "proof note" -m "Triage: reach=bogus"')
output = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("an unreadable Triage trailer is advised as unreadable",
      rc == 0 and "trailer unreadable" in output.get("additionalContext", ""), out + err)

rc, out, err = run("ls -la")
check("a non-commit command passes silently", rc == 0 and out.strip() == "{}", out + err)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
