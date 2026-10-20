#!/usr/bin/env python3
"""Selector/detail access for the agent-template registries under commands/agents/.

Every registry is scan-all-read-few: a command must see which lenses exist to pick the few its
gating admits, but needs the prompt body of only those. Reading a registry whole to dispatch a
subset pays for the templates that were gated OUT — on plan_check_agents.md that is ~13KB of 40KB
on every single plan check.

    index [REGISTRY]        one line per lens (key, gate, tier, size, purpose) — all registries or one
    shared REGISTRY         the shared preamble every lens of that registry needs
    get [--shared] KEY...   verbatim template block(s); --shared prepends the preamble

Registries: review | session_audit | explore | plan_check | structure_audit | redteam
Keys are globally unique (prefix per registry), so `get` needs no registry argument.

A template's header block (between its `###` heading and its fenced prompt) may declare
`preload: <path>` lines, relative to `.claude/`. `get`, with or without `--shared`, appends each
declared file once per output, every line blockquoted under `> Preloaded doctrine: <path>`, so a
lens receives the doctrine its mandate cites. `index` sizes include those bytes. A declared path
that does not resolve exits 2 naming it. A project may attach more files to a lens through `adaptation.json`
`lens_preloads` ({lens key: [path]}); they follow the declared ones, each file once.

A header block also declares `mandates: <key>, <key>` (one line): the review-ledger catalog keys
(adaptation.json `review_mandates`) the lens owns. `Lens.mandates` lists them; `index` prints them; the
engine and `review_ledger.py` expand a seat's lenses through them. `review_ledger.py keys --check` judges
the declarations, and `review_ledger.rule_hash` hashes one key's rule text: its home plus every file a lens
declaring the key preloads (`review_ledger.mandate_doctrine`), so a mandate's doctrine dependencies have
this one source.

A header line `admission: <label>[, <label>]` lists the lens's admission labels (`Lens.admission`); a
`/session_audit` lens is conditional when `conditional` is one of them. `execution_depth.md` §Mandate admission
stays the one home of when such a lens runs, and `review_ledger.py` leaves a conditional lens's mandates out of
the owed set a roster does not name.

A header line `kfm: index` declares that the lens reads the known-failure-modes catalog. `Lens.kfm` is
true for it, and `lens_briefs.py` materializes and checks the catalog snapshot and puts its index path in
that lens's brief (`kfm.py` owns the snapshot).

A header line `citers: plan` declares that the lens judges a plan's changed surfaces. `Lens.citers` is `"plan"`
for it and `""` otherwise, and `lens_briefs.py` runs `citers.py` over each meta subject plan's `surfaces` block and
embeds the result as `INBOUND_REFS` in that lens's brief.

Each lens carries the tier its seat reviews at: its registry's `REGISTRY_TIERS` value unless
`LENS_TIERS` names it. A plan or design review is architect work, a review of code or concrete
artifacts executor work, an anchored check fanout work. `seat_tier` is the one reader: the provider
guard and review_fanout.js fill a seat that declares no tier from it.

Templates stay in their registry file — this reads, never rewrites. The index is generated at read
time, so it cannot drift from the templates it lists.
"""
from __future__ import annotations

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
from cli_usage import usage



import argparse
import re
import sys
from pathlib import Path

import adaptation  # noqa: E402

AGENTS = Path(__file__).resolve().parent.parent / "commands" / "agents"
DOCTRINE_ROOT = Path(__file__).resolve().parent.parent

_PREFIXES = adaptation.get(DOCTRINE_ROOT, "lens_prefixes")
EXTRA_PRELOADS = adaptation.get(DOCTRINE_ROOT, "lens_preloads")

REGISTRIES = {
    "review": ("review_agents.md", _PREFIXES.get("review", "")),
    "session_audit": ("session_audit_agents.md", "sa-"),
    "explore": ("explore_agents.md", "exp-"),
    "plan_check": ("plan_check_agents.md", "plc-"),
    "structure_audit": ("structure_audit_agents.md", "stra-"),
    "redteam": ("redteam_agents.md", "rt-"),
}

TEMPLATES_RE = re.compile(r"^## (Agent|Lens) Templates\b")

REGISTRY_TIERS = {
    "review": "executor",
    "session_audit": "executor",
    "explore": "fanout",
    "plan_check": "architect",
    "structure_audit": "executor",
    "redteam": "architect",
}
LENS_TIERS = {
    "stra-layout-hygiene": "fanout",
    "stra-reference-integrity": "fanout",
    "plc-evidence-grounding": "fanout",
    "plc-instruction-quality": "fanout",
    "sa-harness-quality": "fanout",
    "sa-architecture-sweep": "architect",
    "sa-holistic": "architect",
}

PURPOSE_RE = re.compile(r"^\s*\(([^)]{3,160})\)")
GATE_MARKERS = (
    ("— code", "code"),
    ("meta or inseparable mixed code/meta", "meta-or-mixed"),
    ("PLAN_SHAPE == meta", "meta-only"),
    ("CONDITIONAL", "conditional"),
    ("floor lens", "floor"),
    ("— triggered", "triggered"),
    ("mode only", "mode-gated"),
)
PURPOSE_CAP = 96
PRELOAD_RE = re.compile(r"^preload:\s*(\S+)\s*$")
MANDATES_RE = re.compile(r"^mandates:\s*(.+?)\s*$")
KFM_RE = re.compile(r"^kfm:\s*index\s*$")
CITERS_RE = re.compile(r"^citers:\s*plan\s*$")
ADMISSION_RE = re.compile(r"^admission:\s*(.+?)\s*$")


class Lens:
    def __init__(self, key: str, descriptor: str, registry: str):
        self.key = key
        self.descriptor = descriptor
        self.registry = registry
        self.lines: list[str] = []

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
    def tier(self) -> str:
        return LENS_TIERS.get(self.key) or REGISTRY_TIERS[self.registry]

    def _header_len(self) -> int:
        """Lines before the first fence: the block where `preload:` declarations live."""
        return next((n for n, line in enumerate(self.lines) if line.startswith("```")), len(self.lines))

    @property
    def preloads(self) -> list[str]:
        found = (PRELOAD_RE.match(line) for line in self.lines[:self._header_len()])
        declared = [m.group(1) for m in found if m]
        return list(dict.fromkeys(declared + list(EXTRA_PRELOADS.get(self.key) or [])))

    @property
    def mandates(self) -> list[str]:
        found = (MANDATES_RE.match(line) for line in self.lines[:self._header_len()])
        return [key for m in found if m for key in re.split(r"[,\s]+", m.group(1)) if key]

    @property
    def admission(self) -> list[str]:
        found = (ADMISSION_RE.match(line) for line in self.lines[:self._header_len()])
        return [label for m in found if m for label in re.split(r"[,\s]+", m.group(1)) if label]

    @property
    def kfm(self) -> bool:
        return any(KFM_RE.match(line) for line in self.lines[:self._header_len()])

    @property
    def citers(self) -> str:
        return "plan" if any(CITERS_RE.match(line) for line in self.lines[:self._header_len()]) else ""

    @property
    def size(self) -> int:
        return sum(len(l.encode("utf-8")) for l in self.lines) + len(doctrine([self]).encode("utf-8"))

    def body(self) -> str:
        cut = self._header_len()
        kept = [l for l in self.lines[:cut] if not (PRELOAD_RE.match(l) or MANDATES_RE.match(l) or KFM_RE.match(l)
                                                   or CITERS_RE.match(l)
                                                   or ADMISSION_RE.match(l))] + self.lines[cut:]
        return f"### {self.key} {self.descriptor}\n" + "".join(kept).rstrip() + "\n"


def preload_block(rel: str) -> str:
    """One declared file, every line blockquoted: the read-only profile scan and the nested-fan-out
    guard read quoted lines as data, so the file's own commands never become the seat's."""
    path = DOCTRINE_ROOT / rel
    if not path.is_file():
        print(f"preload path does not resolve: {rel} (relative to {DOCTRINE_ROOT})", file=sys.stderr)
        raise SystemExit(2)
    quoted = "\n".join(f"> {line}".rstrip() if line.strip() else ">"
                       for line in path.read_text(encoding="utf-8").splitlines())
    return (f"> Preloaded doctrine: {rel}\n"
            "> Reference for your judgment, quoted whole: cite its sections, and leave its commands "
            "and file lookups to the parent session.\n>\n" + quoted + "\n")


def doctrine(lenses: list[Lens]) -> str:
    """Every file the given lenses declare, each once, in declaration order; "" when none."""
    rels = list(dict.fromkeys(rel for l in lenses for rel in l.preloads))
    return "\n".join(preload_block(rel) for rel in rels)


def parse(registry: str, agents: Path | None = None) -> tuple[str, list[Lens]]:
    filename, prefix = REGISTRIES[registry]
    path = (agents or AGENTS) / filename
    if not path.exists():
        sys.exit(f"registry file not found: {path}")
    head_re = re.compile(rf"^### ({re.escape(prefix)}[a-z0-9][a-z0-9-]*)\s*(.*)$")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lenses: list[Lens] = []
    current: Lens | None = None
    first = len(lines)
    # Lenses start under the templates heading; `###` sections above it belong to the preamble.
    start = next((n for n, line in enumerate(lines) if TEMPLATES_RE.match(line)), 0)
    for n, line in enumerate(lines):
        m = head_re.match(line) if n > start or not start else None
        if m:
            if current is None:
                first = n
            current = Lens(m.group(1), m.group(2).strip(), registry)
            lenses.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    return "".join(lines[:first]), lenses


def registry_of(key: str) -> str | None:
    """The registry whose key prefix the lens key carries; the longest prefix wins, so an empty
    prefix (a registry whose headings share none) takes only the keys no other prefix claims."""
    ranked = sorted(REGISTRIES.items(), key=lambda item: -len(item[1][1]))
    return next((name for name, (_file, prefix) in ranked if key.startswith(prefix)), None)


def seat_tier(key) -> str | None:
    """The tier of a seat key: one lens, or `a+b` lenses on one seat, which takes the strongest.

    None when the key is not a string or any part names no lens in its prefix's registry file.
    """
    if not isinstance(key, str) or not key.strip():
        return None
    import model_registry  # sibling module; imported late so `index`/`get` never need it
    tiers = []
    for part in key.split("+"):
        reg = registry_of(part)
        if reg is None or not (AGENTS / REGISTRIES[reg][0]).exists():
            return None
        found = next((l for l in parse(reg)[1] if l.key == part), None)
        if found is None:
            return None
        tiers.append(found.tier)
    return min(tiers, key=model_registry.TIER_ORDER.index)


def seat_mandates(key) -> list[str] | None:
    """The catalog keys a seat key carries: its lenses' `mandates:` in seat order, each once.

    None when the key is not a string or any part names no lens in its prefix's registry file.
    """
    if not isinstance(key, str) or not key.strip():
        return None
    keys: list[str] = []
    for part in key.split("+"):
        reg = registry_of(part)
        if reg is None or not (AGENTS / REGISTRIES[reg][0]).exists():
            return None
        found = next((l for l in parse(reg)[1] if l.key == part), None)
        if found is None:
            return None
        keys.extend(k for k in found.mandates if k not in keys)
    return keys


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
            keys = f"  [{','.join(l.mandates)}]" if l.mandates else ""
            print(f"{l.key:30} {l.gate:22} {l.tier:10} {l.size:6,}B  {l.purpose}{keys}")


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
        print(f"no such lens: {', '.join(missing)} — run `lens.py index`", file=sys.stderr)
        raise SystemExit(2)
    if not want:
        return

    if with_shared:
        for reg in dict.fromkeys(l.registry for l in want):
            if preambles[reg].strip():
                print(f"<!-- shared preamble: {reg} -->")
                sys.stdout.write(preambles[reg].rstrip() + "\n\n")
    blocks = doctrine(want)
    for l in want:
        print(f"<!-- registry: {l.registry} -->")
        print(l.body())
    if blocks:
        print(blocks)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=usage(__doc__),
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
