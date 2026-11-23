"""Proof for hooks/_file_lock.py and its consumers.

The lock's liveness evidence is the holder's open handle, never the lock's age: a live
holder that stalls keeps its lock, and a holder that exits (however it exits) releases it.
"""
import importlib.util
import os
import subprocess
import sys
import time
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "hooks"))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
FILE_LOCK_PATH = os.path.join(HOOKS, "_file_lock.py")
HOOK_STATE_PATH = os.path.join(HOOKS, "_hook_state.py")

# argv: module path, "file_lock" | "hook_state", lock/state path, "hold" | "crash"
HOLDER = """
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("lock_owner", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
kind, path, mode = sys.argv[2], sys.argv[3], sys.argv[4]
if kind == "file_lock":
    held = module.acquire(path, 10.0)
else:
    held = module._acquire_json_lock(path)
print("held" if held else "refused", flush=True)
if mode == "crash":
    os._exit(0)
time.sleep(120)
"""

COUNTER = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("lock_counter", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
target = sys.argv[2]
for _ in range(int(sys.argv[3])):
    with module.locked(target + ".lock", 30.0):
        with open(target, encoding="ascii") as handle:
            value = int(handle.read() or "0")
        with open(target, "w", encoding="ascii") as handle:
            handle.write(str(value + 1))
"""

# Loads _hook_state by file path from a foreign cwd with no hooks dir on sys.path, the way
# tools/session_end_check.py does.
PATH_LOADED_HOOK_STATE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("_close_state", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
written, result = module.update_json_locked(sys.argv[2], lambda state: state.update(k=1) or "ran")
print("%s %s" % (written, result))
"""


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def start_holder(module_path, kind, path, mode="hold"):
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, module_path, kind, path, mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return process, process.stdout.readline().strip() == "held"


def stop(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=10)


@pytest.fixture(scope="module")
def file_lock():
    return load("_file_lock_under_test", FILE_LOCK_PATH)


@pytest.fixture
def holder_of():
    """`start(module_path, kind, path, mode)` starts a holder process; every one still running is killed at teardown."""
    started = []

    def start(*args, **kwargs):
        process, ready = start_holder(*args, **kwargs)
        started.append(process)
        return process, ready
    yield start
    for process in started:
        stop(process)


def test_an_old_lock_held_by_a_live_process_is_never_stolen_nor_the_owner_terminated(file_lock, holder_of, tmp_path):
    live = str(tmp_path / "owner-probe.lock")
    holder, ready = holder_of(FILE_LOCK_PATH, "file_lock", live)
    ancient = time.time() - 3600
    os.utime(live, (ancient, ancient))
    with pytest.raises(TimeoutError):
        with file_lock.locked(live, 0.3):
            pass
    assert ready and file_lock.acquire(live, 0.2) is None
    assert holder.poll() is None, "probing a live lock owner terminated it"


def test_a_killed_owners_lock_is_recovered_and_removed_on_release(file_lock, holder_of, tmp_path):
    live = str(tmp_path / "owner-probe.lock")
    holder, ready = holder_of(FILE_LOCK_PATH, "file_lock", live)
    assert ready
    holder.kill()
    holder.wait(timeout=10)
    with file_lock.locked(live, 5.0):
        pass
    assert not os.path.exists(live)


def test_a_lock_abandoned_by_an_exited_owner_is_recovered(file_lock, tmp_path):
    crashed = str(tmp_path / "crashed-owner.lock")
    crash = subprocess.run([sys.executable, "-c", HOLDER, FILE_LOCK_PATH, "file_lock", crashed, "crash"],
                           capture_output=True, text=True, timeout=60)
    assert crash.stdout.strip() == "held" and os.path.exists(crashed)
    with file_lock.locked(crashed, 5.0):
        pass
    assert not os.path.exists(crashed)


def test_a_legacy_mkdir_lock_directory_is_removed_and_the_file_lock_acquired(file_lock, tmp_path):
    legacy = str(tmp_path / "legacy-dir.lock")
    os.mkdir(legacy)
    handle = file_lock.acquire(legacy, 2.0)
    try:
        assert handle is not None and os.path.isfile(legacy)
    finally:
        file_lock.release(handle)
    assert not os.path.exists(legacy)


@pytest.mark.skipif(os.name == "nt", reason="flock seam is POSIX-only")
def test_a_contender_arriving_while_the_lock_is_created_leaves_one_holder(file_lock, tmp_path, monkeypatch):
    # The seam runs the contender at the creator's first flock.
    creating = str(tmp_path / "creating.lock")
    real_fcntl = file_lock.fcntl
    contender = []

    def flock_after_contender(fd, operation):
        if not contender:
            contender.append(None)
            contender[0] = file_lock.acquire(creating, 0)
        return real_fcntl.flock(fd, operation)

    monkeypatch.setattr(file_lock, "fcntl", types.SimpleNamespace(
        flock=flock_after_contender, LOCK_EX=real_fcntl.LOCK_EX, LOCK_NB=real_fcntl.LOCK_NB))
    creator = file_lock.acquire(creating, 0)
    monkeypatch.undo()
    holders = [h for h in (creator, contender[0] if contender else None) if h is not None]
    assert len(holders) == 1 and os.path.samestat(os.fstat(holders[0].fd), os.stat(creating))
    for held in holders:
        file_lock.release(held)
    assert not os.path.exists(creating)


@pytest.mark.skipif(os.name == "nt", reason="hard-link refusal seam is POSIX-only")
def test_a_filesystem_without_hard_links_still_locks_in_place(file_lock, tmp_path, monkeypatch):
    unlinkable = str(tmp_path / "no-hard-links" / "fat.lock")

    def link_unsupported(*_args, **_kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", link_unsupported)
    in_place = file_lock.acquire(unlinkable, 1.0)
    rival = file_lock.acquire(unlinkable, 0) if in_place is not None else None
    monkeypatch.undo()
    assert in_place is not None and rival is None and os.path.isfile(unlinkable)
    file_lock.release(in_place)
    assert os.listdir(os.path.dirname(unlinkable)) == [], "a staged file was left"


def test_acquire_is_total_and_release_none_is_a_no_op(file_lock, tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="ascii")
    assert file_lock.acquire(str(blocker / "child.lock"), 0.2) is None
    file_lock.release(None)


def test_six_processes_incrementing_under_the_lock_lose_no_update(tmp_path):
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="ascii")
    workers = [subprocess.Popen([sys.executable, "-c", COUNTER, FILE_LOCK_PATH, str(counter), "25"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(6)]
    codes = [worker.communicate(timeout=120) and worker.returncode for worker in workers]
    assert codes == [0] * 6 and counter.read_text(encoding="ascii") == "150"


def test_hook_state_a_live_holder_stalled_for_a_while_keeps_its_lock(holder_of, tmp_path):
    # Liveness is the holder's open handle, not the lock's age (the old stale threshold no longer exists).
    hook_state = load("_hook_state_under_test", HOOK_STATE_PATH)
    stalled_state = str(tmp_path / "stalled" / "state.json")
    holder, ready = holder_of(HOOK_STATE_PATH, "hook_state", stalled_state)
    time.sleep(0.3)
    written, result = hook_state.update_json_locked(stalled_state, lambda state: "stole")
    assert ready and written is False and result is None and holder.poll() is None
    assert not os.path.exists(stalled_state)
    stop(holder)
    written_after, _ = hook_state.update_json_locked(stalled_state, lambda state: "after")
    assert written_after is True, "the lock was not recovered once the stalled holder was gone"


def test_hook_state_loaded_by_file_path_from_a_foreign_cwd_still_locks_and_writes(tmp_path):
    path_loaded = subprocess.run(
        [sys.executable, "-c", PATH_LOADED_HOOK_STATE, HOOK_STATE_PATH, str(tmp_path / "path-loaded" / "state.json")],
        capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert path_loaded.returncode == 0 and path_loaded.stdout.strip() == "True ran"
