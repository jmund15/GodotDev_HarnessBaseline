"""Re-runnable proof for the advice text in hooks/git_guardrails.py's destructive-command blocks.

The stash list is shared by every session in a checkout, and a whole-file overwrite through an
uninspected verb (`git show <ref>:<path> > <path>`) discards a peer's edit as surely as the blocked
command. So the checkout and restore blocks advise copying the file aside and editing your own change
out in place, `reset --hard` advises `git reset --keep`, and no block recommends `git stash` or
`git show`. Real PreToolUse payloads; deny is stderr + exit 2.

    python3 .claude/tests/test_git_guardrails_advice.py
"""
import json
import os
import subprocess
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(_TESTS_DIR), "hooks", "git_guardrails.py")
FAILURES = []


def run_hook(command, repo):
    payload = {"tool_name": "Bash", "session_id": "s1", "cwd": repo, "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, cwd=repo)
    crashed = r.returncode not in (0, 2) or "Traceback" in r.stderr
    return r.returncode, r.stderr + r.stdout, crashed


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + detail[:400] if detail else ""))


def recommends_stash(message):
    return "git stash" in message.replace("Never `git stash`", "")


def main():
    repo = tempfile.mkdtemp(prefix="ggadvice_")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    for command in ("git checkout -- f.txt", "git restore f.txt"):
        rc, msg, crashed = run_hook(command, repo)
        check("%s is blocked" % command, not crashed and rc == 2, msg)
        check("%s block recommends no git stash" % command, not recommends_stash(msg), msg)
        check("%s block recommends no git show overwrite" % command, "git show" not in msg, msg)
        check("%s block names copying the file aside" % command, "cp <path> .claude/scratch/" in msg, msg)
        check("%s block names the in-place edit" % command, "in place" in msg, msg)

    rc, msg, crashed = run_hook("git reset --hard", repo)
    check("git reset --hard is blocked", not crashed and rc == 2, msg)
    check("reset --hard block recommends no git stash", not recommends_stash(msg), msg)
    check("reset --hard block names git reset --keep", "git reset --keep" in msg, msg)

    rc, msg, crashed = run_hook("git stash drop", repo)
    check("git stash drop is still blocked", not crashed and rc == 2, msg)

    rc, msg, crashed = run_hook("git restore --staged f.txt", repo)
    check("git restore --staged still passes", not crashed and rc == 0, msg)

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
