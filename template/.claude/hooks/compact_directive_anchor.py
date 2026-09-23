#!/usr/bin/env python3
"""SessionStart(compact) — re-inject the owner's own messages after a compaction.

The compaction summary is model-written. Across several compactions the owner's directive survives
only as a paraphrase, and the model cannot see what the paraphrase dropped (2026-09-14, session
3259384b: the owner restated the directive right after two compactions). This hook re-injects the
owner's messages, oldest first with send times: typed prompts, command arguments and question
answers, read through `session_digest.owner_prompts`, the digest's own definition.

The hook supplies evidence; it never decides which messages still hold. The injection frames them as
a record of what the owner said and when, each bound to the scope it was given, and leaves the model
to reconcile them against the summary, which records what happened since. Selection is structural,
never a keyword judgment: the filter drops interrupt markers, bare slash commands and client
session-control commands such as `/model fable`. A question answer keeps the owner's words; its
question text is shortened and the tool's trailing instruction is removed. The digest's public
`is_substantive_owner_prompt()` helper owns the filter so compact recovery and digest selection stay in parity.

The whole output has a byte cap, TOTAL_BYTES, under Claude Code's 10,000-character hook output limit
(past it the client swaps the text for a file preview). The first HEAD_WHOLE messages (the directive)
and the newest TAIL_WHOLE (current steering) share the larger clip, never above MESSAGE_CEILING; the
middle shares what is left, never more than the ends. A clipped message keeps its opening and its closing
words, since an owner often pastes material first and states the ask last. A clipped answer gets at least
ANSWER_FLOOR_CHARS per answer, shortens its questions before its answers, and water-fills the room across
its answers. Middle messages are omitted only when FLOOR_CHARS each does not fit, newest middle kept
first. Clipped and omitted IDs are named with a command that prints only the requested messages
(`--show <file> <ID>...`), so recall costs one message, never the whole uncapped file. The uncapped
text goes to `logs/compact_directives_<sid8>.md`, one file per session, pruned after 30 days. An owner
message naming another session whose transcript sits beside this one gets a pointer to that session's
digest, for the root goal it set.

After the messages, the session's active task record (`tools/task_record.py`, written by the parent at
phase boundaries) is re-injected as one bounded block that shares TOTAL_BYTES with the messages. A
session with no record gets one line naming the command that opens one instead: a drive long enough
to compact is a drive that wanted the record.

A sidecar child is skipped: `sidecar_recompact_reprompt.py` re-injects its brief. A payload that is
not a JSON object is a silent no-op. Any read failure prints one line naming the recovery command;
the hook never blocks.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

TOTAL_BYTES = 9500
MESSAGE_CEILING = 2000
FLOOR_CHARS = 160
QUESTION_CHARS = 100
QUESTION_MIN_CHARS = 24
ANSWER_FLOOR_CHARS = 110
TAIL_SHARE = 0.4
HEAD_WHOLE = 3
TAIL_WHOLE = 3
LISTED_IDS = 10
REFERENCED_SESSIONS = 3
PRUNE_AFTER_SEC = 30 * 86400
TAG = "[owner-directives]"
ANSWER_PREFIX = "(answer) The user answered: "
ANSWER_TRAILER = re.compile(r"\.?\s*Read the answers carefully\b.*\Z", re.S)
ANSWER_PAIR = re.compile(r'"(.*?)"="(.*?)"(?=, "|\Z)', re.S)
SESSION_REF = re.compile(r"\b[0-9a-f]{8}\b")
FULL_HEADING = re.compile(r"(?m)^## (U\d+) · [^\n]*\n\n")

HERE = Path(__file__).resolve().parent


def _digest():
    for directory in (HERE, HERE.parent / "tools"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    import session_digest
    return session_digest


def keep(message: dict) -> bool:
    """Reuse session_digest's public substantive-owner-prompt policy."""
    return _digest().is_substantive_owner_prompt(message)


def clip(text: str, limit: int) -> str:
    """At most `limit` chars, marker included, whenever `limit` exceeds the marker: head and tail kept."""
    if len(text) <= limit:
        return text
    room = max(0, limit - len(f" …[+{len(text)} chars]… "))
    tail = int(room * TAIL_SHARE)
    head = text[:room - tail].rstrip()
    end = text[len(text) - tail:].lstrip() if tail else ""
    cut = len(text) - len(head) - len(end)
    return f"{head} …[+{cut} chars]… {end}" if end else f"{head} …[+{cut} chars]"


def join_answer(pairs: list) -> str:
    return ANSWER_PREFIX + ", ".join(f'"{q}"="{a}"' for q, a in pairs)


def shape(content: str) -> tuple:
    """(injected full text, [(short question, answer)] or None): the owner's answers stay verbatim."""
    if not content.startswith(ANSWER_PREFIX):
        return content, None
    body = ANSWER_TRAILER.sub("", content[len(ANSWER_PREFIX):])
    pairs = ANSWER_PAIR.findall(body)
    if not pairs or ", ".join(f'"{q}"="{a}"' for q, a in pairs) != body:
        return ANSWER_PREFIX + body, None
    short = [(q if len(q) <= QUESTION_CHARS else q[:QUESTION_CHARS - 1].rstrip() + "…", a) for q, a in pairs]
    return join_answer(short), short


def largest(fits, low: int, high: int) -> int:
    """The largest value in [low, high] satisfying a monotonic predicate; `low` when none does."""
    while low < high:
        mid = (low + high + 1) // 2
        if fits(mid):
            low = mid
        else:
            high = mid - 1
    return low


def render(shaped: tuple, limit: int) -> str:
    text, pairs = shaped
    if pairs is None:
        return clip(text, limit)
    limit = max(limit, ANSWER_FLOOR_CHARS * len(pairs))
    if len(text) <= limit:
        return text
    stub = max(QUESTION_MIN_CHARS, min(QUESTION_CHARS, limit // len(pairs) // 3))
    pairs = [(q if len(q) <= stub else q[:stub - 1].rstrip() + "…", a) for q, a in pairs]
    per_answer = largest(lambda n: len(join_answer([(q, clip(a, n)) for q, a in pairs])) <= limit,
                         0, max(len(a) for _, a in pairs))
    return clip(join_answer([(q, clip(a, per_answer)) for q, a in pairs]), limit)


def block(message: dict, body: str) -> str:
    sent = message.get("timestamp") or ""
    stamp = sent[5:16].replace("T", " ") if len(sent) >= 16 else ""
    return f"--- U{message.get('index')}{' · ' + stamp if stamp else ''} ---\n{body}\n"


def size(entries: list, level: int) -> int:
    return sum(len(block(m, render(shaped, level)).encode("utf-8")) for m, shaped in entries)


def fit(entries: list, budget: int) -> tuple:
    """(kept [(message, shaped)], omitted [message], levels {id(message): clip}) inside budget bytes."""
    split = max(HEAD_WHOLE, len(entries) - TAIL_WHOLE)
    ends = entries[:HEAD_WHOLE] + entries[split:]
    middle = entries[HEAD_WHOLE:split]
    room = budget - size(ends, FLOOR_CHARS)
    kept_middle = middle
    if size(middle, FLOOR_CHARS) > room:
        kept_middle = []
        for entry in reversed(middle):
            cost = size([entry], FLOOR_CHARS)
            if cost > room:
                break
            kept_middle.append(entry)
            room -= cost
        kept_middle.reverse()
    middle_floor = size(kept_middle, FLOOR_CHARS)
    end_level = largest(lambda level: size(ends, level) + middle_floor <= budget, FLOOR_CHARS, MESSAGE_CEILING)
    end_cost = size(ends, end_level)
    middle_level = largest(lambda level: end_cost + size(kept_middle, level) <= budget, FLOOR_CHARS, end_level)
    kept = entries[:HEAD_WHOLE] + kept_middle + entries[split:]
    levels = {id(m): end_level for m, _ in ends}
    levels.update({id(m): middle_level for m, _ in kept_middle})
    omitted = [m for m, _ in entries if id(m) not in levels]
    return kept, omitted, levels


def note(label: str, messages: list, fetch: str) -> str:
    ids = ", ".join(f"U{m.get('index')}" for m in messages[:LISTED_IDS])
    more = f" and {len(messages) - LISTED_IDS} more" if len(messages) > LISTED_IDS else ""
    return f"{TAG} {label}: {ids}{more}. Fetch one only when you act on it: {fetch}"


def referenced_sessions(messages: list, transcript: Path, sid: str) -> list:
    found = []
    for m in messages:
        for token in SESSION_REF.findall(m["content"]):
            if token == sid[:8] or token in found:
                continue
            try:
                if next(transcript.parent.glob(f"{token}*.jsonl"), None) is not None:
                    found.append(token)
            except OSError:
                continue
            if len(found) == REFERENCED_SESSIONS:
                return found
    return found


def write_full(logs: Path, sid: str, messages: list) -> Path:
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"compact_directives_{sid[:8]}.md"
    body = [f"# Owner messages, session {sid}", "",
            "Written by hooks/compact_directive_anchor.py at compaction; verbatim, oldest first.", ""]
    for m in messages:
        body += [f"## U{m.get('index')} · {m.get('timestamp') or 'no timestamp'}", "", m["content"], ""]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(body))
    cutoff = time.time() - PRUNE_AFTER_SEC
    for old in logs.glob("compact_directives_*.md"):
        try:
            if old != path and old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return path


def show(args: list) -> int:
    """`--show <file> <ID>...`: print only the requested owner messages from an uncapped file."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if len(args) < 2:
        print("usage: compact_directive_anchor.py --show logs/compact_directives_<sid8>.md U<id> [U<id>...]",
              file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_absolute():
        path = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()) / path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {args[0]}: {exc}", file=sys.stderr)
        return 1
    parts = FULL_HEADING.split(text)
    sections = {parts[i]: parts[i + 1].rstrip("\n") for i in range(1, len(parts) - 1, 2)}
    wanted = [a if a.startswith("U") else f"U{a}" for a in args[1:]]
    for ident in wanted:
        if ident in sections:
            print(f"--- {ident} ---\n{sections[ident]}")
    missing = [ident for ident in wanted if ident not in sections]
    if missing:
        print(f"no message {', '.join(missing)} in {args[0]}", file=sys.stderr)
        return 1
    return 0


def task_block(sid: str, project: Path) -> str:
    """The session's active task record (tools/task_record.py) as its bounded block; '' when none.
    Read-only here: the parent writes the record, this hook only re-injects it after a compaction."""
    try:
        tools = str(HERE.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import task_record
        if not os.environ.get("HARNESS_TASK_RECORD_DIR"):
            os.environ["HARNESS_TASK_RECORD_DIR"] = str(project / ".claude" / "logs" / "tasks")
        rec = task_record.active(sid)
        if rec:
            return task_record.render_block(rec["task_id"])
        return (f"{TAG} No task record (orchestration \u00a710). A drive that outlives one context "
                f"window keeps its state in one: `task_record.py open <id> --session {sid} --title <t>`.")
    except Exception:
        return ""


def main() -> int:
    if sys.argv[1:2] == ["--show"]:
        return show(sys.argv[2:])
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict) or payload.get("source") not in (None, "compact"):
        return 0
    if os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip():
        return 0
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    transcript = Path(payload.get("transcript_path") or "")
    sid = payload.get("session_id") or transcript.stem
    try:
        sd = _digest()
        messages = [m for m in sd.owner_prompts(transcript) if keep(m)]
    except Exception as exc:
        print(f"{TAG} context was compacted, but this hook could not read the transcript "
              f"({type(exc).__name__}: {exc}). Recover the owner's messages before continuing: "
              f"python3 .claude/tools/session_digest.py --session {sid[:8]} --handoff")
        return 0
    if not messages:
        return 0
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    try:
        full = write_full(project / "logs", sid, messages)
        where = f"logs/{full.name}"
        fetch = f"python3 .claude/hooks/compact_directive_anchor.py --show {where} <ID>"
    except OSError as exc:
        where = f"unavailable ({exc})"
        fetch = (f"python3 .claude/tools/session_digest.py --session {sid[:8]} --handoff (writes "
                 f"logs/session_digest_{sid[:8]}.json), then session_digest.py --digest-file "
                 f"logs/session_digest_{sid[:8]}.json --select <ID>")

    lines = [f"{TAG} Context was compacted. Below are the owner's own messages from this session, oldest first, "
             f"with send times: typed prompts, command arguments and question answers (answer wording kept; "
             f"question text shortened). They are a record of what the owner said and when, not a list of open "
             f"orders. Each message holds only for the scope it was given; a step, a question or a temporary "
             f"routing choice may already be done or replaced. The summary above records what happened since, "
             f"so use these messages to catch what it dropped or distorted; its Owner directives section gives "
             f"each ID a status (holds, done, replaced, scoped, unclear) to check against these words. On the same point, "
             f"a later message "
             f"replaces an earlier one. If you cannot tell whether a message still holds, ask the owner. "
             f"Uncapped text: {where}"]
    refs = referenced_sessions(messages, transcript, sid)
    if refs:
        lines.append(f"{TAG} These messages name other sessions. Read their owner messages for the root goal they "
                     f"set; their later messages steered that session and bind here only if restated: "
                     + "; ".join(f"python3 .claude/tools/session_digest.py --session {r} --handoff" for r in refs))
    worst_note = note("clipped for injection", [{"index": 10 ** 7}] * LISTED_IDS, fetch) + " and 9999999 more"
    reserve = 2 * (len(worst_note.encode("utf-8")) + 1)
    # The task record's block shares TOTAL_BYTES with the owner messages: the messages are clipped
    # around it, so the whole output stays under the client's hook-output limit.
    record_block = task_block(sid, project)
    budget = (TOTAL_BYTES - sum(len(line.encode("utf-8")) + 1 for line in lines) - reserve
              - (len(record_block.encode("utf-8")) + 2 if record_block else 0))
    entries = [(m, shape(m["content"])) for m in messages]
    kept, omitted, levels = fit(entries, budget)
    clipped = [m for m, shaped in kept if render(shaped, levels[id(m)]) != shaped[0]]

    for line in lines:
        print(line)
    for message, shaped in kept:
        sys.stdout.write(block(message, render(shaped, levels[id(message)])))
    if clipped:
        print(note("clipped for injection", clipped, fetch))
    if omitted:
        print(note("omitted for budget", omitted, fetch))
    if record_block:
        print(record_block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
