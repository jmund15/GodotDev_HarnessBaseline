#!/usr/bin/env python3
"""Review calibration facts: the escape log beside the review yield log, and the periodic review's proposals.

  python3 .claude/tools/review_calibration.py [--json FILE]

`build` is what `eval_dashboard.build_facts` stores under `facts["review"]`. It edits nothing: each proposal is
`{kind, subject, evidence, action}` and waits for the owner. `premerge-seat` counts every group whose
newest reached rows are all zero and prints that run length L; the owner judges whether L is enough evidence.
`merge-seat` is raised only when the zero streak holds under both orders of rows tied on date and timestamp.

Inputs: escape-log rows, archived seat rows (Workflow and sidecar; a row with no `ledger_join` is not a review seat),
and the loaded model registry. A `ledger_new` that is null is unmeasured and never counts as zero. `ledger_new` is a
seat-level count, so a mandate's `ledger_new` sums it over the pre-merge seats that carried that mandate alone; a
mandate no such seat reached has null there.
"""
import argparse
import datetime
import json
import os
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import escape_log  # noqa: E402
import model_registry  # noqa: E402
import orchestration_metrics  # noqa: E402
import triage  # noqa: E402

NEWEST_ESCAPES = 5
KINDS = ("merge-seat", "premerge-seat", "sensitivity-tag", "triage-rule", "compensation")
PREMERGE = "pre-merge"
NOT_REACHED = "not-reached"


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _model(name, registry):
    """(canonical id, family) of a model string; an unknown string stands for itself."""
    try:
        row = model_registry.row_for_model(name, registry)
    except Exception:
        row = None
    if row is None:
        return str(name), str(name)
    return row["id"], row.get("family") or row["id"]


def _seat_key(row):
    block = row.get("seat_overlap")
    if isinstance(block, dict) and block.get("key"):
        return block["key"]
    label = str(row.get("label") or "")
    return label[len("review:"):] if label.startswith("review:") else label


def _ordered(rows, ties_reversed=False):
    """Rows oldest first by date, then timestamp. Rows tied on both keep their input order, or the reverse when
    `ties_reversed`: a Workflow row carries no timestamp, so a same-day order is not on record."""
    sign = -1 if ties_reversed else 1
    indexed = sorted(enumerate(rows), key=lambda pair: (orchestration_metrics.row_date(pair[1]) or "",
                                                         str(pair[1].get("timestamp") or ""), sign * pair[0]))
    return [row for _, row in indexed]


def _zero_run(values):
    """Length of the newest run of numeric zeros; a null or non-zero value ends it."""
    length = 0
    for value in reversed(values):
        if value != 0:
            break
        length += 1
    return length


def _count_by(rows, field):
    out = {}
    for row in rows:
        value = row.get(field)
        for item in (value if isinstance(value, list) else [value]):
            if item is not None:
                out[str(item)] = out.get(str(item), 0) + 1
    return dict(sorted(out.items()))


def _escapes_block(escapes, escape_bytes):
    newest = [{key: row.get(key) for key in ("id", "ts", "sha", "shouldHaveCaught", "authorModel", "tier", "domain")}
              for row in sorted(escapes, key=lambda r: str(r.get("ts") or ""), reverse=True)[:NEWEST_ESCAPES]]
    return {"rows": len(escapes), "bytes": escape_bytes,
            "by_mandate": _count_by(escapes, "shouldHaveCaught"), "by_domain": _count_by(escapes, "domain"),
            "by_model": _count_by(escapes, "authorModel"), "by_tier": _count_by(escapes, "tier"), "newest": newest}


def _reached(row):
    mandates = row.get("mandates")
    if not isinstance(mandates, dict):
        return {}
    return {key: m for key, m in mandates.items() if isinstance(m, dict) and m.get("outcome") != NOT_REACHED}


def _mandates_block(seats, escapes):
    stats = {}
    for row in seats:
        alone = len(row.get("mandates") or {}) == 1
        for key, m in _reached(row).items():
            entry = stats.setdefault(key, {"key": key, "reviews": 0, "unique": 0, "ledger_new": None, "escapes": 0})
            entry["reviews"] += 1
            entry["unique"] += _number(m.get("unique")) or 0
            if alone and row.get("review_stage") == PREMERGE:
                entry["ledger_new"] = (entry["ledger_new"] or 0) + (_number(row.get("ledger_new")) or 0)
    for row in escapes:
        key = row.get("shouldHaveCaught")
        if key and key != escape_log.NO_MANDATE:
            entry = stats.setdefault(key, {"key": key, "reviews": 0, "unique": 0, "ledger_new": None, "escapes": 0})
            entry["escapes"] += 1
    return [stats[key] for key in sorted(stats)]


def _premerge_groups(seats, registry):
    groups = {}
    for row in _ordered(seats):
        if row.get("review_stage") != PREMERGE:
            continue
        family = _model(row.get("model"), registry)[1]
        groups.setdefault((_seat_key(row), family), []).append(_number(row.get("ledger_new")))
    return groups


def _premerge_block(groups):
    return [{"seat": seat, "family": family, "reviews": len(values),
             "ledger_new": sum(v for v in values if v is not None), "null": sum(1 for v in values if v is None)}
            for (seat, family), values in sorted(groups.items())]


def _seat_map_files(root, key):
    hits = []
    for top in ("commands", "skills"):
        for base, dirs, files in os.walk(os.path.join(root, ".claude", top)):
            dirs[:] = [d for d in dirs if d != "scratch"]
            for name in files:
                path = os.path.join(base, name)
                if name.endswith(".md"):
                    try:
                        with open(path, encoding="utf-8") as handle:
                            if key in handle.read():
                                hits.append(os.path.relpath(path, root).replace(os.sep, "/"))
                    except OSError:
                        continue
    return sorted(hits)


def _merge_seats(seats, root, today):
    keys = sorted(set(orchestration_metrics.seat_yield(_ordered(seats)))
                  & set(orchestration_metrics.seat_yield(_ordered(seats, ties_reversed=True))))
    out = []
    for key in keys:
        rows = [r for r in seats if isinstance(r.get("seat_overlap"), dict) and r["seat_overlap"].get("key") == key]
        mandates = sorted({m for r in rows for m in _reached(r)})
        files = _seat_map_files(root, key) if root else []
        markers = "; ".join("review-when: escape mandate:%s since:%s >= 1" % (m, today) for m in mandates)
        out.append({
            "kind": "merge-seat", "subject": key,
            "evidence": "the last %d of %d reviews carry unique 0; seat map in %s" % (
                orchestration_metrics.PANEL_YIELD_WINDOW, len({r.get("run") for r in rows}),
                ", ".join(files) or "no tracked command or skill file found (name the owner file)"),
            "action": "merge seat %s into one that reads the same input; the merged seat's owner file carries one "
                      "evidence marker per mandate: %s" % (key, markers or "the mandates are unrecorded")})
    return out


def _premerge_seats(groups):
    found = []
    for (seat, family), values in groups.items():
        length = _zero_run(values)
        if length:
            found.append((length, {
                "kind": "premerge-seat", "subject": "%s / %s" % (seat, family),
                "evidence": "L=%d: the newest %d pre-merge reviews of %d carry ledger_new 0" % (length, length, len(values)),
                "action": "drop the cross-family pre-merge seat %s" % seat}))
    return [p for _, p in sorted(found, key=lambda item: -item[0])]


def _triage_rules(escapes, claude):
    found = []
    for row in escapes:
        text = row.get("triage")
        if text == escape_log.UNRECORDED:
            why = "recorded triage: unrecorded"
        else:
            fields, _error = triage.parse_trailer("Triage: %s" % text)
            if fields is None:
                continue
            try:
                res = triage.derive(triage.planned_changes(row.get("paths") or []), triage.load_tags(claude))
            except (OSError, ValueError):
                continue
            raised = triage.raises(fields, res)
            if not raised:
                continue
            why = "; ".join(triage.describe(r) for r in raised)
        found.append({"kind": "triage-rule", "subject": str(row.get("id")),
                      "evidence": "escape %s (%s): %s" % (row.get("id"), str(row.get("sha") or "")[:8], why),
                      "action": "change the triage rule or tag that let this change through"})
    return found


def _compensation(escapes, registry):
    found = []
    for model in registry.get("models") or []:
        marks = [m for m in model.get("probation") or [] if isinstance(m, dict)]
        notes = [n for n in model.get("driverNotes") or [] if isinstance(n, dict) and n.get("removeWhen")]
        if not marks and not notes:
            continue
        mine = sorted((e for e in escapes if _model(e.get("authorModel"), registry)[0] == model["id"]),
                      key=lambda e: str(e.get("ts") or ""))
        newest = "; newest %s %s" % (mine[-1].get("id"), mine[-1].get("ts")) if mine else ""
        states = []
        for mark in marks:
            try:
                basis = model_registry.probation(model["id"], mark.get("tier"), data=registry)
            except Exception as exc:
                basis = "unreadable (%s)" % exc
            states.append("probation(%s): %s" % (mark.get("tier"), basis))
        found.append({
            "kind": "compensation", "subject": model["id"],
            "evidence": "%d escape row(s)%s; %s" % (len(mine), newest, "; ".join(states) or "no probation mark"),
            "action": "; ".join(["remove the driver note %r when %s" % (str(n.get("text"))[:60], n["removeWhen"])
                                 for n in notes] + ["lift the owner probation mark on tier %s" % m.get("tier")
                                                    for m in marks])})
    return found


def build(escapes, escape_bytes, yield_rows, registry, root=None, today=None):
    """The Review tab's facts; see the module docstring. `root` locates the seat maps and the tag registry."""
    today = today or datetime.date.today().isoformat()
    claude = os.path.join(root, ".claude") if root else escape_log.claude_dir()
    seats = [r for r in yield_rows if r.get("ledger_join")]
    joined = [r for r in seats if r["ledger_join"] == "joined"]
    groups = _premerge_groups(joined, registry)
    untagged = [{"kind": "sensitivity-tag", "subject": str(row.get("id")),
                 "evidence": "escape %s (%s) touched %s with domain untagged" % (
                     row.get("id"), str(row.get("sha", ""))[:8], ", ".join(row.get("paths") or [])),
                 "action": "add a tag over these paths in the tag registry"}
                for row in escapes if triage.UNTAGGED in (row.get("domain") or [])]
    proposals = (_merge_seats(joined, root, today) + _premerge_seats(groups)
                 + untagged + _triage_rules(escapes, claude) + _compensation(escapes, registry))
    return {"escapes": _escapes_block(escapes, escape_bytes), "mandates": _mandates_block(joined, escapes),
            "premerge": _premerge_block(groups), "proposals": proposals,
            "join": {state: sum(1 for r in seats if r["ledger_join"] == state)
                     for state in orchestration_metrics.REVIEW_JOIN_STATES}}


def sources():
    """(escape rows, escape file bytes or None, archived seat rows, registry or None) from the live defaults."""
    path = escape_log.log_path()
    size = os.path.getsize(path) if os.path.isfile(path) else None
    rows, side, _ = orchestration_metrics.archive_rows([])
    try:
        registry = model_registry.load()
    except model_registry.RegistryError:
        registry = None
    return escape_log.rows(path), size, rows + side, registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default=None, metavar="FILE", help="write the facts here instead of stdout")
    args = parser.parse_args(argv)
    escapes, size, seats, registry = sources()
    facts = build(escapes, size, seats, registry or {"models": []})
    text = json.dumps(facts, indent=2, ensure_ascii=False)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
