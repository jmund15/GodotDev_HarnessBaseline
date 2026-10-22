"""Proof for hooks/duplicate_commit_check.py: a commit in a declared instruction scope with a malformed scope exits 1 with the error on stderr and no deny; an undeclared scope passes silently.

A planted git repo in a temp root is the project. Real PreToolUse payloads on stdin.

    python3 .claude/tests/test_duplicate_commit_check.py
"""
import json
import os
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "duplicate_commit_check.py")
FAILURES = []
PROJECT = tempfile.mkdtemp(prefix="dup_commit_proof_")
ADAPTATION = os.path.join(PROJECT, ".claude", "skills", "project_subsystems", "adaptation.json")


def git(*args):
    subprocess.run(["git", "-C", PROJECT] + list(args), capture_output=True, text=True, check=True)


def write(rel, text):
    path = os.path.join(PROJECT, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(command, tool="Bash"):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=PROJECT)
    payload = {"tool_name": tool, "tool_input": {"command": command}, "cwd": PROJECT}
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
write("docs/rules.md", "Every rule has one owner.\n")
git("add", "--", "docs/rules.md")

rc, out, err = run('git commit -q -m "proof"')
check("a commit with an undeclared instruction scope passes silently", rc == 0 and out.strip() == "{}", out + err)

write(os.path.relpath(ADAPTATION, PROJECT).replace(os.sep, "/"), json.dumps({"instruction_scope": {}}))
rc, out, err = run('git commit -q -m "proof"')
check("a commit under a malformed instruction scope exits 1 with the error on stderr and no deny",
      rc == 1 and "instruction_scope" in err, out + err)

rc, out, err = run("ls -la")
check("a non-commit command passes silently", rc == 0 and out.strip() == "{}", out + err)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
