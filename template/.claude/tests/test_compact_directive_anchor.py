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
    env = dict(os.environ, CLAUDE_PROJECT_DIR=tmp, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_CODE_SIDECAR_PROMPT_FILE", None)
    env.update(env_extra or {})
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
        registration_log = project / "logs" / f"compact_directives_{REGISTRATION_SID[:8]}.md"
        registration_rc, registration_out, registration_err = 1, "", "not registered"
        if expected_command in registered_commands:
            # Git Bash, as Claude Code runs hooks. A bare "bash" from Windows Python resolves System32's
            # WSL bash first, and WSL does not inherit CLAUDE_PROJECT_DIR.
            bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH") or git_bash()
            env = dict(os.environ, CLAUDE_PROJECT_DIR=project.as_posix(), PYTHONIOENCODING="utf-8")
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

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
