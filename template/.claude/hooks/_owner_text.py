#!/usr/bin/env python3
"""One parser for owner rows in a Claude Code transcript; each consumer keeps its own projection.

`classify(entry, index, raw=None)` returns an `OwnerRow` for every user-role row whose `message` is an
object, and None for anything else. The row is a superset: it drops nothing a consumer keeps, and
policy (which rows count) stays with the consumer. Consent must stay strict; the owner-message
record must stay complete.

Projection table (shape x consumer). `test_owner_text.py` parses this table and runs one fixture
through all four consumers, so a projection change that is not written here fails the proof.

| shape | kill_guard | harness_growth_guard | digest | provider_guard | owner_turns |
|---|---|---|---|---|---|
| prompt | text | turn | text | text | text |
| queued_prompt (mid-turn) | text | no turn | text | text | text |
| meta | drop | no turn | drop | drop | drop |
| meta ARGUMENTS | drop | no turn | label goal | drop | drop |
| sidechain | drop | no turn | drop | drop | drop |
| injection-prefixed | text | turn | drop | text | text |
| envelope-only | drop | no turn | drop | drop | drop |
| agent-message | drop | no turn | drop | drop | drop |
| cross-session message | drop | no turn | drop | drop | drop |
| command with args | args | turn | name args | drop | args |
| empty-args command | drop | no turn | name args | drop | drop |
| answer via toolUseResult | pairs | no turn | answer | drop | label |
| answer via tool_result text | drop | no turn | answer | drop | drop |
| tool_result with text blocks | drop | no turn | text | drop | drop |
| compact summary | drop | no turn | drop | drop | drop |

Cells:
- kill_guard (`_consent_text`, `_answer_pairs`): `text` is `OwnerRow.text`; `args` is `command_args`, used
  whenever the text contains `<command-name>`; `pairs` is `pairs`, the strict toolUseResult source.
  Meta, sidechain and tool_result rows never give consent text.
- git_guardrails (`_owner_approved_discard`): kill_guard's `pairs` cell for answers plus the provider_guard
  `text` cell; the latest such row decides, so a later owner prompt revokes an answer's consent.
- harness_growth_guard (`_starts_turn`): `turn` means the row can start a turn, keyed on `uuid`, so two
  identical prompt texts are two turns.
- provider_guard (`owner_quote_text`): `text` is the row text a `hooks/workflow_provider_guard.py`
  `owner:` quote is verified against -- the owner speaking. `drop` is every other row: harness
  plumbing (meta, command, envelope, injection *into* the transcript), a peer's cross-session or
  agent message, and tool results. A quote found only in a dropped row does not lift a deny, so a
  peer quoting the owner's words cannot name the transport.
- owner_turns (`owner_turn_text`): the record `tools/verify_claims.py` owner items read, to bind a
  recorded owner quote to its turn's uuid. `text` is the provider_guard projection; `label` is an
  answer's picked labels, one per line (`owner_answer_text`); `args` is the arguments the owner typed
  after a slash command (`owner_command_text`). The `ARGUMENTS:` meta companion of a typed command
  repeats the same words and a model's `Skill` call leaves its own meta row, so both stay `drop`.
  `tools/task_record.py::_owner_turns` reads the same projection for task conditions.
- digest (`TranscriptSummaryBuilder._process_user_message` with `full_evidence=True`, plus
  `session_digest.recover_meta_command_prompts` and `recover_question_answers`, merged by
  `session_digest.owner_prompts`): `text` is the row's text blocks joined with a space; `name args` is
  `"<command_name> <command_args>"` stripped; `label goal` is `"<command_name> <command_args>"` from
  `ARGUMENTS:`; `answer` is `"(answer) <result text>"` for a non-error tool_result whose id belongs to an
  earlier AskUserQuestion call. The bounded builder (`full_evidence=False`) also drops empty-args
  commands, rows under 10 characters and any row opening with a tag.

D6 changed these cells: harness_growth_guard's sidechain, envelope-only, agent-message and
empty-args command rows (turn -> no turn) and injection-prefixed rows (no turn -> turn); the digest's
agent-message rows (text -> drop). D6 also changed two digest renderings on purpose: a command row
that opens with `<command-message>` before `<command-name>`, and a command row with leading
whitespace, now render as `name args` instead of raw tag text (so the bounded builder keeps a
`<command-message>`-first row that carries args). Every other cell is the behavior each consumer had
before.

Shapes: `envelope-only` is a runtime envelope (`task-notification`, `local-command-stdout`,
`cross-session-message`, or the `[Cross-session ...]` idle notice); `injection-prefixed` is owner text after a leading `system-reminder`,
`user-prompt-submit-hook` or `local-command-caveat` block; `command` rows open with
`<command-name>` or `<command-message>`; `queued_prompt` is a message the owner sent MID-TURN,
recorded as an `attachment` of type `queued_command` rather than a user row (see
`queued_owner_prompt`); `compact summary` is the recap the client writes after a compaction
(`isCompactSummary`, "This session is being continued..."). It is never the owner, so it classifies as
`meta` with its own kind and every consumer that drops `meta` rows drops it: a paraphrase inside a summary
must not verify as the owner's words or read as consent.
"""
import hashlib
import json
import re
from dataclasses import dataclass

LEADING_INJECTION_RE = re.compile(
    r"^\s*<(?P<tag>system-reminder|user-prompt-submit-hook|local-command-caveat)"
    r"(?:\s+[^>]*)?>.*?</(?P=tag)>\s*",
    re.I | re.S,
)
ENVELOPE_RE = re.compile(
    r"^(?:<(agent-message|cross-session-message|task-notification|local-command-stdout)\b"
    r"|\[(cross-session)\s)", re.I)


def _envelope_name(match):
    return (match.group(1) or match.group(2)).lower()


COMMAND_LEAD_RE = re.compile(r"\s*<command-(?:name|message)>")
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
COMMAND_LABEL_RE = re.compile(r"^#{1,3}\s*(/\S+)", re.M)
COMMAND_LABEL_INLINE_RE = re.compile(r"`(/[\w-]+)`")


@dataclass(frozen=True)
class OwnerRow:
    kind: str                 # prompt | queued_prompt | command | answer | meta_arguments | compact_summary | tool_result
    uuid: str                 # transcript uuid, else sha1 of the raw line (12 hex)
    index: int                # caller's line number
    text: str                 # text blocks joined, stripped, leading injections removed
    blocks: tuple             # the raw text values: the string content, or each text block
    command_name: str | None  # command name, or the `ARGUMENTS:` label on a meta_arguments row
    command_args: str | None  # command args ("" when absent), or the `ARGUMENTS:` goal
    pairs: tuple | None       # ((question, label), ...) from toolUseResult, all questions answered
    tool_results: tuple       # ((tool_use_id, result text, is_error), ...)
    injection_prefixed: bool
    envelope: str | None      # runtime envelope tag the text opens with
    sidechain: bool
    meta: bool


def meta_arguments(text):
    """(label, goal) for a command body carrying typed `ARGUMENTS:` text, else None."""
    if not isinstance(text, str) or "\nARGUMENTS:" not in text:
        return None
    head, goal = text.split("ARGUMENTS:", 1)
    goal = goal.strip()
    if not goal:
        return None
    m = COMMAND_LABEL_RE.search(head) or COMMAND_LABEL_INLINE_RE.search(text[:600])
    return (m.group(1) if m else "(command)"), goal


def _result_text(body):
    if isinstance(body, str):
        return body
    if not isinstance(body, list):
        return ""
    return "\n".join(str(b.get("text") or "") for b in body if isinstance(b, dict) and b.get("type") == "text")


def _pairs(entry, message, tool_results):
    if message.get("role") != "user" or not tool_results:
        return None
    result = entry.get("toolUseResult")
    if not isinstance(result, dict):
        return None
    questions, answers = result.get("questions"), result.get("answers")
    if not isinstance(questions, list) or not isinstance(answers, dict):
        return None
    pairs = []
    for item in questions:
        question = item.get("question") if isinstance(item, dict) else None
        label = answers.get(question) if isinstance(question, str) else None
        if isinstance(label, list) and label and all(isinstance(x, str) for x in label):
            label = ",".join(label)  # a multiSelect answer stores its picked labels as a list
        if not question or not isinstance(label, str) or not label.strip():
            return None
        pairs.append((question, label))
    return tuple(pairs) or None


def _identity(entry, raw):
    uuid = entry.get("uuid")
    if isinstance(uuid, str) and uuid:
        return uuid
    if raw is None:
        raw = json.dumps(entry, sort_keys=True)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def queued_owner_prompt(entry):
    """The text of a message the owner sent MID-TURN, or None.

    A message sent while the model was working is recorded only as a `queue-operation` pair
    (enqueue, then remove with `reason: absorbed_mid_turn`) plus an `attachment` row of type
    `queued_command` whose `prompt` is the text. None of those is a user-role row with a `message`
    object, so every mid-turn owner message was invisible to `classify` -- which meant a stop order
    given mid-turn could never authorize a TaskStop, leaving a hung background task unstoppable
    (2026-09-18: two hung sidecars survived repeated stop orders for this reason)."""
    if not isinstance(entry, dict) or entry.get("type") != "attachment":
        return None
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    prompt = attachment.get("prompt")
    return prompt if isinstance(prompt, str) and prompt.strip() else None


def owner_quote_text(row):
    """The text a provider guard `owner:` quote is verified against, or None: the projection's last
    column. A statement about routing is the owner speaking: a prompt, a mid-turn queued prompt, or
    a prompt behind a leading injection block. Meta, sidechain, envelope (cross-session and agent
    messages), command and tool-result rows are plumbing, a peer, or a recorded action -- a quote
    found only there is not the owner naming a transport."""
    if row is None or row.meta or row.sidechain or row.envelope or row.tool_results:
        return None
    if row.kind not in ("prompt", "queued_prompt") or not row.text:
        return None
    return row.text


def owner_answer_text(row):
    """The owner's picked labels of an AskUserQuestion answer (one per line), or None: the words an answer
    carries are the owner's, the question beside them is the assistant's. Only the strict `toolUseResult`
    pairing counts; an answer visible only as tool_result text cannot be told from any other tool output."""
    if row is None or row.meta or row.sidechain or row.kind != "answer" or not row.pairs:
        return None
    return "\n".join(label for _, label in row.pairs)


def owner_command_text(row):
    """The arguments the owner typed after a slash command (`/session_end <args>`), or None. The command row is the
    owner's typed input. Its `ARGUMENTS:` meta companion repeats the same words and a model's `Skill` call leaves its
    own meta row, so neither counts; a command typed without arguments carries no words."""
    if row is None or row.meta or row.sidechain or row.envelope or row.tool_results or row.kind != "command":
        return None
    return row.command_args or None


def owner_turn_text(row):
    """What `owner_turns` takes from a row, or None: prompt text, an answer's picked labels, or a command's typed
    arguments."""
    return owner_quote_text(row) or owner_answer_text(row) or owner_command_text(row)


def _rows(path):
    """Every classifiable row of a transcript file, in order; None when it cannot be read. A malformed row is
    not an owner statement, and never a crash."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    rows = []
    for index, line in enumerate(lines):
        try:
            rows.append(classify(json.loads(line), index + 1, raw=line))
        except Exception:
            continue
    return rows


def _turns(path, project):
    rows = _rows(path)
    if rows is None:
        return None
    out = []
    for row in rows:
        text = project(row)
        if text:
            out.append((row.uuid, text))
    return out


def owner_turns(path):
    """`(OwnerRow.uuid, text)` for every turn the owner spoke, in order (`owner_turn_text`): each `owner_quote_text`
    row, each AskUserQuestion answer (`owner_answer_text`) and each command's typed arguments
    (`owner_command_text`). None when the file cannot be read.

    An unreadable transcript must not read like one that carries nothing: both deny, but a reader of
    this proof has to be able to tell the two apart. `tools/verify_claims.py` reads the owner through
    this; the provider guard reads `owner_quote_rows`, which drops answers and command arguments.
    """
    return _turns(path, owner_turn_text)


def owner_answer_pairs(path):
    """`(OwnerRow.uuid, ((question, label), ...))` for every AskUserQuestion answer of a transcript file, in
    order; None when it cannot be read. A picked label means what the question asked, so a consumer that
    verifies a label also checks its question."""
    rows = _rows(path)
    if rows is None:
        return None
    return [(row.uuid, tuple(row.pairs)) for row in rows if owner_answer_text(row)]


AFFIRM_RE = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|approved?|confirm(?:ed)?)"
                       r"(?:\W+(?:please|pls|thanks|thank you|do it|go ahead|now))*\W*$", re.I)


OPTION_CODE_RE = re.compile(r"^\W*(O\d+)\W*$", re.I)


def _option_lines(asked, code=r"O\d+"):
    opens = re.compile(r"^[\s>*_#-]*%s\b" % code, re.I)
    return [line for line in (asked or "").splitlines() if opens.match(line)]


def _picked_line(reply, asked):
    """The line of the agent's packet `asked` that a code-only `reply` ("O1") opens, or None."""
    code = OPTION_CODE_RE.match(reply or "")
    return next(iter(_option_lines(asked, re.escape(code.group(1)))), None) if code else None


def _picked_option(reply, asked):
    """The label of the option a code-only `reply` ("O1") picks in the agent's packet `asked`, or None: the bold span
    of the line the code opens, else that line's first sentence. Its explanation is not the option, and a "no" in it
    would read as a keep word."""
    line = _picked_line(reply, asked)
    if line is None:
        return None
    bold = re.search(r"\*\*(.+?)\*\*", line)
    return bold.group(1) if bold else re.split(r"(?<=[.?!])\s", line, maxsplit=1)[0]


def _option_concerns(reply, packet, targets):
    """True when the option `reply` picks concerns every target: its line names them all, or the packet names them
    and no option line names any one (a target named only in the header). An option line naming a target makes the
    options target-specific, so a pick of another target's option never approves this one."""
    if names_all(_picked_line(reply, packet), targets):
        return True
    return names_all(packet, targets) and not any(
        names_all(line, [t]) for line in _option_lines(packet) for t in targets)


def _last_naming(text, targets):
    """The last sentence of `text` that names every target, or None."""
    named = [s for s in re.split(r"(?<=[.?!])\s+|\n+", text or "") if names_all(s, targets)]
    return named[-1] if named else None


def _asks(sentence):
    return (sentence or "").rstrip().rstrip("`*_\"')]").endswith("?")


def _assistant_text(entry):
    if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
        return ""
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def names_all(text, targets):
    """True when `text` names every target as a whole token (a longer name or a path continuing it does not
    count); backslashes read as forward slashes, case folded on Windows-style paths."""
    text = (text or "").replace("\\", "/")
    return bool(targets) and all(
        re.search(r"(?<![\w./-])" + re.escape(t.replace("\\", "/")) + r"(?![\w/-]|\.\w)", text, re.I)
        for t in targets)


def owner_consent(lines, targets, action_re, decline_re):
    """True when the owner's LATEST input approves `action_re` on every one of `targets`:
    - an AskUserQuestion answer whose question names every target and whose picked label matches `action_re`;
    - a typed statement (not a question) naming every target with an action verb; or
    - a typed reply that is only an affirmative ("yes", "go ahead", "yes please"; a condition, question or
      further instruction voids it) to a preceding assistant message (the last one before the reply) whose last
      sentence naming every target is a question carrying an action verb; or
    - a typed reply that is only an option code ("O1") picking, from the latest assistant message since the owner's
      previous input that labels that code, an option whose label line carries an action verb, where that line names
      every target, or the message does and no option line names one.
    `decline_re` anywhere in the owner's words refuses. Any later owner input replaces the earlier one, so a
    later "wait" revokes. Sidechain, meta, envelope and tool rows are never the owner. `lines` is the
    transcript's raw JSON lines in order."""
    latest, asked, since, said = None, "", [], []
    for index, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        spoken = _assistant_text(entry)
        if spoken:
            said.append(spoken)
            continue
        try:
            row = classify(entry, index, raw=line)
        except Exception:
            continue
        if row is None or row.meta or row.sidechain:
            continue
        if row.pairs:
            latest, asked, since, said = ("answer", row.pairs), (said[-1] if said else ""), said, []
        elif owner_quote_text(row):
            latest, asked, since, said = ("text", row.text), (said[-1] if said else ""), said, []
    if latest is None:
        return False
    kind, value = latest
    if kind == "answer":
        return any(names_all(q, targets) and action_re.search(label) and not decline_re.search(label)
                   for q, label in value)
    if decline_re.search(value):
        return False
    if names_all(value, targets) and action_re.search(value) and not _asks(value) and not _asks(_last_naming(value, targets)):
        return True
    for packet in reversed(since):
        option = _picked_option(value, packet)
        if option is not None:
            return _option_concerns(value, packet, targets) and bool(action_re.search(option)) and not decline_re.search(option)
    question = _last_naming(asked, targets)
    return bool(AFFIRM_RE.search(value)) and _asks(question) and bool(action_re.search(question))


PASTED_RE = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content\b[^>]*>", re.S)


def strip_pasted(text):
    """`text` with every pasted block blanked, so only the owner's own words remain."""
    return PASTED_RE.sub(" ", text)


def holds_token(text, name):
    """True when `text` holds the literal word-bounded token `--<name>` outside a pasted block."""
    token = re.compile(r"(?<![\w-])--" + re.escape(name) + r"(?![\w-])")
    return bool(token.search(strip_pasted(text)))


def owner_quote_rows(path):
    """`owner_quote_text` for every owner row of a transcript file, in order; None when it cannot be read."""
    turns = _turns(path, owner_quote_text)
    return None if turns is None else [text for _, text in turns]


def classify(entry, index, raw=None):
    """The typed owner row for one parsed transcript entry, or None when it is not a user row."""
    if not isinstance(entry, dict):
        return None
    queued = queued_owner_prompt(entry)
    if queued is not None:
        # Its own kind, so each consumer declares a cell rather than inheriting the `prompt` one:
        # a mid-turn message carries owner text (kill_guard, digest) but does NOT start a turn.
        # The runtime queues its own events the same way (a background task's notification carries
        # `commandMode: task-notification`); those are envelopes, never the owner speaking.
        attachment = entry.get("attachment") or {}
        mode = attachment.get("commandMode") or (attachment.get("origin") or {}).get("kind")
        opens = ENVELOPE_RE.match(queued.strip())
        envelope = (_envelope_name(opens) if opens
                    else (str(mode).lower() if mode and str(mode).lower() != "prompt" else None))
        return OwnerRow(
            kind="queued_prompt", uuid=_identity(entry, raw), index=index, text=queued.strip(),
            blocks=(queued,), command_name=None, command_args=None, pairs=None, tool_results=(),
            injection_prefixed=False, envelope=envelope,
            sidechain=bool(entry.get("isSidechain")), meta=False,
        )
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    role = entry.get("type") if entry.get("type") in ("user", "assistant") else message.get("role")
    if role != "user":
        return None
    content = message.get("content", entry.get("content", ""))
    blocks, tool_results = (), ()
    if isinstance(content, list):
        tool_results = tuple(
            (block.get("tool_use_id"), _result_text(block.get("content")), bool(block.get("is_error")))
            for block in content if isinstance(block, dict) and block.get("type") == "tool_result")
        blocks = tuple(str(block.get("text") or "")
                       for block in content if isinstance(block, dict) and block.get("type") == "text")
    elif isinstance(content, str):
        blocks = (content,)

    text = "".join(blocks).strip()
    injection_prefixed = False
    while True:
        stripped = LEADING_INJECTION_RE.sub("", text, count=1)
        if stripped == text:
            break
        text, injection_prefixed = stripped.strip(), True

    envelope = ENVELOPE_RE.match(text)
    command_name = command_args = None
    if "<command-name>" in text:
        name, args = COMMAND_NAME_RE.search(text), COMMAND_ARGS_RE.search(text)
        command_name = name.group(1).strip() if name else None
        command_args = args.group(1).strip() if args else ""
    is_command = "<command-name>" in text and COMMAND_LEAD_RE.match(text) is not None

    compact_summary = bool(entry.get("isCompactSummary"))
    meta = bool(entry.get("isMeta")) or compact_summary
    arguments = (next((found for found in map(meta_arguments, blocks) if found), None)
                 if meta and not compact_summary else None)
    pairs = _pairs(entry, message, tool_results)
    if compact_summary:
        kind = "compact_summary"
    elif arguments:
        kind = "meta_arguments"
        command_name, command_args = arguments
    elif is_command:
        kind = "command"
    elif pairs:
        kind = "answer"
    elif tool_results and not text:
        kind = "tool_result"
    else:
        kind = "prompt"
    return OwnerRow(
        kind=kind, uuid=_identity(entry, raw), index=index, text=text, blocks=blocks,
        command_name=command_name, command_args=command_args, pairs=pairs, tool_results=tool_results,
        injection_prefixed=injection_prefixed, envelope=_envelope_name(envelope) if envelope else None,
        sidechain=bool(entry.get("isSidechain")), meta=meta,
    )
