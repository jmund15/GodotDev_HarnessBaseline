#!/usr/bin/env python3
"""Selector/detail access for the agent-template registries under commands/agents/.

Every registry is scan-all-read-few: a command must see which lenses exist to pick the few its
gating admits, but needs the prompt body of only those. Reading a registry whole to dispatch a
subset pays for the templates that were gated OUT — on plan_check_agents.md that is ~13KB of 40KB
on every single plan check.

    index [REGISTRY]        one line per lens (key, model, gate, purpose) — all registries or one
    shared REGISTRY         the shared preamble every lens of that registry needs
    get [--shared] KEY...   verbatim template block(s); --shared prepends the preamble

Registries: review | session_audit | explore | plan_check | structure_audit
Keys are globally unique (prefix per registry), so `get` needs no registry argument.

Templates stay in their registry file — this reads, never rewrites. The index is generated at read
time, so it cannot drift from the templates it lists.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENTS = Path(__file__).resolve().parent.parent / "commands" / "agents"

REGISTRIES = {
    "review": ("review_agents.md", ""),
    "session_audit": ("session_audit_agents.md", "sa-"),
    "explore": ("explore_agents.md", "exp-"),
    "plan_check": ("plan_check_agents.md", "plc-"),
    "structure_audit": ("structure_audit_agents.md", "stra-"),
}

MODEL_RE = re.compile(r'model:\s*"(\w+)"')
PURPOSE_RE = re.compile(r"^\s*\(([^)]{3,160})\)")
GATE_MARKERS = (
    ("PLAN_SHAPE == meta", "meta-only"),
    ("CONDITIONAL", "conditional"),
    ("OMITTED", "omittable"),
    ("floor lens", "floor"),
)
PURPOSE_CAP = 96


class Lens:
    def __init__(self, key: str, descriptor: str, registry: str):
        self.key = key
        self.descriptor = descriptor
        self.registry = registry
        self.lines: list[str] = []

    @property
    def model(self) -> str:
        m = MODEL_RE.search(self.descriptor)
        return m.group(1) if m else "-"

    @property
    def gate(self) -> str:
        hits = [label for marker, label in GATE_MARKERS if marker in self.descriptor]
        return ",".join(hits) if hits else "always"

    @property
    def purpose(self) -> str:
        m = PURPOSE_RE.match(self.descriptor)
        text = m.group(1) if m else self.descriptor
        text = re.sub(r"[`*]", "", text).strip()
        if len(text) > PURPOSE_CAP:
            text = text[:PURPOSE_CAP].rsplit(" ", 1)[0] + "…"
        return text

    @property
    def size(self) -> int:
        return sum(len(l.encode("utf-8")) for l in self.lines)

    def body(self) -> str:
        return f"### {self.key} {self.descriptor}\n" + "".join(self.lines).rstrip() + "\n"


def parse(registry: str) -> tuple[str, list[Lens]]:
    filename, prefix = REGISTRIES[registry]
    path = AGENTS / filename
    if not path.exists():
        sys.exit(f"registry file not found: {path}")
    head_re = re.compile(rf"^### ({re.escape(prefix)}[\w-]+)\s*(.*)$")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lenses: list[Lens] = []
    current: Lens | None = None
    first = len(lines)
    for n, line in enumerate(lines):
        m = head_re.match(line)
        if m:
            if current is None:
                first = n
            current = Lens(m.group(1), m.group(2).strip(), registry)
            lenses.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    return "".join(lines[:first]), lenses


def cmd_index(which: list[str]) -> None:
    print("# Agent-template selector index — pick keys, then:")
    print("#   python3 .claude/tools/lens.py get --shared <KEY> [<KEY> ...]")
    print("# `--shared` prepends the registry preamble every lens of that registry needs.")
    for reg in which:
        preamble, lenses = parse(reg)
        total = len(preamble.encode("utf-8")) + sum(l.size for l in lenses)
        print(f"\n## {reg}  ({REGISTRIES[reg][0]}, {total:,}B — preamble "
              f"{len(preamble.encode('utf-8')):,}B + {len(lenses)} lenses)")
        for l in lenses:
            print(f"{l.key:30} {l.model:8} {l.gate:22} {l.size:6,}B  {l.purpose}")


def cmd_shared(reg: str) -> None:
    preamble, _ = parse(reg)
    sys.stdout.write(preamble)


def cmd_get(keys: list[str], with_shared: bool) -> None:
    found: dict[str, Lens] = {}
    preambles: dict[str, str] = {}
    for reg in REGISTRIES:
        preamble, lenses = parse(reg)
        preambles[reg] = preamble
        for l in lenses:
            found[l.key] = l

    want = [found[k] for k in keys if k in found]
    missing = [k for k in keys if k not in found]
    if missing:
        print(f"<!-- no such lens: {', '.join(missing)} — run `lens.py index` -->")
    if not want:
        return

    if with_shared:
        for reg in dict.fromkeys(l.registry for l in want):
            if preambles[reg].strip():
                print(f"<!-- shared preamble: {reg} -->")
                sys.stdout.write(preambles[reg].rstrip() + "\n\n")
    for l in want:
        print(f"<!-- registry: {l.registry} -->")
        print(l.body())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("index")
    i.add_argument("registry", nargs="?", choices=sorted(REGISTRIES))
    s = sub.add_parser("shared")
    s.add_argument("registry", choices=sorted(REGISTRIES))
    g = sub.add_parser("get")
    g.add_argument("keys", nargs="+")
    g.add_argument("--shared", action="store_true")
    args = ap.parse_args()

    if args.cmd == "index":
        cmd_index([args.registry] if args.registry else sorted(REGISTRIES))
    elif args.cmd == "shared":
        cmd_shared(args.registry)
    else:
        cmd_get(args.keys, args.shared)


if __name__ == "__main__":
    main()
