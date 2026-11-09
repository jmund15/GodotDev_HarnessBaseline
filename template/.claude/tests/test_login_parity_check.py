#!/usr/bin/env python3
"""Proof for hooks/login_parity_check.py: SessionStart repairs login drift in the background.

The sync's `--check` is a planted script that prints scripted drift and exits with a scripted code, the
spawner is planted (it records argv and log path), and the logs dir is a temp dir, so no case reaches a
real login, the network or a real `claude`. One case spawns a real detached child running a planted
one-liner, because detaching is the mechanism under proof there. A case whose hook raises, or whose
subprocess run exits outside {0}, is a CRASH, never a pass.

    python3 .claude/tests/test_login_parity_check.py
"""
import atexit
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
HOOK = HERE.parent / "hooks" / "login_parity_check.py"

DRIFT = ("anthropic-secondary: semantic-search@dreb 2.28.0 -> 2.69.0 (update)\n"
         "anthropic: semantic-search@dreb idle shim needs repair: idle unload is not installed\n")
DEFERRED = DRIFT + "plugins: deferred while score daemon camp-x (pid 7) is live\n"


def new_root():
    root = Path(tempfile.mkdtemp(prefix="login_parity_check_"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


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


def run_hook(root, check_argv, env=None):
    """(stdout, spawns, crash) for one in-process SessionStart run."""
    module = load()
    module.CHECK_ARGV = check_argv
    module.LOGS = root / "logs"
    spawns = []
    module.spawn = lambda argv, log: spawns.append((list(argv), Path(log)))
    saved = {k: os.environ.get(k) for k in ("CLAUDE_CODE_SIDECAR",)}
    os.environ.pop("CLAUDE_CODE_SIDECAR", None)   # the proof may itself run inside a sidecar child
    for key, value in (env or {}).items():
        os.environ[key] = value
    out = io.StringIO()
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    crash = None
    try:
        with contextlib.redirect_stdout(out):
            module.main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            crash = "exit %r" % exc.code
    except Exception as exc:  # noqa: BLE001  a raising hook is a CRASH, reported by the case
        crash = "%s: %s" % (type(exc).__name__, exc)
    finally:
        sys.stdin = stdin
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return out.getvalue(), spawns, crash


def decide_or_crash(module):
    """decide()'s line, or "CRASH <error>" when it raises."""
    try:
        return module.decide()
    except Exception as exc:  # noqa: BLE001  a raising hook is a CRASH, reported by the case
        return "CRASH %s: %s" % (type(exc).__name__, exc)


def main():
    cases = []

    def case(name, ok, detail=""):
        cases.append((name, bool(ok), detail))

    if not HOOK.is_file():
        case("hooks/login_parity_check.py exists", False, str(HOOK))
        return report(cases)

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, DRIFT, 1))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    case("drift: the hook does not crash", crash is None, crash)
    case("drift: one injected line naming what updates, the log path and the restart instruction",
         len(lines) == 1 and "semantic-search@dreb 2.28.0 -> 2.69.0" in lines[0] and "idle shim" in lines[0]
         and spawns and str(spawns[0][1]) in lines[0]
         and "restart sessions on this login after it ends" in lines[0], repr(out))
    case("drift: the sync starts once, detached, without --check, logging under the logs dir",
         len(spawns) == 1 and spawns[0][0][-1].endswith("sync_plan_config.py") and "--check" not in spawns[0][0]
         and spawns[0][1].parent == root / "logs", repr(spawns))

    out2, spawns2, crash = run_hook(root, plant(root, DRIFT, 1))
    case("a second session while that sync is recent starts no second sync and names the first one's log",
         crash is None and not spawns2 and spawns and str(spawns[0][1]) in out2, repr((out2, spawns2, crash)))

    root = new_root()
    (root / "logs").mkdir()
    module = load()
    module.CHECK_ARGV = plant(root, DRIFT, 1)
    module.LOGS = root / "logs"
    claimed = []
    module.spawn = lambda argv, log: claimed.append(Path(log))
    (root / "logs" / module.MARKER).write_text(json.dumps({"at": 0, "log": "stale.log"}), encoding="utf-8")
    saved_sidecar = os.environ.pop("CLAUDE_CODE_SIDECAR", None)
    lines = []
    racers = [threading.Thread(target=lambda: lines.append(module.decide())) for _ in range(2)]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=120)
    if saved_sidecar is not None:
        os.environ["CLAUDE_CODE_SIDECAR"] = saved_sidecar
    case("two sessions that both find a stale marker start one sync: the other names that sync's log",
         len(lines) == 2 and len(claimed) == 1 and sum(str(claimed[0]) in (ln or "") for ln in lines) == 2,
         repr((lines, claimed)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, "", 0))
    case("no drift: nothing injected, nothing started", crash is None and out.strip() == "" and not spawns,
         repr((out, spawns, crash)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, DEFERRED, 1))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    case("drift deferred by a live benchmark: one line naming the drift and the run; nothing started",
         crash is None and not spawns and len(lines) == 1 and "camp-x" in lines[0] and "2.69.0" in lines[0],
         repr((out, spawns, crash)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, "", 2))
    lines = [ln for ln in out.splitlines() if ln.strip()]
    case("a failed check: one line naming the exit and the command to run; nothing started",
         crash is None and not spawns and len(lines) == 1 and "exit 2" in lines[0]
         and "sync_plan_config.py --check" in lines[0], repr((out, spawns, crash)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, DRIFT, 1), env={"CLAUDE_CODE_SIDECAR": "1"})
    case("a sidecar child runs no check and starts nothing",
         crash is None and out.strip() == "" and not spawns and not (root / "check_ran").exists(),
         repr((out, spawns, crash)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, DRIFT, 1), env={"CLAUDE_CODE_REMOTE": "true"})
    os.environ.pop("CLAUDE_CODE_REMOTE", None)
    case("a cloud VM runs no check and starts nothing (its logins are the account's, not this machine's)",
         crash is None and out.strip() == "" and not spawns and not (root / "check_ran").exists(),
         repr((out, spawns, crash)))

    root = new_root()
    out, spawns, crash = run_hook(root, plant(root, DRIFT, 1), env={"CLAUDE_CODE_REMOTE": "1"})
    os.environ.pop("CLAUDE_CODE_REMOTE", None)
    case("a cloud VM with CLAUDE_CODE_REMOTE=1 runs no check (same cloud test as the other hooks)",
         crash is None and out.strip() == "" and not spawns and not (root / "check_ran").exists(),
         repr((out, spawns, crash)))

    root = new_root()
    module = load()
    module.CHECK_ARGV = plant(root, DRIFT, 1)
    module.LOGS = root / "logs"

    def refuse(argv, log):
        raise OSError(2, "planted spawn failure")
    module.spawn = refuse
    saved_sidecar = os.environ.pop("CLAUDE_CODE_SIDECAR", None)
    failed_line = decide_or_crash(module)
    retried = []
    module.spawn = lambda argv, log: retried.append(Path(log))
    retry_line = module.decide()
    case("a failed spawn names the failure and releases its claim: the next session starts the sync",
         "could not start" in (failed_line or "") and "planted spawn failure" in failed_line
         and "sync_plan_config.py" in failed_line and len(retried) == 1 and str(retried[0]) in (retry_line or ""),
         repr((failed_line, retry_line, retried)))

    root = new_root()
    module = load()
    module.CHECK_ARGV = plant(root, DRIFT, 1)
    module.LOGS = root / "logs"

    def superseded(argv, log):
        (root / "logs" / module.MARKER).write_text(json.dumps({"at": time.time(), "log": "successor.log"}),
                                                   encoding="utf-8")
        raise OSError(2, "planted spawn failure")
    module.spawn = superseded
    decide_or_crash(module)
    if saved_sidecar is not None:
        os.environ["CLAUDE_CODE_SIDECAR"] = saved_sidecar
    held = json.loads((root / "logs" / module.MARKER).read_text(encoding="utf-8"))
    case("a failed spawn never releases a newer session's claim", held.get("log") == "successor.log", repr(held))

    root = new_root()
    module = load()
    marker = root / "grandchild_outlived"
    grandchild = root / "grandchild.py"
    grandchild.write_text("import pathlib, time\ntime.sleep(6)\npathlib.Path(%s).write_text('x')\n"
                          % json.dumps(str(marker)), encoding="utf-8", newline="\n")
    hung = root / "hung_check.py"
    hung.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, %s])\ntime.sleep(30)\n"
                    % json.dumps(str(grandchild)), encoding="utf-8", newline="\n")
    module.CHECK_ARGV = [sys.executable, str(hung)]
    module.CHECK_TIMEOUT = 2
    module.LOGS = root / "logs"
    spawned = []
    module.spawn = lambda argv, log: spawned.append(list(argv))
    saved_sidecar = os.environ.pop("CLAUDE_CODE_SIDECAR", None)
    start = time.time()
    line = module.decide()
    elapsed = time.time() - start
    if saved_sidecar is not None:
        os.environ["CLAUDE_CODE_SIDECAR"] = saved_sidecar
    time.sleep(max(0.0, 8 - (time.time() - start)))
    case("a hung check ends at its deadline with the processes it started: the timeout line, no grandchild left",
         "ran past 2 s" in (line or "") and elapsed < 5 and not marker.exists() and not spawned,
         repr((line, round(elapsed, 1), marker.exists(), spawned)))

    proc = subprocess.run([sys.executable, str(HOOK)], input="{}", capture_output=True, text=True,
                          env={**os.environ, "CLAUDE_CODE_SIDECAR": "1"}, timeout=60)
    case("the hook imports and exits 0 as SessionStart runs it (a sidecar child, so nothing is checked)",
         proc.returncode == 0 and proc.stdout.strip() == "", repr((proc.returncode, proc.stdout, proc.stderr)))

    root = new_root()
    module = load()
    log = root / "spawned.log"
    module.spawn([sys.executable, "-c", "print('detached child ran')"], log)
    deadline = time.time() + 20
    while time.time() < deadline and "detached child ran" not in (log.read_text(encoding="utf-8") if log.exists() else ""):
        time.sleep(0.2)
    case("the real spawner starts a child that writes its output to the log",
         log.exists() and "detached child ran" in log.read_text(encoding="utf-8"), str(log))
    return report(cases)


def report(cases):
    failed = [n for n, ok, _d in cases if not ok]
    for name, ok, detail in cases:
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else "   [%s]" % str(detail)[:500]))
    print("\n%d/%d passed" % (len(cases) - len(failed), len(cases)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
