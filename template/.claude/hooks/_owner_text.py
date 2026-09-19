#!/usr/bin/env python3
"""One parser for owner rows in a Claude Code transcript; each consumer keeps its own projection.

`classify(entry, index, raw=None)` returns an `OwnerRow` for every user-role row whose `message` is an
object, and None for anything else. The row is a superset: it drops nothing a consumer keeps, and
policy (which rows count) stays with the consumer. Consent must stay strict; the owner-message
record must stay complete.

Projection table (shape x consumer). `test_owner_text.py` parses this table and runs one fixture
through all three consumers, so a projection change that is not written here fails the proof.

| shape | kill_guard | harness_growth_guard | digest |
|---|---|---|---|
| prompt | text | turn | text |
| meta | drop | no turn | drop |
| meta ARGUMENTS | drop | no turn | label goal |
| sidechain | drop | no turn | drop |
| injection-prefixed | text | turn | drop |
| envelope-only | drop | no turn | drop |
| agent-message | drop | no turn | drop |
| command with args | args | turn | name args |
| empty-args command | drop | no turn | name args |
| answer via toolUseResult | pairs | no turn | answer |
| answer via tool_result text | drop | no turn | answer |
| tool_result with text blocks | drop | no turn | text |

Cells:
- kill_guard (`_consent_text`, `_answer_pairs`): `text` is `OwnerRow.text`; `args` is `command_args`, used
  whenever the text contains `<command-name>`; `pairs` is `pairs`, the strict toolUseResult source.
  Meta, sidechain and tool_result rows never give consent text.
- harness_growth_guard (`_starts_turn`): `turn` means the row can start a turn, keyed on `uuid`, so two
  identical prompt texts are two turns.
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
`cross-session-message`); `injection-prefixed` is owner text after a leading `system-reminder`,
`user-prompt-submit-hook` or `local-command-caveat` block; `command` rows open with
`<command-name>` or `<command-message>`.
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
    r"^<(agent-message|cross-session-message|task-notification|local-command-stdout)\b", re.I)
COMMAND_LEAD_RE = re.compile(r"\s*<command-(?:name|message)>")
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
COMMAND_LABEL_RE = re.compile(r"^#{1,3}\s*(/\S+)", re.M)
COMMAND_LABEL_INLINE_RE = re.compile(r"`(/[\w-]+)`")


@dataclass(frozen=True)
class OwnerRow:
    kind: str                 # prompt | command | answer | meta_arguments | tool_result
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


def classify(entry, index, raw=None):
    """The typed owner row for one parsed transcript entry, or None when it is not a user row."""
    if not isinstance(entry, dict):
        return None
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

    meta = bool(entry.get("isMeta"))
    arguments = next((found for found in map(meta_arguments, blocks) if found), None) if meta else None
    pairs = _pairs(entry, message, tool_results)
    if arguments:
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
        injection_prefixed=injection_prefixed, envelope=envelope.group(1).lower() if envelope else None,
        sidechain=bool(entry.get("isSidechain")), meta=meta,
    )
