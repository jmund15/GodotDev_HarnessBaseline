#!/usr/bin/env python3
"""Proof for hooks/compact_directive_ledger.py — PreCompact asks the summary for an owner-directive ledger.

Claude Code appends PreCompact stdout to the compaction instructions. Only the summarizer still sees
what happened after each owner message, so it is the one place that can tell a standing decision
from a finished step or a replaced instruction (owner decision 2026-09-14, session 3259384b). The
hook lists every owner message by the same U-ID the SessionStart anchor re-injects, and asks for one
status line per ID, or names it in one compact settled list once two consecutive prior summaries
agree it is done, scoped or replaced (owner decision A2, 2026-09-15: settled IDs cost ~138 bytes/msg
forever without this). Each case runs the real hook as a process; an exit outside {0} or a traceback
is a CRASH, never a pass.

    python3 .claude/tests/test_compact_directive_ledger.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import _settings_probe

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from sidecar_launch import git_bash  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "compact_directive_ledger.py")
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import compact_directive_ledger as ledger_hook  # noqa: E402
SID = "beef5678-0000-4000-8000-ledgerproof"
DIRECTIVE = "Drive the harness optimization directive to completion; session end only when nothing is left."
LONG = "Long owner message. " + "detail " * 400 + "TAIL-MARKER"
ANSWER = ('The user answered: "Which edits should land before the commit?"="Do all recommendations". '
          "Read the answers carefully — they may request clarification, changes, or that you not proceed "
          "— and follow what they actually say.")
MAX_OUTPUT_BYTES = 9_000
COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/compact_directive_ledger.py"'


def user(content, **extra):
    return dict({"type": "user", "timestamp": "2026-09-14T13:01:00Z",
                 "message": {"role": "user", "content": content}}, **extra)


def rows():
    return [
        user(DIRECTIVE),
        user("<system-reminder>hook echo that is not the owner</system-reminder>"),
        user("<command-name>/model</command-name>\n<command-message>model</command-message>\n"
             "<command-args>fable</command-args>"),
        user("[Request interrupted by user]"),
        user("<command-message>orchestration</command-message>\n<command-name>/orchestration</command-name>\n"
             "<command-args>route the audit lanes to luna</command-args>"),
        user(LONG),
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_q1", "name": "AskUserQuestion", "input": {}}]}},
        user([{"type": "tool_result", "tool_use_id": "toolu_q1", "content": ANSWER}]),
        user("This session is being continued from a previous conversation. SUMMARY-PARAPHRASE",
             isCompactSummary=True),
        user("Continue"),
        user("/model fable"),
    ]


def summary_row(text, ts="2026-09-14T14:00:00Z"):
    return dict({"type": "user", "timestamp": ts, "message": {"role": "user", "content": text},
                 "isCompactSummary": True})


def rows_zero_summary():
    """Today's `rows()` fixture with its one compact-summary record dropped: zero prior summaries,
    the no-regression control the fixed golden file in fixtures/ was captured against."""
    return [r for r in rows() if not r.get("isCompactSummary")]


def carry_forward_rows():
    """U1 is done in two summaries in a row -> settles into the compact list. U4 is done in only
    the latest summary -> stays a full entry (needs two in a row). U2 holds in both -> never
    settles. U6 postdates the latest summary and was never statused."""
    return [
        user("U1 settled directive body"),
        user("U2 holds directive body"),
        summary_row("## Owner directives\nU1 — done — did the thing\nU2 — holds — keep watching X\n"),
        user("U4 filler between summaries"),
        summary_row("## Owner directives\nU1 — done — did the thing\nU2 — holds — keep watching X\n"
                    "U4 — done — trivial\n"),
        user("U6 new message after latest summary"),
    ]


def reopened_rows():
    """U1 is done in two summaries running, which would settle it -- but a later owner message
    reopens it and the newest summary marks it holds again, so the newest two summaries disagree
    and it must stay a full entry (owner decision 2026-09-14: U9587 went done -> holds this way)."""
    return [
        user("U1 reopened directive body"),
        summary_row("## Owner directives\nU1 — done — did the thing\n"),
        summary_row("## Owner directives\nU1 — done — did the thing\n"),
        user("U4 owner reopens U1 here"),
        summary_row("## Owner directives\nU1 — holds — reopened by owner\n"),
    ]


def one_summary_rows():
    """Exactly one prior summary: fewer than two, so carry-forward must not engage."""
    return [
        user("U1 one-summary body"),
        summary_row("## Owner directives\nU1 — done — did the thing\n"),
    ]


def realistic_rows(done=25, scoped=6, replaced=2, holds=12, fresh=1):
    """Stand-in for the measured 46-message session (33 terminal, 12 holds, 1 unstatused), with
    two consecutive summaries agreeing on every terminal ID so carry-forward has real work to do."""
    msgs, ident, terminal_ids, holds_ids = [], 1, [], []
    for kind, n in (("done", done), ("scoped", scoped), ("replaced", replaced)):
        for _ in range(n):
            msgs.append(user(f"U{ident} {kind} directive body, a realistic length of owner request text."))
            terminal_ids.append((ident, kind))
            ident += 1
    for _ in range(holds):
        msgs.append(user(f"U{ident} holds directive body, a realistic length of owner request text."))
        holds_ids.append(ident)
        ident += 1

    def section():
        lines = ["## Owner directives"]
        for i, kind in terminal_ids:
            status = "replaced by U9999" if kind == "replaced" else kind
            lines.append(f"U{i} — {status} — did a step")
        for i in holds_ids:
            lines.append(f"U{i} — holds — still binds")
        return "\n".join(lines) + "\n"

    msgs.append(summary_row(section()))
    msgs.append(summary_row(section()))
    for _ in range(fresh):
        msgs.append(user(f"U{ident} fresh message after the latest summary"))
        ident += 1
    return msgs


def write_transcript(tmp, content_rows, name=SID):
    path = Path(tmp) / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in content_rows) + "\n", encoding="utf-8")
    return path


def run_hook(tmp, payload, env_extra=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    return out, r.returncode != 0 or "Traceback" in err


CHECKPOINTING = os.path.join(HERE, "..", "skills", "orchestration", "reference", "checkpointing.md")
ANCHOR_HOOK = os.path.join(HERE, "..", "hooks", "compact_directive_anchor.py")
# Bytes of the carry-forward instruction text before R5a, measured by running the hook on session
# 30b7e19d. The echoed ledger does not grow net of the summary-fidelity rules R5a adds.
INSTRUCTION_BYTES_BEFORE = 3706
WHOLE_RE = re.compile(r"^Anchor prints whole: (.*)\.$", re.MULTILINE)


def fidelity_bullets():
    """The bullets of checkpointing.md `## Summary fidelity`, read straight from the file."""
    with open(CHECKPOINTING, encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(r"^## Summary fidelity\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return [l.strip() for l in (match.group(1) if match else "").splitlines() if l.startswith("- ")]


def summary_fidelity_cases():
    cases = []
    bullets = fidelity_bullets()
    joined = " ".join(bullets)
    with tempfile.TemporaryDirectory() as tmp:
        transcript = write_transcript(tmp, rows())
        out, crashed = run_hook(tmp, {"hook_event_name": "PreCompact", "trigger": "auto", "session_id": SID,
                                      "transcript_path": str(transcript)})
    cases.append(("checkpointing.md owns a Summary fidelity section of rule bullets", len(bullets) >= 5))
    cases.append(("the hook emits every bullet of that section to the summarizer, verbatim",
                  not crashed and bool(bullets) and all(b in out for b in bullets)))
    for label, phrase in (
            ("re-issued strings are kept character for character, with no elision inside a quote",
             "character for character"),
            ("a pending step whose command already ran once keeps that exact command", "already ran once"),
            ("a guard demand is stated only as its denial text", "denial text"),
            ("an owner ID gets one line and a closed lane closes its directives", "lane closes"),
            ("landed-commit sha lists and fixed errors are dropped", "landed-commit")):
        cases.append((label, phrase in joined and phrase in out))
    cases.append(("the elision rule names the character", "…" in joined))
    fidelity = len(ledger_hook._fidelity_section().encode("utf-8"))
    cases.append((f"the carry-forward text, net of the fidelity section ({fidelity} bytes), stays within its "
                  f"pre-R5a {INSTRUCTION_BYTES_BEFORE} bytes",
                  fidelity > 0 and len(ledger_hook.INSTRUCTIONS_CARRY_FORWARD.encode("utf-8")) - fidelity
                  <= INSTRUCTION_BYTES_BEFORE))
    cases.append(("the legacy text stays below the carry-forward text",
                  len(ledger_hook.INSTRUCTIONS.encode("utf-8")) < len(ledger_hook.INSTRUCTIONS_CARRY_FORWARD.encode("utf-8"))))
    return cases


def heavy_rows(count=30):
    """Mid-length owner messages: the anchor prints its ends whole and clips or omits the middle."""
    return [user(f"Heavy owner message {n}. " + "detail " * 35 + f"END{n}") for n in range(count)]


def whole_in_anchor(tmp, transcript, session):
    """{U-id} of the owner messages whose full text the real anchor run prints."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, PYTHONIOENCODING="utf-8", HARNESS_PENDING_COUNT="0")
    env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
    payload = {"source": "compact", "session_id": session, "transcript_path": str(transcript)}
    r = subprocess.run([sys.executable, ANCHOR_HOOK], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    digest = ledger_hook.anchor._digest()
    messages = [m for m in digest.owner_prompts(transcript) if ledger_hook.anchor.keep(m)]
    shaped = ledger_hook.anchor.shape
    return {m["index"] for m in messages if shaped(m["content"])[0] in out}, len(messages), r.returncode == 0


def anchor_overlap_cases():
    """R4c: the ledger names the IDs the anchor prints whole, so a holds line for one carries no quote."""
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, content_rows in (("heavy", heavy_rows()), ("mixed", rows()),
                                   ("tiny", [user("Ship the filter."), user("Then stop.")])):
            transcript = write_transcript(tmp, content_rows, name)
            out, crashed = run_hook(tmp, {"hook_event_name": "PreCompact", "trigger": "auto", "session_id": name,
                                          "transcript_path": str(transcript)})
            anchor_whole, total, anchor_ok = whole_in_anchor(tmp, transcript, name)
            match = WHOLE_RE.search(out)
            claimed = match.group(1) if match else None
            if claimed == "every owner message":
                ledger_whole = anchor_whole if len(anchor_whole) == total else None
            elif claimed is None:
                ledger_whole = None
            else:
                ledger_whole = {int(i) for i in re.findall(r"U(\d+)", claimed)}
            cases.append((f"{name}: the ledger names exactly the IDs the anchor prints whole "
                          f"({claimed!r} vs {sorted(anchor_whole)})",
                          not crashed and anchor_ok and ledger_whole == anchor_whole))
            if name == "heavy":
                cases.append(("heavy: the anchor really clips part of the session (the control is not vacuous)",
                              0 < len(anchor_whole) < total))
            if name == "tiny":
                cases.append(("tiny: every message is whole, so the ledger says so in one phrase",
                              claimed == "every owner message"))
    cases.append(("a holds line for an ID the anchor prints whole is ID, status and scope only",
                  "ID, status and scope only" in ledger_hook.INSTRUCTIONS_CARRY_FORWARD
                  and "ID, status and scope only" in ledger_hook.INSTRUCTIONS))
    cases.append(("every other holds line still quotes the owner's binding sentences verbatim",
                  "verbatim" in ledger_hook.INSTRUCTIONS_CARRY_FORWARD and "binding sentences" in ledger_hook.INSTRUCTIONS))
    return cases


def main():
    cases = []
    if not os.path.exists(HOOK):
        print("RED: hooks/compact_directive_ledger.py does not exist")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        transcript = write_transcript(tmp, rows())
        payload = {"hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": None,
                   "session_id": SID, "transcript_path": str(transcript)}

        out, crashed = run_hook(tmp, payload)
        cases.append(("a compaction run does not crash", not crashed))
        cases.append(("the summary is asked for an owner directives section", "## Owner directives" in out))
        cases.append(("each ID gets one status from a fixed vocabulary",
                      all(word in out for word in ("holds", "done", "replaced by U", "scoped", "unclear"))))
        cases.append(("status is judged from what happened after the message", "what happened after" in out))
        cases.append(("no ID may be dropped", "every ID" in out))
        # Live defect 2026-09-14: "U9433 — done — ... (codify report still to finish)" and a finished step
        # message carrying a standing constraint were both marked done.
        cases.append(("a message with any part still pending or still binding holds, naming that part",
                      "still pending or still binding" in out))
        # Owner D1 2026-09-14: summaries grew 18.7 -> 24.5 KB over five compactions by restating on-disk files.
        cases.append(("the summary gets a required artifacts section, one line per path",
                      "Artifacts (required section" in out and "one line per changed file" in out
                      and "never restated in any section (code, constants, test names, prompt or report text)" in out
                      and "exact error" in out))
        # Live defect 2026-09-22 (fd4ff546): the rule read as Artifacts-only, so "Key technical concepts"
        # restated the plan's design section, and an open question's observed value came back wrong
        # ("clean" -> "right-sized" where the file said ["clean", "low"]).
        cases.append(("the no-restating rule covers every section, and open questions keep their observed values",
                      "never restated in any section" in out and "Never restate a file's contents" not in out and "open question's observed values" in out))
        cases.append(("every owner message is listed by its anchor ID",
                      all(f"U{i} ·" in out for i in (1, 5, 6, 8, 10))))
        cases.append(("hook echoes, interrupts, the summary and session-control commands are not listed",
                      not any(f"U{i} ·" in out for i in (2, 3, 4, 9, 11))))
        cases.append(("each entry shows its send time and opening words",
                      "U1 · 09-14 13:01 · Drive the harness optimization" in out))
        cases.append(("an entry is a short head, not the whole message", "TAIL-MARKER" not in out
                      and all(len(line) <= 160 for line in out.splitlines() if line.startswith("U"))))
        cases.append(("an answer entry shows the owner's answer words",
                      "Do all recommendations" in out and "Read the answers carefully" not in out))
        cases.append(("a command entry shows the command and its arguments",
                      "/orchestration route the audit lanes to luna" in out))
        cases.append(("no raw command markup is emitted", "<command-" not in out))
        cases.append(("the ledger names the SessionStart anchor that re-injects the same IDs",
                      "same IDs" in out))

        # What the 30-compaction audit (2026-10-01) found summaries lose: a ruling's scope and start,
        # an option reply's meaning, exact syntax a denial taught, and hook-gated reads carried as done.
        cases.append(("a holds line carries the ruling's scope, when it stops and its doc anchor",
                      "when it stops" in out and "doc anchor" in out))
        cases.append(("a ruling holds while it constrains work, even after its first use",
                      "after its first use" in out))
        cases.append(("a reply that only names an option gets that option's text written out",
                      "only names an option" in out))
        # Two 2026-10-12 audits: every holds ruling written twice (a separate rulings section), the
        # owner's messages restated three times, and a next step copied into three sections.
        cases.append(("each fact has one section; the others cite it", "one section" in out
                      and "## Rulings in force" not in out))
        cases.append(("the built-in user-message list points at the directives instead of restating them",
                      "All user messages" in out and "see Owner directives" in out))
        cases.append(("an unanswered owner question survives as its own section, with options, pick and default",
                      "Open questions (required section" in out and "your pick" in out and "default" in out))
        cases.append(("an uncommitted file names whose it is, from evidence", "whose it is" in out))
        cases.append(("a pending step carries exact values, never a placeholder", "placeholder" in out))
        cases.append(("a job's state is the last observation with its time, never an inference",
                      "last observation" in out))
        cases.append(("exact syntax a denial or a usage print taught is kept in full",
                      "a denial or a usage print" in out))
        cases.append(("a hook-gated read is never carried as done", "compaction resets" in out))
        cases.append(("only consumed and unrated dispatches are listed, with their outcome",
                      "Pending ratings" in out and "not yet rated" in out))

        # Facts the summarizer misstated in 5 of 30 compactions ("reply not yet sent" after it was sent),
        # read from the transcript instead: the last messages the owner saw and the jobs still running.
        def said(text, ts):
            return {"type": "assistant", "timestamp": ts,
                    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

        def bash_bg(tool_id, desc, ts):
            return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash",
                 "input": {"command": "python3 run.py", "description": desc, "run_in_background": True}}]}}

        def bg_result(tool_id, task):
            return user([{"type": "tool_result", "tool_use_id": tool_id,
                          "content": f"Command running in background with ID: {task}. Output is being written to: x"}],
                         toolUseResult={"backgroundTaskId": task})

        def notice(task):
            return {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-09-14T13:30:00Z",
                    "content": f"<task-notification>\n<task-id>{task}</task-id>\n<status>completed</status>\n"
                               f"<summary>Background command done</summary>\n</task-notification>"}

        fact_rows = rows() + [
            bash_bg("toolu_b1", "RUNNING-JOB-DESC", "2026-09-14T13:10:00Z"), bg_result("toolu_b1", "bgjobrun"),
            bash_bg("toolu_b2", "FINISHED-JOB-DESC", "2026-09-14T13:11:00Z"), bg_result("toolu_b2", "bgjobdone"),
            notice("bgjobdone"),
            said("INTERIM-MARKER: still checking.", "2026-09-14T13:39:00Z"),
            said("LAST-SHOWN-MARKER: the report is sent.\nSecond line of the report.", "2026-09-14T13:40:00Z"),
            {"type": "assistant", "timestamp": "2026-09-14T13:41:00Z", "message": {
                "role": "assistant", "model": "<synthetic>",
                "content": [{"type": "text", "text": "SYNTHETIC-MARKER No response requested."}]}},
        ]
        out, crashed = run_hook(tmp, dict(payload, transcript_path=str(write_transcript(tmp, fact_rows, "facts"))))
        cases.append(("facts run does not crash", not crashed))
        cases.append(("the last message the owner saw is given with its send time",
                      "LAST-SHOWN-MARKER: the report is sent." in out and "Second line" not in out
                      and "13:40" in out))
        cases.append(("a turn's interim status line is not listed; its closing message is",
                      "INTERIM-MARKER" not in out))
        cases.append(("a client-synthetic assistant row is not a message the owner saw", "SYNTHETIC-MARKER" not in out))
        cases.append(("a background job with no completion notice is listed as running",
                      "bgjobrun" in out and "RUNNING-JOB-DESC" in out))
        cases.append(("a finished background job is not listed", "bgjobdone" not in out))
        cases.append(("the facts run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))

        # A malformed row degrades the facts block to nothing; the ledger and instructions still print.
        for label, odd in (
                ("a null text block", {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": None}]}}),
                ("a string tool input", {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_o1", "name": "Bash", "input": "ls"}]}})):
            out, crashed = run_hook(tmp, dict(payload, transcript_path=str(write_transcript(
                tmp, rows() + [odd], "odd"))))
            cases.append((f"{label} in the transcript does not lose the instructions or the ledger",
                          not crashed and "## Owner directives" in out and "U1 ·" in out))

        # The turn-boundary rule and the job/notice join are separate collectors, each proven apart.
        def text_row(text, ts):
            return {"type": "assistant", "timestamp": ts,
                    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

        closers = ledger_hook.TurnClosers()
        for row in (user("first ask"), text_row("interim", "2026-09-14T13:00:00Z"),
                    text_row("closing one\nmore", "2026-09-14T13:01:00Z"),
                    {"type": "queue-operation", "operation": "enqueue", "content": "x"},
                    text_row("closing two", "2026-09-14T13:02:00Z"),
                    user([{"type": "tool_result", "tool_use_id": "t", "content": "r"}]),
                    text_row("closing two, later", "2026-09-14T13:03:00Z"),
                    user("second ask"), text_row("closing three", "2026-09-14T13:04:00Z"),
                    text_row("sidechain", "2026-09-14T13:05:00Z") | {"isSidechain": True}):
            closers.feed(row)
        cases.append(("a turn closes at a queued notice or a typed prompt, never at a tool result",
                      [first for _, first in closers.finish()] == ["closing one", "closing two, later", "closing three"]))

        jobs = ledger_hook.BackgroundJobs()
        for row in (bash_bg("tb1", "ALPHA", "2026-09-14T13:10:00Z"), bg_result("tb1", "jobalpha"),
                    bash_bg("tb2", "BETA", "2026-09-14T13:11:00Z"), bg_result("tb2", "jobbeta"),
                    notice("jobbeta"),
                    {"type": "assistant", "timestamp": "t", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "tb3", "name": "Bash", "input": {"command": "ls"}}]}},
                    bg_result("tb3", "jobforeground")):
            jobs.feed(row)
        cases.append(("a launched job with no completion notice is running; a noticed or foreground one is not",
                      [task for task, _, _ in jobs.running()] == ["jobalpha"]))

        # A Workflow launch reports "Task ID:", not the Bash wording; a 2026-10-12 summary lost a running
        # Workflow job this way. Result text cut from that session's transcript.
        def wf_launch(tool_id, ts):
            return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Workflow",
                 "input": {"scriptPath": ".claude/workflows/dispatch.js",
                           "args": {"jobs": [{"label": "WF-LABEL"}], "spillDir": "x"}}}]}}

        def wf_result(tool_id, task):
            return user([{"type": "tool_result", "tool_use_id": tool_id, "content":
                          f"Workflow launched in background. Task ID: {task}\nSummary: Run independent pinned "
                          f"jobs in parallel\nTranscript dir: C:\\x\\subagents\\workflows\\"}],
                         toolUseResult={"status": "async_launched", "taskId": task, "taskType": "local_workflow"})

        jobs = ledger_hook.BackgroundJobs()
        for row in (wf_launch("tw1", "2026-09-14T13:12:00Z"), wf_result("tw1", "wfrunning"),
                    wf_launch("tw2", "2026-09-14T13:13:00Z"), wf_result("tw2", "wfdone"), notice("wfdone"),
                    {"type": "assistant", "timestamp": "t", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "tw3", "name": "Workflow", "input": {"scriptPath": "x.js"}}]}},
                    user([{"type": "tool_result", "tool_use_id": "tw3",
                           "content": "PreToolUse:Workflow hook error: Dispatch DENIED"}])):
            jobs.feed(row)
        running = jobs.running()
        cases.append(("a background Workflow with no completion notice is running, named by its job labels; a "
                      "noticed or denied one is not",
                      [task for task, _, _ in running] == ["wfrunning"] and "WF-LABEL" in running[0][1]))
        # A tool that merely quotes the launch wording (a Read of a transcript) carries no task id field.
        jobs = ledger_hook.BackgroundJobs()
        for row in ({"type": "assistant", "timestamp": "t", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "tr1", "name": "Read", "input": {"file_path": "x.md"}}]}},
                    user([{"type": "tool_result", "tool_use_id": "tr1",
                           "content": "Workflow launched in background. Task ID: phantomid\nSummary: s"}])):
            jobs.feed(row)
        cases.append(("a result that only quotes 'Task ID:' registers no job", jobs.running() == []))
        # A background Agent reports `agentId` with `isAsync` (keys cut from sessions 027997c5 and b48600e4); its
        # completion notice names that id. A foreground Agent's result also carries an agentId.
        jobs = ledger_hook.BackgroundJobs()
        for tool_id, task, is_async in (("ta1", "a58efa0a543e695d1", True), ("ta2", "abc8cba10750e49c6", False)):
            jobs.feed({"type": "assistant", "timestamp": "t", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_id, "name": "Agent",
                 "input": {"description": "AGENT-JOB", "prompt": "p", "run_in_background": is_async}}]}})
            jobs.feed(user([{"type": "tool_result", "tool_use_id": tool_id, "content": "Async agent launched"}],
                           toolUseResult={"agentId": task, "isAsync": is_async, "description": "AGENT-JOB",
                                          "outputFile": "x.output", "canReadOutputFile": True}))
        cases.append(("a background Agent with no completion notice is running; a foreground Agent is not",
                      [task for task, _, _ in jobs.running()] == ["a58efa0a543e695d1"]))
        cases.append(("the stamp and the clip live in the anchor, not as ledger copies",
                      not hasattr(ledger_hook, "_stamp") and not hasattr(ledger_hook, "_clip")
                      and ledger_hook.anchor.head_clip("a  b\nc" + "x" * 50, 10) == "a b cxxxx…"))

        many = [user(f"PROMPT-{i:04d} x") for i in range(3000)]
        out, crashed = run_hook(tmp, dict(payload, transcript_path=str(write_transcript(tmp, many, "many"))))
        cases.append(("an over-budget run does not crash", not crashed))
        cases.append(("an over-budget run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))
        cases.append(("the first and newest IDs survive the budget", "U1 ·" in out and "U3000 ·" in out))
        cases.append(("omitted IDs are counted, not silent", "omitted" in out))

        out, crashed = run_hook(tmp, dict(payload, transcript_path=str(Path(tmp) / "missing.jsonl")))
        cases.append(("an unreadable transcript still asks for the section without IDs",
                      not crashed and "## Owner directives" in out and "U1 ·" not in out))
        out, crashed = run_hook(tmp, [])
        cases.append(("a non-object payload is a silent no-op", out.strip() == "" and not crashed))
        out, crashed = run_hook(tmp, payload, {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": str(transcript)})
        cases.append(("a sidecar child is silent", out.strip() == "" and not crashed))
        out, crashed = run_hook(tmp, dict(payload, agent_id="a1b2c3d4e5f6"))
        cases.append(("a subagent's compaction gets no parent owner ledger", out.strip() == "" and not crashed))
        out, crashed = run_hook(tmp, dict(payload, agent_id=""))
        cases.append(("an empty agent_id is a parent and gets the ledger",
                      "## Owner directives" in out and not crashed))

        project = Path(HERE).parents[1]
        settings = _settings_probe.settings(project / ".claude")
        registered = [hook.get("command") for group in settings.get("hooks", {}).get("PreCompact", [])
                      for hook in group.get("hooks", [])]
        cases.append(("settings registers the ledger in PreCompact", COMMAND in registered))
        rc, reg_out, reg_err = 1, "", "not registered"
        if COMMAND in registered:
            # Git Bash, as Claude Code runs hooks; a bare "bash" from Windows Python can resolve to WSL.
            bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or git_bash()
            env = dict(os.environ, CLAUDE_PROJECT_DIR=project.as_posix(), PYTHONIOENCODING="utf-8")
            env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
            result = subprocess.run([bash, "-lc", COMMAND], input=json.dumps(payload), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
            rc, reg_out, reg_err = result.returncode, result.stdout or "", result.stderr or ""
        cases.append(("the exact registered command runs through bash",
                      rc == 0 and "Traceback" not in reg_err and "U1 ·" in reg_out))

    with tempfile.TemporaryDirectory() as tmp2:
        # A2 status carry-forward -----------------------------------------------------------
        cf_transcript = write_transcript(tmp2, carry_forward_rows(), "carryfwd")
        cf_payload = {"hook_event_name": "PreCompact", "trigger": "auto", "custom_instructions": None,
                      "session_id": "carryfwd", "transcript_path": str(cf_transcript)}
        out, crashed = run_hook(tmp2, cf_payload)
        cases.append(("carry-forward: two consecutive terminal summaries do not crash the hook",
                      not crashed))
        cases.append(("an ID settled in the last two summaries is named in one compact line, not a full entry",
                      "settled (status carried forward" in out and "U1" in out and "U1 ·" not in out))
        cases.append(("an ID done in only the latest summary keeps its full entry (needs two in a row)",
                      "U4 ·" in out))
        cases.append(("a holds ID never carries forward, in the latest summary or the compact line",
                      "U2 ·" in out))
        cases.append(("a message newer than the latest summary keeps its full entry",
                      "U6 ·" in out))
        cases.append(("carry-forward mode asks for one comma-separated settled list",
                      "comma-separated" in out and "Settled (done/scoped/replaced)" in out))
        cases.append(("carry-forward mode still names the reopen exception",
                      "reopen" in out.lower()))

        # Reopening must remain possible ------------------------------------------------------
        reopened_transcript = write_transcript(tmp2, reopened_rows(), "reopened")
        out, crashed = run_hook(tmp2, dict(cf_payload, session_id="reopened",
                                           transcript_path=str(reopened_transcript)))
        cases.append(("reopened: a later 'holds' status blocks carry-forward even after two prior "
                      "terminal summaries", not crashed and "U1 ·" in out
                      and "settled (status carried forward" not in out))

        # Fewer than two prior summaries: exactly today's behavior ----------------------------
        one_transcript = write_transcript(tmp2, one_summary_rows(), "onesummary")
        out, crashed = run_hook(tmp2, dict(cf_payload, session_id="onesummary",
                                           transcript_path=str(one_transcript)))
        cases.append(("one prior summary is fewer than two: no carry-forward, no new instructions wording",
                      not crashed and "U1 ·" in out and "comma-separated" not in out
                      and "settled (status carried forward" not in out))

        # No-regression: zero prior summaries reproduces today's exact bytes -----------------
        # "Today" is reconstructed from the hook's own legacy pieces (INSTRUCTIONS, ledger_lines),
        # unchanged by A2/A3 for this path, rather than a frozen golden file that could rot or
        # silently miss the .claude/tests/ gitignore rule that only force-tracks named files.
        zero_transcript = write_transcript(tmp2, rows_zero_summary(), "zerosummary")
        legacy_messages = [m for m in ledger_hook.anchor._digest().owner_prompts(zero_transcript)
                           if ledger_hook.anchor.keep(m)]
        header = "Owner messages (ID · sent · opening words):"
        legacy_budget = ledger_hook.TOTAL_BYTES - ledger_hook.size([ledger_hook.INSTRUCTIONS, header])
        legacy_lines = ledger_hook.ledger_lines(legacy_messages, legacy_budget)
        expected = ledger_hook.INSTRUCTIONS + "\n" + header + "\n" + "".join(l + "\n" for l in legacy_lines)
        out, crashed = run_hook(tmp2, dict(payload, session_id="zerosummary",
                                           transcript_path=str(zero_transcript)))
        out = re.sub(r"^Anchor prints whole: .*\n", "", out, count=1, flags=re.MULTILINE)  # own cases below
        new_bytes, expected_bytes = len(out.encode("utf-8")), len(expected.encode("utf-8"))
        cases.append((f"no prior summary: output is byte-identical to today's legacy path "
                      f"({new_bytes} vs {expected_bytes} bytes)", not crashed and out == expected))

        # Measured saving on a realistic 46-message session -----------------------------------
        realistic = realistic_rows()
        realistic_legacy = [m for m in realistic if not m.get("isCompactSummary")]
        rt_transcript = write_transcript(tmp2, realistic, "realistic")
        rt_legacy_transcript = write_transcript(tmp2, realistic_legacy, "realisticlegacy")
        out_new, crashed_new = run_hook(tmp2, dict(cf_payload, session_id="realistic",
                                                    transcript_path=str(rt_transcript)))
        out_old, crashed_old = run_hook(tmp2, dict(cf_payload, session_id="realisticlegacy",
                                                    transcript_path=str(rt_legacy_transcript)))
        # The saving is the owner lines': each arm's fixed instruction text is subtracted, so growth in
        # the shared instructions cannot hide or fake it.
        old_bytes = len(out_old.encode("utf-8")) - len(ledger_hook.INSTRUCTIONS.encode("utf-8"))
        new_bytes = len(out_new.encode("utf-8")) - len(ledger_hook.INSTRUCTIONS_CARRY_FORWARD.encode("utf-8"))
        print(f"realistic 46-message ledger lines: legacy {old_bytes} bytes -> carry-forward {new_bytes} bytes")
        cases.append(("a realistic 46-message session (33 terminal, 12 holds) compacts to a "
                      "meaningfully smaller ledger", not crashed_new and not crashed_old
                      and new_bytes < old_bytes * 0.65))

    cases += summary_fidelity_cases() + anchor_overlap_cases()

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
