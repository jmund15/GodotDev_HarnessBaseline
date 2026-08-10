#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — warn when the target script has a LIVE process.

Fully domain-agnostic —
fully domain-agnostic, and directly relevant here: this project's
`scripts/deepseek_sidecar.sh` is exactly the kind of long-running interpreted
script this hook protects.

Why:
- `bash` reads a script lazily, byte-offset by byte-offset, as it executes. Editing
  a running .sh shifts every offset after the edit point, so the live instance
  resumes mid-token and executes garbage. This has corrupted two in-flight
  sidecar dispatches from a mid-run edit to the same `deepseek_sidecar.sh` this
  project copied.
- The rule is memorized (`auto-memory/archive/gotcha_editing_shell_script_corrupts_running_instances.md`)
  and still gets violated under a routine-looking edit — recall alone doesn't
  survive that. This converts it into a call-time nudge; the memory file stays
  canonical, this hook only enforces and cites it.

What it does:
- Gates on file_path: only `.claude/scripts/**` with a `.sh` / `.ps1` suffix.
  Everything else exits immediately with no process scan.
- On a match, scans live processes for a command line referencing that script,
  EXCLUDING this hook's own parent chain (an ancestor shell often merely mentions
  the path — grep, echo, the Edit call's own wrapper — which a substring scan
  cannot tell from executing it), and emits a hookSpecificOutput.additionalContext
  WARN naming the PIDs.
- Never blocks. The correct action depends on intent — kill the run, or
  copy-then-edit and swap — so this advises rather than decides.

No dedupe by design: the hazard is per-edit, not per-session. The gate keeps the
cost at zero for every edit outside `.claude/scripts/`.

Wired in: settings.json hooks.PreToolUse with matcher "Write|Edit".
"""

import json
import os
import subprocess
import sys

SCRIPT_DIR_MARKER = "/.claude/scripts/"
GUARDED_SUFFIXES = (".sh", ".ps1")

# Must stay below this hook's settings.json timeout (10s).
SCAN_TIMEOUT = 5


def is_guarded_script(file_path: str) -> bool:
    """True for .claude/scripts/**.{sh,ps1}. Separators normalized — Write/Edit
    may deliver either on Windows."""
    if not file_path:
        return False
    norm = file_path.replace("\\", "/")
    if not norm.endswith(GUARDED_SUFFIXES):
        return False
    return SCRIPT_DIR_MARKER in norm or norm.startswith(".claude/scripts/")


def _scan_command() -> list:
    """Platform-appropriate 'dump pid|ppid|command line for every process'."""
    if sys.platform == "win32":
        return [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_Process | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)|$($_.CommandLine)\" }",
        ]
    return ["ps", "-eo", "pid=,ppid=,args="]


def _parse_processes(stdout: str) -> dict:
    """Return {pid: (ppid, cmdline)}. Tolerates both output shapes."""
    procs = {}
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped:                      # Windows: pid|ppid|cmdline
            parts = stripped.split("|", 2)
        else:                                    # POSIX: pid ppid args...
            parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not pid.isdigit() or not ppid.isdigit():
            continue
        procs[pid] = (ppid, cmd)
    return procs


def _ancestor_pids(procs: dict, start_pid: str) -> set:
    """PIDs on this process's parent chain, inclusive.

    A process that would be corrupted by the edit is never an ancestor of this
    hook — the hook runs inside the Edit call, while a live script instance was
    launched by an earlier, unrelated shell. Our own ancestors, by contrast,
    routinely MENTION the script name (a Bash tool call that greps or echoes the
    path), which a substring scan cannot distinguish from executing it.
    """
    chain = set()
    pid = start_pid
    for _ in range(32):                          # cycle/corruption guard
        if pid in chain or pid not in procs:
            break
        chain.add(pid)
        pid = procs[pid][0]
    return chain


def find_live_instances(basename: str) -> list:
    """Return ['<pid> <command line>', ...] for processes referencing basename.

    Best-effort: returns [] on any failure so the hook stays silent rather than
    warning about a scan it could not perform.
    """
    try:
        proc = subprocess.run(
            _scan_command(),
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []

    procs = _parse_processes(proc.stdout)
    excluded = _ancestor_pids(procs, str(os.getpid()))
    needle = basename.lower()

    hits = []
    for pid, (_ppid, cmd) in procs.items():
        if pid in excluded or needle not in cmd.lower():
            continue
        hits.append(f"{pid} {cmd}"[:200])
    return hits[:5]


def build_warning(basename: str, hits: list) -> str:
    listed = "\n".join(f"  • {h}" for h in hits)
    return (
        f"⚠ RUNNING INSTANCE DETECTED — `{basename}` appears in {len(hits)} live "
        "process command line(s):\n"
        f"{listed}\n"
        "\n"
        "bash reads a script lazily as it executes, so an in-place edit shifts every "
        "byte offset after the edit point and the running instance resumes mid-token. "
        "Either kill the run first, or copy-then-edit (edit a copy, swap it in once "
        "the run finishes).\n"
        "Canon: `auto-memory/archive/gotcha_editing_shell_script_corrupts_running_instances.md`."
    )


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    if not is_guarded_script(file_path):
        sys.exit(0)

    basename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    hits = find_live_instances(basename)
    if not hits:
        sys.exit(0)

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": build_warning(basename, hits),
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory hook: never block a script edit because the guard broke.
        sys.exit(0)
