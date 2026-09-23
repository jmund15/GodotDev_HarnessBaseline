#!/usr/bin/env python3
"""Proof for tools/reap.py — the sanctioned stop for a process this session owns.

The reaper must let a justified kill of the session's own duplicate through on proof, and must still
refuse the kills the guard exists for: a peer's process, a session host, shared infra anywhere in the
tree, a live job with no justification, a kill it cannot record.

The process table and the kill are planted; nothing here touches the OS.
Run: python3 .claude/tests/test_reap.py
"""
import io, json, os, sys, tempfile, time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import reap  # noqa: E402

SID = "9ecb32b6-e8a8-45d1-9f2e-8331d8d0c9f3"
SESSION_PID = 43144

# The owner's typed messages in the planted session transcript. `--reason owner` accepts a --why only
# when it appears verbatim in one of these, so an agent cannot author its own authorisation.
OWNER_SAID = [
    "please kill the duplicate judge now",
    "you close it, I am away from my desk",
    "Stop the env wrapper sidecar,   it is stuck",
]


def owner_row(text, sidechain=False):
    return {"type": "user", "sessionId": SID, "isSidechain": sidechain,
            "message": {"role": "user", "content": text}}


def assistant_row(text):
    return {"type": "assistant", "sessionId": SID,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def plant_transcript(rows):
    """A projects root holding this session's transcript; rows=None plants no transcript at all."""
    root = Path(tempfile.mkdtemp(prefix="reap_projects_"))
    if rows is not None:
        proj = root / "C--x-{{PROJECT_NAME}}"
        proj.mkdir()
        with open(proj / (SID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return root


def row(pid, ppid, name, cmd, start="2026-09-15 14:00:00"):
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "start": start}


def table(extra=()):
    rows = [
        row(SESSION_PID, 1, "claude.exe", r"C:\Users\x\claude.exe --session-id " + SID),
        row(100, SESSION_PID, "bash.exe", "bash -c \"bash scoring/score_daemon.sh opus-gaps\""),
        row(101, 100, "bash.exe", "bash scoring/score_daemon.sh opus-gaps"),
        row(102, 101, "bash.exe", "bash anthropic_sidecar.sh -m haiku -l daemon:ARM-T3-OPUS-r1"),
        row(103, 102, "claude.exe", r"C:\Users\x\claude.exe -p \"Call Workflow(score_cell.js)\""),
        # the twin: same work, still alive, not a target
        row(200, SESSION_PID, "bash.exe", "bash scoring/score_daemon.sh opus-gaps"),
        # a peer session and its child
        row(900, 1, "claude.exe", r"C:\Users\x\claude.exe --session-id 11111111-2222-3333-4444-555555555555"),
        row(901, 900, "bash.exe", "bash scoring/score_daemon.sh opus-gaps"),
        # shared infra
        row(950, SESSION_PID, "node.exe", "node semantic-search-mcp.js"),
    ]
    rows.extend(extra)
    return {r["pid"]: r for r in rows}


def run(argv, tbl, env=None, ledger=None, environ=None, transcript="default"):
    killed = []
    rows = [owner_row(t) for t in OWNER_SAID] if transcript == "default" else transcript
    reap.PROJECTS_ROOT = plant_transcript(rows)
    reap.process_table = lambda: dict(tbl)
    reap.taskkill = lambda pid, tree=True: killed.append(pid) or True
    # Nothing here may touch the OS: the environ read is planted, defaulting to "no environment
    # readable" so every case exercises the fallback proofs unless it says otherwise.
    reap.environ_of = environ or (lambda pid: b"")
    reap.LEDGER = Path(ledger) if ledger else Path(tempfile.mkdtemp()) / "reap_ledger.jsonl"
    e = {"CLAUDE_PID": str(SESSION_PID), "CLAUDE_CODE_SESSION_ID": SID}
    if env:
        e.update(env)
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            rc = reap.main(argv, env=e)
        except SystemExit as ex:
            rc = ex.code
    return rc, killed, buf.getvalue()


def main():
    n = fails = 0

    def ck(name, cond, detail=""):
        nonlocal n, fails
        n += 1
        if cond:
            print("  PASS ", name)
        else:
            fails += 1
            print("  FAIL ", name, ("\n        " + detail) if detail else "")

    # 1. the incident: our own duplicate daemon, twin alive -> whole tree goes, ledger written
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reap_ledger.jsonl")
        rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh opus-gaps",
                               "--why", "launched twice; 200 continues"], table(), ledger=ledger)
        # taskkill /T takes the tree from the root; the planted kill records the root it was handed.
        ck("own duplicate daemon with a live twin is killed at its root, tree 101/102/103 reported",
           rc == 0 and killed == [101] and "[101, 102, 103]" in out, f"rc={rc} killed={killed}\n{out}")
        lines = open(ledger, encoding="utf-8").read().splitlines() if os.path.exists(ledger) else []
        rec = json.loads(lines[-1]) if lines else {}
        ck("ledger row carries pid, reason, why, proof and the tree",
           rec.get("pid") == 101 and rec.get("reason") == "duplicate" and "200" in json.dumps(rec.get("evidence"))
           and rec.get("tree") == [101, 102, 103] and rec.get("session") == SID, json.dumps(rec)[:300])

    # 2. a peer's process: identical command line, other session -> denied, nothing killed
    rc, killed, out = run(["901", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh", "--why", "x"], table())
    ck("a peer session's identical daemon is denied (not ours)", rc == 2 and killed == [] and "NOT OWNED" in out, out[-300:])

    # 3. an orphan: parent gone, ownership proven through its dispatch record's parentSessionId
    with tempfile.TemporaryDirectory() as td:
        recp = os.path.join(td, "ARM-T5-OPUS-r1.daemon.record.json")
        json.dump({"parentSessionId": SID}, open(recp, "w", encoding="utf-8"))
        orphan = row(300, 7777, "bash.exe", f"bash anthropic_sidecar.sh -m haiku -R {recp} -l daemon:ARM-T5-OPUS-r1")
        rc, killed, out = run(["300", "--reason", "orphan", "--why", "parent daemon killed"], table([orphan]))
        ck("an orphan whose record names this session is ours: killed", rc == 0 and killed == [300], out[-300:])
        json.dump({"parentSessionId": "someone-else"}, open(recp, "w", encoding="utf-8"))
        rc, killed, out = run(["300", "--reason", "orphan", "--why", "x"], table([orphan]))
        ck("... and denied when the record names another session", rc == 2 and killed == [], out[-300:])

    # 3b. the -R path is any path the launcher was given, not one ending in ".record.json"
    #     (2026-09-18: every real dispatch used -R .../rec_arch.json, so the record proof never ran)
    with tempfile.TemporaryDirectory() as td:
        plain = os.path.join(td, "rec_arch.json")
        json.dump({"parentSessionId": SID}, open(plain, "w", encoding="utf-8"))
        ck("the -R matcher accepts a path that does not end in .record.json",
           reap.RECORD_RE.search(f"bash codex_proxy_sidecar.sh -R {plain} -l plc-arch") is not None,
           "a real launch command must yield its record path")
        orphan = row(301, 7777, "bash.exe", f"bash codex_proxy_sidecar.sh -m luna -R {plain} -l plc-arch")
        rc, killed, out = run(["301", "--reason", "orphan", "--why", "wrapper killed"], table([orphan]))
        ck("an orphan whose plainly-named record names this session is killed", rc == 0 and killed == [301], out[-300:])

    # 3c. the hung-run case (2026-09-18): the wrapper was killed by TaskStop, so no ancestor is
    #     left and the -R record is 0 bytes. The surviving proof is the target's OWN environment.
    with tempfile.TemporaryDirectory() as td:
        hung = row(7865, 7744, "bash.exe",
                   f'"C:/Program Files/Git/usr/bin/bash.exe" {td}/snap.sh -m luna -R {td}/rec_arch.json -l plc-arch')
        mine = lambda pid: (b"PATH=/usr/bin\0CLAUDE_CODE_SESSION_ID=" + SID.encode() + b"\0CLAUDE_PID=1\0")
        theirs = lambda pid: (b"CLAUDE_CODE_SESSION_ID=11111111-2222-3333-4444-555555555555\0")
        rc, killed, out = run(["7865", "--reason", "orphan", "--why", "wrapper killed by TaskStop"],
                              table([hung]), environ=mine)
        ck("a hung run whose own environment names this session is killed", rc == 0 and killed == [7865], out[-300:])
        rc, killed, out = run(["7865", "--reason", "orphan", "--why", "x"], table([hung]), environ=theirs)
        ck("... and denied when its environment names another session", rc == 2 and killed == [], out[-300:])
        rc, killed, out = run(["7865", "--reason", "orphan", "--why", "x"], table([hung]))
        ck("... and denied when no environment is readable (fail closed)", rc == 2 and killed == [], out[-300:])
        # The environ proof outranks the record one: a record naming another session cannot
        # authorize a process whose own environment says a different session launched it.
        plain = os.path.join(td, "rec_arch.json")
        json.dump({"parentSessionId": SID}, open(plain, "w", encoding="utf-8"))
        rc, killed, out = run(["7865", "--reason", "orphan", "--why", "x"], table([hung]), environ=theirs)
        ck("another session's process is denied even when a record on disk names ours",
           rc == 2 and killed == [], out[-300:])

    # 4. ours, but the justification does not hold: twin is dead / not an orphan / no artifact
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "555", "--key", "score_daemon.sh", "--why", "x"], table())
    ck("duplicate with a dead twin is denied (the work would not continue anywhere)", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["101", "--reason", "orphan", "--why", "x"], table())
    ck("orphan claim on a process whose parent is alive is denied", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["101", "--reason", "superseded", "--artifact", "C:/nope/never.json", "--why", "x"], table())
    ck("superseded claim without the artifact on disk is denied", rc == 2 and killed == [], out[-300:])

    # 5. stalled: a log that went quiet long enough qualifies; a fresh one does not
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "job.log"); open(log, "w").write("x")
        old = time.time() - 40 * 60; os.utime(log, (old, old))
        rc, killed, out = run(["101", "--reason", "stalled", "--log", log, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: log idle beyond the threshold -> killed", rc == 0 and killed == [101], out[-300:])
        os.utime(log, None)
        rc, killed, out = run(["101", "--reason", "stalled", "--log", log, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: a log written just now -> denied", rc == 2 and killed == [], out[-300:])

    # 6. never a session host or shared infra, even under our own ancestry
    rc, killed, out = run([str(SESSION_PID), "--reason", "owner", "--why", "owner said kill it"], table())
    ck("this session's own host is never a target", rc == 2 and killed == [] and "REFUSED" in out, out[-300:])
    # A host that DOES pass the ownership gate (a nested/peer host under our own ancestry) must be
    # stopped by the NEVER gate, not by ownership.
    host = row(940, SESSION_PID, "claude.exe", r"C:\Users\x\claude.exe --session-id 11111111-2222-3333-4444-555555555555")
    rc, killed, out = run(["940", "--reason", "owner", "--why", "owner said kill it"], table([host]))
    ck("a session host inside our own ancestry is refused by the NEVER gate",
       rc == 2 and killed == [] and "HOST" in out, out[-300:])
    rc, killed, out = run(["950", "--reason", "owner", "--why", "owner said"], table())
    ck("shared infra (an MCP server) is never a target", rc == 2 and killed == [], out[-300:])
    envclean = row(960, SESSION_PID, "env.exe", "env.exe -u CLAUDE_CODE_CHILD_SESSION -u ENABLE_LSP_TOOL -u CLAUDE_PID claude.exe -p \"judge\"")
    rc, killed, out = run(["960", "--reason", "owner", "--why", "stop the env wrapper sidecar, it is stuck"],
                          table([envclean]))
    ck("a sidecar env wrapper naming ENABLE_LSP_TOOL is not 'infra': killable", rc == 0 and killed == [960], out[-300:])

    # 7. owner reason needs the owner's words; a headless claude -p child of ours is a valid target
    rc, killed, out = run(["103", "--reason", "owner", "--why", ""], table())
    ck("owner reason with empty --why is denied", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["103", "--reason", "owner", "--why", "kill the duplicate judge"], table())
    ck("our own headless claude -p child is killable on the owner's words", rc == 0 and killed == [103], out[-300:])

    # 7f. the OWNER's instruction stands in for the ownership proof.
    # Ownership proof exists to establish that SOMEONE authorised this kill when no human is in the
    # loop. A human naming the target IS that authorisation, so `owner` is the one reason that may
    # substitute for it -- otherwise the reason is dead code: anything we already own never needs the
    # owner's words, and anything we do not own is refused before the reason is ever read.
    # The live case: a Godot editor holding a worktree, blocking every test run, owner away from the
    # keyboard. Not ours, not infra, and only the owner can say it may go.
    editor = row(53844, 7777, "Godot_v4.7.1-stable_mono_win64.exe",
                 "Godot_v4.7.1-stable_mono_win64.exe --path C:/x/.claude/worktrees/sync-pr113 --editor")
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "you close it, I am away from my desk"],
                          table([editor]))
    ck("a NOT-OWNED process the owner named is killable on their words", rc == 0 and killed == [53844], out[-300:])
    rec = json.loads(open(reap.LEDGER, encoding="utf-8").read().splitlines()[-1])
    ck("the ledger records the owner instruction as the proof, so the substitution is auditable",
       "owner-instruction" in (rec.get("proof") or "") and "away from my desk" in (rec.get("proof") or ""),
       json.dumps(rec)[:300])

    # ... and every other refusal still binds. These are the arms that keep the substitution narrow.
    rc, killed, out = run(["53844", "--reason", "orphan", "--why", "x"], table([editor]))
    ck("the same not-owned process stays refused under a NON-owner reason",
       rc == 2 and killed == [] and "NOT OWNED" in out, out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "   "], table([editor]))
    ck("owner reason without the owner's words cannot unlock a not-owned target", rc == 2 and killed == [], out[-300:])
    foreign_env = lambda pid: b"CLAUDE_CODE_SESSION_ID=11111111-2222-3333-4444-555555555555\x00"  # noqa: E731
    rc, killed, out = run(["901", "--reason", "owner", "--why", "user: kill it"], table(), environ=foreign_env)
    ck("a process whose OWN environment names another session is refused even on the owner's words",
       rc == 2 and killed == [] and "DIFFERENT session" in out, out[-300:])
    editor_infra = row(53845, 7777, "node.exe", "node semantic-search-mcp.js --port 2")
    rc, killed, out = run(["53845", "--reason", "owner", "--why", "user: kill it"], table([editor_infra]))
    ck("shared infra stays refused on the owner's words", rc == 2 and killed == [], out[-300:])

    # 7g. the owner's words must be the OWNER's: --why is checked against the owner-typed messages of
    #     this session's transcript. Any session could otherwise kill an unowned process (the owner's
    #     hand-launched Godot editor) by writing its own quote.
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "the owner told me to close the editor"],
                          table([editor]))
    ck("an owner quote absent from the owner's messages is refused", rc == 2 and killed == []
       and "verbatim" in out, out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "close the Godot editor now please"],
                          table([editor]), transcript=[owner_row("keep going"),
                                                       assistant_row("I will close the Godot editor now please")])
    ck("a quote found only in an assistant message is refused", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "close the Godot editor now please"],
                          table([editor]), transcript=[owner_row("close the Godot editor now please",
                                                                 sidechain=True)])
    ck("a quote found only in a subagent (sidechain) prompt is refused", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "you close it, I am away from my desk"],
                          table([editor]), transcript=None)
    ck("a missing session transcript refuses the owner reason (fail closed) and says what to do",
       rc == 2 and killed == [] and "transcript" in out, out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "  YOU close it,\n I am AWAY from my desk "],
                          table([editor]))
    ck("the quote match ignores case and whitespace", rc == 0 and killed == [53844], out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "close it"], table([editor]))
    ck("a quote shorter than the minimum is refused even when present", rc == 2 and killed == [], out[-300:])

    # 7b. the NEVER gate covers the whole tree: an owned, justified root with an MCP server underneath is refused
    # The sparing contract: refusing the WHOLE kill would mean a sidecar whose tree merely contains
    # an MCP server could never be stopped at all. Infra is spared; everything else still dies.
    infra_child = row(104, 101, "node.exe", "node semantic-search-mcp.js --port 1")
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh", "--why", "x"], table([infra_child]))
    ck("a tree holding shared infra kills the root and its ordinary members, sparing the infra",
       rc == 0 and 101 in killed and 102 in killed and 103 in killed and 104 not in killed and "104" in out,
       f"rc={rc} killed={killed}\n{out[-300:]}")
    # ... and a spared member's own subtree goes with it: the MCP server's children are its own.
    infra_grand = row(105, 104, "python3", "python3 semantic-search/worker.py")
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh", "--why", "x"], table([infra_child, infra_grand]))
    ck("sparing covers the infra member's whole subtree", rc == 0 and 104 not in killed and 105 not in killed, f"killed={killed}\n{out[-300:]}")
    # 7c. infra is recognised behind interpreter flags, not only as the first argument
    flagged = row(970, SESSION_PID, "node.exe", "node --inspect=9229 C:/x/semantic-search/server.js")
    rc, killed, out = run(["970", "--reason", "owner", "--why", "owner said"], table([flagged]))
    ck("an MCP server launched with flags before its script is still infra", rc == 2 and killed == [], out[-300:])
    # 7d. no ledger, no kill: the record is part of the contract
    with tempfile.TemporaryDirectory() as td:
        blocked = os.path.join(td, "not-a-dir.txt"); open(blocked, "w").write("x")
        rc, killed, out = run(["103", "--reason", "owner", "--why", "kill the duplicate judge"], table(),
                              ledger=os.path.join(blocked, "reap_ledger.jsonl"))
        ck("an unwritable ledger refuses the kill (fail closed)", rc == 2 and killed == [] and "LEDGER" in out, out[-300:])
    # 7e. a target inside another target's tree is covered once, and duplicates collapse
    rc, killed, out = run(["101", "103", "101", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh", "--why", "x"], table())
    ck("a descendant of another target and a repeated pid are covered by one tree kill (exit 0)", rc == 0 and killed == [101], f"rc={rc} killed={killed}\n{out[-300:]}")

    # 8. dry run kills nothing and prints the plan
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "score_daemon.sh", "--why", "x", "--dry-run"], table())
    ck("--dry-run prints the tree and kills nothing", rc == 0 and killed == [] and "DRY" in out and "103" in out, out[-300:])

    # 9. unknown pid and MSYS pid mapping
    rc, killed, out = run(["4242", "--reason", "owner", "--why", "x"], table())
    ck("a pid absent from the table is denied as gone/stale", rc == 2 and killed == [], out[-300:])
    ps = "      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n  1018021 1018020 1018009      101  ?         197608 13:59:21 /usr/bin/bash\n"
    ck("an MSYS pid maps to its WINPID through ps output", reap.winpid_from_ps(ps, 1018021) == 101 and reap.winpid_from_ps(ps, 5) is None)

    # 10. the shell used for the environ read is Git's, never WSL's: a bare `bash` from Windows
    #     Python is System32\bash.exe, whose /proc cannot see MSYS pids at all.
    #     Planted: a Git-style usr/bin holding ps, so the case runs the same on any OS.
    real_which = reap.shutil.which
    with tempfile.TemporaryDirectory() as git_root:
        usr_bin = os.path.join(git_root, "Git", "usr", "bin")
        os.makedirs(usr_bin)
        open(os.path.join(usr_bin, "ps.exe"), "w").close()
        reap.shutil.which = lambda name, *a, **k: os.path.join(usr_bin, "ps.exe") if name == "ps" else None
        try:
            without_bash = reap.git_bash()
            open(os.path.join(usr_bin, "bash.exe"), "w").close()
            b = reap.git_bash() or ""
        finally:
            reap.shutil.which = real_which
        ck("the environ read uses the bash.exe beside Git's ps, never a bare `bash` lookup",
           b == os.path.join(usr_bin, "bash.exe") and without_bash is None, f"resolved {b!r}, without {without_bash!r}")
    # 11. the reverse pid map: a Windows pid finds its MSYS row, an absent one does not
    ps = "      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n  1018021 1018020 1018009      101  ?         197608 13:59:21 /usr/bin/bash\n"
    real_run = reap.subprocess.run
    reap.subprocess.run = lambda *a, **k: type("R", (), {"returncode": 0, "stdout": ps})()
    try:
        ck("a present WINPID maps back to its MSYS pid, an absent one gives None",
           reap.msys_pid_for_winpid(101) == 1018021 and reap.msys_pid_for_winpid(999) is None,
           f"got {reap.msys_pid_for_winpid(101)!r}")
    finally:
        reap.subprocess.run = real_run

    print(f"\ntest_reap: {n - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
