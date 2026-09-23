#!/usr/bin/env python3
"""Proof: hooks/_owner_text.classify parses every owner-row shape once, and each consumer's
projection matches the shape x consumer table in the module docstring.

Consumers: kill_guard (consent text and answer pairs), harness_growth_guard (turn start) and the
session digest's owner-prompt record (session_digest.owner_prompts, built on
_transcript_summary.TranscriptSummaryBuilder). The table is parsed from `_owner_text.__doc__`, so a
projection change that is not written into the table fails here.

    python3 .claude/tests/test_owner_text.py
"""
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
TOOLS = HERE.parent / "tools"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STOP_QUESTION = "Stop the probe-task run?"
ANSWER_TEXT = 'User has answered your questions: "Stop the probe-task run?"="Yes, stop it".'
QUESTION_TEXT = 'The user answered: "Which edits?"="Do all recommendations"'


def user(content, uuid, **extra):
    return dict({"type": "user", "uuid": uuid, "timestamp": "2026-09-14T12:00:00Z",
                 "message": {"role": "user", "content": content}}, **extra)


# (shape label from the docstring table, row); None label = not an owner-row shape.
FIXTURE = [
    ("prompt", user("Ship the owner text parser.", "u01")),
    ("meta", user([{"type": "text", "text": "# Delegate\n\nCanonical route for delegation.\n"}], "u02",
                  isMeta=True)),
    ("meta ARGUMENTS", user([{"type": "text", "text": "# /overnight — Unattended run\n\nBody.\n\n"
                                                       "ARGUMENTS: finish the wave"}], "u03", isMeta=True)),
    ("sidechain", user("Subagent brief that is not the owner.", "u04", isSidechain=True)),
    ("injection-prefixed", user("<system-reminder>hook context</system-reminder>\n"
                                "Please keep going with the plan.", "u05")),
    ("envelope-only", user("<task-notification>background task completed</task-notification>", "u06")),
    ("agent-message", user('<agent-message from="peer">Peer update for the owner.</agent-message>', "u07")),
    ("command with args", user("<command-name>/model</command-name>\n<command-message>model</command-message>\n"
                               "<command-args>fable</command-args>", "u08")),
    ("empty-args command", user("<command-message>clear</command-message>\n<command-name>/clear</command-name>",
                                "u09")),
    (None, {"type": "assistant", "uuid": "u10", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_q1", "name": "AskUserQuestion", "input": {}},
        {"type": "tool_use", "id": "toolu_q2", "name": "AskUserQuestion", "input": {}}]}}),
    ("answer via toolUseResult", user([{"type": "tool_result", "tool_use_id": "toolu_q1",
                                        "content": ANSWER_TEXT}], "u11",
                                      toolUseResult={"questions": [{"question": STOP_QUESTION}],
                                                     "answers": {STOP_QUESTION: "Yes, stop it"}})),
    ("answer via tool_result text", user([{"type": "tool_result", "tool_use_id": "toolu_q2",
                                           "content": QUESTION_TEXT}], "u12")),
    ("tool_result with text blocks", user([{"type": "tool_result", "tool_use_id": "toolu_b1", "content": "a.txt"},
                                           {"type": "text", "text": "text typed beside a tool result"}], "u13")),
    ("prompt", user("continue", "u14")),
    ("prompt", user("continue", "u15")),
    # A message the owner sends MID-TURN is never a user row: the harness records it as an
    # `attachment` of type `queued_command`. Until this shape parsed, such a message was invisible
    # to every consumer, so a stop order given mid-turn could not authorize a TaskStop.
    ("queued_prompt (mid-turn)",
     {"type": "attachment", "uuid": "u17",
      "attachment": {"type": "queued_command", "prompt": "stop the two hung codex shells"}}),
]
TURN_BEFORE_ANSWERS = "u08"   # the latest turn when the transcript ends at the tool_result row u13


def table(doc):
    rows = {}
    for line in (doc or "").splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[0] == "shape" or len(cells) < 4:
            continue
        rows[cells[0]] = cells[1:4]
    return rows


def main():
    cases = []
    try:
        ot = load("_owner_text", HOOKS / "_owner_text.py")
    except Exception as exc:  # the RED state before the module exists
        print("FAIL _owner_text imports (%s: %s)" % (type(exc).__name__, exc))
        print("\n0/1 cases pass")
        return 1
    kg = load("kg", HOOKS / "kill_guard.py")
    gg = load("gg", HOOKS / "harness_growth_guard.py")
    sd = load("sd", TOOLS / "session_digest.py")

    expected = table(ot.__doc__)
    shapes = sorted({label for label, _ in FIXTURE if label})
    cases.append(("the docstring table names every fixture shape", set(shapes) <= set(expected)))

    lines = [json.dumps(row) for _, row in FIXTURE]
    rows = [ot.classify(entry, i + 1, raw=lines[i]) for i, (_, entry) in enumerate(FIXTURE)]
    by_uuid = {entry["uuid"]: row for (_, entry), row in zip(FIXTURE, rows)}

    # classify: the typed superset row.
    cases.append(("an assistant row is not an owner row", by_uuid["u10"] is None))
    cases.append(("every user row classifies", all(row is not None for (label, _), row in zip(FIXTURE, rows) if label)))
    r = by_uuid
    kinds = {u: (r[u].kind if r[u] else None) for u in r}
    cases.append(("kinds per shape", kinds == {
        "u01": "prompt", "u02": "prompt", "u03": "meta_arguments", "u04": "prompt", "u05": "prompt",
        "u06": "prompt", "u07": "prompt", "u08": "command", "u09": "command", "u10": None,
        "u11": "answer", "u12": "tool_result", "u13": "prompt", "u14": "prompt", "u15": "prompt",
        "u17": "queued_prompt"}))
    cases.append(("index is the 1-based transcript line", [row.index for row in rows if row] ==
                  [i + 1 for i, row in enumerate(rows) if row]))
    cases.append(("meta and sidechain flags", r["u02"].meta and r["u03"].meta and r["u04"].sidechain
                  and not r["u01"].meta and not r["u01"].sidechain))
    cases.append(("meta ARGUMENTS carries its label and goal",
                  (r["u03"].command_name, r["u03"].command_args) == ("/overnight", "finish the wave")))
    cases.append(("an injection prefix is stripped and flagged",
                  r["u05"].injection_prefixed and r["u05"].text == "Please keep going with the plan."
                  and not r["u01"].injection_prefixed))
    cases.append(("runtime envelopes are named", (r["u06"].envelope, r["u07"].envelope, r["u01"].envelope)
                  == ("task-notification", "agent-message", None)))
    cases.append(("a command row carries name and args",
                  (r["u08"].command_name, r["u08"].command_args) == ("/model", "fable")))
    cases.append(("an empty-args command row carries empty args",
                  (r["u09"].command_name, r["u09"].command_args) == ("/clear", "")))
    cases.append(("toolUseResult answers become strict pairs",
                  list(r["u11"].pairs or []) == [(STOP_QUESTION, "Yes, stop it")]))
    cases.append(("a tool_result without toolUseResult has no pairs but keeps its result text",
                  r["u12"].pairs is None and list(r["u12"].tool_results) == [("toolu_q2", QUESTION_TEXT, False)]))
    cases.append(("tool_result beside text keeps both", r["u13"].text == "text typed beside a tool result"
                  and [t[0] for t in r["u13"].tool_results] == ["toolu_b1"]))
    cases.append(("two identical prompts get two identities",
                  r["u14"].text == r["u15"].text and r["u14"].uuid != r["u15"].uuid
                  and (r["u14"].uuid, r["u15"].uuid) == ("u14", "u15")))
    bare = {"type": "user", "message": {"role": "user", "content": "no uuid here"}}
    raw = json.dumps(bare)
    cases.append(("without a uuid, identity is the raw line hash",
                  ot.classify(bare, 1, raw=raw).uuid == hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]))

    # F9: a tool_result text block whose `text` is not a string never raises.
    odd = user([{"type": "tool_result", "tool_use_id": "toolu_odd",
                 "content": [{"type": "text", "text": {"nested": 1}}]}], "u16")
    try:
        odd_row = ot.classify(odd, 16)
        odd_ok = odd_row is not None and odd_row.tool_results[0][1] == str({"nested": 1})
    except Exception as exc:
        odd_ok = False
        print("     classify raised %s: %s" % (type(exc).__name__, exc))
    cases.append(("a non-string tool_result text block converts with str() and never raises", odd_ok))

    # Projections against the docstring table.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fixture.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        digest = {m["index"]: m["content"] for m in sd.owner_prompts(path)}
        head = Path(tmp) / "head.jsonl"
        head.write_text("\n".join(lines[:13]) + "\n", encoding="utf-8")
        latest_full = gg._latest_turn_key(str(path))
        latest_head = gg._latest_turn_key(str(head))

    starts_turn = getattr(gg, "_starts_turn", None)
    for (label, entry), row in zip(FIXTURE, rows):
        if not label or label not in expected:
            continue
        k_cell, g_cell, s_cell = expected[label]
        name = "%s [%s]" % (label, entry["uuid"])
        text, pairs = kg._message_text(entry), kg._askuserquestion_answers(entry)
        k_ok = {"drop": text is None and pairs is None,
                "text": text == row.text and pairs is None,
                "args": text == row.command_args and pairs is None,
                "pairs": text is None and pairs is not None and list(pairs) == list(row.pairs or [])}.get(k_cell)
        cases.append(("kill_guard %s: %s" % (k_cell, name), bool(k_ok)))
        g_ok = starts_turn is not None and {"turn": True, "no turn": False}.get(g_cell) == starts_turn(row)
        cases.append(("harness_growth_guard %s: %s" % (g_cell, name), bool(g_ok)))
        got = digest.get(row.index)
        s_ok = {"drop": got is None,
                "text": got == " ".join(row.blocks),
                "name args": got == ("%s %s" % (row.command_name or "", row.command_args or "")).strip(),
                "label goal": got == "%s %s" % (row.command_name, row.command_args),
                "answer": got == "(answer) " + row.tool_results[0][1].strip() if row.tool_results else False,
                }.get(s_cell)
        cases.append(("digest %s: %s (got %r)" % (s_cell, name, got), bool(s_ok)))

    cases.append(("growth turn key is the latest turn row's uuid", latest_full == "u15"))
    cases.append(("growth skips no-turn rows back to the last turn row", latest_head == TURN_BEFORE_ANSWERS))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
