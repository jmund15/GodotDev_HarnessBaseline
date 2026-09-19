#!/usr/bin/env python3
"""Cases for hooks/shell_census.py: the planted 60-min shell is reported (the guard bites), a
5-min shell and a non-claude process are not, a bash -c chain collapses to one root, a sidecar
with a stale -P stream is flagged, an empty process list renders nothing, ownership classifies
own/other/unknown roots correctly, and per-session emission dedupes on an unchanged key while
re-arming after a compaction and after a malformed stdin payload."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))

fails = 0


def case(name, ok, detail=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + (" — " + detail if detail and not ok else ""))
    if not ok:
        fails += 1


# Redirect shared hook state before importing, so a proof run never touches the real
# ~/.claude/.routing_state — _hook_state.state_dir() reads this env var live, so it only
# needs to be set before shell_census (which imports _hook_state) is imported.
_state_dir = tempfile.mkdtemp(prefix="shell_census_state_")
os.environ["HARNESS_HOOK_STATE_DIR"] = _state_dir

import shell_census  # noqa: E402

WRAP = ("\"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"source /c/Users/x/.claude/shell-snapshots/snapshot-bash-1.sh "
        "2>/dev/null || true && eval '%s' < /dev/null && pwd -P >| /c/Users/x/AppData/Local/Temp/claude-1-cwd\"")

# 1. planted violation: a 60-minute claude shell is reported, with the eval body as its tail
procs = [{"pid": 10, "ppid": 1, "age": 60, "cmd": WRAP % "tail -f some.log"}]
rows = shell_census.select(procs)
case("planted 60-min shell reported", len(rows) == 1 and rows[0]["pid"] == 10 and rows[0]["tail"] == "tail -f some.log",
     repr(rows))
case("render names pid, age and the reaper route (never a raw taskkill)", "pid 10" in shell_census.render(rows) and "1h00m" in shell_census.render(rows)
     and "reap.py" in shell_census.render(rows) and "taskkill" not in shell_census.render(rows)
     and "owner-unknown shell" in shell_census.render(rows))
case("header never claims \"this harness spawned\"", "this harness spawned" not in shell_census.render(rows),
     shell_census.render(rows))
case("unresolved ancestry renders as owner unknown, not this harness",
     rows[0]["owner"] == "unknown" and "(owner unknown)" in shell_census.render(rows))

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


def ancestry(own_root_pid=100):
    """Full-table entries for a synthetic own-hook process ancestry: pid 999 (the hook itself,
    what select()'s own_pid parameter names) -> ppid 500 (an intermediate, non-session-named
    process) -> ppid `own_root_pid`, itself named node.exe — the session root `session_root_pid`
    is looking for."""
    return [
        {"pid": 999, "ppid": 500, "name": "python.exe", "cmd": None, "age": None},
        {"pid": 500, "ppid": own_root_pid, "name": "bash.exe", "cmd": None, "age": None},
        {"pid": own_root_pid, "ppid": 1, "name": "node.exe", "cmd": None, "age": None},
    ]


# 6. own ownership: a shell whose chain reaches the same claude.exe/node.exe root as the hook's
# own process is "own", not "other" and not "unknown"
procs = ancestry() + [{"pid": 40, "ppid": 100, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f own.log"}]
rows = shell_census.select(procs, own_pid=999)
case("shell sharing the hook's own root is classified own",
     len(rows) == 1 and rows[0]["owner"] == "own", repr(rows))
case("own row renders in full without owner-unknown label",
     "pid 40" in shell_census.render(rows) and "(owner unknown)" not in shell_census.render(rows))

# 7. other-session ownership: a shell whose chain reaches a DIFFERENT claude.exe root collapses
# to one summary line, with no pid in it
procs = ancestry() + [
    {"pid": 200, "ppid": 1, "name": "node.exe", "cmd": None, "age": None},  # a peer session's root
    {"pid": 41, "ppid": 200, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f peer.log"},
]
rows = shell_census.select(procs, own_pid=999)
txt = shell_census.render(rows)
case("shell reaching a different root is classified other", rows[0]["owner"] == "other", repr(rows))
case("other-session shells collapse to one line with no pid",
     "belong to other Claude sessions" in txt and "pid 41" not in txt, txt)

# 8. unknown ancestry: a shell whose ppid chain breaks before reaching a claude.exe/node.exe
# ancestor renders in full labeled "owner unknown", not "own" and not silently dropped
procs = ancestry() + [{"pid": 42, "ppid": 9999, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f orphan.log"}]
rows = shell_census.select(procs, own_pid=999)
txt = shell_census.render(rows)
case("broken ancestry chain is owner unknown, not own",
     rows[0]["owner"] == "unknown" and "pid 42" in txt and "(owner unknown)" in txt, txt)

# --- emission / dedupe (drives shell_census.run(), the per-session path main() calls) ---

SESSION = "probe-dedupe-session"


def _clear_session_state():
    try:
        os.remove(shell_census.state_path(SESSION))
    except OSError:
        pass


# 9. same set twice in one session: second call is silent
_clear_session_state()
procs = ancestry() + [{"pid": 40, "ppid": 100, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f own.log"}]
first = shell_census.run({"session_id": SESSION}, procs, own_pid=999)
second = shell_census.run({"session_id": SESSION}, procs, own_pid=999)
case("first call of a new set emits", bool(first), repr(first))
case("identical second call is silent", second == "", repr(second))

# 10. a new own shell (an additional pid) changes the key and re-emits
procs_plus = procs + [{"pid": 43, "ppid": 100, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f second.log"}]
third = shell_census.run({"session_id": SESSION}, procs_plus, own_pid=999)
case("a new own shell re-emits", bool(third) and "pid 43" in third, repr(third))

# 11. the same shell turning STALE (its -P stream crossing the threshold) changes the key
_clear_session_state()
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "run.progress.jsonl").replace("\\", "/")
    open(p, "w").close()
    fresh = time.time() - 1 * 60
    os.utime(p, (fresh, fresh))
    sidecar_procs = ancestry() + [{"pid": 50, "ppid": 100, "age": 60, "name": "bash.exe",
                                    "cmd": "bash.exe .claude/scripts/codex_proxy_sidecar.sh -m luna -P \"%s\" -l x" % p}]
    now0 = time.time()
    fresh_emit = shell_census.run({"session_id": SESSION}, sidecar_procs, now=now0, own_pid=999)
    stale_emit = shell_census.run({"session_id": SESSION}, sidecar_procs, now=now0 + 11 * 60, own_pid=999)
    case("fresh stream emits once", bool(fresh_emit), repr(fresh_emit))
    case("stream turning STALE re-emits", bool(stale_emit) and "STALE" in stale_emit, repr(stale_emit))
    unchanged = shell_census.run({"session_id": SESSION}, sidecar_procs, now=now0 + 11 * 60, own_pid=999)
    case("unchanged STALE state re-asked is silent", unchanged == "", repr(unchanged))

# 12. only other-session shells: the one-line count, no pids, still counted in the emission key
_clear_session_state()
other_only = ancestry() + [
    {"pid": 200, "ppid": 1, "name": "node.exe", "cmd": None, "age": None},
    {"pid": 41, "ppid": 200, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f peer.log"},
]
peer_first = shell_census.run({"session_id": SESSION}, other_only, own_pid=999)
peer_second = shell_census.run({"session_id": SESSION}, other_only, own_pid=999)
case("other-session-only set emits the one-line count", bool(peer_first)
     and "belong to other Claude sessions" in peer_first and "pid" not in peer_first.split("\n")[0], repr(peer_first))
case("identical other-session-only set re-asked is silent", peer_second == "", repr(peer_second))

# 13. after clear_compaction_keys, the identical set emits again
_clear_session_state()
resumed = shell_census.run({"session_id": SESSION}, procs, own_pid=999)
silent_again = shell_census.run({"session_id": SESSION}, procs, own_pid=999)
import _hook_state  # noqa: E402
_hook_state.clear_compaction_keys(SESSION)
post_compaction = shell_census.run({"session_id": SESSION}, procs, own_pid=999)
case("set up: first emits, second silent", bool(resumed) and silent_again == "")
case("identical set re-emits once after a compaction", bool(post_compaction), repr(post_compaction))

# 13b. cached snapshot: main() reuses a process list another prompt cached, so the hook's own pid
# is absent from it. Ownership must come from the session's stored root, and an unresolvable own
# root must never label a shell as another session's.
_clear_session_state()
cached = [{"pid": 100, "ppid": 1, "name": "node.exe", "cmd": None, "age": None},
          {"pid": 40, "ppid": 100, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f own.log"}]
rows = shell_census.select(cached, own_pid=4242)
case("own pid missing from a cached snapshot never claims another session",
     rows[0]["owner"] == "unknown", repr(rows))
fresh_root = shell_census.resolve_own_root(SESSION, ancestry(), own_pid=999, fresh=True)
case("a fresh snapshot resolves and stores the session root", fresh_root == 100, repr(fresh_root))
stored = shell_census.resolve_own_root(SESSION, cached, own_pid=4242, fresh=False)
case("a cached snapshot reuses the stored root", stored == 100, repr(stored))
rows = shell_census.select(cached, own_pid=4242, own_root=stored)
case("with the stored root the cached shell is own", rows[0]["owner"] == "own", repr(rows))
gone = [{"pid": 40, "ppid": 100, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f own.log"}]
case("a stored root absent from the cached table is not trusted",
     shell_census.resolve_own_root(SESSION, gone, own_pid=4242, fresh=False) is None)
via_run = shell_census.run({"session_id": SESSION + "-run"}, cached, own_pid=4242, own_root=100)
case("run() passes the stored root through to the render", bool(via_run) and "pid 40" in via_run
     and "(owner unknown)" not in via_run, repr(via_run))

# 13c. the same pid changing owner (own -> unknown) changes what renders, so it must re-emit
_clear_session_state()
owned = shell_census.run({"session_id": SESSION}, cached, own_pid=4242, own_root=100)
unowned = shell_census.run({"session_id": SESSION}, cached, own_pid=4242, own_root=None)
case("set up: the owned render emits", bool(owned) and "(owner unknown)" not in owned, repr(owned))
case("the same pid turning owner-unknown re-emits", bool(unowned) and "(owner unknown)" in unowned, repr(unowned))

# 14. malformed stdin: main() exits 0 with no traceback
import subprocess  # noqa: E402
hook_path = os.path.join(HERE, "..", "hooks", "shell_census.py")
env = dict(os.environ)
env["HARNESS_HOOK_STATE_DIR"] = _state_dir
proc = subprocess.run([sys.executable, hook_path], input="not json{{{", capture_output=True, text=True,
                       env=env, timeout=30)
case("malformed stdin exits 0", proc.returncode == 0, "rc=%r stderr=%r" % (proc.returncode, proc.stderr))
case("malformed stdin produces no traceback", "Traceback" not in proc.stderr, proc.stderr)

# 15. no CLAUDE_PROJECT_DIR: the cache lands under this checkout's .claude/.cache, never under cwd
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as cwd_tmp:
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["HARNESS_HOOK_STATE_DIR"] = _state_dir
    subprocess.run([sys.executable, os.path.abspath(hook_path)], input="{}", capture_output=True, text=True,
                   env=env, cwd=cwd_tmp, timeout=60)
    case("without CLAUDE_PROJECT_DIR the cache is not written under cwd",
         not os.path.exists(os.path.join(cwd_tmp, ".claude", ".cache", "shell-census.json")))

sys.exit(1 if fails else 0)
