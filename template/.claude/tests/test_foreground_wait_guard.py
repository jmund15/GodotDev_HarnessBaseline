"""Proof for hooks/foreground_wait_guard.py: a Monitor watch past the tool's own ceiling is denied; one under it passes.
A backgrounded pipeline's trailing `| tail`/`| head` is dropped for the caller, and every other blind shape is denied.

Real PreToolUse payloads on stdin. The Monitor branch reads no state, so the proof plants none; the blind cases read
none either.

    python3 .claude/tests/test_foreground_wait_guard.py
"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "foreground_wait_guard.py")
MONITOR_CAP_MS = 1800000
FAILURES = []


def run(payload):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30)
    return r.returncode, r.stdout, r.stderr


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:300]))


def monitor(timeout_ms):
    return {"session_id": "proof-fwg-1", "hook_event_name": "PreToolUse", "tool_name": "Monitor",
            "tool_input": {"command": "tail -f run.log", "timeout_ms": timeout_ms}}


rc, out, err = run(monitor(MONITOR_CAP_MS + 1))
decision = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
check("a Monitor asking past the tool ceiling is denied",
      rc == 0 and decision.get("permissionDecision") == "deny", out + err)

rc, out, err = run(monitor(60000))
check("a Monitor under the ceiling passes silently", rc == 0 and out.strip() == "", out + err)


def bash(command, background=False):
    tool_input = {"command": command, "run_in_background": True} if background else {"command": command}
    return {"session_id": "proof-fwg-2", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": tool_input}


def advisory(command, background=False):
    rc, out, err = run(bash(command, background))
    context = json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "") if out.strip() else ""
    return rc, context, out + err


for label, command in (("a subshell backgrounded with a trailing ampersand", "( pytest -q ) &"),
                       ("a line ending in a background ampersand", "pytest -q &\necho done")):
    rc, context, detail = advisory(command)
    check(label + " draws the run_in_background advisory",
          rc == 0 and "run_in_background" in context, detail)

for label, command, background in (
        ("a logical and is not a background job", "pytest -q && echo ok", False),
        ("a redirect into a descriptor is not a background job", "pytest -q 2>&1", False),
        ("an ampersand inside quotes is not a background job", 'echo "tail &"', False),
        ("an ampersand in a heredoc body is data", "cat <<'EOF'\nline &\nEOF", False),
        ("an ampersand on a call already detached with run_in_background is not repeated", "( pytest -q ) &", True)):
    rc, context, detail = advisory(command, background)
    check(label + " passes silently", rc == 0 and "run_in_background" not in context, detail)

def decision_of(command, background):
    rc, out, err = run(bash(command, background))
    hso = json.loads(out).get("hookSpecificOutput", {}) if out.strip() else {}
    return rc, hso, out + err


# A blind stage that ends the command is dropped for the caller: the call already says the right one.
for label, command, want in (
        ("a tail count", "python3 .claude/scripts/harness_tests.py --close | tail -15",
         "python3 .claude/scripts/harness_tests.py --close"),
        ("a stderr redirect then a tail count", "python3 x.py 2>&1 | tail -n 20", "python3 x.py 2>&1"),
        ("a bare tail count", "python3 x.py | tail -5", "python3 x.py"),
        ("a head stage", "npm run build | head -n 40", "npm run build"),
        ("a scoped harness run piped into tail",
         "cd /r && python3 .claude/scripts/harness_tests.py --proofs-for x.py 2>&1 | tail -15",
         "cd /r && python3 .claude/scripts/harness_tests.py --proofs-for x.py 2>&1"),
        ("two stacked blind stages", "python3 x.py | tail -5 | head -3", "python3 x.py")):
    rc, hso, detail = decision_of(command, True)
    got = (hso.get("updatedInput") or {}).get("command")
    check("backgrounded, %s is rewritten to %r" % (label, want),
          rc == 0 and hso.get("permissionDecision") is None and got == want, detail)
    check("...and the note names the dropped stage and the output file",
          "output file keeps every line" in (hso.get("additionalContext") or ""), detail)

# Every other blind shape keeps the deny: dropping the stage would change what runs or where its result goes.
for label, command in (
        ("a blind stage mid-pipeline", "python3 x.py | tail -n 20 | cat"),
        ("a blind stage inside a subshell", "(python3 x.py | tail -n 5)"),
        ("a test run filtered then cut by head", "(npx vitest run 2>&1 | grep -E 'Tests |FAIL' | head -8); echo end"),
        ("a blind stage followed by a command", "python3 x.py | tail -n 5; echo done"),
        ("a blind stage followed by a logical and", "python3 x.py | tail -n 5 && echo ok"),
        ("a blind stage carrying a redirection", "python3 x.py | tail -n 5 > out.txt"),
        ("a command carrying a substitution", "python3 $(cat listing.txt) | tail -n 5")):
    rc, hso, detail = decision_of(command, True)
    check("backgrounded, " + label + " is denied", rc == 0 and hso.get("permissionDecision") == "deny", detail)

# A foreground call, and a pipe that feeds a captured string or a quoted argument, are not the task's output pipe.
for label, command, background in (
        ("a foreground call piped into tail", "python3 x.py | tail -5", False),
        ("a pipe inside a command substitution", "echo $(python3 x.py | tail -n 5)", True),
        ("a quoted pipe", 'echo "a | tail"', True)):
    rc, hso, detail = decision_of(command, background)
    check(label + " is left alone",
          rc == 0 and hso.get("permissionDecision") is None and not hso.get("updatedInput"), detail)

for label, command, background in (
        ("a backgrounded job teed to a file before tail", "npm run build | tee build.log | tail -3", True),
        ("a backgrounded tail -f follows a file", "tail -f run.log", True),
        ("a backgrounded job writing a file, then tail of that file", "npm run build > b.log 2>&1; tail -5 b.log", True),
        ("a backgrounded filter with no truncation", "npx vitest run 2>&1 | grep FAIL", True),
        ("a foreground command piped into tail", "git log --oneline | tail -5", False)):
    rc, hso, detail = decision_of(command, background)
    check(label + " is not denied", rc == 0 and hso.get("permissionDecision") != "deny", detail)

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
