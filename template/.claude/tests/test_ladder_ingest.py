"""Paired-session ladder ingest proofs — synthetic transcripts, no live session needed.

Each case builds two tiny JSONL transcripts in a temp projects dir (a user prompt,
assistant events carrying `model` at `message.model`, tool_use blocks, a cross-review
user turn + reply, a final message), runs `.claude/tools/ladder_ingest.py` against
them via `--projects-dir`, and asserts on the outputs.

    python3 .claude/tests/test_ladder_ingest.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))
from session_model_rails import ANTHROPIC_RAILS  # noqa: E402  the real rail the transport parse reads

TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOL = os.path.join(TOOLS_DIR, "ladder_ingest.py")

PROMPT = "Should the orchestrator delegate this task to a subagent?"
OTHER_PROMPT = "A different question entirely."
REVIEW_PROMPT = "Here is the other session's answer — please review it against yours."


def _u(text, ts="2026-09-08T10:00:00.000Z"):
    return {"type": "user", "isSidechain": False, "timestamp": ts,
            "message": {"role": "user", "content": text}}


def _a(text=None, model="claude-opus-5", tool=None, ts="2026-09-08T10:01:00.000Z", effort=None):
    content = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool is not None:
        content.append({"type": "tool_use", "id": "tu-" + tool,
                        "name": tool, "input": {"command": "ls"}})
    row = {"type": "assistant", "isSidechain": False, "timestamp": ts,
           "message": {"model": model, "role": "assistant", "content": content}}
    if effort is not None:
        row["effort"] = effort  # the runtime stamps the live effort on every assistant row
    return row


def _rails(marker="[anthropic session — dispatch transport]", effort_line=None,
           ts="2026-09-08T09:59:00.000Z"):
    content = ("[session] Session tier: `condensed` — skip `## detailed` sections.\n" + marker
               + ("\n" + effort_line if effort_line else ""))
    return {"type": "attachment", "isSidechain": False, "timestamp": ts,
            "attachment": {"type": "hook_success", "hookName": "SessionStart:startup",
                           "hookEvent": "SessionStart", "content": content}}


def make_projects(sessions):
    """{prefix: rows} -> (projects_dir, {prefix: stem}); stems share the prefix."""
    pdir = tempfile.mkdtemp(prefix="ladderingest_")
    stems = {}
    for i, (prefix, rows) in enumerate(sessions.items()):
        stem = "%s-%04d-abcd-efgh-ijkl-mnop" % (prefix, i)
        with open(os.path.join(pdir, stem + ".jsonl"), "w", encoding="utf-8",
                  newline="\n") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        stems[prefix] = stem
    return pdir, stems


def transcript_a():
    return [
        _rails(ANTHROPIC_RAILS),
        _u(PROMPT),
        _a("Let me look into delegation.", tool="Bash", effort="medium"),
        _u(REVIEW_PROMPT, ts="2026-09-08T10:02:00.000Z"),
        _a("The other answer misses grounding.", ts="2026-09-08T10:03:00.000Z", effort="medium"),
        _a("Final verdict: delegate reads widely.", ts="2026-09-08T10:04:00.000Z", effort="medium"),
    ]


def _injected_skill_doc():
    # Harness-injected skill text: isMeta + list content, the shape real transcripts
    # carry. Contains a heuristic word ("compare") so the test proves the isMeta
    # skip, not the keyword list, keeps it out of turns and cross-review.
    return {"type": "user", "isSidechain": False, "isMeta": True,
            "timestamp": "2026-09-08T10:02:30.000Z",
            "message": {"role": "user",
                        "content": [{"type": "text",
                                     "text": "Base directory for this skill: "
                                             "compare implementations here"}]}}


def transcript_b(model="claude-sonnet-5"):
    return [
        _rails("[codex session — dispatch transport]"),
        _u(PROMPT),
        _injected_skill_doc(),
        _a("Checking the docs first.", model=model, tool="Bash", effort="high"),
        _a("Second tool pass.", model=model, tool="Read",
           ts="2026-09-08T10:02:00.000Z", effort="xhigh"),
        _u("/compact", ts="2026-09-08T10:02:45.000Z"),
        _u(REVIEW_PROMPT, ts="2026-09-08T10:03:00.000Z"),
        _a("My answer stands; theirs costs more.", model=model,
           ts="2026-09-08T10:04:00.000Z"),
        _a("Final verdict: delegate cheaply.", model=model,
           ts="2026-09-08T10:05:00.000Z"),
    ]


def run(tool_args):
    return subprocess.run([sys.executable, TOOL] + tool_args,
                          capture_output=True, text=True)


def case_session_files_carry_model_and_turns(failures):
    pdir, _ = make_projects({"aaaa1111": transcript_a(), "bbbb2222": transcript_b()})
    out = tempfile.mkdtemp(prefix="ladderingest_out_")
    r = run(["aaaa1111", "bbbb2222", "--out", out, "--slug", "s1",
             "--projects-dir", pdir])
    ok = r.returncode == 0
    detail = "rc=%s stderr=%r" % (r.returncode, r.stderr[-300:])
    if ok:
        # `arm-<name>.md`, not `session-<id8>.md`: an arm is no longer always a session, and an
        # 8-char id slice rendered a whole workflow fan-out as `agent-a0`, `agent-a2`.
        fa = os.path.join(out, "s1", "arm-aaaa1111.md")
        fb = os.path.join(out, "s1", "arm-bbbb2222.md")
        try:
            ta = open(fa, encoding="utf-8").read()
            tb = open(fb, encoding="utf-8").read()
        except OSError as exc:
            ok, detail = False, "missing session file: %s" % exc
        else:
            checks = [
                ("model: claude-opus-5" in ta, "a model line"),
                ("model: claude-sonnet-5" in tb, "b model line"),
                ("transport: anthropic" in ta, "a transport line"),
                ("transport: codex" in tb, "b transport line"),
                ("effort: medium" in ta, "a effort from the assistant rows, not the rails"),
                ("effort: high->xhigh" in tb, "b effort sequence when it changed mid-session"),
                ("turns: 2" in ta, "a turn count (prompt + cross-review)"),
                ("turns: 2" in tb, "b turn count"),
                ("tool_calls: 1" in ta, "a tool count"),
                ("tool_calls: 2" in tb, "b tool count"),
                ("Final verdict: delegate reads widely." in ta, "a final deliverable"),
                ("The other answer misses grounding." in ta, "a cross-review reply"),
                ("## Cross-review turns" in ta and "(none)" not in ta,
                 "a non-empty cross-review section"),
            ]
            for passed, what in checks:
                if not passed:
                    ok, detail = False, "session file missing %s" % what
                    break
    print("%-4s per-session files carry model / transport / turn counts / review reply"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("session files: %s" % detail)


def case_same_prompt_true(failures):
    pdir, _ = make_projects({"aaaa1111": transcript_a(), "bbbb2222": transcript_b()})
    out = tempfile.mkdtemp(prefix="ladderingest_out_")
    r = run(["aaaa1111", "bbbb2222", "--out", out, "--slug", "s1",
             "--projects-dir", pdir])
    comp = ""
    if r.returncode == 0:
        with open(os.path.join(out, "s1", "COMPARISON.md"), encoding="utf-8") as fh:
            comp = fh.read()
    ok = r.returncode == 0 and "same_prompt: true" in comp
    print("%-4s matching prompts -> same_prompt: true" % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("same_prompt true: rc=%s comp=%r" % (r.returncode, comp[-200:]))


def case_same_prompt_false_names_hashes(failures):
    rows_b = transcript_b()
    rows_b[1] = _u(OTHER_PROMPT)  # swap only the first prompt
    pdir, _ = make_projects({"aaaa1111": transcript_a(), "bbbb2222": rows_b})
    out = tempfile.mkdtemp(prefix="ladderingest_out_")
    r = run(["aaaa1111", "bbbb2222", "--out", out, "--slug", "s1",
             "--projects-dir", pdir])
    comp = ""
    if r.returncode == 0:
        with open(os.path.join(out, "s1", "COMPARISON.md"), encoding="utf-8") as fh:
            comp = fh.read()
    ha = hashlib.sha256(PROMPT.encode()).hexdigest()
    hb = hashlib.sha256(OTHER_PROMPT.encode()).hexdigest()
    ok = (r.returncode == 0 and "same_prompt: false" in comp
          and ha in comp and hb in comp)
    print("%-4s differing prompts -> same_prompt: false with both hashes"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("same_prompt false: rc=%s comp=%r" % (r.returncode, comp[-300:]))


def case_empty_transcript_fails_loud(failures):
    # A rails-only transcript carries zero real prompts — the guarded case.
    pdir, _ = make_projects({"aaaa1111": transcript_a(),
                             "ffff4444": [_rails("[anthropic session]")]})
    out = tempfile.mkdtemp(prefix="ladderingest_out_")
    r = run(["aaaa1111", "ffff4444", "--out", out, "--slug", "s1",
             "--projects-dir", pdir])
    produced = os.path.exists(os.path.join(out, "s1", "COMPARISON.md"))
    ok = r.returncode != 0 and not produced and "empty transcript" in r.stderr
    print("%-4s empty transcript exits non-zero, silent success impossible"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("empty transcript: rc=%s produced=%s stderr=%r"
                        % (r.returncode, produced, (r.stderr or "")[-200:]))


JUDGE_FIXTURE = {
    "criteria": [
        {"name": "correctness", "winner": "aaaa1111", "margin": "slight",
         "evidence": [{"session": "aaaa1111", "quote": "delegate reads widely"}],
         "why": "ties the claim to the delegation docs"},
        {"name": "cost", "winner": "bbbb2222", "margin": "clear",
         "evidence": [{"session": "bbbb2222", "quote": "delegate cheaply"}],
         "why": "fewer turns for the same verdict"},
    ],
    "overall": "aaaa1111",
    "doesNotShow": ["n=1 per arm on one prompt supports a hypothesis, never a ranking"],
    "ladderClause": "reads the delegation docs before judging cost",
    "orchestrationNote": "cheaper arm skipped the grounding read",
    "couldNotSatisfy": "",
}


def case_apply_fills_adjudication(failures):
    pdir, _ = make_projects({"aaaa1111": transcript_a(), "bbbb2222": transcript_b()})
    out = tempfile.mkdtemp(prefix="ladderingest_out_")
    r0 = run(["aaaa1111", "bbbb2222", "--out", out, "--slug", "s1",
              "--projects-dir", pdir])
    if r0.returncode != 0:
        print("FAIL --apply setup ingest failed: %r" % r0.stderr[-200:])
        failures.append("apply setup ingest rc=%s" % r0.returncode)
        return
    judge = os.path.join(out, "judge.json")
    with open(judge, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(JUDGE_FIXTURE, fh)
    r = run(["--out", out, "--slug", "s1", "--apply", judge])
    comp = open(os.path.join(out, "s1", "COMPARISON.md"), encoding="utf-8").read()
    ok = (r.returncode == 0
          and "adjudication pending" not in comp
          and "| correctness | aaaa1111 | slight |" in comp
          and "n=1 per arm" in comp
          and "reads the delegation docs before judging cost" in r.stdout
          and "cheaper arm skipped the grounding read" in r.stdout
          and "route via /codify" in r.stdout)
    print("%-4s --apply fills Adjudication and prints both clauses + route line"
          % ("ok" if ok else "FAIL"))
    if not ok:
        failures.append("apply: rc=%s stdout=%r comp_tail=%r"
                        % (r.returncode, r.stdout[-300:], comp[-300:]))


def case_launcher_event_array_output_is_read(failures):
    """`-o json` under the user setting `"verbose": true` writes an ARRAY of every event."""
    sys.path.insert(0, TOOLS_DIR)
    from pathlib import Path
    import ladder_ingest
    d = tempfile.mkdtemp(prefix="li_array_")
    out = Path(d) / "run.out.json"
    out.write_text(json.dumps([
        {"type": "system", "subtype": "init", "session_id": "s1"},
        {"type": "user", "message": {"role": "user", "content": "the brief"}},
        {"type": "result", "subtype": "success", "result": "the deliverable", "session_id": "s1"},
    ]), encoding="utf-8")
    final, prompt = ladder_ingest._launcher_result(out)
    if final != "the deliverable" or prompt != "the brief":
        failures.append("event array: got (%r, %r)" % (final, prompt))


def main():
    failures = []
    case_session_files_carry_model_and_turns(failures)
    case_same_prompt_true(failures)
    case_same_prompt_false_names_hashes(failures)
    case_empty_transcript_fails_loud(failures)
    case_apply_fills_adjudication(failures)
    case_launcher_event_array_output_is_read(failures)

    total = 6
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
