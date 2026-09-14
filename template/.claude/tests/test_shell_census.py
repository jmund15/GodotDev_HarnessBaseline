#!/usr/bin/env python3
"""Cases for hooks/shell_census.py: the planted 60-min shell is reported (the guard bites), a
5-min shell and a non-claude process are not, a bash -c chain collapses to one root, a sidecar
with a stale -P stream is flagged, and an empty process list renders nothing."""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import shell_census  # noqa: E402

WRAP = ("\"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"source /c/Users/<user>/.claude/shell-snapshots/snapshot-bash-1.sh "
        "2>/dev/null || true && eval '%s' < /dev/null && pwd -P >| /c/Users/<user>/AppData/Local/Temp/claude-1-cwd\"")

fails = 0


def case(name, ok, detail=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail and not ok else ""))
    if not ok:
        fails += 1


# 1. planted violation: a 60-minute claude shell is reported, with the eval body as its tail
procs = [{"pid": 10, "ppid": 1, "age": 60, "cmd": WRAP % "tail -f some.log"}]
rows = shell_census.select(procs)
case("planted 60-min shell reported", len(rows) == 1 and rows[0]["pid"] == 10 and rows[0]["tail"] == "tail -f some.log",
     repr(rows))
case("render names pid, age and the kill command", "pid 10" in shell_census.render(rows) and "1h00m" in shell_census.render(rows)
     and "taskkill" in shell_census.render(rows))

# 2. clean: young shell and a foreign process are ignored
procs = [{"pid": 11, "ppid": 1, "age": 5, "cmd": WRAP % "sleep 1"},
         {"pid": 12, "ppid": 1, "age": 900, "cmd": "python.exe C:/x/server.py"}]
case("young shell and non-claude process ignored", shell_census.select(procs) == [])

# 3. a bash -c chain (wrapper -> inner bash -> inner bash) collapses to its root pid
procs = [{"pid": 20, "ppid": 1, "age": 70, "cmd": WRAP % "bash roster.sh"},
         {"pid": 21, "ppid": 20, "age": 70, "cmd": WRAP % "bash roster.sh"},
         {"pid": 22, "ppid": 21, "age": 70, "cmd": WRAP % "bash roster.sh"}]
rows = shell_census.select(procs)
case("chain collapses to one root", [r["pid"] for r in rows] == [20], repr(rows))

# 4. sidecar launcher with a stale -P stream is flagged STALE
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "run.progress.jsonl").replace("\\", "/")
    open(p, "w").close()
    old = time.time() - 30 * 60
    os.utime(p, (old, old))
    procs = [{"pid": 30, "ppid": 1, "age": 50, "cmd": "bash.exe .claude/scripts/codex_proxy_sidecar.sh -m luna -P \"%s\" -l x" % p}]
    rows = shell_census.select(procs)
    txt = shell_census.render(rows)
    case("sidecar with a 30-min-idle -P stream flagged STALE", len(rows) == 1 and rows[0]["stream"] is not None
         and rows[0]["stream"] >= 29 and "STALE" in txt, txt)

# 5. empty input renders nothing (the hook is silent, not "clean")
case("empty process list renders empty string", shell_census.render(shell_census.select([])) == "")

sys.exit(1 if fails else 0)
