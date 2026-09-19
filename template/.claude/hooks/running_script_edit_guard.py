#!/usr/bin/env python3
"""
Hook: PreToolUse on Write|Edit — warn when the target script has a LIVE process.

Why:
- `bash` reads a script lazily, byte-offset by byte-offset, as it executes. Editing
  a running .sh shifts every offset after the edit point, so the live instance
  resumes mid-token and executes garbage. Observed 2026-08-04: two in-flight
  sidecar lenses corrupted by an edit to `deepseek_sidecar.sh` — the canonical
  instance of the hazard, and the highest-traffic script here, but the mechanism
  is generic to every `.sh` under `.claude/scripts/` and the guard treats them
  alike. Sanctioned fixes are (a) kill the whole subtree first, or (c) version the
  filename and point only NEW dispatches at it. Moving a copy over the live path
  is NOT a fix: it replaces the same bytes the running shell is still reading.
- SCOPE, since 2026-09-08: launchers that call `sc_reexec_snapshot` (anthropic, codex_proxy,
  deepseek) execute a $TEMP copy named `<script>.sh.<pid>.sh`, so their source file is no longer
  corruptible AND no live command line names it — this guard deliberately does not fire for them.
  It covers every other `.claude/scripts/**.sh`, including launchers that have not adopted the
  snapshot re-exec (opencode_sidecar.sh).
- The rule was already memorized (`gotcha_editing_shell_script_corrupts_running_instances.md`)
  and still violated — recall alone does not survive a routine-looking edit.
  This converts it into a call-time nudge. The rule's canonical home stays the
  memory file; this hook only enforces and cites it.

What it does:
- Gates on file_path: only `.claude/scripts/**` with a `.sh` / `.ps1` suffix.
  Everything else exits immediately with no process scan.
- On a match, scans live processes for a command line that INVOKES that script
  (`bash <path> …`, `timeout N bash <path> …`, or the path run directly) — never
  one that merely names it: a `grep`/`cat`/`git` argument, a path inside a
  `bash -c "…"` wrapper string (a Claude Bash tool shell; its real child is
  scanned on its own), or this hook's own parent chain (a corruptible instance
  is never an ancestor). Matching the noun warned on peers' greps and on
  wrappers whose child had already exited (measured 2026-09-08, twice).
  Survivors are re-probed for liveness once — a snapshot row can be a process
  that finished between the scan and the warning — and the hook emits a
  hookSpecificOutput.additionalContext WARN naming the PIDs. Per the verified
  channel matrix, additionalContext is the ONLY model-visible advisory channel
  on PreToolUse (stderr on an exit-0 PreToolUse path is a dead channel).
- Never blocks. The correct action depends on intent — kill the run, or
  copy-then-edit and swap — so this advises rather than decides.

No dedupe by design: the hazard is per-edit, not per-session. The gate keeps the
cost at zero for every edit outside `.claude/scripts/`.

Boundaries:
- Always exits 0. Any scan failure, timeout, or malformed input exits 0 silently
  (advisory hooks fail open; only enforcement gates may fail closed).
- SCAN_TIMEOUT fits inside the settings.json timeout for this hook.

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
    Excluding the chain removes that entire false-positive class.
    """
    chain = set()
    pid = start_pid
    for _ in range(32):                          # cycle/corruption guard
        if pid in chain or pid not in procs:
            break
        chain.add(pid)
        pid = procs[pid][0]
    return chain


# Programs that READ a script without executing it: a command line whose program is one of
# these names the script as a noun (harness_tooling.md §A guard matches the ACTION, never the noun).
NOUN_TOOLS = frozenset({
    "grep", "rg", "cat", "sed", "awk", "wc", "head", "tail", "echo", "git", "less", "more",
    "type", "findstr", "diff", "ls", "find", "stat", "file", "cp", "mv", "python", "python3",
    "node", "code", "select-string", "get-content",
})
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "pwsh", "powershell"})
# Prefix programs that hand off to whatever follows (`timeout 600 bash x.sh`).
PASS_THROUGH = frozenset({"timeout", "env", "nohup", "time", "exec", "nice"})


def _program(token: str) -> str:
    return token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")


def _split(cmd: str) -> list:
    try:
        import shlex
        return shlex.split(cmd, posix=False)
    except ValueError:
        return cmd.split()


def _is_invocation(cmd: str, basename: str) -> bool:
    """True only when the command line RUNS the script: the script is a shell's script argument,
    or the program itself. A `-c`/`-Command` string wrapper is not an invocation (its child is a
    separate process and is judged on its own row); an argument to a noun tool is not either."""
    needle = basename.lower()
    if needle not in cmd.lower():
        return False
    toks = _split(cmd)
    for i, tok in enumerate(toks):
        prog = _program(tok)
        if prog in NOUN_TOOLS:
            return False
        if prog in PASS_THROUGH:
            continue
        if prog in SHELLS:
            for rest in toks[i + 1:]:
                if rest.lower() in ("-c", "-command"):
                    return False
                if _program(rest).endswith(needle):
                    return True
            return False
        if prog.endswith(needle):
            return True
        # Anything else in program position with the script only as an argument is a mention.
        if not tok.startswith("-") and not tok.replace(".", "").isdigit():
            return False
    return False


def _default_scan() -> str:
    proc = subprocess.run(
        _scan_command(),
        capture_output=True,
        text=True,
        timeout=SCAN_TIMEOUT,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout or ""


def _default_alive(pids: list) -> set:
    """PIDs from `pids` that still exist. A probe failure keeps every hit (advisory: warn rather
    than hide), which is why this returns the whole input on error."""
    try:
        if sys.platform == "win32":
            ids = ",".join(pids)
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"Get-Process -Id {ids} -ErrorAction SilentlyContinue | ForEach-Object {{ $_.Id }}"],
                capture_output=True, text=True, timeout=SCAN_TIMEOUT, encoding="utf-8", errors="replace",
            )
            return {line.strip() for line in (proc.stdout or "").splitlines() if line.strip().isdigit()}
        alive = set()
        for pid in pids:
            try:
                os.kill(int(pid), 0)
                alive.add(pid)
            except (OSError, ValueError):
                pass
        return alive
    except (OSError, subprocess.SubprocessError):
        return set(pids)


def find_live_instances(basename: str, scan=None, alive=None) -> list:
    """Return ['<pid> <command line>', ...] for processes INVOKING basename that are still alive.

    `scan` returns the process-table text; `alive` maps candidate pids to the subset still
    running — both injectable so the proof runs against a planted table. Best-effort: returns []
    on a scan failure so the hook stays silent rather than warning about a scan it could not perform.
    """
    try:
        table = (scan or _default_scan)()
    except (OSError, subprocess.SubprocessError):
        return []

    procs = _parse_processes(table)
    excluded = _ancestor_pids(procs, str(os.getpid()))

    candidates = [(pid, cmd) for pid, (_ppid, cmd) in procs.items()
                  if pid not in excluded and _is_invocation(cmd, basename)]
    if not candidates:
        return []
    still = (alive or _default_alive)([pid for pid, _ in candidates])
    return [f"{pid} {cmd}"[:200] for pid, cmd in candidates if pid in still][:5]


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
        "Canon: `gotcha_editing_shell_script_corrupts_running_instances.md`."
    )


def process(input_data, scan=None, alive=None):
    """Dispatcher entry: `{"context": warning}` when a live instance exists, else None.

    `scan`/`alive` stay injectable for the proof. `main()` keeps the standalone channel.
    """
    if input_data.get("tool_name") not in ("Write", "Edit"):
        return None

    file_path = (input_data.get("tool_input") or {}).get("file_path") or ""
    if not is_guarded_script(file_path):
        return None

    basename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    hits = find_live_instances(basename, scan=scan, alive=alive)
    if not hits:
        return None

    return {"context": build_warning(basename, hits)}


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    result = process(input_data) or {}
    if result.get("context"):
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": result["context"],
            }
        }))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Advisory hook: never block a script edit because the guard broke.
        sys.exit(0)
