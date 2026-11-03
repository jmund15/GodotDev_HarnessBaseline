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

The hook prints in two parts, each registered as its own SessionStart(compact) command, because Claude Code
caps each hook's output at 10,000 characters on its own (past it the client swaps the text for a file path
and a 2,000-character preview). Each part has its own byte cap, TOTAL_BYTES. `--opening` prints the first
HEAD_WHOLE messages, which set the session's goal, whole at any length, oldest first while they fit
(`fit_opening`; when even the first cannot, it alone prints, clipped); a line after them names each opening
message that did not print whole and tells the session to read it with `--show` before any other step.
The plain run prints the later messages, filled by `fit` in priority order: the newest TAIL_WHOLE (current
steering) at FLOOR_CHARS; middle messages at FLOOR_CHARS, newest first, the rest omitted; then the newest up
to MESSAGE_CEILING and the middle up to the newest's clip. An opening message the first part could not
print joins the middle. A clipped message keeps its opening and
its closing words, since an owner often pastes material first and states the ask last. A clipped answer
gets at least ANSWER_FLOOR_CHARS per answer, shortens its questions before its answers, and water-fills
the room across its answers. One line names the clipped and omitted IDs with a command that prints only
the requested messages (`--show <file> <ID>...`), and tells the session to read one before work it could
govern and each one the summary gives no status. The uncapped text goes to
`logs/compact_directives_<sid8>.md`, one file per session, pruned after 30 days. An owner message naming
another session whose transcript sits beside this one gets a pointer to that session's digest, for the
root goal it set.

A reply that carries no ruling of its own (a question answer, a message no longer than SHORT_REPLY_CHARS, or
one naming a reference code such as `O2` or `S4`: a capital letter and digits; the owner's own `U<n>`
message ids are not one) gets the text it answered
on a line marked `(answering, assistant text)`: an answer gets its chosen options' descriptions, any
other reply the closing words of the assistant message before it, where a packet sits; the client's
synthetic rows are skipped. That text is
the assistant's, labeled apart from the owner's words, and shares each message's clip.

After the messages, the project's document roots outside the repo (`adaptation.json` `doc_roots`)
ride on one `[locations]` line, then the session's active task record (`tools/task_record.py`, written by the parent at
phase boundaries) is re-injected as one bounded block that shares TOTAL_BYTES with the messages. A
session with no record gets one line naming the command that opens one instead: a drive long enough
to compact is a drive that wanted the record. When this session has unrated dispatches, one
`[rating-debt]` line follows the record and shares its budget. That line is optional: its count reads
the session's sidecar records and launch transcript, which grows with the drive, so it runs under
RATING_DEADLINE_SEC measured from hook start and prints nothing when it is not done by then. A client
that cancels the hook at its timeout discards every line, the owner messages included.

`plan` lays both parts out and `whole_ids` names the messages either prints in full. The PreCompact
ledger (`compact_directive_ledger.py`) calls the same `plan` before the compaction, so the summary's holds
lines carry only ID, status and scope for those messages and never duplicate the words printed here.

The intro names what the summary cannot replace: where the summary words or rates a message differently,
the message's own words hold. It asserts nothing about the summary's sections, which depend on whether
the PreCompact ledger ran.

Every transcript pass (owner messages, reply contexts, the rating count's launch scan) resumes from
its `hooks/_transcript_scan.py` cache, so a run reads only what was appended since the last one. Each
run appends its duration, output bytes, scans and payload keys to `logs/compaction_hooks.jsonl`
(`log_run`, shared with compact_directive_ledger.py).

A sidecar child is skipped: `sidecar_recompact_reprompt.py` re-injects its brief. A payload carrying
`agent_id` is a subagent's and is skipped with a logged row. The hook runs for every other compaction,
because a SessionStart payload carries no trigger to judge a subagent by, and a parent that loses this
injection loses its goal. A payload that is not a JSON object is a silent no-op. Any read failure prints
one line naming the recovery command; the hook never blocks.
"""
import contextlib
import io
import json
import os
import re
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

TOTAL_BYTES = 9500
MESSAGE_CEILING = 2000
FLOOR_CHARS = 160
SHORT_REPLY_CHARS = 160
QUESTION_CHARS = 100
QUESTION_MIN_CHARS = 24
ANSWER_FLOOR_CHARS = 110
TAIL_SHARE = 0.4
HEAD_WHOLE = 3
TAIL_WHOLE = 3
LISTED_IDS = 10
REFERENCED_SESSIONS = 3
PRUNE_AFTER_SEC = 30 * 86400
RATING_DEADLINE_SEC = 20  # the registered hook timeout is 30 s; only fitting and printing follow the line
TAG = "[owner-directives]"
ANSWER_PREFIX = "(answer) The user answered: "
ANSWER_TRAILER = re.compile(r"\.?\s*Read the answers carefully\b.*\Z", re.S)
ANSWER_PAIR = re.compile(r'"(.*?)"="(.*?)"(?=, "|\Z)', re.S)
SESSION_REF = re.compile(r"\b[0-9a-f]{8}\b")
FULL_HEADING = re.compile(r"(?m)^## (U\d+) · [^\n]*\n\n")
CODE_REF = re.compile(r"\b(?!U\d+\b)[A-Z]\d+\b")
CONTEXT_LABEL = "(answering, assistant text) "

HERE = Path(__file__).resolve().parent
RUN_LOG = "compaction_hooks.jsonl"


def _scan():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import _transcript_scan
    return _transcript_scan


def log_run(hook: str, payload: dict, started: float, output: str, skipped: str = None) -> None:
    """Append one row per compaction-hook run to logs/compaction_hooks.jsonl (rotated): its duration,
    output bytes, transcript size, the transcript scans it ran and the payload's keys (whether the client
    sent `agent_id`), so an auto-compaction's cost is auditable after the fact. A run that printed nothing
    because it judged the compaction a subagent's logs `skipped`. Never raises."""
    try:
        scans = list(_scan().STATS)
        from _hook_state import append_jsonl_rotating, log_path
        transcript = payload.get("transcript_path") or ""
        size = os.path.getsize(transcript) if transcript and os.path.isfile(transcript) else None
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "hook": hook,
               "session": (payload.get("session_id") or "")[:8], "trigger": payload.get("trigger") or payload.get("source"),
               "duration_s": round(time.monotonic() - started, 3), "output_bytes": len(output.encode("utf-8")),
               "transcript_bytes": size, "scans": scans, "payload_keys": sorted(payload)}
        if skipped:
            row["skipped"] = skipped
        append_jsonl_rotating(log_path(RUN_LOG), [row])
    except Exception:
        pass


def emit(hook: str, payload: dict, started: float, output: str) -> None:
    """Write a compaction hook's whole output to stdout, then log the run."""
    sys.stdout.write(output)
    sys.stdout.flush()
    log_run(hook, payload, started, output)


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


def tail_clip(text: str, limit: int) -> str:
    """At most `limit` chars of `text`'s closing words, marker included: a packet closes its message."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    marker = f"…[+{len(text)} chars] "
    room = max(0, limit - len(marker))
    return f"…[+{len(text) - room} chars] " + text[len(text) - room:].lstrip()


def head_clip(text: str, limit: int) -> str:
    """At most `limit` chars of `text`'s opening words, whitespace collapsed, an ellipsis marking the cut."""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def stamp(timestamp) -> str:
    """`MM-DD HH:MM` of an ISO timestamp; '' when there is none."""
    return timestamp[5:16].replace("T", " ") if isinstance(timestamp, str) and len(timestamp) >= 16 else ""


def shown_assistant_row(row) -> bool:
    """An assistant row the owner could have seen: not a subagent's and not the client's synthetic row."""
    return (isinstance(row, dict) and row.get("type") == "assistant" and not row.get("isSidechain")
            and (row.get("message") or {}).get("model") != "<synthetic>")


def block(message: dict, body: str, level: int = 0, context_cap: int = MESSAGE_CEILING) -> str:
    stamp_text = stamp(message.get("timestamp"))
    context = message.get("context")
    tail = f"{CONTEXT_LABEL}{tail_clip(context, min(level, context_cap))}\n" if context and level else ""
    return f"--- U{message.get('index')}{' · ' + stamp_text if stamp_text else ''} ---\n{body}\n{tail}"


def size(entries: list, level: int, context_cap: int = MESSAGE_CEILING) -> int:
    return sum(len(block(m, render(shaped, level), level, context_cap).encode("utf-8")) for m, shaped in entries)


def _text_blocks(row: dict) -> list:
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return []


def needs_context(message: dict) -> bool:
    """A reply whose own words carry no ruling: an answer, a short reply, or one naming a packet code."""
    text = message.get("content") or ""
    return (text.startswith("(answer)") or len(text) <= SHORT_REPLY_CHARS or bool(CODE_REF.search(text)))


def _reply_scanner(wanted: dict):
    """The reply-context fold for this call's `wanted` messages. Its state records the content of each
    wanted message it reached, so a resumed state built for another wanted set is detected and rebuilt."""
    def feed(state, i, line):
        state["lines"] = i
        message = wanted.get(i)
        if message is not None:
            content = message.get("content") or ""
            state["wanted"][str(i)] = content
            if content.startswith("(answer)"):
                chosen = [f"{label}: {desc}" for label, desc in state["options"].items()
                          if desc and f'="{label}' in content]
                if chosen:
                    state["found"][str(i)] = "; ".join(chosen)
            elif state["last_text"]:
                state["found"][str(i)] = state["last_text"]
        if '"assistant"' not in line:
            return
        try:
            row = json.loads(line)
        except ValueError:
            return
        if not shown_assistant_row(row):
            return
        texts = [t for t in _text_blocks(row) if t.strip()]
        if texts:
            state["last_text"] = texts[-1]
        for b in (row.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                state["options"] = {o.get("label"): o.get("description") or ""
                                    for q in (b.get("input") or {}).get("questions") or []
                                    for o in q.get("options") or [] if isinstance(o, dict) and o.get("label")}
    return _scan().Scanner("reply_contexts", lambda: {"lines": 0, "last_text": "", "options": {}, "found": {},
                                                      "wanted": {}}, feed, sources=[__file__])


def reply_contexts(transcript: Path, messages: list) -> dict:
    """{message index: the assistant text it answered} for each message `needs_context` admits, in one
    pass resumed from the transcript scan cache: an answer gets its chosen options' descriptions, any
    other reply the last assistant text."""
    wanted = {m["index"]: m for m in messages if needs_context(m)}
    if not wanted:
        return {}
    scanner = _reply_scanner(wanted)
    state = _scan().scan(transcript, scanner)
    reached = {str(i): m.get("content") or "" for i, m in wanted.items() if i <= state["lines"]}
    if state["wanted"] != reached:
        state = _scan().scan(transcript, scanner, fresh=True)
    return {int(i): text for i, text in state["found"].items()}


def locations_line(project: Path) -> str:
    """The project's document roots outside the repo, or '' when it names none."""
    try:
        tools = str(HERE.parent / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import adaptation
        roots = [r for r in adaptation.get(str(project / ".claude"), "doc_roots") or [] if isinstance(r, str)]
    except Exception:
        return ""
    return f"[locations] document roots outside the repo: {'; '.join(roots)}" if roots else ""


def _whole(entry) -> int:
    return max(FLOOR_CHARS, len(entry[1][0]))


def fit_opening(entries: list, budget: int) -> tuple:
    """(printed [(message, shaped)], levels {id(message): clip}) of the first HEAD_WHOLE entries inside the
    opening part's budget bytes: oldest first while each prints whole; when even the first cannot, it alone
    prints, clipped to fit."""
    opening = entries[:HEAD_WHOLE]
    printed, levels, cost = [], {}, 0
    for entry in opening:
        whole = size([entry], _whole(entry), FLOOR_CHARS)
        if cost + whole > budget:
            break
        printed.append(entry)
        levels[id(entry[0])] = _whole(entry)
        cost += whole
    if opening and not printed:
        first = opening[0]
        level = largest(lambda n: size([first], n, FLOOR_CHARS) <= budget, FLOOR_CHARS, _whole(first))
        printed, levels = [first], {id(first[0]): level}
    return printed, levels


def fit(entries: list, budget: int) -> tuple:
    """(kept [(message, shaped)], omitted [message], levels {id(message): clip}) inside budget bytes, filled in
    priority order: the newest TAIL_WHOLE at the floor; the middle at the floor, newest first; then the newest
    up to MESSAGE_CEILING; then the middle up to the newest's clip."""
    split = max(0, len(entries) - TAIL_WHOLE)
    middle, tail = entries[:split], entries[split:]
    room = budget - size(tail, FLOOR_CHARS)
    kept_middle = []
    for entry in reversed(middle):
        cost = size([entry], FLOOR_CHARS)
        if cost > room:
            break
        kept_middle.append(entry)
        room -= cost
    kept_middle.reverse()
    middle_floor = size(kept_middle, FLOOR_CHARS)
    tail_level = largest(lambda level: size(tail, level) + middle_floor <= budget, FLOOR_CHARS, MESSAGE_CEILING)
    tail_cost = size(tail, tail_level)
    middle_level = largest(lambda level: tail_cost + size(kept_middle, level) <= budget, FLOOR_CHARS, tail_level)
    kept = kept_middle + tail
    levels = {id(m): tail_level for m, _ in tail}
    levels.update({id(m): middle_level for m, _ in kept_middle})
    omitted = [m for m, _ in entries if id(m) not in levels]
    return kept, omitted, levels


def _listed(label: str, messages: list) -> str:
    ids = ", ".join(f"U{m.get('index')}" for m in messages[:LISTED_IDS])
    more = f" and {len(messages) - LISTED_IDS} more" if len(messages) > LISTED_IDS else ""
    return f"{label}: {ids}{more}"


def note(clipped: list, omitted: list, fetch: str) -> str:
    """One line naming the clipped and the omitted messages and how to read them; '' when there are none."""
    parts = [_listed(label, found) for label, found in (("clipped for injection", clipped),
                                                         ("omitted for budget", omitted)) if found]
    if not parts:
        return ""
    return (f"{TAG} {'; '.join(parts)}. Each may still bind: read one whole before work it could govern, "
            f"and read each one the summary gives no status: {fetch}")


def opening_intro() -> str:
    return (f"{TAG} Context was compacted. The owner's opening messages for this session, verbatim with send times. "
            f"They set its goal; the owner's later messages follow in the other {TAG} block.")


def opening_line(messages: list, where: str) -> str:
    """The required read of the opening messages that could not print whole."""
    ids = " ".join(f"U{m.get('index')}" for m in messages)
    return (f"{TAG} The opening messages {ids} did not fit whole here. Read them whole before any other step: "
            f"python3 .claude/hooks/compact_directive_anchor.py --show {where} {ids}")


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
                f"window keeps its state in one: `task_record.py open <id> --session {sid} --title <t>`, "
                f"then `task_record.py set <id> --next \"<the summary's next step>\" --files <docs it reads>`.")
    except Exception:
        return ""


def _pending_count(sid: str) -> int:
    tools = str(HERE.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from orchestration_metrics import pending_count
    return pending_count(session_id=sid)


def rating_debt_line(sid: str, budget: float = RATING_DEADLINE_SEC) -> str:
    """One line naming this session's unrated dispatches, or '' when none, unknown or not counted
    within `budget` seconds. Compaction is a phase boundary: the model still holds the task record,
    so it can rate what it consumed. HARNESS_PENDING_COUNT stands in for the count in proofs."""
    seam = os.environ.get("HARNESS_PENDING_COUNT")
    try:
        if seam is not None:
            n = int(seam)
        else:
            if budget <= 0:
                return ""
            found = []
            worker = threading.Thread(target=lambda: found.append(_pending_count(sid)), daemon=True)
            worker.start()
            worker.join(budget)
            if not found:
                return ""
            n = found[0]
    except Exception:
        return ""
    return rating_debt_text(n) if n > 0 else ""


def rating_debt_text(n: int) -> str:
    return (f"[rating-debt] {n} unrated dispatches — rate every consumed result you did not accept as-is "
            f"in one --rate-batch call (python3 .claude/tools/orchestration_metrics.py --rate-batch <file|->; "
            f"--rate <label> <outcome> --note <why> for one), then "
            f"--accept-rest for the Workflow jobs you consumed; a sidecar job takes --rate or --rate-batch "
            f"(/orchestration_metrics §Incremental rating).")


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
    started = time.monotonic()
    opening = sys.argv[1:2] == ["--opening"]
    hook = "compact_directive_anchor" + (" --opening" if opening else "")
    if str(payload.get("agent_id") or "").strip():
        log_run(hook, payload, started, "", skipped="subagent")
        return 0
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = _compact(payload, started, opening)
    emit(hook, payload, started, buffer.getvalue())
    return code


def project_dir(payload: dict) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def full_location(sid: str) -> str:
    return f"logs/compact_directives_{sid[:8]}.md"


def fetch_command(where: str) -> str:
    return f"python3 .claude/hooks/compact_directive_anchor.py --show {where} <ID>"


def intro_line(where: str) -> str:
    return (f"{TAG} Context was compacted. Below are the owner's later messages from this session, oldest first, "
            f"with send times: typed prompts, command arguments and question answers (question text shortened); "
            f"the opening messages that set its goal are in their own {TAG} block. They are a record of what the "
            f"owner said and when, not a list of open orders: each holds for the scope it was given, a step or "
            f"question may be done, and on the same point a later message replaces an earlier one. Where the "
            f"summary above words or rates a message differently, its words here hold. If you cannot tell whether "
            f"a message still holds, ask the owner. Uncapped text: {where}")


Injection = namedtuple("Injection", "lines kept omitted levels clipped record_block opening_lines opening "
                                    "opening_levels")


def plan(transcript: Path, sid: str, project: Path, messages: list, where: str, fetch: str,
         started: float = None, rating_line: str = None) -> Injection:
    """Everything both parts print, each laid out inside its own TOTAL_BYTES. The opening part: its intro,
    the opening messages `fit_opening` prints and the required read of the ones that did not fit whole. The
    later part: the intro lines, the kept later messages with their clip levels, the clipped and omitted
    ones, and the record block. `compact_directive_ledger.py` calls it before the compaction with
    `rating_line` standing in for the count, so the ledger and this hook decide from one layout which
    messages print whole (`whole_ids`)."""
    lines = [intro_line(where)]
    refs = referenced_sessions(messages, transcript, sid)
    if refs:
        lines.append(f"{TAG} These messages name other sessions. Read their owner messages for the root goal they "
                     f"set; their later messages steered that session and bind here only if restated: "
                     + "; ".join(f"python3 .claude/tools/session_digest.py --session {r} --handoff" for r in refs))
    worst = [{"index": 10 ** 7}] * (LISTED_IDS + 1)
    reserve = len(note(worst, worst, fetch).replace(" and 1 more", " and 9999999 more").encode("utf-8")) + 1
    # The task record's block shares TOTAL_BYTES with the owner messages: the messages are clipped
    # around it, so the whole output stays under the client's hook-output limit.
    try:
        contexts = reply_contexts(transcript, messages)
    except Exception:
        contexts = {}
    if rating_line is None:
        elapsed = time.monotonic() - started if started is not None else 0
        rating_line = rating_debt_line(sid, RATING_DEADLINE_SEC - elapsed)
    record_block = "\n".join(b for b in (locations_line(project), task_block(sid, project), rating_line) if b)
    budget = (TOTAL_BYTES - sum(len(line.encode("utf-8")) + 1 for line in lines) - reserve
              - (len(record_block.encode("utf-8")) + 2 if record_block else 0))
    messages = [dict(m, context=contexts[m["index"]]) if m["index"] in contexts else m for m in messages]
    entries = [(m, shape(m["content"])) for m in messages]
    opening_head = [opening_intro()]
    opening_budget = TOTAL_BYTES - len(opening_head[0].encode("utf-8")) - 1

    def opening_cut(printed, levels):
        return [m for m, shaped in entries[:HEAD_WHOLE]
                if id(m) not in printed or render(shaped, levels[id(m)]) != shaped[0]]

    opening, opening_levels = fit_opening(entries, opening_budget)
    if opening_cut({id(m) for m, _ in opening}, opening_levels):
        worst_line = opening_line([m for m, _ in entries[:HEAD_WHOLE]], where)
        opening, opening_levels = fit_opening(entries, opening_budget - len(worst_line.encode("utf-8")) - 1)
    printed = {id(m) for m, _ in opening}
    cut = opening_cut(printed, opening_levels)
    opening_lines = opening_head + ([opening_line(cut, where)] if cut else [])
    kept, omitted, levels = fit([e for e in entries if id(e[0]) not in printed], budget)
    clipped = [m for m, shaped in kept if render(shaped, levels[id(m)]) != shaped[0]]
    return Injection(lines, kept, omitted, levels, clipped, record_block, opening_lines, opening, opening_levels)


def whole_ids(injection: Injection) -> list:
    """The U-ids of the messages either part of `injection` prints in full, oldest first."""
    whole = [m for m, shaped in injection.opening if render(shaped, injection.opening_levels[id(m)]) == shaped[0]]
    cut = {id(m) for m in injection.clipped}
    whole += [m for m, _ in injection.kept if id(m) not in cut]
    return sorted(m["index"] for m in whole)


def _compact(payload: dict, started: float, opening: bool = False) -> int:
    """Print one part: the opening messages (`opening`), or the later messages and the record. Only the later
    part reports a read failure and writes the uncapped file, so the two parallel runs never race on it."""
    transcript = Path(payload.get("transcript_path") or "")
    sid = payload.get("session_id") or transcript.stem
    try:
        sd = _digest()
        messages = [m for m in sd.owner_prompts(transcript) if keep(m)]
    except Exception as exc:
        if not opening:
            print(f"{TAG} context was compacted, but this hook could not read the transcript "
                  f"({type(exc).__name__}: {exc}). Recover the owner's messages before continuing: "
                  f"python3 .claude/tools/session_digest.py --session {sid[:8]} --handoff")
        return 0
    if not messages:
        return 0
    project = project_dir(payload)
    if opening:
        where = full_location(sid)
        injection = plan(transcript, sid, project, messages, where, fetch_command(where), started, rating_line="")
        print(injection.opening_lines[0])
        for message, shaped in injection.opening:
            level = injection.opening_levels[id(message)]
            sys.stdout.write(block(message, render(shaped, level), level, FLOOR_CHARS))
        for line in injection.opening_lines[1:]:
            print(line)
        return 0
    try:
        full = write_full(project / "logs", sid, messages)
        where = f"logs/{full.name}"
        fetch = fetch_command(where)
    except OSError as exc:
        where = f"unavailable ({exc})"
        fetch = (f"python3 .claude/tools/session_digest.py --session {sid[:8]} --handoff (writes "
                 f"logs/session_digest_{sid[:8]}.json), then session_digest.py --digest-file "
                 f"logs/session_digest_{sid[:8]}.json --select <ID>")

    injection = plan(transcript, sid, project, messages, where, fetch, started)
    for line in injection.lines:
        print(line)
    for message, shaped in injection.kept:
        level = injection.levels[id(message)]
        sys.stdout.write(block(message, render(shaped, level), level))
    listed = note(injection.clipped, injection.omitted, fetch)
    if listed:
        print(listed)
    if injection.record_block:
        print(injection.record_block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
