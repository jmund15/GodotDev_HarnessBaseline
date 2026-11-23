#!/usr/bin/env python3
"""Proof: hooks/_owner_text.classify parses every owner-row shape once, and each consumer's
projection matches the shape x consumer table in the module docstring.

Consumers: the
session digest's owner-prompt record (session_digest.owner_prompts, built on
_transcript_summary.TranscriptSummaryBuilder), the `owner:` quote
(`owner_quote_text`/`owner_quote_rows`) and the `owner_turns` record. The
table is parsed from `_owner_text.__doc__`, so a projection change that is not written into the table
fails here.

`fixtures/owner_text/live_rows.jsonl` holds rows cut from a captured live transcript (trimmed; ids, paths and remotes renamed):
a compaction summary, a typed command with its `ARGUMENTS:` companion, an owner prompt and an owner answer.

    python -m pytest .claude/tests/test_owner_text.py -q
"""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent / "hooks"
TOOLS = HERE.parent / "tools"
LIVE = {item["label"]: item["row"] for item in map(
    json.loads, (HERE / "fixtures" / "owner_text" / "live_rows.jsonl").read_text(encoding="utf-8").splitlines())}
# Words a compaction summary quotes from the session it recaps: never the owner speaking.
SUMMARY_WORDS = "All touched tests pass. Nothing is committed."
# Words the owner typed as `/session_end` arguments (command row 17737 of the captured session).
COMMAND_WORDS = "The closeout did not tell me any of that"
STOP_QUESTION = "Stop the probe-task run?"
ANSWER_TEXT = 'User has answered your questions: "Stop the probe-task run?"="Yes, stop it".'
QUESTION_TEXT = 'The user answered: "Which edits?"="Do all recommendations"'
# The words a peer repeats back in a cross-session message: a quote found only there is not the
# owner naming a transport, and the owner-quote check must not lift on it.
OWNER_WORDS = "route this session to the sidecar for the rest of the day"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
OT = load("_owner_text", HOOKS / "_owner_text.py")
SD = load("sd", TOOLS / "session_digest.py")


def table(doc):
    """{shape: [digest, quote, owner_turns]} from the docstring; a
    column the table lacks reads "(missing)", which no projection matches."""
    rows = {}
    for line in (doc or "").splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells[0] == "shape" or len(cells) < 3:
            continue
        rows[cells[0]] = (cells[1:4] + ["(missing)"] * 3)[:3]
    return rows


EXPECTED = table(OT.__doc__)
LINES = [json.dumps(row) for _, row in FIXTURE]
ROWS = [OT.classify(entry, i + 1, raw=LINES[i]) for i, (_, entry) in enumerate(FIXTURE)]
BY_UUID = {entry["uuid"]: row for (_, entry), row in zip(FIXTURE, ROWS)}
R = BY_UUID
# Each labelled fixture row that a consumer projects: (position in FIXTURE, shape label, uuid).
PROJECTED = [(i, label, entry["uuid"]) for i, (label, entry) in enumerate(FIXTURE)
             if label and label in EXPECTED]


@pytest.fixture(scope="module")
def transcript(tmp_path_factory):
    """The fixture rows as a transcript file, read by the digest, the quote and the turns projections."""
    path = tmp_path_factory.mktemp("owner_text") / "fixture.jsonl"
    path.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture(scope="module")
def digest(transcript):
    return {m["index"]: m["content"] for m in SD.owner_prompts(Path(transcript))}


@pytest.fixture(scope="module")
def quotes(transcript):
    return OT.owner_quote_rows(transcript)


@pytest.fixture(scope="module")
def turns(transcript):
    return OT.owner_turns(transcript) if hasattr(OT, "owner_turns") else None


@pytest.fixture(scope="module")
def missing_file(tmp_path_factory):
    return str(tmp_path_factory.mktemp("owner_text_absent") / "absent.jsonl")


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """The live rows as a transcript file, with its owner turns and owner-quote rows."""
    path = tmp_path_factory.mktemp("owner_text_live") / "live.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in LIVE.values()) + "\n", encoding="utf-8")
    return SimpleNamespace(turns=OT.owner_turns(str(path)) or [], quotes=OT.owner_quote_rows(str(path)) or [])


# --- classify: the typed superset row ----------------------------------------------------------

def test_the_docstring_table_names_every_fixture_shape():
    shapes = {label for label, _ in FIXTURE if label}
    assert shapes <= set(EXPECTED)


def test_an_assistant_row_is_not_an_owner_row():
    assert R["u10"] is None


def test_every_user_row_classifies():
    assert all(row is not None for (label, _), row in zip(FIXTURE, ROWS) if label)


def test_the_kind_of_each_row_by_shape():
    kinds = {u: (R[u].kind if R[u] else None) for u in R}
    assert kinds == {
        "u01": "prompt", "u02": "prompt", "u03": "meta_arguments", "u04": "prompt", "u05": "prompt",
        "u06": "prompt", "u07": "prompt", "u08": "command", "u09": "command", "u10": None,
        "u11": "answer", "u12": "tool_result", "u13": "prompt", "u14": "prompt", "u15": "prompt",
        "u17": "queued_prompt", "u18": "prompt",
        LIVE["owner_prompt"]["uuid"]: "prompt", LIVE["ask_user_question_call"]["uuid"]: None,
        LIVE["answer_via_toolUseResult"]["uuid"]: "answer", LIVE["command_with_args"]["uuid"]: "command",
        LIVE["meta_arguments_companion"]["uuid"]: "meta_arguments",
        LIVE["compact_summary"]["uuid"]: "compact_summary",
        LIVE["compact_summary_session_id_variant"]["uuid"]: "compact_summary",
        LIVE["empty_args_command"]["uuid"]: "command"}


def test_the_index_is_the_1_based_transcript_line():
    assert [row.index for row in ROWS if row] == [i + 1 for i, row in enumerate(ROWS) if row]


def test_meta_and_sidechain_flags():
    assert R["u02"].meta and R["u03"].meta and R["u04"].sidechain and not R["u01"].meta and not R["u01"].sidechain


def test_meta_arguments_carries_its_label_and_goal():
    assert (R["u03"].command_name, R["u03"].command_args) == ("/overnight", "finish the wave")


def test_an_injection_prefix_is_stripped_and_flagged():
    assert R["u05"].injection_prefixed and R["u05"].text == "Please keep going with the plan." \
        and not R["u01"].injection_prefixed


def test_runtime_envelopes_are_named():
    assert (R["u06"].envelope, R["u07"].envelope, R["u01"].envelope) == ("task-notification", "agent-message", None)


def test_a_bracket_cross_session_notice_is_an_envelope_never_the_owner():
    notice = user('[Cross-session idle notice] "project-a2", which you asked to be notified about, is idle now', "u90")
    notice_row = OT.classify(notice, 90, raw=json.dumps(notice))
    assert notice_row is not None and notice_row.envelope == "cross-session"


def test_a_command_row_carries_name_and_args():
    assert (R["u08"].command_name, R["u08"].command_args) == ("/model", "fable")


def test_an_empty_args_command_row_carries_empty_args():
    assert (R["u09"].command_name, R["u09"].command_args) == ("/clear", "")


def test_toolUseResult_answers_become_strict_pairs():
    assert list(R["u11"].pairs or []) == [(STOP_QUESTION, "Yes, stop it")]


def test_a_multiselect_answers_picked_labels_become_one_pair_joined_as_the_runtime_shows_them():
    """Trimmed from a live multiSelect answer row (session 69255593): the runtime stores the picked labels as a list."""
    multi = user([{"type": "tool_result", "tool_use_id": "toolu_m1",
                   "content": "Your questions have been answered: \"Prune which? G1: close-probe\"=\"Remove G1 orphans,Remove G3 twins\"."}],
                 "u31", toolUseResult={"questions": [{"header": "Prune", "multiSelect": True, "question": "Prune which? G1: close-probe",
                                                      "options": [{"label": "Remove G1 orphans"}, {"label": "Remove G3 twins"}]}],
                                       "answers": {"Prune which? G1: close-probe": ["Remove G1 orphans", "Remove G3 twins"]}})
    got = OT.classify(multi, 0)
    assert got.kind == "answer" and list(got.pairs or []) == [
        ("Prune which? G1: close-probe", "Remove G1 orphans,Remove G3 twins")]


def test_a_tool_result_without_toolUseResult_has_no_pairs_but_keeps_its_result_text():
    assert R["u12"].pairs is None and list(R["u12"].tool_results) == [("toolu_q2", QUESTION_TEXT, False)]


def test_a_tool_result_beside_text_keeps_both():
    assert R["u13"].text == "text typed beside a tool result" and [t[0] for t in R["u13"].tool_results] == ["toolu_b1"]


def test_two_identical_prompts_get_two_identities():
    assert R["u14"].text == R["u15"].text and R["u14"].uuid != R["u15"].uuid \
        and (R["u14"].uuid, R["u15"].uuid) == ("u14", "u15")


def test_without_a_uuid_identity_is_the_raw_line_hash():
    bare = {"type": "user", "message": {"role": "user", "content": "no uuid here"}}
    raw = json.dumps(bare)
    assert OT.classify(bare, 1, raw=raw).uuid == hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def test_a_non_string_tool_result_text_block_converts_with_str_and_never_raises():
    """F9."""
    odd = user([{"type": "tool_result", "tool_use_id": "toolu_odd",
                 "content": [{"type": "text", "text": {"nested": 1}}]}], "u16")
    odd_row = OT.classify(odd, 16)
    assert odd_row is not None and odd_row.tool_results[0][1] == str({"nested": 1})


# --- projections against the docstring table ---------------------------------------------------

@pytest.mark.parametrize("position, label, uuid", PROJECTED, ids=[f"{l} [{u}]" for _, l, u in PROJECTED])
def test_the_digest_projection_matches_the_table(digest, position, label, uuid):
    row = ROWS[position]
    s_cell = EXPECTED[label][0]
    got = digest.get(row.index)
    s_ok = {"drop": got is None,
            "text": got == " ".join(row.blocks),
            "name args": got == ("%s %s" % (row.command_name or "", row.command_args or "")).strip(),
            "label goal": got == "%s %s" % (row.command_name, row.command_args),
            "answer": got == "(answer) " + row.tool_results[0][1].strip() if row.tool_results else False,
            }.get(s_cell)
    assert s_ok, "digest %s, got %r" % (s_cell, got)


@pytest.mark.parametrize("position, label, uuid", PROJECTED, ids=[f"{l} [{u}]" for _, l, u in PROJECTED])
def test_the_quote_projection_matches_the_table(position, label, uuid):
    row = ROWS[position]
    q_cell = EXPECTED[label][1]
    q_ok = {"drop": OT.owner_quote_text(row) is None,
            "text": OT.owner_quote_text(row) == row.text}.get(q_cell)
    assert q_ok, "quote %s" % q_cell


@pytest.mark.parametrize("position, label, uuid", PROJECTED, ids=[f"{l} [{u}]" for _, l, u in PROJECTED])
def test_the_owner_turns_projection_matches_the_table(turns, position, label, uuid):
    row = ROWS[position]
    t_cell = EXPECTED[label][2]
    turns_by_uuid = {}
    for turn_uuid, text in turns or []:
        turns_by_uuid.setdefault(turn_uuid, []).append(text)
    want = {"drop": None, "text": row.text, "args": row.command_args,
            "label": "\n".join(label_ for _, label_ in row.pairs or ())}.get(t_cell, "(bad cell)")
    held = turns_by_uuid.get(row.uuid, [])
    assert held == [] if want is None else held == [want], "owner_turns %s, held %r" % (t_cell, held)


# --- the owner-quote projection over a real transcript file ------------------------------------

def test_owner_quote_rows_reads_the_planted_transcript(quotes):
    assert quotes is not None


def test_an_owner_prompt_is_quotable(quotes):
    assert any("Ship the owner text parser." in text for text in quotes)


def test_a_mid_turn_queued_prompt_is_quotable(quotes):
    assert any("stop the two hung codex shells" in text for text in quotes)


def test_an_injection_prefixed_prompt_is_quotable(quotes):
    assert any("Please keep going with the plan." in text for text in quotes)


def test_a_cross_session_message_quoting_the_owners_words_does_not_lift(quotes):
    assert not any(OWNER_WORDS in text for text in quotes)


def test_nor_does_an_agent_message_a_meta_row_or_a_commands_args(quotes):
    assert not any("Peer update for the owner." in text for text in quotes)
    assert not any("Canonical route for delegation." in text for text in quotes)
    assert not any("finish the wave" in text for text in quotes)


def test_an_unreadable_transcript_is_not_an_empty_one_for_owner_quote_rows(tmp_path):
    assert OT.owner_quote_rows(str(tmp_path / "absent.jsonl")) is None


def test_the_owner_turns_yield_the_owner_quote_rows_texts_each_with_its_row_uuid(turns, quotes):
    expected_turns = [(row.uuid, OT.owner_quote_text(row)) for row in ROWS if row and OT.owner_quote_text(row)]
    answer_or_command = {"u08", "u11", LIVE["answer_via_toolUseResult"]["uuid"], LIVE["command_with_args"]["uuid"]}
    prompt_turns = [(uuid, text) for uuid, text in turns or [] if uuid not in answer_or_command]
    turn_ids = [uuid for uuid, _ in turns or []]
    assert prompt_turns == expected_turns and [text for _, text in prompt_turns] == quotes \
        and turn_ids[:2] == ["u01", "u05"]


def test_owner_turns_reads_an_ask_user_question_answer_as_the_owners_picked_label_in_order(turns):
    turn_ids = [uuid for uuid, _ in turns or []]
    assert [t for t in turns or [] if t[0] == "u11"] == [("u11", "Yes, stop it")] \
        and [u for u in turn_ids if u in ("u05", "u08", "u11")] == ["u05", "u08", "u11"]


def test_owner_turns_reads_a_typed_commands_arguments_in_order(turns):
    assert bool(R["u08"].command_args) and [t for t in turns or [] if t[0] == "u08"] == [("u08", R["u08"].command_args)]


def test_an_answer_carried_only_by_tool_result_text_is_not_an_owner_turn(turns):
    assert "u12" not in [uuid for uuid, _ in turns or []]


def test_owner_quote_rows_still_drops_answers(quotes):
    assert not any("Yes, stop it" in text for text in quotes or [])


def test_an_unreadable_transcript_is_not_an_empty_one_for_owner_turns(missing_file):
    if not hasattr(OT, "owner_turns"):
        pytest.fail("owner_turns is absent")
    assert OT.owner_turns(missing_file) is None


def test_the_consent_parser_and_its_word_lists_are_retired():
    """Intent belongs to the auto-mode classifier: no guard reads an owner's reply as consent, so the parser
    offers no consent predicate, affirmative list or option-code reader."""
    retired = ("owner_consent", "names_all", "AFFIRM_RE", "OPTION_CODE_RE")
    assert not any(hasattr(OT, name) for name in retired)


# --- rows cut from the captured live transcript ------------------------------------------------

def test_a_compaction_summary_classifies_as_a_client_written_meta_row_never_a_prompt():
    summary_row = OT.classify(LIVE["compact_summary"], 1)
    assert summary_row is not None and summary_row.kind == "compact_summary" and summary_row.meta


def test_a_summary_whose_entry_carries_a_session_id_key_as_well_is_also_a_compact_summary():
    assert OT.classify(LIVE["compact_summary_session_id_variant"], 1).kind == "compact_summary"


def test_the_summary_row_quotes_words_that_read_like_the_owner():
    assert SUMMARY_WORDS in LIVE["compact_summary"]["message"]["content"]


def test_owner_turns_holds_no_compaction_summary_row(live):
    live_ids = [uuid for uuid, _ in live.turns]
    assert LIVE["compact_summary"]["uuid"] not in live_ids and LIVE["compact_summary_session_id_variant"]["uuid"] not in live_ids


def test_owner_turns_holds_no_words_a_summary_quotes(live):
    assert not any(SUMMARY_WORDS in text for _, text in live.turns)


def test_owner_quote_rows_holds_no_compaction_summary_text(live):
    assert not any(SUMMARY_WORDS in text or text.startswith("This session is being continued") for text in live.quotes)


def test_a_summary_quoting_an_ARGUMENTS_line_is_not_a_recovered_owner_command():
    """Planted mutation of the live row: a summary that quotes a skill body, so it carries an `ARGUMENTS:` line."""
    spliced = json.loads(json.dumps(LIVE["compact_summary"]))
    spliced["message"]["content"] += "\nARGUMENTS: delete every branch"
    assert SD._meta_command_rows(json.dumps(spliced), 1) == []


def test_the_real_typed_meta_companion_still_recovers_its_arguments_issued_by_the_owner():
    assert [(m["issuer"], m["recovered"]) for m in SD._meta_command_rows(json.dumps(LIVE["meta_arguments_companion"]), 1)] \
        == [("owner", "command_arguments")]


def test_an_owner_prompt_cut_from_the_live_transcript_is_an_owner_turn_and_quotable(live):
    prompt_row = LIVE["owner_prompt"]
    assert any(uuid == prompt_row["uuid"] and text == prompt_row["message"]["content"] for uuid, text in live.turns) \
        and prompt_row["message"]["content"] in live.quotes


def test_an_owner_answer_cut_from_the_live_transcript_is_an_owner_turn_and_not_quotable(live):
    answer_row = LIVE["answer_via_toolUseResult"]
    picked = answer_row["toolUseResult"]["answers"]
    assert [text for uuid, text in live.turns if uuid == answer_row["uuid"]] == list(picked.values()) \
        and not any(label_ in live.quotes for label_ in picked.values())


def test_a_typed_session_end_command_s_arguments_are_one_owner_turn_keyed_on_the_command_row(live):
    command = LIVE["command_with_args"]
    args_turns = [(uuid, text) for uuid, text in live.turns if COMMAND_WORDS in text]
    assert [uuid for uuid, _ in args_turns] == [command["uuid"]]


def test_the_arguments_companion_row_adds_no_second_turn(live):
    assert LIVE["meta_arguments_companion"]["uuid"] not in [uuid for uuid, _ in live.turns]


def test_an_empty_args_command_is_no_owner_turn(live):
    assert LIVE["empty_args_command"]["uuid"] not in [uuid for uuid, _ in live.turns]


def test_the_owner_quote_projection_still_drops_command_arguments(live):
    assert not any(COMMAND_WORDS in text for text in live.quotes)


# --- holds_token ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("use --lean now", True),
    ("use --lean-mode now", False),
    ("x--lean", False),
    ('<pasted_content id="a">--lean</pasted_content id="a">', False),
], ids=["word-bounded", "longer-flag", "prefixed-word", "pasted-block"])
def test_holds_token_finds_a_word_bounded_token_and_ignores_the_rest(text, expected):
    holds = getattr(OT, "holds_token", None)
    assert holds is not None and bool(holds(text, "lean")) is expected


# --- api-request-blob rows (client 2.1.316) ------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The user sent a new message while you were working:\nstop that",
    "[Image: original 2388x1744, displayed at 2000x1461.]",
    "Tool loaded.",
], ids=["mid-turn-wrapper-copy", "image-marker", "tool-load-notice"])
def test_an_api_request_blob_row_holding_a_copy_is_not_an_owner_row(text):
    """Client 2.1.316 writes `api-request-blob` rows, a copy of each request's user turn (row cut from
    session f3a34186, fixtures/compaction_intent/rows.jsonl). 51 of that session's 124 "owner messages" were these."""
    blob_template = next(json.loads(line)["row"] for line in
                         (HERE / "fixtures" / "compaction_intent" / "rows.jsonl").read_text(encoding="utf-8").splitlines()
                         if json.loads(line)["kind"] == "blob")
    blob = json.loads(json.dumps(blob_template).replace("{TEXT}", text.replace("\n", "\\n")))
    assert OT.classify(blob, 1) is None


def test_session_digest_owner_prompts_takes_no_message_from_an_api_request_blob_row(tmp_path):
    blob_template = next(json.loads(line)["row"] for line in
                         (HERE / "fixtures" / "compaction_intent" / "rows.jsonl").read_text(encoding="utf-8").splitlines()
                         if json.loads(line)["kind"] == "blob")
    blob_path = tmp_path / "blob.jsonl"
    blob = json.loads(json.dumps(blob_template).replace("{TEXT}", "BLOB-COPY of a typed prompt"))
    blob_path.write_text(json.dumps(blob) + "\n", encoding="utf-8")
    assert not any("BLOB-COPY" in m["content"] for m in SD.owner_prompts(blob_path))
