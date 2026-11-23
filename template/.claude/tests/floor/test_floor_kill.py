"""Process stops, TaskStop and terminal-window closes: every case of v1's `test_kill_guard.py` (byte-identical in
both repos) as a row through `_floor.decide`.

PID resolution is planted (`kill_guard.commandline`), and name kills read a planted process table
(`KILL_GUARD_PROCESS_TABLE`), so no row reads the live machine.
"""
import json
import os

import pytest

import kill_guard as kg
from case import bash, decide, taskstop

FAKE = {
    "21764": "python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json --compare|python.exe",
    "53236": "C:/Program Files/Git/bin/bash.exe .claude/scripts/codex_proxy_sidecar.sh -m luna|bash.exe",
    "9001": "grep -rl claude-gpt /c/Users/<user>/|grep.exe",
    "9002": "node C:/Users/<user>/AppData/.../semantic-search/server.js|node.exe",
    "9003": "claude.exe --resume|claude.exe",
    "9004": "pwsh -File .claude/scripts/gate_chain.ps1|pwsh.exe",
    "9005": "",
    "9006": 'python3 -c "import time; time.sleep(600)  # sidecar_launch.py"|python3.exe',
    "9007": "python3 .claude/tools/sidecar_launch.py --job x|python3.exe",
    "9008": "bash .claude/scripts/watch_sidecar.sh|bash.exe",
    "9010": "python3 .claude/tools/lf_normalize.py|python.exe",
    "9011": "bash C:/Users/<user>/Game_Dev/BenchmarkArms/logs/x/run_grid.snapshot.20260915T161253Z.sh x sol:1|bash.exe",
}
REAL_COMMANDLINE = kg.commandline


@pytest.fixture(autouse=True)
def planted(tmp_path, monkeypatch):
    monkeypatch.setattr(kg, "commandline", lambda pid, msys_first=False, deadline=None: FAKE.get(pid))
    empty = tmp_path / "empty_table.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KILL_GUARD_PROCESS_TABLE", str(empty))
    monkeypatch.delenv("HARNESS_ALLOW_KILL", raising=False)
    return tmp_path


def reason(command, cwd=None):
    got = bash(command, cwd)
    return got.reason if got.denied else None


# --- pid kills ---------------------------------------------------------------------------------------------------
PID_DENIED = [
    ("the incident: a sidecar fan-out", "MSYS_NO_PATHCONV=1 taskkill /PID 21764 /T /F", "sidecar_fanout.py"),
    ("a sidecar launcher child", "taskkill /PID 53236 /T /F", "BLOCKED"),
    ("a python process running sidecar_launch.py", "taskkill /PID 9007 /F", "launcher child"),
    ("a bash script named *_sidecar.sh", "taskkill /PID 9008 /F", "launcher child"),
    ("a peer Claude session", "taskkill /PID 9003 /F", "possibly a peer"),
    ("a gate run", "taskkill /PID 9004 /F", "gate"),
    ("an unresolvable pid fails closed", "taskkill /PID 4242 /T /F", "could not resolve"),
    ("an already-gone pid is stale", "taskkill /PID 9005 /F", "no such process"),
    ("posix kill <pid>", "kill -9 21764", "BLOCKED"),
    ("the refusal points at KillShell/TaskStop", "taskkill /PID 21764 /F", "KillShell"),
    ("a benchmark grid by pid", "taskkill /PID 9011 /T /F", "benchmark grid"),
    ("a heredoc fed to bash is code", "bash <<'EOF'\nkill 21764\nEOF", "BLOCKED"),
    ("a heredoc fed to sh is code", "sh - <<EOF\nkill 21764\nEOF", "BLOCKED"),
    ("an inline bash program is code", "bash -c 'kill 21764'", "BLOCKED"),
    ("a real kill beside kill -0", "kill -0 21764 || kill 21764", "BLOCKED"),
    ("bypass names another pid", "HARNESS_ALLOW_KILL=9001 taskkill /PID 21764 /T /F", "BLOCKED"),
] + [("a kill after a quote, backtick or paren: " + c, c, "BLOCKED") for c in (
    'sh -c "kill 21764"', "echo `kill 21764`", "x=$(kill 21764)", "(kill -9 21764)", 'echo "$(kill 21764)"',
    "bash -lc 'sleep 1; kill 21764'")] + [("a quoted kill a code reader runs: " + c, c, "BLOCKED") for c in (
    'eval "kill 21764"', "eval 'sleep 1; kill 21764'", 'watch -n 5 "kill 21764"', 'su -c "kill 21764"',
    'su root -c "kill 21764"', 'flock /tmp/x.lock -c "kill 21764"', "script -q -c 'kill 21764' /dev/null")]
PID_ALLOWED = [
    ("a heredoc fed to python is data", "python3 - <<'EOF'\nkill 21764\ntaskkill /PID 21764 /T /F\npkill -f sidecar_fanout\nEOF"),
    ("a heredoc fed to cat is data", "cat > notes.md <<'EOF'\nkill 21764\ntaskkill /PID 21764 /T /F\npkill -f sidecar_fanout\nEOF"),
    ("a heredoc fed to python -c is data",
     "python3 -c \"print(1)\" <<'EOF'\nkill 21764\ntaskkill /PID 21764 /T /F\npkill -f sidecar_fanout\nEOF"),
    ("an inline python program is data", "python3 -c 'import os; print(\"kill 21764\")'"),
    ("kill -0 sends no signal", "kill -0 21764"),
    ("kill -0 inside bash -lc", "bash -lc 'kill -0 21764 2>/dev/null'"),
    ("a quoted mention: echo", 'echo "kill 21764"'),
    ("a quoted mention: echo single", "echo 'kill 21764 now'"),
    ("a quoted mention: commit message", 'git commit -q -m "kill 21764 left a seat running"'),
    ("a python shell only mentioning sidecar_launch.py", "taskkill /PID 9006 /F"),
    ("the stray grep I meant", "MSYS_NO_PATHCONV=1 taskkill /PID 9001 /T /F"),
    ("an unrelated node server", "taskkill /PID 9002 /F"),
    ("no kill in the command", "git status && echo taskkilled"),
    ("a .claude/ path is not a Claude session", "taskkill /PID 9010 /F"),
    ("the bypass covers the pid it names", "HARNESS_ALLOW_KILL=21764 taskkill /PID 21764 /T /F"),
]


@pytest.mark.parametrize("label,command,token", PID_DENIED, ids=[r[0] for r in PID_DENIED])
def test_pid_kill_denied(label, command, token):
    got = reason(command)
    assert got is not None and token.lower() in got.lower()


@pytest.mark.parametrize("label,command", PID_ALLOWED, ids=[r[0] for r in PID_ALLOWED])
def test_pid_kill_allowed(label, command):
    assert bash(command).kind == "allow"


def test_unverified_target_deny_cites_shell_discipline_and_the_reaper(monkeypatch):
    monkeypatch.setattr(kg, "commandline", lambda *a, **k: None)
    got = reason("taskkill /PID 4242 /T /F")
    assert "CLAUDE.core.md §Shell Discipline" in got and "python3 .claude/tools/reap.py --help" in got


def test_several_pids_are_each_checked():
    got = reason("taskkill /PID 9001 /F && taskkill /PID 21764 /F")
    assert "21764" in got and "9001" not in got


PS_SAMPLE = ("      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n"
             "   874263       1  874240      10924  ?         197608 12:09:15 /usr/bin/bash\n")


def test_msys_pid_maps_to_its_winpid():
    assert kg._winpid_from_ps(PS_SAMPLE, "874263") == "10924"
    assert kg._winpid_from_ps(PS_SAMPLE, "999") is None and kg._winpid_from_ps("", "874263") is None


def test_git_bash_kill_resolves_the_msys_pid_first(monkeypatch):
    table = {"4242": "bash .claude/scripts/deepseek_sidecar.sh -m flash|bash.exe", "777": "notepad.exe|notepad.exe"}
    monkeypatch.setattr(kg, "_cim_commandline", lambda pid: table.get(pid, ""))
    monkeypatch.setattr(kg, "_ps_winpid", lambda pid: "4242" if pid == "777" else None)
    assert kg.pid_forms("kill 777") == [("777", True)]
    assert "sidecar" in (REAL_COMMANDLINE("777", msys_first=True) or "")
    assert (REAL_COMMANDLINE("777") or "").startswith("notepad")
    assert kg.pid_forms("taskkill /PID 9001 /T /F") == [("9001", False)]


def test_exhausted_budget_resolves_nothing(monkeypatch):
    monkeypatch.setattr(kg, "_cim_commandline", lambda pid: "notepad.exe|notepad.exe")
    monkeypatch.setattr(kg, "_ps_winpid", lambda pid: None)
    assert REAL_COMMANDLINE("777", deadline=kg._now() - 1) is None


# --- name and pattern kills against a planted table -------------------------------------------------------------
NAMED_ROWS = (
    (100, "claude.exe", "C:/Tools/claude.exe --resume"),
    (101, "python.exe", "python3 .claude/tools/sidecar_fanout.py jobs.json --compare"),
    (102, "pwsh.exe", "pwsh -File .claude/scripts/gate_chain.ps1"),
    (103, "node.exe", "node C:/srv/app/server.js"),
    (104, "python.exe", "python3 -m http.server 8000"),
    (105, "grep.exe", "grep -rl foo ."),
)


@pytest.fixture
def named(planted, monkeypatch):
    path = planted / "named.json"
    path.write_text(json.dumps({str(p): {"pid": p, "ppid": 1, "name": n, "cmd": c} for p, n, c in NAMED_ROWS}),
                    encoding="utf-8")
    monkeypatch.setenv("KILL_GUARD_PROCESS_TABLE", str(path))
    return path


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


@pytest.mark.parametrize("label", list(NAME_KILLS_DENIED))
def test_name_kill_denied(named, label):
    got = reason(NAME_KILLS_DENIED[label])
    assert got is not None and "BLOCKED kill" in got


@pytest.mark.parametrize("label", list(NAME_KILLS_ALLOWED))
def test_name_kill_allowed(named, label):
    assert bash(NAME_KILLS_ALLOWED[label]).kind == "allow"


def test_name_kill_with_an_unreadable_table_fails_closed(planted, monkeypatch):
    monkeypatch.setenv("KILL_GUARD_PROCESS_TABLE", str(planted / "no_such_table.json"))
    assert "cannot read" in (reason("pkill node") or "")
    assert bash("pgrep node").kind == "allow"


def test_name_kill_deny_names_the_protected_match_and_the_reaper(named):
    got = reason("pkill claude")
    assert "PID 100" in got and "reap.py" in got and "PID 103" not in got


# --- terminal-window close ---------------------------------------------------------------------------------------
WINDOW_CLOSERS = {
    "a SendMessageTimeout SC_CLOSE sweep": 'python3 - <<EOF\nu.SendMessageTimeoutW(h, 0x112, 0xF060, 0, 2, 2000, None)\nEOF',
    "a PostMessage WM_CLOSE": "powershell -Command \"[W]::PostMessage($h, 0x10, 0, 0)\"",
    "CloseMainWindow": "powershell -Command \"Get-Process python | ForEach-Object { $_.CloseMainWindow() }\"",
    "Stop-Process -Name WindowsTerminal": "powershell -Command \"Stop-Process -Name WindowsTerminal -Force\"",
    "taskkill /IM WindowsTerminal.exe": "taskkill /IM WindowsTerminal.exe /F",
    "Get-Process piped to Stop-Process": "powershell -Command \"Get-Process OpenConsole | Stop-Process\"",
}
WINDOW_DENIED = list(WINDOW_CLOSERS.values()) + [
    "HARNESS_ALLOW_WINDOW_CLOSE=mine " + WINDOW_CLOSERS["taskkill /IM WindowsTerminal.exe"],
    "HARNESS_ALLOW_WINDOW_CLOSE=mine " + WINDOW_CLOSERS["CloseMainWindow"],
    "HARNESS_ALLOW_WINDOW_CLOSE='quoted reason' powershell -Command \"Stop-Process -Name WindowsTerminal\"",
    'cat $(powershell -c "(Get-Process WindowsTerminal).CloseMainWindow()")',
    'cat "$(powershell -Command \'Stop-Process -Name WindowsTerminal\')"',
    'cat `powershell -c "Stop-Process -Name WindowsTerminal"`',
    'cat "`powershell -c \'Stop-Process -Name WindowsTerminal\'`"',
    'cat "$(cat "$(powershell -c \'Stop-Process -Name WindowsTerminal\')")"',
    "git -c alias.x='!powershell -c \"Stop-Process -Name WindowsTerminal\"' x",
    "sed '1e powershell -c \"Stop-Process -Name WindowsTerminal\"' input.txt",
    "Get-Process WindowsTerminal | Stop-Process",
    "cat $(powershell -c (Get-Process WindowsTerminal).CloseMainWindow())",
    "grep -n WM_CLOSE x.py; " + WINDOW_CLOSERS["Stop-Process -Name WindowsTerminal"],
    "HARNESS_ALLOW_WINDOW_CLOSE=x taskkill /IM WindowsTerminal.exe /F",
]
WINDOW_ALLOWED = [
    "powershell -Command \"Get-Process WindowsTerminal | Select-Object Id,MainWindowTitle\"",
    "taskkill /PID 9001 /T /F", "python3 .claude/tests/test_kill_guard.py", "git log --oneline -5",
    'grep -E "CloseMainWindow|WM_CLOSE" .claude/hooks/kill_guard.py',
    "grep -n 'foo; WM_CLOSE' .claude/hooks/kill_guard.py",
    "grep -n '$(powershell -c CloseMainWindow)' x.py",
    "grep -n '`powershell -c CloseMainWindow`' x.py",
    'grep -n "foo; WM_CLOSE" x.py | head -5',
    'grep -n "CloseMainWindow\\\"|WM_CLOSE" x.py',
    "grep -n 'CloseMainWindow' .claude/hooks/kill_guard.py",
]


@pytest.mark.parametrize("command", WINDOW_DENIED)
def test_window_close_denied(command):
    got = reason(command)
    assert got is not None and "Ask the owner" in got and "HARNESS_ALLOW_WINDOW_CLOSE" not in got.split("\n")[-1]


@pytest.mark.parametrize("command", WINDOW_ALLOWED)
def test_window_close_allowed(command):
    assert bash(command).kind == "allow"


def test_rm_is_not_a_reader_stage():
    assert "rm" not in kg.READ_ONLY_VERBS


@pytest.fixture
def scratch(tmp_path):
    folder = tmp_path / "proj" / ".claude" / "scratch"
    folder.mkdir(parents=True)
    return folder


def test_read_only_mentions_of_a_closer_script_pass_and_running_it_denies(scratch):
    root = scratch.parents[1]
    (scratch / "closer.py").write_text("u.SendMessageTimeoutW(h, 0x112, 0xF060, 0, 2, 2000, None)\n", encoding="utf-8")
    for command in ("grep -n 'WM_CLOSE' .claude/hooks/kill_guard.py",
                    'grep -n "CloseMainWindow" .claude/hooks/kill_guard.py | head -5',
                    "rg -n 'taskkill /IM WindowsTerminal' .claude", "cat .claude/scratch/closer.py",
                    "git show HEAD:.claude/scratch/closer.py",
                    "Select-String -Pattern 'Stop-Process -Name WindowsTerminal' -Path .claude/hooks/kill_guard.py"):
        assert bash(command, root).kind == "allow", command
    assert bash("python3 .claude/scratch/closer.py", root).denied


def test_every_executed_scratch_script_in_a_pipeline_is_inspected(scratch):
    root = scratch.parents[1]
    (scratch / "input.py").write_text("print('input')\n", encoding="utf-8")
    (scratch / "closer.py").write_text("u.PostMessageW(h, 0x10, 0, 0)\n", encoding="utf-8")
    for command in ("cat .claude/scratch/input.py | python .claude/scratch/closer.py",
                    "python .claude/scratch/input.py | python .claude/scratch/closer.py",
                    'cat "$(python .claude/scratch/closer.py)"'):
        assert bash(command, root).denied, command


def test_a_reader_naming_a_closer_beside_an_executing_stage_only_reads(scratch):
    (scratch / "closer.py").write_text("CloseMainWindow()\n", encoding="utf-8")
    assert bash("cat .claude/scratch/closer.py | python safe.py", scratch.parents[1]).kind == "allow"


def test_a_scratch_closer_script_denies_and_a_non_scratch_one_is_not_scanned(scratch):
    root = scratch.parents[1]
    tests = root / ".claude" / "tests"
    tests.mkdir()
    for folder in (scratch, tests):
        (folder / "closer.ps1").write_text("SendMessageTimeout(h, 0x112, 0xF060)\n", encoding="utf-8")
    assert bash("powershell -File .claude/scratch/closer.ps1", root).denied
    assert bash("powershell -File .claude/tests/closer.ps1", root).kind == "allow"


# --- TaskStop ------------------------------------------------------------------------------------------------------
def _text_row(message):
    return {"type": "user", "message": {"role": "user", "content": message}}


def _answer_row(question, label):
    return {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_question",
        "content": 'Your questions have been answered: "%s"="%s".' % (question, label)}]},
        "toolUseResult": {"questions": [{"question": question, "header": "Stop tasks", "multiSelect": False,
                                         "options": [{"label": label, "description": ""}]}],
                          "answers": {question: label}}}


def _bash_row(stdout):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_bash", "content": stdout}]},
        "toolUseResult": {"stdout": stdout, "stderr": "", "interrupted": False}}


def _launch_rows(task_id, command, *, name="Bash", background=True, sidechain=False, owner="ok"):
    use_id = "toolu_launch_" + task_id
    tool_input = {"command": command, "description": "probe"}
    if background:
        tool_input["run_in_background"] = True
    result = {"stdout": "", "stderr": "", "interrupted": False}
    if name == "Monitor":
        tool_input = {"command": command, "description": "probe", "timeout_ms": 300000}
        result = {"taskId": task_id, "timeoutMs": 300000, "persistent": False}
    elif background:
        result["backgroundTaskId"] = task_id
    return [_text_row(owner),
            {"type": "assistant", "isSidechain": sidechain, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": use_id, "name": name, "input": tool_input}]}},
            {"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": use_id,
                 "content": "Command running in background with ID: %s." % task_id}]},
             "toolUseResult": result}]


def _ended_row(task_id, status="completed"):
    return {"type": "queue-operation", "operation": "enqueue",
            "content": "<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n"
                       "<summary>Agent finished</summary>\n</task-notification>" % (task_id, status)}


def stop(tmp_path, rows, task_id):
    path = tmp_path / ("t_%s.jsonl" % task_id)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return taskstop(task_id, path)


RUNNING_LAUNCHES = {
    "a poll loop": ("while :; do grep -q DONE grid.log && exit 0; sleep 120; done", "Bash", "allow"),
    "a billed sidecar launch": ("bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt", "Bash", "ask"),
    "a benchmark grid": ("bash .claude/scripts/benchmark_campaign/run_grid.sh deepseek-v41-flash flash:1", "Bash",
                         "allow"),
    "a gate run": ("pwsh -File .claude/scripts/gate_chain.ps1", "Bash", "allow"),
    "a harness proof run": ("python3 .claude/scripts/harness_tests.py --close", "Bash", "allow"),
    "a Monitor poll loop": ("while true; do grep -E '^VERDICT=' gate.out && break; sleep 10; done", "Monitor", "allow"),
    "an Agent": ("sleep 60", "Agent", "ask"),
}


@pytest.mark.parametrize("label", list(RUNNING_LAUNCHES))
def test_taskstop_of_an_own_running_launch(tmp_path, label):
    command, tool, kind = RUNNING_LAUNCHES[label]
    task = "own%d" % len(label)
    assert stop(tmp_path, _launch_rows(task, command, name=tool), task).kind == kind


def test_taskstop_running_workflow_asks_and_ended_workflow_passes(tmp_path):
    rows = _launch_rows("wfrun01", "unused", name="Workflow")
    rows[1]["message"]["content"][0]["input"] = {"script": "export const meta = {}"}
    rows[2]["toolUseResult"] = {"taskId": "wfrun01"}
    assert stop(tmp_path, rows, "wfrun01").kind == "ask"
    assert stop(tmp_path, rows + [_ended_row("wfrun01")], "wfrun01").kind == "allow"


def test_taskstop_async_agent_asks_and_a_foreground_agent_id_is_no_launch(tmp_path):
    rows = _launch_rows("aasync01", "sleep 60", name="Agent")
    rows[2]["toolUseResult"] = {"agentId": "aasync01", "isAsync": True}
    assert stop(tmp_path, rows, "aasync01").kind == "ask"
    fore = _launch_rows("afore001", "sleep 60", name="Agent")
    fore[2]["toolUseResult"] = {"agentId": "afore001", "isAsync": False}
    got = stop(tmp_path, fore, "afore001")
    assert got.denied and "no launch of task" in got.reason


def test_taskstop_own_foreground_shell_timed_out_into_the_background_passes(tmp_path):
    rows = _launch_rows("bgtimeout1", "git clone -q https://example.invalid/repo.git repo", background=False)
    rows[2]["toolUseResult"].update(backgroundTaskId="bgtimeout1", timedOutAfterMs=300000)
    assert stop(tmp_path, rows, "bgtimeout1").kind == "allow"


def test_taskstop_keep_words_grant_no_deny(tmp_path):
    rows = _launch_rows("bgkeep1", "sleep 600", owner="No, don't stop it. Let it finish.")
    assert stop(tmp_path, rows, "bgkeep1").kind == "allow"


@pytest.mark.parametrize("rows,task", [
    ([_text_row("Stop agent-123 now.")], "agent-123"),
    ([_text_row("Stop it.")], "task-b"),
    ([_answer_row("Can I TaskStop bhunx053s?", "Yes, stop it (Recommended)")], "bhunx053s"),
    ([_text_row("ok"), _bash_row("will stop bhunx053s shortly")], "bhunx053s"),
])
def test_taskstop_stop_words_grant_nothing(tmp_path, rows, task):
    got = stop(tmp_path, rows, task)
    assert got.denied and "no launch of task" in got.reason


def _padding(megabytes):
    return [_text_row("filler " * 20000)] * (megabytes * 7)


def test_taskstop_launch_and_end_beyond_a_tail_window(tmp_path):
    assert stop(tmp_path, _launch_rows("bgearly1", "sleep 600") + _padding(10), "bgearly1").kind == "allow"
    rows = _launch_rows("bgearly2", "sleep 60") + [_ended_row("bgearly2")] + _padding(10)
    assert stop(tmp_path, rows, "bgearly2").kind == "allow"


@pytest.mark.parametrize("rows,task", [
    (_launch_rows("bgside3", "sleep 600", sidechain=True), "bgside3"),
    (_launch_rows("bgpoll9", "sleep 600"), "bgpoll8"),
    ([_ended_row("aother99")], "aended02"),
    ([_bash_row("grep says <task-id>aended03</task-id> <status>completed</status>")], "aended03"),
])
def test_taskstop_not_this_sessions_launch_denies(tmp_path, rows, task):
    got = stop(tmp_path, rows, task)
    assert got.denied and "no launch of task" in got.reason


def test_taskstop_with_no_task_id_denies():
    got = decide({"tool_name": "TaskStop", "tool_input": {}})
    assert got.denied and "no launch of task" in got.reason


def test_taskstop_with_an_unreadable_transcript_denies(tmp_path):
    got = taskstop("agent-123", tmp_path / "no-such-transcript.jsonl")
    assert got.denied and "no launch of task" in got.reason


def test_taskstop_deny_names_the_task_and_one_next_step(tmp_path):
    got = stop(tmp_path, [], "peer-task-77")
    assert "peer-task-77" in got.reason and "Shell Discipline" in got.reason
    assert got.reason.count("Stop it from") == 1 and "owner" not in got.reason.lower()


def test_taskstop_ended_tasks_pass(tmp_path):
    assert stop(tmp_path, [_ended_row("aended01")], "aended01").kind == "allow"
    rows = _launch_rows("bgended1", "bash .claude/scripts/deepseek_sidecar.sh -m flash -f brief.txt")
    assert stop(tmp_path, rows + [_ended_row("bgended1", "failed")], "bgended1").kind == "allow"


def test_a_non_object_payload_fails_closed():
    import _floor
    from case import read
    got = read(_floor.decide([]))
    assert got.denied and "could not read its input" in got.reason and "Re-issue the same call" in got.reason


def test_taskstop_goes_through_the_floor_entry():
    import _floor
    assert "TaskStop" in _floor.MATCHER.split("|")


def test_the_owner_word_machinery_is_gone():
    gone = ("STOP_LABEL_RE", "KEEP_LABEL_RE", "_answer_verdict", "TASKSTOP_DIRECTIVE_RE", "_engages_task",
            "taskstop_authorized", "_owner_inputs", "_consent_text", "_answer_pairs", "quiet_shell_blocker",
            "own_quiet_shell")
    assert not [name for name in gone if hasattr(kg, name)]
