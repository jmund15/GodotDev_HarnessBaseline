#!/usr/bin/env python3
"""Write the prompt for one independent /codify derivation from a fixed template.

Usage:
    codify_prompt.py --session <id> --at "<YYYY-MM-DD HH:MM>" --closing "<owner words>"
                     [--part-opens "<owner words>"] [--base <sha>] [--turns N] [--out-suffix -glm] [--transcript <path>]
    codify_prompt.py --check <prompt.md>

`--at` is the correction's UTC minute. `--closing` and `--part-opens` must appear in the owner message
sent at that minute (whitespace folded); words that do not are refused and nothing is written. The owner
messages come from `session_digest.owner_prompts`, the reader the digest and the compaction anchor share.

Writes `.claude/scratch/codify_<sid8>_<n>/prompt<suffix>.md` and `evidence<suffix>.md`, the packet the
prompt names by digest. `<n>` is one past the highest existing folder for the session; with `--out-suffix`
it joins the highest existing folder instead. The template carries pointers only: no account of what the session did.

`--check` re-renders a prompt from the fields it holds and prints a line when the prompt differs from
that render or its packet is missing or changed. `differs(path)` is the same check for `dispatch_size.py`'s preflight.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PART = ' This pass covers the part that opens "{opens}". The other parts run as separate passes; stay inside yours.'
BASE = "`git show {base}:<path>`"
NO_BASE = "`git show <sha>:<path>`, with `<sha>` the last commit before the session's first fix to them"
TEMPLATE = """# Independent /codify derivation

Run Steps 0 to 5 of `{root}/.claude/commands/codify.md` for one correction, and return its Report block. Land nothing: change no file outside your own notes. Read-only.

- Session id: `{sid}`.
- Transcript: `{transcript}` (`python3 .claude/tools/session_digest.py` reads spans; `--help` for `--select`).
- The correction: the owner's message sent {date} at {time} UTC, closing "{closing}".{part} Take the scope from the owner's own words.

Start from the evidence packet `.claude/scratch/{folder}/evidence{suffix}.md` (sha256 `{digest}`), extracted by script: the owner's messages up to the correction and the session's calls around them. Read other transcript spans with `session_digest.py` only for what the packet lacks. Treat the session's own account of the cause, in its replies, as a suspect's account: check it against the artifacts. Do not read the session's sealed derivation (`own.md`). Read harness files as they stood before any fix: {base}. Write your notes only under `.claude/scratch/{folder}/delegate{suffix}/`.
"""
PARSE = re.compile(
    r"\A# Independent /codify derivation\n\nRun Steps 0 to 5 of `(?P<root>.+)/\.claude/commands/codify\.md` .*\n\n"
    r"- Session id: `(?P<sid>[^`]+)`\.\n- Transcript: `(?P<transcript>[^`]+)` .*\n"
    r'- The correction: the owner\'s message sent (?P<date>\S+) at (?P<time>\S+) UTC, closing "(?P<closing>.*?)"\.'
    r'(?: This pass covers the part that opens "(?P<opens>.*?)"\. The other parts run as separate passes; stay inside yours\.)?'
    r" Take the scope .*\n\nStart from the evidence packet `[^`]+` \(sha256 `(?P<digest>[0-9a-f]{64})`\), .*Read harness files as they stood before any fix: (?:`git show (?P<base>[^:`<]+):<path>`|`git show <sha>:<path>`.*?)\. "
    r"Write your notes only under `\.claude/scratch/(?P<folder>[^/`]+)/delegate(?P<suffix>[^/`]*)/`\.\n\Z", re.S)


def fold(text: str) -> str:
    return " ".join(str(text).split())


def render(root, sid, transcript, date, time, closing, opens, base, folder, suffix, digest) -> str:
    return TEMPLATE.format(
        root=root, sid=sid, transcript=transcript, date=date, time=time, closing=fold(closing),
        part=PART.format(opens=fold(opens)) if opens else "",
        base=BASE.format(base=base) if base else NO_BASE, folder=folder, suffix=suffix, digest=digest)


def differs(path) -> list:
    """[] when the file is what the tool renders from its own fields; else one line saying how."""
    text = Path(path).read_text(encoding="utf-8")
    found = PARSE.match(text)
    if not found:
        return ["not the tool's template"]
    f = found.groupdict()
    again = render(f["root"], f["sid"], f["transcript"], f["date"], f["time"], f["closing"], f["opens"],
                   f["base"], f["folder"], f["suffix"], f["digest"])
    if again != text:
        return ["differs from the tool's render"]
    directory = Path(f["root"]) / ".claude" / "scratch" / f["folder"]
    if Path(path).resolve() != (directory / f'prompt{f["suffix"]}.md').resolve():
        return ["not at the tool's rendered prompt path"]
    packet = directory / f'evidence{f["suffix"]}.md'
    if not packet.is_file():
        return ["its evidence packet is missing"]
    if hashlib.sha256(packet.read_bytes()).hexdigest() != f["digest"]:
        return ["its evidence packet differs from the extracted one"]
    return []


# Leave room for whole owner messages in Read's default window; the caller can widen the turn window.
CLIP = 600
TURNS = 3
TURN_ROWS = 160


def _reply_rows(row: dict) -> list[str]:
    clock = str(row.get("timestamp", ""))[11:19]
    content = row["message"].get("content") or []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    out = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and (text := fold(block.get("text") or "")):
            out.append(f"- {clock} reply: {text[:CLIP]}")
        elif block.get("type") == "tool_use":
            payload = json.dumps(block.get("input"), ensure_ascii=False)
            out.append(f"- {clock} {block.get('name')}: {payload[:CLIP]}")
    return out


def evidence(owners: list[dict], replies: list[tuple[int, list[str]]], sid: str, turns: int) -> str:
    """Render through the selected correction, with assistant rows grouped by transcript position."""
    first = max(0, len(owners) - turns)
    boundaries = [m["index"] for m in owners]
    turn_rows = [[] for _ in owners[first:]]
    for index, rows in replies:
        k = bisect.bisect_left(boundaries, index)
        if first <= k < len(owners):
            turn_rows[k - first].extend(rows)
    out = ["# /codify evidence packet", "",
           f"Extracted by `codify_prompt.py` from session `{sid}`; the session that made the mistake wrote none of it.",
           "", "## The owner's messages, up to the correction", ""]
    for message in owners:
        out += [f"### {message.get('timestamp', '?')}", "", message["content"], ""]
    out += [f"## The session's replies and calls in the turns before the last {len(turn_rows)} owner messages", ""]
    if first:
        out += [f"Earlier turns, through the owner message at {owners[first - 1]['timestamp']}, are not in this packet; "
                "`session_digest.py` reads them.", ""]
    for message, rows in zip(owners[first:], turn_rows):
        if len(rows) > TURN_ROWS:
            half = TURN_ROWS // 2
            rows = rows[:half] + [f"- ... {len(rows) - TURN_ROWS} rows left out; `session_digest.py` reads them"] + rows[-half:]
        out += [f"### Before {message.get('timestamp', '?')}", "", *rows, ""]
    return "\n".join(out)


def next_folder(scratch: Path, sid8: str, joins_latest: bool) -> str:
    numbers = [int(p.name.rsplit("_", 1)[1]) for p in scratch.glob("codify_%s_*" % sid8)
               if p.name.rsplit("_", 1)[1].isdigit()]
    top = max(numbers, default=0)
    return "codify_%s_%d" % (sid8, top if joins_latest and top else top + 1)


ACCEPTS = ('accepts: --session <id> --at "<YYYY-MM-DD HH:MM>" --closing "<words in that owner message>" '
           '[--part-opens "<words>"] [--base <sha>] [--turns N] [--out-suffix -glm]\n'
           'example: codify_prompt.py --session 5a10d094-0ffb-4cba-b8eb-733dac619b81 --at "2026-11-18 23:40" '
           '--closing "neutral and unbiased, right?"')


def refuse(message: str) -> int:
    print("codify_prompt: %s\n%s" % (message, ACCEPTS), file=sys.stderr)
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check")
    ap.add_argument("--session")
    ap.add_argument("--at")
    ap.add_argument("--closing")
    ap.add_argument("--part-opens")
    ap.add_argument("--base")
    ap.add_argument("--turns", type=int, default=TURNS)
    ap.add_argument("--out-suffix", default="")
    ap.add_argument("--transcript")
    ap.add_argument("--root", default=str(HERE.parent.parent))
    argv = list(sys.argv[1:] if argv is None else argv)
    for i, word in enumerate(argv[:-1]):
        if word == "--out-suffix":
            argv[i:i + 2] = ["--out-suffix=" + argv[i + 1]]
            break
    args = ap.parse_args(argv)
    if args.check:
        for line in differs(args.check):
            print("%s: %s" % (args.check, line))
        return 0
    if not (args.session and args.at and args.closing):
        return refuse("--session, --at and --closing are required")
    transcript = args.transcript
    if not transcript:
        import model_registry
        hits = model_registry.session_transcript_hits(args.session)
        if len(hits) != 1:
            return refuse("%d transcripts for session %s" % (len(hits), args.session))
        transcript = hits[0]
    if args.turns < 1:
        return refuse("--turns must be positive")
    import session_digest

    replies = []

    def collect(index, row):
        if rows := _reply_rows(row):
            replies.append((index, rows))

    owners = [m for m in session_digest.owner_prompts(Path(transcript), on_assistant=collect)
              if m.get("issuer") != "model"]
    minute = args.at.strip().replace(" ", "T")[:16]
    at_minute = [i for i, m in enumerate(owners) if str(m.get("timestamp", ""))[:16] == minute]
    if not at_minute:
        return refuse("no owner message at %s UTC in %s" % (minute, transcript))
    for label, words in (("closing", args.closing), ("part-opens", args.part_opens)):
        if words:
            at_minute = [i for i in at_minute if fold(words) in fold(owners[i]["content"])]
            if not at_minute:
                return refuse("%s words not in the owner message at %s UTC: %r" % (label, minute, words))
    if len(at_minute) != 1:
        return refuse("closing words match more than one owner message; quote a distinct span")
    date, time = minute.split("T")
    root = Path(args.root).resolve()
    scratch = root / ".claude" / "scratch"
    folder = next_folder(scratch, args.session[:8], bool(args.out_suffix))
    posix = lambda p: str(p).replace("\\", "/")
    directory = scratch / folder
    directory.mkdir(parents=True, exist_ok=True)
    packet = directory / f"evidence{args.out_suffix}.md"
    payload = evidence(owners[:at_minute[0] + 1], replies, args.session, args.turns).encode("utf-8")
    packet.write_bytes(payload)
    text = render(posix(root), args.session, posix(transcript), date, time, args.closing, args.part_opens,
                  args.base, folder, args.out_suffix, hashlib.sha256(payload).hexdigest())
    out = directory / f"prompt{args.out_suffix}.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(posix(packet))
    print(posix(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
