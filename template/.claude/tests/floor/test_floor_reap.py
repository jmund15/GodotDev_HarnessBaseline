"""`tools/reap.py`, the stop tool `kill_guard` admits (T1): v1's `test_reap.py` and `test_reap_native_env.py` cases
whose subject v2 keeps, as rows. Where each v1 case went, and each dropped case's plan line: `test_floor_ported.md`.

Decision rows run `reap.main` in process on a planted process table, environment blocks, session registry and kill.
Two Windows rows read live processes, because the process is the subject: the Win32 snapshot holds this process,
and a real stop of a child this test starts. One spawned case runs the entry point.
"""
import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

import pytest

import reap
from conftest import TOOLS

SID = "9ecb32b6-e8a8-45d1-9f2e-8331d8d0c9f3"
PEER = "11111111-2222-3333-4444-555555555555"
OTHER = "22222222-3333-4444-5555-666666666666"
DEAD = "deaddead-0000-0000-0000-000000000000"
SESSION_PID = 43144
PROJECT = "C--x-PushinPotions"
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="reads the live Windows process table")


def row(pid, ppid, name, cmd, start="2026-09-15 14:00:00"):
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "start": start}


BASE = [
    row(SESSION_PID, 1, "claude.exe", r"C:\Users\x\claude.exe --session-id " + SID),
    row(100, SESSION_PID, "bash.exe", "bash -c \"bash scoring/worker.py opus-gaps\""),
    row(101, 100, "bash.exe", "bash scoring/worker.py opus-gaps"),
    row(102, 101, "bash.exe", "bash job.sh -m haiku"),
    row(103, 102, "claude.exe", r"C:\Users\x\claude.exe -p \"Call Workflow(score_cell.js)\""),
    row(200, SESSION_PID, "bash.exe", "bash scoring/worker.py opus-gaps"),
    # a peer session and its child
    row(900, 1, "claude.exe", r"C:\Users\x\claude.exe --session-id " + PEER),
    row(901, 900, "bash.exe", "bash scoring/worker.py opus-gaps"),
    # shared infra
    row(950, SESSION_PID, "node.exe", "node semantic-search-mcp.js"),
]
EDITOR = row(53844, 7777, "Godot_v4.7.1-stable_mono_win64.exe",
             "Godot_v4.7.1-stable_mono_win64.exe --path C:/x/.claude/worktrees/sync-pr113 --editor")
RUNNER = row(680, 7290, "Godot_v4.7.1-stable_mono_win64.exe",
             r'"C:\x\Godot_v4.7.1-stable_mono_win64.exe" --path . -s res://GdUnit4TestRunnerScene.cs')
LIVE_HOST = row(910, 1, "claude.exe", r'"C:\Users\x\.local\bin\claude.exe"')
HOST_MCP = row(911, 910, "node.exe", "node C:/x/server.js")
PS_HEADER = "      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n"


def table(*extra):
    return {r["pid"]: r for r in BASE + list(extra)}


def block(session=None):
    return b"PATH=/usr/bin\0" + (b"CLAUDE_CODE_SESSION_ID=" + session.encode() + b"\0" if session else b"")


@pytest.fixture
def run(tmp_path, monkeypatch):
    """run(argv, tbl, ...) -> SimpleNamespace(rc, killed, out, err, ledger): `reap.main` on planted facts.

    `blocks` maps pid to its environment block (default: readable, naming no session); `sessions` maps a live pid
    to the session its registry row names; `transcripts` maps a project key to the session ids it holds."""
    ledger = tmp_path / "logs" / "reap_ledger.jsonl"

    def go(argv, tbl, blocks=None, sessions=None, kill_ok=True, ps=None, transcripts=None, ledger_path=None):
        killed = []
        registry = tmp_path / ("sessions_%d" % len(list(tmp_path.glob("sessions_*"))))
        registry.mkdir()
        for pid, session in (sessions or {}).items():
            (registry / ("%d.json" % pid)).write_text(json.dumps({"pid": pid, "sessionId": session}), encoding="utf-8")
        projects = tmp_path / ("projects_%d" % len(list(tmp_path.glob("projects_*"))))
        for key, sids in ({PROJECT: [SID]} if transcripts is None else transcripts).items():
            (projects / key).mkdir(parents=True)
            for sid in sids:
                (projects / key / (sid + ".jsonl")).write_text("{}\n", encoding="utf-8")
        read = (lambda pid: blocks.get(pid, b"PATH=planted\0")) if blocks is not None else (lambda pid: b"PATH=planted\0")
        ok = kill_ok if callable(kill_ok) else (lambda pid: kill_ok)
        monkeypatch.setattr(reap, "session_registry_dirs", lambda: [registry])
        monkeypatch.setattr(reap, "process_table", lambda timeout=60: {k: dict(v) for k, v in tbl.items()})
        monkeypatch.setattr(reap, "ps_text", lambda: ps)
        monkeypatch.setattr(reap, "environ_of", lambda pid, text=None: read(pid))
        monkeypatch.setattr(reap, "native_environ", read)
        monkeypatch.setattr(reap, "taskkill", lambda pid, tree=True: (killed.append(pid) or True) if ok(pid) else False)
        monkeypatch.setattr(reap, "projects_root", lambda: projects)
        monkeypatch.setattr(reap, "LEDGER", ledger_path or ledger)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = reap.main(argv, env={"CLAUDE_PID": str(SESSION_PID), "CLAUDE_CODE_SESSION_ID": SID})
            except SystemExit as exc:
                rc = exc.code
        try:
            rows = [json.loads(x) for x in (ledger_path or ledger).read_text(encoding="utf-8").splitlines()]
        except OSError:
            rows = []
        return SimpleNamespace(rc=rc, killed=killed, out=out.getvalue(), err=err.getvalue(), ledger=rows)
    return go


# --- what is stopped -------------------------------------------------------------------------------------------
STOPPED = [
    ("this session's own process tree, stopped at its root", ["101", "--why", "launched twice"], table(), {}, 101),
    ("this session's own headless claude -p child, with no --why", ["103"], table(), {}, 103),
    ("an empty --why", ["103", "--why", ""], table(), {}, 103),
    ("a hung run whose own environment names this session (its wrapper is gone)", ["7865"],
     table(row(7865, 7744, "bash.exe", "bash snap.sh -m luna")), {7865: block(SID)}, 7865),
    ("a process whose parent is gone and whose environment names no session", ["7865"],
     table(row(7865, 7744, "bash.exe", "bash snap.sh -m luna")), {}, 7865),
    ("an env wrapper naming ENABLE_LSP_TOOL is no MCP server", ["960"],
     table(row(960, SESSION_PID, "env.exe", "env.exe -u CLAUDE_CODE_CHILD_SESSION -u ENABLE_LSP_TOOL claude.exe -p \"judge\"")),
     {}, 960),
    ("a codex.exe this session launched", ["972"],
     table(row(972, SESSION_PID, "codex.exe", r"C:\Users\x\codex.exe exec judge-brief")), {}, 972),
    ("a process no session owns (the owner's editor), with no --why", ["53844"], table(EDITOR), {}, 53844),
    ("a plain 'Yes' as --why", ["150", "--why", "Yes"],
     table(row(150, 7777, "ChatGPT.exe", r"C:\Program Files\ChatGPT.exe")), {}, 150),
] + [("%s named by a short word in a --why with a 'don't need' clause" % exe,
      ["150", "--why", "close rider, whisper, code, firefox, gpt and chrome (that you don't need)"],
      table(row(150, 7777, exe, r"C:\Program Files\%s --type=gpu" % exe)), {}, 150)
     for exe in ("rider64.exe", "ChatGPT.exe", "Code.exe", "firefox.exe", "chrome.exe")]


@pytest.mark.parametrize("label,argv,tbl,blocks,pid", STOPPED, ids=[r[0] for r in STOPPED])
def test_stopped(run, label, argv, tbl, blocks, pid):
    got = run(argv, tbl, blocks=blocks)
    assert (got.rc, got.killed) == (0, [pid]), got.out
    assert got.ledger[-1]["result"] == "killed" and got.ledger[-1]["proof"]


def test_the_ledger_row_carries_pid_session_why_proof_and_tree(run):
    got = run(["101", "--why", "launched twice; 200 continues"], table())
    rec = got.ledger[-1]
    assert (rec["pid"], rec["session"], rec["why"], rec["tree"]) == (101, SID, "launched twice; 200 continues", [101, 102, 103])
    assert "descends" in rec["proof"]


def test_an_unclaimed_stop_records_its_proof(run):
    assert "unclaimed" in run(["53844"], table(EDITOR)).ledger[-1]["proof"]


@pytest.mark.parametrize("why", ["x", "  the owner said: close it, (that you don't need)  "])
def test_any_why_is_recorded_verbatim(run, why):
    got = run(["53844", "--why", why], table(EDITOR))
    assert got.rc == 0 and got.ledger[-1]["why"] == why


def test_a_missing_session_transcript_does_not_refuse(run):
    assert run(["53844", "--why", "x"], table(EDITOR), transcripts={}).killed == [53844]


# --- what is refused -------------------------------------------------------------------------------------------
REFUSED = [
    ("a peer session's process with an identical command line", ["901", "--why", "x"], table(), {}, None),
    ("a process whose own environment names a live peer session", ["901", "--why", "x"], table(), {901: block(PEER)}, None),
    ("a hung run whose own environment names a live peer session", ["7865"],
     table(row(7865, 7744, "bash.exe", "bash snap.sh -m luna")), {7865: block(PEER)}, None),
    ("this session's own host", [str(SESSION_PID)], table(), {}, None),
    ("a session host inside this session's ancestry", ["940"],
     table(row(940, SESSION_PID, "claude.exe", r"C:\Users\x\claude.exe --session-id " + PEER)), {}, None),
    ("shared infra (an MCP server) in this session's tree", ["950", "--why", "x"], table(), {}, None),
    ("shared infra no session owns", ["53845", "--why", "x"],
     table(row(53845, 7777, "node.exe", "node semantic-search-mcp.js --port 2")), {}, None),
    ("an MCP server launched with flags before its script", ["970", "--why", "owner said"],
     table(row(970, SESSION_PID, "node.exe", "node --inspect=9229 C:/x/semantic-search/server.js")), {}, None),
    ("a codex.exe that ChatGPT.exe runs", ["971"],
     table(row(970, 1, "ChatGPT.exe", r"C:\Users\x\ChatGPT.exe"), row(971, 970, "codex.exe", r"C:\Users\x\codex.exe app-server")),
     {}, None),
    ("a target whose environment cannot be read", ["53844"], table(EDITOR), {53844: b""}, None),
    ("a pid that is not in the process table", ["99999"], table(), {}, None),
    ("the retired --reason owner", ["53844", "--reason", "owner", "--why", "x"], table(EDITOR), {}, None),
    ("the retired --reason orphan on a dead session's leftover", ["680", "--reason", "orphan"],
     table(RUNNER), {680: block(DEAD)}, None),
    ("the retired --reason duplicate with a live twin", ["101", "--reason", "duplicate"], table(), {}, None),
] + [("the retired `%s` verb" % verb, [verb, "101"], table(), {}, None) for verb in sorted(set(reap.RETIRED) - {"--reason"})]


@pytest.mark.parametrize("label,argv,tbl,blocks,_", REFUSED, ids=[r[0] for r in REFUSED])
def test_refused(run, label, argv, tbl, blocks, _):
    got = run(argv, tbl, blocks=blocks)
    assert got.rc == 2 and got.killed == [] and "REFUSED" in got.out, got.out


def test_a_refusal_names_the_peer_session_it_belongs_to(run):
    assert PEER[:8] in run(["901"], table()).out


def test_a_retired_flag_names_its_replacement(run):
    assert "omit --reason" in run(["53844", "--reason", "orphan"], table(EDITOR)).out


def test_a_refusal_on_an_observed_fact_prints_no_usage_shape(run):
    got = run(["999999", "--why", "x"], table())
    assert got.rc == 2 and "accepts:" not in got.err and "example:" not in got.err


def test_a_malformed_call_states_the_accepted_shape(run):
    got = run(["--no-such-flag"], table())
    assert got.rc == 2 and "accepts:" in got.err and "example:" in got.err


# --- a dead session's leftover ---------------------------------------------------------------------------------
DEAD_ROWS = [
    ("a dead session's orphaned child is stopped, with no flag", ["680"], [RUNNER], {680: block(DEAD)}, None, 0),
    ("a console host carrying the dead id is no host", ["680"],
     [RUNNER, row(682, 7290, "conhost.exe", r"\??\C:\WINDOWS\system32\conhost.exe 0x4")],
     {680: block(DEAD), 682: block(DEAD)}, None, 0),
    ("a dead session's child is stopped while its parent lives", ["680"],
     [RUNNER, row(7290, 1, "testhost.exe", "testhost.exe --port 1")], {680: block(DEAD)}, None, 0),
    ("a live host whose registry row names another session leaves the dead one dead", ["680"],
     [RUNNER, LIVE_HOST, HOST_MCP], {680: block(DEAD), 911: block(OTHER)}, {910: "33333333-0000-0000-0000-000000000000"}, 0),
    ("a session a live host's registry row names is live: refused", ["680"],
     [RUNNER, LIVE_HOST, HOST_MCP], {680: block(DEAD), 911: block(OTHER)}, {910: DEAD}, 2),
    ("a session a live host names after --session-id is live: refused", ["662"],
     [row(662, 7207, "claude.exe", r"C:\Users\x\.local\bin\claude.exe -p --model model-a")], {662: block(PEER)}, None, 2),
    ("a live host with no row and no id, whose child carries a session, makes every session unknown: refused", ["680"],
     [RUNNER, LIVE_HOST, HOST_MCP], {680: block(DEAD), 911: block(OTHER)}, None, 2),
]


@pytest.mark.parametrize("label,argv,extra,blocks,sessions,rc", DEAD_ROWS, ids=[r[0] for r in DEAD_ROWS])
def test_a_dead_sessions_leftover(run, label, argv, extra, blocks, sessions, rc):
    got = run(argv, table(*extra), blocks=blocks, sessions=sessions)
    assert got.rc == rc and got.killed == ([int(argv[0])] if rc == 0 else []), got.out


@pytest.mark.parametrize("sessions,token", [({910: DEAD}, "pid 910"), (None, "pid 910")], ids=["live", "unknown"])
def test_a_dead_session_refusal_names_the_host_that_withheld_the_proof(run, sessions, token):
    got = run(["680"], table(RUNNER, LIVE_HOST, HOST_MCP), blocks={680: block(DEAD), 911: block(OTHER)}, sessions=sessions)
    assert token in got.out


PANE = ('"C:\\Program Files\\Git\\bin\\bash.exe" -c "source /c/x/.claude/shell-snapshots/snap-b.sh 2>/dev/null '
        "|| true && eval 'python3 .claude/tools/watch_pane.py --pane' < /dev/null\"")
LIVE = {SESSION_PID: SID, 910: PEER}


def test_a_dead_sessions_pane_watcher_under_this_sessions_live_wrapper_is_this_sessions(run):
    """The 2026-11-10 class: the machine's MCP servers also carry the dead id, and none reads as its live host."""
    extra = (row(501, SESSION_PID, "bash.exe", PANE), row(502, 501, "python3.exe", "python3 .claude/tools/watch_pane.py"),
             row(503, SESSION_PID, "python.exe", "python C:/x/semantic-search-mcp.py"), LIVE_HOST)
    got = run(["501", "--dry-run"], table(*extra), blocks={501: block(DEAD), 502: block(DEAD), 503: block(DEAD)},
              sessions=LIVE, transcripts={PROJECT: [SID, DEAD]})
    assert (got.rc, got.killed) == (0, []) and "DRY 501" in got.out, got.out


@pytest.mark.parametrize("owner,transcripts", [(OTHER, {PROJECT: [SID], "C--y-OtherProject": [OTHER]}),
                                               (PEER, {PROJECT: [SID, PEER]})],
                         ids=["another project's dead pane watcher", "a live session's pane watcher"])
def test_a_pane_watcher_of_another_project_or_a_live_session_is_refused(run, owner, transcripts):
    got = run(["521", "--dry-run"], table(row(521, 9999, "bash.exe", PANE), LIVE_HOST), blocks={521: block(owner)},
              sessions=LIVE, transcripts=transcripts)
    assert (got.rc, got.killed) == (2, []) and "DRY" not in got.out, got.out


# --- the tree --------------------------------------------------------------------------------------------------
def test_a_tree_holding_shared_infra_stops_the_rest_and_spares_the_infra_with_its_subtree(run):
    infra = row(104, 101, "node.exe", "node semantic-search-mcp.js --port 1")
    below = row(105, 104, "python3", "python3 semantic-search/worker.py")
    got = run(["101"], table(infra, below))
    assert got.rc == 0 and {101, 102, 103} <= set(got.killed) and not {104, 105} & set(got.killed), got.out


def test_a_repeated_pid_and_a_descendant_of_another_target_are_one_tree_stop(run):
    got = run(["101", "103", "101"], table())
    assert (got.rc, got.killed) == (0, [101]), got.out


def test_dry_run_stops_nothing_and_records_the_plan(run):
    got = run(["101", "--dry-run"], table())
    assert (got.rc, got.killed, got.ledger[-1]["result"]) == (0, [], "dry-run") and "103" in got.out


def test_an_unwritable_ledger_refuses_the_kill(run, tmp_path):
    blocked = tmp_path / "not-a-dir.txt"
    blocked.write_text("x", encoding="utf-8")
    got = run(["103"], table(), ledger_path=blocked / "reap_ledger.jsonl")
    assert (got.rc, got.killed) == (2, []), got.out


def test_the_killing_row_is_written_before_the_kill(run, tmp_path):
    ledger = tmp_path / "order.jsonl"
    seen = []

    def recorded_first(pid):
        rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()] if ledger.exists() else []
        seen.append(bool(rows) and rows[-1]["pid"] == pid and rows[-1]["result"] == "killing")
        return True
    got = run(["101"], table(), kill_ok=recorded_first, ledger_path=ledger)
    assert got.rc == 0 and seen == [True]


def test_a_kill_that_fails_exits_2_and_is_recorded(run):
    got = run(["103"], table(), kill_ok=False)
    assert got.rc == 2 and got.ledger[-1]["result"] == "kill-failed"


def test_a_git_bash_child_outside_the_windows_tree_dies_with_its_msys_parent(run):
    ps = PS_HEADER + ("     5101    5100    5100      101  ?         197608 13:59:21 /usr/bin/bash\n"
                      "     5150    5101    5100      150  ?         197608 13:59:22 /usr/bin/bash\n")
    grid = row(150, 7777, "bash.exe", "bash run_cells.sh camp spec")
    assert run(["101"], table(grid), ps=ps).killed == [101, 150]
    assert run(["101"], table(grid), ps=None).killed == [101]


# --- the readers -----------------------------------------------------------------------------------------------
def test_msys_and_windows_pids_map_through_ps():
    ps = PS_HEADER + "  1018021 1018020 1018009      101  ?         197608 13:59:21 /usr/bin/bash\n"
    assert reap.winpid_from_ps(ps, 1018021) == 101 and reap.winpid_from_ps(ps, 5) is None
    assert reap.msys_pid_for_winpid(101, ps) == 1018021 and reap.msys_pid_for_winpid(999, ps) is None
    assert reap.resolve_pid(1018021, {101: {}}, ps) == 101 and reap.resolve_pid(1018021, {101: {}}, None) is None


def test_the_environment_read_uses_gits_bash_never_a_bare_bash(tmp_path, monkeypatch):
    usr_bin = tmp_path / "Git" / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    (usr_bin / "ps.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(reap._msys, "tool", lambda name: str(usr_bin / (name + ".exe")))
    assert reap.git_bash() is None
    (usr_bin / "bash.exe").write_text("", encoding="utf-8")
    assert reap.git_bash() == str(usr_bin / "bash.exe")


RAW_CIM = ('[{"ProcessId":5,"ParentProcessId":1,"Name":"node.exe","CommandLine":"node --probe \x1b[0m",'
           '"Start":"2026-01-01 00:00:00"},{"ProcessId":6,"ParentProcessId":1,"Name":"git.exe",'
           '"CommandLine":"git status","Start":"2026-01-01 00:00:00"}]')


def test_a_raw_control_character_in_one_command_line_does_not_blank_the_table(monkeypatch):
    monkeypatch.setattr(reap.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=RAW_CIM, stderr=""))
    got = reap._cim_table()
    assert len(got) == 2 and got[5]["name"] == "node.exe"


def test_a_command_line_that_is_not_utf8_does_not_crash_or_blank_the_table(monkeypatch):
    def undecodable(*a, **k):  # a strict decode leaves stdout None in subprocess's reader thread
        ok = k.get("errors") in ("replace", "backslashreplace", "surrogateescape")
        return SimpleNamespace(returncode=0, stdout=RAW_CIM if ok else None, stderr="")
    monkeypatch.setattr(reap.subprocess, "run", undecodable)
    assert reap._cim_table()[6]["name"] == "git.exe"


def test_off_windows_the_table_reads_proc_and_spawns_no_cim(monkeypatch):
    spawned = []
    monkeypatch.setattr(reap.subprocess, "run", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(reap, "_on_windows", lambda: False)
    monkeypatch.setattr(reap, "_proc_table", lambda: {7: {"pid": 7}})
    assert reap.process_table() == {7: {"pid": 7}} and not spawned


def test_on_windows_an_empty_win32_snapshot_falls_back_to_cim(monkeypatch):
    monkeypatch.setattr(reap.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=RAW_CIM, stderr=""))
    monkeypatch.setattr(reap, "_on_windows", lambda: True)
    monkeypatch.setattr(reap, "_win32_table", lambda: {})
    assert len(reap.process_table()) == 2


@pytest.mark.parametrize("gone,want", [(True, True), (False, False)],
                         ids=["a nonzero taskkill whose target is gone is a kill", "one whose target still runs is not"])
def test_taskkill_success_is_the_target_gone(monkeypatch, gone, want):
    def planted(cmd, *a, **k):
        if cmd[0] == "taskkill":
            return subprocess.CompletedProcess(cmd, 128, "", 'ERROR: The process "77" not found.')
        listing = "INFO: No tasks are running which match the specified criteria." if gone else '"bash.exe","77","Console","1","10,892 K"'
        return subprocess.CompletedProcess(cmd, 0, listing, "")
    monkeypatch.setattr(reap.subprocess, "run", planted)
    assert reap.taskkill(77) is want


def test_transcripts_are_read_under_this_logins_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert reap.projects_root() == tmp_path / "projects"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert reap.projects_root() == reap.Path(os.path.expanduser("~/.claude")) / "projects"


def test_the_session_registry_is_read_in_each_declared_login_that_exists(tmp_path, monkeypatch):
    declared, undeclared = tmp_path / "declared", tmp_path / ".claude-stale"
    for folder in (declared, undeclared):
        (folder / "sessions").mkdir(parents=True)
    monkeypatch.setattr(reap, "login_dirs", lambda: [str(declared), str(tmp_path / "absent")])
    assert reap.session_registry_dirs() == [declared / "sessions"]


# --- the live machine (the process is the subject) -------------------------------------------------------------
@WINDOWS
def test_the_win32_snapshot_holds_this_process_and_spawns_no_cim(monkeypatch):
    spawned = []
    real = subprocess.run
    monkeypatch.setattr(reap.subprocess, "run", lambda *a, **k: spawned.append(a) or real(*a, **k))
    me = reap.process_table().get(os.getpid(), {})
    assert not spawned and me.get("ppid") == os.getppid() and me.get("start") and me.get("cpu") is not None
    assert os.path.basename(sys.executable).lower().split(".")[0] in me.get("cmd", "").lower()


@WINDOWS
def test_a_real_stop_of_a_child_this_session_started(tmp_path, monkeypatch):
    """The child's own environment names the session (no ancestry: CLAUDE_PID is unset), so the stop reads the
    live table, the child's native environment block and kills it through taskkill."""
    token = "reap-proof-%d" % os.getpid()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             env=dict(os.environ, CLAUDE_CODE_SESSION_ID=token))
    try:
        deadline = time.time() + 10
        while time.time() < deadline and reap.native_environ(child.pid) == b"":
            time.sleep(0.1)
        assert reap._env_value(reap.environ_of(child.pid), reap.SID_ENV) == token
        assert reap.native_environ(4) == b""  # the System process: access denied yields b'', never a partial block
        monkeypatch.setattr(reap, "LEDGER", tmp_path / "reap_ledger.jsonl")
        monkeypatch.setattr(reap, "session_registry_dirs", lambda: [])
        monkeypatch.setattr(reap, "projects_root", lambda: tmp_path / "projects")
        with redirect_stdout(io.StringIO()):
            rc = reap.main([str(child.pid)], env={"CLAUDE_CODE_SESSION_ID": token})
        assert rc == 0 and child.wait(timeout=15) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_the_entry_point_runs_and_refuses_a_retired_flag():
    done = subprocess.run([sys.executable, str(TOOLS / "reap.py"), "1", "--reason", "orphan"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "omit --reason" in done.stdout, (done.stdout, done.stderr)
