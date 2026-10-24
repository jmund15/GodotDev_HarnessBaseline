#!/usr/bin/env python3
"""roadmap_pos.py — compute every Part's Pos from its Deps (roadmap_schema.md §6.2 *Pos*).

    python3 .claude/tools/roadmap_pos.py <roadmap.md> [--apply]

Prints one line per Part whose Pos or table position changes, then a summary; exit 1 when anything
would change, 0 when the roadmap already matches. `--apply` writes the result: Pos cells, row order
(sequenced by Pos, the divider, then the un-sequenced rows) and the Mermaid node labels (`N: Name`).

The rule it applies:
  - `idea-pending`, `workshop-pending` and `abandoned` Parts are un-sequenced (`—`), and so is any Part
    whose Deps include an un-sequenced Part: its place is unknown until that Dep is placed.
  - `complete` Parts are placed first, so finished work reads first: each after its complete Deps.
  - Every other Part sits at least one stage after its highest Dep and after the last complete Part.
  - A Pos already later than its floor is kept: the owner moved it later for priority.
  - Pos values are then renumbered to a contiguous 1..N, keeping their order.
Cross-folder Deps (`path#part`) carry no Pos here and add no constraint. Mermaid labels are rewritten
only inside ```mermaid fences.
"""
import re
import sys

UNSEQUENCED_STATES = {"idea-pending", "workshop-pending", "abandoned"}
DASH = "—"


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(text):
    """(lines, start, end, header, rows): the Parts table spans lines[start:end]; rows are dicts with
    `line` (index), `cells`, `part`, `state`, `deps`, `pos`; the divider row has `divider: True`."""
    lines = text.split("\n")
    try:
        head = next(i for i, l in enumerate(lines) if l.strip() == "## Parts")
    except StopIteration:
        raise ValueError("no `## Parts` heading")
    start = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith("|")), None)
    if start is None:
        raise ValueError("no Parts table under `## Parts`")
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    header = [h.lower() for h in _cells(lines[start])]
    for col in ("pos", "part", "state", "deps"):
        if col not in header:
            raise ValueError("Parts table has no `%s` column" % col)
    idx = {c: header.index(c) for c in ("pos", "part", "state", "deps")}
    rows = []
    for i in range(start + 2, end):
        cells = _cells(lines[i])
        if any(c.startswith("─") for c in cells):
            rows.append({"line": i, "divider": True})
            continue
        if len(cells) <= max(idx.values()):
            raise ValueError("Parts table line %d has %d cells, fewer than its header" % (i + 1, len(cells)))
        part = cells[idx["part"]]
        pos = cells[idx["pos"]]
        rows.append({"line": i, "cells": cells, "part": part, "state": cells[idx["state"]].split()[0] if cells[idx["state"]] else "",
                     "deps_cell": cells[idx["deps"]], "pos": int(pos) if pos.isdigit() else None})
    names = sorted((r["part"] for r in rows if not r.get("divider")), key=len, reverse=True)
    for r in rows:
        if not r.get("divider"):
            r["deps"] = split_deps(r["deps_cell"], names)
    return lines, start, end, idx, rows


def split_deps(cell, names):
    """Known Part names in a Deps cell, matched longest first: Part names may themselves hold commas."""
    rest, found = cell.strip(), []
    if rest in ("", DASH, "-"):
        return found
    while rest:
        match = next((n for n in names if rest.startswith(n) and (len(rest) == len(n) or rest[len(n)] == ",")), None)
        if match is None:  # a cross-folder or unknown Dep: skip to the next comma
            cut = rest.find(",")
            rest = rest[cut + 1:].strip() if cut >= 0 else ""
            continue
        found.append(match)
        rest = rest[len(match):].lstrip(",").strip()
    return found


def compute(rows):
    """{part: new Pos or None}."""
    parts = {r["part"]: r for r in rows if not r.get("divider")}
    unseq = {p for p, r in parts.items() if r["state"] in UNSEQUENCED_STATES}
    changed = True
    while changed:
        changed = False
        for p, r in parts.items():
            if p not in unseq and r["state"] != "complete" and any(d in unseq for d in r["deps"]):
                unseq.add(p)
                changed = True
    pos = {p: None for p in parts}
    done = [p for p in parts if parts[p]["state"] == "complete"]
    pending = [p for p in parts if p not in unseq and parts[p]["state"] != "complete"]
    cycle = _place(done, set(done), parts, pos, 0)
    base = max([pos[p] for p in done] or [0])
    cycle += _place(pending, set(parts) - unseq, parts, pos, base)
    if cycle:
        raise ValueError("Deps cycle among: %s" % ", ".join(cycle))
    order = sorted({v for v in pos.values() if v is not None})
    dense = {v: i + 1 for i, v in enumerate(order)}
    return {p: (dense[v] if v is not None else None) for p, v in pos.items()}


def _place(group, constraining, parts, pos, base):
    """Fill `pos` in place: each Part in `group` gets a Pos after `base` and after its Deps in
    `constraining`. Returns the Parts left unplaced, which only a Deps cycle leaves."""
    for _ in range(len(group) + 1):
        progressed = False
        for p in group:
            if pos[p] is not None:
                continue
            deps = [d for d in parts[p]["deps"] if d in constraining]
            if any(pos[d] is None for d in deps):
                continue
            floor = max([base] + [pos[d] for d in deps]) + 1
            current = parts[p]["pos"]
            pos[p] = current if current is not None and current >= floor else floor
            progressed = True
        if not progressed:
            break
    return [p for p in group if pos[p] is None]


def _part_names(body, idx):
    return [_cells(l)[idx["part"]] for l in body if "─" not in l]


def render(text):
    lines, start, end, idx, rows = parse(text)
    new = compute(rows)
    parts = [r for r in rows if not r.get("divider")]
    divider = next((lines[r["line"]] for r in rows if r.get("divider")), None)
    report = []
    for r in parts:
        old = r["pos"]
        if new[r["part"]] != old:
            report.append("%s: Pos %s -> %s" % (r["part"], old if old is not None else DASH,
                                                  new[r["part"]] if new[r["part"]] is not None else DASH))

    def with_pos(r):
        cells = list(r["cells"])
        width = len(cells[idx["pos"]])
        value = str(new[r["part"]]) if new[r["part"]] is not None else DASH
        segs = lines[r["line"]].split("|")
        seg = segs[idx["pos"] + 1]
        segs[idx["pos"] + 1] = " " + value.ljust(max(len(seg) - 2, width, len(value))) + " "
        return "|".join(segs)

    seq = sorted([r for r in parts if new[r["part"]] is not None], key=lambda r: (new[r["part"]], r["line"]))
    uns = [r for r in parts if new[r["part"]] is None]
    body = [with_pos(r) for r in seq]
    if uns:
        if divider is None:
            divider = "|   | ─── unsequenced ─── |" + "   |" * (len(lines[start].split("|")) - 4)
        body += [divider] + [with_pos(r) for r in uns]
    old_body = lines[start + 2:end]
    if _part_names(body, idx) != _part_names(old_body, idx) and not report:
        report.append("row order changes")
    out = lines[:start + 2] + body + lines[end:]

    def relabel(fence):
        block = fence.group(0)
        for r in parts:
            name, p = r["part"], new[r["part"]]
            label = "%d: %s" % (p, name) if p is not None else name
            block = re.sub(r'\["(?:\d+: )?' + re.escape(name) + r'"\]', lambda m: '["%s"]' % label, block)
        return block

    result = re.sub(r"```mermaid\n.*?```", relabel, "\n".join(out), flags=re.S)
    return result, report


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().split("\n\n")[0])
        return 2
    path, apply = argv[0], "--apply" in argv[1:]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        result, report = render(text)
    except ValueError as exc:
        print("roadmap_pos: %s" % exc)
        return 2
    for line in report:
        print(line)
    if result == text:
        print("roadmap_pos: Pos matches Deps")
        return 0
    if apply:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(result)
        print("roadmap_pos: applied %d change(s)" % max(len(report), 1))
        return 0
    print("roadmap_pos: %d change(s); --apply writes them" % max(len(report), 1))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
