#!/usr/bin/env python3
"""Proof for hooks/login_parity_check.py: SessionStart repairs login drift in the background.

The sync's `--check` is a planted script that prints scripted drift and exits with a scripted code, the
spawner is planted (it records argv and log path), and the logs dir is a temp dir, so no case reaches a
real login, the network or a real `claude`. One case spawns a real detached child running a planted
one-liner, because detaching is the mechanism under proof there. A case whose hook raises, or whose
subprocess run exits outside {0}, is a CRASH, never a pass.

    python -m pytest .claude/tests/test_login_parity_check.py -q
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "login_parity_check.py"

DRIFT = ("anthropic-secondary: semantic-search@dreb 2.28.0 -> 2.69.0 (update)\n"
         "anthropic: semantic-search@dreb idle shim needs repair: idle unload is not installed\n")


@pytest.fixture(autouse=True)
def clean_session_env(monkeypatch, seat_logins):
    """The proof may itself run inside a sidecar child or a cloud VM; every case sets its own session env. The
    shipped seat's login is planted (`seat_logins`), since the hook checks nothing on a machine with no external login."""
    monkeypatch.delenv("CLAUDE_CODE_SIDECAR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)


def load():
    spec = importlib.util.spec_from_file_location("login_parity_check_under_test", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plant(root, out, code):
    """A stand-in for `sync_plan_config.py --check`: prints `out`, exits `code`, and leaves a mark it ran."""
    script = root / ("check_%d.py" % code)
    script.write_text("import sys, pathlib\npathlib.Path(%r).write_text('ran')\nsys.stdout.write(%r)\nsys.exit(%d)\n"
                      % (str(root / "check_ran"), out, code), encoding="utf-8")
    return [sys.executable, str(script)]


def run_hook(root, check_argv, monkeypatch, env=None):
    """(stdout, spawns, crash) for one in-process SessionStart run."""
    module = load()
    module.CHECK_ARGV = check_argv
    module.LOGS = root / "logs"
    spawns = []
    module.spawn = lambda argv, log: spawns.append((list(argv), Path(log)))
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"hook_event_name": "SessionStart", "source": "startup"})))
    out = io.StringIO()
    crash = None
    try:
        with redirect_stdout(out):
            module.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            crash = "exit %r" % exc.code
    except Exception as exc:  # a raising hook is a CRASH, reported by the case
        crash = "%s: %s" % (type(exc).__name__, exc)
    return out.getvalue(), spawns, crash


def decide_or_crash(module):
    """decide()'s line, or "CRASH <error>" when it raises."""
    try:
        return module.decide()
    except Exception as exc:  # a raising hook is a CRASH, reported by the case
        return "CRASH %s: %s" % (type(exc).__name__, exc)


def test_drift_the_hook_does_not_crash(tmp_path, monkeypatch):
    _, _, crash = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch)
    assert crash is None, crash


def test_drift_injects_one_line_naming_the_update_the_log_path_and_the_restart(tmp_path, monkeypatch):
    out, spawns, _ = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert (len(lines) == 1 and "semantic-search@dreb 2.28.0 -> 2.69.0" in lines[0] and "idle shim" in lines[0]
            and spawns and str(spawns[0][1]) in lines[0]
            and "restart sessions on this login after it ends" in lines[0]), repr(out)


def test_drift_starts_the_sync_once_detached_without_check_logging_under_the_logs_dir(tmp_path, monkeypatch):
    _, spawns, _ = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch)
    assert (len(spawns) == 1 and spawns[0][0][-1].endswith("sync_plan_config.py") and "--check" not in spawns[0][0]
            and spawns[0][1].parent == tmp_path / "logs"), repr(spawns)


def test_a_second_session_while_that_sync_is_recent_starts_none_and_names_the_first_log(tmp_path, monkeypatch):
    _, spawns, _ = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch)
    out2, spawns2, crash = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch)
    assert crash is None and not spawns2 and spawns and str(spawns[0][1]) in out2, repr((out2, spawns2, crash))


def test_two_sessions_that_both_find_a_stale_marker_start_one_sync(tmp_path, monkeypatch):
    module = load()
    module.CHECK_ARGV = plant(tmp_path, DRIFT, 1)
    module.LOGS = tmp_path / "logs"
    (tmp_path / "logs").mkdir()
    claimed = []
    module.spawn = lambda argv, log: claimed.append(Path(log))
    (tmp_path / "logs" / module.MARKER).write_text(json.dumps({"at": 0, "log": "stale.log"}), encoding="utf-8")
    lines = []
    racers = [threading.Thread(target=lambda: lines.append(module.decide())) for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=120)
    assert len(lines) == 2 and len(claimed) == 1 and sum(str(claimed[0]) in (ln or "") for ln in lines) == 2, \
        repr((lines, claimed))


def test_no_drift_injects_nothing_and_starts_nothing(tmp_path, monkeypatch):
    out, spawns, crash = run_hook(tmp_path, plant(tmp_path, "", 0), monkeypatch)
    assert crash is None and out.strip() == "" and not spawns, repr((out, spawns, crash))


def test_a_failed_check_injects_one_line_naming_the_exit_and_the_command_and_starts_nothing(tmp_path, monkeypatch):
    out, spawns, crash = run_hook(tmp_path, plant(tmp_path, "", 2), monkeypatch)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert (crash is None and not spawns and len(lines) == 1 and "exit 2" in lines[0]
            and "sync_plan_config.py --check" in lines[0]), repr((out, spawns, crash))


def test_a_sidecar_child_runs_no_check_and_starts_nothing(tmp_path, monkeypatch):
    out, spawns, crash = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch, env={"CLAUDE_CODE_SIDECAR": "1"})
    assert crash is None and out.strip() == "" and not spawns and not (tmp_path / "check_ran").exists(), \
        repr((out, spawns, crash))


def test_a_cloud_vm_runs_no_check_and_starts_nothing(tmp_path, monkeypatch):
    """Its logins are the account's, not this machine's."""
    out, spawns, crash = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch, env={"CLAUDE_CODE_REMOTE": "true"})
    assert crash is None and out.strip() == "" and not spawns and not (tmp_path / "check_ran").exists(), \
        repr((out, spawns, crash))


def test_a_cloud_vm_with_claude_code_remote_one_runs_no_check(tmp_path, monkeypatch):
    """The same cloud test as the other hooks."""
    out, spawns, crash = run_hook(tmp_path, plant(tmp_path, DRIFT, 1), monkeypatch, env={"CLAUDE_CODE_REMOTE": "1"})
    assert crash is None and out.strip() == "" and not spawns and not (tmp_path / "check_ran").exists(), \
        repr((out, spawns, crash))


def test_a_failed_spawn_names_the_failure_and_releases_its_claim_so_the_next_session_starts_the_sync(tmp_path, monkeypatch):
    module = load()
    module.CHECK_ARGV = plant(tmp_path, DRIFT, 1)
    module.LOGS = tmp_path / "logs"

    def refuse(argv, log):
        raise OSError(2, "planted spawn failure")
    module.spawn = refuse
    failed_line = decide_or_crash(module)
    retried = []
    module.spawn = lambda argv, log: retried.append(Path(log))
    retry_line = module.decide()
    assert ("could not start" in (failed_line or "") and "planted spawn failure" in failed_line
            and "sync_plan_config.py" in failed_line and len(retried) == 1 and str(retried[0]) in (retry_line or "")), \
        repr((failed_line, retry_line, retried))


def test_a_failed_spawn_never_releases_a_newer_sessions_claim(tmp_path):
    module = load()
    module.CHECK_ARGV = plant(tmp_path, DRIFT, 1)
    module.LOGS = tmp_path / "logs"

    def superseded(argv, log):
        (tmp_path / "logs" / module.MARKER).write_text(json.dumps({"at": time.time(), "log": "successor.log"}),
                                                       encoding="utf-8")
        raise OSError(2, "planted spawn failure")
    module.spawn = superseded
    decide_or_crash(module)
    held = json.loads((tmp_path / "logs" / module.MARKER).read_text(encoding="utf-8"))
    assert held.get("log") == "successor.log", repr(held)


def test_a_hung_check_ends_at_its_deadline_with_the_processes_it_started_and_no_grandchild_left(tmp_path):
    """Spawned: the check's own process tree is the subject. The grandchild would write its mark 6 s in."""
    module = load()
    marker = tmp_path / "grandchild_outlived"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text("import pathlib, time\ntime.sleep(6)\npathlib.Path(%s).write_text('x')\n"
                          % json.dumps(str(marker)), encoding="utf-8", newline="\n")
    hung = tmp_path / "hung_check.py"
    hung.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, %s])\ntime.sleep(30)\n"
                    % json.dumps(str(grandchild)), encoding="utf-8", newline="\n")
    module.CHECK_ARGV = [sys.executable, str(hung)]
    module.CHECK_TIMEOUT = 1
    module.LOGS = tmp_path / "logs"
    spawned = []
    module.spawn = lambda argv, log: spawned.append(list(argv))
    start = time.time()
    line = module.decide()
    elapsed = time.time() - start
    time.sleep(max(0.0, 2.8 - (time.time() - start)))   # past the moment a surviving grandchild would write
    assert ("ran past 1 s" in (line or "") and elapsed < 2.5 and not marker.exists() and not spawned), \
        repr((line, round(elapsed, 1), marker.exists(), spawned))


def test_a_seats_json_with_no_external_login_runs_no_check_and_injects_no_line(tmp_path):
    module = load()
    module.CHECK_ARGV = plant(tmp_path, DRIFT, 1)
    module.LOGS = tmp_path / "logs"
    module.spawn = lambda argv, log: None
    module._seat_rows = lambda: {"only-local": {"adapter": "scripts/seat_agy.sh", "writes": "no",
                                                "max_concurrent": 1, "configDir": "", "model": "m"}}
    line = decide_or_crash(module)
    assert line is None and not (tmp_path / "check_ran").exists(), repr((line, (tmp_path / "check_ran").exists()))


def test_the_hook_imports_and_exits_zero_as_session_start_runs_it():
    """Spawned: the hook's own entry point is the subject. A sidecar child, so nothing is checked."""
    proc = subprocess.run([sys.executable, str(HOOK)], input="{}", capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CODE_SIDECAR": "1"}, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "", repr((proc.returncode, proc.stdout, proc.stderr))


def test_the_real_spawner_starts_a_child_that_writes_its_output_to_the_log(tmp_path):
    """Spawned: detaching is the mechanism under proof, so a real child runs."""
    module = load()
    log = tmp_path / "spawned.log"
    module.spawn([sys.executable, "-c", "print('detached child ran')"], log)
    deadline = time.time() + 20
    while time.time() < deadline and "detached child ran" not in (log.read_text(encoding="utf-8") if log.exists() else ""):
        time.sleep(0.2)
    assert log.exists() and "detached child ran" in log.read_text(encoding="utf-8"), str(log)
