#!/usr/bin/env python3
"""reap.py — stop a process THIS session owns, on proof, with a reason, on the record.

    python3 .claude/tools/reap.py <pid> [<pid> ...] --reason <class> --why "<one line>" [class evidence] [--dry-run]

A raw kill cannot show WHY it is justified, so the classifier denies it. This tool can, and refuses when
it cannot (incident: auto-memory/archive/feedback_guard_denial_of_justified_work_fix_the_guard.md):

  ownership   any one of:
              - it descends from this session's claude.exe (CLAUDE_PID);
              - its OWN environment names this session's CLAUDE_CODE_SESSION_ID, the proof that
                survives the launcher wrapper being killed and so covers the hung-run case;
              - it is a detached sidecar child (launcher scrubs CLAUDE_*, wrapper exits) whose
                SIDECAR_PARENT_SESSION_ID tag names this session, or, launched before that tag
                existed, whose CLAUDE_CODE_SIDECAR_PROMPT_FILE is a -f this session's transcript
                launched;
              - only when the environment is unreadable, its command line names a dispatch record
                whose parentSessionId is this session.
              A process whose environment names a DIFFERENT session is refused outright, record or
              no record. A peer's process with an identical command line is NOT OWNED and is never
              killed.
  never       a session host (claude.exe --session-id / --bg-pty-host), an MCP server, a shared
              transport proxy, a codex.exe whose parent is ChatGPT.exe (the app runs it headless, with
              no window). A proxy is shared when its EXECUTABLE (the image name, never argument text)
              is claude-code-proxy and its environment carries no SIDECAR_LAUNCH_ID; a text-only shim is
              never a transport proxy.
              A never-kill member INSIDE a target's tree is spared and the rest of the tree dies;
              a never-kill TARGET is refused outright, because its children are its own.
  reason      one closed class, each verified from evidence, not from prose:
                duplicate   --twin <pid> alive and sharing --key <text> in its command line: the
                            work continues there, so this copy is pure waste
                orphan      the target's parent is gone from the process table; for a process carrying
                            SIDECAR_LAUNCH_ID=X that is not itself a launcher (a seat child), no
                            launcher of X lives instead (see `launchers` below)
                stalled     --log <path> untouched for --idle-min minutes (default 30); a sidecar's
                            `<record>.stall` marker counts by the idle seconds its launcher wrote
                superseded  --artifact <path> exists: the output the target is producing is already on disk,
                            written by another job. An artifact whose label stem (the name before
                            its first dot, any case) is in the target tree's command lines is the
                            job's own in-progress output and refuses.
                queued      nothing the job runs is in flight: no model client (claude, codex, opencode,
                            ollama), `*_sidecar.sh` launcher or dispatch shim in its tree, and no live process
                            outside it carrying its job id (REAP_JOB_ID, or a sidecar's SIDECAR_LAUNCH_ID, set
                            by its launcher at spawn, so detached members count). A target with no job id
                            refuses. Stopping it loses no paid or model work, so an owned job waiting its turn
                            stops on this.
                owner       --why is the owner's stop order, verbatim from this session's transcript (case and
                            whitespace ignored): an owner-typed message that passes kill_guard's stop-order
                            test (a stop verb, no keep word), or the question of an AskUserQuestion the owner
                            answered with a stop option. It names the target: its pid, its launch label, or the
                            script or campaign its command line or a launching ancestor's runs. Any other owner
                            sentence is one the agent chose. The owner's latest reply also passes with no quote
                            when it approves the stop (_owner_text.owner_consent): a typed "yes" to the agent's
                            message that asked to stop the target by one of those names, or a typed or answered
                            order naming it. A missing or unreadable transcript refuses. The
                            only prose-backed class,
                            and the only one that SUBSTITUTES for the ownership proof above: the
                            proof stands in for a human's authorisation, so a human naming the
                            target outranks it. It does not reach a never-kill target or one whose
                            own environment names a different session.
  dispatch    a DISPATCH SERVER carries SIDECAR_LAUNCH_ID=X and is a claude-code-proxy executable, a
  servers     text_only_shim.py python process, or the nohup wrapper of either. A sidecar launch starts
              them detached and stops them in an exit trap, which a TerminateProcess never runs.
              Ownership is the session proof above, over the server's own environment.
              orphan on a dispatch server: no live process OTHER than dispatch servers carries X (the
              launcher and the seat child are gone); a live one refuses, naming it. The parent-gone
              test does not apply, because a detached server's parent proves nothing.
              Killing (or dry-running) a process that carries X and is not itself a dispatch server
              also kills every dispatch server carrying X that the ownership proof covers, printed as
              `KILLED <pid> ... (dispatch server of launch X)` (`DRY` under --dry-run).
  launchers   a launcher of X is a live `*_sidecar.sh` bash that carries X, or that is an ancestor of the
              target: a direct launch exports X after its own bash started, and an MSYS process's block
              holds only what its native parent gave it, so only a fan-out's launchers carry it. One
              carrying SIDECAR_SEAT_LAUNCH_ID=X runs inside the seat and is not its launcher.
  dead        `orphan` also reaches a process of ANOTHER session when it carries a launch id, is not a
  session     launcher, has no live launcher, and its owning session (SIDECAR_PARENT_SESSION_ID, else
              CLAUDE_CODE_SESSION_ID) has no live host: nothing can consume its result. The host test:
              a session is live when a live process's `<login>/sessions/<pid>.json` row names it (every
              login model_registry.login_dirs declares), when a live process matching the session-host
              patterns of `never` names it after --session-id or --resume, or when a live process with no
              launch id or sidecar tag carries it as CLAUDE_CODE_SESSION_ID (a tool shell, an MCP
              server). A live claude host with no registry row and no id in its command line, whose
              children carry a CLAUDE_CODE_SESSION_ID, is a session nobody can name: every session is
              then unknown and the rule refuses.
  ledger      every kill (and every refusal) is appended to .claude/logs/reap_ledger.jsonl.

    python3 .claude/tools/reap.py live-launch [--prompt <file>] [--label <name>] [--record <file>]
                                              [--resume <sid>] [--self <winpid>] [--launch <id>] [--fanout <id>]

The sidecar duplicate-launch scan (scripts/lib/sidecar_common.sh, tools/sidecar_fanout.py): every live
launcher whose -f names <file> or whose -r is <sid>, and every seat child whose CLAUDE_CODE_SIDECAR_PROMPT_FILE
is <file> or whose command line resumes <sid>, printed with its launch id, age and the reap command that
stops it. A prompt match is a duplicate only for the same JOB when the caller names one: a launcher whose -l
is <name> or whose -R is <file>, or a seat child whose CLAUDE_CODE_SIDECAR_LABEL is <name>. Jobs that share a
brief under their own label and record (an A/B's arms) are not duplicates; with neither flag the prompt alone
is the key, and a seat child carrying no label cannot be matched to a named job.
Never a match: <winpid> with its ancestors and descendants, this scan's own ancestors, a process
carrying launch <id>, a bash -c wrapper whose launcher's -R record's .pid names launch <id>, and a job of
fan-out <id> (SIDECAR_FANOUT_ID; siblings share a brief by design). The record exemption covers only -c
wrappers, never launcher or seat processes: a second launch reusing -R must still refuse.
Exit 0 none, 3 at least one, 4 process table unreadable (a warning; the caller launches), 2 bad usage.

The kill is `taskkill /PID <root> /T /F` — the whole tree. A launcher detaches its headless claude seat
outside that tree, so killing a process whose tree carries launch X also kills each owned process carrying
SIDECAR_SEAT_LAUNCH_ID=X (its never-kill members spared), printed `KILLED <pid> ... (seat of launch X)`.
Exit 0 = every target killed (or dry-run); 2 = at least one target refused (nothing partial
is hidden: refused targets are listed, killed ones are killed).

MSYS pids (what Git Bash `kill` and `ps` print) are mapped to Windows pids through `ps -W`.
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
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / ".claude" / "hooks"))
from kill_guard import KEEP_LABEL_RE, STOP_LABEL_RE, _owner_inputs  # noqa: E402  (the owner-message projection a stop request reads)
from _owner_text import owner_consent  # noqa: E402
import _hook_state  # noqa: E402
from _win_path import drive_path, native_path  # noqa: E402

LEDGER = REPO / ".claude" / "logs" / "reap_ledger.jsonl"
STOPPED_LAUNCHES_KEY = "sidecar_stopped_launches"  # session hook state; watch_records.py reads it
sys.path.insert(0, str(REPO / ".claude" / "tools"))
from model_registry import login_dirs, session_projects_dir  # noqa: E402
import sidecar_argv  # noqa: E402

PROJECTS_ROOT = Path(session_projects_dir())
REASONS = ("duplicate", "orphan", "stalled", "superseded", "queued", "owner")
# A tree member doing paid or result-bearing work: a model client, a sidecar launcher, or a dispatch shim.
IN_FLIGHT_RE = re.compile(r"(?:^|[\\/\s\"'])(?:claude|codex|opencode|ollama)(?:\.exe)?(?=[\s\"']|$)|_sidecar\.sh\b|text_only_shim",
                          re.I)
# The id a job's launcher sets in its environment at spawn; every process carrying it is one of the job's.
JOB_ID_KEYS = (b"REAP_JOB_ID=", b"SIDECAR_LAUNCH_ID=")

# Never a target, whoever owns it: a session host, a background pty host, an MCP server. A shared transport
# proxy is judged by image name and environment in never_reason, not by command-line text.
NEVER = (
    (re.compile(r"claude(?:\.exe)?\"?\s+(?:.*\s)?--(?:session-id|bg-pty-host)\b", re.I), "a Claude Code session HOST"),
    (re.compile(r"claude(?:\.exe)?\"?\s*$", re.I), "an interactive Claude Code session HOST"),
    # Executable + server name in any argument (flags may precede the script), not a bare word: `-u ENABLE_LSP_TOOL`
    # sits in every sidecar's env cleanup.
    (re.compile(r"(?:node|python3?|pwsh|dotnet)(?:\.exe)?\"?\s+(?:\S+\s+)*?\S*(?:mcp[-_]|[-_]mcp|semantic-search|[\w.-]+-ls(?:\.|\s|$))", re.I),
     "shared INFRA (an MCP/LSP server other sessions use)"),
)
HOST_PATTERNS = tuple(pat for pat, _ in NEVER[:2])
HOST_SESSION = re.compile(r"--(?:session-id|resume)[=\s]+\"?([A-Za-z0-9_-]+)")
SEAT_RESUME = re.compile(r"--resume[=\s]+\"?([A-Za-z0-9_-]+)")
LAUNCHER_SCRIPT = re.compile(r"[\w.-]*_sidecar\.sh\b", re.I)
PROXY_IMAGE = re.compile(r"^claude-code-proxy[\w.-]*$", re.I)
SHIM_SCRIPT = re.compile(r"text_only_shim\.py\b", re.I)
NOHUP_PROXY = re.compile(r"claude-code-proxy[\w.-]*(?:\.exe)?\"?\s+serve\b", re.I)
LAUNCH_ENV = b"SIDECAR_LAUNCH_ID="
# Per-invocation caches (environment blocks, `ps -W`, the launch census); None outside main().
_RUN = None
# The launcher's -R takes ANY path (docs: "run-record path"), so the matcher must not demand a
# suffix. 2026-09-18: it required ".record.json" while every real dispatch passed
# "-R .claude/scratch/plancheck/rec_arch.json", so the record proof never ran and two hung sidecars
# could not be reaped.
RECORD_RE = re.compile(r"(?:-R|--record)\s+\"?([^\s\"]+)")
SID_ENV = b"CLAUDE_CODE_SESSION_ID="
# A sidecar launcher scrubs CLAUDE_* from its child and detaches it, so the child carries neither this
# session's id nor its ancestry. The launcher tags the child with its parent session instead; a child
# launched before the tag existed still names its prompt file, which this session's transcript launched.
PARENT_ENV = b"SIDECAR_PARENT_SESSION_ID="
PROMPT_ENV = b"CLAUDE_CODE_SIDECAR_PROMPT_FILE="
LABEL_ENV = b"CLAUDE_CODE_SIDECAR_LABEL="
SEAT_ENV = b"SIDECAR_SEAT_LAUNCH_ID="
FANOUT_ENV = b"SIDECAR_FANOUT_ID="


# ----------------------------------------------------------------- process table
def process_table(timeout=60):
    """{pid: {pid, ppid, name, cmd, start}} for every process, via CIM. Empty dict when unreadable."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine,"
          " @{n='Start';e={$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')}} | ConvertTo-Json -Compress")
    for exe in ("pwsh", "powershell"):
        try:
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, errors="replace", timeout=timeout)
        except Exception:
            continue
        if r.returncode != 0 or not (r.stdout or "").strip():
            continue
        try:
            # strict=False: one process's command line may carry a raw control character (a probe
            # passing \x1b, a stray \x01). Rejecting the whole document blanks the table, and an
            # empty table refuses every kill on the machine.
            rows = json.loads(r.stdout, strict=False)
        except ValueError:
            continue
        out = {}
        for row in rows if isinstance(rows, list) else [rows]:
            pid = row.get("ProcessId")
            if isinstance(pid, int):
                out[pid] = {"pid": pid, "ppid": row.get("ParentProcessId"), "name": row.get("Name") or "",
                            "cmd": row.get("CommandLine") or "", "start": row.get("Start") or ""}
        return out
    return {}


def winpid_from_ps(text, msys_pid):
    """WINPID for an MSYS pid from `ps -W` text, else None."""
    lines = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or "WINPID" not in lines[0]:
        return None
    col = lines[0].index("WINPID")
    for r in lines[1:]:
        if r and r[0] == str(msys_pid) and len(r) > col and r[col].isdigit():
            return int(r[col])
    return None


def resolve_pid(pid, table):
    """A Windows pid in the table, mapping an MSYS pid through `ps -W` when needed; None when gone."""
    if pid in table:
        return pid
    try:
        r = subprocess.run(["ps", "-W"], capture_output=True, text=True, timeout=15)
        mapped = winpid_from_ps(r.stdout, pid) if r.returncode == 0 else None
    except Exception:
        mapped = None
    return mapped if mapped in table else None


def ancestors(table, pid):
    out, seen = [], set()
    cur = _parent(table.get(pid, {}), table)
    while isinstance(cur, int) and cur in table and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = _parent(table[cur], table)
    return out


def _parent(row, table):
    """The Windows parent while it lives, else the MSYS parent `link_msys_parents` recorded."""
    ppid = row.get("ppid")
    return ppid if ppid in table else row.get("msys_ppid")


def outside_windows_tree(table, pid, tree):
    """Members of `tree` that `taskkill /T` on `pid` cannot reach: no Windows-parent chain to `pid`."""
    out = []
    for k in tree:
        cur, seen = table[k].get("ppid"), set()
        while isinstance(cur, int) and cur in table and cur != pid and cur not in seen:
            seen.add(cur)
            cur = table[cur].get("ppid")
        if k != pid and cur != pid:
            out.append(k)
    return out


def descendants(table, pid):
    out, frontier = [], [pid]
    while frontier:
        cur = frontier.pop()
        for k, r in table.items():
            if cur in (r.get("ppid"), r.get("msys_ppid")) and k not in out and k != pid:
                out.append(k)
                frontier.append(k)
    return sorted(out)


# ----------------------------------------------------------------- the three gates
def never_reason(row, table=None):
    """The command line decides: `claude.exe -p ...` is a headless child, `claude.exe --session-id` a host.

    A codex.exe is protected by its parent, which only the process table shows.
    """
    for pat, why in NEVER:
        if pat.search(row.get("cmd") or ""):
            return why
    if PROXY_IMAGE.match(row.get("name") or "") and not launch_of(row.get("pid")):
        return "a transport PROXY other sessions may route through (no SIDECAR_LAUNCH_ID)"
    parent = (table or {}).get(row.get("ppid")) or {}
    if (row.get("name") or "").lower() == "codex.exe" and (parent.get("name") or "").lower() == "chatgpt.exe":
        return "a codex.exe run by ChatGPT.exe"
    return None


def _resolve(path):
    """The path as this process can open it: MSYS `/c/...` to `C:/...`, and repo-relative to REPO.

    A dispatch passes `-R .claude/scratch/...`, which is relative to the repo, not to reap's cwd.
    """
    path = native_path(path)
    return path if os.path.isabs(path) else str(REPO / path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def ps_text():
    """`ps -W` output, read once per invocation; None when unreadable."""
    if _RUN is not None and "ps" in _RUN:
        return _RUN["ps"]
    try:
        r = subprocess.run(["ps", "-W"], capture_output=True, text=True, timeout=15)
        text = r.stdout if r.returncode == 0 else None
    except Exception:
        text = None
    if _RUN is not None:
        _RUN["ps"] = text
    return text


def link_msys_parents(table, text):
    """Add `msys_ppid` (a Windows pid) to each MSYS process whose MSYS parent is not its Windows parent.

    An MSYS exec replaces the Windows process, so a `bash -c` wrapper's child names a Windows parent
    that is gone; only `ps -W` keeps the real link. Without it `descendants` misses the child and a
    reaped wrapper leaves its work running.
    """
    lines = [ln.split() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or not {"PID", "PPID", "WINPID"} <= set(lines[0]):
        return table
    pc, ppc, wc = (lines[0].index(k) for k in ("PID", "PPID", "WINPID"))
    rows = [f for f in lines[1:] if len(f) > max(pc, ppc, wc) and f[pc].isdigit() and f[ppc].isdigit() and f[wc].isdigit()]
    win = {int(f[pc]): int(f[wc]) for f in rows}
    for f in rows:
        child, parent = int(f[wc]), win.get(int(f[ppc]))
        if child in table and parent in table and parent != child and table[child].get("ppid") != parent:
            table[child]["msys_ppid"] = parent
    return table


def msys_pid_for_winpid(winpid):
    """The MSYS pid for a Windows pid, from `ps -W`; None when it is not an MSYS process.

    `/proc` on Git Bash is MSYS's own filesystem and is keyed by MSYS pid, while the CIM process
    table is keyed by Windows pid, so the environ read below needs this translation.
    """
    text = ps_text()
    if not text:
        return None
    lines = [ln.split() for ln in text.splitlines() if ln.strip()]
    if not lines or "WINPID" not in lines[0]:
        return None
    col = lines[0].index("WINPID")
    for f in lines[1:]:
        if len(f) > col and f[col].isdigit() and int(f[col]) == winpid and f[0].isdigit():
            return int(f[0])
    return None


def git_bash():
    """Git Bash's bash.exe, never WSL's.

    A bare `bash` from Windows Python resolves to `C:\\Windows\\System32\\bash.exe` -- WSL, a
    different kernel whose /proc has never heard of our MSYS pids (measured 2026-09-18: `uname`
    said `microsoft-standard-WSL2` and the read failed `No such file or directory`). `ps` is
    Git's, so its directory is the reliable anchor.
    """
    ps = shutil.which("ps")
    if not ps:
        return None
    cand = os.path.join(os.path.dirname(ps), "bash.exe")
    return cand if os.path.exists(cand) else None


def native_environ(pid):
    """A native Windows process's environment block (NUL-separated, UTF-8), read from its PEB; b'' when
    unreadable. MSYS `/proc` holds only MSYS processes, so without this a native orphan (Godot, python,
    dotnet) whose launcher died has no ownership proof at all."""
    if os.name != "nt" or ctypes is None or ctypes.sizeof(ctypes.c_void_p) != 8:
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


def process_exited(pid, start):
    """True when Windows pid `pid` no longer runs the process the table read, started at `start`
    ('yyyy-MM-dd HH:mm:ss', local): it has exited, or the pid now belongs to a newer process. False when
    it still runs, or when the OS cannot say (access denied), so an unreadable process is never presumed dead."""
    if os.name != "nt" or ctypes is None:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no process has this pid
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
            return True
        created, _exit, _kernel, _user = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, _exit, _kernel, _user))):
            return False
        epoch = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 1e7 - 11644473600
        try:
            listed = time.mktime(time.strptime(start or "", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return False
        return abs(epoch - listed) > 2
    finally:
        k32.CloseHandle(handle)


def msys_environ(pid):
    """The MSYS `/proc` environment block of Windows pid `pid`, or b'' when it is not an MSYS process or
    the read fails. Windows Python cannot see `/proc`; `ps -W` lists native processes too, but `/proc`
    holds a block only for MSYS ones."""
    if _RUN is not None and pid in _RUN.setdefault("msys", {}):
        return _RUN["msys"][pid]
    block = _msys_environ_read(pid)
    if _RUN is not None:
        _RUN["msys"][pid] = block
    return block


def _msys_environ_read(pid):
    msys = msys_pid_for_winpid(pid)
    shell = git_bash() if msys is not None else None
    if not shell:
        return b""
    try:
        # `cat`, not `tr '\\0' '\\n'`: the quotes and backslashes do not survive CreateProcess's
        # re-quoting (the shell receives them mangled and the read fails empty). NULs pass through
        # a binary pipe unharmed, and the caller only does a substring search.
        r = subprocess.run([shell, "-c", "cat /proc/%d/environ" % msys], capture_output=True, timeout=15)
    except Exception:
        return b""
    return r.stdout if r.returncode == 0 else b""


def environ_of(pid):
    """The target's environment block, or b''. The MSYS block first, kept only when it carries the
    `CLAUDE_CODE_SESSION_ID` key; otherwise the process's own PEB."""
    block = msys_environ(pid)
    if SID_ENV.split(b"=")[0] in block:
        return block
    return native_environ(pid)


def _environ(pid):
    """environ_of, read once per pid per invocation."""
    if _RUN is None:
        return environ_of(pid)
    if pid not in _RUN["env"]:
        _RUN["env"][pid] = environ_of(pid)
    return _RUN["env"][pid]


def launch_environ(pid):
    """The block a launch id is read from: the process's PEB. SIDECAR_LAUNCH_ID is inherited at spawn, so
    the PEB holds it for an MSYS process a native one spawned, and the census reads every process, where
    environ_of's per-process bash spawn is too slow for a whole-table read. An MSYS process another MSYS
    process exec'd (a launcher's snapshot re-exec) has a bare PEB; `_ids_block` reads its MSYS block."""
    return native_environ(pid)


def launch_block(pid):
    """launch_environ, read once per pid per invocation."""
    if not isinstance(pid, int):
        return b""
    if _RUN is None:
        return launch_environ(pid)
    if pid not in _RUN["launch"]:
        _RUN["launch"][pid] = launch_environ(pid)
    return _RUN["launch"][pid]


def launch_of(pid):
    """The SIDECAR_LAUNCH_ID in the process's own environment, or None."""
    return _env_value(launch_block(pid), LAUNCH_ENV)


def is_launcher(row):
    """A bash running a `*_sidecar.sh` launcher, or a snapshot copy or fork of one."""
    return (row.get("name") or "").lower().startswith("bash") and bool(LAUNCHER_SCRIPT.search(row.get("cmd") or ""))


def launchers_of(table, launch, pid):
    """Live launchers of `launch` outside `pid`'s own subtree (module docstring, `launchers`)."""
    below = set(descendants(table, pid)) | {pid}
    above = set(ancestors(table, pid))
    out = []
    for k in sorted(table):
        if k in below or not is_launcher(table[k]):
            continue
        block = launch_block(k)
        if k in above or (_env_value(block, LAUNCH_ENV) == launch and _env_value(block, SEAT_ENV) != launch):
            out.append(k)
    return out


def session_registry_dirs():
    """Every declared login's `sessions` dir that exists (`model_registry.login_dirs`)."""
    return [Path(p) / "sessions" for p in login_dirs() if (Path(p) / "sessions").is_dir()]


def registered_sessions(table):
    """{live pid: {sessionId, ...}} from every login's session registry."""
    out = {}
    for folder in session_registry_dirs():
        for path in Path(folder).glob("*.json"):
            data = _read_json(str(path))
            pid, session = data.get("pid"), data.get("sessionId")
            if isinstance(pid, int) and pid in table and isinstance(session, str):
                out.setdefault(pid, set()).add(session)
    return out


def session_live(table, session):
    """True when a live host serves `session`, False when none does, None when a live Claude Code host's
    session cannot be read (module docstring, `dead session`)."""
    cache = _RUN.setdefault("live", {}) if _RUN is not None else {}
    if session in cache:
        return cache[session]
    registry = registered_sessions(table)
    verdict = False if not any(session in found for found in registry.values()) else True
    for pid in sorted(table) if verdict is False else ():
        block = launch_block(pid)
        if (_env_value(block, SID_ENV) == session and not _env_value(block, LAUNCH_ENV)
                and not _env_value(block, PARENT_ENV)):
            verdict = True
            break
        cmd = table[pid].get("cmd") or ""
        if not any(pat.search(cmd) for pat in HOST_PATTERNS):
            continue
        named = HOST_SESSION.search(cmd)
        if named:
            if named.group(1) == session:
                verdict = True
                break
            continue
        if pid not in registry and any(_env_value(launch_block(k), SID_ENV)
                                       for k, r in table.items() if r.get("ppid") == pid):
            verdict = None
    cache[session] = verdict
    return verdict


def leftover_proof(table, pid, sid):
    """The ownership proof for a dead session's leftover (module docstring, `dead session`), else None."""
    launch = launch_of(pid)
    if not launch or is_launcher(table[pid]):
        return None
    block = launch_block(pid)
    owner = _env_value(block, PARENT_ENV) or _env_value(block, SID_ENV)
    if not owner or owner == sid or session_live(table, owner) is not False:
        return None
    if launchers_of(table, launch, pid):
        return None
    return ("leftover of dead session %s: no live session host serves it, and launch %s has no live launcher"
            % (owner[:8], launch))


def is_server_image(row):
    """A claude-code-proxy executable, a text_only_shim.py python process, or the nohup wrapper of either."""
    name, cmd = (row.get("name") or "").lower(), row.get("cmd") or ""
    if PROXY_IMAGE.match(name):
        return True
    if name.startswith("python"):
        return bool(SHIM_SCRIPT.search(cmd))
    if name in ("nohup", "nohup.exe"):
        return bool(SHIM_SCRIPT.search(cmd) or NOHUP_PROXY.search(cmd))
    return False


def server_launch(row):
    """The launch id when the process is a dispatch server, else None."""
    return launch_of(row.get("pid")) if is_server_image(row) else None


def launch_census(table):
    """{launch id: [pids carrying it]} over the whole table, built once per invocation."""
    if _RUN is not None and _RUN.get("census") is not None:
        return _RUN["census"]
    census = {}
    for pid in sorted(table):
        launch = launch_of(pid)
        if launch:
            census.setdefault(launch, []).append(pid)
    if _RUN is not None:
        _RUN["census"] = census
    return census


def ownership(table, pid, session_pid, sid):
    """(proof, foreign): a proof string when the target is this session's; `foreign` set when it is
    positively ANOTHER session's; (None, None) when ownership simply cannot be established."""
    if session_pid and session_pid in ancestors(table, pid):
        return "descends from this session's claude.exe pid %d" % session_pid, None
    # The target's OWN environment is the decisive evidence: a process launched from this session
    # inherits CLAUDE_CODE_SESSION_ID, and nothing else can put it there. It is also the only proof
    # that survives the launcher wrapper being killed -- 2026-09-18, two hung sidecars were
    # unreapable because their ancestor was gone and the -R record is written only when a run ENDS.
    env = _environ(pid)
    mine = (SID_ENV + sid.encode()) if sid else None
    if env and mine and mine in env:
        return "its own environment names session %s (launched from this session)" % sid[:8], None
    parent = _env_value(env, PARENT_ENV)
    if parent:
        if sid and parent == sid:
            return "its sidecar tag names parent session %s (a sidecar this session launched)" % sid[:8], None
        return None, "its sidecar tag names a DIFFERENT parent session -- a peer's dispatch"
    if env and SID_ENV in env:
        return None, "its own environment names a DIFFERENT session -- a peer's process"
    prompt = _env_value(env, PROMPT_ENV)
    if prompt and sid and launched_prompt(sid, prompt):
        return "its sidecar prompt file %s was launched by this session's transcript" % os.path.basename(prompt), None
    # A record on disk is second-hand (a stale -R path can name us while the process is a peer's),
    # so it is consulted only when the environment was unreadable.
    cmd = table[pid].get("cmd") or ""
    m = RECORD_RE.search(cmd)
    if m and sid:
        path = m.group(1)
        if _read_json(_resolve(path)).get("parentSessionId") == sid:
            return "its dispatch record %s names parentSessionId %s (this session)" % (os.path.basename(path), sid[:8]), None
    return None, None


def _env_value(env, key):
    """The value of `key` (bytes, ending in `=`) in a NUL-separated environment block, or None."""
    for item in (env or b"").split(b"\0"):
        if item.startswith(key):
            return item[len(key):].decode("utf-8", "replace")
    return None


def _same_path(a, b):
    fix = lambda p: os.path.normcase(os.path.abspath(drive_path(p.replace("\\", "/"))))
    return fix(a) == fix(b)


def launched_prompt(sid, prompt):
    """True when this session's transcript holds a sidecar launch whose -f resolves to `prompt`."""
    transcript = session_transcript(sid)
    if not transcript:
        return False
    sys.path.insert(0, str(REPO / ".claude" / "tools"))
    import orchestration_metrics as om
    import sidecar_argv
    name = os.path.basename(prompt)
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if name not in line or "_sidecar.sh" not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if entry.get("type") == "assistant" else None
            for item in (message or {}).get("content") or []:
                command = (item.get("input") or {}).get("command") if isinstance(item, dict) else None
                if item.get("type") != "tool_use" or not isinstance(command, str):
                    continue
                run_cwd, calls = om._resolved_launcher_calls(command, entry.get("cwd") or str(REPO))
                for kind, _script, args in calls:
                    value = sidecar_argv.sidecar_flags(args).get("f") if kind == "sidecar" else None
                    if isinstance(value, str) and _same_path(os.path.join(run_cwd, value), prompt):
                        return True
    return False


def _norm(text):
    return " ".join((text or "").split()).lower()


def session_transcript(sid):
    """This session's main transcript: the single `<projects>/<key>/<sid>.jsonl`, else None."""
    if not sid or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid):
        return None
    hits = [p for p in PROJECTS_ROOT.glob("*/%s.jsonl" % sid) if p.is_file()]
    return hits[0] if len(hits) == 1 else None


def owner_quote(why, sid):
    """(ok, evidence): --why is the owner's stop order, verbatim (case/whitespace-folded) from THIS session's
    transcript: an owner-typed message, or the question of an AskUserQuestion the owner answered with a stop
    option. A typed quote passes kill_guard's stop-order test itself: a stop verb, no keep word. Assistant text,
    subagent prompts and tool output never count. `owner_names_target` then requires it to name the target."""
    quote = _norm(why).strip("\"'`“”‘’ ")
    if not quote:
        return False, "--why must quote the owner's stop order, verbatim from their message"
    path = session_transcript(sid)
    inputs = _owner_inputs(str(path)) if path else None
    if inputs is None:
        return False, ("this session's transcript (session %s) is missing or unreadable, so the owner's "
                       "words cannot be verified; ask the owner to stop the process themselves, or prove "
                       "ownership under another --reason" % ((sid or "?")[:8]))
    def stopped(pairs):
        return any(quote in _norm(question) and STOP_LABEL_RE.search(label) and not KEEP_LABEL_RE.search(label)
                   for question, label in pairs)
    typed_order = bool(STOP_LABEL_RE.search(quote)) and not KEEP_LABEL_RE.search(quote)
    if not any((typed_order and quote in _norm(text)) if kind == "text" else stopped(text) for kind, text in inputs):
        return False, ("--why is neither an owner-typed stop order (a stop verb, no keep word) nor the question of "
                       "an AskUserQuestion the owner answered with a stop option, verbatim from this session's "
                       "transcript (case and whitespace are ignored)")
    return True, "owner: %s" % why.strip()[:160]


def orphan_evidence(table, pid):
    """(ok, evidence) for `--reason orphan`."""
    row = table[pid]
    launch = server_launch(row)
    if launch:
        live = [k for k in launch_census(table).get(launch, []) if k in table and not is_server_image(table[k])]
        if live:
            k = live[0]
            return False, "launch %s is still live: pid %d (%s)" % (launch, k, (table[k].get("cmd") or "")[:60])
        return True, "no launcher or seat child of launch %s is alive" % launch
    launch = launch_of(pid)
    if launch and not is_launcher(row):
        live = launchers_of(table, launch, pid)
        if live:
            k = live[0]
            return False, "launcher %d of launch %s is alive (%s)" % (k, launch, (table[k].get("cmd") or "")[:60])
        return True, "no launcher of launch %s is alive" % launch
    ppid = row.get("ppid")
    if isinstance(ppid, int) and ppid in table:
        return False, "parent %d is alive (%s)" % (ppid, (table[ppid].get("cmd") or "")[:60])
    return True, "parent %s is gone" % ppid


def justification(args, table, pid, targets, owner=None, sid=None):
    """(ok, evidence) for the claimed reason, verified mechanically. `owner` is the precomputed
    `owner_quote` verdict, read once per invocation rather than once per target."""
    reason = args.reason
    if reason == "duplicate":
        twin = args.twin
        if twin is None or twin not in table:
            return False, "twin %s is not alive; the work would not continue anywhere" % twin
        if twin in targets:
            return False, "twin %s is itself a target" % twin
        key = (args.key or "").strip()
        if not key or key not in (table[twin].get("cmd") or "") or key not in (table[pid].get("cmd") or ""):
            return False, "--key %r is not in both command lines" % key
        return True, "twin %d alive, both run %r" % (twin, key)
    if reason == "orphan":
        return orphan_evidence(table, pid)
    if reason == "stalled":
        if not args.log or not os.path.isfile(args.log):
            return False, "--log %r is not a file" % args.log
        if args.log.endswith(".stall"):
            # A sidecar launcher's marker: it measures silence without the status heartbeats that keep
            # the stream's mtime fresh, and it deletes the marker when the run speaks again.
            with open(args.log, encoding="utf-8", errors="replace") as fh:
                found = re.search(r"idle=(\d+)", fh.read())
            if not found:
                return False, "%s carries no idle=<seconds>" % os.path.basename(args.log)
            idle = int(found.group(1)) / 60.0
        else:
            idle = (time.time() - os.path.getmtime(args.log)) / 60.0
        if idle < args.idle_min:
            return False, "%s written %.1f min ago (< %d)" % (os.path.basename(args.log), idle, args.idle_min)
        return True, "%s idle %.0f min (>= %d)" % (os.path.basename(args.log), idle, args.idle_min)
    if reason == "superseded":
        if not args.artifact or not os.path.exists(args.artifact):
            return False, "--artifact %r does not exist" % args.artifact
        name = os.path.basename(args.artifact)
        # A job's outputs share its label stem (<label>.record.json, .out.json, .exit, ...), and
        # Windows paths ignore case: a stem in the tree's command lines is the job's own output.
        stem = name.lower().split(".")[0]
        if stem and any(stem in (table[member].get("cmd") or "").replace("\\", "/").lower()
                        for member in [pid] + descendants(table, pid)):
            return False, ("artifact %s is the target's own output, still being written; stop it under "
                           "--reason queued once nothing in its tree is in flight" % name)
        return True, "artifact %s exists" % name
    if reason == "queued":
        return queued_evidence(table, pid)
    if reason == "owner":
        if owner and owner[0]:
            refusal = owner_names_target(args.why, table, pid)
            if refusal is None:
                return owner
        else:
            refusal = owner or (False, "owner quote was not verified")
        if owner_consents(sid, table, pid):
            return True, "owner: their latest reply approved stopping %d" % pid
        return refusal
    return False, "unknown reason"


def queued_evidence(table, pid):
    """(ok, evidence) for `--reason queued`: nothing the job runs is in flight, its detached members included.
    Those are found by the job id its launcher set at spawn, so a target carrying none refuses: its detached
    work cannot be seen."""
    tree = [pid] + descendants(table, pid)
    busy = [k for k in tree if IN_FLIGHT_RE.search("%s %s" % (table[k].get("name") or "", table[k].get("cmd") or ""))]
    if busy:
        return False, ("%s in flight in its tree (%s); wait for that work to end"
                       % (", ".join(map(str, busy)), (table[busy[0]].get("cmd") or "")[:60]))
    block = _environ(pid)
    ids = [(key, _env_value(block, key)) for key in JOB_ID_KEYS if _env_value(block, key)]
    if not ids:
        return False, ("it carries no job id (%s), so work it detached cannot be seen"
                       % ", ".join(k.decode().rstrip("=") for k in JOB_ID_KEYS))
    since = table[pid].get("start") or ""
    members = [k for k, row in table.items() if k not in tree and (row.get("start") or "") >= since
               and any(_env_value(_environ(k), key) == value for key, value in ids)]
    if members:
        return False, ("its detached work is live (%s: %s); wait for it to end"
                       % (", ".join(map(str, members)), (table[members[0]].get("cmd") or "")[:60]))
    return True, "nothing in flight: no model client or launcher in its tree, and no live process carries its job id"


def target_names(table, pid):
    """The names a stop order may call `pid` by: its launch label, and the name of a script or campaign its
    command line or a launching ancestor's runs (a path's file name without extension or a trailing _YYYYMMDD)."""
    label = _launch_label(table, pid)
    members = [pid] + [a for a in ancestors(table, pid) if not never_reason(table[a], table)]
    names = {label} if label != "unknown" else set()
    for k in members:
        for path in re.findall(r"[\w.:~-]*[\\/][^\s\"']+", table[k].get("cmd") or ""):
            stem = os.path.splitext(re.split(r"[\\/]", path)[-1])[0]
            names.update(n for n in (stem, re.sub(r"_\d{8}$", "", stem)) if len(n) >= 6)
    return names


def owner_names_target(why, table, pid):
    """None when the owner's stop order names `pid` or one of its `target_names`; else the refusal. An order
    that names none of them stops some other process."""
    said = why.lower()
    msys = msys_pid_for_winpid(pid)
    if any(re.search(r"(?<!\d)%d(?!\d)" % p, why) for p in [pid] + ([msys] if msys else [])) or any(
            re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(n.lower()), said) for n in target_names(table, pid)):
        return None
    return False, ("the owner's stop order names neither pid %d, its launch label, nor its script or campaign; "
                   "quote the order that names this process" % pid)


def owner_consents(sid, table, pid):
    """True when the owner's latest input approves stopping `pid` (`_owner_text.owner_consent`): a typed "yes"
    to the agent's stop question that named the pid or one of its `target_names` counts, as in the git guards."""
    path = session_transcript(sid)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path else []
    except OSError:
        return False
    msys = msys_pid_for_winpid(pid)
    pids = [str(pid)] + ([str(msys)] if msys else [])
    return bool(lines) and any(owner_consent(lines, [name], STOP_LABEL_RE, KEEP_LABEL_RE)
                               for name in pids + sorted(target_names(table, pid)))


# ----------------------------------------------------------------- act + record
def taskkill(pid, *, tree=True):
    """Kill `pid`. tree=True takes its whole tree; tree=False kills only that process, which is what
    a tree containing never-kill INFRA members needs (see `kill_subtree_sparing_infra`). Success is the
    target gone: `/T` exits nonzero when a member dies before its turn, though the root is killed."""
    cmd = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
        listing = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return listing.returncode == 0 and '"%d"' % int(pid) not in (listing.stdout or "")


def kill_subtree_sparing_infra(table, root, never_pids):
    """Kill root and its descendants EXCEPT the never-kill members and their own subtrees.

    A tree-kill is refused outright when any member is shared INFRA, which is right -- but it also
    means a sidecar whose tree merely CONTAINS an MCP server could never be stopped. Enumerating
    the tree lets both hold: the spared set is every INFRA member plus everything below it, and the
    rest dies deepest-first so no target is orphaned mid-kill. Returns (killed, failed, spared)."""
    tree = [root] + descendants(table, root)
    spared = set()
    for pid in never_pids:
        spared.add(pid)
        spared.update(descendants(table, pid))
    doomed = [p for p in tree if p not in spared]
    depth = {p: len(ancestors(table, p)) for p in doomed}
    killed, failed = [], []
    for pid in sorted(doomed, key=lambda p: -depth.get(p, 0)):
        (killed if taskkill(pid, tree=False) else failed).append(pid)
    return killed, failed, sorted(spared)


def record(entry):
    """Append one ledger row; False when it cannot be written. The kill path refuses on False."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def remember_stop(sid, launch):
    """Add `launch` to this session's `sidecar_stopped_launches`, so the session watcher reads the end of that
    job as the session's own stop. Called only after a kill landed; a failed write warns and keeps the kill."""
    if not sid or launch == "unknown":
        return

    def add(state):
        launches = state.get(STOPPED_LAUNCHES_KEY) or []
        if launch not in launches:
            state[STOPPED_LAUNCHES_KEY] = (launches + [launch])[-50:]  # the sidecar_watchers cap
    written, _ = _hook_state.update_json_locked(_hook_state.state_path(sid), add)
    if not written:
        print("WARN: could not record launch %s as stopped by this session; its end will wake the session" % launch)


def _owned_proof(table, k, session_pid, sid, reason):
    """`k`'s ownership proof; under `--reason orphan`, else its dead-session leftover proof; else None."""
    proof, _foreign = ownership(table, k, session_pid, sid)
    if not proof and reason == "orphan":
        proof = leftover_proof(table, k, sid)
    return proof


def dispatch_servers_of(table, pid, session_pid, sid, tree, reason=None):
    """(launch, [(server pid, proof)]): the owned dispatch servers of the launch `pid` carries, when `pid`
    is not itself one. Servers already inside the target's tree die with it and are not listed."""
    launch = launch_of(pid)
    if not launch or is_server_image(table[pid]):
        return launch, []
    out = []
    for k in launch_census(table).get(launch, []):
        if k in tree or k not in table or not is_server_image(table[k]):
            continue
        proof = _owned_proof(table, k, session_pid, sid, reason)
        if proof:
            out.append((k, proof))
    return launch, out


def seats_of(table, session_pid, sid, tree, reason=None):
    """[(seat pid, launch, proof)]: the owned detached seats of every launch the target's tree carries. A
    launcher detaches its seat, so the seat sits outside the launcher's tree and taskkill /T never reaches
    it. A seat carries SIDECAR_SEAT_LAUNCH_ID=X; only the topmost carrier of each seat is listed."""
    launches = set()
    for k in tree:
        if k in table and not is_server_image(table[k]):
            block = launch_block(k)
            x = _env_value(_ids_block(k, block) if is_launcher(table[k]) else block, LAUNCH_ENV)
            if x and _env_value(launch_block(k), SEAT_ENV) != x:
                launches.add(x)
    census = launch_census(table)
    found = {k: x for x in launches for k in census.get(x, [])
             if k in table and k not in tree and not is_launcher(table[k]) and not is_server_image(table[k])
             and _env_value(launch_block(k), SEAT_ENV) == x}
    out = []
    for k in sorted(found):
        if set(ancestors(table, k)) & set(found):
            continue
        proof = _owned_proof(table, k, session_pid, sid, reason)
        if proof:
            out.append((k, found[k], proof))
    return out


def _reap_stray(table, k, pid, label, proof, kill, base, args, **fields):
    """Kill (or dry-run) one stray of the launch `pid` carries, on the record; returns 1 on failure, else 0.
    The 'killing' row is written before `kill()` runs, and an unwritable ledger refuses the kill."""
    head = "%d %s :: %s" % (k, table[k].get("name"), (table[k].get("cmd") or "")[:110])
    entry = dict(base, pid=k, name=table[k].get("name"), cmd=(table[k].get("cmd") or "")[:400], proof=proof,
                 evidence="%s, whose process %d is being reaped" % (label, pid), **fields)
    if args.dry_run:
        print("DRY %s (%s)" % (head, label))
        entry["result"] = "dry-run"; record(entry)
        return 0
    entry["result"] = "killing"
    if not record(entry):
        print("REFUSED %s (%s)\n   LEDGER unwritable: %s" % (head, label, LEDGER))
        return 1
    done = kill()
    print("%s %s (%s)" % ("KILLED" if done else "FAILED", head, label))
    entry["result"] = "killed" if done else "kill-failed"; record(entry)
    return 0 if done else 1


def launch_strays(table, pid, session_pid, sid, tree, reason):
    """[(stray pid, label, proof, kill, ledger fields)]: the detached seats and dispatch servers of the launch
    `pid`'s tree carries. Found BEFORE the target dies: a dead launcher's environment names no launch.
    A seat's never-kill members (its MCP servers) are spared, as inside a target's own tree, and a never-kill
    topmost carrier (an MCP server that outlived its seat) is no seat at all."""
    strays = []
    for k, launch, proof in seats_of(table, session_pid, sid, tree, reason):
        if never_reason(table[k], table):
            continue
        sub = [k] + descendants(table, k)
        never = [m for m in sub if m != k and never_reason(table[m], table)]

        def kill(k=k, never=never):
            if not never:
                return taskkill(k)
            killed, bad, _spared = kill_subtree_sparing_infra(table, k, never)
            return k in killed and not bad
        strays.append((k, "seat of launch %s" % launch, proof, kill, {"tree": sub, "spared_never": never}))
    launch, servers = dispatch_servers_of(table, pid, session_pid, sid, tree, reason)
    for k, proof in servers:
        strays.append((k, "dispatch server of launch %s" % launch, proof,
                       lambda k=k: taskkill(k, tree=False), {"tree": [k]}))
    return strays


def reap_strays(table, pid, strays, args, base):
    """Kill (or dry-run) each of `launch_strays`'s strays; returns the failure count."""
    return sum(_reap_stray(table, k, pid, label, proof, kill, base, args, **fields)
               for k, label, proof, kill, fields in strays)


def main(argv=None, env=None):
    global _RUN
    _RUN = {"env": {}, "launch": {}, "census": None}
    try:
        return _main(argv, env)
    finally:
        _RUN = None


def live_launches(table, prompt=None, resume=None, self_pid=None, launch=None, fanout=None, label=None, record=None):
    """[(pid, what)] for each live launcher or seat child already running `prompt` as this job (`label` or
    `record`; neither means the prompt alone) or resuming `resume` (module docstring, `live-launch`)."""
    record = _resolve(record) if record else None
    mine = {os.getpid()} | set(ancestors(table, os.getpid()))
    if self_pid in table:
        mine |= {self_pid} | set(ancestors(table, self_pid)) | set(descendants(table, self_pid))
    hits = []
    for pid in sorted(table):
        row = table[pid]
        if pid in mine:
            continue
        block = launch_block(pid)
        cmd = row.get("cmd") or ""
        if is_launcher(row):
            flags = sidecar_argv.sidecar_flags(launcher_args(cmd))
            path, session = flags.get("f"), flags.get("r")
            theirs, their_record = flags.get("l"), flags.get("R")
        elif (row.get("name") or "").lower().startswith("claude") and re.search(r"\s-p(?:\s|$)", cmd):
            path = _env_value(block, PROMPT_ENV)
            found = SEAT_RESUME.search(cmd)
            session = found.group(1) if found else None
            theirs, their_record = _env_value(block, LABEL_ENV), None
        else:
            continue
        same_job = (not (label or record)
                    or bool(label and theirs == label)
                    or bool(record and their_record and _same_path(_resolve(their_record), record)))
        if prompt and same_job and isinstance(path, str) and _same_path(_resolve(path), prompt):
            what = "runs prompt %s" % path
        elif resume and session == resume:
            what = "resumes session %s" % resume
        else:
            continue
        ids = _ids_block(pid, block)
        if (launch and _env_value(ids, LAUNCH_ENV) == launch) or (fanout and _env_value(ids, FANOUT_ENV) == fanout):
            continue
        # Tool wrappers predate the id export; a reused record must not exempt an actual launcher.
        if (launch and is_launcher(row) and re.match(r'^(?:"[^"]+"|\S+)\s+-c(?:\s|$)', cmd)
                and _launch_label(table, pid) == launch):
            continue
        # The table is a snapshot taken seconds ago: a launcher's command-substitution fork carries the
        # launcher's command line but exits within moments, and an exiting process's environment reads
        # empty, so it would not show it a sibling. A process that has exited is no live launch.
        if process_exited(pid, row.get("start")):
            continue
        hits.append((pid, what))
    return hits


def _ids_block(pid, block):
    """`block` when it carries a launch id, else the process's MSYS block: a snapshot re-exec's PEB is bare
    (launch_environ). Read only for a matching process, so the per-process bash spawn stays rare."""
    if _env_value(block, LAUNCH_ENV) or _env_value(block, FANOUT_ENV):
        return block
    return msys_environ(pid) or block


def launcher_args(cmd):
    """The arguments after the `*_sidecar.sh` token of a launcher's command line."""
    tokens = [tok.strip('"') for tok in re.findall(r'"[^"]*"|\S+', cmd or "")]
    for index, tok in enumerate(tokens):
        if LAUNCHER_SCRIPT.search(tok):
            return tokens[index + 1:]
    return []


def _age(row):
    try:
        started = datetime.datetime.strptime(row.get("start") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "?"
    minutes = max(0, int((datetime.datetime.now() - started).total_seconds() // 60))
    return "%dm" % minutes if minutes < 120 else "%dh%02dm" % divmod(minutes, 60)


def _launch_label(table, pid):
    """The process's launch id, else the one its launcher's `-R` record names in `<record>.pid`."""
    launch = launch_of(pid)
    if launch or not is_launcher(table[pid]):
        return launch or "unknown"
    record = sidecar_argv.sidecar_flags(launcher_args(table[pid].get("cmd"))).get("R")
    try:
        with open(_resolve(record) + ".pid", encoding="utf-8", errors="replace") as fh:
            found = re.findall(r"\blaunch=(\S+)", fh.read())
    except (OSError, TypeError):
        found = []
    return found[-1] if found else "unknown"


def describe_launches(table, hits):
    """Each live PID with its launch id and age, then one reap command per process tree root."""
    pids = [pid for pid, _ in hits]
    lines = ["  pid=%d launch=%s age=%s %s (%s) :: %s" % (pid, _launch_label(table, pid), _age(table[pid]),
                                                        table[pid].get("name"), what, (table[pid].get("cmd") or "")[:140])
             for pid, what in hits]
    roots = [pid for pid in pids if not any(a in pids for a in ancestors(table, pid))]
    for pid in roots:
        ok, _evidence = orphan_evidence(table, pid)
        lines.append("  stop: python3 .claude/tools/reap.py %d %s" % (
            pid, '--reason orphan --why "a relaunch replaces this run"' if ok
            else "--reason queued once nothing it runs is in flight, else ask the owner a question naming it"))
    return "\n".join(lines)


def live_launch_main(argv):
    ap = argparse.ArgumentParser(prog="reap.py live-launch")
    ap.add_argument("--prompt")
    ap.add_argument("--resume")
    ap.add_argument("--self", type=int, dest="self_pid")
    ap.add_argument("--launch")
    ap.add_argument("--fanout")
    ap.add_argument("--label")
    ap.add_argument("--record")
    args = ap.parse_args(argv)
    if not args.prompt and not args.resume:
        print("live-launch needs --prompt or --resume", file=sys.stderr)
        return 2
    table = process_table()
    if not table:
        print("[sidecar] WARN: process table unreadable; the duplicate-launch check is skipped", file=sys.stderr)
        return 4
    hits = live_launches(table, args.prompt, args.resume, args.self_pid, args.launch, args.fanout,
                         args.label, args.record)
    if not hits:
        return 0
    subject = ("prompt %s%s" % (args.prompt, " as job %s" % (args.label or args.record) if args.label or args.record else "")
               if args.prompt else "session %s" % args.resume)
    print("DUPLICATE LAUNCH refused: a live launch already runs %s.\n%s\n"
          "Stop the previous attempt's whole tree (fan-out driver, launchers, seat, servers), confirm none "
          "remain, then relaunch." % (subject, describe_launches(table, hits)))
    return 3


def _main(argv=None, env=None):
    env = os.environ if env is None else env
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "live-launch":
        return live_launch_main(argv[1:])
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pids", nargs="+", type=int, help="Windows or MSYS pids")
    ap.add_argument("--reason", required=True, choices=REASONS)
    ap.add_argument("--why", default="", help="one line for the ledger; for --reason owner, the owner's stop order or the question they answered with a stop option, verbatim (their latest reply approving the stop needs no quote)")
    ap.add_argument("--twin", type=int, help="duplicate: the surviving copy")
    ap.add_argument("--key", help="duplicate: text both command lines carry (script + campaign, or the cell)")
    ap.add_argument("--log", help="stalled: the log the target should be writing")
    ap.add_argument("--idle-min", type=int, default=30, help="stalled: minutes of silence that qualify")
    ap.add_argument("--artifact", help="superseded: the output that already exists")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    session_pid = int(env.get("CLAUDE_PID") or 0) or None
    sid = env.get("CLAUDE_CODE_SESSION_ID") or None
    table = process_table()
    if not table:
        print("REFUSED: process table unreadable — nothing is killed on a guess")
        return 2
    link_msys_parents(table, ps_text())

    targets = []
    for raw in args.pids:
        pid = resolve_pid(raw, table)
        if pid is None:
            print("REFUSED %s: not in the process table — already gone, or a stale number; re-read the list" % raw)
            record({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "session": sid, "pid": raw,
                    "reason": args.reason, "why": args.why, "result": "refused", "evidence": "not in table"})
            continue
        targets.append(pid)

    refused = len(args.pids) - len(targets)
    # One kill per tree: a repeated pid, or a target inside another target's tree, is covered by the root.
    unique = list(dict.fromkeys(targets))
    covered = {t for t in unique if any(a in unique for a in ancestors(table, t))}
    for pid in covered:
        print("COVERED %d: inside another target's tree; killed with it" % pid)
    targets = [t for t in unique if t not in covered]
    owner = owner_quote(args.why, sid) if args.reason == "owner" and targets else None
    for pid in targets:
        row = table[pid]
        head = "%d %s :: %s" % (pid, row.get("name"), (row.get("cmd") or "")[:110])
        proof, foreign = ownership(table, pid, session_pid, sid)
        ok, evidence = justification(args, table, pid, set(targets), owner=owner, sid=sid)
        tree = [pid] + descendants(table, pid)
        # The NEVER gate is judged on every member, but a member that may never die is SPARED rather
        # than the whole kill being refused: refusing made any sidecar whose tree merely contains an
        # MCP server impossible to stop, which is how two hung codex shells survived a stop order.
        root_never = never_reason(table[pid], table)
        never = [(k, never_reason(table[k], table)) for k in tree if k != pid and never_reason(table[k], table)]
        entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(), "session": sid, "pid": pid,
                 "name": row.get("name"), "cmd": (row.get("cmd") or "")[:400], "reason": args.reason,
                 "why": args.why, "proof": proof, "evidence": evidence, "tree": tree,
                 "spared_never": [k for k, _ in never]}
        # A never-kill member that is not the ROOT is spared and the rest dies (below). A never-kill
        # ROOT is refused outright: sparing it and killing its children would destroy a server other
        # sessions are using, and reporting that as "FAILED" reads as a broken kill, not a refusal.
        if root_never:
            print("REFUSED %s\n   NEVER: %s — not a target, and its own children are not ours to kill" % (head, root_never))
            entry["result"] = "refused-never"; record(entry); refused += 1
            continue
        # `owner` is the one reason that may STAND IN for the ownership proof. That proof exists to
        # establish that someone authorised this kill when no human is in the loop; a human naming
        # the target is that authorisation. Gated behind it, the reason was dead code -- anything we
        # already own never needs the owner's words, and anything we do not own was refused before
        # the reason was read. It does NOT reach the two hard boundaries, both judged above and
        # below it: a never-kill target, and a process whose own environment names a DIFFERENT
        # session, which is that session's to stop.
        if args.reason == "owner" and ok and not proof and not foreign:
            proof = "owner-instruction: %s" % args.why.strip()[:160]
            entry["proof"] = proof
        # A dead session's leftover: no session can consume its result, so any session may stop it.
        if args.reason == "orphan" and ok and not proof:
            leftover = leftover_proof(table, pid, sid)
            if leftover:
                proof, foreign = leftover, None
                entry["proof"] = proof
        if not proof:
            print("REFUSED %s\n   %s" % (head, foreign or
                  "NOT OWNED: no ancestor is this session's claude.exe (%s), its environment does not "
                  "name session %s, and no dispatch record does either%s"
                  % (session_pid, (sid or "?")[:8],
                     "\n   OWNER: %s" % evidence if args.reason == "owner" and not ok else "")))
            entry["result"] = "refused-foreign" if foreign else "refused-not-owned"
            record(entry); refused += 1
            continue
        if not ok:
            print("REFUSED %s\n   %s: %s" % (head, args.reason.upper(), evidence))
            entry["result"] = "refused-unjustified"; record(entry); refused += 1
            continue
        base = {k: entry[k] for k in ("ts", "session", "reason", "why")}
        # Read before the kill: a dead launcher's environment names no launch.
        stop_launch = _launch_label(table, pid)
        strays = launch_strays(table, pid, session_pid, sid, tree, args.reason)
        if args.dry_run:
            print("DRY %s\n   owned: %s\n   %s: %s\n   tree: %s" % (head, proof, args.reason, evidence, tree))
            entry["result"] = "dry-run"; record(entry)
            reap_strays(table, pid, strays, args, base)
            continue
        # The row is written BEFORE the kill: no ledger, no kill.
        entry["result"] = "killing"
        if not record(entry):
            print("REFUSED %s\n   LEDGER unwritable: %s" % (head, LEDGER))
            refused += 1
            continue
        if never:
            killed, failed, spared = kill_subtree_sparing_infra(table, pid, [k for k, _ in never])
            print("%s %s\n   owned: %s\n   %s: %s\n   killed: %s\n   spared (never-kill): %s"
                  % ("PARTIAL" if (killed and failed) else "KILLED" if killed else "FAILED",
                     head, proof, args.reason, evidence, sorted(killed), spared))
            entry["result"] = "partial" if (killed and failed) else "killed" if killed else "kill-failed"
            entry["killed"], entry["spared"] = sorted(killed), spared
            record(entry)
            if pid in killed:
                remember_stop(sid, stop_launch)
            if not killed or failed:
                refused += 1
            if pid in killed:
                refused += reap_strays(table, pid, strays, args, base)
            continue
        done = taskkill(pid)
        if done:
            for k in outside_windows_tree(table, pid, tree):
                done = taskkill(k, tree=False) and done
        print("%s %s\n   owned: %s\n   %s: %s\n   tree: %s" % ("KILLED" if done else "FAILED", head, proof, args.reason, evidence, tree))
        entry["result"] = "killed" if done else "kill-failed"; record(entry)
        if not done:
            refused += 1
        else:
            remember_stop(sid, stop_launch)
            refused += reap_strays(table, pid, strays, args, base)
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
