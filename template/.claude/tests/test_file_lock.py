"""Re-runnable proof for hooks/_file_lock.py and its three consumers.

The lock's liveness evidence is the holder's open handle, never the lock's age: a live
holder that stalls keeps its lock, and a holder that exits (however it exits) releases it.
The lock cases once lived in test_self_eval_archive_store.py and
test_orchestration_metrics_pending.py; they moved here when both tools and _hook_state
adopted the one implementation.

    python3 .claude/tests/test_file_lock.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.normpath(os.path.join(HERE, "..", "hooks"))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
FILE_LOCK_PATH = os.path.join(HOOKS, "_file_lock.py")
HOOK_STATE_PATH = os.path.join(HOOKS, "_hook_state.py")
STORE_PATH = os.path.join(TOOLS, "self_eval_archive_store.py")
METRICS_PATH = os.path.join(TOOLS, "orchestration_metrics.py")

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


def main():
    cases = []
    root = tempfile.mkdtemp(prefix="filelock_")

    try:
        file_lock = load("_file_lock_under_test", FILE_LOCK_PATH)
    except Exception as exc:  # the module is the subject; its absence fails its cases
        print("cannot load _file_lock: %s: %s" % (type(exc).__name__, exc))
        file_lock = None

    if file_lock is not None:
        # A live holder whose lock is ancient keeps it; probing it never ends the holder.
        live = os.path.join(root, "owner-probe.lock")
        holder, ready = start_holder(FILE_LOCK_PATH, "file_lock", live)
        try:
            ancient = time.time() - 3600
            os.utime(live, (ancient, ancient))
            try:
                with file_lock.locked(live, 0.3):
                    live_rejected = False
            except TimeoutError:
                live_rejected = True
            acquire_refused = file_lock.acquire(live, 0.2) is None
            survived = holder.poll() is None
            holder.kill()
            holder.wait(timeout=10)
            try:
                with file_lock.locked(live, 5.0):
                    killed_recovered = True
            except TimeoutError:
                killed_recovered = False
        finally:
            stop(holder)
        cases.append(("an old lock held by a live process is never stolen",
                      ready and live_rejected and acquire_refused))
        cases.append(("probing a live lock owner never terminates it", survived))
        cases.append(("a killed owner's lock is recovered and removed on release",
                      killed_recovered and not os.path.exists(live)))

        crashed = os.path.join(root, "crashed-owner.lock")
        crash = subprocess.run(
            [sys.executable, "-c", HOLDER, FILE_LOCK_PATH, "file_lock", crashed, "crash"],
            capture_output=True, text=True, timeout=60,
        )
        crash_left_lock = crash.stdout.strip() == "held" and os.path.exists(crashed)
        try:
            with file_lock.locked(crashed, 5.0):
                crashed_recovered = True
        except TimeoutError:
            crashed_recovered = False
        cases.append(("a lock abandoned by an exited owner is recovered",
                      crash_left_lock and crashed_recovered and not os.path.exists(crashed)))

        legacy = os.path.join(root, "legacy-dir.lock")
        os.mkdir(legacy)
        handle = file_lock.acquire(legacy, 2.0)
        legacy_taken = handle is not None and os.path.isfile(legacy)
        if handle is not None:
            file_lock.release(handle)
        cases.append(("a legacy mkdir lock directory is removed and the file lock acquired",
                      legacy_taken and not os.path.exists(legacy)))

        blocker = os.path.join(root, "not-a-directory")
        with open(blocker, "w", encoding="ascii") as fh:
            fh.write("x")
        try:
            impossible = file_lock.acquire(os.path.join(blocker, "child.lock"), 0.2)
            total = impossible is None
        except Exception:
            total = False
        cases.append(("acquire is total: an impossible lock path returns None", total))
        file_lock.release(None)
        cases.append(("release(None) is a no-op", True))

        counter = os.path.join(root, "counter.txt")
        with open(counter, "w", encoding="ascii") as fh:
            fh.write("0")
        workers = [subprocess.Popen([sys.executable, "-c", COUNTER, FILE_LOCK_PATH, counter, "25"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for _ in range(6)]
        outcomes = [worker.communicate(timeout=120) + (worker.returncode,) for worker in workers]
        with open(counter, encoding="ascii") as fh:
            total_count = fh.read()
        cases.append(("six processes incrementing under the lock lose no update",
                      all(outcome[2] == 0 for outcome in outcomes) and total_count == "150"))
    else:
        for label in ("an old lock held by a live process is never stolen",
                      "probing a live lock owner never terminates it",
                      "a killed owner's lock is recovered and removed on release",
                      "a lock abandoned by an exited owner is recovered",
                      "a legacy mkdir lock directory is removed and the file lock acquired",
                      "acquire is total: an impossible lock path returns None",
                      "release(None) is a no-op",
                      "six processes incrementing under the lock lose no update"):
            cases.append((label, False))

    # _hook_state: a live holder stalled past the stale threshold keeps its lock. The threshold
    # is injected short (0.05 s) so the stall is real without a 30 s sleep.
    hook_state = load("_hook_state_under_test", HOOK_STATE_PATH)
    stalled_state = os.path.join(root, "stalled", "state.json")
    holder, ready = start_holder(HOOK_STATE_PATH, "hook_state", stalled_state)
    try:
        hook_state._LOCK_STALE_SECONDS = 0.05
        time.sleep(0.3)
        written, result = hook_state.update_json_locked(stalled_state, lambda state: "stole")
        survived = holder.poll() is None
    finally:
        stop(holder)
    cases.append(("_hook_state: a live holder stalled past the stale threshold keeps its lock",
                  ready and written is False and result is None and survived
                  and not os.path.exists(stalled_state)))
    written_after, _ = hook_state.update_json_locked(stalled_state, lambda state: "after")
    cases.append(("_hook_state: the lock is recovered once the stalled holder is gone",
                  written_after is True))

    path_loaded = subprocess.run(
        [sys.executable, "-c", PATH_LOADED_HOOK_STATE, HOOK_STATE_PATH,
         os.path.join(root, "path-loaded", "state.json")],
        capture_output=True, text=True, timeout=60, cwd=root,
    )
    cases.append(("_hook_state loaded by file path from a foreign cwd still locks and writes",
                  path_loaded.returncode == 0 and path_loaded.stdout.strip() == "True ran"))

    # Tools wait on a holder of the shared lock and carry no private copy of it.
    store = load("self_eval_archive_store_under_test", STORE_PATH)
    tool_lock = os.path.join(root, "store-wiring.lock")
    holder, ready = start_holder(FILE_LOCK_PATH, "file_lock", tool_lock)
    original_timeout = store.LOCK_TIMEOUT_SECONDS
    store.LOCK_TIMEOUT_SECONDS = 0.3
    try:
        try:
            with store._lock(tool_lock):
                store_waited = False
        except TimeoutError:
            store_waited = True
    finally:
        store.LOCK_TIMEOUT_SECONDS = original_timeout
        stop(holder)
    cases.append(("self_eval_archive_store waits on a _file_lock holder",
                  ready and store_waited))
    cases.append(("self_eval_archive_store carries no private lock implementation",
                  not any(hasattr(store, name) for name in
                          ("_create_lock", "_remove_abandoned_lock", "_release_lock"))))

    metrics = load("orchestration_metrics_under_test", METRICS_PATH)
    metrics.PENDING_VERDICTS = os.path.join(root, "orchestration_verdicts.json")
    holder, ready = start_holder(FILE_LOCK_PATH, "file_lock", metrics.PENDING_VERDICTS + ".lock")
    original_timeout = metrics.ARCHIVE_LOCK_TIMEOUT_SECONDS
    metrics.ARCHIVE_LOCK_TIMEOUT_SECONDS = 0.3
    try:
        try:
            with metrics._pending_lock():
                metrics_waited = False
        except TimeoutError:
            metrics_waited = True
    finally:
        metrics.ARCHIVE_LOCK_TIMEOUT_SECONDS = original_timeout
        stop(holder)
    cases.append(("orchestration_metrics waits on a _file_lock holder",
                  ready and metrics_waited))
    cases.append(("orchestration_metrics carries no private lock implementation",
                  not any(hasattr(metrics, name) for name in
                          ("_create_lock", "_remove_abandoned_lock", "_release_lock",
                           "_exclusive_lock"))))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
