#!/usr/bin/env python3
"""The finding bar: which findings count at a run's rigor, and the seat-facing words that say so.

A finding's `minRigor` is the lowest rigor whose bar admits it. It counts at a run when its rank is at most the
run's rank. `critical`, ASK and an open PLAN always count; so does an absent or unrecognized tag. Owned by
`skills/orchestration/reference/rigor.md`, which carries the same words.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402

RIGORS = triage.RIGORS
BAR_PHRASES = (
    "it is a defect that forces rework or a wrong result",
    "it also is a significant gap",
    "the Reporting Filter admits it at all",
    "The bar decides which findings owe a later round and move a verdict, never what you report or where you look.",
    "in doubt between two, take the lower",
)
_CLAUSE = {
    "light": "a defect that forces rework or a wrong result",
    "normal": "a defect that forces rework or a wrong result, or a significant gap",
    "thorough": "any defect, gap or improvement the Reporting Filter admits",
}


def counts(finding, rigor):
    if rigor not in RIGORS or finding.get("critical") or finding.get("action") == "ASK":
        return True
    if finding.get("action") == "PLAN" and not any(str(o or "").strip() for o in finding.get("options") or ()):
        return True
    tag = finding.get("minRigor")
    if tag not in RIGORS:
        return True
    return RIGORS.index(tag) <= RIGORS.index(rigor)


def below(finding, rigor):
    return not counts(finding, rigor)


def stamp(rows, rigor):
    """Copies of `rows`, each with `counts` at `rigor`, so a fold defers by the field and never judges the bar; the
    rows unchanged when `rigor` is unknown."""
    if rigor not in RIGORS:
        return rows
    return [dict(row, counts=counts(row, rigor)) for row in rows]


def strongest(members):
    tags = [member.get("minRigor") for member in members]
    if not tags or any(tag not in RIGORS for tag in tags):
        return None
    return min(tags, key=RIGORS.index)


def set_strongest(row, members):
    tag = strongest(members)
    if tag is None:
        row.pop("minRigor", None)
    else:
        row["minRigor"] = tag


def run_rigor(task_id, root, session_id=None):
    try:
        return triage.rigor(task_id, root, session_id)
    except (OSError, ValueError):
        return None


def bar_contract(rigor):
    if rigor not in RIGORS:
        return ""
    return (
        "BAR: this run's rigor is %s. A finding counts at that rigor when it is %s. Report every finding the "
        "Reporting Filter admits, counting or not, and set `minRigor` on each to the lowest of light, normal or "
        "thorough whose bar admits it; in doubt between two, take the lower level (light before normal before "
        "thorough, so the finding counts at more runs); never lower a `critical` finding. The bar decides which "
        "findings owe a later round and move a verdict, never what you report or where you look: read and search "
        "exactly as widely as your mandate asks." % (rigor, _CLAUSE[rigor])
    )


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Stamp a standalone fold's findings file with `counts` at the run's "
                                     "rigor and print its `Bar:` line; nothing is stamped or printed when no rigor reads.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("stamp")
    cmd.add_argument("findings", help="a JSON array of findings, or an object holding one under `findings`")
    cmd.add_argument("--task", default=None)
    args = parser.parse_args(argv)
    path = Path(args.findings)
    data = json.loads(path.read_text(encoding="utf-8"))
    rigor = run_rigor(args.task, Path(__file__).resolve().parents[2])
    if rigor not in RIGORS:
        return 0
    if isinstance(data, dict):
        data["findings"] = stamp(data.get("findings") or [], rigor)
    else:
        data = stamp(data, rigor)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("Bar: " + rigor)
    return 0


if __name__ == "__main__":
    sys.exit(main())
