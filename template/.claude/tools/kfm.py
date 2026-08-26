#!/usr/bin/env python3
"""Selector/detail access for the known-failure-modes catalog.

The catalog is scan-all-read-few: a reviewer must see every entry's trigger to
know which ones bear, but needs the body of only the handful that fire. Loading
all 48 bodies into every fan-out lens pays ~49KB per agent to use ~6KB of it.

    index              one line per entry (id + name + trigger), grouped by section
    get ID [ID ...]    full body of the named entries
    get -s SUBSTR      full bodies of every entry in the matching section
    next-id            lowest unused id (never trust a cached max)

The index is GENERATED, never stored: an index file would drift the first time
autolearn appended without updating it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CATALOG = Path(__file__).resolve().parent.parent / "reference" / "known_failure_modes_entries.md"

ENTRY_RE = re.compile(r"^### (\d+)\. (.+)$")
SECTION_RE = re.compile(r"^## (.+)$")
TRIGGER_RE = re.compile(r"^\*\*Catches you when\*\*:\s*(.+)$")
TRIGGER_CAP = 84


class Entry:
    def __init__(self, eid: int, name: str, section: str, start: int):
        self.id = eid
        self.name = name
        self.section = section
        self.start = start
        self.lines: list[str] = []

    @property
    def trigger(self) -> str:
        for line in self.lines:
            m = TRIGGER_RE.match(line)
            if not m:
                continue
            text = m.group(1).strip()
            for sep in (" — ", ". "):
                head = text.split(sep, 1)[0]
                if len(head) < len(text):
                    text = head
                    break
            if len(text) > TRIGGER_CAP:
                text = text[:TRIGGER_CAP].rsplit(" ", 1)[0] + "…"
            return text.rstrip(".")
        return ""

    def body(self) -> str:
        return f"### {self.id}. {self.name}\n" + "".join(self.lines).rstrip() + "\n"


def parse() -> list[Entry]:
    if not CATALOG.exists():
        sys.exit(f"catalog not found: {CATALOG}")
    entries: list[Entry] = []
    section = ""
    current: Entry | None = None
    for n, line in enumerate(CATALOG.read_text(encoding="utf-8").splitlines(keepends=True), 1):
        sm = SECTION_RE.match(line)
        if sm:
            section = sm.group(1).strip()
            current = None
            continue
        em = ENTRY_RE.match(line)
        if em:
            current = Entry(int(em.group(1)), em.group(2).strip(), section, n)
            entries.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    return entries


def cmd_index(entries: list[Entry]) -> None:
    dupes = {e.id for e in entries if sum(1 for o in entries if o.id == e.id) > 1}
    print(f"# Known failure modes — selector index ({len(entries)} entries)")
    print("# Scan every line. For each that plausibly bears on the work, fetch the body:")
    print("#   python3 .claude/tools/kfm.py get <ID> [<ID> ...]   |   kfm.py get -s <section-substring>")
    print("# Fetching is cheap; a missed entry is the failure this catalog exists to prevent. When in doubt, fetch.")
    section = None
    for e in entries:
        if e.section != section:
            section = e.section
            print(f"\n## {section}")
        flag = "  [DUPLICATE-ID]" if e.id in dupes else ""
        trigger = f" — {e.trigger}" if e.trigger else ""
        print(f"#{e.id} {e.name}{trigger}{flag}")


def cmd_get(entries: list[Entry], ids: list[str], section: str | None) -> None:
    if section:
        want = [e for e in entries if section.lower() in e.section.lower()]
        if not want:
            sys.exit(f"no section matching {section!r}; sections: " + ", ".join(dict.fromkeys(e.section for e in entries)))
    else:
        try:
            wanted = [int(i.lstrip("#")) for i in ids]
        except ValueError:
            sys.exit("ids must be integers")
        want = [e for e in entries if e.id in wanted]
        missing = sorted(set(wanted) - {e.id for e in want})
        if missing:
            print(f"<!-- no such entry: {', '.join(f'#{m}' for m in missing)} -->")
    for e in want:
        print(f"<!-- section: {e.section} -->")
        print(e.body())


def cmd_next_id(entries: list[Entry]) -> None:
    used = {e.id for e in entries}
    n = 1
    while n in used:
        n += 1
    print(n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    g = sub.add_parser("get")
    g.add_argument("ids", nargs="*")
    g.add_argument("-s", "--section")
    sub.add_parser("next-id")
    args = ap.parse_args()

    entries = parse()
    if args.cmd == "index":
        cmd_index(entries)
    elif args.cmd == "get":
        if not args.ids and not args.section:
            sys.exit("get needs ids or --section")
        cmd_get(entries, args.ids, args.section)
    else:
        cmd_next_id(entries)


if __name__ == "__main__":
    main()
