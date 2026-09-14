#!/usr/bin/env python3
"""Report what the semantic-search corpus is made of, by chunk count.

Ranking is competitive: a bucket that grows crowds every other bucket out of the top
results regardless of relevance (see /reindex_search "Corpus hygiene"). Recall degrades
silently, so this number every rebuild is the drift signal.

Read-only. Exits 0 and prints a note when the index is absent or locked — this runs
inside /reindex_search's report step and must never block it.
"""
from __future__ import annotations

import os
import sqlite3
import sys

# Baseline measured after the 2026-08-16 corpus cleanup; drift is read against these.
BASELINE_CHUNKS = 11_521
BASELINE_MEMORY_SHARE = 5.7
MEMORY_BUCKET = ".claude/auto-memory"


def bucket_for(path: str) -> str:
    """Group by top-level dir, but keep .claude/<sub> split — that is where drift shows."""
    p = path.replace("\\", "/")
    parts = p.split("/")
    if p.startswith(".claude/") and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else p


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    db = os.path.join(root, ".search-index", "search.db")
    if not os.path.isfile(db):
        print(f"index-composition: no index at {db} — skipped (not an error)")
        return 0

    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT file_path FROM chunks").fetchall()
        con.close()
    except sqlite3.Error as exc:  # locked by a peer session, schema drift, etc.
        print(f"index-composition: could not read index ({exc}) — skipped (not an error)")
        return 0

    total = len(rows)
    if not total:
        print("index-composition: index is empty — rebuild likely failed")
        return 0

    counts: dict[str, int] = {}
    for (fp,) in rows:
        b = bucket_for(fp)
        counts[b] = counts.get(b, 0) + 1

    print(f"\n  index composition — {total:,} chunks across {len(counts)} buckets")
    print(f"  {'bucket':<28}{'chunks':>8}{'share':>8}")
    print(f"  {'-' * 44}")
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        mark = "  <-- memory" if name == MEMORY_BUCKET else ""
        print(f"  {name:<28}{n:>8,}{100 * n / total:>7.1f}%{mark}")

    mem_share = 100 * counts.get(MEMORY_BUCKET, 0) / total
    growth = 100 * (total - BASELINE_CHUNKS) / BASELINE_CHUNKS
    print(
        f"\n  vs 2026-08-16 baseline: {total:,} chunks ({growth:+.0f}%), "
        f"memory {mem_share:.1f}% (was {BASELINE_MEMORY_SHARE}%)"
    )

    # Advisory only — never a non-zero exit. This informs a human judgement call.
    top_name, top_n = max(counts.items(), key=lambda kv: kv[1])
    top_share = 100 * top_n / total
    if top_share >= 35.0:
        print(
            f"  FLAG: {top_name} is {top_share:.0f}% of the corpus. Check it against "
            f"/reindex_search §Corpus hygiene before adding more to it."
        )
    if mem_share < BASELINE_MEMORY_SHARE / 2:
        print(
            f"  FLAG: memory share has more than halved ({mem_share:.1f}%) — something "
            f"large entered the index and is displacing recall."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
