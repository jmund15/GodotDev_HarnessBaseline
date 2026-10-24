#!/usr/bin/env python3
"""Selector/detail access for the project_subsystems detail catalog.

The catalog is scan-all-read-few: a session opens the registry YAML for every
subsystem's shape, but the prose detail for only the 1-2 it is touching.
Loading every entry into every plan/audit pays ~24KB to use ~1KB of it.

    index              one line per entry (id + first sentence)
    get ID [ID ...]    full body of the named entries

The index is GENERATED, never stored: a stored index drifts the first time
/sync_subsystems appends an entry without updating it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cli_usage import usage

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CATALOG = Path(__file__).resolve().parent.parent / "skills" / "project_subsystems" / "reference" / "subsystem_details.md"

ENTRY_RE = re.compile(r"^### (`[^`]+`(?: / `[^`]+`)*)$")
ID_RE = re.compile(r"`([^`]+)`")


class Entry:
    def __init__(self, ids: list[str], start: int):
        self.ids = ids
        self.start = start
        self.lines: list[str] = []

    @property
    def first_sentence(self) -> str:
        text = "".join(self.lines).strip()
        text = text.lstrip("#").strip()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            for sep in (". ", " — "):
                head = line.split(sep, 1)[0]
                if len(head) < len(line):
                    return head.strip()
            return line[:100].rstrip() + ("…" if len(line) > 100 else "")
        return ""

    def body(self) -> str:
        header = " / ".join(f"`{i}`" for i in self.ids)
        return f"### {header}\n" + "".join(self.lines).rstrip() + "\n"


def parse() -> list[Entry]:
    if not CATALOG.exists():
        sys.exit(f"catalog not found: {CATALOG}")
    entries: list[Entry] = []
    current: Entry | None = None
    for n, line in enumerate(CATALOG.read_text(encoding="utf-8").splitlines(keepends=True), 1):
        em = ENTRY_RE.match(line.rstrip("\n"))
        if em:
            # Header may carry multiple slash-separated ids, e.g. "### `movement` / `stats`".
            ids = ID_RE.findall(em.group(1))
            current = Entry(ids, n)
            entries.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    return entries


def cmd_index(entries: list[Entry]) -> None:
    print(f"# project_subsystems — detail index ({len(entries)} entries)")
    print("# Fetch the body of any entry that bears on the work:")
    print("#   python3 .claude/tools/subsys.py get <id> [<id> ...]")
    for e in entries:
        label = " / ".join(e.ids)
        sentence = f" — {e.first_sentence}" if e.first_sentence else ""
        print(f"{label}{sentence}")


def cmd_get(entries: list[Entry], ids: list[str]) -> None:
    wanted = set(ids)
    want = [e for e in entries if wanted & set(e.ids)]
    found_ids = {i for e in want for i in e.ids}
    missing = sorted(wanted - found_ids)
    for e in want:
        print(e.body())
    if missing:
        print(f"no such entry: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    g = sub.add_parser("get")
    g.add_argument("ids", nargs="+")
    args = ap.parse_args()

    entries = parse()
    if args.cmd == "index":
        cmd_index(entries)
    else:
        cmd_get(entries, args.ids)


if __name__ == "__main__":
    main()
