#!/usr/bin/env python3
"""Proof for hooks/runaway_scan_reaper.py with real planted processes.

Every process this proof plants is pinned by a Windows handle at plant time. The reaper runs
with HARNESS_REAPER_ONLY_PIDS limited to those pids, so it can never select another session's
process, and cleanup terminates through the pinned handles, so a reused pid is never hit.

Cases: an orphaned Git grep blocked on a pipe is reaped at zero thresholds; a grep with a live
parent survives; an orphaned non-search process (Git sleep) survives; default thresholds spare a
small orphan; a second call inside the throttle window takes no snapshot; an unreadable process
table is silent; pre_bash_dispatch, post_read_dispatch and post_edit_dispatch each surface a
planted reap in their own additionalContext; the registrations are UserPromptSubmit plus the
anchored PostToolUse matcher (never `*`), both run through Git Bash, and the registered
PreToolUse dispatcher denies a recursive grep through Git Bash.

    python3 .claude/tests/test_runaway_scan_reaper.py
"""
import ctypes
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

import _settings_probe

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
HOOKS = os.path.join(REPO, ".claude", "hooks")
REAPER = os.path.join(HOOKS, "runaway_scan_reaper.py")
SETTINGS = _settings_probe.settings_path(os.path.join(REPO, ".claude"))
REAPER_CMD_TAIL = ".claude/hooks/runaway_scan_reaper.py"
NARROW_MATCHER = "^(?:Agent|Workflow|TaskStop|TaskOutput|Skill|ToolSearch|LSP|NotebookEdit|mcp__.*)$"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
SYNCHRONIZE, PROCESS_TERMINATE, QUERY_LIMITED = 0x00100000, 0x0001, 0x1000

failures = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        failures.append(label + (" :: " + str(detail)[:400] if detail else ""))


def git_bash():
    """Git Bash, never WSL: CLAUDE_CODE_GIT_BASH_PATH, else PATH, rejected under System32."""
    path = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or shutil.which("bash") or ""
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
    out = os.path.join(tmp, "plant_%d.json" % time.time_ns())
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


def main():
    bash = git_bash()
    check("Git Bash resolves outside System32/WSL (%s)" % bash, bool(bash))
    if not bash:
        return 1
    usr_bin = git_usr_bin(bash)
    check("Git grep.exe and sleep.exe exist under %s" % usr_bin,
          os.path.isfile(os.path.join(usr_bin, "grep.exe")) and os.path.isfile(os.path.join(usr_bin, "sleep.exe")))

    tmp = tempfile.mkdtemp(prefix="rsr_")
    planted = Planted()
    live = None
    try:
        # --- reap an orphan; controls: live-parent grep and orphaned sleep survive ----------------
        o1 = plant_orphan(planted, usr_bin, tmp)
        live = subprocess.Popen([os.path.join(usr_bin, "grep.exe"), "never-matches-live"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        planted.pin(live.pid)
        time.sleep(0.5)
        check("planted orphan grep %d and sleep %d are running, parent %d gone" % (o1["grep"], o1["sleep"], o1["middle"]),
              planted.alive(o1["grep"]) and planted.alive(o1["sleep"]))

        cache1 = os.path.join(tmp, "cache1")
        only = ",".join(str(x) for x in (o1["grep"], o1["sleep"], live.pid))
        zero = {"HARNESS_REAPER_CACHE_DIR": cache1, "HARNESS_REAPER_MIN_PRIVATE_MB": "0", "HARNESS_REAPER_MIN_CPU_SEC": "0",
                "HARNESS_REAPER_ONLY_PIDS": only}
        p, ctx = reaper(zero)
        check("reaper exits 0 with no traceback", p.returncode == 0 and "Traceback" not in p.stderr, p.stderr)
        check("orphaned grep %d is reaped at zero thresholds" % o1["grep"], planted.wait_dead(o1["grep"]), ctx)
        check("advisory names the reaped pid, command tail, memory and CPU time",
              ("pid %d" % o1["grep"]) in ctx and "never-matches-planted" in ctx and "MB" in ctx and "CPU" in ctx
              and len(ctx.strip().splitlines()) == 1, ctx)
        check("grep %d with a live parent survives" % live.pid, planted.alive(live.pid))
        check("orphaned non-search sleep %d survives" % o1["sleep"], planted.alive(o1["sleep"]))

        # --- throttle: a second call inside the window takes no snapshot ------------------------
        o2 = plant_orphan(planted, usr_bin, tmp)
        time.sleep(0.3)
        zero2 = dict(zero, HARNESS_REAPER_ONLY_PIDS=str(o2["grep"]))
        p, ctx = reaper(zero2)
        check("second call inside the throttle window is silent and reaps nothing",
              p.returncode == 0 and ctx == "" and not p.stdout.strip() and planted.alive(o2["grep"]), p.stdout + p.stderr)

        # --- default thresholds spare a small orphan (fresh window) ------------------------------
        p, ctx = reaper({"HARNESS_REAPER_CACHE_DIR": os.path.join(tmp, "cache2"), "HARNESS_REAPER_ONLY_PIDS": str(o2["grep"])})
        check("default thresholds (1024 MB / 600 s) spare a small orphan, silently",
              p.returncode == 0 and not p.stdout.strip() and planted.alive(o2["grep"]), p.stdout + p.stderr)

        # --- positive control for the throttle case: a fresh window does reap it -----------------
        p, ctx = reaper(dict(zero2, HARNESS_REAPER_CACHE_DIR=os.path.join(tmp, "cache3")))
        check("the same orphan is reaped once the window is fresh", planted.wait_dead(o2["grep"]), ctx)

        # --- unreadable process table is silent ---------------------------------------------------
        sys.path.insert(0, HOOKS)
        try:
            import runaway_scan_reaper as rsr
            os.environ.update({"HARNESS_REAPER_CACHE_DIR": os.path.join(tmp, "cache4"),
                               "HARNESS_REAPER_MIN_PRIVATE_MB": "0", "HARNESS_REAPER_MIN_CPU_SEC": "0",
                               "HARNESS_REAPER_ONLY_PIDS": "-1"})

            def broken():
                raise OSError("process table unreadable")
            rsr.snapshot = broken
            saved_in, saved_out = sys.stdin, sys.stdout
            sys.stdin, sys.stdout = io.StringIO('{"hook_event_name":"PostToolUse"}'), io.StringIO()
            try:
                code = rsr.main()
                emitted = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = saved_in, saved_out
            check("an unreadable process table is silent and exits 0", code == 0 and emitted == "", emitted)

            # --- cost evidence ------------------------------------------------------------------
            best = 1e9
            for _ in range(200):
                t0 = time.perf_counter()
                throttled = rsr.throttled(os.path.join(tmp, "cache1"), rsr.throttle_sec(), time.time())
                best = min(best, time.perf_counter() - t0)
            check("throttled gate reports throttled on a fresh stamp", throttled is True)
            importlib_reload = __import__("importlib").reload
            rsr = importlib_reload(rsr)
            snap_best = 1e9
            for _ in range(5):
                t0 = time.perf_counter()
                rows = rsr.snapshot()
                snap_best = min(snap_best, time.perf_counter() - t0)
            check("one real snapshot returns the process table (%d rows)" % len(rows), len(rows) > 10)
            spawn_best = 1e9
            for _ in range(5):
                t0 = time.perf_counter()
                reaper({"HARNESS_REAPER_CACHE_DIR": os.path.join(tmp, "cache1"), "HARNESS_REAPER_ONLY_PIDS": "-1"})
                spawn_best = min(spawn_best, time.perf_counter() - t0)
            print("cost: throttled gate in-process %.3f ms | throttled hook process end-to-end %.1f ms | "
                  "one snapshot %.1f ms (best of runs)" % (best * 1000, spawn_best * 1000, snap_best * 1000))
            check("throttled hook process costs under 50 ms end-to-end", spawn_best < 0.050, "%.1f ms" % (spawn_best * 1000))
        except ImportError as exc:
            check("runaway_scan_reaper imports", False, exc)
        finally:
            for k in ("HARNESS_REAPER_CACHE_DIR", "HARNESS_REAPER_MIN_PRIVATE_MB", "HARNESS_REAPER_MIN_CPU_SEC", "HARNESS_REAPER_ONLY_PIDS"):
                os.environ.pop(k, None)

        # --- cache root without CLAUDE_PROJECT_DIR: this checkout, never cwd -------------------------
        cenv = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "HARNESS_REAPER_CACHE_DIR")}
        p = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                            "import runaway_scan_reaper as r; print(r.cache_dir())", HOOKS],
                           capture_output=True, text=True, env=cenv, cwd=tmp, timeout=60)
        check("without CLAUDE_PROJECT_DIR the cache dir is this checkout's .claude/.cache, not cwd",
              os.path.normcase(os.path.abspath(p.stdout.strip()))
              == os.path.normcase(os.path.join(REPO, ".claude", ".cache")), p.stdout + p.stderr)

        # --- each dispatcher surfaces a planted advisory line in its own additionalContext --------
        scratch = os.path.join(tmp, "edited.txt")
        with open(scratch, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        dispatchers = (
            ("pre_bash_dispatch.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                      "tool_input": {"command": "git status --short | head -1"}}),
            ("post_read_dispatch.py", {"hook_event_name": "PostToolUse", "tool_name": "Read",
                                       "tool_input": {"file_path": scratch}, "tool_response": {}}),
            ("post_edit_dispatch.py", {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                                       "tool_input": {"file_path": scratch, "old_string": "x", "new_string": "y"},
                                       "tool_response": {}}),
        )
        for i, (script, pl) in enumerate(dispatchers):
            od = plant_orphan(planted, usr_bin, tmp)
            time.sleep(0.3)
            pl = dict(pl, session_id="rsrd%04d" % i, cwd=REPO)
            denv = dict(os.environ, CLAUDE_PROJECT_DIR=REPO, PYTHONIOENCODING="utf-8",
                        HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "dstate"),
                        HARNESS_REAPER_CACHE_DIR=os.path.join(tmp, "dcache%d" % i),
                        HARNESS_REAPER_MIN_PRIVATE_MB="0", HARNESS_REAPER_MIN_CPU_SEC="0",
                        HARNESS_REAPER_ONLY_PIDS=str(od["grep"]))
            p = subprocess.run([sys.executable, os.path.join(HOOKS, script)], input=json.dumps(pl),
                               capture_output=True, text=True, encoding="utf-8", timeout=120, env=denv, cwd=REPO)
            dctx = ""
            try:
                dctx = (json.loads(p.stdout).get("hookSpecificOutput") or {}).get("additionalContext", "") if p.stdout.strip() else ""
            except ValueError:
                dctx = "UNPARSEABLE:" + p.stdout
            check("%s surfaces the planted reap of pid %d in additionalContext" % (script, od["grep"]),
                  p.returncode == 0 and ("killed orphaned search pid %d" % od["grep"]) in dctx
                  and planted.wait_dead(od["grep"]), (dctx + " | " + p.stderr)[:400])

        # --- registration, run through Git Bash ---------------------------------------------------
        with open(SETTINGS, encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]

        def commands(event, want_matcher=None):
            found = []
            for entry in hooks.get(event, []):
                if want_matcher is not None and entry.get("matcher") != want_matcher:
                    continue
                for h in entry.get("hooks", []):
                    if REAPER_CMD_TAIL in h.get("command", ""):
                        found.append((entry.get("matcher"), h))
            return found

        benv = dict(os.environ, CLAUDE_PROJECT_DIR=REPO.replace("\\", "/"), PYTHONIOENCODING="utf-8",
                    HARNESS_REAPER_CACHE_DIR=os.path.join(tmp, "cache5"), HARNESS_REAPER_ONLY_PIDS="-1",
                    HARNESS_REAPER_MIN_PRIVATE_MB="0", HARNESS_REAPER_MIN_CPU_SEC="0")
        all_post = commands("PostToolUse")
        check("PostToolUse registers the reaper exactly once, never on `*`",
              len(all_post) == 1 and all_post[0][0] == NARROW_MATCHER, all_post)
        for event, matcher in (("PostToolUse", NARROW_MATCHER), ("UserPromptSubmit", None)):
            found = commands(event, matcher)
            check("%s registers the reaper (matcher %r)" % (event, matcher), len(found) == 1, found)
            for m, h in found:
                pl = json.dumps({"hook_event_name": event, "session_id": "rsr00002", "tool_name": "Bash",
                                 "prompt": "x", "tool_input": {}})
                p = subprocess.run([bash, "-c", h["command"]], input=pl, capture_output=True, text=True,
                                   encoding="utf-8", timeout=60, env=benv, cwd=REPO)
                check("%s registered command runs through Git Bash: exit 0, no traceback (timeout %s)"
                      % (event, h.get("timeout")), p.returncode == 0 and "Traceback" not in p.stderr
                      and "No such file" not in p.stderr, p.stderr)

        pre = [h for e in hooks.get("PreToolUse", []) if e.get("matcher") == "Bash|PowerShell|Monitor"
               for h in e.get("hooks", []) if "pre_bash_dispatch.py" in h.get("command", "")]
        check("PreToolUse Bash|PowerShell|Monitor registers pre_bash_dispatch.py", len(pre) == 1, pre)
        for h in pre:
            pl = json.dumps({"hook_event_name": "PreToolUse", "session_id": "rsr00003", "tool_name": "Bash",
                             "cwd": REPO, "tool_input": {"command": "grep -rln ProjectSuite ."}})
            p = subprocess.run([bash, "-c", h["command"]], input=pl, capture_output=True, text=True,
                               encoding="utf-8", timeout=90, env=dict(benv, HARNESS_HOOK_STATE_DIR=os.path.join(tmp, "st")), cwd=REPO)
            decision = ""
            try:
                decision = (json.loads(p.stdout).get("hookSpecificOutput") or {}).get("permissionDecision", "")
            except ValueError:
                pass
            check("registered pre_bash_dispatch command denies a recursive grep through Git Bash",
                  p.returncode == 0 and decision == "deny", p.stdout[:200] + p.stderr[:200])
    except Exception as exc:  # a planting or harness failure is a FAIL, never a pass
        check("proof ran to completion", False, repr(exc))
    finally:
        if live is not None:
            try:
                live.stdin.close()
            except Exception:
                pass
        planted.cleanup()
        if live is not None:
            try:
                live.wait(timeout=5)
            except Exception:
                pass

    print("\n%d failure(s)" % len(failures) if failures else "\nall ok")
    for f in failures:
        print("  " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
