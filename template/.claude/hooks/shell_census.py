#!/usr/bin/env python3
"""UserPromptSubmit hook — census of long-running shells this harness spawned (advisory).

A backgrounded Bash call that hangs produces nothing: no notification, no error, no line in any
log. Measured 2026-09-08: three shells over an hour and one over two days, every one found by the
owner. This hook prints, on every prompt, each claude-spawned shell (`shell-snapshots` in its
command line) or sidecar launcher older than AGE_MIN minutes, with its age, the tail of its
command, the Windows pid to kill it with, and — for a sidecar with a `-P` stream — how stale that
stream is. Silent when nothing qualifies. Fail-open: any error exits 0 with no output.

Rule home: CLAUDE.md §Shell Discipline (never wait on a shell nobody watches); sidecar stalls
self-kill via lib/sidecar_common.sh `sc_run_watched` (-Z / SIDECAR_STALL_SEC).

One CIM query per prompt (~1 s); cached in .claude/.cache/shell-census.json for CACHE_SEC.
"""
import json
import os
import re
import subprocess
import sys
import time

AGE_MIN = 45
CACHE_SEC = 120
STALE_STREAM_MIN = 10

_PS = (
    "$now=Get-Date; Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(bash|python)' } | "
    "ForEach-Object { [pscustomobject]@{ pid=$_.ProcessId; ppid=$_.ParentProcessId; "
    "age=[int]($now - $_.CreationDate).TotalMinutes; cmd=$_.CommandLine } } | ConvertTo-Json -Compress"
)


def list_processes():
    """-> [{pid, ppid, age (min), cmd}] via one CIM query; [] on any failure."""
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


def select(procs, age_min=AGE_MIN, now=None):
    """Long-running roots: claude-spawned shells and sidecar launchers older than age_min whose
    parent is not itself selected (a `bash -c` chain shows 2-3 pids for one shell)."""
    cand = {}
    for p in procs:
        cmd = p.get("cmd") or ""
        if int(p.get("age") or 0) < age_min:
            continue
        if "shell-snapshots" in cmd or "_sidecar.sh" in cmd:
            cand[int(p["pid"])] = p
    roots = [p for pid, p in cand.items() if int(p.get("ppid") or 0) not in cand]
    rows = []
    for p in sorted(roots, key=lambda x: -int(x.get("age") or 0)):
        cmd = p.get("cmd") or ""
        row = {"pid": int(p["pid"]), "age": int(p.get("age") or 0), "tail": _tail(cmd), "stream": None}
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
    if not rows:
        return ""
    lines = ["[shells] %d long-running shell(s) this harness spawned — hung or legitimately long? decide now, "
             "never wait on one nobody watches. The tails below are TRUNCATED — resolve a pid "
             "before killing it (`kill_guard.py` denies a harness-critical target and prints what "
             "it really is): MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F" % len(rows)]
    for r in rows:
        extra = ""
        if r["stream"] is not None:
            extra = " | -P stream idle %d min%s" % (r["stream"], " (STALE)" if r["stream"] >= STALE_STREAM_MIN else "")
        lines.append("  pid %d  age %dh%02dm%s | %s" % (r["pid"], r["age"] // 60, r["age"] % 60, extra, r["tail"]))
    return "\n".join(lines)


def main():
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        cache_dir = os.path.join(root, ".claude", ".cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, "shell-census.json")
        now = time.time()
        text = None
        try:
            with open(cache, encoding="utf-8") as fh:
                c = json.load(fh)
            if now - float(c.get("at", 0)) < CACHE_SEC:
                text = c.get("text", "")
        except Exception:
            text = None
        if text is None:
            text = render(select(list_processes(), now=now))
            try:
                tmp = cache + ".%d.tmp" % os.getpid()
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"at": now, "text": text}, fh)
                os.replace(tmp, cache)
            except Exception:
                pass
        if text:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(text)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
