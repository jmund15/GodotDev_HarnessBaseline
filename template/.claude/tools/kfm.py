#!/usr/bin/env python3
"""Selector/detail access for the known-failure-modes catalog.

The catalog is scan-all-read-few: a reviewer must see every entry's trigger to
know which ones bear, but needs the body of only the handful that fire. Loading
all 48 bodies into every fan-out lens pays ~49KB per agent to use ~6KB of it.

    index              one line per entry (id + name + trigger), grouped by section
    get ID [ID ...]    full body of the named entries
    get -s SUBSTR      full bodies of every entry in the matching section
    materialize --out .claude/scratch/<run>/kfm
                       generated index and separate Read-able body per entry
    check --dir .claude/scratch/<run>/kfm
                       fail if catalog or a body changed after materialization
    next-id            lowest unused id (never trust a cached max)

The index is GENERATED, never stored: an index file would drift the first time
autolearn appended without updating it.
"""
from __future__ import annotations

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
from cli_usage import usage



import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CATALOG = Path(__file__).resolve().parent.parent / "reference" / "known_failure_modes_entries.md"
ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / ".claude" / "scratch"

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


def snapshot_is_current(directory: Path, catalog_path: Path) -> bool:
    directory = Path(directory)
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        entries = manifest["entries"]
        if not entries or manifest["catalogSha256"] != hashlib.sha256(catalog_path.read_bytes()).hexdigest():
            return False
        index = (directory / "index.md").read_text(encoding="utf-8")
        if manifest["catalogSha256"] not in index:
            return False
        expected = {"manifest.json", "index.md", *(row["file"] for row in entries)}
        if {child.name for child in directory.iterdir()} != expected:
            return False
        for row in entries:
            body = (directory / row["file"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != row["sha256"] or row["path"] not in index:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _shown(path: Path) -> str:
    """A snapshot path as briefs name it: repository-relative inside the checkout, else absolute."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def cmd_materialize(entries: list[Entry], out: str) -> None:
    directory = Path(out).resolve()
    try:
        directory.relative_to(SCRATCH.resolve())
    except ValueError:
        sys.exit("materialize output must be inside .claude/scratch")
    print(materialize(entries, directory))


def materialize(entries: list[Entry], directory: Path) -> str:
    """Publish the snapshot at `directory` (or accept a current one there) and return its index path.

    The `.claude/scratch` confinement is the CLI's; an in-process caller owns its directory choice."""
    directory = Path(directory).resolve()
    if directory.exists():
        if snapshot_is_current(directory, CATALOG):
            return _shown(directory / "index.md")
        sys.exit("materialize output exists but is stale or contains unrelated files; use a new run directory")
    if len({entry.id for entry in entries}) != len(entries):
        sys.exit("duplicate catalog IDs; cannot materialize without losing an entry")
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}.staging-", dir=directory.parent))
    manifest = {"catalogSha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(), "entries": []}
    lines = [f"# Known failure modes — {len(entries)} entries; catalog-sha256: {manifest['catalogSha256']}",
             "# Scan every trigger. Read only the body paths whose triggers can bear on this task."]
    section = None
    for entry in entries:
        if entry.section != section:
            section = entry.section
            lines.extend(("", f"## {section}"))
        filename = f"{entry.id}.md"
        body = f"<!-- section: {section} -->\n{entry.body()}"
        (staging / filename).write_text(body, encoding="utf-8", newline="\n")
        path = _shown(directory / filename)
        manifest["entries"].append({"id": entry.id, "file": filename, "path": path,
                                    "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})
        lines.append(f"#{entry.id} {entry.name} — {entry.trigger} — Read: {path}")
    (staging / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                            encoding="utf-8", newline="\n")
    try:
        os.replace(staging, directory)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if not snapshot_is_current(directory, CATALOG):
            raise
    return _shown(directory / "index.md")


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
    ap = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    g = sub.add_parser("get")
    g.add_argument("ids", nargs="*")
    g.add_argument("-s", "--section")
    m = sub.add_parser("materialize")
    m.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    sub.add_parser("next-id")
    args = ap.parse_args()

    entries = parse()
    if args.cmd == "index":
        cmd_index(entries)
    elif args.cmd == "get":
        if not args.ids and not args.section:
            sys.exit("get needs ids or --section")
        cmd_get(entries, args.ids, args.section)
    elif args.cmd == "materialize":
        cmd_materialize(entries, args.out)
    elif args.cmd == "check":
        if not snapshot_is_current(Path(args.dir), CATALOG):
            sys.exit("KFM snapshot stale or incomplete; materialize into a new run directory")
        print("CURRENT")
    else:
        cmd_next_id(entries)


if __name__ == "__main__":
    main()
