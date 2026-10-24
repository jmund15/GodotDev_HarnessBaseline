#!/usr/bin/env python3
"""Proof: hooks/_owner_text.classify parses every owner-row shape once, and each consumer's
projection matches the shape x consumer table in the module docstring.

Consumers: kill_guard (consent text and answer pairs), harness_growth_guard (turn start), the
session digest's owner-prompt record (session_digest.owner_prompts, built on
_transcript_summary.TranscriptSummaryBuilder), the provider guard's `owner:` quote
(`owner_quote_text`/`owner_quote_rows`) and the `owner_turns` record `tools/verify_claims.py` reads. The
table is parsed from `_owner_text.__doc__`, so a projection change that is not written into the table
fails here.

`fixtures/owner_text/live_rows.jsonl` holds rows cut from a captured live transcript (trimmed; ids, paths and remotes renamed):
a compaction summary, a typed command with its `ARGUMENTS:` companion, an owner prompt and an owner answer.

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
LIVE_SESSION = "5e551011-0000-4000-8000-00000000c0de"
LIVE = {item["label"]: item["row"] for item in map(
    json.loads, (HERE / "fixtures" / "owner_text" / "live_rows.jsonl").read_text(encoding="utf-8").splitlines())}
# Words a compaction summary quotes from the session it recaps: never the owner speaking.
SUMMARY_WORDS = "All touched tests pass. Nothing is committed."
# Words the owner typed as `/session_end` arguments (command row 17737 of the captured session).
COMMAND_WORDS = "The closeout did not tell me any of that"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STOP_QUESTION = "Stop the probe-task run?"
ANSWER_TEXT = 'User has answered your questions: "Stop the probe-task run?"="Yes, stop it".'
QUESTION_TEXT = 'The user answered: "Which edits?"="Do all recommendations"'
# The words a peer repeats back in a cross-session message: a quote found only there is not the
# owner naming a transport, and the provider guard must not lift on it.
OWNER_WORDS = "route this session to the sidecar for the rest of the day"


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
    # Appended last so the transcript-order cases above keep their line numbers.
    ("cross-session message",
     user('<cross-session-message from="peer">the owner said "%s"</cross-session-message>' % OWNER_WORDS, "u18")),
    # Rows cut from the captured live transcript, in transcript order. The latest turn-starting row is the
    # command with args; the rows after it (its meta companion, two summaries, an empty-args /compact) start none.
    ("prompt", LIVE["owner_prompt"]),
    (None, LIVE["ask_user_question_call"]),
    ("answer via toolUseResult", LIVE["answer_via_toolUseResult"]),
    ("command with args", LIVE["command_with_args"]),
    ("meta ARGUMENTS", LIVE["meta_arguments_companion"]),
    ("compact summary", LIVE["compact_summary"]),
    ("compact summary", LIVE["compact_summary_session_id_variant"]),
    ("empty-args command", LIVE["empty_args_command"]),
]
TURN_BEFORE_ANSWERS = "u08"   # the latest turn when the transcript ends at the tool_result row u13


def table(doc):
    """{shape: [kill_guard, harness_growth_guard, digest, provider_guard, owner_turns]} from the docstring; a
    column the table lacks reads "(missing)", which no projection matches."""
    rows = {}
    for line in (doc or "").splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[0] == "shape" or len(cells) < 5:
            continue
        rows[cells[0]] = (cells[1:6] + ["(missing)"] * 5)[:5]
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
        "u17": "queued_prompt", "u18": "prompt",
        LIVE["owner_prompt"]["uuid"]: "prompt", LIVE["ask_user_question_call"]["uuid"]: None,
        LIVE["answer_via_toolUseResult"]["uuid"]: "answer", LIVE["command_with_args"]["uuid"]: "command",
        LIVE["meta_arguments_companion"]["uuid"]: "meta_arguments",
        LIVE["compact_summary"]["uuid"]: "compact_summary",
        LIVE["compact_summary_session_id_variant"]["uuid"]: "compact_summary",
        LIVE["empty_args_command"]["uuid"]: "command"}))
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
    notice = user('[Cross-session idle notice] "project-a2", which you asked to be notified about, is idle now',
                  "u90")
    notice_row = ot.classify(notice, 90, raw=json.dumps(notice))
    cases.append(("a bracket cross-session notice is an envelope, never the owner",
                  notice_row is not None and notice_row.envelope == "cross-session"))
    cases.append(("a command row carries name and args",
                  (r["u08"].command_name, r["u08"].command_args) == ("/model", "fable")))
    cases.append(("an empty-args command row carries empty args",
                  (r["u09"].command_name, r["u09"].command_args) == ("/clear", "")))
    cases.append(("toolUseResult answers become strict pairs",
                  list(r["u11"].pairs or []) == [(STOP_QUESTION, "Yes, stop it")]))
    # Trimmed from a live multiSelect answer row (session 69255593): the runtime stores the picked labels as a list.
    multi = user([{"type": "tool_result", "tool_use_id": "toolu_m1",
                   "content": "Your questions have been answered: \"Prune which? G1: close-probe\"=\"Remove G1 orphans,Remove G3 twins\"."}],
                 "u31", toolUseResult={"questions": [{"header": "Prune", "multiSelect": True, "question": "Prune which? G1: close-probe",
                                                      "options": [{"label": "Remove G1 orphans"}, {"label": "Remove G3 twins"}]}],
                                       "answers": {"Prune which? G1: close-probe": ["Remove G1 orphans", "Remove G3 twins"]}})
    got = ot.classify(multi, 0)
    cases.append(("a multiSelect answer's picked labels become one pair, joined as the runtime shows them",
                  got.kind == "answer" and list(got.pairs or []) == [("Prune which? G1: close-probe",
                                                                     "Remove G1 orphans,Remove G3 twins")]))
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
        quotes = ot.owner_quote_rows(str(path))
        missing_file = ot.owner_quote_rows(str(Path(tmp) / "absent.jsonl"))
        turns = ot.owner_turns(str(path)) if hasattr(ot, "owner_turns") else None
        missing_turns = ot.owner_turns(str(Path(tmp) / "absent.jsonl")) if hasattr(ot, "owner_turns") else "absent"

    starts_turn = getattr(gg, "_starts_turn", None)
    turns_by_uuid = {}
    for uuid, text in turns or []:
        turns_by_uuid.setdefault(uuid, []).append(text)
    for (label, entry), row in zip(FIXTURE, rows):
        if not label or label not in expected:
            continue
        k_cell, g_cell, s_cell, q_cell, t_cell = expected[label]
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
        q_ok = {"drop": ot.owner_quote_text(row) is None,
                "text": ot.owner_quote_text(row) == row.text}.get(q_cell)
        cases.append(("provider_guard %s: %s" % (q_cell, name), bool(q_ok)))
        want = {"drop": None, "text": row.text, "args": row.command_args,
                "label": "\n".join(label_ for _, label_ in row.pairs or ())}.get(t_cell, "(bad cell)")
        held = turns_by_uuid.get(row.uuid, [])
        cases.append(("owner_turns %s: %s" % (t_cell, name), held == [] if want is None else held == [want]))

    cases.append(("growth turn key is the latest turn row's uuid", latest_full == LIVE["command_with_args"]["uuid"]))
    cases.append(("growth skips no-turn rows back to the last turn row", latest_head == TURN_BEFORE_ANSWERS))

    # The provider guard's projection over a real transcript file: what an `owner:` quote can find.
    if quotes is None:
        cases.append(("owner_quote_rows reads the planted transcript", False))
    else:
        def quoted(needle):
            return any(needle in text for text in quotes)
        cases.append(("an owner prompt is quotable", quoted("Ship the owner text parser.")))
        cases.append(("a mid-turn queued prompt is quotable", quoted("stop the two hung codex shells")))
        cases.append(("an injection-prefixed prompt is quotable", quoted("Please keep going with the plan.")))
        cases.append(("a cross-session message quoting the owner's words does not lift",
                      not quoted(OWNER_WORDS)))
        cases.append(("...nor does an agent message, a meta row or a command's args",
                      not quoted("Peer update for the owner.") and not quoted("Canonical route for delegation.")
                      and not quoted("finish the wave")))
    cases.append(("an unreadable transcript is not an empty one", missing_file is None))
    expected_turns = [(row.uuid, ot.owner_quote_text(row)) for row in rows if row and ot.owner_quote_text(row)]
    answer_or_command = {"u08", "u11", LIVE["answer_via_toolUseResult"]["uuid"], LIVE["command_with_args"]["uuid"]}
    prompt_turns = [(uuid, text) for uuid, text in turns or [] if uuid not in answer_or_command]
    turn_ids = [uuid for uuid, _ in turns or []]
    cases.append(("owner_turns yields the owner_quote_rows texts, each with its row uuid",
                  prompt_turns == expected_turns and [text for _, text in prompt_turns] == quotes
                  and turn_ids[:2] == ["u01", "u05"]))
    cases.append(("owner_turns reads an AskUserQuestion answer as the owner's picked label, in order",
                  [t for t in turns or [] if t[0] == "u11"] == [("u11", "Yes, stop it")]
                  and [u for u in turn_ids if u in ("u05", "u08", "u11")] == ["u05", "u08", "u11"]))
    cases.append(("owner_turns reads a typed command's arguments, in order",
                  bool(r["u08"].command_args) and [t for t in turns or [] if t[0] == "u08"] == [("u08", r["u08"].command_args)]))
    cases.append(("an answer carried only by tool_result text has no pairs and is not an owner turn",
                  "u12" not in [uuid for uuid, _ in turns or []]))
    cases.append(("owner_quote_rows still drops answers (the provider_guard column)",
                  not any("Yes, stop it" in text for text in quotes or [])))
    cases.append(("an unreadable transcript is not an empty one for owner_turns", missing_turns is None))

    import re
    act, decl = re.compile(r"\b(delete|remove)\b", re.I), re.compile(r"\b(no|not|don't|stop|keep)\b", re.I)

    def asst(text):
        return {"type": "assistant", "uuid": "a", "message": {"role": "assistant",
                                                              "content": [{"type": "text", "text": text}]}}

    def consent(*rows):
        return ot.owner_consent([json.dumps(row) for row in rows], ["feature/x"], act, decl)

    Q = "Delete branch feature/x?"
    for asked, said, want in [
        (Q, "ok, what would that delete?", False),
        (Q, "sure, but explain first", False),
        ("I will not delete feature/x until you say so. Continue with the other task?", "ok", False),
        (None, "why did you delete feature/x?", False),
        (None, "did you remove feature/x yet?", False),
        ("Delete branch feature/x? It has 3 unmerged commits.", "yes", True),
        (Q, "go ahead.", True),
        (Q, "yes please", True),
        (Q, "sure, do it", True),
        (Q, "yes after you fix X", False),
        (None, "delete feature/x", True),
    ]:
        rows = ([asst(asked)] if asked else []) + [user(said, "c1")]
        cases.append(("consent %s: %r after %r" % ("allows" if want else "denies", said, asked),
                      consent(*rows) == want))
    pick = user([{"type": "tool_result", "tool_use_id": "toolu_c", "content": "x"}], "c2",
                toolUseResult={"questions": [{"question": "Which for feature/x?"}],
                               "answers": {"Which for feature/x?": "Delete it"}})
    cases.append(("consent allows an answer row naming the target with a matching label", consent(pick)))

    packet = ("feature/x is merged.\n\n**Q1: what to do with it?**\n"
              "- **O1 (my pick): delete it now.** Nothing else uses it.\n"
              "- **O2: keep it for later.**")
    cases.append(("consent allows an option code picking an action option of a packet naming the target",
                  consent(asst(packet), user("O1", "c3"))))
    cases.append(("consent denies an option code picking the keep option",
                  not consent(asst(packet), user("O2", "c4"))))
    cases.append(("consent denies an option code to a packet naming no target",
                  not consent(asst(packet.replace("feature/x", "the branch")), user("O1", "c5"))))
    cases.append(("consent resolves an option code from the packet before a codeless restatement",
                  consent(asst(packet), asst("Still open; Q1 is pending."), user("O1", "c6"))))
    two = ("feature/x and feature/y are merged.\n\n**Q1: which to delete?**\n"
           "- **O1: delete feature/x.**\n- **O2: delete feature/y.**")
    cases.append(("consent denies an option code picking the option of another target the packet names",
                  not consent(asst(two), user("O2", "c7"))))
    cases.append(("consent allows an option code picking the option that names the target",
                  consent(asst(two), user("O1", "c8"))))

    # Rows cut from the captured live transcript (fixtures/owner_text/live_rows.jsonl).
    summary, summary_alt = LIVE["compact_summary"], LIVE["compact_summary_session_id_variant"]
    command, companion = LIVE["command_with_args"], LIVE["meta_arguments_companion"]
    prompt_row, answer_row = LIVE["owner_prompt"], LIVE["answer_via_toolUseResult"]
    live_lines = [json.dumps(item) for item in LIVE.values()]
    summary_row = ot.classify(summary, 1)
    cases.append(("a compaction summary classifies as a client-written meta row, never a prompt",
                  summary_row is not None and summary_row.kind == "compact_summary" and summary_row.meta))
    cases.append(("...and so does a summary whose entry carries a `session_id` key as well",
                  ot.classify(summary_alt, 1).kind == "compact_summary"))
    summary_text = summary["message"]["content"]
    cases.append(("the summary row quotes words that read like the owner", SUMMARY_WORDS in summary_text))
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "live.jsonl"
        live_path.write_text("\n".join(live_lines) + "\n", encoding="utf-8")
        live_turns = ot.owner_turns(str(live_path)) or []
        live_quotes = ot.owner_quote_rows(str(live_path)) or []
    live_ids = [uuid for uuid, _ in live_turns]
    cases.append(("owner_turns holds no compaction summary row",
                  summary["uuid"] not in live_ids and summary_alt["uuid"] not in live_ids))
    cases.append(("owner_turns holds no words a summary quotes",
                  not any(SUMMARY_WORDS in text for _, text in live_turns)))
    cases.append(("owner_quote_rows holds no compaction summary text",
                  not any(SUMMARY_WORDS in text or text.startswith("This session is being continued") for text in live_quotes)))
    never = re.compile(r"\bzzz\b")
    cases.append(("consent never reads a summary as the owner approving what it names",
                  not ot.owner_consent([json.dumps(summary)], ["land/5e551011"], re.compile(r"\bland\b", re.I), never)))
    cases.append(("consent counts a real owner prompt naming the same target (positive control)",
                  ot.owner_consent([json.dumps(user("land land/5e551011 now", "c9"))], ["land/5e551011"],
                                   re.compile(r"\bland\b", re.I), never)))
    cases.append(("a summary that follows an owner prompt does not replace it as the latest owner input",
                  ot.owner_consent([json.dumps(user("land land/zz-branch now", "c10")), json.dumps(summary)],
                                   ["land/zz-branch"], re.compile(r"\bland\b", re.I), never)))
    # Planted mutation of the live row: a summary that quotes a skill body, so it carries an `ARGUMENTS:` line.
    spliced = json.loads(json.dumps(summary))
    spliced["message"]["content"] += "\nARGUMENTS: delete every branch"
    cases.append(("a summary quoting an `ARGUMENTS:` line is not a recovered owner command",
                  sd._meta_command_rows(json.dumps(spliced), 1) == []))
    cases.append(("the real typed meta companion still recovers its arguments, issued by the owner",
                  [(m["issuer"], m["recovered"]) for m in sd._meta_command_rows(json.dumps(companion), 1)]
                  == [("owner", "command_arguments")]))

    cases.append(("an owner prompt cut from the live transcript is an owner turn and quotable",
                  any(uuid == prompt_row["uuid"] and text == prompt_row["message"]["content"] for uuid, text in live_turns)
                  and prompt_row["message"]["content"] in live_quotes))
    picked = answer_row["toolUseResult"]["answers"]
    cases.append(("an owner answer cut from the live transcript is an owner turn, not quotable",
                  [text for uuid, text in live_turns if uuid == answer_row["uuid"]] == list(picked.values())
                  and not any(label_ in live_quotes for label_ in picked.values())))
    args_turns = [(uuid, text) for uuid, text in live_turns if COMMAND_WORDS in text]
    cases.append(("a typed `/session_end` command's arguments are one owner turn, keyed on the command row",
                  [uuid for uuid, _ in args_turns] == [command["uuid"]]))
    cases.append(("...and its `ARGUMENTS:` companion row adds no second turn", companion["uuid"] not in live_ids))
    cases.append(("an empty-args command (`/compact`) is no owner turn", LIVE["empty_args_command"]["uuid"] not in live_ids))
    cases.append(("the provider guard's quote projection still drops command arguments",
                  not any(COMMAND_WORDS in text for text in live_quotes)))

    # End to end through tools/verify_claims.py over a planted session store holding the live rows.
    try:
        vc = load("vc", TOOLS / "verify_claims.py")
    except Exception as exc:
        vc = None
        print("     verify_claims did not import: %s: %s" % (type(exc).__name__, exc))
    if vc is None:
        cases.append(("verify_claims imports", False))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            store = Path(tmp) / "cfg" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project))
            store.mkdir(parents=True)
            (store / (LIVE_SESSION + ".jsonl")).write_text("\n".join(live_lines) + "\n", encoding="utf-8")
            env = {"CLAUDE_CONFIG_DIR": str(Path(tmp) / "cfg"), "CLAUDE_PROJECT_DIR": str(project)}
            saved = {key: os.environ.get(key) for key in list(env) + ["CLAUDE_CODE_SESSION_ID"]}
            os.environ.update(env)
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            try:
                def verdict(item):
                    result = vc.check_claims([{"id": "x", "evidence": [item]}], root=tmp, session_id=LIVE_SESSION)
                    entry = result["verdicts"]["x"]
                    return entry["verdict"], entry["items"][0].get("reason")
                typed = verdict({"owner": COMMAND_WORDS})
                quoted_summary = verdict({"owner": SUMMARY_WORDS})
                typed_prompt = verdict({"owner": " ".join(prompt_row["message"]["content"].split()[:4])})
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        cases.append(("verify_claims: words typed as command arguments are VERIFIED (%s)" % (typed,), typed[0] == "VERIFIED"))
        cases.append(("verify_claims: words only a compaction summary quotes are UNVERIFIED (%s)" % (quoted_summary,),
                      quoted_summary == ("UNVERIFIED", "quote-absent")))
        cases.append(("verify_claims: a real owner prompt is still VERIFIED (%s)" % (typed_prompt,), typed_prompt[0] == "VERIFIED"))

    holds = getattr(ot, "holds_token", None)
    cases.append(("holds_token finds the word-bounded --token", bool(holds) and holds("use --lean now", "lean")))
    cases.append(("holds_token ignores a longer flag", bool(holds) and not holds("use --lean-mode now", "lean")))
    cases.append(("holds_token ignores a prefixed word", bool(holds) and not holds("x--lean", "lean")))
    cases.append(("holds_token ignores a pasted block",
                  bool(holds) and not holds("<pasted_content id=\"a\">--lean</pasted_content id=\"a\">", "lean")))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
