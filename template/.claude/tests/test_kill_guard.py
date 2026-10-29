#!/usr/bin/env python3
"""Proof for kill_guard — prove it FIRES on the exact mistake that created it.

The incident: a session read `shell_census`'s TRUNCATED list, meant to kill a stray `grep`, matched
the wrong row, and killed its own `sidecar_fanout.py` run. So the load-bearing case is a taskkill
whose PID resolves to a sidecar fan-out, and the load-bearing NEGATIVES are the ordinary kills that
must keep working — a guard that blocks every kill gets bypassed rather than obeyed.

PID resolution is stubbed: the real one shells out to CIM, which no proof should depend on.

Run: python3 .claude/tests/test_kill_guard.py
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

import _settings_probe

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "kill_guard.py")
PROJECT = os.path.normpath(os.path.join(HERE, "..", ".."))
SETTINGS = _settings_probe.settings_path(os.path.join(HERE, ".."))
spec = importlib.util.spec_from_file_location("kg", HOOK)
kg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kg)

FAKE = {
    "21764": "python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json --compare|python.exe",
    "53236": "C:/Program Files/Git/bin/bash.exe .claude/scripts/codex_proxy_sidecar.sh -m luna|bash.exe",
    "9001": "grep -rl claude-gpt /c/Users/<user>/|grep.exe",
    "9002": "node C:/Users/<user>/AppData/.../semantic-search/server.js|node.exe",
    "9003": "claude.exe --resume|claude.exe",
    "9004": "pwsh -File .claude/scripts/gate_chain.ps1|pwsh.exe",
    "9005": "",
}


def stub(pid, msys_first=False, deadline=None):
    return FAKE.get(pid, None)


REAL_COMMANDLINE = kg.commandline
kg.commandline = stub
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def v(cmd, env=None):
    return kg.verdict(cmd, env or {})


def taskstop(*messages, transcript_path=None, task_id="agent-123", rows=None):
    with tempfile.TemporaryDirectory(prefix="taskstop_guard_") as tmp:
        path = transcript_path or os.path.join(tmp, "session.jsonl")
        if transcript_path is None:
            with open(path, "w", encoding="utf-8") as fh:
                source = rows if rows is not None else [
                    {"type": "user", "message": {"role": "user", "content": content}}
                    for content in messages
                ]
                for row in source:
                    fh.write(json.dumps(row) + "\n")
        return taskstop_payload({
            "tool_name": "TaskStop",
            "transcript_path": path,
            "tool_input": {"task_id": task_id},
        })


EMPTY_TABLE = os.path.join(tempfile.mkdtemp(prefix="kg_table_"), "table.json")
with open(EMPTY_TABLE, "w", encoding="utf-8") as _fh:
    _fh.write("{}")


def taskstop_payload(payload, table=None):
    """`table`: a planted process table file; by default an empty one, so no case reads the live machine."""
    result = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(os.environ, KILL_GUARD_PROCESS_TABLE=table or EMPTY_TABLE),
    )
    if result.returncode != 0 or "Traceback" in (result.stderr or ""):
        raise RuntimeError(result.stderr or "kill_guard crashed")
    output = (result.stdout or "").strip()
    if not output:
        return None
    hook = json.loads(output).get("hookSpecificOutput") or {}
    decision = hook.get("permissionDecision")
    if decision is None:
        return None
    return hook.get("permissionDecisionReason") or "<%s without a reason>" % decision


@case("the TaskStop helper reads a deny without a reason as a deny, not as allow")
def c_helper_reads_decision():
    real = subprocess.run

    def planted(stdout):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, "")

    try:
        subprocess.run = planted(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}))
        bare_deny = taskstop_payload({})
        subprocess.run = planted(json.dumps({"hookSpecificOutput": {"additionalContext": "note"}}))
        context_only = taskstop_payload({})
    finally:
        subprocess.run = real
    return bare_deny is not None and context_only is None


@case("THE INCIDENT: killing a sidecar fan-out is blocked and NAMES it")
def c_incident():
    r = v("MSYS_NO_PATHCONV=1 taskkill /PID 21764 /T /F") or ""
    return "BLOCKED" in r and "sidecar_fanout.py" in r and "billed" in r


@case("a sidecar launcher child is blocked too")
def c_launcher():
    return "BLOCKED" in (v("taskkill /PID 53236 /T /F") or "")


@case("a peer Claude session is blocked")
def c_peer():
    return "possibly a peer" in (v("taskkill /PID 9003 /F") or "")


@case("a gate run is blocked")
def c_gate():
    return "gate" in (v("taskkill /PID 9004 /F") or "").lower()


@case("NEGATIVE: killing the stray grep I actually meant is ALLOWED")
def c_stray():
    return v("MSYS_NO_PATHCONV=1 taskkill /PID 9001 /T /F") is None


@case("NEGATIVE: an unrelated node server is allowed")
def c_node():
    return v("taskkill /PID 9002 /F") is None


@case("NEGATIVE: a command with no kill in it is untouched")
def c_notakill():
    return v("git status && echo taskkilled") is None


@case("an UNRESOLVABLE pid fails CLOSED — an unread pid is an unread target")
def c_unresolvable():
    r = v("taskkill /PID 4242 /T /F") or ""
    return "could not resolve" in r


@case("unverified target deny cites Shell Discipline and routes through the reaper")
def c_unverified_channel():
    payload = {"tool_name": "Bash", "tool_input": {"command": "taskkill /PID 4242 /T /F"}}
    output = io.StringIO()
    with patch.object(kg, "commandline", return_value=None), \
            patch.object(kg.sys, "stdin", io.StringIO(json.dumps(payload))), \
            patch.object(kg.sys, "stdout", output):
        kg.main()
    try:
        hook = json.loads(output.getvalue())["hookSpecificOutput"]
        reason = hook["permissionDecisionReason"]
    except (KeyError, ValueError):
        return False
    return (hook.get("permissionDecision") == "deny" and "CLAUDE.core.md §Shell Discipline" in reason
            and "python3 .claude/tools/reap.py --help" in reason)


@case("an ALREADY-GONE pid is refused as stale rather than passed")
def c_gone():
    return "no such process" in (v("taskkill /PID 9005 /F") or "")


@case("the bypass works, and only for the pid it names")
def c_bypass():
    ok = v("HARNESS_ALLOW_KILL=21764 taskkill /PID 21764 /T /F",
           {"HARNESS_ALLOW_KILL": "21764"}) is None
    other = v("HARNESS_ALLOW_KILL=9001 taskkill /PID 21764 /T /F",
              {"HARNESS_ALLOW_KILL": "9001"}) is not None
    return ok and other


@case("posix `kill <pid>` is parsed too, not just taskkill")
def c_posix():
    return "BLOCKED" in (v("kill -9 21764") or "")


@case("the refusal points at KillShell/TaskStop for a Bash background task")
def c_routes():
    return "KillShell" in (v("taskkill /PID 21764 /F") or "")


@case("NEGATIVE: a `.claude/` path in the command line is not a Claude SESSION")
def c_dotclaude():
    FAKE["9006"] = "python3 .claude/tools/lf_normalize.py|python.exe"
    return v("taskkill /PID 9006 /F") is None


@case("several pids in one command are each checked")
def c_multi():
    r = v("taskkill /PID 9001 /F && taskkill /PID 21764 /F") or ""
    return "21764" in r and "9001" not in r


# ---- TaskStop decides on facts only: ended, or launched by this session ----------------------------
# Rule owner: kill_guard.py module docstring. Whether stopping a running task is wanted is the auto-mode
# classifier's to judge; the guard reads no owner word, stop verb, keep word or answer.

def _text_row(message):
    return {"type": "user", "message": {"role": "user", "content": message}}


def _answer_row(question, label):
    return {
        "type": "user",
        "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "toolu_question",
            "content": 'Your questions have been answered: "%s"="%s".' % (question, label),
        }]},
        "toolUseResult": {
            "questions": [{"question": question, "header": "Stop tasks", "multiSelect": False,
                           "options": [{"label": label, "description": ""}]}],
            "answers": {question: label},
        },
    }


def _bash_row(stdout):
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_bash",
                                                  "content": stdout}]},
        "toolUseResult": {"stdout": stdout, "stderr": "", "interrupted": False},
    }


def _launch_rows(task_id, command, *, name="Bash", background=True, sidechain=False, owner="ok"):
    use_id = "toolu_launch_" + task_id
    tool_input = {"command": command, "description": "probe"}
    if background:
        tool_input["run_in_background"] = True
    result = {"stdout": "", "stderr": "", "interrupted": False}
    if name == "Monitor":
        # A Monitor launch records its id as toolUseResult.taskId, never backgroundTaskId.
        tool_input = {"command": command, "description": "probe", "timeout_ms": 300000}
        result = {"taskId": task_id, "timeoutMs": 300000, "persistent": False}
    elif background:
        result["backgroundTaskId"] = task_id
    return [
        _text_row(owner),
        {"type": "assistant", "isSidechain": sidechain,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}]}},
        {"type": "user", "isSidechain": sidechain,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": use_id,
              "content": "Command running in background with ID: %s." % task_id}]},
         "toolUseResult": result},
    ]


RUNNING_LAUNCHES = {
    "a poll loop": ("while :; do grep -q DONE grid.log && exit 0; sleep 120; done", "Bash"),
    "a billed sidecar launch": ("bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt", "Bash"),
    "a benchmark grid": ("bash .claude/scripts/benchmark_campaign/run_grid.sh deepseek-v41-flash flash:1", "Bash"),
    "a gate run": ("pwsh -File .claude/scripts/gate_chain.ps1", "Bash"),
    "a harness proof run": ("python3 .claude/scripts/harness_tests.py --close", "Bash"),
    "a Monitor poll loop": ("while true; do grep -E '^VERDICT=' gate.out && break; sleep 10; done", "Monitor"),
    "an Agent": ("sleep 60", "Agent"),
}


@case("a running task this session launched passes whatever it runs: the classifier judges whether to stop it")
def c_taskstop_own_running_passes():
    bad = [label for label, (command, tool) in RUNNING_LAUNCHES.items()
           if taskstop(rows=_launch_rows("own%d" % len(label), command, name=tool),
                       task_id="own%d" % len(label)) is not None]
    assert not bad, "denied: %s" % bad
    return True


@case("a background Agent, recorded by agentId and isAsync alone, passes; a foreground Agent's agentId does not")
def c_taskstop_async_agent_launch():
    rows = _launch_rows("aasync01", "sleep 60", name="Agent")
    rows[2]["toolUseResult"] = {"agentId": "aasync01", "isAsync": True}
    foreground = _launch_rows("afore001", "sleep 60", name="Agent")
    foreground[2]["toolUseResult"] = {"agentId": "afore001", "isAsync": False}
    return (taskstop(rows=rows, task_id="aasync01") is None
            and "no launch of task" in (taskstop(rows=foreground, task_id="afore001") or ""))


@case("an own foreground shell that timed out into the background passes")
def c_taskstop_own_timed_out_shell():
    rows = _launch_rows("bgtimeout1", "git clone -q https://example.invalid/repo.git repo", background=False)
    rows[2]["toolUseResult"].update(backgroundTaskId="bgtimeout1", timedOutAfterMs=300000)
    return taskstop(rows=rows, task_id="bgtimeout1") is None


@case("owner wording decides nothing for a task this session launched: a keep-running message does not deny it")
def c_taskstop_keep_words_grant_no_deny():
    rows = _launch_rows("bgkeep1", "bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt",
                        owner="No, don't stop it. Let it finish.")
    return taskstop(rows=rows, task_id="bgkeep1") is None


@case("owner wording grants nothing for a task this session did not launch: a stop order, a stop answer and a quoted id all deny")
def c_taskstop_stop_words_grant_nothing():
    question = "Can I TaskStop bhunx053s?"
    cases = (
        taskstop("Stop agent-123 now."),
        taskstop("Stop it.", task_id="task-b"),
        taskstop(rows=[_answer_row(question, "Yes, stop it (Recommended)")], task_id="bhunx053s"),
        taskstop(rows=[_text_row("ok"), _bash_row("will stop bhunx053s shortly")], task_id="bhunx053s"),
    )
    return all("no launch of task" in (reason or "") for reason in cases)


def _padding(megabytes):
    """Owner-neutral rows that push everything before them out of a fixed-size tail read."""
    return [_text_row("filler " * 20000)] * (megabytes * 7)


@case("a task launched early in a long transcript still passes: the launch fact is not a tail read")
def c_taskstop_launch_beyond_a_tail_window():
    rows = _launch_rows("bgearly1", "bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt")
    rows += _padding(10)
    return taskstop(rows=rows, task_id="bgearly1") is None


@case("a task that ended early in a long transcript still passes")
def c_taskstop_ended_beyond_a_tail_window():
    rows = _launch_rows("bgearly2", "sleep 60") + [_ended_row("bgearly2")] + _padding(10)
    return taskstop(rows=rows, task_id="bgearly2") is None


@case("a subagent's (sidechain) background shell is not this session's: denied")
def c_taskstop_sidechain_shell_denied():
    rows = _launch_rows("bgside3", "sleep 600", sidechain=True)
    return "no launch of task" in (taskstop(rows=rows, task_id="bgside3") or "")


@case("a task launched under ANOTHER id grants nothing for this id")
def c_taskstop_other_id_denied():
    rows = _launch_rows("bgpoll9", "sleep 600")
    return "no launch of task" in (taskstop(rows=rows, task_id="bgpoll8") or "")


@case("TaskStop with no task id in the call is denied")
def c_taskstop_no_task_id():
    reason = taskstop_payload({"tool_name": "TaskStop", "tool_input": {}}) or ""
    return "no launch of task" in reason


@case("a transcript that cannot be read leaves the launch unproven: denied")
def c_taskstop_unreadable():
    missing = os.path.join(tempfile.gettempdir(), "no-such-taskstop-transcript.jsonl")
    return "no launch of task" in (taskstop(transcript_path=missing) or "")


@case("the deny names the task and one next step, and offers no owner-wording route")
def c_taskstop_deny_text():
    reason = taskstop(rows=[], task_id="peer-task-77") or ""
    return ("peer-task-77" in reason and "Shell Discipline" in reason
            and reason.count("Stop it from") == 1
            and "owner" not in reason.lower() and "Let it finish" not in reason)


@case("E5: a non-object TaskStop payload fails closed with plumbing recovery")
def c_taskstop_array_payload():
    reason = taskstop_payload([]) or ""
    return ("hook could not read its input" in reason
            and "Re-issue the same call" in reason
            and "if it repeats, report it" in reason
            and "skill" not in reason.lower())


@case("kill_guard is registered for TaskStop")
def c_taskstop_registered():
    with open(SETTINGS, "r", encoding="utf-8") as fh:
        settings = json.load(fh)
    for group in settings.get("hooks", {}).get("PreToolUse", []):
        if "TaskStop" not in str(group.get("matcher", "")):
            continue
        if any("kill_guard.py" in str(hook.get("command", "")) for hook in group.get("hooks", [])):
            return True
    return False


@case("the owner-word machinery is gone: no stop verb, keep word, answer reader or quiet-shell reader remains")
def c_taskstop_owner_word_machinery_retired():
    gone = ("STOP_LABEL_RE", "KEEP_LABEL_RE", "_answer_verdict", "TASKSTOP_DIRECTIVE_RE", "_engages_task",
            "taskstop_authorized", "_owner_inputs", "_consent_text", "_answer_pairs", "quiet_shell_blocker",
            "own_quiet_shell")
    return not [name for name in gone if hasattr(kg, name)]


def _ended_row(task_id, status="completed"):
    return {"type": "queue-operation", "operation": "enqueue",
            "content": "<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n"
                       "<summary>Agent finished</summary>\n</task-notification>" % (task_id, status)}


@case("a task whose completion notice is in the transcript passes even with no launch row: a stop discards nothing")
def c_taskstop_ended_passes():
    return taskstop(rows=[_ended_row("aended01")], task_id="aended01") is None


@case("an ended billed sidecar launch passes too: the paid work already finished")
def c_taskstop_ended_billed_allowed():
    rows = _launch_rows("bgended1", "bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt") + [_ended_row("bgended1", "failed")]
    return taskstop(rows=rows, task_id="bgended1") is None


@case("NEGATIVE: another task's completion notice grants nothing for this one")
def c_taskstop_other_task_ended_denied():
    rows = [_ended_row("aother99")]
    return "no launch of task" in (taskstop(rows=rows, task_id="aended02") or "")


@case("NEGATIVE: a task id quoted in tool output without a task-notification is not an ended task")
def c_taskstop_quoted_id_not_ended():
    rows = [_bash_row("grep says <task-id>aended03</task-id> <status>completed</status>")]
    return "no launch of task" in (taskstop(rows=rows, task_id="aended03") or "")


def _with_resolvers(cim, winpid):
    saved = (kg._cim_commandline, kg._ps_winpid)
    kg._cim_commandline, kg._ps_winpid = cim, winpid
    return saved


@case("killing a benchmark grid process by pid is blocked and names it")
def c_kill_grid_pid_blocked():
    FAKE["9006"] = "bash C:/Users/<user>/Game_Dev/BenchmarkArms/logs/x/run_grid.snapshot.20260915T161253Z.sh x sol:1|bash.exe"
    r = v("taskkill /PID 9006 /T /F") or ""
    return "BLOCKED" in r and "benchmark grid" in r


# ---- MSYS pids -----------------------------------------------------------------------------------
# Git Bash `kill <pid>` takes MSYS pids, which Windows CIM does not know: resolving one directly reads
# as "no such process" and refuses a justified kill (measured 2026-09-15 on a live waiter, MSYS 802751).
# The guard maps the MSYS pid to its WINPID through `ps -p` output first.
PS_SAMPLE = ("      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n"
             "   874263       1  874240      10924  ?         197608 12:09:15 /usr/bin/bash\n")


@case("an MSYS pid maps to its WINPID from ps -p output")
def c_msys_winpid_parsed():
    return kg._winpid_from_ps(PS_SAMPLE, "874263") == "10924"


@case("ps output that does not list the pid maps to nothing")
def c_msys_winpid_absent():
    return (kg._winpid_from_ps(PS_SAMPLE, "999") is None
            and kg._winpid_from_ps("", "874263") is None)


@case("a Git Bash kill resolves the MSYS pid through ps before trusting a same-numbered Windows pid")
def c_kill_msys_pid_mapped_first():
    table = {"4242": "bash .claude/scripts/deepseek_sidecar.sh -m flash|bash.exe", "777": "notepad.exe|notepad.exe"}
    saved = _with_resolvers(lambda pid: table.get(pid, ""), lambda pid: "4242" if pid == "777" else None)
    try:
        return (kg.pid_forms("kill 777") == [("777", True)]
                and "sidecar" in (REAL_COMMANDLINE("777", msys_first=True) or "")
                and (REAL_COMMANDLINE("777") or "").startswith("notepad"))
    finally:
        kg._cim_commandline, kg._ps_winpid = saved


@case("taskkill /PID stays a Windows pid (no ps mapping)")
def c_taskkill_pid_form_is_windows():
    return kg.pid_forms("taskkill /PID 9001 /T /F") == [("9001", False)]


@case("an exhausted hook budget resolves nothing: the kill fails closed as unresolvable")
def c_kill_budget_exhausted_unresolvable():
    saved = _with_resolvers(lambda pid: "notepad.exe|notepad.exe", lambda pid: None)
    try:
        return REAL_COMMANDLINE("777", deadline=kg._now() - 1) is None
    finally:
        kg._cim_commandline, kg._ps_winpid = saved


WINDOW_CLOSERS = {
    "a SendMessageTimeout SC_CLOSE sweep": 'python3 - <<EOF\nu.SendMessageTimeoutW(h, 0x112, 0xF060, 0, 2, 2000, None)\nEOF',
    "a PostMessage WM_CLOSE": "powershell -Command \"[W]::PostMessage($h, 0x10, 0, 0)\"",
    "CloseMainWindow": "powershell -Command \"Get-Process python | ForEach-Object { $_.CloseMainWindow() }\"",
    "Stop-Process -Name WindowsTerminal": "powershell -Command \"Stop-Process -Name WindowsTerminal -Force\"",
    "taskkill /IM WindowsTerminal.exe": "taskkill /IM WindowsTerminal.exe /F",
    "Get-Process piped to Stop-Process": "powershell -Command \"Get-Process OpenConsole | Stop-Process\"",
}


@case("closing terminal windows by handle, title or host process is denied (the 2026-10-05 bulk close)")
def c_window_close_denied():
    return all(kg.window_close_verdict(cmd) for cmd in WINDOW_CLOSERS.values())


@case("window-close deny names the owner as the closer and offers no self-written override")
def c_window_close_names_next_step():
    why = kg.window_close_verdict(WINDOW_CLOSERS["taskkill /IM WindowsTerminal.exe"])
    return "Ask the owner" in why and "HARNESS_ALLOW_WINDOW_CLOSE" not in why


@case("a self-written HARNESS_ALLOW_WINDOW_CLOSE reason never passes a handle, title or host close")
def c_window_close_reason_is_not_a_pass():
    return all(kg.window_close_verdict(c) for c in (
        "HARNESS_ALLOW_WINDOW_CLOSE=mine " + WINDOW_CLOSERS["taskkill /IM WindowsTerminal.exe"],
        "HARNESS_ALLOW_WINDOW_CLOSE=mine " + WINDOW_CLOSERS["CloseMainWindow"],
        "HARNESS_ALLOW_WINDOW_CLOSE='quoted reason' powershell -Command \"Stop-Process -Name WindowsTerminal\""))


@case("window-close negatives: listing windows and adjacent process work pass")
def c_window_close_negatives():
    ok = ("powershell -Command \"Get-Process WindowsTerminal | Select-Object Id,MainWindowTitle\"",
          "taskkill /PID 9001 /T /F", "python3 .claude/tests/test_kill_guard.py", "git log --oneline -5")
    return all(kg.window_close_verdict(c) is None for c in ok)


@case("must-allow: grep, rg, cat, git show and Select-String that only mention a close API or closer script pass")
def c_window_close_read_only_mentions():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, ".claude", "scratch")
        os.makedirs(scratch)
        with open(os.path.join(scratch, "closer.py"), "w", encoding="utf-8") as fh:
            fh.write("u.SendMessageTimeoutW(h, 0x112, 0xF060, 0, 2, 2000, None)\n")
        reads = ("grep -n 'WM_CLOSE' .claude/hooks/kill_guard.py",
                 'grep -n "CloseMainWindow" .claude/hooks/kill_guard.py | head -5',
                 "rg -n 'taskkill /IM WindowsTerminal' .claude",
                 "cat .claude/scratch/closer.py",
                 "git show HEAD:.claude/scratch/closer.py",
                 "Select-String -Pattern 'Stop-Process -Name WindowsTerminal' -Path .claude/hooks/kill_guard.py")
        bad = [c for c in reads if kg.window_close_verdict(c, tmp)]
        return not bad and bool(kg.window_close_verdict("python3 .claude/scratch/closer.py", tmp))


@case("must-allow: quoted pipes, semicolons and literal substitutions are reader arguments")
def c_window_close_quoted_readers():
    commands = (
        'grep -E "CloseMainWindow|WM_CLOSE" .claude/hooks/kill_guard.py',
        "grep -n 'foo; WM_CLOSE' .claude/hooks/kill_guard.py",
        "grep -n '$(powershell -c CloseMainWindow)' x.py",
        "grep -n '`powershell -c CloseMainWindow`' x.py",
        'grep -n "foo; WM_CLOSE" x.py | head -5',
        'grep -n "CloseMainWindow\\\"|WM_CLOSE" x.py',
    )
    return all(taskstop_payload({"tool_name": "Bash", "tool_input": {"command": cmd}}) is None
               for cmd in commands)


@case("substitutions inside readers execute, including backticks and double-quoted arguments")
def c_window_close_reader_substitutions():
    commands = (
        'cat $(powershell -c "(Get-Process WindowsTerminal).CloseMainWindow()")',
        'cat "$(powershell -Command \'Stop-Process -Name WindowsTerminal\')"',
        'cat `powershell -c "Stop-Process -Name WindowsTerminal"`',
        'cat "`powershell -c \'Stop-Process -Name WindowsTerminal\'`"',
        'cat "$(cat "$(powershell -c \'Stop-Process -Name WindowsTerminal\')")"',
    )
    return all(taskstop_payload({"tool_name": "Bash", "tool_input": {"command": cmd}}) is not None
               for cmd in commands)


@case("git shell aliases and sed execution are not reader stages; rm is not a reader")
def c_window_close_argument_execution():
    return ("rm" not in kg.READ_ONLY_VERBS and all(kg.window_close_verdict(cmd) for cmd in (
        "git -c alias.x='!powershell -c \"Stop-Process -Name WindowsTerminal\"' x",
        "sed '1e powershell -c \"Stop-Process -Name WindowsTerminal\"' input.txt")))


@case("every executed scratch script in a pipeline is inspected, not the first named file")
def c_window_close_later_pipeline_script():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, ".claude", "scratch")
        os.makedirs(scratch)
        for name, body in (("input.py", "print('input')\n"),
                           ("closer.py", "u.PostMessageW(h, 0x10, 0, 0)\n")):
            with open(os.path.join(scratch, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
        commands = (
            "cat .claude/scratch/input.py | python .claude/scratch/closer.py",
            "python .claude/scratch/input.py | python .claude/scratch/closer.py",
            'cat "$(python .claude/scratch/closer.py)"',
        )
        return all(taskstop_payload({"tool_name": "Bash", "cwd": tmp,
                                    "tool_input": {"command": cmd}}) is not None for cmd in commands)


@case("unquoted host pipelines and balanced parentheses in substitutions still deny")
def c_window_close_unquoted_execution():
    return all(kg.window_close_verdict(cmd) for cmd in (
        "Get-Process WindowsTerminal | Stop-Process",
        "cat $(powershell -c (Get-Process WindowsTerminal).CloseMainWindow())",
    ))


@case("a reader naming a closer beside an executing stage still reads only")
def c_window_close_pipeline_data_argument():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, ".claude", "scratch")
        os.makedirs(scratch)
        with open(os.path.join(scratch, "closer.py"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("CloseMainWindow()\n")
        return kg.window_close_verdict("cat .claude/scratch/closer.py | python safe.py", tmp) is None


@case("an executed close still denies after a read-only segment in the same command")
def c_window_close_read_then_execute():
    return bool(kg.window_close_verdict("grep -n WM_CLOSE x.py; " + WINDOW_CLOSERS["Stop-Process -Name WindowsTerminal"]))


@case("a scratch script that closes windows is read and denied; a non-scratch script is not scanned")
def c_window_close_scratch_script():
    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, ".claude", "scratch")
        tests = os.path.join(tmp, ".claude", "tests")
        os.makedirs(scratch)
        os.makedirs(tests)
        for folder in (scratch, tests):
            with open(os.path.join(folder, "closer.ps1"), "w", encoding="utf-8") as fh:
                fh.write("SendMessageTimeout(h, 0x112, 0xF060)\n")
        return (bool(kg.window_close_verdict("powershell -File .claude/scratch/closer.ps1", tmp))
                and kg.window_close_verdict("powershell -File .claude/tests/closer.ps1", tmp) is None)


@case("main() routes a Bash window close through the deny channel")
def c_window_close_main_channel():
    def channel(cmd):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout
    deny = [channel(c) for c in (WINDOW_CLOSERS["taskkill /IM WindowsTerminal.exe"],
                                 "HARNESS_ALLOW_WINDOW_CLOSE=x taskkill /IM WindowsTerminal.exe /F")]
    allow = channel("grep -n 'CloseMainWindow' .claude/hooks/kill_guard.py")
    return (all(rc == 0 and '"permissionDecision": "deny"' in out for rc, out in deny)
            and allow[0] == 0 and "deny" not in allow[1])


NAMED_ROWS = (
    (100, "claude.exe", "C:/Tools/claude.exe --resume"),
    (101, "python.exe", "python3 .claude/tools/sidecar_fanout.py jobs.json --compare"),
    (102, "pwsh.exe", "pwsh -File .claude/scripts/gate_chain.ps1"),
    (103, "node.exe", "node C:/srv/app/server.js"),
    (104, "python.exe", "python3 -m http.server 8000"),
    (105, "grep.exe", "grep -rl foo ."),
)


def _named_table(rows=NAMED_ROWS):
    fd, path = tempfile.mkstemp(prefix="kg_named_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({str(pid): {"pid": pid, "ppid": 1, "name": name, "cmd": cmd} for pid, name, cmd in rows}, fh)
    return path


def nv(cmd, table=None, env=None):
    path = table or _named_table()
    old = os.environ.get("KILL_GUARD_PROCESS_TABLE")
    os.environ["KILL_GUARD_PROCESS_TABLE"] = path
    try:
        return kg.verdict(cmd, env or {})
    finally:
        if old is None:
            os.environ.pop("KILL_GUARD_PROCESS_TABLE", None)
        else:
            os.environ["KILL_GUARD_PROCESS_TABLE"] = old


NAME_KILLS_DENIED = {
    "pkill -f <pattern>": "pkill -f sidecar_fanout",
    "pkill <name>": "pkill claude",
    "pkill python (one of two matches protected)": "pkill python",
    "pkill with a combined flag cluster": "pkill -9 -fx 'python3 .claude/tools/sidecar_fanout.py jobs.json --compare'",
    "killall <name>": "killall claude",
    "taskkill /IM": "taskkill /IM claude.exe /F",
    "taskkill //IM (MSYS escape)": "taskkill //IM claude.exe //F",
    "taskkill /FI IMAGENAME": "taskkill /FI \"IMAGENAME eq claude.exe\" /F",
    "taskkill /FI WINDOWTITLE (unresolvable)": "taskkill /FI \"WINDOWTITLE eq build*\" /F",
    "Stop-Process -Name": "Stop-Process -Name claude -Force",
    "Stop-Process -Name list with a wildcard": "Stop-Process -Name 'node','clau*' -Force",
    "Stop-Process -Id": "Stop-Process -Id 100 -Force",
    "Get-Process <n> | Stop-Process (top level)": "Get-Process claude | Stop-Process -Force",
    "Get-Process <n> | Stop-Process (quoted payload)": "powershell -Command \"Get-Process claude | Stop-Process -Force\"",
    "Get-Process | Where-Object | Stop-Process (unresolvable)":
        "Get-Process | Where-Object { $_.CPU -gt 5 } | Stop-Process",
    "wmic name delete": "wmic process where \"name='claude.exe'\" delete",
    "wmic commandline like call terminate": "wmic process where \"commandline like '%sidecar_fanout%'\" call terminate",
    "Win32_Process Terminate": "Get-CimInstance Win32_Process -Filter \"name='claude.exe'\" | Invoke-CimMethod -MethodName Terminate",
    "kill $(pgrep -f <pattern>)": "kill $(pgrep -f sidecar_fanout)",
    "pgrep | xargs kill": "pgrep -f sidecar_fanout | xargs kill -9",
    "kill $(pidof <name>)": "kill -9 $(pidof claude)",
    "a pkill inside bash -c, after other text": "bash -c \"echo go; pkill -f claude; true\"",
    "a pkill after env assignments": "FOO=1 pkill claude",
    "HARNESS_ALLOW_KILL does not cover a name kill": "HARNESS_ALLOW_KILL=100 pkill claude",
    "pkill with an invalid regex fails closed": "pkill -f '('",
    "pkill with no pattern fails closed": "pkill -9",
}


@case("every name- or pattern-addressed kill form that matches a protected process is denied")
def c_name_kills_denied():
    missed = [label for label, cmd in NAME_KILLS_DENIED.items()
              if "BLOCKED kill" not in (nv(cmd, env={"HARNESS_ALLOW_KILL": "100"} if "ALLOW" in label else None) or "")]
    assert not missed, "not denied: %s" % missed
    return True


NAME_KILLS_ALLOWED = {
    "pkill -f of an unprotected pattern": "pkill -f http.server",
    "pkill of an unprotected name": "pkill node",
    "killall of an unprotected name": "killall node",
    "taskkill /IM of an unprotected image": "taskkill /IM node.exe /F",
    "Stop-Process -Name of an unprotected name": "Stop-Process -Name node -Force",
    "Get-Process <n> | Stop-Process, unprotected": "Get-Process node | Stop-Process",
    "wmic of an unprotected name": "wmic process where \"name='node.exe'\" delete",
    "a name that matches nothing": "pkill -f nothing_runs_this",
    "pgrep alone is a read": "pgrep -f sidecar_fanout",
    "the reaper": "python3 .claude/tools/reap.py --help",
    "grep for the word": "grep pkill notes.md",
    "echo of a taskkill line": "echo taskkill /IM claude.exe",
    "a commit message that mentions pkill": "git commit -m \"document pkill handling\"",
    "kill of a shell job": "sleep 100 & kill $!",
}


@case("NEGATIVE: unprotected name kills, the reaper and read-only mentions are allowed")
def c_name_kills_allowed():
    blocked = [label for label, cmd in NAME_KILLS_ALLOWED.items() if nv(cmd)]
    assert not blocked, "blocked: %s" % blocked
    return True


@case("a name kill whose table cannot be read fails CLOSED; a read-only pgrep does not need the table")
def c_name_kill_table_unreadable():
    missing = os.path.join(tempfile.gettempdir(), "kg_no_such_table.json")
    denied = nv("pkill node", table=missing) or ""
    return "cannot read" in denied and nv("pgrep node", table=missing) is None


@case("the deny names the protected match and points to the reaper")
def c_name_kill_message():
    out = nv("pkill claude") or ""
    return "PID 100" in out and "reap.py" in out and "PID 103" not in out


@case("main() routes a name kill through the deny channel")
def c_name_kill_main_channel():
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "pkill -f sidecar_fanout"}})
    env = dict(os.environ, KILL_GUARD_PROCESS_TABLE=_named_table())
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, timeout=30, env=env)
    return r.returncode == 0 and '"permissionDecision": "deny"' in r.stdout


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
