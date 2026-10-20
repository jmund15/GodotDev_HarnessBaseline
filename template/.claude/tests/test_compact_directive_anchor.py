#!/usr/bin/env python3
"""Proof for hooks/compact_directive_anchor.py — SessionStart(compact) re-injects the owner's own words.

A compaction summary is a model paraphrase; across several compactions the owner's directive
survives only as a rewording (observed 2026-09-14, session 3259384b: the owner restated the
directive right after two compactions). The hook re-injects the owner's messages inside a byte cap,
framed as a record rather than open orders, and writes the uncapped text to logs/. The first live
compaction dropped three whole mid-session messages; a later revision demanded a whole-file read on
nearly every compaction, which the owner rejected as the bloat the hook exists to avoid. A clipped
message is now recalled one at a time with `--show`. Each case runs the real hook as a process on a
fixture transcript; an exit outside {0} or a traceback is a CRASH, never a pass.

    python3 .claude/tests/test_compact_directive_anchor.py
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import _settings_probe

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from sidecar_launch import git_bash  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "..", "hooks", "compact_directive_anchor.py")
SID = "abcd1234-0000-4000-8000-compactproof"
REGISTRATION_SID = "f9a1b2c3-0000-4000-8000-registration-proof"
DIRECTIVE = "Drive the harness optimization directive to completion; session end only when nothing is left."
LONG = "Long owner message. " + "detail " * 400 + "TAIL-MARKER"
ANSWER = 'The user answered: "Which edits?"="Do all recommendations"'
LONG_QUESTION = "Which of these options " + "q" * 150 + " should land?"
TRAILED_ANSWER = (f'The user answered: "{LONG_QUESTION}"="Keep the owner words". Read the answers carefully '
                  "— they may request clarification, changes, or that you not proceed — and follow what "
                  "they actually say.")
REFERENCED = "1f2e3d4c"
UNREFERENCED = "deadbeef"
MESSAGE_CEILING = 2000
MAX_OUTPUT_BYTES = 10_240
FULL_REL = f"logs/compact_directives_{SID[:8]}.md"
SHOW_COMMAND = f"compact_directive_anchor.py --show {FULL_REL}"


def user(content, **extra):
    return dict({"type": "user", "timestamp": "2026-09-14T13:01:00Z",
                 "message": {"role": "user", "content": content}}, **extra)


def question(tool_id, answer):
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_id, "name": "AskUserQuestion", "input": {}}]}},
        user([{"type": "tool_result", "tool_use_id": tool_id, "content": answer}]),
    ]


def base_rows():
    return [
        user(DIRECTIVE),
        user("<system-reminder>hook echo that is not the owner</system-reminder>"),
        user("<command-name>/model</command-name>\n<command-message>model</command-message>\n"
             "<command-args>fable</command-args>"),
        user("[Request interrupted by user]"),
        user("<command-message>orchestration</command-message>\n<command-name>/orchestration</command-name>\n"
             "<command-args>route the audit lanes to sol</command-args>"),
        user(LONG),
        *question("toolu_q1", ANSWER),
        user("This session is being continued from a previous conversation that ran out of context. "
             "SUMMARY-PARAPHRASE", isCompactSummary=True),
        user("Continue"),
        user("/overnight finish"),
        user("/tmp/file failed"),
        user("/model fable"),
        user("/effort xhigh"),
        user("/effort"),
        *question("toolu_q2", TRAILED_ANSWER),
        user(f"Finish what session {REFERENCED} started; ignore the hash {UNREFERENCED}."),
    ]


def write_transcript(tmp, rows, name=SID):
    path = Path(tmp) / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def run_hook(tmp, payload, env_extra=None):
    """(stdout exactly as emitted, crashed, stderr); stdout keeps any CR so byte bounds are real."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, PYTHONIOENCODING="utf-8", HARNESS_PENDING_COUNT="0")
    env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
    env.update(env_extra or {})
    for key in [k for k, v in env.items() if v is None]:
        env.pop(key)
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload).encode("utf-8"),
                       capture_output=True, env=env, timeout=120)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    return out, r.returncode != 0 or "Traceback" in err, err


def run_show(tmp, args):
    """(stdout + stderr, exit code, traceback seen) for `hook --show <file> <ID>...` run from the project dir."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, HOOK, "--show", *args], capture_output=True, env=env, cwd=tmp,
                       stdin=subprocess.DEVNULL, timeout=120)
    text = (r.stdout or b"").decode("utf-8", errors="replace") + (r.stderr or b"").decode("utf-8", errors="replace")
    return text, r.returncode, "Traceback" in text


def whole_plan_cases():
    """R4c: the anchor's own plan names the messages it prints whole, the set the ledger reads."""
    cases = []
    spec = importlib.util.spec_from_file_location("anchor_for_whole_plan", HOOK)
    anchor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(anchor)
    heavy = [user(f"Heavy owner message {n}. " + "detail " * 35 + f"END{n}") for n in range(30)]
    with tempfile.TemporaryDirectory() as tmp:
        transcript = write_transcript(tmp, heavy, "heavyplan")
        os.environ["HARNESS_TASK_RECORD_DIR"] = str(Path(tmp) / "tasks")
        messages = [m for m in anchor._digest().owner_prompts(transcript) if anchor.keep(m)]
        where = anchor.full_location("heavyplan")
        injection = anchor.plan(transcript, "heavyplan", Path(tmp), messages, where, anchor.fetch_command(where),
                                rating_line=anchor.rating_debt_text(99))
        whole = anchor.whole_ids(injection)
        out, crashed, _ = run_hook(tmp, {"source": "compact", "session_id": "heavyplan",
                                         "transcript_path": str(transcript)})
        printed = [m["index"] for m in messages if m["content"] in out]
        cases.append(("plan: the whole set is what the real hook run prints in full",
                      not crashed and whole == printed and 0 < len(whole) < len(messages)))
        cases.append(("plan: a clipped or omitted message is never in the whole set",
                      not ({m["index"] for m in injection.omitted} | {m["index"] for m in injection.clipped}) & set(whole)))
        cases.append(("plan: the rating-debt line template is shared with the hook's own line",
                      anchor.rating_debt_text(3).startswith("[rating-debt] 3 unrated dispatches")))
        cases.append(("plan: the project directory is the payload's own, as the hook resolves it",
                      anchor.project_dir({"cwd": "X:/p"}) == Path(os.environ.get("CLAUDE_PROJECT_DIR") or "X:/p")))
    return cases


def main():
    cases = []
    if not os.path.exists(HOOK):
        print("RED: hooks/compact_directive_anchor.py does not exist")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        transcript = write_transcript(tmp, base_rows())
        (Path(tmp) / f"{REFERENCED}-0000-4000-8000-handoff.jsonl").write_text("{}\n", encoding="utf-8")
        payload = {"source": "compact", "session_id": SID, "transcript_path": str(transcript)}

        out, crashed, err = run_hook(tmp, payload)
        cases.append(("compact run does not crash", not crashed))
        cases.append(("the directive is re-injected verbatim", DIRECTIVE in out))
        cases.append(("a question answer counts as an owner message", ANSWER in out))
        cases.append(("a short typed prompt is kept", "Continue" in out))
        cases.append(("hook echoes are not owner messages", "hook echo" not in out))
        cases.append(("the compaction summary is not re-injected", "SUMMARY-PARAPHRASE" not in out))
        cases.append(("an interrupt marker is dropped", "Request interrupted" not in out))
        cases.append(("a bare model switch is dropped", "/model fable" not in out))
        # The session cannot read its own effort from any hook payload, so the owner setting
        # it is a directive the summary would otherwise lose (owner question 2026-09-15).
        cases.append(("an effort pin carrying its value is kept", "/effort xhigh" in out))
        cases.append(("a bare effort command is dropped",
                      not any(line.rstrip().endswith("\u00b7 /effort") for line in out.splitlines())))
        cases.append(("a slash command with arguments is kept", "/overnight finish" in out))
        cases.append(("a path-led prompt is kept", "/tmp/file failed" in out))
        cases.append(("a message-first slash command is re-injected as the command and its args",
                      "/orchestration route the audit lanes to sol" in out))
        cases.append(("no raw command markup is injected", "<command-" not in out))
        long_line = next((line for line in out.splitlines() if line.startswith("Long owner message.")), "")
        cases.append(("a long message is capped within the per-message ceiling",
                      "detail " * 400 not in out and "…[+" in long_line and 0 < len(long_line) <= MESSAGE_CEILING))
        cases.append(("a clipped message keeps its closing words as well as its opening",
                      long_line.startswith("Long owner message.") and long_line.endswith("TAIL-MARKER")))
        full = Path(tmp) / FULL_REL
        cases.append(("the uncapped text is written to logs/", full.exists()
                      and "TAIL-MARKER" in full.read_text(encoding="utf-8")))
        cases.append(("the injection names the full-text path", full.name in out))
        cases.append(("a clipped message names its ID and the command that fetches only that message",
                      "clipped for injection: U6" in out and SHOW_COMMAND in out))
        cases.append(("no injection demands reading the whole uncapped file",
                      "before your next action" not in out and "Read the uncapped file" not in out))
        cases.append(("an answer keeps the owner's words", '="Keep the owner words"' in out))
        cases.append(("a long question is shortened in the injection",
                      LONG_QUESTION not in out and '"Which of these options' in out))
        cases.append(("the tool's trailing answer instruction is not injected",
                      "Read the answers carefully" not in out))
        cases.append(("the uncapped file keeps the whole answer",
                      full.exists() and LONG_QUESTION in full.read_text(encoding="utf-8")))
        cases.append(("a named session with a transcript gets a bounded handoff pointer",
                      f"session_digest.py --session {REFERENCED} --handoff" in out
                      and f"session_digest.py --session {REFERENCED} --brief" not in out))
        cases.append(("a hex token without a transcript gets no pointer", f"--session {UNREFERENCED}" not in out))
        cases.append(("the messages are framed as a record, not open orders",
                      "not a list of open orders" in out and "override the summary" not in out
                      and "ask the owner" in out))
        cases.append(("the injection points at the summary's owner directives ledger with all five statuses",
                      "Owner directives" in out and "scoped, unclear)" in out))
        cases.append(("another session's messages are read for its root goal only",
                      "root goal" in out and "before acting on those directives" not in out))
        cases.append(("each header carries the send date and time", "--- U1 · 09-14 13:01 ---" in out))

        shown, rc, traced = run_show(tmp, [FULL_REL, "U6"])
        cases.append(("--show prints one message's full text and nothing else",
                      rc == 0 and not traced and "TAIL-MARKER" in shown and DIRECTIVE not in shown))
        shown, rc, traced = run_show(tmp, [FULL_REL, "U1", "U6"])
        cases.append(("--show prints several requested messages",
                      rc == 0 and not traced and DIRECTIVE in shown and "TAIL-MARKER" in shown))
        shown, rc, traced = run_show(tmp, [FULL_REL, "U999"])
        cases.append(("--show names an unknown ID and exits 1", rc == 1 and not traced and "U999" in shown))
        shown, rc, traced = run_show(tmp, ["logs/compact_directives_missing0.md", "U1"])
        cases.append(("--show on a missing file exits 1 without a traceback", rc == 1 and not traced))

        short = write_transcript(tmp, [user("Short directive one."), user("Short directive two.")], name="short")
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(short)))
        cases.append(("an unclipped injection names no fetch command",
                      not crashed and "Short directive two." in out and "--show" not in out))

        spread_text = [f"START-{k} " + "word " * 196 + f"END-{k}" for k in range(10)]
        spread_transcript = write_transcript(tmp, [user(text) for text in spread_text], name="spread")
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(spread_transcript)))
        cases.append(("every message in a spread run appears",
                      not crashed and all(f"START-{k} " in out for k in range(10))))
        cases.append(("the first and newest messages stay whole while the middle is clipped first",
                      all(spread_text[k] in out for k in (0, 1, 2, 7, 8, 9))
                      and not any(spread_text[k] in out for k in (3, 4, 5, 6))))
        cases.append(("a clipped middle message keeps its closing words",
                      all(f"END-{k}" in out for k in (3, 4, 5, 6))))
        cases.append(("the spread run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))

        # The active task record (tools/task_record.py) rides after the owner messages, bounded, inside the
        # same output budget. With no record the block is one line naming the command that opens one:
        # a drive long enough to compact is a drive that wanted the record (owner 2026-09-15).
        cases.append(("no task record -> no [task-record] block", "[task-record]" not in out))
        cases.append(("no task record -> one line naming the command that opens one",
                      "No task record" in out and "task_record.py open" in out))
        import importlib.util
        tasks_dir = str(Path(tmp) / "tasks")
        os.environ["HARNESS_TASK_RECORD_DIR"] = tasks_dir
        spec = importlib.util.spec_from_file_location("task_record", str(Path(HOOK).parent.parent / "tools" / "task_record.py"))
        tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tr)
        tr.open_record("t-drive", session_id=SID, title="Drive")
        tr.set_fields("t-drive", phase="build", next_action="NEXT-MARKER")
        tr.add_item("t-drive", "requirement", "REQ-MARKER no push")
        for k in range(20):
            tr.add_item("t-drive", "decision", f"decision {k} " + "z" * 60)
        out, crashed, _ = run_hook(tmp, payload, {"HARNESS_TASK_RECORD_DIR": tasks_dir, "HARNESS_TASK_RECORD_BLOCK_BYTES": "600"})
        cases.append(("with a record, the block names the task, phase and next action",
                      not crashed and "[task-record] t-drive phase=build next=NEXT-MARKER" in out))
        cases.append(("the block keeps requirements whole", "REQ-MARKER no push" in out))
        cases.append(("an oversize record names its omitted rows with the show command",
                      "omitted:" in out and "task_record.py show t-drive" in out))
        cases.append(("the block comes after the owner messages", out.find("[task-record]") > out.find(DIRECTIVE)))
        cases.append(("the run with a record stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))
        os.environ.pop("HARNESS_TASK_RECORD_DIR", None)
        out, crashed, _ = run_hook(tmp, payload)
        cases.append(("no task record -> the opening command carries --next for the summary's next action",
                      "No task record" in out and "--next" in out))

        # A reply that only names a packet's option ('Do all recs now', 'O2, ...') or a question answer
        # carries no ruling text of its own; the ruling lives in the assistant text it answered
        # (1f652996: 'Sure' to O7(a) survived, 'it starts in S4' did not).
        packet_project = Path(tmp) / "packetproj"
        (packet_project / ".claude" / "skills" / "project_subsystems").mkdir(parents=True)
        (packet_project / ".claude" / "skills" / "project_subsystems" / "adaptation.json").write_text(
            json.dumps({"doc_roots": ["D:/Vault/DOCROOT-MARKER"]}), encoding="utf-8")

        def said(text):
            return {"type": "assistant", "timestamp": "2026-09-14T13:00:00Z",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

        asked = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_p1", "name": "AskUserQuestion", "input": {"questions": [
                {"question": "Which route?", "header": "Route", "multiSelect": False, "options": [
                    {"label": "Fast path (Recommended)", "description": "FAST-DESC-MARKER skips the panel"},
                    {"label": "Slow", "description": "SLOW-DESC-MARKER"}]}]}}]}}
        packet_rows = [
            user(DIRECTIVE),
            said("Long report body. " + "filler " * 80 + "Q1 compaction fixes: do A1-A7 now, default wait. "
                 "PACKET-TAIL-MARKER"),
            {"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>",
                                              "content": [{"type": "text", "text": "SYNTHETIC-MARKER No response requested."}]}},
            user("Do all recs now"),
            asked,
            user([{"type": "tool_result", "tool_use_id": "toolu_p1",
                   "content": 'The user answered: "Which route?"="Fast path (Recommended)"'}]),
            said("NOCTX-MARKER an unrelated status line."),
            user("A long typed instruction with no reference code at all, " + "more words " * 20 + "END."),
            said("PACKET2-MARKER the second packet: O1 keep, O2 change."),
            user("O2, and here is a longer explanation of why that option suits the work, "
                 + "reasoning " * 20 + "done."),
        ]
        packet_transcript = write_transcript(tmp, packet_rows, name="packets")
        out, crashed, _ = run_hook(str(packet_project), dict(payload, transcript_path=str(packet_transcript)))
        cases.append(("packet run does not crash", not crashed))
        cases.append(("a short reply carries the closing words of the message it answered",
                      "PACKET-TAIL-MARKER" in out))
        cases.append(("a question answer carries its chosen option's description, not the other options",
                      "FAST-DESC-MARKER" in out and "SLOW-DESC-MARKER" not in out))
        cases.append(("a long reply naming a reference code carries the packet it answered", "PACKET2-MARKER" in out))
        cases.append(("a long reply with no reference code carries no context", "NOCTX-MARKER" not in out))
        cases.append(("a client-synthetic assistant row is never the answered text", "SYNTHETIC-MARKER" not in out))
        cases.append(("the answered text is labeled as the assistant's, apart from the owner's words",
                      "(answering, assistant text)" in out and "Do all recs now" in out))
        cases.append(("the project's document roots are re-injected as locations",
                      "[locations]" in out and "D:/Vault/DOCROOT-MARKER" in out))
        cases.append(("the packet run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))
        out, crashed, _ = run_hook(tmp, payload)
        cases.append(("a project with no document roots gets no locations line", "[locations]" not in out))

        out, crashed, _ = run_hook(tmp, [])
        cases.append(("a JSON array payload is a silent no-op", out.strip() == "" and not crashed))
        out, crashed, _ = run_hook(tmp, None)
        cases.append(("a JSON null payload is a silent no-op", out.strip() == "" and not crashed))

        out, crashed, _ = run_hook(tmp, dict(payload, source="startup"))
        cases.append(("a startup session is silent", out.strip() == "" and not crashed))
        out, crashed, _ = run_hook(tmp, payload, {"CLAUDE_CODE_SIDECAR_PROMPT_FILE": str(transcript)})
        cases.append(("a sidecar child is silent (sidecar_recompact_reprompt owns it)",
                      out.strip() == "" and not crashed))
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(Path(tmp) / "missing.jsonl")))
        cases.append(("an unreadable transcript says so instead of failing silently",
                      not crashed and "[owner-directives]" in out and "could not read" in out
                      and "--handoff" in out and "--full" not in out))

        multi_answer = ('The user answered: "Policy?"="' + "long reason " * 90 + '", "Rails?"="PAIR-2-KEPT", '
                        '"Cadence?"="PAIR-3-KEPT", "Baseline?"="PAIR-4-KEPT". Read the answers carefully '
                        "— they may request clarification, changes, or that you not proceed — and follow what "
                        "they actually say.")
        medium = [user(f"MID-DECISION-{i:02d} " + "reason " * 170) for i in range(14)] \
            + question("toolu_m1", multi_answer)
        medium_transcript = write_transcript(tmp, medium, name="medium")
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(medium_transcript)))
        cases.append(("a medium over-budget run does not crash", not crashed))
        cases.append(("every message appears when a shared clip fits the budget",
                      all(f"MID-DECISION-{i:02d}" in out for i in range(14)) and "omitted for budget" not in out))
        cases.append(("the shared clip is named with its fetch command",
                      "clipped for injection: U1" in out and "--show" in out))
        cases.append(("a clipped answer keeps every decision",
                      all(f"PAIR-{k}-KEPT" in out for k in (2, 3, 4)) and "long reason " * 90 not in out))
        cases.append(("the medium run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))

        # Live defect 2026-09-14: at the middle floor, four 100-char question stubs ate the whole clip and
        # every answer of a four-decision message vanished while its questions survived.
        long_questions = ", ".join(f'"Question {k} ' + "about the retirement policy " * 5 + f'?"="DECISION-{k} '
                                   + "because " * 12 + '"' for k in range(4))
        tight_answer = ("The user answered: " + long_questions + ". Read the answers carefully — they may request "
                        "clarification, changes, or that you not proceed — and follow what they actually say.")
        tight = [user(f"TIGHT-{i:02d} " + "context " * 90) for i in range(15)] + question("toolu_t1", tight_answer) \
            + [user(f"TIGHT-{i:02d} " + "context " * 90) for i in range(15, 30)]
        tight_transcript = write_transcript(tmp, tight, name="tight")
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(tight_transcript)))
        cases.append(("a floor-clipped answer keeps every decision when its questions are long",
                      not crashed and "omitted for budget" not in out
                      and all(f"DECISION-{k}" in out for k in range(4))))
        cases.append(("the tight run stays inside the byte bound", len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))

        many = [user(f"PROMPT-{i:04d} x") for i in range(3000)]
        transcript2 = write_transcript(tmp, many, name="many")
        out, crashed, _ = run_hook(tmp, dict(payload, transcript_path=str(transcript2)))
        cases.append(("an over-budget run does not crash", not crashed))
        cases.append(("the earliest messages survive the budget",
                      all(f"PROMPT-{i:04d}" in out for i in range(3))))
        cases.append(("the newest message survives the budget", "PROMPT-2999" in out))
        cases.append(("dropped messages are named, not silent",
                      "omitted for budget" in out and "PROMPT-0005" not in out))
        cases.append(("the full SessionStart output has a fixed byte bound",
                      len(out.encode("utf-8")) <= MAX_OUTPUT_BYTES))
        omitted_line = next((line for line in out.splitlines() if "omitted for budget" in line), "")
        cases.append(("the omitted line names the fetch command", "--show" in omitted_line))
        cases.append(("the omitted ID list is capped", "more" in omitted_line and len(omitted_line) < 600))

        stale = Path(tmp) / "logs" / "compact_directives_old00000.md"
        stale.write_text("old", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(stale, (old, old))
        run_hook(tmp, payload)
        cases.append(("anchors older than 30 days are pruned", not stale.exists()))
        cases.append(("this session's anchor is kept", full.exists()))

        source = Path(HOOK).read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(source)) or ""
        cases.append(("the hook docstring states filtering, caps, omissions, and the uncapped file",
                      "prints every" not in doc
                      and all(word in doc.lower() for word in ("filter", "cap", "omit", "uncapped"))))

        project = Path(HERE).parents[1]
        settings = _settings_probe.settings(project / ".claude")
        compact_groups = [group for group in settings.get("hooks", {}).get("SessionStart", [])
                          if group.get("matcher") == "compact"]
        registered_commands = [hook.get("command") for group in compact_groups
                               for hook in group.get("hooks", [])]
        expected_command = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/compact_directive_anchor.py"'
        cases.append(("settings registers the anchor in SessionStart(compact)",
                      expected_command in registered_commands))

        registration_transcript = Path(tmp) / f"{REGISTRATION_SID}.jsonl"
        registration_directive = "REGISTRATION-FIXTURE owner directive"
        registration_transcript.write_text(json.dumps(user(registration_directive)) + "\n",
                                           encoding="utf-8")
        registration_payload = {
            "source": "compact", "session_id": REGISTRATION_SID,
            "transcript_path": str(registration_transcript),
        }
        # The registered command resolves the hook and its logs/ from CLAUDE_PROJECT_DIR: run it from a copy of
        # the hooks in a temp project so the checkout's logs/ is never written.
        run_project = Path(tmp) / "registration_project"
        for part in ("hooks", "tools", "schemas"):
            shutil.copytree(project / ".claude" / part, run_project / ".claude" / part,
                            ignore=shutil.ignore_patterns("__pycache__"))
        registration_log = run_project / "logs" / f"compact_directives_{REGISTRATION_SID[:8]}.md"
        registration_rc, registration_out, registration_err = 1, "", "not registered"
        if expected_command in registered_commands:
            # Git Bash, as Claude Code runs hooks. A bare "bash" from Windows Python resolves System32's
            # WSL bash first, and WSL does not inherit CLAUDE_PROJECT_DIR.
            bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or git_bash()
            env = dict(os.environ, CLAUDE_PROJECT_DIR=run_project.as_posix(), PYTHONIOENCODING="utf-8")
            env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
            try:
                result = subprocess.run([bash, "-lc", expected_command],
                                        input=json.dumps(registration_payload), capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", env=env,
                                        timeout=120)
                registration_rc = result.returncode
                registration_out = result.stdout or ""
                registration_err = result.stderr or ""
            finally:
                registration_log.unlink(missing_ok=True)
        cases.append(("the exact registered command runs through bash",
                      registration_rc == 0 and "Traceback" not in registration_err
                      and registration_directive in registration_out))

        # Rating debt surfaces here, at the compaction boundary, not on every prompt.
        debt_out, debt_crashed, _ = run_hook(tmp, payload, {"HARNESS_PENDING_COUNT": "4"})
        cases.append(("unrated dispatches print one rating-debt line naming --rate and --accept-rest",
                      not debt_crashed and "[rating-debt] 4 unrated" in debt_out
                      and "--rate-batch" in debt_out and "one --rate-batch call" in debt_out
                      and "--accept-rest" in debt_out))
        cases.append(("the debt line stays inside the output cap",
                      len(debt_out.encode("utf-8")) <= MAX_OUTPUT_BYTES))
        for n in ("0", "-1"):
            quiet_out, quiet_crashed, _ = run_hook(tmp, payload, {"HARNESS_PENDING_COUNT": n})
            cases.append((f"count {n} prints no rating-debt line",
                          not quiet_crashed and "[rating-debt]" not in quiet_out))
        # The real path: the payload's session id finds a planted session dir with two unrated agents.
        home = Path(tmp) / "home"
        wdir = home / ".claude" / "projects" / "anyproj" / SID / "workflows"
        wdir.mkdir(parents=True)
        (wdir / "wf_1.json").write_text(json.dumps({"runId": "wf_1", "workflowProgress": [
            {"type": "workflow_agent", "label": "exec:one", "agentId": "a1", "state": "done"},
            {"type": "workflow_agent", "label": "exec:two", "agentId": "a2", "state": "done"}]}),
            encoding="utf-8")
        real = {"HOME": str(home), "USERPROFILE": str(home), "HARNESS_PENDING_COUNT": None,
                "CLAUDE_CODE_SESSION_ID": None, "CLAUDE_CONFIG_DIR": None}
        real_out, real_crashed, _ = run_hook(tmp, payload, real)
        cases.append(("the real import path counts a planted session's two unrated agents",
                      not real_crashed and "[rating-debt] 2 unrated" in real_out))
        peer_out, peer_crashed, _ = run_hook(tmp, dict(payload, session_id="0000beef-no-dispatches"), real)
        cases.append(("a session with no workflows never inherits another session's debt",
                      not peer_crashed and "[rating-debt]" not in peer_out))

        compact_spec = importlib.util.spec_from_file_location("compact_for_compact_parity", HOOK)
        compact = importlib.util.module_from_spec(compact_spec)
        compact_spec.loader.exec_module(compact)
        digest_spec = importlib.util.spec_from_file_location(
            "digest_for_compact_parity", Path(HOOK).parent.parent / "tools" / "session_digest.py")
        digest = importlib.util.module_from_spec(digest_spec)
        digest_spec.loader.exec_module(digest)
        classifier_cases = [
            {"content": "[Request interrupted by user]", "signals": []},
            {"content": "/model fable", "signals": []},
            {"content": "/effort", "signals": []},
            {"content": "/effort xhigh", "signals": []},
            {"content": "/feature run", "signals": []},
            {"content": "owner prose", "signals": []},
        ]
        cases.append(("compact keep() matches the digest owner-prompt classifier",
                      all(digest.is_substantive_owner_prompt(message) == compact.keep(message)
                          for message in classifier_cases)))

        # An optional enrichment never takes down the owner-message injection (a None text block, a
        # question entry that is not a dict).
        def odd_rows(assistant_row):
            return [user(DIRECTIVE), assistant_row, user("Continue")]

        for label, odd in (
                ("a text block whose text is null", {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": None}]}}),
                ("a question entry that is not an object", {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_o1", "name": "AskUserQuestion", "input": {"questions": ["bare"]}}]}})):
            out, crashed, _ = run_hook(tmp, payload | {"transcript_path": str(write_transcript(tmp, odd_rows(odd), name="odd"))})
            cases.append((f"{label} still gets the owner messages re-injected", not crashed and DIRECTIVE in out))

        cases.append(("the short-reply threshold is its own constant, not the clip floor",
                      compact.SHORT_REPLY_CHARS == 160 and compact.needs_context({"content": "x" * 160})
                      and not compact.needs_context({"content": "x" * 161})))
        floor = compact.FLOOR_CHARS
        compact.FLOOR_CHARS = 5000
        try:
            cases.append(("raising the clip floor does not widen which replies get context",
                          not compact.needs_context({"content": "plain words " * 40})))
        finally:
            compact.FLOOR_CHARS = floor
        long_reply = "a long typed instruction with no ruling of its own, " * 6
        cases.append(("a long reply naming an invented reference code (S4) gets the answered text",
                      compact.needs_context({"content": long_reply + "it starts in S4"})))
        cases.append(("a long reply naming an owner message id (U12) gets no answered text",
                      not compact.needs_context({"content": long_reply + "see U12 and U345"})))
        cases.append(("the stamp helper prints month-day hour:minute and is empty without a timestamp",
                      compact.stamp("2026-09-14T13:01:00Z") == "09-14 13:01" and compact.stamp(None) == ""
                      and compact.stamp("2026") == ""))
        cases.append(("the assistant-row predicate drops sidechain and synthetic rows",
                      compact.shown_assistant_row({"type": "assistant", "message": {}})
                      and not compact.shown_assistant_row({"type": "assistant", "isSidechain": True})
                      and not compact.shown_assistant_row({"type": "assistant", "message": {"model": "<synthetic>"}})
                      and not compact.shown_assistant_row({"type": "user"})))

        # The rating-debt line is optional: it never holds the core output past the client's timeout.
        tools_dir = str(Path(HOOK).parent.parent / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import orchestration_metrics
        real_pending = orchestration_metrics.pending_count
        try:
            orchestration_metrics.pending_count = lambda **_: (time.sleep(6), 3)[1]
            started = time.time()
            slow_line = compact.rating_debt_line("slow-session", 0.3)
            slow_elapsed = time.time() - started
            orchestration_metrics.pending_count = lambda **_: 3
            fast_line = compact.rating_debt_line("fast-session", 5)
            orchestration_metrics.pending_count = lambda **_: 3
            spent_line = compact.rating_debt_line("fast-session", 0)
        finally:
            orchestration_metrics.pending_count = real_pending
        cases.append(("a rating count slower than its budget prints no debt line and returns at the budget",
                      slow_line == "" and slow_elapsed < 3))
        cases.append(("a rating count inside its budget still prints the debt line",
                      "[rating-debt] 3 unrated" in fast_line))
        cases.append(("a spent budget skips the count",
                      spent_line == ""))
        registered_timeout = next(hook.get("timeout") for group in compact_groups for hook in group.get("hooks", [])
                                  if hook.get("command") == expected_command)
        cases.append(("the debt deadline leaves the registered timeout room for the core output",
                      0 < compact.RATING_DEADLINE_SEC <= registered_timeout - 5))

        # owner_prompts parses only rows that can hold an owner message: a long drive is mostly
        # tool rows, and every extra full parse of them is seconds of the hook's timeout.
        filler = [{"type": "assistant", "message": {"role": "assistant", "content": [
                      {"type": "text", "text": f"working on step {i}"}]}} for i in range(150)]
        filler += [user([{"type": "tool_result", "tool_use_id": f"toolu_f{i}", "content": "ok"}])
                   for i in range(150)]
        scan_rows = [user(DIRECTIVE), *filler,
                     user("<command-message>x</command-message>\n<command-name>/x</command-name>",
                          isMeta=True),
                     user([{"type": "text", "text": "Base directory for skill: x\nARGUMENTS: ship the filter"}],
                          isMeta=True),
                     *question("toolu_scan", ANSWER), user("Continue")]
        scan_path = write_transcript(tmp, scan_rows, name="scan")
        real_loads, parsed = json.loads, []
        prior_state = os.environ.get("HARNESS_HOOK_STATE_DIR")
        os.environ["HARNESS_HOOK_STATE_DIR"] = str(Path(tmp) / "scan state")
        json.loads = lambda *a, **k: (parsed.append(1), real_loads(*a, **k))[1]
        try:
            scanned = digest.owner_prompts(scan_path)
        finally:
            json.loads = real_loads
            if prior_state is None:
                os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
            else:
                os.environ["HARNESS_HOOK_STATE_DIR"] = prior_state
        contents = [m["content"] for m in scanned]
        cases.append(("owner_prompts still returns the typed prompts, a command argument and an answer",
                      DIRECTIVE in contents and any("ship the filter" in c for c in contents)
                      and any(c.startswith("(answer)") for c in contents) and "Continue" in contents))
        cases.append(("owner_prompts parses each row once for the digest and only candidate rows again",
                      len(parsed) < len(scan_rows) * 1.5))

    cases += whole_plan_cases()

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
