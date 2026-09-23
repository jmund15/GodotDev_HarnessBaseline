#!/usr/bin/env python3
"""UserPromptSubmit hook — census of long-running shells, with ownership (advisory).

A backgrounded Bash call that hangs produces nothing: no notification, no error, no line in any
log. Measured 2026-09-08: three shells over an hour and one over two days, every one found by the
owner. This hook finds every claude-spawned shell (`shell-snapshots` in its command line) or
sidecar launcher older than AGE_MIN minutes.

OWNERSHIP. Not every long-running shell belongs to this session — one machine commonly runs
several Claude Code sessions at once, each spawning its own shells that match the same
`shell-snapshots`/`_sidecar.sh` selection, and a wrong ownership label invites a wrong kill
decision (`auto-memory/gotcha_concurrent_session_hazards.md`). Each selected root is classified by walking its CIM parent
chain to the nearest `claude.exe`/`node.exe` ancestor and comparing that pid to this session's
root: "own" when they match, "other" when both resolve and differ, "owner unknown" when either
cannot be resolved (a missing table entry or the walk-limit). Unresolved ancestry never defaults
to "own" or to "other" — see `session_root_pid`. This session's shells and unknown-owner shells
render in full; other sessions' shells collapse to one summary line, because detail about a shell
this session cannot kill or explain is not actionable, only costly.

EMISSION. Printing the same content every prompt after the model has already decided what to do
about it burns context for no new information. A key (own/unknown pids with their STALE flag and
owner label, plus the other-session count) is stored per session via `_hook_state`; the hook is silent when the key
repeats, and re-arms once after a compaction (`transcript_backup.py` calls
`_hook_state.clear_compaction_keys`), since a compacted session lost the earlier delivery from its
context.

Rule home: CLAUDE.core.md §Shell Discipline ("Watch background jobs through their completion/monitor
contract"); sidecar stalls
self-kill via lib/sidecar_common.sh `sc_run_watched` (-Z / SIDECAR_STALL_SEC).

Fail-open: any error exits 0 with no output.

One CIM query per prompt (~1 s), returning pid/ppid/name for every process (needed to walk
ancestry) and command line/age only for the bash/python candidates selection needs. The result is
cached machine-wide in .claude/.cache/shell-census.json for CACHE_SEC — the cache holds the raw
process list, not the rendered text, because ownership (and therefore the render) is per session
and a shared rendered cache would show one session another session's "own" label. Another hook
process took a cached list, so this process's own pid is not in it: `resolve_own_root` stores the
session root per session from a fresh snapshot and reuses it only while the cached table still
lists that pid under a session-process name; otherwise the hook takes a fresh snapshot.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hook_state import (  # noqa: E402
    fire_once_since_compaction,
    read_json_salvage,
    state_path,
    update_json_locked,
)

AGE_MIN = 45
CACHE_SEC = 120
STALE_STREAM_MIN = 10

# The nearest ancestor name that means "this is a Claude Code session root". Mirrors
# activity_registry.py's SESSION_PROC_NAMES — same identity question, same answer.
SESSION_PROC_NAMES = ("node.exe", "node", "claude.exe", "claude")
_WALK_LIMIT = 12
_OWN_ROOT_KEY = "shell_census_own_root"

_PS = (
    "$now=Get-Date; Get-CimInstance Win32_Process | ForEach-Object { "
    "$isCand = $_.Name -match '^(bash|python)'; "
    "$age = if ($isCand) { [int]($now - $_.CreationDate).TotalMinutes } else { $null }; "
    "$cmd = if ($isCand) { $_.CommandLine } else { $null }; "
    "[pscustomobject]@{ pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; age=$age; cmd=$cmd } "
    "} | ConvertTo-Json -Compress"
)


def list_processes():
    """-> [{pid, ppid, name, age (min|None), cmd (str|None)}] via one CIM query; [] on failure.

    age/cmd are populated only for bash/python-named processes (the candidate set); every other
    process still carries pid/ppid/name so `session_root_pid` can walk through it."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS], capture_output=True,
                             text=True, timeout=20).stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def _tail(cmd, n=110):
    cmd = re.sub(r"\s+", " ", cmd or "")
    # the claude wrapper prefix (snapshot source + exports) carries no information; keep the eval body
    i = cmd.find("eval '")
    if i > 0:
        cmd = cmd[i + 6:]
    cmd = re.sub(r"' < /dev/null && pwd -P >\|.*$", "", cmd)
    cmd = re.sub(r"' && pwd -P >\|.*$", "", cmd)
    return cmd[-n:] if len(cmd) > n else cmd


def build_table(procs):
    """{pid: {"ppid": int|None, "name": str}} for every process in the snapshot — the superset
    `session_root_pid` walks, independent of `select`'s shell-snapshot candidate filter."""
    table = {}
    for p in procs:
        try:
            pid = int(p.get("pid"))
        except (TypeError, ValueError, AttributeError):
            continue
        table[pid] = {"ppid": p.get("ppid"), "name": p.get("name") or ""}
    return table


def session_root_pid(table, pid, limit=_WALK_LIMIT):
    """Walk `table`'s ppid chain from `pid` to the nearest claude.exe/node.exe ancestor.

    Returns that ancestor's pid, or None when the chain hits a missing table entry or an
    unparseable ppid before reaching one — "cannot prove ownership", never guessed. Callers must
    treat None as "owner unknown", not as a match against anything (including another None)."""
    if pid is None:
        return None
    cur = pid
    for _ in range(limit):
        row = table.get(cur)
        if not row:
            return None
        try:
            ppid = int(row.get("ppid"))
        except (TypeError, ValueError):
            return None
        if ppid <= 0 or ppid == cur:
            return None
        prow = table.get(ppid)
        if not prow:
            return None
        if (prow.get("name") or "").lower() in SESSION_PROC_NAMES:
            return ppid
        cur = ppid
    return None


def resolve_own_root(session_id, procs, own_pid, fresh):
    """This session's Claude Code root pid, or None when it cannot be proven.

    A FRESH snapshot contains `own_pid`, so the walk resolves the root and stores it per session.
    A CACHED snapshot was taken by another hook process and does not contain `own_pid`; the stored
    root is reused only while that table still lists it under a session-process name, so a restarted
    session never inherits a dead pid."""
    table = build_table(procs)
    path = state_path(session_id)
    state = read_json_salvage(path)
    if fresh:
        root = session_root_pid(table, own_pid)
        if root is not None and state.get(_OWN_ROOT_KEY) != root:
            update_json_locked(path, lambda current: current.__setitem__(_OWN_ROOT_KEY, root))
        return root
    root = state.get(_OWN_ROOT_KEY)
    row = table.get(root) if isinstance(root, int) and not isinstance(root, bool) else None
    if not row or (row.get("name") or "").lower() not in SESSION_PROC_NAMES:
        return None
    return root


def select(procs, own_pid=None, age_min=AGE_MIN, now=None, own_root=None):
    """Long-running roots: claude-spawned shells and sidecar launchers older than age_min whose
    parent is not itself selected (a `bash -c` chain shows 2-3 pids for one shell), each labeled
    with an "owner" of "own" / "other" / "unknown" relative to this session's Claude Code root:
    `own_root` when the caller already resolved it, else the walk from `own_pid`."""
    table = build_table(procs)
    if own_root is None and own_pid is not None:
        own_root = session_root_pid(table, own_pid)

    cand = {}
    for p in procs:
        cmd = p.get("cmd")
        if cmd is None:
            continue
        if int(p.get("age") or 0) < age_min:
            continue
        if "shell-snapshots" in cmd or "_sidecar.sh" in cmd:
            cand[int(p["pid"])] = p
    roots = [p for pid, p in cand.items() if int(p.get("ppid") or 0) not in cand]
    rows = []
    for p in sorted(roots, key=lambda x: -int(x.get("age") or 0)):
        pid = int(p["pid"])
        cmd = p.get("cmd") or ""
        root_pid = session_root_pid(table, pid)
        if root_pid is None or own_root is None:
            owner = "unknown"
        elif root_pid == own_root:
            owner = "own"
        else:
            owner = "other"
        row = {"pid": pid, "age": int(p.get("age") or 0), "tail": _tail(cmd), "stream": None, "owner": owner}
        m = re.search(r"-P\s+\"?([^\s\"]+\.jsonl)", cmd)
        if m:
            path = m.group(1)
            if path.startswith("/c/"):
                path = "C:/" + path[3:]
            try:
                idle = int(((now or time.time()) - os.path.getmtime(path)) / 60)
                row["stream"] = idle
            except OSError:
                row["stream"] = None
        rows.append(row)
    return rows


def render(rows):
    """Render this session's rows and owner-unknown rows in full; collapse other sessions' rows
    to one line."""
    full = [r for r in rows if r.get("owner") != "other"]
    other = [r for r in rows if r.get("owner") == "other"]
    if not full and not other:
        return ""
    lines = []
    if full:
        lines.append("[shells] %d long-running shell(s) — hung or legitimately long? decide now, "
                      "never wait on one nobody watches. Stop one only through the reaper "
                      "(`python3 .claude/tools/reap.py --help`; --dry-run first); leave an owner-unknown shell "
                      "running. The tails below are TRUNCATED." % len(full))
        for r in full:
            extra = ""
            if r["stream"] is not None:
                extra = " | -P stream idle %d min%s" % (r["stream"], " (STALE)" if r["stream"] >= STALE_STREAM_MIN else "")
            label = " (owner unknown)" if r.get("owner") == "unknown" else ""
            lines.append("  pid %d  age %dh%02dm%s%s | %s" % (r["pid"], r["age"] // 60, r["age"] % 60, label, extra, r["tail"]))
    if other:
        lines.append("[shells] %d long-running shell(s) belong to other Claude sessions — their owners "
                      "decide; leave them running." % len(other))
    return "\n".join(lines)


def emission_key(rows):
    """Sorted [pid, stale, owner] for own/unknown rows, plus the other-session count. Comparing
    this against the last stored key is what makes the hook silent once a prompt has already told
    the model about an unchanged set of shells; the owner is in the key because it changes the
    render ("(owner unknown)")."""
    full = sorted(
        [r["pid"], bool(r["stream"] is not None and r["stream"] >= STALE_STREAM_MIN), r.get("owner")]
        for r in rows if r.get("owner") != "other"
    )
    other = sum(1 for r in rows if r.get("owner") == "other")
    return {"rows": full, "other": other}


def should_emit(session_id, key):
    """True the first time this session sees `key`, or the first ask since a compaction dropped
    the earlier delivery from context. False (silent) when `key` repeats."""
    since_compaction = fire_once_since_compaction(session_id, "shell_census")

    def claim(state):
        if not since_compaction and key == state.get("shell_census_last_key"):
            return False
        state["shell_census_last_key"] = key
        return True

    written, emit = update_json_locked(state_path(session_id), claim)
    return emit if written else True


def run(payload, procs, now=None, own_pid=None, own_root=None):
    """Core logic, injectable for tests: no live CIM query, no stdin. Returns the text to print,
    or "" when there is nothing to show or the dedupe key repeats."""
    session_id = (payload or {}).get("session_id") or "unknown"
    if own_pid is None:
        own_pid = os.getpid()
    rows = select(procs, own_pid=own_pid, now=now, own_root=own_root)
    text = render(rows)
    if not text:
        return ""
    if not should_emit(session_id, emission_key(rows)):
        return ""
    return text


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cache_dir = os.path.join(root, ".claude", ".cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, "shell-census.json")
        now = time.time()
        session_id = (payload or {}).get("session_id") or "unknown"
        procs = None
        try:
            with open(cache, encoding="utf-8") as fh:
                c = json.load(fh)
            if now - float(c.get("at", 0)) < CACHE_SEC:
                procs = c.get("procs")
        except Exception:
            procs = None
        own_root = None
        if procs is not None:
            own_root = resolve_own_root(session_id, procs, os.getpid(), fresh=False)
            if own_root is None:
                procs = None  # a cached list cannot prove this session's root; take a fresh one
        if procs is None:
            procs = list_processes()
            try:
                tmp = cache + ".%d.tmp" % os.getpid()
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"at": now, "procs": procs}, fh)
                os.replace(tmp, cache)
            except Exception:
                pass
            own_root = resolve_own_root(session_id, procs, os.getpid(), fresh=True)
        text = run(payload, procs, now=now, own_root=own_root)
        if text:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(text)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
