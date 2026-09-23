#!/usr/bin/env python3
"""reap.py — stop a process THIS session owns, on proof, with a reason, on the record.

    python3 .claude/tools/reap.py <pid> [<pid> ...] --reason <class> --why "<one line>" [class evidence] [--dry-run]

A raw kill cannot show WHY it is justified, so the classifier denies it. This tool can, and refuses when
it cannot (incident: auto-memory/archive/feedback_guard_denial_of_justified_work_fix_the_guard.md):

  ownership   any one of: the target descends from this session's claude.exe (CLAUDE_PID); or its
              OWN environment names this session's CLAUDE_CODE_SESSION_ID, which is the proof that
              survives the launcher wrapper being killed and so covers the hung-run case; or, only
              when the environment is unreadable, its command line names a dispatch record whose
              parentSessionId is this session. A process whose environment names a DIFFERENT session
              is refused outright, record or no record. A peer's process with an identical command
              line is NOT OWNED and is never killed.
  never       a session host (claude.exe --session-id / --bg-pty-host), an MCP server, a proxy.
              A never-kill member INSIDE a target's tree is spared and the rest of the tree dies;
              a never-kill TARGET is refused outright, because its children are its own.
  reason      one closed class, each verified from evidence, not from prose:
                duplicate   --twin <pid> alive and sharing --key <text> in its command line: the
                            work continues there, so this copy is pure waste
                orphan      the target's parent is gone from the process table
                stalled     --log <path> untouched for --idle-min minutes (default 30)
                superseded  --artifact <path> exists: the output the target is producing is already on disk
                owner       --why quotes the owner's stop instruction. The only prose-backed class,
                            and the only one that SUBSTITUTES for the ownership proof above: the
                            proof stands in for a human's authorisation, so a human naming the
                            target outranks it. It does not reach a never-kill target or one whose
                            own environment names a different session.
  ledger      every kill (and every refusal) is appended to .claude/logs/reap_ledger.jsonl.

The kill is `taskkill /PID <root> /T /F` — the whole tree, so a sidecar's headless claude child dies
with it. Exit 0 = every target killed (or dry-run); 2 = at least one target refused (nothing partial
is hidden: refused targets are listed, killed ones are killed).

MSYS pids (what Git Bash `kill` and `ps` print) are mapped to Windows pids through `ps -W`.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / ".claude" / "logs" / "reap_ledger.jsonl"
REASONS = ("duplicate", "orphan", "stalled", "superseded", "owner")

# Never a target, whoever owns it: a session host, a background pty host, an MCP server, a transport proxy.
NEVER = (
    (re.compile(r"claude(?:\.exe)?\"?\s+(?:.*\s)?--(?:session-id|bg-pty-host)\b", re.I), "a Claude Code session HOST"),
    (re.compile(r"claude(?:\.exe)?\"?\s*$", re.I), "an interactive Claude Code session HOST"),
    # Executable + server name in any argument (flags may precede the script), not a bare word: `-u ENABLE_LSP_TOOL`
    # sits in every sidecar's env cleanup.
    (re.compile(r"(?:node|python3?|pwsh|dotnet)(?:\.exe)?\"?\s+(?:\S+\s+)*?\S*(?:mcp[-_]|[-_]mcp|semantic-search|csharp-ls(?:\.|\s|$))", re.I),
     "shared INFRA (an MCP/LSP server other sessions use)"),
    (re.compile(r"claude-code-proxy", re.I), "a transport PROXY other sessions may route through"),
)
# The launcher's -R takes ANY path (docs: "run-record path"), so the matcher must not demand a
# suffix. 2026-09-18: it required ".record.json" while every real dispatch passed
# "-R .claude/scratch/plancheck/rec_arch.json", so the record proof never ran and two hung sidecars
# could not be reaped.
RECORD_RE = re.compile(r"(?:-R|--record)\s+\"?([^\s\"]+)")
SID_ENV = b"CLAUDE_CODE_SESSION_ID="


# ----------------------------------------------------------------- process table
def process_table():
    """{pid: {pid, ppid, name, cmd, start}} for every process, via CIM. Empty dict when unreadable."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine,"
          " @{n='Start';e={$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')}} | ConvertTo-Json -Compress")
    for exe in ("pwsh", "powershell"):
        try:
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, timeout=60)
        except Exception:
            continue
        if r.returncode != 0 or not r.stdout.strip():
            continue
        try:
            rows = json.loads(r.stdout)
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
    cur = table.get(pid, {}).get("ppid")
    while isinstance(cur, int) and cur in table and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = table[cur].get("ppid")
    return out


def descendants(table, pid):
    out, frontier = [], [pid]
    while frontier:
        cur = frontier.pop()
        for k, r in table.items():
            if r.get("ppid") == cur and k not in out and k != pid:
                out.append(k)
                frontier.append(k)
    return sorted(out)


# ----------------------------------------------------------------- the three gates
def never_reason(row):
    """The command line decides: `claude.exe -p ...` is a headless child, `claude.exe --session-id` a host."""
    for pat, why in NEVER:
        if pat.search(row.get("cmd") or ""):
            return why
    return None


def _resolve(path):
    """The path as this process can open it: MSYS `/c/...` to `C:/...`, and repo-relative to REPO.

    A dispatch passes `-R .claude/scratch/...`, which is relative to the repo, not to reap's cwd.
    """
    drive = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if drive and os.name == "nt":
        path = "%s:/%s" % (drive.group(1), drive.group(2))
    return path if os.path.isabs(path) else str(REPO / path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def msys_pid_for_winpid(winpid):
    """The MSYS pid for a Windows pid, from `ps -W`; None when it is not an MSYS process.

    `/proc` on Git Bash is MSYS's own filesystem and is keyed by MSYS pid, while the CIM process
    table is keyed by Windows pid, so the environ read below needs this translation.
    """
    try:
        r = subprocess.run(["ps", "-W"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    lines = [ln.split() for ln in r.stdout.splitlines() if ln.strip()]
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


def environ_of(pid):
    """The target's environment block, or b''. Shells out: Windows Python cannot see MSYS `/proc`."""
    msys = msys_pid_for_winpid(pid)
    shell = git_bash() if msys is not None else None
    if not shell:
        return b""
    try:
        # `cat`, not `tr '\\0' '\\n'`: the quotes and backslashes do not survive CreateProcess's
        # re-quoting (the shell receives them mangled and the read fails empty). NULs pass through
        # a binary pipe unharmed, and the caller only does a substring search.
        r = subprocess.run([shell, "-c", "cat /proc/%d/environ" % msys],
                           capture_output=True, timeout=15)
    except Exception:
        return b""
    return r.stdout if r.returncode == 0 else b""


def ownership(table, pid, session_pid, sid):
    """(proof, foreign): a proof string when the target is this session's; `foreign` set when it is
    positively ANOTHER session's; (None, None) when ownership simply cannot be established."""
    if session_pid and session_pid in ancestors(table, pid):
        return "descends from this session's claude.exe pid %d" % session_pid, None
    # The target's OWN environment is the decisive evidence: a process launched from this session
    # inherits CLAUDE_CODE_SESSION_ID, and nothing else can put it there. It is also the only proof
    # that survives the launcher wrapper being killed -- 2026-09-18, two hung sidecars were
    # unreapable because their ancestor was gone and the -R record is written only when a run ENDS.
    env = environ_of(pid)
    mine = (SID_ENV + sid.encode()) if sid else None
    if env and SID_ENV in env:
        if mine and mine in env:
            return "its own environment names session %s (launched from this session)" % sid[:8], None
        return None, "its own environment names a DIFFERENT session -- a peer's process"
    # A record on disk is second-hand (a stale -R path can name us while the process is a peer's),
    # so it is consulted only when the environment was unreadable.
    cmd = table[pid].get("cmd") or ""
    m = RECORD_RE.search(cmd)
    if m and sid:
        path = m.group(1)
        if _read_json(_resolve(path)).get("parentSessionId") == sid:
            return "its dispatch record %s names parentSessionId %s (this session)" % (os.path.basename(path), sid[:8]), None
    return None, None


def justification(args, table, pid, targets):
    """(ok, evidence) for the claimed reason, verified mechanically."""
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
        ppid = table[pid].get("ppid")
        if isinstance(ppid, int) and ppid in table:
            return False, "parent %d is alive (%s)" % (ppid, (table[ppid].get("cmd") or "")[:60])
        return True, "parent %s is gone" % ppid
    if reason == "stalled":
        if not args.log or not os.path.isfile(args.log):
            return False, "--log %r is not a file" % args.log
        idle = (time.time() - os.path.getmtime(args.log)) / 60.0
        if idle < args.idle_min:
            return False, "%s written %.1f min ago (< %d)" % (os.path.basename(args.log), idle, args.idle_min)
        return True, "%s idle %.0f min (>= %d)" % (os.path.basename(args.log), idle, args.idle_min)
    if reason == "superseded":
        if not args.artifact or not os.path.exists(args.artifact):
            return False, "--artifact %r does not exist" % args.artifact
        return True, "artifact %s exists" % os.path.basename(args.artifact)
    if reason == "owner":
        if not (args.why or "").strip():
            return False, "--why must quote the owner's stop instruction"
        return True, "owner: %s" % args.why.strip()[:160]
    return False, "unknown reason"


# ----------------------------------------------------------------- act + record
def taskkill(pid, *, tree=True):
    """Kill `pid`. tree=True takes its whole tree; tree=False kills only that process, which is what
    a tree containing never-kill INFRA members needs (see `kill_subtree_sparing_infra`)."""
    cmd = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return r.returncode == 0


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


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pids", nargs="+", type=int, help="Windows or MSYS pids")
    ap.add_argument("--reason", required=True, choices=REASONS)
    ap.add_argument("--why", default="", help="one line for the ledger; the owner's words for --reason owner")
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
    for pid in targets:
        row = table[pid]
        head = "%d %s :: %s" % (pid, row.get("name"), (row.get("cmd") or "")[:110])
        proof, foreign = ownership(table, pid, session_pid, sid)
        ok, evidence = justification(args, table, pid, set(targets))
        tree = [pid] + descendants(table, pid)
        # The NEVER gate is judged on every member, but a member that may never die is SPARED rather
        # than the whole kill being refused: refusing made any sidecar whose tree merely contains an
        # MCP server impossible to stop, which is how two hung codex shells survived a stop order.
        root_never = never_reason(table[pid])
        never = [(k, never_reason(table[k])) for k in tree if k != pid and never_reason(table[k])]
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
        if not proof:
            print("REFUSED %s\n   %s" % (head, foreign or
                  "NOT OWNED: no ancestor is this session's claude.exe (%s), its environment does not "
                  "name session %s, and no dispatch record does either"
                  % (session_pid, (sid or "?")[:8])))
            entry["result"] = "refused-foreign" if foreign else "refused-not-owned"
            record(entry); refused += 1
            continue
        if not ok:
            print("REFUSED %s\n   %s: %s" % (head, args.reason.upper(), evidence))
            entry["result"] = "refused-unjustified"; record(entry); refused += 1
            continue
        if args.dry_run:
            print("DRY %s\n   owned: %s\n   %s: %s\n   tree: %s" % (head, proof, args.reason, evidence, tree))
            entry["result"] = "dry-run"; record(entry)
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
            if not killed or failed:
                refused += 1
            continue
        done = taskkill(pid)
        print("%s %s\n   owned: %s\n   %s: %s\n   tree: %s" % ("KILLED" if done else "FAILED", head, proof, args.reason, evidence, tree))
        entry["result"] = "killed" if done else "kill-failed"; record(entry)
        if not done:
            refused += 1
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
