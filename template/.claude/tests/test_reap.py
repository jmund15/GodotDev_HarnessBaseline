#!/usr/bin/env python3
"""Proof for tools/reap.py — the sanctioned stop for a process this session owns.

The reaper must let a justified kill of the session's own duplicate through on proof, and must still
refuse the kills the guard exists for: a peer's process, a session host, shared infra anywhere in the
tree, a live job with no justification, a kill it cannot record.

The process table and the kill are planted; nothing here touches the OS.
Run: python3 .claude/tests/test_reap.py
"""
import atexit, io, json, os, shutil, subprocess, sys, tempfile, time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import reap  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="reap_scratch_")
atexit.register(shutil.rmtree, SCRATCH, True)
# A kill now writes the session's hook state: keep every case's writes out of the real routing state.
HOOK_STATE = tempfile.mkdtemp(prefix="reap_state_", dir=SCRATCH)
os.environ["HARNESS_HOOK_STATE_DIR"] = HOOK_STATE

# The unplanted reader, kept because run() below replaces it for the decision cases.
REAL_PROCESS_TABLE = reap.process_table
REAL_ENVIRON_OF = reap.environ_of
REAL_SESSION_REGISTRY_DIRS = reap.session_registry_dirs
REAL_PS_TEXT = reap.ps_text
REAL_TASKKILL = reap.taskkill

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


def answer_row(question, label):
    """The owner's AskUserQuestion answer row, in the transcript shape kill_guard's proof plants."""
    return {"type": "user", "sessionId": SID, "isSidechain": False,
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_question",
                "content": 'Your questions have been answered: "%s"="%s".' % (question, label)}]},
            "toolUseResult": {"questions": [{"question": question, "header": "Stop", "multiSelect": False,
                                             "options": [{"label": label, "description": ""}]}],
                              "answers": {question: label}}}


def assistant_row(text):
    return {"type": "assistant", "sessionId": SID,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def plant_transcript(rows):
    """A projects root holding this session's transcript; rows=None plants no transcript at all."""
    root = Path(tempfile.mkdtemp(prefix="reap_projects_", dir=SCRATCH))
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
        row(100, SESSION_PID, "bash.exe", "bash -c \"bash scoring/worker.py opus-gaps\""),
        row(101, 100, "bash.exe", "bash scoring/worker.py opus-gaps"),
        row(102, 101, "bash.exe", "bash anthropic_sidecar.sh -m haiku -l daemon:ARM-T3-OPUS-r1"),
        row(103, 102, "claude.exe", r"C:\Users\x\claude.exe -p \"Call Workflow(score_cell.js)\""),
        # the twin: same work, still alive, not a target
        row(200, SESSION_PID, "bash.exe", "bash scoring/worker.py opus-gaps"),
        # a peer session and its child
        row(900, 1, "claude.exe", r"C:\Users\x\claude.exe --session-id 11111111-2222-3333-4444-555555555555"),
        row(901, 900, "bash.exe", "bash scoring/worker.py opus-gaps"),
        # shared infra
        row(950, SESSION_PID, "node.exe", "node semantic-search-mcp.js"),
    ]
    rows.extend(extra)
    return {r["pid"]: r for r in rows}


def plant_registry(sessions=None):
    """A session registry dir holding `<pid>.json` rows {pid: sessionId}; empty by default."""
    root = Path(tempfile.mkdtemp(prefix="reap_sessions_", dir=SCRATCH))
    for pid, session in (sessions or {}).items():
        (root / ("%d.json" % pid)).write_text(json.dumps({"pid": pid, "sessionId": session}), encoding="utf-8")
    return root


def run(argv, tbl, env=None, ledger=None, environ=None, transcript="default", sessions=None, kill_ok=True,
        peb=None, msys=None, ps=None):
    killed = []
    rows = [owner_row(t) for t in OWNER_SAID] if transcript == "default" else transcript
    reap.PROJECTS_ROOT = plant_transcript(rows)
    registry = plant_registry(sessions)
    reap.session_registry_dirs = lambda: [registry]
    reap.process_table = lambda: dict(tbl)
    ok = kill_ok if callable(kill_ok) else (lambda pid: kill_ok)
    reap.taskkill = lambda pid, tree=True: (killed.append(pid) or True) if ok(pid) else False
    # Nothing here may touch the OS: the environ read is planted, defaulting to "no environment
    # readable" so every case exercises the fallback proofs unless it says otherwise.
    reap.environ_of = environ or (lambda pid: b"")
    reap.launch_environ = peb or reap.environ_of
    reap.msys_environ = msys or (lambda pid: b"")
    reap.ps_text = lambda: ps
    reap.process_exited = lambda pid, start: False  # planted pids are not OS processes
    reap.LEDGER = Path(ledger) if ledger else Path(tempfile.mkdtemp(dir=SCRATCH)) / "reap_ledger.jsonl"
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

    def ck_win(name, cond, detail=""):
        # reap.py reads the Windows process table and compares drive-letter paths; a POSIX run has neither.
        if sys.platform == "win32":
            ck(name, cond, detail)
        else:
            print("  SKIP ", name, "(Windows drive paths)")

    # 1. the incident: our own duplicate daemon, twin alive -> whole tree goes, ledger written
    with tempfile.TemporaryDirectory() as td:
        ledger = os.path.join(td, "reap_ledger.jsonl")
        rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py opus-gaps",
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
    rc, killed, out = run(["901", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"], table())
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
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "555", "--key", "worker.py", "--why", "x"], table())
    ck("duplicate with a dead twin is denied (the work would not continue anywhere)", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["101", "--reason", "orphan", "--why", "x"], table())
    ck("orphan claim on a process whose parent is alive is denied", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["101", "--reason", "superseded", "--artifact", "C:/nope/never.json", "--why", "x"], table())
    ck("superseded claim without the artifact on disk is denied", rc == 2 and killed == [], out[-300:])
    with tempfile.TemporaryDirectory() as td:
        own = os.path.join(td, "job.record.json"); open(own, "w").write("{}")
        other = os.path.join(td, "twin_result.json"); open(other, "w").write("{}")
        live = row(104, 100, "bash.exe", "bash codex_proxy_sidecar.sh -m luna56 -R %s -l job" % own.replace("\\", "/"))
        rc, killed, out = run(["104", "--reason", "superseded", "--artifact", own, "--why", "x"], table([live]))
        ck("superseded: the target's own in-progress output is not a superseding artifact", rc == 2 and killed == [], out[-300:])
        rc, killed, out = run(["104", "--reason", "superseded", "--artifact", other, "--why", "x"], table([live]))
        ck("superseded: another job's finished output still qualifies", rc == 0 and killed == [104], out[-300:])
        sibling = os.path.join(td, "job.out.json"); open(sibling, "w").write("{}")
        rc, killed, out = run(["104", "--reason", "superseded", "--artifact", sibling, "--why", "x"], table([live]))
        ck("superseded: a derived sibling of the target's own record is its own output", rc == 2 and killed == [], out[-300:])
        upper = os.path.join(td, "JOB.RECORD.JSON")
        rc, killed, out = run(["104", "--reason", "superseded", "--artifact", upper, "--why", "x"], table([live]))
        ck("superseded: a case-only variant of the target's own output refuses", rc == 2 and killed == [], out[-300:])
        parent = row(105, 100, "bash.exe", "bash sidecar_fanout.py jobs.json")
        child = row(106, 105, "bash.exe", "bash codex_proxy_sidecar.sh -R %s -l job" % own.replace("\\", "/"))
        rc, killed, out = run(["105", "--reason", "superseded", "--artifact", own, "--why", "x"], table([parent, child]))
        ck("superseded: an artifact named only in a descendant's command line refuses", rc == 2 and killed == [], out[-300:])

    # 5. stalled: a log that went quiet long enough qualifies; a fresh one does not
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "job.log"); open(log, "w").write("x")
        old = time.time() - 40 * 60; os.utime(log, (old, old))
        rc, killed, out = run(["101", "--reason", "stalled", "--log", log, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: log idle beyond the threshold -> killed", rc == 0 and killed == [101], out[-300:])
        os.utime(log, None)
        rc, killed, out = run(["101", "--reason", "stalled", "--log", log, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: a log written just now -> denied", rc == 2 and killed == [], out[-300:])
        # A sidecar's stream keeps taking status heartbeats while the run makes no progress; its launcher
        # measures real silence and writes <record>.stall. A fresh marker is the evidence, not its mtime.
        stall = os.path.join(td, "job.record.json.stall")
        open(stall, "w").write("idle=2400 at=2026-09-30T17:26:19Z\n")
        rc, killed, out = run(["101", "--reason", "stalled", "--log", stall, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: a fresh .stall marker reporting idle beyond the threshold -> killed", rc == 0 and killed == [101], out[-300:])
        open(stall, "w").write("idle=600 at=2026-09-30T17:26:19Z\n")
        rc, killed, out = run(["101", "--reason", "stalled", "--log", stall, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: a .stall marker reporting idle under the threshold -> denied", rc == 2 and killed == [], out[-300:])
        open(stall, "w").write("garbage\n")
        rc, killed, out = run(["101", "--reason", "stalled", "--log", stall, "--idle-min", "30", "--why", "x"], table())
        ck("stalled: an unreadable .stall marker -> denied", rc == 2 and killed == [], out[-300:])

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

    # 6b. a codex.exe whose parent is ChatGPT.exe belongs to that app, and is refused like any other host
    chatgpt = row(970, 1, "ChatGPT.exe", r"C:\Users\x\ChatGPT.exe")
    app_codex = row(971, 970, "codex.exe", r"C:\Users\x\codex.exe app-server")
    rc, killed, out = run(["971", "--reason", "owner", "--why", "kill the duplicate judge"], table([chatgpt, app_codex]))
    ck("a codex.exe parented by ChatGPT.exe is refused by the NEVER gate",
       rc == 2 and killed == [] and "ChatGPT" in out, out[-300:])
    own_codex = row(972, SESSION_PID, "codex.exe", r"C:\Users\x\codex.exe exec brief")
    rc, killed, out = run(["972", "--reason", "owner", "--why", "kill the duplicate judge"], table([own_codex]))
    ck("a codex.exe this session launched is still killable", rc == 0 and killed == [972], out[-300:])

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
    asked = "The Godot editor from 10:13 holds the worktree. Stop it?"
    rc, killed, out = run(["53844", "--reason", "owner", "--why", asked], table([editor]),
                          transcript=[answer_row(asked, "Stop it (Recommended)")])
    ck("a question the owner answered with a stop option is their stop instruction",
       rc == 0 and killed == [53844], out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", asked], table([editor]),
                          transcript=[answer_row(asked, "Keep it running")])
    ck("a question the owner answered with a keep option is refused", rc == 2 and killed == [], out[-300:])
    rc, killed, out = run(["53844", "--reason", "owner", "--why", "Stop it (Recommended)"], table([editor]),
                          transcript=[answer_row(asked, "Stop it (Recommended)")])
    ck("an option label alone, which names no target, is refused", rc == 2 and killed == [], out[-300:])

    # 7b. the NEVER gate covers the whole tree: an owned, justified root with an MCP server underneath is refused
    # The sparing contract: refusing the WHOLE kill would mean a sidecar whose tree merely contains
    # an MCP server could never be stopped at all. Infra is spared; everything else still dies.
    infra_child = row(104, 101, "node.exe", "node semantic-search-mcp.js --port 1")
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"], table([infra_child]))
    ck("a tree holding shared infra kills the root and its ordinary members, sparing the infra",
       rc == 0 and 101 in killed and 102 in killed and 103 in killed and 104 not in killed and "104" in out,
       f"rc={rc} killed={killed}\n{out[-300:]}")
    # ... and a spared member's own subtree goes with it: the MCP server's children are its own.
    infra_grand = row(105, 104, "python3", "python3 semantic-search/worker.py")
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"], table([infra_child, infra_grand]))
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
    rc, killed, out = run(["101", "103", "101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"], table())
    ck("a descendant of another target and a repeated pid are covered by one tree kill (exit 0)", rc == 0 and killed == [101], f"rc={rc} killed={killed}\n{out[-300:]}")

    # 8. dry run kills nothing and prints the plan
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x", "--dry-run"], table())
    ck("--dry-run prints the tree and kills nothing", rc == 0 and killed == [] and "DRY" in out and "103" in out, out[-300:])

    # 9. unknown pid and MSYS pid mapping
    rc, killed, out = run(["4242", "--reason", "owner", "--why", "x"], table())
    ck("a pid absent from the table is denied as gone/stale", rc == 2 and killed == [], out[-300:])
    ps = "      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n  1018021 1018020 1018009      101  ?         197608 13:59:21 /usr/bin/bash\n"
    ck("an MSYS pid maps to its WINPID through ps output", reap.winpid_from_ps(ps, 1018021) == 101 and reap.winpid_from_ps(ps, 5) is None)

    # 9b. an MSYS child whose Windows parent is gone (bash -c wrapper exec'd away) is still in the tree:
    #     2026-10-09 a reaped bench grid wrapper left its run_grid bash running cells for 10 minutes.
    ps = ("      PID    PPID    PGID     WINPID   TTY         UID    STIME COMMAND\n"
          "     5101    5100    5100      101  ?         197608 13:59:21 /usr/bin/bash\n"
          "     5150    5101    5100      150  ?         197608 13:59:22 /usr/bin/bash\n")
    grid = lambda: [row(150, 7777, "bash.exe", "bash run_grid.snapshot.sh camp spec")]
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"],
                          table(grid()), ps=ps)
    ck("an MSYS child outside the Windows tree is killed with its MSYS parent",
       rc == 0 and killed == [101, 150] and "150" in out, f"rc={rc} killed={killed}\n{out[-400:]}")
    rc, killed, out = run(["101", "--reason", "duplicate", "--twin", "200", "--key", "worker.py", "--why", "x"],
                          table(grid()), ps=None)
    ck("... and an unreadable ps leaves the Windows tree alone", rc == 0 and killed == [101], f"killed={killed}")

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
    reap.ps_text = REAL_PS_TEXT
    try:
        ck("a present WINPID maps back to its MSYS pid, an absent one gives None",
           reap.msys_pid_for_winpid(101) == 1018021 and reap.msys_pid_for_winpid(999) is None,
           f"got {reap.msys_pid_for_winpid(101)!r}")
    finally:
        reap.subprocess.run = real_run

    # 12. one process's command line carrying a raw control character (curl's \x1b, a stray \x01)
    #     must not blank the WHOLE table: an empty table refuses every kill on this machine.
    raw = '[{"ProcessId":5,"ParentProcessId":1,"Name":"node.exe","CommandLine":"node --probe \x1b[0m",'\
          '"Start":"2026-01-01 00:00:00"},{"ProcessId":6,"ParentProcessId":1,"Name":"git.exe",'\
          '"CommandLine":"git status","Start":"2026-01-01 00:00:00"}]'
    real_run = reap.subprocess.run
    reap.subprocess.run = lambda *a, **k: type("R", (), {"returncode": 0, "stdout": raw, "stderr": ""})()
    try:
        planted = REAL_PROCESS_TABLE()
    finally:
        reap.subprocess.run = real_run
    ck("a raw control character in one command line does not blank the process table",
       len(planted) == 2 and planted.get(5, {}).get("name") == "node.exe",
       f"got {len(planted)} rows: {planted!r}")

    # 13. a command line that is not valid UTF-8 (byte 0xfa, 2026-09-24): a strict text decode fails in
    #     subprocess's reader thread and leaves stdout None, which crashed the tool mid-reap. The planted run
    #     reproduces that: a strict decode yields None, a tolerant one (errors=...) yields the table.
    def undecodable_run(*a, **k):
        out = raw if k.get("errors") in ("replace", "backslashreplace", "surrogateescape") else None
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    reap.subprocess.run = undecodable_run
    try:
        try:
            planted = REAL_PROCESS_TABLE()
        except Exception as exc:
            planted = {"crash": repr(exc)}
    finally:
        reap.subprocess.run = real_run
    ck("a command line that is not valid UTF-8 does not crash or blank the process table",
       len(planted) == 2 and planted.get(6, {}).get("name") == "git.exe", f"got {planted!r}")

    # 14. a native Windows process (Godot, python, dotnet) is invisible to MSYS /proc, so an orphan whose
    #     launcher died was unprovable and unreapable. Its environment block is read from its own PEB. This
    #     mechanism cannot be planted: the case reads a live child's block.
    if sys.platform == "win32":
        import subprocess as _sp
        child = _sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                          env={**os.environ, "HARNESS_REAP_PROBE": "native-ok"})
        try:
            time.sleep(0.5)
            block = REAL_ENVIRON_OF(child.pid)
        finally:
            child.kill()
            child.wait()
        ck("a native process's own environment block is read", b"HARNESS_REAP_PROBE=native-ok" in block,
           f"read {len(block)} bytes")

    # 15. a detached sidecar child: its launcher scrubs CLAUDE_* from the child's environment and the
    #     wrapper that spawned it exits, so neither ancestry nor CLAUDE_CODE_SESSION_ID can prove it.
    #     The launcher's SIDECAR_PARENT_SESSION_ID tag proves it; a tag naming another session refuses.
    child = row(400, 8888, "timeout.exe", "timeout.exe 14400 claude -p --model gpt-6-sol")
    tagged = lambda sid_: (lambda pid: b"PATH=/usr/bin\0SIDECAR_PARENT_SESSION_ID=" + sid_.encode() + b"\0")
    rc, killed, out = run(["400", "--reason", "orphan", "--why", "over-pinned fold child"], table([child]),
                          environ=tagged(SID))
    ck("a detached sidecar child tagged with this session's id is ours: killed", rc == 0 and killed == [400], out[-300:])
    rc, killed, out = run(["400", "--reason", "orphan", "--why", "x"], table([child]),
                          environ=tagged("11111111-2222-3333-4444-555555555555"))
    ck("... and refused when its tag names another session", rc == 2 and killed == [], out[-300:])

    # 16. a child launched before the tag existed: its prompt file, launched by this session's own
    #     transcript, proves it; the same env with no such launch in the transcript refuses.
    with tempfile.TemporaryDirectory() as td:
        brief = os.path.join(td, "fold_brief.md").replace("\\", "/")
        env_prompt = lambda pid: b"PATH=/usr/bin\0CLAUDE_CODE_SIDECAR_PROMPT_FILE=" + brief.encode() + b"\0"
        launch = {"type": "assistant", "sessionId": SID, "cwd": td, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command":
             "D=%s && bash .claude/scripts/codex_proxy_sidecar.sh -m sol -e high -f $D/fold_brief.md -R $D/r.json -X" % td.replace("\\", "/")}}]}}
        rows_with_launch = [owner_row(t) for t in OWNER_SAID] + [launch]
        rc, killed, out = run(["400", "--reason", "orphan", "--why", "over-pinned fold child"], table([child]),
                              environ=env_prompt, transcript=rows_with_launch)
        ck("an untagged sidecar child whose prompt file this session launched is ours: killed",
           rc == 0 and killed == [400], out[-300:])
        rc, killed, out = run(["400", "--reason", "orphan", "--why", "x"], table([child]), environ=env_prompt)
        ck("... and refused when this session never launched that prompt file", rc == 2 and killed == [], out[-300:])

    # 17. dispatch servers: a sidecar starts a claude-code-proxy and a text-only shim under
    #     nohup and kills them in an exit trap, which a TerminateProcess never runs. Their SIDECAR_LAUNCH_ID
    #     ties them to the launch; a proxy with none is a shared one and stays never-kill.
    X, Y, PEER = "launch-x", "launch-y", "11111111-2222-3333-4444-555555555555"
    shim_cmd = ("C:/x/.claude/scripts/lib/text_only_shim.py --listen 50272 --upstream 50268 "
                "--log C:/Users/x/.local/state/claude-code-proxy/sidecar-serve.log.shim")
    proxy_exe = r"C:\Users\x\AppData\Local\claude-code-proxy\claude-code-proxy-0.1.39-adapterfix.exe"
    shim_nohup = row(499, 7101, "nohup.exe", r'"C:\Program Files\Git\usr\bin\nohup.exe" python3 /c/x/.claude/scripts/lib/text_only_shim.py --listen 50272')
    shim = row(500, 499, "python3.exe", r"C:\Python\python3.exe " + shim_cmd)
    proxy_nohup = row(509, 7102, "nohup.exe", r'"C:\Program Files\Git\usr\bin\nohup.exe" %s serve --no-monitor --port 50268' % proxy_exe)
    proxy = row(510, 509, "claude-code-proxy-0.1.39-adapterfix.exe", proxy_exe + " serve --no-monitor --port 50268")
    launcher = row(520, 7103, "bash.exe", "bash .claude/scripts/codex_proxy_sidecar.sh -m model-a -f brief.md -l j1")
    seat = row(521, 520, "claude.exe", r"C:\Users\x\claude.exe -p --model model-a")
    peer_server = row(530, 7104, "python3.exe", r"C:\Python\python3.exe " + shim_cmd)
    other_launch = row(531, 7105, "python3.exe", r"C:\Python\python3.exe " + shim_cmd)
    shared = row(540, 7106, "claude-code-proxy.exe", r"C:\bin\claude-code-proxy.exe serve --port 8082")
    tail = row(541, 7107, "bash.exe", "tail -f C:/Users/x/.local/state/claude-code-proxy/sidecar-serve.log")

    def env_block(launch=None, session=SID):
        out = b"PATH=/usr/bin\0"
        if launch:
            out += b"SIDECAR_LAUNCH_ID=" + launch.encode() + b"\0"
        if session:
            out += b"CLAUDE_CODE_SESSION_ID=" + session.encode() + b"\0"
        return out

    envs = {499: env_block(X), 500: env_block(X), 509: env_block(X), 510: env_block(X),
            520: env_block(X), 521: env_block(X, session=None), 530: env_block(X, PEER),
            531: env_block(Y), 540: env_block(), 541: env_block()}
    planted_env = lambda pid: envs.get(pid, b"")  # noqa: E731
    servers = [shim_nohup, shim, proxy_nohup, proxy]

    rc, killed, out = run(["500", "--reason", "orphan", "--why", "leaked shim"], table(servers),
                          environ=planted_env)
    ck("a dispatch shim whose launch has no live launcher or seat child is reapable as orphan",
       rc == 0 and killed == [500], out[-400:])
    rc, killed, out = run(["510", "--reason", "orphan", "--why", "leaked proxy"], table(servers),
                          environ=planted_env)
    ck("a dispatch proxy executable carrying a launch id is reapable as orphan", rc == 0 and killed == [510], out[-400:])
    rc, killed, out = run(["500", "--reason", "orphan", "--why", "x"], table(servers + [launcher]),
                          environ=planted_env)
    ck("... and refused, naming the launcher, while a launcher carrying the same id is alive",
       rc == 2 and killed == [] and "520" in out, out[-400:])
    rc, killed, out = run(["500", "--reason", "orphan", "--why", "x"], table(servers + [seat]),
                          environ=planted_env)
    ck("... and refused while the seat child carrying the same id is alive",
       rc == 2 and killed == [] and "521" in out, out[-400:])
    for argv in (["--reason", "orphan"], ["--reason", "owner", "--why", "kill the duplicate judge"],
                 ["--reason", "duplicate", "--twin", "200", "--key", "serve"],
                 ["--reason", "superseded", "--artifact", str(HERE)]):
        rc, killed, out = run(["540"] + argv + (["--why", "x"] if "--why" not in argv else []),
                              table([shared]), environ=planted_env)
        ck("a proxy executable with no launch id stays never-kill under %s" % argv[1],
           rc == 2 and killed == [] and "PROXY" in out, out[-300:])
    rc, killed, out = run(["541", "--reason", "orphan", "--why", "x"], table([tail]), environ=planted_env)
    ck("a process that only NAMES the proxy's log path is not a proxy", rc == 0 and killed == [541], out[-300:])
    rc, killed, out = run(["530", "--reason", "orphan", "--why", "x"], table([peer_server]), environ=planted_env)
    ck("another session's dispatch server is refused (ownership)",
       rc == 2 and killed == [] and "DIFFERENT" in out, out[-300:])

    orphan_launcher = row(520, 7777, launcher["name"], launcher["cmd"])
    everything = servers + [orphan_launcher, seat, peer_server, other_launch]
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"], table(everything),
                          environ=planted_env)
    ck("reaping a launcher also kills its own dispatch servers and prints each",
       rc == 0 and killed[0] == 520 and sorted(killed[1:]) == [499, 500, 509, 510]
       and all("KILLED %d" % p in out for p in (499, 500, 509, 510))
       and out.count("(dispatch server of launch %s)" % X) == 4, f"rc={rc} killed={killed}\n{out[-600:]}")
    ck("... never another session's server or another launch's", 530 not in killed and 531 not in killed,
       f"killed={killed}")
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung", "--dry-run"], table(everything),
                          environ=planted_env)
    ck("--dry-run prints a launcher's dispatch servers and kills nothing",
       rc == 0 and killed == [] and all("DRY %d" % p in out for p in (499, 500, 509, 510))
       and "DRY 530" not in out, out[-600:])
    mcp_child = row(522, 520, "node.exe", "node semantic-search-mcp.js")
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"], table(everything + [mcp_child]),
                          environ=planted_env, kill_ok=False)
    ck("a never-kill-path launcher whose kill fails keeps its dispatch servers",
       rc == 2 and "FAILED 520" in out and "dispatch server of launch" not in out, f"rc={rc}\n{out[-600:]}")
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"], table(everything + [mcp_child]),
                          environ=planted_env, kill_ok=lambda pid: pid != 520)
    ck("... and keeps them when only its descendants die",
       rc == 2 and killed == [521] and "PARTIAL 520" in out and "dispatch server of launch" not in out,
       f"rc={rc} killed={killed}\n{out[-600:]}")

    # 17b. detached seats: the launcher detaches its claude seat, so the seat sits under its own env.exe
    #      outside the launcher's tree; taskkill /T on the launcher never reaches it (2026-10-01: six
    #      comparator seats kept running after their launchers were reaped, twice).
    def seat_block(launch, parent=SID):
        return (b"PATH=/usr/bin\0SIDECAR_LAUNCH_ID=" + launch.encode() + b"\0SIDECAR_SEAT_LAUNCH_ID="
                + launch.encode() + b"\0SIDECAR_PARENT_SESSION_ID=" + parent.encode() + b"\0")
    lone = row(520, 7777, launcher["name"], launcher["cmd"])
    d_env = row(523, 7108, "env.exe", "env -u CLAUDE_CODE_SESSION_ID -u SIDECAR_LAUNCH_ID claude.exe -p")
    d_seat = row(524, 523, "claude.exe", r"C:\Users\x\.local\bin\claude.exe -p --model model-a")
    d_mcp = row(525, 524, "node.exe", "node semantic-search-mcp.js")
    d_tool = row(526, 524, "bash.exe", "bash -c 'grep -n x y'")
    y_seat = row(527, 7109, "claude.exe", r"C:\Users\x\.local\bin\claude.exe -p --model model-a")
    peer_seat = row(528, 7110, "claude.exe", r"C:\Users\x\.local\bin\claude.exe -p --model model-a")
    seat_envs = {**envs, 524: seat_block(X), 525: seat_block(X), 526: seat_block(X),
                 527: seat_block(Y), 528: seat_block(X, PEER)}
    detached = [lone, d_env, d_seat, d_mcp, d_tool, y_seat, peer_seat]
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"], table(detached),
                          environ=lambda pid: seat_envs.get(pid, b""))
    ck("reaping a launcher also kills its detached seat and prints it",
       rc == 0 and 524 in killed and "KILLED 524" in out and "(seat of launch %s)" % X in out,
       f"rc={rc} killed={killed}\n{out[-600:]}")
    ck("... sparing the seat's MCP server, never another launch's seat or another session's",
       525 not in killed and 527 not in killed and 528 not in killed and 526 in killed, f"killed={killed}")
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung", "--dry-run"], table(detached),
                          environ=lambda pid: seat_envs.get(pid, b""))
    ck("--dry-run prints the detached seat and kills nothing",
       rc == 0 and killed == [] and "DRY 524" in out and "(seat of launch %s)" % X in out, out[-600:])

    # A seat that exited leaves its tagged MCP server as the launch's topmost carrier: that server and its
    # children are shared infra, never a seat to kill.
    lone_mcp = row(525, 524, "node.exe", "node semantic-search-mcp.js")  # its seat 524 is gone
    mcp_kid = row(529, 525, "node.exe", "node worker.js")
    mcp_envs = {**seat_envs, 529: seat_block(X)}
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"],
                          table([lone, d_env, lone_mcp, mcp_kid]), environ=lambda pid: mcp_envs.get(pid, b""))
    ck("a seat's surviving MCP server, now the topmost carrier, is spared with its children",
       rc == 0 and killed == [520] and "KILLED 525" not in out, f"rc={rc} killed={killed}\n{out[-600:]}")

    # Detached cleanup reads launch ids the killed tree carried: a dead launcher's environment is unreadable,
    # so discovery runs before the kill. An ancestry-owned launcher never warms its launch id beforehand.
    dead = set()
    owned_launcher = row(520, SESSION_PID, launcher["name"], launcher["cmd"])
    rc, killed, out = run(["520", "--reason", "superseded", "--artifact", str(HERE), "--why", "x"],
                          table([owned_launcher, d_env, d_seat, d_mcp, d_tool] + servers),
                          kill_ok=lambda pid: dead.add(pid) or True,
                          environ=lambda pid: b"" if pid in dead else seat_envs.get(pid, b""))
    ck("an owned launcher whose kill clears its environment still reaps its detached seat and servers",
       rc == 0 and {520, 524, 499, 500, 509, 510} <= set(killed) and 525 not in killed,
       f"rc={rc} killed={killed}\n{out[-600:]}")

    # A tree member that is no launcher never costs an MSYS `/proc` read (one bash spawn each) to find a launch id.
    msys_reads = []
    rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung", "--dry-run"],
                          table(detached + [row(540, 520, "node.exe", "node x.js"),
                                            row(541, 520, "bash.exe", "bash -c 'grep -n x y'")]),
                          environ=lambda pid: seat_envs.get(pid, b""),
                          msys=lambda pid: (msys_reads.append(pid) or b""))
    ck("seat discovery reads the MSYS block of no non-launcher tree member",
       rc == 0 and "tree: [520, 540, 541]" in out and not {540, 541} & set(msys_reads), f"rc={rc} msys={msys_reads}\n{out[-400:]}")

    # The ledger records 'killing' before every kill, the launch's strays included: a kill that is
    # not on the record first did not happen through this tool.
    with tempfile.TemporaryDirectory() as td:
        order_ledger = os.path.join(td, "reap_ledger.jsonl")
        unrecorded = []

        def recorded_first(pid):
            rows = [json.loads(x) for x in open(order_ledger, encoding="utf-8")] if os.path.exists(order_ledger) else []
            mine = [r for r in rows if r.get("pid") == pid or pid in (r.get("tree") or [])]
            if not mine or mine[-1].get("result") != "killing":
                unrecorded.append(pid)
            return True
        rc, killed, out = run(["520", "--reason", "orphan", "--why", "launcher hung"],
                              table(detached + servers), ledger=order_ledger, kill_ok=recorded_first,
                              environ=lambda pid: seat_envs.get(pid, b""))
        ck("every seat and dispatch-server kill follows its 'killing' ledger row",
           rc == 0 and 524 in killed and unrecorded == [] and "(dispatch server of launch %s)" % X in out,
           f"rc={rc} killed={killed} unrecorded={unrecorded}\n{out[-600:]}")

    # 18. orphaned seats: a seat child carries its launch id; it is orphaned when no launcher
    #     (a *_sidecar.sh bash) of that launch lives.
    S1, S2, DEAD = "launch-s1", "launch-s2", "deaddead-0000-0000-0000-000000000000"
    codex = "bash /c/x/.claude/scripts/codex_proxy_sidecar.sh -m model-a -f %s -l %s -R %s.record.json"
    seat_cmd = r"C:\Users\x\.local\bin\claude.exe -p --model model-a --output-format stream-json"

    def env18(launch=None, session=None, parent=None, prompt=None, fanout=None, label=None):
        out = b"PATH=/usr/bin\0"
        for key, value in (("SIDECAR_LAUNCH_ID", launch), ("CLAUDE_CODE_SESSION_ID", session),
                           ("SIDECAR_PARENT_SESSION_ID", parent), ("CLAUDE_CODE_SIDECAR_PROMPT_FILE", prompt),
                           ("SIDECAR_FANOUT_ID", fanout), ("CLAUDE_CODE_SIDECAR_LABEL", label)):
            if value:
                out += key.encode() + b"=" + value.encode() + b"\0"
        return out

    seat = row(621, 7201, "claude.exe", seat_cmd)                       # its launcher 7201 is gone
    fan_launcher = row(630, 7202, "bash.exe", codex % ("C:/x/brief.md", "s1", "C:/x/s1"))
    envs18 = {621: env18(S1, parent=SID), 630: env18(S1, session=SID)}
    planted18 = lambda pid: envs18.get(pid, b"")  # noqa: E731
    rc, killed, out = run(["621", "--reason", "orphan", "--why", "seat outlived launcher"], table([seat]),
                          environ=planted18)
    ck("a seat child whose launcher is gone is reapable as orphan", rc == 0 and killed == [621], out[-400:])
    rc, killed, out = run(["621", "--reason", "orphan", "--why", "x"], table([seat, fan_launcher]), environ=planted18)
    ck("... and refused, naming it, while a launcher carrying the same launch id lives",
       rc == 2 and killed == [] and "630" in out, out[-400:])
    direct_launcher = row(640, 7203, "bash.exe", codex % ("C:/x/brief.md", "s1", "C:/x/s1"))
    fork = row(641, 640, "bash.exe", direct_launcher["cmd"])
    seat_under = row(642, 641, "claude.exe", seat_cmd)
    envs18[642] = env18(S1, parent=SID)
    rc, killed, out = run(["642", "--reason", "orphan", "--why", "x"],
                          table([direct_launcher, fork, seat_under]), environ=planted18)
    ck("... and refused while its launcher is an ancestor, though the launcher's own block lacks the id",
       rc == 2 and killed == [] and ("640" in out or "641" in out), out[-400:])
    env_parent = row(651, 7204, "env.exe", "/usr/bin/env CLAUDE_CODE_ENTRYPOINT=cli claude -p")
    seat_env = row(652, 651, "claude.exe", seat_cmd)
    envs18[652] = env18(S1, parent=SID)
    rc, killed, out = run(["652", "--reason", "orphan", "--why", "x"], table([env_parent, seat_env]),
                          environ=planted18)
    ck("a seat child whose parent lives but whose launcher is gone is orphan",
       rc == 0 and killed == [652], out[-400:])

    # A dead session's leftovers: nothing can consume their result, so any session may reap them.
    leftover = row(660, 7205, "claude.exe", seat_cmd)
    leftover_proxy = row(661, 7206, "claude-code-proxy-0.1.42.exe",
                         r"C:\x\claude-code-proxy-0.1.42.exe serve --no-monitor --port 51000")
    peer_seat = row(662, 7207, "claude.exe", seat_cmd)
    live_host = row(910, 1, "claude.exe", r'"C:\Users\x\.local\bin\claude.exe"')
    host_mcp = row(911, 910, "node.exe", "node C:/x/server.js")
    envs18.update({660: env18(S2, parent=DEAD), 661: env18(S2, session=DEAD),
                   662: env18(S2, parent="11111111-2222-3333-4444-555555555555"),
                   911: env18(session="22222222-3333-4444-5555-666666666666")})
    rc, killed, out = run(["660", "--reason", "orphan", "--why", "dead session's seat"], table([leftover, leftover_proxy]),
                          environ=planted18)
    ck("a dead session's leftover seat is reapable as orphan by another session, with its server",
       rc == 0 and sorted(killed) == [660, 661] and "dead" in out.lower(), f"rc={rc} killed={killed}\n{out[-500:]}")
    rc, killed, out = run(["662", "--reason", "orphan", "--why", "x"], table([peer_seat]), environ=planted18)
    ck("... and a live session's seat (its host names the session) is refused",
       rc == 2 and killed == [] and "DIFFERENT" in out, out[-400:])
    envs18[660] = env18(S2, parent="22222222-3333-4444-5555-666666666666")
    rc, killed, out = run(["660", "--reason", "orphan", "--why", "x"], table([leftover, live_host, host_mcp]),
                          environ=planted18, sessions={910: "22222222-3333-4444-5555-666666666666"})
    ck("... and a session a live host's registry row names is live, so its seat is refused",
       rc == 2 and killed == [], out[-400:])
    envs18[660] = env18(S2, parent=DEAD)
    rc, killed, out = run(["660", "--reason", "orphan", "--why", "x"], table([leftover, live_host, host_mcp]),
                          environ=planted18)
    ck("... and a live host whose session cannot be read makes every session unknown: refused",
       rc == 2 and killed == [], out[-400:])
    rc, killed, out = run(["660", "--reason", "orphan", "--why", "x"], table([leftover, live_host, host_mcp]),
                          environ=planted18, sessions={910: "33333333-0000-0000-0000-000000000000"})
    ck("... while a host mapped to another session leaves the dead one provably dead",
       rc == 0 and killed == [660], out[-400:])
    other_launcher = row(663, 7208, "bash.exe", codex % ("C:/x/b2.md", "s2", "C:/x/s2"))
    envs18[663] = env18(S2, session=DEAD)
    rc, killed, out = run(["660", "--reason", "orphan", "--why", "x"], table([leftover, other_launcher]),
                          environ=planted18)
    ck("... and a dead session's seat whose launcher lives is refused", rc == 2 and killed == [], out[-400:])

    # The duplicate-launch scan: a launch refuses while a live launcher or seat child already runs
    # its prompt file (or resumes its session), naming each PID and the reap command.
    prompt = "C:/x/brief.md"
    rel_launcher = row(670, 7209, "bash.exe", codex % (".claude/scratch/j3/brief.md", "rel", "C:/x/rel"))
    resumer = row(671, 7210, "bash.exe", "bash /c/x/.claude/scripts/codex_proxy_sidecar.sh -m model-a -r sess-abc -l r1")
    resumed_seat = row(672, 7211, "claude.exe", seat_cmd + " --resume sess-xyz")
    prompt_seat = row(673, 7212, "claude.exe", seat_cmd)
    envs18.update({630: env18(S1, session=SID, fanout="fan-a"), 673: env18("launch-s3", parent=SID, prompt=prompt)})
    rc, killed, out = run(["live-launch", "--prompt", prompt], table([fan_launcher]), environ=planted18)
    ck_win("live-launch: a live launcher carrying the prompt file refuses (3), naming its pid and the reap command",
       rc == 3 and "630" in out and "reap.py 630" in out and "launch=%s" % S1 in out and killed == [], out[-600:])
    rc, killed, out = run(["live-launch", "--prompt", "C:/x/other.md"], table([fan_launcher]), environ=planted18)
    ck_win("live-launch: another prompt file proceeds (0)", rc == 0 and "630" not in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", str(reap.REPO / ".claude/scratch/j3/brief.md")],
                     table([rel_launcher]), environ=planted18)
    ck_win("live-launch: a relative -f resolves against the repo", rc == 3 and "670" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", "c:\\X\\Brief.md"], table([fan_launcher]), environ=planted18)
    ck_win("live-launch: path case and separators are normalized", rc == 3 and "630" in out, out[-400:])
    rc, _, out = run(["live-launch", "--resume", "sess-abc"], table([resumer]), environ=planted18)
    ck_win("live-launch: a resume of a session a live launcher resumes refuses", rc == 3 and "671" in out, out[-400:])
    rc, _, out = run(["live-launch", "--resume", "sess-xyz"], table([resumed_seat]), environ=planted18)
    ck_win("live-launch: a resume of a session a live seat child runs refuses", rc == 3 and "672" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt], table([prompt_seat]), environ=planted18)
    ck_win("live-launch: an orphaned seat child carrying the prompt refuses, and its stop command is orphan",
       rc == 3 and "673" in out and "reap.py 673 --reason orphan" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--self", "630"], table([fan_launcher]), environ=planted18)
    ck_win("live-launch: the launch's own process is never its duplicate", rc == 0, out[-400:])
    child_of_self = row(631, 630, "bash.exe", fan_launcher["cmd"])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--self", "631"], table([fan_launcher, child_of_self]),
                     environ=planted18)
    ck_win("live-launch: nor is an ancestor of it (the re-exec stub, the -X wrapper)", rc == 0, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--fanout", "fan-a"], table([fan_launcher]),
                     environ=planted18)
    ck_win("live-launch: a sibling job of the same fan-out is not a duplicate", rc == 0, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--fanout", "fan-b"], table([fan_launcher]),
                     environ=planted18)
    ck_win("live-launch: another fan-out's job is", rc == 3, out[-400:])
    # The key is the JOB (its -l label, its -R record) on the shared prompt: an A/B shares one frozen brief
    # across arms on purpose, while a relaunch of the same job is still a duplicate.
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "s2", "--record", "C:/x/s2.record.json"],
                     table([fan_launcher]), environ=planted18)
    ck_win("live-launch: another job on the same prompt (own label and record) is not a duplicate", rc == 0, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "s1", "--record", "C:/x/new.record.json"],
                     table([fan_launcher]), environ=planted18)
    ck_win("live-launch: the same label on the same prompt is a duplicate", rc == 3 and "630" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "s2", "--record", "C:/x/s1.record.json"],
                     table([fan_launcher]), environ=planted18)
    ck_win("live-launch: the same record on the same prompt is a duplicate", rc == 3 and "630" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", "C:/x/other.md", "--label", "s1"], table([fan_launcher]),
                     environ=planted18)
    ck_win("live-launch: the same label on another prompt is another job", rc == 0, out[-400:])
    labeled_seat = row(674, 7214, "claude.exe", seat_cmd)
    envs18[674] = env18("launch-s4", parent=SID, prompt=prompt, label="seat-lbl")
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "seat-lbl"], table([labeled_seat]), environ=planted18)
    ck_win("live-launch: an orphaned seat child of the same label refuses", rc == 3 and "674" in out, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "other-job"], table([labeled_seat]), environ=planted18)
    ck_win("live-launch: an orphaned seat child of another label does not", rc == 0, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--label", "other-job"], table([prompt_seat]), environ=planted18)
    ck_win("live-launch: a seat child carrying no label cannot be matched to a job", rc == 0, out[-400:])
    # A launcher re-execs from a snapshot copy whose Windows block lacks the ids; its MSYS block has them.
    snap = row(632, 7213, "bash.exe", codex.replace("scripts/codex", "snap/codex") % ("C:/x/brief.md", "s1", "C:/x/s1"))
    peb_bare = lambda pid: b"PATH=/usr/bin\0" if pid == 632 else b""  # noqa: E731
    msys_ids = lambda pid: env18(S1, session=SID, fanout="fan-a") if pid == 632 else b""  # noqa: E731
    rc, _, out = run(["live-launch", "--prompt", prompt, "--fanout", "fan-a"], table([snap]),
                     environ=planted18, peb=peb_bare, msys=msys_ids)
    ck_win("live-launch: a sibling's snapshot re-exec, ids only in its MSYS block, is not a duplicate", rc == 0, out[-400:])
    rc, _, out = run(["live-launch", "--prompt", prompt, "--fanout", "fan-b"], table([snap]),
                     environ=planted18, peb=peb_bare, msys=msys_ids)
    ck_win("... while another fan-out's snapshot re-exec is", rc == 3 and "632" in out, out[-400:])
    # Tool shells predate the launch-id export and can sit outside the Windows ancestry chain.
    with tempfile.TemporaryDirectory(prefix="reap_direct_") as td:
        record = Path(td, "direct.json")
        other_record = Path(td, "other.json")
        Path(str(record) + ".pid").write_text("launch=direct-own winpid=680 pid=1000\n", encoding="utf-8")
        Path(str(other_record) + ".pid").write_text("launch=direct-other winpid=681 pid=1001\n", encoding="utf-8")
        direct_cmd = ('bash ".claude/scripts/codex_proxy_sidecar.sh" -m model-a -f %s '
                      '-l a8f-direct -R %s -e low -G survey -P stream') % (prompt, record.as_posix())
        wrappers = [
            row(35000, 7300, "bash.exe", r'"C:\Program Files\Git\bin\..\usr\bin\bash.exe" -c "source /c/Users/x/.claude/shell-snapshots/snapshot-bash-test.sh; ' + direct_cmd + '"'),
            row(62764, 7301, "bash.exe", r'"C:\Program Files\Git\bin\bash.exe" -c "source /c/Users/x/.claude/shell-snapshots/snapshot-bash-test.sh; ' + direct_cmd + '"'),
        ]
        own_launcher = row(680, 7302, "bash.exe", direct_cmd)
        scan = ["live-launch", "--prompt", prompt, "--self", "680", "--launch", "direct-own"]
        rc, killed, out = run(scan, table(wrappers + [own_launcher]))
        ck_win("live-launch: both own -c tool wrappers are skipped by their record's launch id",
           rc == 0 and killed == [], f"rc={rc}\n{out[-1000:]}")
        for peer_record in (other_record, record):
            second = row(681, 7303, "bash.exe", direct_cmd.replace(record.as_posix(), peer_record.as_posix()))
            rc, killed, out = run(scan, table(wrappers + [own_launcher, second]))
            ck_win("live-launch: a second launcher still refuses, including a reused -R (%s)" % peer_record.name,
               rc == 3 and "pid=681 " in out and "pid=35000 " not in out and "pid=62764 " not in out
               and killed == [], f"rc={rc}\n{out[-1000:]}")
        second_seat = row(682, 7304, "claude.exe", seat_cmd)
        rc, killed, out = run(scan, table(wrappers + [own_launcher, second_seat]),
                              environ=lambda pid: env18("direct-first", prompt=prompt) if pid == 682 else b"")
        ck_win("live-launch: a prior seat still refuses when the new launch reuses its record",
           rc == 3 and "pid=682 " in out and "pid=35000 " not in out and "pid=62764 " not in out
           and killed == [], f"rc={rc}\n{out[-1000:]}")
        different_wrapper = row(683, 7305, "bash.exe", wrappers[0]["cmd"].replace(record.as_posix(), other_record.as_posix()))
        rc, _, out = run(scan, table([own_launcher, different_wrapper]))
        ck_win("live-launch: a -c wrapper with a different -R for the same prompt refuses",
           rc == 3 and "pid=683 " in out, f"rc={rc}\n{out[-600:]}")
        for stamp in (None, "garbage\n", "launch=direct-other pid=1001\n"):
            pidfile = Path(str(record) + ".pid")
            if stamp is None:
                pidfile.unlink()
            else:
                pidfile.write_text(stamp, encoding="utf-8")
            rc, _, out = run(scan, table(wrappers + [own_launcher]))
            ck_win("live-launch: own-record wrappers without this launch's id still refuse (%r)" % stamp,
               rc == 3 and "pid=35000 " in out and "pid=62764 " in out, f"rc={rc}\n{out[-600:]}")

    rc, _, out = run(["live-launch", "--prompt", prompt], {}, environ=planted18)
    ck("live-launch: an unreadable process table is unknown (4), never a refusal", rc == 4, out[-400:])

    # The owner check reads the transcript where THIS session's login writes it: a session on a second
    # login (CLAUDE_CONFIG_DIR) keeps its transcripts under that login, not under ~/.claude.
    probe = "import sys; sys.path.insert(0, %r); import reap; print(reap.PROJECTS_ROOT)" % str(HERE.parent / "tools")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, CLAUDE_CONFIG_DIR=td)
        got = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
        want = os.path.normcase(os.path.normpath(os.path.join(td, "projects")))
        ck("a second login's session reads its transcripts under that login's projects dir",
           os.path.normcase(os.path.normpath(got.stdout.strip())) == want, (got.stdout + got.stderr)[-300:])
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    got = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
    ck("... and the default login still reads ~/.claude/projects",
       os.path.normcase(os.path.normpath(got.stdout.strip()))
       == os.path.normcase(os.path.normpath(os.path.expanduser("~/.claude/projects"))), (got.stdout + got.stderr)[-300:])

    # A login's session registry is found where the registry declares the login, not by a home glob.
    with tempfile.TemporaryDirectory() as td:
        declared, undeclared = Path(td, "declared"), Path(td, ".claude-stale")
        for folder in (declared, undeclared):
            (folder / "sessions").mkdir(parents=True)
        saved = getattr(reap, "login_dirs", None)
        reap.login_dirs = lambda: [str(declared), str(Path(td, "absent"))]
        try:
            got = REAL_SESSION_REGISTRY_DIRS()
        finally:
            if saved is None:
                del reap.login_dirs
            else:
                reap.login_dirs = saved
        ck("session_registry_dirs reads each declared login's sessions dir, and only those that exist",
           [os.path.normcase(str(p)) for p in got] == [os.path.normcase(str(declared / "sessions"))], str(got))

    # 9. a stop the session orders is remembered in its hook state (sidecar_stopped_launches), so the session
    #    watcher reads that job's end as the session's own: the launch id lands only after a kill lands, once.
    WHY = "Stop the env wrapper sidecar, it is stuck"
    L1, L2 = "launch-stop-l1", "launch-stop-l2"

    def launch_env(launch):
        return b"PATH=/usr/bin\0SIDECAR_LAUNCH_ID=" + launch.encode() + b"\0"

    def planted(pairs):
        return lambda pid: pairs.get(pid, b"")

    def stop_case(name):
        folder = tempfile.mkdtemp(prefix="reap_stop_%s_" % name, dir=SCRATCH)
        os.environ["HARNESS_HOOK_STATE_DIR"] = folder
        return Path(folder) / (SID[:8] + ".json")

    def stopped_of(path):
        return json.loads(path.read_text(encoding="utf-8")).get("sidecar_stopped_launches") if path.exists() else None

    launcher_env = {102: launch_env(L1), 103: launch_env(L1)}
    path = stop_case("owner")
    rc, killed, out = run(["102", "--reason", "owner", "--why", WHY], table(), environ=planted(launcher_env))
    ck("an owner stop of a launcher records its launch id once its kill lands",
       rc == 0 and 102 in killed and stopped_of(path) == [L1], f"rc={rc} killed={killed} state={stopped_of(path)}\n{out[-300:]}")

    # 7. control first: the same real stop records, so each refusal below is judged against a stop that lands.
    path = stop_case("control")
    rc, killed, out = run(["102", "--reason", "owner", "--why", WHY], table(), environ=planted(launcher_env))
    ck("7. control: the same real stop records its launch id", 102 in killed and stopped_of(path) == [L1],
       f"killed={killed} state={stopped_of(path)}\n{out[-300:]}")
    path = stop_case("dry")
    rc, killed, out = run(["102", "--reason", "owner", "--why", WHY, "--dry-run"], table(), environ=planted(launcher_env))
    ck("--dry-run records no stop", killed == [] and stopped_of(path) is None, f"killed={killed} state={stopped_of(path)}")

    path = stop_case("refused")
    rc, killed, out = run(["102", "--reason", "duplicate", "--why", "no twin here"], table(), environ=planted(launcher_env))
    ck("a refused-unjustified stop records no stop", rc == 2 and killed == [] and stopped_of(path) is None, out[-300:])
    path = stop_case("foreign")
    peer_env = {901: b"CLAUDE_CODE_SESSION_ID=11111111-2222-3333-4444-555555555555\0SIDECAR_LAUNCH_ID=" + L2.encode() + b"\0"}
    rc, killed, out = run(["901", "--reason", "owner", "--why", WHY], table(), environ=planted(peer_env))
    ck("a refused-foreign stop records no stop", rc == 2 and killed == [] and stopped_of(path) is None, out[-300:])
    path = stop_case("failed")
    rc, killed, out = run(["102", "--reason", "owner", "--why", WHY], table(), environ=planted(launcher_env), kill_ok=False)
    ck("a kill-failed stop records no stop", rc == 2 and stopped_of(path) is None, f"rc={rc}\n{out[-300:]}")

    path = stop_case("seat")
    rc, killed, out = run(["103", "--reason", "owner", "--why", WHY], table(), environ=planted({103: launch_env(L1)}))
    ck("a seat child carrying a launch id records that id", 103 in killed and stopped_of(path) == [L1],
       f"killed={killed} state={stopped_of(path)}\n{out[-300:]}")
    path = stop_case("bare")
    rc, killed, out = run(["101", "--reason", "owner", "--why", WHY], table(), environ=planted({}))
    ck("a process with no launch id records no stop", 101 in killed and stopped_of(path) is None,
       f"killed={killed} state={stopped_of(path)}")
    path = stop_case("twice")
    for _ in range(2):
        run(["102", "--reason", "owner", "--why", WHY], table(), environ=planted(launcher_env))
    ck("the same launch id stopped twice is listed once", stopped_of(path) == [L1], f"state={stopped_of(path)}")
    os.environ["HARNESS_HOOK_STATE_DIR"] = HOOK_STATE

    # taskkill /T exits nonzero when a tree member dies before its turn, though the target is gone.
    real_run = reap.subprocess.run
    for gone, want, name in ((True, True, "a nonzero taskkill whose target is gone is a kill"),
                             (False, False, "a nonzero taskkill whose target still runs is a failure")):
        def planted_run(cmd, *a, gone=gone, **k):
            if cmd[0] == "taskkill":
                return subprocess.CompletedProcess(cmd, 128, "", "ERROR: The process \"77\" not found.")
            listing = "INFO: No tasks are running which match the specified criteria." if gone else \
                '"bash.exe","77","Console","1","10,892 K"'
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        reap.subprocess.run = planted_run
        try:
            got = REAL_TASKKILL(77)
        finally:
            reap.subprocess.run = real_run
        ck(name, got is want, f"got={got}")

    print(f"\ntest_reap: {n - fails} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
