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

# The proof may run inside a delegate: plant the main channel, then each case plants its own caller.
for _k in [k for k in os.environ if k.startswith("CLAUDE_CODE_SIDECAR") or k == "CLAUDE_CODE_ENTRYPOINT"]:
    del os.environ[_k]

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


for label, command in (("a subshell backgrounded with a trailing ampersand", "( make check ) &"),
                       ("a line ending in a background ampersand", "pytest -q &\necho done")):
    rc, context, detail = advisory(command)
    check(label + " draws the run_in_background advisory",
          rc == 0 and "run_in_background" in context, detail)

for label, command, background in (
        ("a logical and is not a background job", "make check && echo ok", False),
        ("a redirect into a descriptor is not a background job", "make check 2>&1", False),
        ("an ampersand inside quotes is not a background job", 'echo "tail &"', False),
        ("an ampersand in a heredoc body is data", "cat <<'EOF'\nline &\nEOF", False),
        ("an ampersand on a call already detached with run_in_background is not repeated", "( make check ) &", True)):
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


# A caller no wake reaches (a print-mode child, a Workflow agent) has a 5-minute prompt cache: a foreground call
# longer than SUBAGENT_WAIT_MS returns to a cold cache and rewrites the whole context. Its timeout is clamped,
# the client moves a command still running at the clamp to the background, and the appended EXIT line in that
# command's output file is the caller's done-test.
SUBAGENT_WAIT_MS = 270000


def timed(command, timeout, **caller):
    payload = {"session_id": "proof-fwg-3", "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, "timeout": timeout}}
    payload.update(caller)
    return payload


def rewrite(payload, entrypoint, sidecar=""):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SIDECAR"}
    env.update(CLAUDE_CODE_ENTRYPOINT=entrypoint, **({"CLAUDE_CODE_SIDECAR": sidecar} if sidecar else {}))
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=30, env=env)
    hso = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
    return r.returncode, hso.get("updatedInput"), hso.get("permissionDecision"), r.stdout + r.stderr


RENDER = "node design/live/frames.mjs --out /tmp/a1"
for label, payload, entrypoint in (
        ("a print-mode child's", timed(RENDER, 600000), "sdk-cli"),
        ("a Workflow agent's", timed(RENDER, 590000, agent_id="a1", agent_type="workflow-subagent"), "cli")):
    rc, updated, verdict, detail = rewrite(payload, entrypoint)
    check(label + " long foreground command is clamped to the cache bound with an EXIT done line",
          rc == 0 and verdict is None and updated is not None
          and updated.get("timeout") == SUBAGENT_WAIT_MS and not updated.get("run_in_background")
          and updated.get("command", "").startswith(RENDER)
          and updated.get("command", "").rstrip().endswith('echo "EXIT $?"'), detail)

rc, updated, verdict, detail = rewrite(timed(RENDER, 600000), "cli", sidecar="deepseek")
check("a seat launcher's child (entrypoint cli, CLAUDE_CODE_SIDECAR set) is never woken: clamped to the cache bound",
      rc == 0 and updated is not None and updated.get("timeout") == SUBAGENT_WAIT_MS, detail)

rc, updated, verdict, detail = rewrite(timed(RENDER, 200000), "sdk-cli")
check("a print-mode child's command inside the cache bound passes untouched",
      rc == 0 and updated is None and verdict is None, detail)

rc, updated, verdict, detail = rewrite(timed(RENDER, 600000), "cli")
check("the interactive orchestrator's raised timeout is clamped to the default in the foreground, never to the cache bound",
      rc == 0 and updated is not None and not updated.get("run_in_background")
      and updated.get("timeout") == 120000 and updated.get("command") == RENDER, detail)

LOOP_WAIT = "until grep -q done a2.log; do sleep 30; done"
# The clamp is the child's prompt-cache lifetime: a child on the 1-hour cache (`CLAUDE_CODE_PROMPT_CACHE_TTL=1h`, set
# by its launcher) waits to the Bash ceiling; a 5-minute or unknown one keeps SUBAGENT_WAIT_MS.
for ttl, want in (("1h", 590000), ("5m", SUBAGENT_WAIT_MS), (None, SUBAGENT_WAIT_MS)):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_PROMPT_CACHE_TTL"}
    env.update(CLAUDE_CODE_ENTRYPOINT="sdk-cli", **({"CLAUDE_CODE_PROMPT_CACHE_TTL": ttl} if ttl else {}))
    for shape, cmd in (("command", RENDER), ("until-loop", LOOP_WAIT)):
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(timed(cmd, 590000)), capture_output=True,
                           text=True, encoding="utf-8", timeout=30, env=env)
        upd = (json.loads(r.stdout).get("hookSpecificOutput", {}).get("updatedInput") if r.stdout.strip() else None) or {}
        check("a %s child's 590000 ms %s wait runs at %d ms" % (ttl or "no-TTL", shape, want),
              r.returncode == 0 and upd.get("timeout", 590000) == want, r.stdout + r.stderr)

LOOP = "for i in $(seq 1 40); do grep -q 'facts in' a2.log && break; sleep 20; done"
rc, updated, verdict, detail = rewrite(timed(LOOP, 600000), "sdk-cli")
check("a for-loop wait is a wait loop: clamped, with no EXIT line added",
      rc == 0 and updated is not None and updated.get("timeout") == SUBAGENT_WAIT_MS
      and updated.get("command") == LOOP, detail)

# A raised foreground timeout keeps the call in the foreground at the default timeout: a job that ends in time returns
# this turn with no wake. Only the interactive orchestrator is rewritten.
import tempfile
import uuid

BASH_DEFAULT_MS = 120000
STATE_DIR = tempfile.mkdtemp(prefix="fwg-state-")


def raised(command, timeout_ms, extra=None, env=None):
    payload = {"session_id": "%s-fwg" % uuid.uuid4().hex, "hook_event_name": "PreToolUse",
               "tool_name": "Bash", "tool_input": {"command": command, "timeout": timeout_ms}}
    payload.update(extra or {})
    merged = dict(os.environ)
    merged.pop("CLAUDE_CODE_ENTRYPOINT", None)
    merged["HARNESS_HOOK_STATE_DIR"] = STATE_DIR
    merged.update(env or {})
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", timeout=30, env=merged)
    hso = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
    return r.returncode, hso, r.stdout + r.stderr


rc, hso, detail = raised("python3 sidecar_fanout.py --detach", 600000)
updated = hso.get("updatedInput") or {}
note = hso.get("additionalContext", "")
check("a raised timeout stays in the foreground", rc == 0 and bool(updated) and not updated.get("run_in_background"), detail)
check("a raised timeout is clamped to the default", updated.get("timeout") == BASH_DEFAULT_MS, detail)
check("the raised-timeout note cites the rule and promises no wake",
      "CLAUDE.interactive.md" in note and "one wake" not in note, detail)

rc, hso, detail = raised("python3 x.py", BASH_DEFAULT_MS)
check("a timeout at the default is left alone", rc == 0 and not hso.get("updatedInput"), detail)

rc, hso, detail = raised("python3 x.py", 600000, {"agent_id": "a1", "agent_type": "general-purpose"},
                         {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
check("a delegate's raised timeout is clamped to the cache bound, not kept",
      rc == 0 and (hso.get("updatedInput") or {}).get("timeout") == SUBAGENT_WAIT_MS, detail)

with tempfile.TemporaryDirectory() as tmp:
    with open(os.path.join(tmp, "harness_proof_durations.json"), "w", encoding="utf-8") as fh:
        json.dump({"test_timed_fit_probe.py": 200}, fh)
    env = {"HARNESS_TEST_STAMP": os.path.join(tmp, "stamp.json")}
    rc, hso, detail = raised("python3 .claude/tests/test_timed_fit_probe.py", 300000, None, env)
    check("a proof batch whose recorded time fits the raise keeps its raise",
          rc == 0 and not hso.get("updatedInput"), detail)
    rc, hso, detail = raised("python3 .claude/tests/test_timed_fit_probe.py", 150000, None, env)
    check("a proof batch whose recorded time exceeds the raise is clamped",
          rc == 0 and (hso.get("updatedInput") or {}).get("timeout") == BASH_DEFAULT_MS, detail)

# A foreground pytest run that names no single test file is the whole battery: a woken caller gets the batch advice.
# One that names a test file is a narrow run and stays silent.
for label, command, advised in (
        ("a wide pytest run", "python3 -m pytest -n auto -q .claude/tests", True),
        ("a bare pytest run", "pytest -q", True),
        ("a pytest run naming one test file", "python3 -m pytest -q .claude/tests/test_main_check.py", False),
        ("a pytest run naming one case", "python3 -m pytest .claude/tests/test_a.py::test_b", False),
        ("a pytest help call", "python3 -m pytest --help", False),
        ("a grep that names pytest", "grep -n pytest .claude/tests/conftest.py", False)):
    rc, hso, detail = raised(command, BASH_DEFAULT_MS)
    got = "whole battery" in hso.get("additionalContext", "")
    check("%s %s the batch advice" % (label, "draws" if advised else "never draws"), rc == 0 and got == advised, detail)

# A foreground loop wait is bounded by its per-call clamp, never by a running total. The job a print-mode child waits
# on is almost always its own run, and a run it started in its own shell ends with the child (reap end-seat), so a
# deny past 30 minutes of waits would force a return that kills the run it waits on. Eight clamped waits are 36 minutes.
def waits_in_one_session(entrypoint, agent, count, command):
    session = "%s-fwg" % uuid.uuid4().hex
    state = tempfile.mkdtemp(prefix="fwg-waits-")
    env = dict(os.environ, CLAUDE_CODE_ENTRYPOINT=entrypoint, HARNESS_HOOK_STATE_DIR=state)
    env.pop("CLAUDE_CODE_SIDECAR", None)
    env.pop("CLAUDE_CODE_PROMPT_CACHE_TTL", None)
    verdicts = []
    for _ in range(count):
        payload = timed(command, 600000, session_id=session, **({"agent_id": agent, "agent_type": "workflow-subagent"} if agent else {}))
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                           encoding="utf-8", timeout=30, env=env)
        hso = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
        verdicts.append((r.returncode, hso.get("permissionDecision"), (hso.get("updatedInput") or {}).get("timeout"), r.stdout))
    return verdicts


TASK_OUTPUT = "until grep -q 'EXIT' /tmp/claude/tasks/b1.output; do sleep 30; done"
for label, entrypoint, agent in (("a print-mode child's", "sdk-cli", ""), ("a Workflow agent's", "cli", "agent-hb")):
    verdicts = waits_in_one_session(entrypoint, agent, 8, TASK_OUTPUT)
    check(label + " eight clamped waits on its own run's output are never denied for their length",
          all(rc == 0 and verdict is None and timeout == SUBAGENT_WAIT_MS for rc, verdict, timeout, _ in verdicts),
          [v[:3] for v in verdicts])

print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
for f in FAILURES:
    print("  " + f)
sys.exit(1 if FAILURES else 0)
