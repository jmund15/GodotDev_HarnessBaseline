#!/usr/bin/env python3
"""Proof for hooks/runaway_scan_reaper.py with real planted processes.

Every process this proof plants is pinned by a Windows handle at plant time. The reaper runs
with HARNESS_REAPER_ONLY_PIDS limited to those pids, so it can never select another session's
process, and cleanup terminates through the pinned handles, so a reused pid is never hit.

Cases: an orphaned Git grep blocked on a pipe is reaped at zero thresholds; a grep with a live
parent survives; an orphaned non-search process (Git sleep) survives; default thresholds spare a
small orphan; a second call inside the throttle window takes no snapshot; an unreadable process
table is silent. Hook registrations are each repo's own settings.json.

    python -m pytest .claude/tests/test_runaway_scan_reaper.py -q
"""
import ctypes
import io
import json
import os
import subprocess
import sys
import time

import pytest

import _platform_skip

if os.name != "nt":
    _platform_skip.skip_proof("runaway-scan reaper", "its planted-process table and pinned Windows handles need Win32")

from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
HOOKS = os.path.join(REPO, ".claude", "hooks")
REAPER = os.path.join(HOOKS, "runaway_scan_reaper.py")

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
SYNCHRONIZE, PROCESS_TERMINATE, QUERY_LIMITED = 0x00100000, 0x0001, 0x1000

ZERO = {"HARNESS_REAPER_MIN_PRIVATE_MB": "0", "HARNESS_REAPER_MIN_CPU_SEC": "0"}


def git_bash():
    """Git Bash, never WSL: CLAUDE_CODE_GIT_BASH_PATH, else PATH, rejected under System32."""
    sys.path.insert(0, os.path.join(HERE, "..", "tools"))
    import reap
    path = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or reap.git_bash()
    if not path or "system32" in path.lower() or "windowsapps" in path.lower():
        return None
    return path


def git_usr_bin(bash):
    d = os.path.dirname(os.path.abspath(bash))
    root = os.path.dirname(os.path.dirname(d)) if os.path.basename(d).lower() == "bin" and \
        os.path.basename(os.path.dirname(d)).lower() == "usr" else os.path.dirname(d)
    return os.path.join(root, "usr", "bin")


class Planted:
    """pid -> pinned handle, so liveness and cleanup never touch a reused pid."""

    def __init__(self):
        self.handles = {}

    def pin(self, pid):
        h = k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE | QUERY_LIMITED, False, pid)
        if not h:
            raise OSError("cannot pin planted pid %d" % pid)
        self.handles[pid] = h
        return pid

    def alive(self, pid):
        return k32.WaitForSingleObject(self.handles[pid], 0) == 0x102  # WAIT_TIMEOUT

    def wait_dead(self, pid, sec=5.0):
        k32.WaitForSingleObject(self.handles[pid], int(sec * 1000))
        return not self.alive(pid)

    def cleanup(self):
        for pid, h in self.handles.items():
            if k32.WaitForSingleObject(h, 0) == 0x102:
                k32.TerminateProcess(h, 1)
            k32.CloseHandle(h)
        self.handles.clear()


MIDDLE = r'''
import json, os, subprocess, sys
usr_bin, out = sys.argv[1], sys.argv[2]
r, w = os.pipe()
s = subprocess.Popen([os.path.join(usr_bin, "sleep.exe"), "600"], stdin=subprocess.DEVNULL,
                     stdout=w, stderr=subprocess.DEVNULL)
g = subprocess.Popen([os.path.join(usr_bin, "grep.exe"), "never-matches-planted"], stdin=r,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
with open(out, "w") as fh:
    json.dump({"middle": os.getpid(), "sleep": s.pid, "grep": g.pid}, fh)
'''


def plant_orphan(planted, usr_bin, tmp):
    """A Git grep blocked reading a pipe that a planted Git sleep holds open; their parent exits."""
    out = os.path.join(str(tmp), "plant_%d.json" % time.time_ns())
    m = subprocess.run([sys.executable, "-c", MIDDLE, usr_bin, out], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
    if m.returncode != 0:
        raise RuntimeError("plant failed: " + m.stderr.decode(errors="replace"))
    with open(out) as fh:
        pids = json.load(fh)
    planted.pin(pids["grep"])
    planted.pin(pids["sleep"])
    return pids


def reaper(env_extra, payload=None, cwd=REPO):
    """Spawned: the hook's stdin entry point is the subject. Returns (process, advisory text)."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8", **env_extra)
    payload = payload or {"session_id": "rsr00001", "hook_event_name": "PostToolUse",
                          "tool_name": "Read", "tool_input": {}}
    p = subprocess.run([sys.executable, REAPER], input=json.dumps(payload), capture_output=True,
                       text=True, encoding="utf-8", timeout=60, env=env, cwd=cwd)
    ctx = ""
    if p.stdout.strip():
        try:
            ctx = (json.loads(p.stdout).get("hookSpecificOutput") or {}).get("additionalContext", "")
        except ValueError:
            ctx = "UNPARSEABLE:" + p.stdout
    return p, ctx


@pytest.fixture(scope="module")
def usr_bin():
    bash = git_bash()
    assert bash, "Git Bash resolves outside System32/WSL"
    return git_usr_bin(bash)


@pytest.fixture
def planted():
    p = Planted()
    yield p
    p.cleanup()


@pytest.fixture(scope="module")
def scenario(tmp_path_factory, usr_bin):
    """One orphaned grep and sleep, a live grep under a live parent, and the reaper run once over them."""
    tmp = tmp_path_factory.mktemp("rsr")
    planted = Planted()
    live = subprocess.Popen([os.path.join(usr_bin, "grep.exe"), "never-matches-live"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    planted.pin(live.pid)
    orphan = plant_orphan(planted, usr_bin, tmp)
    time.sleep(0.5)
    before = planted.alive(orphan["grep"]) and planted.alive(orphan["sleep"])
    only = ",".join(str(x) for x in (orphan["grep"], orphan["sleep"], live.pid))
    p, ctx = reaper(dict(ZERO, HARNESS_REAPER_CACHE_DIR=os.path.join(str(tmp), "cache1"), HARNESS_REAPER_ONLY_PIDS=only))
    result = {"before": before, "orphan": orphan, "returncode": p.returncode, "stderr": p.stderr, "ctx": ctx,
              "reaped": planted.wait_dead(orphan["grep"]), "live_alive": planted.alive(live.pid),
              "sleep_alive": planted.alive(orphan["sleep"])}
    yield result
    planted.cleanup()
    try:
        live.stdin.close()
        live.wait(timeout=5)
    except Exception:
        pass


def test_git_bash_resolves_outside_system32_and_wsl():
    assert git_bash()


def test_git_grep_and_sleep_exist_under_the_usr_bin(usr_bin):
    assert os.path.isfile(os.path.join(usr_bin, "grep.exe")) and os.path.isfile(os.path.join(usr_bin, "sleep.exe"))


def test_the_planted_orphan_grep_and_sleep_are_running_before_the_reaper_runs(scenario):
    assert scenario["before"]


def test_the_reaper_exits_zero_with_no_traceback(scenario):
    assert scenario["returncode"] == 0 and "Traceback" not in scenario["stderr"], scenario["stderr"]


def test_an_orphaned_grep_is_reaped_at_zero_thresholds(scenario):
    assert scenario["reaped"], scenario["ctx"]


def test_the_advisory_names_the_reaped_pid_command_tail_memory_and_cpu_time(scenario):
    ctx, grep = scenario["ctx"], scenario["orphan"]["grep"]
    assert ("pid %d" % grep) in ctx and "never-matches-planted" in ctx and "MB" in ctx and "CPU" in ctx \
        and len(ctx.strip().splitlines()) == 1, ctx


def test_a_grep_with_a_live_parent_survives(scenario):
    assert scenario["live_alive"]


def test_an_orphaned_non_search_sleep_survives(scenario):
    assert scenario["sleep_alive"]


def test_a_second_call_inside_the_throttle_window_is_silent_and_reaps_nothing(usr_bin, planted, tmp_path):
    cache = os.path.join(str(tmp_path), "cache")
    reaper(dict(ZERO, HARNESS_REAPER_CACHE_DIR=cache, HARNESS_REAPER_ONLY_PIDS="-1"))   # stamps the window
    orphan = plant_orphan(planted, usr_bin, tmp_path)
    time.sleep(0.3)
    p, ctx = reaper(dict(ZERO, HARNESS_REAPER_CACHE_DIR=cache, HARNESS_REAPER_ONLY_PIDS=str(orphan["grep"])))
    assert p.returncode == 0 and ctx == "" and not p.stdout.strip() and planted.alive(orphan["grep"]), p.stdout + p.stderr


def test_default_thresholds_spare_a_small_orphan_and_zero_thresholds_reap_the_same_one(usr_bin, planted, tmp_path):
    orphan = plant_orphan(planted, usr_bin, tmp_path)
    time.sleep(0.3)
    p, _ = reaper({"HARNESS_REAPER_CACHE_DIR": os.path.join(str(tmp_path), "defaults"),
                   "HARNESS_REAPER_ONLY_PIDS": str(orphan["grep"])})
    assert p.returncode == 0 and not p.stdout.strip() and planted.alive(orphan["grep"]), p.stdout + p.stderr
    # Control: the same orphan is reaped once the thresholds are zero, so the spare above was the threshold.
    _, ctx = reaper(dict(ZERO, HARNESS_REAPER_CACHE_DIR=os.path.join(str(tmp_path), "zero"),
                         HARNESS_REAPER_ONLY_PIDS=str(orphan["grep"])))
    assert planted.wait_dead(orphan["grep"]), ctx


def test_an_unreadable_process_table_is_silent_and_exits_zero(monkeypatch, capsys, tmp_path):
    monkeypatch.syspath_prepend(HOOKS)
    import runaway_scan_reaper as rsr
    monkeypatch.setenv("HARNESS_REAPER_CACHE_DIR", os.path.join(str(tmp_path), "cache"))
    monkeypatch.setenv("HARNESS_REAPER_MIN_PRIVATE_MB", "0")
    monkeypatch.setenv("HARNESS_REAPER_MIN_CPU_SEC", "0")
    monkeypatch.setenv("HARNESS_REAPER_ONLY_PIDS", "-1")

    def broken():
        raise OSError("process table unreadable")
    monkeypatch.setattr(rsr, "snapshot", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name":"PostToolUse"}'))
    code = rsr.main()
    emitted = capsys.readouterr().out
    assert code == 0 and emitted == "", emitted


@pytest.fixture
def reaper_module(monkeypatch):
    monkeypatch.syspath_prepend(HOOKS)
    import runaway_scan_reaper as rsr
    return rsr


def test_the_throttled_gate_reports_throttled_on_a_fresh_stamp(reaper_module, tmp_path):
    cache = os.path.join(str(tmp_path), "cache")
    reaper({**ZERO, "HARNESS_REAPER_CACHE_DIR": cache, "HARNESS_REAPER_ONLY_PIDS": "-1"})
    assert reaper_module.throttled(cache, reaper_module.throttle_sec(), time.time()) is True


def test_one_real_snapshot_returns_the_process_table(reaper_module):
    rows = reaper_module.snapshot()
    assert len(rows) > 10, len(rows)


def test_a_throttled_hook_process_costs_under_3_5x_a_bare_interpreter_spawn(tmp_path):
    """The budget is a multiple of a bare interpreter spawn timed in the same loop, so machine load scales
    both sides. The throttled path is start-up plus the json/re imports (about 2.5x a bare spawn)."""
    cache = os.path.join(str(tmp_path), "cache")
    reaper({**ZERO, "HARNESS_REAPER_CACHE_DIR": cache, "HARNESS_REAPER_ONLY_PIDS": "-1"})
    spawn_best = bare_best = 1e9
    for _ in range(7):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True, timeout=60)
        bare_best = min(bare_best, time.perf_counter() - t0)
        t0 = time.perf_counter()
        reaper({"HARNESS_REAPER_CACHE_DIR": cache, "HARNESS_REAPER_ONLY_PIDS": "-1"})
        spawn_best = min(spawn_best, time.perf_counter() - t0)
    assert spawn_best < 3.5 * bare_best, "%.1f ms vs %.1f ms" % (spawn_best * 1000, bare_best * 1000)


def test_without_claude_project_dir_the_cache_dir_is_this_checkouts_claude_cache_not_cwd(tmp_path):
    cenv = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "HARNESS_REAPER_CACHE_DIR")}
    p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "import runaway_scan_reaper as r; print(r.cache_dir())", HOOKS],
                       capture_output=True, text=True, env=cenv, cwd=str(tmp_path), timeout=60)
    assert (os.path.normcase(os.path.abspath(p.stdout.strip()))
            == os.path.normcase(os.path.join(REPO, ".claude", ".cache"))), p.stdout + p.stderr
