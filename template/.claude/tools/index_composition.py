#!/usr/bin/env python3
"""Report what the semantic-search corpus is made of, by chunk count.

Ranking is competitive: a bucket that grows crowds every other bucket out of the top
results regardless of relevance (see /reindex_search "Corpus hygiene"). Recall degrades
silently, so this number every rebuild is the drift signal.

Drift is read against `.claude/index_composition_baseline.json` ({chunks, memory_share, date}),
written only by `--set-baseline` after a corpus the agent judged healthy. With no readable
baseline it prints the absolute flags and `no baseline set`.

Read-only except `--set-baseline`. Exits 0 and prints a note when the index is absent or locked —
this runs inside /reindex_search's report step and must never block it. `--set-baseline` exits 1
and writes nothing when the index cannot be read.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys

BASELINE_RELPATH = os.path.join(".claude", "index_composition_baseline.json")
NO_BASELINE = "no baseline set"
MEMORY_BUCKET = ".claude/auto-memory"


def bucket_for(path: str) -> str:
    """Group by top-level dir, but keep .claude/<sub> split — that is where drift shows."""
    p = path.replace("\\", "/")
    parts = p.split("/")
    if p.startswith(".claude/") and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else p


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_baseline(root: str) -> dict | None:
    try:
        with open(os.path.join(root, BASELINE_RELPATH), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    chunks, share, day = data.get("chunks"), data.get("memory_share"), data.get("date")
    if not (_number(chunks) and chunks > 0 and _number(share) and isinstance(day, str) and day):
        return None
    return {"chunks": chunks, "memory_share": share, "date": day}


def read_counts(db: str) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT file_path FROM chunks").fetchall()
    finally:
        con.close()
    counts: dict[str, int] = {}
    for (fp,) in rows:
        b = bucket_for(fp)
        counts[b] = counts.get(b, 0) + 1
    return counts


def set_baseline(root: str, db: str) -> int:
    try:
        counts = read_counts(db) if os.path.isfile(db) else {}
    except sqlite3.Error as exc:
        print(f"index-composition: could not read index ({exc}) — baseline not written")
        return 1
    total = sum(counts.values())
    if not total:
        print(f"index-composition: no readable chunks at {db} — baseline not written")
        return 1
    baseline = {
        "chunks": total,
        "memory_share": round(100 * counts.get(MEMORY_BUCKET, 0) / total, 1),
        "date": datetime.date.today().isoformat(),
    }
    path = os.path.join(root, BASELINE_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(baseline, fh, indent=2)
        fh.write("\n")
    print(f"index-composition: baseline set — {total:,} chunks, memory {baseline['memory_share']}%, {baseline['date']}")
    return 0


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    db = os.path.join(root, ".search-index", "search.db")
    if "--set-baseline" in sys.argv[1:]:
        return set_baseline(root, db)
    if not os.path.isfile(db):
        print(f"index-composition: no index at {db} — skipped (not an error)")
        return 0

    try:
        counts = read_counts(db)
    except sqlite3.Error as exc:  # locked by a peer session, schema drift, etc.
        print(f"index-composition: could not read index ({exc}) — skipped (not an error)")
        return 0

    total = sum(counts.values())
    if not total:
        print("index-composition: index is empty — rebuild likely failed")
        return 0

    print(f"\n  index composition — {total:,} chunks across {len(counts)} buckets")
    print(f"  {'bucket':<28}{'chunks':>8}{'share':>8}")
    print(f"  {'-' * 44}")
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        mark = "  <-- memory" if name == MEMORY_BUCKET else ""
        print(f"  {name:<28}{n:>8,}{100 * n / total:>7.1f}%{mark}")

    mem_share = 100 * counts.get(MEMORY_BUCKET, 0) / total
    baseline = read_baseline(root)
    if baseline:
        growth = 100 * (total - baseline["chunks"]) / baseline["chunks"]
        print(
            f"\n  vs {baseline['date']} baseline: {total:,} chunks ({growth:+.0f}%), "
            f"memory {mem_share:.1f}% (was {baseline['memory_share']}%)"
        )
    else:
        print(f"\n  {NO_BASELINE}")

    # Advisory only — never a non-zero exit. This informs a human judgement call.
    top_name, top_n = max(counts.items(), key=lambda kv: kv[1])
    top_share = 100 * top_n / total
    if top_share >= 35.0:
        print(
            f"  FLAG: {top_name} is {top_share:.0f}% of the corpus. Check it against "
            f"/reindex_search §Corpus hygiene before adding more to it."
        )
    if baseline and mem_share < baseline["memory_share"] / 2:
        print(
            f"  FLAG: memory share has more than halved ({mem_share:.1f}%) — something "
            f"large entered the index and is displacing recall."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
