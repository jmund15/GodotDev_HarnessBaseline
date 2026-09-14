#!/usr/bin/env python3
"""Resolve a PID to its real command line BEFORE killing it, and refuse the harness-critical ones.

WHY THIS EXISTS: `shell_census.py` lists long-running shells with their command tails TRUNCATED, and
its own text hands over `taskkill /PID <pid> /T /F`. Measured 2026-09-08: a session read that list,
meant to kill a stray home-directory `grep`, matched the wrong row, and killed its own
`sidecar_fanout.py` run mid-flight -- destroying a paid comparison arm. Nothing checked what the PID
actually was, because nothing ever resolves it.

A kill is irreversible and the display is lossy, so this gate fails CLOSED (instruction_quality
section 16): an unresolvable PID is refused rather than assumed harmless. The refusal always PRINTS
the resolved command line -- seeing "sidecar_fanout.py" where you expected "grep" is the whole point.

Bypass, per target: `HARNESS_ALLOW_KILL=<pid> taskkill /PID <pid> /T /F` (inline prefix). Naming the pid
twice is deliberate -- it cannot be satisfied by reflex, only by having looked.
"""
import json
import os
import re
import subprocess
import sys

# Killing any of these takes down work that cannot be resumed from where it stopped: a billed
# sidecar arm, a gate/suite run, or a peer's whole session.
PROTECTED = (
    (r"sidecar_fanout\.py", "a sidecar fan-out — its in-flight arms are billed and unresumable"),
    (r"_sidecar\.sh|sidecar_launch\.py", "a sidecar launcher child — the arm dies with it"),
    (r"harness_tests\.py", "the harness proof runner"),
    (r"regression_gate|gate_chain|verify\.ps1", "a gate/verification run"),
    (r"vstest|dotnet\s+test", "a test run"),
    # NOT \bclaude\b — that matches `.claude/` (present in nearly every harness command) and
    # `claude-gpt`. Match the EXECUTABLE: `claude.exe`, or `claude` standing as the command word.
    (r"(?:^|[\s/\\\"])claude\.exe|(?:^|[\s/\\\"])claude(?:\s|$)",
     "a Claude Code session — possibly a peer's"),
    (r"claude-code-proxy", "a transport proxy other sessions may be routing through"),
)

PID_RE = re.compile(r"(?:/PID|/pid|-PID)\s+(\d+)|(?:^|\s|;|&&|\|)kill\s+(?:-\w+\s+)?(\d+)\b")


def pids_in(cmd):
    out = []
    for m in PID_RE.finditer(cmd):
        out.append(m.group(1) or m.group(2))
    return [p for p in out if p]


def commandline(pid):
    """The process's full command line, or None. None is NOT 'harmless' — the caller fails closed."""
    ps = ("$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%s' -ErrorAction SilentlyContinue;"
          " if ($p) { $p.CommandLine + '|' + $p.Name }" % pid)
    for exe in ("pwsh", "powershell"):
        try:
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, timeout=8)
        except Exception:
            continue
        if r.returncode == 0:
            line = (r.stdout or "").strip()
            return line or ""          # "" = resolved, and the process is gone
    return None                        # could not resolve at all


def verdict(cmd, env):
    if "taskkill" not in cmd and not re.search(r"(^|[\s;&|])kill\s", cmd):
        return None
    targets = pids_in(cmd)
    if not targets:
        return None

    allowed = {p.strip() for p in (env.get("HARNESS_ALLOW_KILL") or "").replace(",", " ").split() if p}
    problems = []
    for pid in targets:
        if pid in allowed:
            continue
        line = commandline(pid)
        if line is None:
            problems.append("  PID %s: could not resolve. A kill is irreversible and an unread "
                            "PID is an unread target." % pid)
            continue
        if line == "":
            problems.append("  PID %s: no such process — it already exited, or the number is "
                            "stale. Re-read the list before killing." % pid)
            continue
        for pat, why in PROTECTED:
            if re.search(pat, line, re.I):
                problems.append("  PID %s is %s\n      %s" % (pid, why, line[:160]))
                break
    if not problems:
        return None
    return ("BLOCKED kill — the target is not what a truncated shell list shows.\n"
            + "\n".join(problems)
            + "\n\nResolve it yourself first:\n"
              "  pwsh -NoProfile -Command \"(Get-CimInstance Win32_Process -Filter "
              "'ProcessId=<pid>').CommandLine\"\n"
              "A Bash-tool background task is NOT an OS process to taskkill — stop it with "
              "KillShell/TaskStop by its task id.\n"
              "Deliberate: prefix `HARNESS_ALLOW_KILL=<pid>` (naming the pid a second time).")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    inline = dict(os.environ)
    for m in re.finditer(r"HARNESS_ALLOW_KILL=(\S+)", cmd):
        inline["HARNESS_ALLOW_KILL"] = inline.get("HARNESS_ALLOW_KILL", "") + " " + m.group(1)
    why = verdict(cmd, inline)
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
