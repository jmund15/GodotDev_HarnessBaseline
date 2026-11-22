#!/usr/bin/env python3
"""Write the prompt for one independent /codify derivation from a fixed template.

Usage:
    codify_prompt.py --session <id> --at "<YYYY-MM-DD HH:MM>" --closing "<owner words>"
                     [--part-opens "<owner words>"] [--base <sha>] [--out-suffix -glm] [--transcript <path>]
    codify_prompt.py --check <prompt.md>

`--at` is the correction's UTC minute. `--closing` and `--part-opens` must appear in the owner message
sent at that minute (whitespace folded); words that do not are refused and nothing is written. The owner
messages come from `session_digest.owner_prompts`, the reader the digest and the compaction anchor share.

Writes `.claude/scratch/codify_<sid8>_<n>/prompt<suffix>.md`, `<n>` one past the highest existing folder for
the session; with `--out-suffix` it joins the highest existing folder instead, so the plain and `-glm`
prompts of one pass sit together. The template carries pointers only: no account of what the session did.

`--check` re-renders a prompt from the fields it holds and prints a line when the file differs from
that render (a hand edit). `differs(path)` is the same check for `dispatch_size.py`'s preflight.
"""
from __future__ import annotations

import argparse
import os
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

Read the transcript up to that correction. Treat the session's own account of the cause, in its replies, as a suspect's account: check it against the artifacts. Read harness files as they stood before any fix: {base}. Stay out of `.claude/scratch/{folder}/` except to write your notes under `.claude/scratch/{folder}/delegate{suffix}/`.
"""
PARSE = re.compile(
    r"\A# Independent /codify derivation\n\nRun Steps 0 to 5 of `(?P<root>.+)/\.claude/commands/codify\.md` .*\n\n"
    r"- Session id: `(?P<sid>[^`]+)`\.\n- Transcript: `(?P<transcript>[^`]+)` .*\n"
    r'- The correction: the owner\'s message sent (?P<date>\S+) at (?P<time>\S+) UTC, closing "(?P<closing>.*?)"\.'
    r'(?: This pass covers the part that opens "(?P<opens>.*?)"\. The other parts run as separate passes; stay inside yours\.)?'
    r" Take the scope .*\n\n.*Read harness files as they stood before any fix: (?:`git show (?P<base>[^:`<]+):<path>`|`git show <sha>:<path>`.*?)\. "
    r"Stay out of `\.claude/scratch/(?P<folder>[^/`]+)/` .* `\.claude/scratch/[^/`]+/delegate(?P<suffix>[^/`]*)/`\.\n\Z", re.S)


def fold(text: str) -> str:
    return " ".join(str(text).split())


def render(root, sid, transcript, date, time, closing, opens, base, folder, suffix) -> str:
    return TEMPLATE.format(
        root=root, sid=sid, transcript=transcript, date=date, time=time, closing=fold(closing),
        part=PART.format(opens=fold(opens)) if opens else "",
        base=BASE.format(base=base) if base else NO_BASE, folder=folder, suffix=suffix)


def differs(path) -> list:
    """[] when the file is what the tool renders from its own fields; else one line saying how."""
    text = Path(path).read_text(encoding="utf-8")
    found = PARSE.match(text)
    if not found:
        return ["not the tool's template"]
    f = found.groupdict()
    again = render(f["root"], f["sid"], f["transcript"], f["date"], f["time"], f["closing"], f["opens"],
                   f["base"], f["folder"], f["suffix"])
    return [] if again == text else ["differs from the tool's render"]


def owner_messages(transcript: Path) -> list:
    import session_digest
    return session_digest.owner_prompts(transcript)


def message_at(transcript: Path, at: str) -> list:
    minute = at.strip().replace(" ", "T")[:16]
    return [m["content"] for m in owner_messages(transcript) if str(m.get("timestamp", ""))[:16] == minute]


def next_folder(scratch: Path, sid8: str, joins_latest: bool) -> str:
    numbers = [int(p.name.rsplit("_", 1)[1]) for p in scratch.glob("codify_%s_*" % sid8)
               if p.name.rsplit("_", 1)[1].isdigit()]
    top = max(numbers, default=0)
    return "codify_%s_%d" % (sid8, top if joins_latest and top else top + 1)


ACCEPTS = ('accepts: --session <id> --at "<YYYY-MM-DD HH:MM>" --closing "<words in that owner message>" '
           '[--part-opens "<words>"] [--base <sha>] [--out-suffix -glm]\n'
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
    messages = message_at(Path(transcript), args.at)
    if not messages:
        return refuse("no owner message at %s UTC in %s" % (args.at, transcript))
    for label, words in (("closing", args.closing), ("part-opens", args.part_opens)):
        if words and not any(fold(words) in fold(m) for m in messages):
            return refuse("%s words not in the owner message at %s UTC: %r" % (label, args.at, words))
    date, _, time = args.at.strip().replace("T", " ").partition(" ")
    root = Path(args.root).resolve()
    scratch = root / ".claude" / "scratch"
    folder = next_folder(scratch, args.session[:8], bool(args.out_suffix))
    posix = lambda p: str(p).replace("\\", "/")
    text = render(posix(root), args.session, posix(transcript), date, time[:5], args.closing, args.part_opens,
                  args.base, folder, args.out_suffix)
    out = scratch / folder / ("prompt%s.md" % args.out_suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(posix(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
