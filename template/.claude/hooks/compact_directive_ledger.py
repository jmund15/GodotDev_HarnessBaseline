#!/usr/bin/env python3
"""PreCompact — ask for directive statuses, not a second copy of the owner's words.

The summarizer sees what followed each owner message; the SessionStart anchor preserves the
verbatim words. This hook uses the anchor's owner filter and shaping, lists new or unstatused
messages by U-ID and a bounded head, and refers to the preceding summary for IDs it already
statused. Only an ID terminal in both newest summaries enters the compact settled list. A later
holds or unclear status reopens it. Missing or unreadable sections keep the full listing.

Summary sections accept ATX, bold and numbered headings, with bulleted settled IDs and formatted
U-IDs. The status parser stops at the next section. Current work, unanswered questions, file
ownership, exact strings, scope and stop conditions stay in the summary. `checkpointing.md`
Summary fidelity owns what it keeps; `_fidelity_section` emits its bullets to the summarizer.
Owner holds lines keep ID, status and scope; clipped or omitted words remain recoverable through
the anchor's --show command instead of being quoted twice.

Unchanged ratings and deferred bookkeeping can cite a section of a prior summary already on disk.
`--show-summary <transcript> <sha256> <section>` prints that section verbatim and rejects a missing
summary or section. The hash binds the command to its captured text; no mutable checkpoint store
or extra write is needed. New or changed facts and the current next action stay inline.

The facts block records sent turn closers and observed background launches without inferring job
state. Main-hook scans resume from `_transcript_scan`; cold section recovery streams its source.
Manual /compact echoes PreCompact stdout into the resumed context, so instructions and message
heads share TOTAL_BYTES. The hook logs duration, emitted bytes and scans through the anchor.
Sidecar children, subagent payloads and non-object payloads are silent; the hook never blocks.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
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
Each fact goes in one section; the others cite its ID, label or path.
- Owner directives owns request statuses and scope. Primary Request gives the goal, citing U-IDs. All user messages: write "see Owner directives".
- Artifacts (required section): one line per changed file later work needs: `<path>` — what changed and, while uncommitted, whose it is (this session, a delegate's label, another session's id, else "unknown"). Files and Code Sections: write "see Artifacts".
- Open questions (required section): each unanswered question's decision, options, your pick, default if work keeps running, and the open question's observed values verbatim. None: write "none".
- Pending Tasks owns open work: what, why, next action. Current Work and Next Step cite its item, never copy the steps.
- A job's state is its last observation with its time ("result written 17:06; end not checked"), never an inferred "not returned".
- Key Concepts and Errors keep only what a pending step needs and no file holds: exact error text and syntax a denial or a usage print taught.
- Pending ratings owns consumed dispatches not yet rated: label, judged outcome (clean, defects, rework or discarded) and why."""

_LEGACY_ASK = """Add a section headed "Owner directives". Give one line for every ID listed below, in order: `U<id> — <status> — <scope or result>`. The status is exactly one of:"""

_CARRY_ASK = """Add a section headed "Owner directives". Keep the preceding summary's statused IDs, updating them from later conversation. Carry every settled ID in one comma-separated line, `Settled (done/scoped/replaced): U<id>, U<id>, ...`, with no text; reopen one with a full line only when later conversation reopened it. Every other ID gets a full line: `U<id> — <status> — <scope or result>`. The status is exactly one of:"""

_STATUS_MENU = """- holds: still binds the rest of the session (a goal, constraint, preference or grant of authority);
- done: carried out, answered, or no longer needed; a grant of authority is not done because the action it allowed ran;
- replaced by U<id>: a later owner message changed it;
- scoped: bound a step or moment that has passed;
- unclear: the conversation does not settle it.
A message with any part still pending or still binding holds; name that part, its scope, when it stops and the doc anchor holding its full text. Every holds line is ID, status and scope only: compact_directive_anchor.py re-injects the owner's words under the same IDs and supplies --show recovery for clipped or omitted messages. Recover their binding sentences verbatim there, never quote or paraphrase them in the summary. A ruling holds while it constrains work, even after its first use. A grant of authority (to push, land, spend, delete) holds while the line of work it was given for continues, past its first use and past the instances the question that drew it listed; its holds line names that line of work. When a reply only names an option ("O2", "Sure"), keep the option's meaning unless the anchor's labeled answering context already carries it.
Judge each status from what happened after the message, not its wording."""

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

UNREADABLE = ("The owner message list could not be read; anchor recovery is unverified. For this compaction, "
              "override the no-quote rule: keep binding sentences verbatim in Owner directives, with their "
              "status and scope. List the owner's decisions and instructions from the conversation.")

TERMINAL_STATUSES = frozenset({"done", "scoped", "replaced"})
_SECTION_RE = re.compile(
    r"^(?:#{1,6}[ \t]+(?P<atx>[^\n]+)|\*\*(?P<bold>[^\n*]+)\*\*:?[ \t]*|"
    r"\d+\.[ \t]+(?P<numbered>[A-Z][^\n]+))$", re.MULTILINE)
_STATUS_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*|`)?U(\d+)(?:\*\*|`)?\s*[—-]\s*"
    r"(holds|done|scoped|unclear|replaced)\b", re.IGNORECASE | re.MULTILINE)
_SETTLED_LIST_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?settled\b[^:\n]*:\s*(.+)$",
                              re.IGNORECASE | re.MULTILINE)
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


def summary_section(text: str, name: str) -> str:
    """One section, including its heading, from the client's Markdown summary formats."""
    headings = list(_SECTION_RE.finditer(text))
    wanted = name.casefold()
    for i, heading in enumerate(headings):
        title = next(value for value in heading.groupdict().values() if value is not None).strip().rstrip(":").casefold()
        if title == wanted or title.startswith(wanted + " ("):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            return text[heading.start():end]
    return ""


def directive_statuses(summary_text: str) -> dict:
    """{id: status} from Owner directives; a settled ID is terminal without its former sub-status."""
    section = summary_section(summary_text, "Owner directives")
    statuses = {}
    for m in _STATUS_LINE_RE.finditer(section):
        statuses[int(m.group(1))] = m.group(2).lower()
    for m in _SETTLED_LIST_RE.finditer(section):
        for id_match in _ID_TOKEN_RE.finditer(m.group(1)):
            statuses.setdefault(int(id_match.group(1)), "done")
    return statuses


def summary_recovery_lines(transcript_path: Path, summaries: list) -> list:
    """Exact section reads of the preceding summary, without a second store or a mutable pointer."""
    if not summaries:
        return []
    text = summaries[-1]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    command = ("python3 .claude/hooks/compact_directive_ledger.py --show-summary "
               f"{shlex.quote(transcript_path.resolve().as_posix())} {digest}")
    lines = [f"- {name}: {command} {shlex.quote(name)}"
             for name in ("Pending Tasks", "Pending ratings") if summary_section(text, name)]
    return ["## Stored resume state (recover only when that work is next)"] + lines if lines else []


def show_summary(args: list) -> int:
    """`--show-summary <transcript> <sha256> <section>` prints only that captured section."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if len(args) != 3 or not re.fullmatch(r"[0-9a-f]{64}", args[1]):
        print("usage: compact_directive_ledger.py --show-summary <transcript> <sha256> <section>", file=sys.stderr)
        return 2
    path, digest, section = args
    try:
        with open(path, encoding="utf-8") as transcript:
            for line in transcript:
                if "isCompactSummary" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not row.get("isCompactSummary"):
                    continue
                text = _row_text(row)
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
                    continue
                found = summary_section(text, section)
                if not found:
                    print(f"no section {section!r} in summary {digest}", file=sys.stderr)
                    return 1
                print(found.rstrip())
                return 0
    except (OSError, UnicodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1
    print(f"no summary {digest} in {path}", file=sys.stderr)
    return 1


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


def main() -> int:
    if sys.argv[1:2] == ["--show-summary"]:
        return show_summary(sys.argv[2:])
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in (None, "PreCompact"):
        return 0
    if os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip():
        return 0
    started = time.monotonic()
    if is_subagent_payload(payload):
        anchor.log_run("compact_directive_ledger", payload, started, "", skipped="subagent")
        return 0
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
    facts = summary_recovery_lines(transcript_path, summaries) + facts_lines(transcript_path)
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
    known_ids = (directive_statuses(summaries[-1]).keys() & listed_ids) - carry_ids
    full_messages = [m for m in messages if m["index"] not in carry_ids | known_ids]
    known = (anchor._listed("Statused in preceding summary", [{"index": i} for i in sorted(known_ids)])
             + ". Keep their status lines; update them from later conversation." if known_ids else None)
    settled = settled_line(carry_ids) if carry_ids else None
    carried = [line for line in (known, settled) if line]

    print(INSTRUCTIONS_CARRY_FORWARD)
    print("Owner messages (ID · sent · opening words):")
    budget = (TOTAL_BYTES - size([INSTRUCTIONS_CARRY_FORWARD, "Owner messages (ID · sent · opening words):"])
              - size(carried) - size(facts))
    for line in ledger_lines(full_messages, budget):
        print(line)
    for line in carried + facts:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
