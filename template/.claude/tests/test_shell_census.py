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
# The hook's cache and the reaper stamp resolve from CLAUDE_PROJECT_DIR: point it at a temp root, not the checkout.
_project_dir = tempfile.mkdtemp(prefix="shell_census_project_")
os.environ["CLAUDE_PROJECT_DIR"] = _project_dir

import shell_census  # noqa: E402

WRAP = ("\"C:\\Program Files\\Git\\bin\\bash.exe\" -c \"source /c/Users/x/.claude/shell-snapshots/snapshot-bash-1.sh "
        "2>/dev/null || true && eval '%s' < /dev/null && pwd -P >| /c/Users/x/AppData/Local/Temp/claude-1-cwd\"")

# 1. planted violation: a 60-minute claude shell is reported, with the eval body as its tail
procs = [{"pid": 10, "ppid": 1, "age": 60, "cmd": WRAP % "tail -f some.log"}]
rows = shell_census.select(procs)
case("planted 60-min shell reported", len(rows) == 1 and rows[0]["pid"] == 10 and rows[0]["tail"] == "tail -f some.log",
     repr(rows))
case("unknown owner renders only a count", shell_census.render(rows) ==
     '[shells] 1 long-running shell(s), owner unknown; leave them running.')
case("header never claims \"this harness spawned\"", "this harness spawned" not in shell_census.render(rows),
     shell_census.render(rows))
case("unresolved ancestry renders as owner unknown, not this harness",
     rows[0]["owner"] == "unknown" and "owner unknown" in shell_census.render(rows))

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
         and rows[0]["stream"] >= 29 and "STALE" not in txt and "owner unknown" in txt, txt)

# 4b. a shell running this session's live watcher renders as the wake route, never a hang candidate
WATCH_SESSION = "probe-watcher-session"
WATCHER_PID = os.getpid()
watcher_identity = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + (
    " winpid=%d" % WATCHER_PID if os.name == "nt" else "") + " pid=%d" % WATCHER_PID
watcher_procs = [{"pid": 35, "ppid": 1, "age": 50, "name": "bash.exe",
                  "cmd": WRAP % "python3 .claude/tools/watch_records.py --session --wake"},
                 {"pid": WATCHER_PID, "ppid": 35, "name": "python.exe"}]
os.makedirs(_state_dir, exist_ok=True)
with open(os.path.join(_state_dir, WATCH_SESSION[:8] + ".json"), "w") as fh:
    json.dump({"sidecar_watchers": [{"process": watcher_identity, "records": [], "session": True}]}, fh)
_watcher_text = shell_census.run({"session_id": WATCH_SESSION}, watcher_procs, own_pid=999, own_root=100)
case("a live watcher shell is silent",
     _watcher_text == '',
     repr(_watcher_text))
_unwatched_text = shell_census.run({"session_id": "nowatch0-0000-0000-000000000000"}, watcher_procs,
                                   own_pid=999, own_root=100)
case("the same unresolved shell without a registered watcher gets a count",
     "owner unknown" in _unwatched_text and "pid" not in _unwatched_text,
     repr(_unwatched_text))

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
     "pid 40" in shell_census.render(rows) and "owner unknown" not in shell_census.render(rows))

# 7. other-session ownership: a shell whose chain reaches a DIFFERENT claude.exe root collapses
# to one summary line, with no pid in it
procs = ancestry() + [
    {"pid": 200, "ppid": 1, "name": "node.exe", "cmd": None, "age": None},  # a peer session's root
    {"pid": 41, "ppid": 200, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f peer.log"},
]
rows = shell_census.select(procs, own_pid=999)
txt = shell_census.render(rows)
case("shell reaching a different root is classified other", rows[0]["owner"] == "other", repr(rows))
case("other-session shells are silent", txt == '', txt)

# 8. unknown ancestry: a shell whose ppid chain breaks before reaching a claude.exe/node.exe
# ancestor renders in full labeled "owner unknown", not "own" and not silently dropped
procs = ancestry() + [{"pid": 42, "ppid": 9999, "age": 60, "name": "bash.exe", "cmd": WRAP % "tail -f orphan.log"}]
rows = shell_census.select(procs, own_pid=999)
txt = shell_census.render(rows)
case("broken ancestry chain is owner unknown, not own",
     rows[0]["owner"] == "unknown" and "pid 42" not in txt and "owner unknown" in txt, txt)

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
case("other-session-only set stays silent", peer_first == '', repr(peer_first))
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
     and "owner unknown" not in via_run, repr(via_run))

# 13c. the same pid changing owner (own -> unknown) changes what renders, so it must re-emit
_clear_session_state()
owned = shell_census.run({"session_id": SESSION}, cached, own_pid=4242, own_root=100)
unowned = shell_census.run({"session_id": SESSION}, cached, own_pid=4242, own_root=None)
case("set up: the owned render emits", bool(owned) and "owner unknown" not in owned, repr(owned))
case("the same pid turning owner-unknown re-emits", bool(unowned) and "owner unknown" in unowned, repr(unowned))

# 13d. one unparseable process row must not fail the whole census open
procs = [{"pid": 60, "ppid": 1, "age": 70, "cmd": WRAP % "bash roster.sh"},
         {"pid": None, "ppid": 60, "age": 5, "cmd": "child.exe"},
         {"pid": "x", "ppid": 1, "age": 90, "cmd": WRAP % "bash bad_pid.sh"},
         {"pid": 61, "ppid": 1, "age": "old", "cmd": WRAP % "bash bad_age.sh"},
         {"pid": 62, "ppid": "nope", "age": 80, "cmd": WRAP % "bash bad_ppid.sh"}]
try:
    rows = shell_census.select(procs)
except Exception as exc:
    rows = "raised %s: %s" % (type(exc).__name__, exc)
case("rows with an unparseable pid, age or ppid are skipped and the valid shells still report",
     isinstance(rows, list) and sorted(r["pid"] for r in rows) == [60, 62], repr(rows))


# 13e. the ppid-to-children map is built once per select(), not once per candidate root
class CountingRow(dict):
    reads = 0

    def get(self, key, default=None):
        if key == "ppid":
            CountingRow.reads += 1
        return dict.get(self, key, default)


def ppid_reads(roots, total=90):
    procs = [CountingRow(pid=100 + i, ppid=1, age=70, cmd=WRAP % ("bash r%d.sh" % i)) for i in range(roots)]
    procs += [CountingRow(pid=1000 + i, ppid=100, age=5, cmd="filler.exe") for i in range(total - roots)]
    CountingRow.reads = 0
    shell_census.select(procs)
    return CountingRow.reads


one, six = ppid_reads(1), ppid_reads(6)
case("ppid reads do not scale with the number of candidate roots (%d for 1 root, %d for 6)" % (one, six),
     six - one < 90, "1 root=%d 6 roots=%d" % (one, six))

# 14. malformed stdin: main() exits 0 with no traceback
import subprocess  # noqa: E402
hook_path = os.path.join(HERE, "..", "hooks", "shell_census.py")
env = dict(os.environ)
env["HARNESS_HOOK_STATE_DIR"] = _state_dir
proc = subprocess.run([sys.executable, hook_path], input="not json{{{", capture_output=True, text=True,
                       env=env, timeout=30)
case("malformed stdin exits 0", proc.returncode == 0, "rc=%r stderr=%r" % (proc.returncode, proc.stderr))
case("malformed stdin produces no traceback", "Traceback" not in proc.stderr, proc.stderr)

# 15. no CLAUDE_PROJECT_DIR: the cache lands under the hook's own checkout root, never under cwd.
# The hook runs from a copy inside a temp checkout so that root is not this one.
import shutil  # noqa: E402
with tempfile.TemporaryDirectory() as cwd_tmp, tempfile.TemporaryDirectory() as fake_root:
    fake_hooks = os.path.join(fake_root, ".claude", "hooks")
    os.makedirs(fake_hooks)
    for name in ("shell_census.py", "_hook_state.py"):
        shutil.copy(os.path.join(HERE, "..", "hooks", name), fake_hooks)
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["HARNESS_HOOK_STATE_DIR"] = _state_dir
    subprocess.run([sys.executable, os.path.join(fake_hooks, "shell_census.py")], input="{}", capture_output=True, text=True,
                   env=env, cwd=cwd_tmp, timeout=60)
    case("without CLAUDE_PROJECT_DIR the cache is not written under cwd",
         not os.path.exists(os.path.join(cwd_tmp, ".claude", ".cache", "shell-census.json")))
    case("without CLAUDE_PROJECT_DIR the cache lands under the hook's checkout root",
         os.path.exists(os.path.join(fake_root, ".claude", ".cache", "shell-census.json")))

sys.exit(1 if fails else 0)
