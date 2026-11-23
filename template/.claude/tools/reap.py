#!/usr/bin/env python3
"""reap.py — stop a process this session started or the owner named, on the facts the tool observes, on the record.

    python3 .claude/tools/reap.py <pid> [<pid> ...] [--why "<note>"] [--dry-run]

The one stop route `hooks/kill_guard.py` admits (T1): a raw kill cannot show what it kills, and a kill of pids the
call does not name is denied there (K-XA). This tool reads each target and decides on what it observes, never on a
stated reason. Whether the owner ordered a stop is the auto-mode classifier's to judge on the call, which carries the
pids and the --why note; --why is a ledger note and never a gate.

Each target, in this order:
  never      a Claude Code session host (`--session-id`, `--bg-pty-host`, a bare interactive `claude`), a shared
             MCP or language server, or a codex.exe that ChatGPT.exe runs: refused, with its children. One inside
             a target's tree is spared with its own subtree, and the rest of the tree dies.
  mine       it descends from this session's claude.exe (CLAUDE_PID), or its own environment names this session's
             CLAUDE_CODE_SESSION_ID, the proof that survives a killed wrapper: stopped.
  unreadable its environment cannot be read, so a session's claim on it cannot be ruled out: refused.
  another    it runs inside another session's host tree, or its environment names another session that a live
             host serves or that no host can be ruled out for: refused; that session stops it.
  dead       its environment names another session that no live host serves, and that session's transcript is
             in no other project: stopped, as the leftover of a dead session.
  unclaimed  its environment names no session and no session host sits above it (an app the owner launched):
             stopped.
A session is live when a live process's `<login>/sessions/<pid>.json` row names it, or a live session host names it
after --session-id or --resume. A tool shell, MCP server or console host that only carries the id is a dependent and
proves nothing: it outlives its session. A live host with no row and no id whose children carry a session id makes
every session unknown, and the target is refused.

The kill is `taskkill /PID <pid> /F` on each member of the verified tree, deepest first (never `/T`, which walks
reused OS parent pids). A child belongs to a parent only if the parent started no later than the child. Every
refusal, dry run and kill is a row in .claude/logs/reap_ledger.jsonl; a kill's row is written first, and an
unwritable ledger refuses the kill. MSYS pids (Git Bash `kill`, `ps`) map to Windows pids through `ps -W`.
Exit 0 every target stopped (or dry-run); 2 a target refused, a kill failed, or a bad call.
"""
import argparse
import datetime
import json
import os
try:
    import ctypes
    from ctypes import wintypes
except ImportError:  # pragma: no cover
    ctypes = None
import re
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
import _msys  # noqa: E402
import _hook_state  # noqa: E402
sys.path.insert(0, str(REPO / ".claude" / "tools"))
from launch_check import login_dirs  # noqa: E402  the one login list
import cli_usage  # noqa: E402

LEDGER = REPO / ".claude" / "logs" / "reap_ledger.jsonl"
ACCEPTS = "PIDS... [--why TEXT] [--dry-run]"
EXAMPLE = "python3 .claude/tools/reap.py 12345 --dry-run"
# A retired spelling names its replacement: docs and memory keep the old text.
RETIRED = {
    "--reason": "omit --reason: the tool decides on the facts it observes, and a dead session's leftover needs no flag",
    "inspect": "run `reap.py <pid> --dry-run`: it prints each target's proof or refusal and stops nothing",
    "leftovers": "name the pid: `reap.py <pid> --dry-run` shows whether it is a dead session's leftover",
    "live-launch": "the sidecar launchers it served are gone; seat.sh refuses a full seat itself",
    "end-seat": "the sidecar launchers it served are gone; a seat ends with its seat.sh",
}

# Never a target, whoever owns it.
NEVER = (
    (re.compile(r"claude(?:\.exe)?\"?\s+(?:.*\s)?--(?:session-id|bg-pty-host)\b", re.I), "a Claude Code session HOST"),
    (re.compile(r"claude(?:\.exe)?\"?\s*$", re.I), "an interactive Claude Code session HOST"),
    # Executable + server name in any argument (flags may precede the script), not a bare word: `-u ENABLE_LSP_TOOL`
    # sits in an env wrapper's cleanup.
    (re.compile(r"(?:node|python3?|pwsh|dotnet)(?:\.exe)?\"?\s+(?:\S+\s+)*?\S*(?:mcp[-_]|[-_]mcp|semantic-search|[\w.-]+-ls(?:\.|\s|$))", re.I),
     "shared INFRA (an MCP/LSP server other sessions use)"),
)
HOST_PATTERNS = tuple(pat for pat, _ in NEVER[:2])
# A headless `claude -p` is the host of its own session: its registry row names it.
SEAT_HOST = re.compile(r"claude(?:\.exe)?\"?\s+(?:.*\s)?-p(?:\s|$)", re.I)
HOST_SESSION = re.compile(r"--(?:session-id|resume)[=\s]+\"?([A-Za-z0-9_-]+)")
SID_ENV = b"CLAUDE_CODE_SESSION_ID="

# This session: its claude.exe pid, its id, and the Claude project folder its transcript sits in.
Session = namedtuple("Session", "pid sid project")


def build_parser():
    ap = cli_usage.ShapeParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.accepts, ap.example = ACCEPTS, EXAMPLE
    ap.add_argument("pids", nargs="+", type=int, help="Windows or MSYS pids")
    ap.add_argument("--why", default="", help="optional note for the ledger, recorded verbatim; never a gate")
    ap.add_argument("--dry-run", action="store_true", help="print each target's proof or refusal; stop nothing")
    ap.add_argument("--reason", help=argparse.SUPPRESS)
    return ap


# ----------------------------------------------------------------- process table
def _proc_table():
    """The POSIX process table from /proc, in process_table's row shape; {} when /proc is unreadable."""
    try:
        names = [n for n in os.listdir("/proc") if n.isdigit()]
        with open("/proc/stat", encoding="utf-8") as fh:
            boot = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
        tick = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration):
        return {}
    out = {}
    for name in names:
        pid = int(name)
        try:
            with open("/proc/%s/stat" % name, "rb") as fh:
                stat = fh.read().decode("utf-8", "replace")
            with open("/proc/%s/cmdline" % name, "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace").replace("\0", " ").strip()
        except OSError:
            continue  # exited between the listing and the read
        head, _sep, tail = stat.rpartition(")")
        fields = tail.split()
        if not head or len(fields) < 20:
            continue
        started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot + int(fields[19]) / tick))
        out[pid] = {"pid": pid, "ppid": int(fields[1]), "name": head.partition("(")[2],
                    "cmd": cmd, "start": started,
                    "cpu": (int(fields[11]) + int(fields[12])) / tick}
    return out


def _on_windows():
    return os.name == "nt"


def process_table(timeout=60):
    """{pid: {pid, ppid, name, cmd, start, cpu}} for every process: the Win32 snapshot on Windows (CIM when it
    cannot be read), /proc elsewhere. `cpu` is the cumulative user+kernel seconds the OS reports, or None.
    Empty dict when unreadable. `kill_guard` resolves a name kill against this table."""
    if not _on_windows():
        return _proc_table()
    return _win32_table() or _cim_table(timeout)


def _win32_table():
    """process_table's Windows reader: a Toolhelp snapshot for pid, parent and image, then each process's own
    times and command line (its PEB). About 0.06 s for the whole table, where Get-CimInstance spawns a
    PowerShell and takes 1 s or more. A process this user cannot open (system, protected) keeps an empty `cmd`,
    `start` and None `cpu`. {} when unreadable, so the caller falls back to CIM."""
    if ctypes is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        return {}
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_void_p), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("ppid", wintypes.DWORD), ("pri", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("exe", ctypes.c_wchar * 260)]

    class Basic(ctypes.Structure):
        _fields_ = [("ExitStatus", ctypes.c_void_p), ("Peb", ctypes.c_void_p), ("Affinity", ctypes.c_void_p),
                    ("Base", ctypes.c_void_p), ("Pid", ctypes.c_void_p), ("Parent", ctypes.c_void_p)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.ReadProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t))
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ntdll.NtQueryInformationProcess.argtypes = (wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                                ctypes.POINTER(ctypes.c_ulong))
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    listed = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = k32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            listed.append((entry.pid, entry.ppid, entry.exe))
            more = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)

    def ticks(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    out = {}
    for pid, ppid, name in listed:
        cmd, start, cpu = "", "", None
        # QUERY_LIMITED_INFORMATION alone still yields the times; QUERY_INFORMATION | VM_READ adds the PEB.
        handle = k32.OpenProcess(0x1000 | 0x0400 | 0x0010, False, pid) or k32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                created, _exit, kernel, user = (wintypes.FILETIME() for _ in range(4))
                if k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, _exit, kernel, user))):
                    start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ticks(created) / 1e7 - 11644473600))
                    cpu = int((ticks(kernel) + ticks(user)) / 10000000)
                cmd = _peb_command_line(k32, ntdll, handle, Basic)
            finally:
                k32.CloseHandle(handle)
        out[pid] = {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "start": start, "cpu": cpu}
    return out


def _peb_command_line(k32, ntdll, handle, basic):
    """The command line in an opened process's PEB (RTL_USER_PROCESS_PARAMETERS.CommandLine, offset 0x70), or ''."""
    def read(addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not addr or not k32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            return b""
        return buf.raw[:got.value]

    info = basic()
    if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None) != 0:
        return ""
    peb = read(info.Peb, 0x28)
    params = read(int.from_bytes(peb[0x20:0x28], "little"), 0x80) if len(peb) == 0x28 else b""
    if len(params) < 0x80:
        return ""
    length = int.from_bytes(params[0x70:0x72], "little")
    return read(int.from_bytes(params[0x78:0x80], "little"), length).decode("utf-16-le", "replace")


def _cim_table(timeout=60):
    """process_table's Windows reader, through Get-CimInstance. Empty dict when unreadable."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine,"
          " @{n='Start';e={$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')}},"
          " @{n='Cpu';e={if ($_.KernelModeTime -ne $null -and $_.UserModeTime -ne $null)"
          " { [long](($_.KernelModeTime + $_.UserModeTime) / 10000000) } else { $null }}}"
          " | ConvertTo-Json -Compress")
    for exe in ("pwsh", "powershell"):
        try:
            # errors="replace": a command line that is not valid UTF-8 otherwise leaves stdout None.
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, errors="replace", timeout=timeout)
        except Exception:
            continue
        if r.returncode != 0 or not (r.stdout or "").strip():
            continue
        try:
            # strict=False: one command line may carry a raw control character, and rejecting the whole
            # document blanks the table, which refuses every kill on the machine.
            rows = json.loads(r.stdout, strict=False)
        except ValueError:
            continue
        out = {}
        for row in rows if isinstance(rows, list) else [rows]:
            pid = row.get("ProcessId")
            if isinstance(pid, int):
                out[pid] = {"pid": pid, "ppid": row.get("ParentProcessId"), "name": row.get("Name") or "",
                            "cmd": row.get("CommandLine") or "", "start": row.get("Start") or "",
                            "cpu": row.get("Cpu")}
        return out
    return {}


# ----------------------------------------------------------------- Git Bash (MSYS) processes
def ps_text():
    """Git's `ps -W` output, or None when unreadable. Read once per run and passed on."""
    try:
        r = subprocess.run([_msys.tool("ps"), "-W"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def _ps_rows(text):
    """[(msys pid, msys ppid, winpid)] from `ps -W` text."""
    lines = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or not {"PID", "PPID", "WINPID"} <= set(lines[0]):
        return []
    pc, ppc, wc = (lines[0].index(k) for k in ("PID", "PPID", "WINPID"))
    return [(int(f[pc]), int(f[ppc]), int(f[wc])) for f in lines[1:]
            if len(f) > max(pc, ppc, wc) and f[pc].isdigit() and f[ppc].isdigit() and f[wc].isdigit()]


def winpid_from_ps(text, msys_pid):
    """The WINPID of an MSYS pid in `ps -W` text, else None."""
    return next((win for pid, _ppid, win in _ps_rows(text) if pid == msys_pid), None)


def msys_pid_for_winpid(winpid, text):
    """The MSYS pid of a Windows pid in `ps -W` text; None when it is not an MSYS process."""
    return next((pid for pid, _ppid, win in _ps_rows(text) if win == winpid), None)


def resolve_pid(pid, table, text):
    """A Windows pid in the table, mapping an MSYS pid through `ps -W`; None when gone."""
    if pid in table:
        return pid
    mapped = winpid_from_ps(text, pid)
    return mapped if mapped in table else None


def link_msys_parents(table, text):
    """Add `msys_ppid` (a Windows pid) to each MSYS process whose MSYS parent is not its Windows parent.

    An MSYS exec replaces the Windows process, so a `bash -c` wrapper's child names a Windows parent that is gone;
    only `ps -W` keeps the real link. Without it a stopped wrapper leaves its work running."""
    rows = _ps_rows(text)
    win = {pid: w for pid, _ppid, w in rows}
    for _pid, ppid, child in rows:
        parent = win.get(ppid)
        if child in table and parent in table and parent != child and table[child].get("ppid") != parent:
            table[child]["msys_ppid"] = parent
    return table


def git_bash():
    """Git Bash's bash.exe, never WSL's: a bare `bash` from Windows Python is System32's WSL shim, whose /proc
    cannot see MSYS pids. Git's `ps` is the anchor."""
    ps = _msys.tool("ps")
    if not os.path.isabs(ps):
        return None
    cand = os.path.join(os.path.dirname(ps), "bash.exe")
    return cand if os.path.exists(cand) else None


def msys_environ(pid, text):
    """The MSYS `/proc` environment block of Windows pid `pid`, or b'' when it is not an MSYS process or the read
    fails. A Git Bash child of a Git Bash process carries its environment only there: its Windows block is bare."""
    msys = msys_pid_for_winpid(pid, text)
    shell = git_bash() if msys is not None else None
    if not shell:
        return b""
    try:
        # `cat`, not `tr`: quotes and backslashes do not survive CreateProcess's re-quoting.
        r = subprocess.run([shell, "-c", "/usr/bin/cat /proc/%d/environ" % msys], capture_output=True, timeout=15)
    except Exception:
        return b""
    return r.stdout if r.returncode == 0 else b""


def native_environ(pid):
    """A process's own environment block (NUL-separated, UTF-8), read from its PEB on Windows and /proc elsewhere;
    b'' when unreadable."""
    if os.name != "nt":
        try:
            with open("/proc/%d/environ" % pid, "rb") as fh:
                return fh.read()
        except OSError:
            return b""
    if ctypes is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        return b""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.ReadProcessMemory.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ntdll.NtQueryInformationProcess.argtypes = (wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p,
                                                ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))

    class _BasicInfo(ctypes.Structure):
        _fields_ = [("ExitStatus", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                    ("AffinityMask", ctypes.c_void_p), ("BasePriority", ctypes.c_void_p),
                    ("UniqueProcessId", ctypes.c_void_p), ("InheritedFrom", ctypes.c_void_p)]

    handle = k32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
    if not handle:
        return b""

    def read(addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not addr or not k32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            return b""
        return buf.raw[:got.value]

    try:
        info = _BasicInfo()
        if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None) != 0:
            return b""
        peb = read(info.PebBaseAddress, 0x28)
        params = read(int.from_bytes(peb[0x20:0x28], "little"), 0x3F8) if len(peb) == 0x28 else b""
        if len(params) < 0x3F8:
            return b""
        size = min(int.from_bytes(params[0x3F0:0x3F8], "little"), 1 << 20)
        block = read(int.from_bytes(params[0x80:0x88], "little"), size)
        return block.decode("utf-16-le", "replace").encode("utf-8")
    finally:
        k32.CloseHandle(handle)


def environ_of(pid, text=None):
    """The target's environment block, or b'': its MSYS block when that carries a session id, else its own."""
    block = msys_environ(pid, text)
    return block if SID_ENV in block else native_environ(pid)


def _env_value(env, key):
    """The value of `key` (bytes, ending in `=`) in a NUL-separated environment block, or None."""
    for item in (env or b"").split(b"\0"):
        if item.startswith(key):
            return item[len(key):].decode("utf-8", "replace")
    return None


# ----------------------------------------------------------------- trees
def parents(table, row):
    """Pids that are `row`'s parent: the recorded Windows and MSYS parents that are live and started no later than
    `row`. Windows reuses pids, so a recorded parent that started after its child is a stranger holding the old
    number, never the parent. A start time that is missing cannot rule the link out."""
    out = []
    for key in ("ppid", "msys_ppid"):
        pid = row.get(key)
        parent = table.get(pid) if isinstance(pid, int) else None
        if parent is not None and pid != row.get("pid") and pid not in out                 and not (parent.get("start") and row.get("start") and parent["start"] > row["start"]):
            out.append(pid)
    return out


def children_of(table):
    """{parent pid: [child pids]} over `parents`: the one reader of the parent link."""
    out = {}
    for pid, row in table.items():
        for parent in parents(table, row):
            out.setdefault(parent, []).append(pid)
    return out


def ancestors(table, pid):
    out, seen = [], set()
    cur = (parents(table, table.get(pid, {})) or [None])[0]
    while cur is not None and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = (parents(table, table[cur]) or [None])[0]
    return out


def descendants(table, pid):
    kids, out, frontier = children_of(table), [], [pid]
    while frontier:
        for k in kids.get(frontier.pop(), ()):
            if k not in out and k != pid:
                out.append(k)
                frontier.append(k)
    return sorted(out)


# ----------------------------------------------------------------- the facts a verdict reads
def never_reason(row, table):
    """Why `row` is never a target, else None. A codex.exe is protected by its parent, which only the table shows."""
    for pat, why in NEVER:
        if pat.search(row.get("cmd") or ""):
            return why
    if (row.get("name") or "").lower() == "codex.exe" and any(
            (table[p].get("name") or "").lower() == "chatgpt.exe" for p in parents(table, row)):
        return "a codex.exe run by ChatGPT.exe"
    return None


def session_registry_dirs():
    """Every declared login's `sessions` dir that exists (`launch_check.login_dirs`)."""
    return [Path(p) / "sessions" for p in login_dirs() if (Path(p) / "sessions").is_dir()]


def registered_sessions(table):
    """{live pid: {sessionId, ...}} from every login's session registry."""
    out = {}
    for folder in session_registry_dirs():
        for path in Path(folder).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pid, session = data.get("pid"), data.get("sessionId")
            if isinstance(pid, int) and pid in table and isinstance(session, str):
                out.setdefault(pid, set()).add(session)
    return out


def session_host(table, session, registry, exclude, block_of):
    """(verdict, host pid): True when a live host serves `session`, False when none does, None when a live host's
    session cannot be read. `exclude` holds the targets' own trees; `block_of` reads a process's own block."""
    served = [pid for pid, found in sorted(registry.items()) if session in found]
    if served:
        return True, served[0]
    children = children_of(table)
    verdict = (False, None)
    for pid in sorted(table):
        row = table[pid]
        cmd = row.get("cmd") or ""
        if pid in exclude or (row.get("name") or "").lower() == "conhost.exe":
            continue
        if not (any(pat.search(cmd) for pat in HOST_PATTERNS) or SEAT_HOST.search(cmd)):
            continue
        named = HOST_SESSION.search(cmd)
        if named:
            if named.group(1) == session:
                return True, pid
            continue
        if pid not in registry and any(_env_value(block_of(k), SID_ENV) for k in children.get(pid, ())):
            verdict = (None, pid)
    return verdict


def projects_root():
    """This login's transcript folders: CLAUDE_CONFIG_DIR's `projects`, else ~/.claude/projects."""
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")) / "projects"


def project_keys(root=None):
    """{session id: [project folder name, ...]} over every transcript under `root` (one scandir per folder)."""
    keys = {}
    try:
        with os.scandir(root or projects_root()) as projects:
            for project in projects:
                if not project.is_dir():
                    continue
                with os.scandir(project.path) as files:
                    for entry in files:
                        if entry.is_file() and entry.name.endswith(".jsonl"):
                            keys.setdefault(entry.name[:-len(".jsonl")], []).append(project.name)
    except OSError:
        return {}
    return keys


def same_project(a, b):
    """True when two Claude project keys are the same repository: a worktree's key starts with `<root key>--`."""
    return a == b or a.startswith(b + "--") or b.startswith(a + "--")


def judge(table, pid, me, block_of, host_of, project_of):
    """(proof, None) when `pid` may be stopped, else (None, refusal), in the module docstring's order.

    `block_of(pid)` is the target's environment block; `host_of(session)` is `session_host`'s verdict;
    `project_of(session)` is the project key of that session's transcript, or None."""
    row = table[pid]
    never = never_reason(row, table)
    if never:
        return None, "NEVER: %s, not a target, and its own children are not ours to stop" % never
    if me.pid and me.pid in ancestors(table, pid):
        return "descends from this session's claude.exe pid %d" % me.pid, None
    env = block_of(pid)
    if not env:
        return None, "its environment is unreadable, so a session's claim on it cannot be ruled out"
    owner = _env_value(env, SID_ENV)
    if owner and owner == me.sid:
        return "its own environment names this session %s" % owner[:8], None
    for a in ancestors(table, pid):
        cmd = table[a].get("cmd") or ""
        named = HOST_SESSION.search(cmd) if any(pat.search(cmd) for pat in HOST_PATTERNS) else None
        if named and named.group(1) != me.sid:
            return None, ("it runs inside a DIFFERENT session's tree (host pid %d, session %s): that session stops it"
                          % (a, named.group(1)[:8]))
    if not owner:
        return "unclaimed: its environment names no session and no session host sits above it", None
    live, host = host_of(owner)
    if live:
        return None, ("its environment names a DIFFERENT session %s, which a live session host serves (pid %s): "
                      "that session stops it" % (owner[:8], host))
    if live is None:
        return None, ("its environment names a DIFFERENT session %s that cannot be ruled dead: host pid %s runs with "
                      "a session that cannot be read" % (owner[:8], host))
    theirs = project_of(owner)
    if theirs and me.project and not same_project(theirs, me.project):
        return None, ("it is the leftover of dead session %s of another project (%s): that project stops it"
                      % (owner[:8], theirs))
    return "leftover of dead session %s: no live session host serves it" % owner[:8], None


# ----------------------------------------------------------------- the stop
def taskkill(pid):
    """Kill the one process `pid`. Never `/T`: it walks the OS parent pids, which Windows reuses, so it can stop a
    stranger. Success is the target gone."""
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
        listing = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return listing.returncode == 0 and '"%d"' % int(pid) not in (listing.stdout or "")


def kill_sparing(table, root, never_pids):
    """Kill root and its verified descendants except each never-kill member and its own subtree, deepest first, so
    no member is orphaned mid-kill. Returns (killed, failed, spared)."""
    spared = set()
    for pid in never_pids:
        spared.update([pid] + descendants(table, pid))
    doomed = [p for p in [root] + descendants(table, root) if p not in spared]
    killed, failed = [], []
    for pid in sorted(doomed, key=lambda p: -len(ancestors(table, p))):
        (killed if taskkill(pid) else failed).append(pid)
    return killed, failed, sorted(spared)


def record(entry):
    """Append one ledger row; False when it cannot be written."""
    return _hook_state.append_jsonl_rotating(str(LEDGER), [entry])


def _stop(table, pid, never):
    """(result, killed pids) for one judged target."""
    killed, failed, _spared = kill_sparing(table, pid, never)
    return ("partial" if killed and failed else "killed" if killed else "kill-failed"), sorted(killed)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in RETIRED:
        print("REFUSED: `reap.py %s` is retired; %s" % (argv[0], RETIRED[argv[0]]))
        return 2
    args = build_parser().parse_args(argv)
    if args.reason is not None:
        print("REFUSED: --reason is retired; %s" % RETIRED["--reason"])
        return 2
    sid = env.get("CLAUDE_CODE_SESSION_ID") or None
    table = process_table()
    if not table:
        print("REFUSED: process table unreadable; nothing is stopped on a guess")
        return 2
    ps = ps_text()
    link_msys_parents(table, ps)
    keys = project_keys()

    def project_of(session):
        found = keys.get(session) or []
        return found[0] if len(found) == 1 else None

    me = Session(int(env.get("CLAUDE_PID") or 0) or None, sid, project_of(sid) if sid else None)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    refused = 0
    targets = []
    for raw in args.pids:
        pid = resolve_pid(raw, table, ps)
        if pid is None:
            print("REFUSED %s: not in the process table, already gone or a stale number; re-read the list" % raw)
            record({"ts": now, "session": sid, "pid": raw, "why": args.why, "result": "refused",
                    "refusal": "not in the process table"})
            refused += 1
            continue
        targets.append(pid)
    # One stop per tree: a repeated pid, or a target inside another target's tree, goes with its root.
    unique = list(dict.fromkeys(targets))
    covered = {t for t in unique if any(a in unique for a in ancestors(table, t))}
    for pid in sorted(covered):
        print("COVERED %d: inside another target's tree; stopped with it" % pid)
    targets = [t for t in unique if t not in covered]
    trees = {t: [t] + descendants(table, t) for t in targets}
    exclude = frozenset(k for tree in trees.values() for k in tree)
    registry = registered_sessions(table)
    blocks = {}

    def block_of(pid):
        if pid not in blocks:
            blocks[pid] = environ_of(pid, ps)
        return blocks[pid]

    def host_of(session):
        return session_host(table, session, registry, exclude, native_environ)

    for pid in targets:
        row, tree = table[pid], trees[pid]
        head = "%d %s :: %s" % (pid, row.get("name"), (row.get("cmd") or "")[:110])
        proof, refusal = judge(table, pid, me, block_of, host_of, project_of)
        never = [k for k in tree if k != pid and never_reason(table[k], table)]
        entry = {"ts": now, "session": sid, "pid": pid, "name": row.get("name"), "cmd": (row.get("cmd") or "")[:400],
                 "why": args.why, "proof": proof, "refusal": refusal, "tree": tree, "spared_never": never}
        if refusal:
            print("REFUSED %s\n   %s" % (head, refusal))
            entry["result"] = "refused"
            record(entry)
            refused += 1
            continue
        if args.dry_run:
            print("DRY %s\n   proof: %s\n   tree: %s%s" % (head, proof, tree,
                                                      "\n   spared (never-kill): %s" % never if never else ""))
            entry["result"] = "dry-run"
            record(entry)
            continue
        entry["result"] = "killing"
        if not record(entry):
            print("REFUSED %s\n   LEDGER unwritable: %s; no kill without its row" % (head, LEDGER))
            refused += 1
            continue
        result, killed = _stop(table, pid, never)
        print("%s %s\n   proof: %s\n   killed: %s%s" % (result.upper(), head, proof, killed,
                                                       "\n   spared (never-kill): %s" % never if never else ""))
        entry.update(result=result, killed=killed)
        record(entry)
        refused += result != "killed"
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
