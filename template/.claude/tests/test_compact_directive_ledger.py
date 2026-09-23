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
                      "## Artifacts" in out and "one line per changed file" in out
                      and "Never restate a file's contents" in out
                      and "exact error" in out))
        # Live defect 2026-09-22 (fd4ff546): the rule read as Artifacts-only, so "Key technical concepts"
        # restated the plan's design section, and an open question's observed value came back wrong
        # ("clean" -> "right-sized" where the file said ["clean", "low"]).
        cases.append(("the no-restating rule covers every section, and open questions keep their observed values",
                      "contents in any section" in out and "open question's observed values" in out))
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
        old_bytes, new_bytes = len(out_old.encode("utf-8")), len(out_new.encode("utf-8"))
        print(f"realistic 46-message ledger: legacy {old_bytes} bytes -> carry-forward {new_bytes} bytes")
        cases.append(("a realistic 46-message session (33 terminal, 12 holds) compacts to a "
                      "meaningfully smaller ledger", not crashed_new and not crashed_old
                      and new_bytes < old_bytes * 0.65))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
