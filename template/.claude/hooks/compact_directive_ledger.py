#!/usr/bin/env python3
"""PreCompact — ask the compaction summary for an owner-directive ledger.

Claude Code appends PreCompact stdout to the compaction instructions. The summarizer is the only
reader that still sees what happened after each owner message, so it is the one place that can tell
a standing decision from a finished step or a replaced instruction (owner decision 2026-09-14,
session 3259384b). This hook lists every owner message by the U-ID that `compact_directive_anchor.py`
re-injects after compaction, and asks the summary for one status line per ID.

Selection and shaping are the anchor's own (`keep`, `shape`): interrupts, bare and session-control
commands are filtered out, and an answer entry shows the owner's answers rather than the question.
Each entry is a head of at most HEAD_CHARS characters, shortened together toward MIN_HEAD_CHARS when
the list is long. The output has a byte cap, TOTAL_BYTES, under the client's 10,000-character hook
output limit; past it the first HEAD_KEEP entries and the newest that fit stay, and the omitted
middle is counted.

Status carry-forward (owner decision A2, 2026-09-15): a settled ID costs its full ~138-byte entry
every compaction forever, even though most entries stop changing. The hook reads the transcript's own
prior `isCompactSummary` records for their "Owner directives" sections. Once at least two exist, an ID
whose status was terminal (done, scoped or replaced) in both of the last two is dropped from the full
listing and named only in one bare comma-separated line; the next summary is asked to keep carrying it
forward the same way unless later conversation actually reopened it, in which case it gets a fresh
full status line. Fewer than two prior summaries in the transcript: the hook behaves exactly as before
this feature, full entries and the plain instructions text, which is also what an unreadable
transcript falls back to (asking for the section, without IDs). A sidecar child, a subagent
payload (`_hook_state.is_subagent_payload`; its transcript is the parent's, so the ledger would list the parent's
owner messages in the subagent's summary) and a non-object payload are silent no-ops; the hook never
blocks.

The instructions give each kind of fact one section of the summary, the client's built-in sections
included, which then cite it: compaction audits found
summaries restating owner messages up to three times, writing each ruling in force twice and copying
the next step into three sections. They also ask for what those audits found lost: a ruling's scope
and stop condition (on its holds line), the meaning of a reply that only names an option, exact syntax
a denial taught, unanswered owner questions, whose an uncommitted file is, exact values in a pending
step, and unrated outcomes. After the owner lines, a facts block read from the transcript gives the last
assistant messages with their send times and the background jobs (a background Bash call, or any
tool whose result reports a task id, such as a Workflow) launched with no completion notice, since
summaries claimed a sent reply unsent in 5 of 30 compactions and inferred a running job's state. An empty facts block prints nothing.

What a summary keeps and drops (exact re-issuable strings, the command of each pending step that already
ran once, no "…" inside a quote, one line per ID, no landed-commit lists or fixed-error history) is owned by
`skills/orchestration/reference/checkpointing.md` §Summary fidelity. The summarizer cannot read files, so
the hook emits that section's bullets as they stand (`_fidelity_section`). The owner-message list closes with
an "Anchor prints whole" line: the IDs the SessionStart anchor re-injects in full, from the anchor's own
layout (`anchor.plan`), so a holds line for one is ID, status and scope only. Any other ID quotes its
binding sentences, the anchor clipping or omitting them.

Echo: the client appends this stdout to the summarizer's instructions and, after a manual `/compact` only,
records it again as `<local-command-stdout>` in the context that follows. The hooks documentation names no PreCompact output channel (only
UserPromptSubmit, UserPromptExpansion, SessionStart and PostModelSwitch add stdout as context, and PreCompact
discards `systemMessage`), so no channel avoids the echo. The echo is held to the cap `TOTAL_BYTES`, and the
instruction text grows by less than the fidelity section.

Every transcript pass (owner messages, prior summaries, facts) resumes from its `hooks/_transcript_scan.py`
cache, and each run appends its duration, output bytes and scans to `logs/compaction_hooks.jsonl`
(`compact_directive_anchor.log_run`).
"""
import contextlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _background_tasks  # noqa: E402
import _transcript_scan  # noqa: E402
import compact_directive_anchor as anchor  # noqa: E402
from _hook_state import is_subagent_payload  # noqa: E402

TOTAL_BYTES = 8000
HEAD_CHARS = 110
MIN_HEAD_CHARS = 40
HEAD_KEEP = 10
LAST_SHOWN = 3
JOBS_SHOWN = 10
FACT_CHARS = 160
MIN_PRIOR_SUMMARIES = 2  # fewer than this: behave exactly as before A2 (owner decision 2026-09-15)
FIDELITY_DOC = HERE.parent / "skills" / "orchestration" / "reference" / "checkpointing.md"

_SECTIONS = """## Section owners
Each fact goes in one section; the others cite it by ID, label or path.- Owner directives (below) owns every owner request and ruling. Primary Request gives the goal in a few lines, citing U-IDs. All user messages: write "see Owner directives".
- Artifacts (required section): one line per changed file later work needs: `<path>` — what changed and, while uncommitted, whose it is (this session, a delegate's job label, another session's id, else "unknown"). Files and Code Sections: write "see Artifacts".
- Open questions (required section): each question put to the owner and unanswered: what it decides, the options, your pick, the default if work keeps running, the open question's observed values verbatim. None: write "none".
- Pending Tasks owns open work; each item stands alone for a cold reader: what, why, next action. Current Work and Next Step cite the item, never copy its steps.
- A job's state is its last observation with its time ("result written 17:06; end not checked"), never an inference such as "not returned".
- Key Concepts and Errors keep only what a pending step needs and no file holds: an open problem's exact error text, and syntax a denial or a usage print taught.
- Pending ratings: each dispatch result consumed and not yet rated, by label, with the outcome you judged (clean, defects, rework or discarded) and why."""

_LEGACY_ASK = """Add a section headed "Owner directives". Give one line for every ID listed below, in order: `U<id> — <status> — <what it asked, in a few words>`. The status is exactly one of:"""

_CARRY_ASK = """Add a section headed "Owner directives". The list below marks some IDs settled. Carry every settled ID forward in one comma-separated line, `Settled (done/scoped/replaced): U<id>, U<id>, ...`, with no status or text; reopen one with its own full line only when later conversation actually reopened it. Every other ID (holds, unclear, never statused) gets its own full line: `U<id> — <status> — <what it asked, in a few words>`. The status is exactly one of:"""

_STATUS_MENU = """- holds: still binds the rest of the session (a standing goal, constraint or preference);
- done: carried out, answered, or no longer needed;
- replaced by U<id>: a later owner message changed or reversed it;
- scoped: bound one step or moment that has passed;
- unclear: the conversation does not settle it.
A message with any part still pending or still binding holds; name only that part, its scope, when it stops (a slice, a phase, a condition) and the doc anchor holding its full text. A holds line for an ID on the "Anchor prints whole" line below is ID, status and scope only: compact_directive_anchor.py re-injects those messages under the same IDs. Any other holds line quotes the owner's binding sentences verbatim, the words that constrain later work; a paraphrase drops the reasoning the owner corrected with. A ruling holds while it constrains work, even after its first use. When a reply only names an option ("O2", "Sure"), write out that option's text from the message it answered.
Judge each status from what happened after the message, not from its wording."""

_KEEP_ALL = "Keep every ID."
_KEEP_SPLIT = "Keep every ID: settled ones in the comma list, the rest on full lines."


def _fidelity_section() -> str:
    """The bullets of `checkpointing.md` §Summary fidelity, the one owner of what a summary keeps and
    drops. The summarizer has no file access, so the hook emits them as they stand; "" when the file or
    section is unreadable (`test_compact_directive_ledger.py` proves it is not)."""
    try:
        text = FIDELITY_DOC.read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.search(r"^## Summary fidelity\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    bullets = [l.rstrip() for l in (found.group(1) if found else "").splitlines() if l.startswith("- ")]
    return "\n".join(["## Summary fidelity"] + bullets) if bullets else ""


def _instructions(ask: str, keep: str) -> str:
    """Both variants differ only in how they ask for the list, and in what keeping every ID means."""
    fidelity = _fidelity_section()
    return "\n".join([_SECTIONS] + ([fidelity] if fidelity else []) + [
        "", "## Owner directives (required section of the summary)", ask, " ".join([_STATUS_MENU, keep])])


INSTRUCTIONS = _instructions(_LEGACY_ASK, _KEEP_ALL)
INSTRUCTIONS_CARRY_FORWARD = _instructions(_CARRY_ASK, _KEEP_SPLIT)

UNREADABLE = ("The owner message list could not be read. List the owner's decisions and instructions you can "
              "find in the conversation, each with a status.")

TERMINAL_STATUSES = frozenset({"done", "scoped", "replaced"})
_OWNER_SECTION_RE = re.compile(r"##\s*owner directives\b.*?\n(.*?)(?=\n##\s|\Z)", re.IGNORECASE | re.DOTALL)
_STATUS_LINE_RE = re.compile(r"^\s*-?\s*U(\d+)\s*[—-]\s*(holds|done|scoped|unclear|replaced)\b",
                             re.IGNORECASE | re.MULTILINE)
_SETTLED_LIST_RE = re.compile(r"^\s*settled\b[^:\n]*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ID_TOKEN_RE = re.compile(r"U(\d+)")


def _row_text(row: dict) -> str:
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _summary_feed(state: dict, index: int, line: str) -> None:
    if "isCompactSummary" not in line:
        return
    try:
        row = json.loads(line.strip())
    except json.JSONDecodeError:
        return
    if isinstance(row, dict) and row.get("isCompactSummary"):
        text = _row_text(row)
        if text:
            state["newest"] = (state["newest"] + [text])[-MIN_PRIOR_SUMMARIES:]


SUMMARY_SCAN = _transcript_scan.Scanner("ledger_summaries", lambda: {"newest": []}, _summary_feed,
                                        sources=[__file__])


def prior_summaries(transcript_path: Path) -> list:
    """The newest MIN_PRIOR_SUMMARIES `isCompactSummary` messages' text, oldest first; [] unreadable.
    Carry-forward reads only these, so the scan keeps no older one."""
    try:
        return _transcript_scan.scan(transcript_path, SUMMARY_SCAN)["newest"]
    except OSError:
        return []


def directive_statuses(summary_text: str) -> dict:
    """{id: status} read from one past summary's own Owner directives section. A bare settled-list
    ID counts as terminal ("done"); its exact prior sub-status is not needed for carry-forward."""
    match = _OWNER_SECTION_RE.search(summary_text)
    section = match.group(1) if match else ""
    statuses = {}
    for m in _STATUS_LINE_RE.finditer(section):
        statuses[int(m.group(1))] = m.group(2).lower()
    for m in _SETTLED_LIST_RE.finditer(section):
        for id_match in _ID_TOKEN_RE.finditer(m.group(1)):
            statuses.setdefault(int(id_match.group(1)), "done")
    return statuses


def carried_forward_ids(summaries: list) -> set:
    """IDs terminal in the last two consecutive summaries; empty unless at least two exist."""
    if len(summaries) < MIN_PRIOR_SUMMARIES:
        return set()
    latest = directive_statuses(summaries[-1])
    prior = directive_statuses(summaries[-2])
    latest_terminal = {i for i, s in latest.items() if s in TERMINAL_STATUSES}
    prior_terminal = {i for i, s in prior.items() if s in TERMINAL_STATUSES}
    return latest_terminal & prior_terminal


def settled_line(ids) -> str:
    joined = ", ".join(f"U{i}" for i in sorted(ids))
    return f"settled (status carried forward; re-list one only if later conversation reopened it): {joined}"


class TurnClosers:
    """The closing message of each turn: a turn ends at a typed prompt or a queued notice, and a tool
    result never ends one. Its state is a JSON dict, so the transcript scan can resume it; it keeps
    only the newest LAST_SHOWN closed turns, the ones the facts block prints."""

    def __init__(self, state=None):
        self.state = state if state is not None else {"shown": [], "closing": None}

    @property
    def shown(self):
        return self.state["shown"]

    def _close(self):
        if self.state["closing"]:
            self.state["shown"] = (self.state["shown"] + [self.state["closing"]])[-LAST_SHOWN:]
            self.state["closing"] = None

    def feed(self, row: dict) -> None:
        if row.get("type") == "queue-operation":
            if row.get("operation") == "enqueue":
                self._close()
            return
        content = (row.get("message") or {}).get("content")
        if row.get("type") == "user" and not row.get("isMeta") and (
                isinstance(content, str) or (isinstance(content, list) and not any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content))):
            self._close()
        if not isinstance(content, list) or not anchor.shown_assistant_row(row):
            return
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                self.state["closing"] = [row.get("timestamp"), b["text"].strip().splitlines()[0]]

    def finish(self) -> list:
        self._close()
        return self.state["shown"]


def _labels(value, depth=0) -> list:
    """The `label` values in a tool call's args (a dispatch's jobs, seats or chain lanes), in order."""
    if depth > 3:
        return []
    if isinstance(value, dict):
        own = [value["label"]] if isinstance(value.get("label"), str) else []
        return own + [x for k, v in value.items() if k != "label" for x in _labels(v, depth + 1)]
    if isinstance(value, list):
        return [x for v in value for x in _labels(v, depth + 1)]
    return []


def _launch_name(block: dict) -> str:
    """A launch's description, else its tool name and the job labels its args carry."""
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    if tool_input.get("description"):
        return tool_input["description"]
    return " ".join([str(block.get("name") or "")] + _labels(tool_input.get("args"))[:4]).strip()


class BackgroundJobs:
    """Background launches joined to their task ids by the launch result, minus the ids a completion
    notice named. A Bash call counts when it asks for the background; any other tool counts when its
    result reports a task id (a Workflow or a background agent). Its state is a JSON dict, so the
    transcript scan can resume it: a launch leaves `launches` once its result row is read, since a
    tool_use id gets one result, and ids are keyed by their JSON text."""

    def __init__(self, state=None):
        self.state = state if state is not None else {"launches": {}, "ids": {}, "done": {}}

    def feed(self, row: dict) -> None:
        launches, ids, done = self.state["launches"], self.state["ids"], self.state["done"]
        if row.get("type") == "queue-operation":
            done.update(dict.fromkeys(_background_tasks.ENDED_RE.findall(str(row.get("content") or "")), True))
            return
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list) or (row.get("message") or {}).get("model") == "<synthetic>":
            return
        launching = anchor.shown_assistant_row(row)
        for b in content:
            if not isinstance(b, dict):
                continue
            if launching and b.get("type") == "tool_use" and (
                    b.get("name") != "Bash" or (b.get("input") or {}).get("run_in_background")):
                launches[_key(b.get("id"))] = [_launch_name(b), row.get("timestamp")]
            elif b.get("type") == "tool_result" and _key(b.get("tool_use_id")) in launches:
                launch = launches.pop(_key(b["tool_use_id"]))
                task = _background_tasks.launched_task_id(row)
                if task:
                    ids[_key(task)] = launch

    def running(self) -> list:
        done = self.state["done"]
        return [(json.loads(task), desc, ts) for task, (desc, ts) in self.state["ids"].items()
                if json.loads(task) not in done][-JOBS_SHOWN:]


def _key(value) -> str:
    """A dict key that survives a JSON round trip; an unhashable value raises TypeError as a dict key would."""
    hash(value)
    return json.dumps(value)


def _facts_feed(state: dict, index: int, line: str) -> None:
    try:
        row = json.loads(line)
    except ValueError:
        return
    if not isinstance(row, dict) or row.get("isSidechain"):
        return
    TurnClosers(state["turns"]).feed(row)
    BackgroundJobs(state["jobs"]).feed(row)


FACTS_SCAN = _transcript_scan.Scanner(
    "ledger_facts", lambda: {"turns": TurnClosers().state, "jobs": BackgroundJobs().state}, _facts_feed,
    sources=[__file__, anchor.__file__, _background_tasks.__file__])


def facts_lines(transcript_path: Path) -> list:
    """The closing message of each of the last LAST_SHOWN turns (first line, send time) and the
    background jobs launched with no completion notice, newest JOBS_SHOWN, from one transcript pass
    feeding `TurnClosers` and `BackgroundJobs`; [] when neither exists or a row is malformed: the facts
    block is supplementary and never costs the ledger. The pass resumes from the transcript scan cache."""
    try:
        state = _transcript_scan.scan(transcript_path, FACTS_SCAN)
        shown, running = TurnClosers(state["turns"]).finish(), BackgroundJobs(state["jobs"]).running()
    except (OSError, AttributeError, TypeError):
        return []
    if not shown and not running:
        return []
    lines = ["## Facts from the transcript (Current Work and Pending Tasks must agree with these)",
             "Last messages the owner saw (a message listed here was sent):"]
    lines += [f"- {anchor.stamp(ts) or '?'} · {anchor.head_clip(first, FACT_CHARS)}" for ts, first in shown[-LAST_SHOWN:]]
    if running:
        lines.append("Background jobs launched with no completion notice when compaction began:")
        lines += [f"- {task} · {anchor.head_clip(desc, 100)} · launched {anchor.stamp(ts) or '?'}"
                  for task, desc, ts in running]
    return lines


def head(message, shaped, limit):
    text, pairs = shaped
    if pairs is not None:
        text = "(answer) " + "; ".join(answer for _, answer in pairs)
    return anchor.head_clip(text, limit)


def entry(message, shaped, limit):
    sent = anchor.stamp(message.get("timestamp"))
    stamp = f" · {sent}" if sent else ""
    return f"U{message.get('index')}{stamp} · {head(message, shaped, limit)}"


def size(lines):
    return sum(len(line.encode("utf-8")) + 1 for line in lines)


def ledger_lines(messages, budget):
    shaped = [(m, anchor.shape(m["content"])) for m in messages]
    limit = anchor.largest(lambda n: size(entry(m, s, n) for m, s in shaped) <= budget,
                           MIN_HEAD_CHARS, HEAD_CHARS)
    lines = [entry(m, s, limit) for m, s in shaped]
    if size(lines) <= budget:
        return lines
    kept_head = lines[:HEAD_KEEP]
    room = budget - size(kept_head) - 200
    tail = []
    for line in reversed(lines[HEAD_KEEP:]):
        if size([line]) > room:
            break
        tail.append(line)
        room -= size([line])
    omitted = len(lines) - len(kept_head) - len(tail)
    note = (f"({omitted} owner messages between these entries are omitted for length; give them a status "
            f"from the conversation by their position, or mark the gap unclear.)")
    return kept_head + [note] + tail[::-1]


def whole_line(payload: dict, transcript_path: Path, messages: list) -> str:
    """Which messages the SessionStart anchor will print in full, from the anchor's own layout
    (`anchor.plan`), so a holds line for one can be ID, status and scope only. A layout that cannot
    be built names none: every holds line then quotes, the safe direction."""
    try:
        sid = payload.get("session_id") or transcript_path.stem
        where = anchor.full_location(sid)
        layout = anchor.plan(transcript_path, sid, anchor.project_dir(payload), messages, where,
                             anchor.fetch_command(where), rating_line=anchor.rating_debt_text(99))
        ids = anchor.whole_ids(layout)
    except Exception:
        ids = []
    if ids and len(ids) == len(messages):
        return "Anchor prints whole: every owner message."
    return f"Anchor prints whole: {', '.join(f'U{i}' for i in ids) or 'none'}."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in (None, "PreCompact"):
        return 0
    if os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip() or is_subagent_payload(payload):
        return 0
    started = time.monotonic()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = _ledger(payload)
    anchor.emit("compact_directive_ledger", payload, started, buffer.getvalue())
    return code


def _ledger(payload: dict) -> int:
    transcript_path = Path(payload.get("transcript_path") or "")
    try:
        sd = anchor._digest()
        messages = [m for m in sd.owner_prompts(transcript_path) if anchor.keep(m)]
    except Exception:
        print(INSTRUCTIONS)
        print(UNREADABLE)
        return 0
    if not messages:
        return 0

    summaries = prior_summaries(transcript_path)
    whole = whole_line(payload, transcript_path, messages)
    facts = [whole] + facts_lines(transcript_path)
    if len(summaries) < MIN_PRIOR_SUMMARIES:
        print(INSTRUCTIONS)
        print("Owner messages (ID · sent · opening words):")
        budget = TOTAL_BYTES - size([INSTRUCTIONS, "Owner messages (ID · sent · opening words):"]) - size(facts)
        for line in ledger_lines(messages, budget):
            print(line)
        for line in facts:
            print(line)
        return 0

    listed_ids = {m["index"] for m in messages}
    carry_ids = carried_forward_ids(summaries) & listed_ids
    full_messages = [m for m in messages if m["index"] not in carry_ids]

    print(INSTRUCTIONS_CARRY_FORWARD)
    print("Owner messages (ID · sent · opening words):")
    settled = settled_line(carry_ids) if carry_ids else None
    budget = (TOTAL_BYTES - size([INSTRUCTIONS_CARRY_FORWARD, "Owner messages (ID · sent · opening words):"])
              - (size([settled]) if settled else 0) - size(facts))
    for line in ledger_lines(full_messages, budget):
        print(line)
    if settled:
        print(settled)
    for line in facts:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
