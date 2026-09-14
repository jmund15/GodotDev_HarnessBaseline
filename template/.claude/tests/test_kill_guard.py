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
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kg", os.path.join(HERE, "..", "hooks",
                                                                 "kill_guard.py"))
kg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kg)

FAKE = {
    "21764": "python3 .claude/tools/sidecar_fanout.py .claude/scratch/jobs.json --compare|python.exe",
    "53236": "C:/Program Files/Git/bin/bash.exe .claude/scripts/codex_proxy_sidecar.sh -m luna|bash.exe",
    "9001": "grep -rl claude-gpt /c/Users/<user>/|grep.exe",
    "9002": "node C:/Users/<user>/AppData/.../semantic-search/server.js|node.exe",
    "9003": "claude.exe --resume|claude.exe",
    "9004": "pwsh -File .claude/scripts/regression_gate.ps1|pwsh.exe",
    "9005": "",
}


def stub(pid):
    return FAKE.get(pid, None)


kg.commandline = stub
CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def v(cmd, env=None):
    return kg.verdict(cmd, env or {})


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
