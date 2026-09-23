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
transcript falls back to (asking for the section, without IDs). A sidecar child and a non-object
payload are silent no-ops; the hook never blocks.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compact_directive_anchor as anchor  # noqa: E402

TOTAL_BYTES = 8000
HEAD_CHARS = 110
MIN_HEAD_CHARS = 40
HEAD_KEEP = 10
MIN_PRIOR_SUMMARIES = 2  # fewer than this: behave exactly as before A2 (owner decision 2026-09-15)

_ARTIFACTS = """## Artifacts (required section of the summary)
Add a section headed "Artifacts": one line per changed file later work needs, `<path>` — what it is, what changed. Never restate a file's contents in any section (no code, constants, test names or prompt/report text); one read recovers them. Keep decisions, open work, exact error text and an open question's observed values in full."""

_LEGACY_ASK = """Add a section headed "Owner directives". Give one line for every ID listed below, in order: `U<id> — <status> — <what it asked, in a few words>`. The status is exactly one of:"""

_CARRY_ASK = """Add a section headed "Owner directives". The list below marks some IDs settled: two summaries in a row judged them done, scoped or replaced. Carry every settled ID forward in one line, comma-separated, `Settled (done/scoped/replaced): U<id>, U<id>, ...` — no restated status or text. Reopen a settled ID with its own fresh full line only when later conversation actually reopened it (owner decision 2026-09-14: U9587 went done -> holds this way). Give every other ID — holds, unclear, or never statused — its own full line: `U<id> — <status> — <what it asked, in a few words>`. The status is exactly one of:"""

_STATUS_MENU = """- holds: still binds the rest of the session (a standing goal, constraint or preference);
- done: carried out, answered, or no longer needed;
- replaced by U<id>: a later owner message changed or reversed it;
- scoped: bound one step or moment that has passed;
- unclear: the conversation does not settle it.
A message with any part still pending or still binding holds; name only that part.
Judge each status from what happened after the message, not from its wording."""

_KEEP_ALL = "Keep every ID."
_KEEP_SPLIT = "Keep every ID: settled ones in the comma list, every other one on its own full line."
_ANCHOR_NOTE = ("After compaction, compact_directive_anchor.py re-injects the owner's own words under the "
                "same IDs, so each status can be checked against them.")


def _instructions(ask: str, keep: str) -> str:
    """Both variants differ only in how they ask for the list, and in what keeping every ID means."""
    return "\n".join([_ARTIFACTS, "", "## Owner directives (required section of the summary)",
                      ask, " ".join([_STATUS_MENU, keep, _ANCHOR_NOTE])])


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


def prior_summaries(transcript_path: Path) -> list:
    """Every `isCompactSummary` message's text from the transcript, oldest first; [] unreadable."""
    out = []
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("isCompactSummary"):
                    text = _row_text(row)
                    if text:
                        out.append(text)
    except OSError:
        return []
    return out


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


def head(message, shaped, limit):
    text, pairs = shaped
    if pairs is not None:
        text = "(answer) " + "; ".join(answer for _, answer in pairs)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def entry(message, shaped, limit):
    sent = message.get("timestamp") or ""
    stamp = f" · {sent[5:16].replace('T', ' ')}" if len(sent) >= 16 else ""
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
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or payload.get("hook_event_name") not in (None, "PreCompact"):
        return 0
    if os.environ.get("CLAUDE_CODE_SIDECAR_PROMPT_FILE", "").strip():
        return 0
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
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
    if len(summaries) < MIN_PRIOR_SUMMARIES:
        print(INSTRUCTIONS)
        print("Owner messages (ID · sent · opening words):")
        budget = TOTAL_BYTES - size([INSTRUCTIONS, "Owner messages (ID · sent · opening words):"])
        for line in ledger_lines(messages, budget):
            print(line)
        return 0

    listed_ids = {m["index"] for m in messages}
    carry_ids = carried_forward_ids(summaries) & listed_ids
    full_messages = [m for m in messages if m["index"] not in carry_ids]

    print(INSTRUCTIONS_CARRY_FORWARD)
    print("Owner messages (ID · sent · opening words):")
    settled = settled_line(carry_ids) if carry_ids else None
    budget = (TOTAL_BYTES - size([INSTRUCTIONS_CARRY_FORWARD, "Owner messages (ID · sent · opening words):"])
              - (size([settled]) if settled else 0))
    for line in ledger_lines(full_messages, budget):
        print(line)
    if settled:
        print(settled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
