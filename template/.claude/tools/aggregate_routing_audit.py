#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aggregate_routing_audit.py — read logs/routing_audit.jsonl, group by week +
rule + classification, emit logs/routing_audit_stats.json + console summary.

Modeled on .claude/tools/analyze_eval_archive.py (which feeds /eval_dashboard
the same way this feeds the new "Routing Stability" section).

Usage:
    python3 aggregate_routing_audit.py [--weeks N] [--rotate-older-than-days D]

Modes:
    Default (no args): aggregate everything in logs/routing_audit.jsonl,
                       write stats JSON, print human-readable summary.
    --weeks N        : only include entries from the last N weeks.
    --rotate-older-than-days D
                     : move entries older than D days to
                       logs/routing-audit-archive/YYYY-MM.jsonl, leave the
                       active log file with only fresh entries. Use to keep
                       the active log bounded (default policy: 30 days).

Stats JSON schema (single source of truth for /eval_dashboard):
    {
      "generated_at": "2026-05-04T...",
      "log_path": "logs/routing_audit.jsonl",
      "active_window_days": 30,
      "total_entries": int,
      "silent_misses": int,                # nudge-warranted + nudge_fired=false (total)
      "silent_misses_main_loop": int,      # silent_misses with empty agent_id (actionable)
      "silent_misses_subagent": int,       # silent_misses from subagents (see note below)
      "nudged_routing_misses": int,        # nudge-warranted + nudge_fired=true
      "cue_exempt_overrides": int,         # cue-exempt classification
      "by_rule": {
        "pascal-grep-on-cs": {"silent_miss": int, "silent_miss_main": int,
                              "silent_miss_sub": int, "nudged": int, "cue_exempt": int},
        ...
      },

    Subagent silent-miss caveat: a subagent's one-off discovery Grep writes its
    nudge-fired record AFTER routing_audit.py reads it (PostToolUse hook ordering),
    so first-occurrence subagent patterns are systematically mis-counted as silent.
    The MAIN-LOOP count is the actionable signal; the subagent count is dominated by
    this measurement artifact until the hook-ordering fix lands.
      "by_week": {
        "2026-W18": {"silent_miss": int, "nudged": int, "cue_exempt": int},
        ...
      },
      "top_5_silent_miss_rules": [
        {"rule": "...", "count": int, "trend_vs_prior_4wk_avg": "+12%"|"-3%"|"flat"},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_PATH = Path("logs/routing_audit.jsonl")
STATS_PATH = Path("logs/routing_audit_stats.json")
ARCHIVE_DIR = Path("logs/routing-audit-archive")


# === I/O =====================================================================

def _load_entries(path: Path) -> list[dict]:
    """Load all parseable entries. Skip malformed lines silently."""
    if not path.exists():
        return []
    entries = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict):
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _parse_iso(ts: str) -> datetime | None:
    """Parse the audit log's ISO-8601 timestamp. Returns None on failure."""
    if not ts:
        return None
    try:
        # Python's fromisoformat handles "+00:00" but not "Z" (3.10-).
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


# === Aggregation =============================================================

def _classification_bucket(entry: dict) -> str:
    """
    Map (classification, nudge_fired) pair to dashboard bucket.
    Returns one of: "silent_miss", "nudged", "cue_exempt", "other".
    """
    cls = entry.get("classification", "")
    nudge = entry.get("nudge_fired", False)
    if cls == "nudge-warranted":
        return "nudged" if nudge else "silent_miss"
    if cls == "cue-exempt":
        return "cue_exempt"
    return "other"


def _is_subagent(entry: dict) -> bool:
    """A populated agent_id means the call came from a subagent, not the main loop."""
    return bool(entry.get("agent_id"))


def _week_key(ts: datetime) -> str:
    """ISO week format: 2026-W18."""
    iso_year, iso_week, _ = ts.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _trend_label(current: int, prior_avg: float) -> str:
    """Compute a percent-change label: '+12%', '-3%', 'flat', or 'new'."""
    if prior_avg == 0:
        return "new" if current > 0 else "flat"
    delta_pct = ((current - prior_avg) / prior_avg) * 100
    if abs(delta_pct) < 5:
        return "flat"
    sign = "+" if delta_pct > 0 else ""
    return f"{sign}{int(delta_pct)}%"


def aggregate(entries: list[dict], active_window_days: int = 30) -> dict:
    """Build the stats dict from raw audit entries."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=active_window_days)

    in_window = []
    for entry in entries:
        ts = _parse_iso(entry.get("ts", ""))
        if ts is None or ts < cutoff:
            continue
        in_window.append((ts, entry))

    by_rule_counts: dict[str, Counter] = defaultdict(Counter)
    by_week_counts: dict[str, Counter] = defaultdict(Counter)
    bucket_totals = Counter()

    for ts, entry in in_window:
        bucket = _classification_bucket(entry)
        if bucket == "other":
            continue
        rule = entry.get("rule") or "(unknown)"
        bucket_totals[bucket] += 1
        by_rule_counts[rule][bucket] += 1
        by_week_counts[_week_key(ts)][bucket] += 1
        # Split silent-misses by origin. Subagent first-occurrence greps are
        # over-counted (nudge-state write races the audit read), so the
        # main-loop count is the actionable signal.
        if bucket == "silent_miss":
            origin = "silent_miss_sub" if _is_subagent(entry) else "silent_miss_main"
            bucket_totals[origin] += 1
            by_rule_counts[rule][origin] += 1

    # Top-5 silent-miss rules with trend vs prior 4-week avg
    silent_miss_by_rule_thisweek = Counter()
    silent_miss_by_rule_prior4 = defaultdict(list)  # rule → [count_w-1, count_w-2, ...]
    this_week = _week_key(now)
    for week_str, counts in by_week_counts.items():
        # parse this iso week back to a date for comparison
        try:
            year, week = week_str.split("-W")
            year, week = int(year), int(week)
        except (ValueError, AttributeError):
            continue
        # NOTE: we don't have per-rule per-week breakdown in the loop above —
        # rebuild it. (The double-loop is fine; entries-in-window typically <1k.)
    # Per-rule per-week breakdown
    rule_week_counts: dict[str, Counter] = defaultdict(Counter)
    for ts, entry in in_window:
        if _classification_bucket(entry) != "silent_miss":
            continue
        rule = entry.get("rule") or "(unknown)"
        rule_week_counts[rule][_week_key(ts)] += 1

    top_5: list[dict] = []
    rules_by_silent_miss = sorted(
        by_rule_counts.items(),
        key=lambda kv: kv[1].get("silent_miss", 0),
        reverse=True,
    )
    for rule, counts in rules_by_silent_miss[:5]:
        sm_count = counts.get("silent_miss", 0)
        if sm_count == 0:
            continue
        # Compute prior-4-week average (excluding the current week)
        prior_counts = []
        for offset in range(1, 5):
            wk_date = now - timedelta(weeks=offset)
            wk_key = _week_key(wk_date)
            prior_counts.append(rule_week_counts[rule].get(wk_key, 0))
        prior_avg = sum(prior_counts) / 4 if prior_counts else 0.0
        current_wk_count = rule_week_counts[rule].get(this_week, 0)
        top_5.append({
            "rule": rule,
            "count": sm_count,
            "main_loop_count": counts.get("silent_miss_main", 0),
            "subagent_count": counts.get("silent_miss_sub", 0),
            "current_week_count": current_wk_count,
            "prior_4wk_avg": round(prior_avg, 1),
            "trend_vs_prior_4wk_avg": _trend_label(current_wk_count, prior_avg),
        })

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "log_path": str(LOG_PATH),
        "active_window_days": active_window_days,
        "total_entries": len(in_window),
        "silent_misses": bucket_totals.get("silent_miss", 0),
        "silent_misses_main_loop": bucket_totals.get("silent_miss_main", 0),
        "silent_misses_subagent": bucket_totals.get("silent_miss_sub", 0),
        "nudged_routing_misses": bucket_totals.get("nudged", 0),
        "cue_exempt_overrides": bucket_totals.get("cue_exempt", 0),
        "by_rule": {
            rule: dict(counts) for rule, counts in by_rule_counts.items()
        },
        "by_week": {
            week: dict(counts) for week, counts in by_week_counts.items()
        },
        "top_5_silent_miss_rules": top_5,
    }


# === Rotation ================================================================

def rotate_old(entries: list[dict], cutoff_days: int) -> tuple[list[dict], dict[str, int]]:
    """
    Split entries into (kept_in_active_log, archived_by_month).
    Writes archived entries to logs/routing-audit-archive/YYYY-MM.jsonl
    (append mode). Returns the kept list and a {month: count} dict for
    reporting.

    NOTE: callers are responsible for replacing the active log with the
    `kept` list — this function does NOT mutate the active log itself.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    kept: list[dict] = []
    by_month: dict[str, list[dict]] = defaultdict(list)

    for entry in entries:
        ts = _parse_iso(entry.get("ts", ""))
        if ts is None or ts >= cutoff:
            kept.append(entry)
            continue
        month_key = ts.strftime("%Y-%m")
        by_month[month_key].append(entry)

    archive_counts: dict[str, int] = {}
    if by_month:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        for month_key, archived in by_month.items():
            archive_path = ARCHIVE_DIR / f"{month_key}.jsonl"
            with archive_path.open("a", encoding="utf-8") as f:
                for entry in archived:
                    f.write(json.dumps(entry, ensure_ascii=True) + "\n")
            archive_counts[month_key] = len(archived)

    return kept, archive_counts


def _replace_log(entries: list[dict]) -> None:
    """Atomic-ish replace of the active log with `entries`."""
    tmp = LOG_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    os.replace(tmp, LOG_PATH)


# === Reporting ===============================================================

def render_console_summary(stats: dict) -> str:
    """Human-readable summary suitable for /session_end stdout."""
    lines = []
    lines.append("=" * 60)
    lines.append("Routing Audit — Aggregated Summary")
    lines.append("=" * 60)
    lines.append(f"Active window: last {stats['active_window_days']} days")
    lines.append(f"Total classified events: {stats['total_entries']}")
    lines.append("")
    lines.append("Headline counts:")
    lines.append(f"  silent_miss          : {stats['silent_misses']}    "
                 "(rule-warranted, existing nudge channel didn't reach the agent)")
    lines.append(f"    - main-loop         : {stats.get('silent_misses_main_loop', 0)}    "
                 "(the actionable signal)")
    lines.append(f"    - subagent          : {stats.get('silent_misses_subagent', 0)}    "
                 "(largely a hook-ordering measurement artifact -- discount)")
    lines.append(f"  nudged_routing_miss  : {stats['nudged_routing_misses']}    "
                 "(rule-warranted, nudge fired; channel works but agent may still ignore)")
    lines.append(f"  cue_exempt_override  : {stats['cue_exempt_overrides']}    "
                 "(legitimate K1/L6/audit-shape override)")
    lines.append("")
    if stats["top_5_silent_miss_rules"]:
        lines.append("Top silent-miss rules:")
        for row in stats["top_5_silent_miss_rules"]:
            lines.append(
                f"  {row['rule']:<35} {row['count']:>3} total | "
                f"this week: {row['current_week_count']} | "
                f"prior-4wk avg: {row['prior_4wk_avg']} | "
                f"trend: {row['trend_vs_prior_4wk_avg']}"
            )
    else:
        lines.append("No silent-miss rules in window. (Possible interpretations: "
                     "(a) routing doctrine internalized, (b) hooks blocking before classifier sees them, "
                     "(c) audit log brand-new with not enough events yet.)")
    lines.append("")
    lines.append(f"Stats written to: {STATS_PATH}")
    return "\n".join(lines)


# === Main ====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate logs/routing_audit.jsonl")
    parser.add_argument(
        "--weeks", type=int, default=None,
        help="Only include entries from the last N weeks (default: use --rotate-older-than-days window)",
    )
    parser.add_argument(
        "--rotate-older-than-days", type=int, default=30,
        help="Move entries older than D days to logs/routing-audit-archive/. Default 30. Set 0 to disable.",
    )
    args = parser.parse_args()

    entries = _load_entries(LOG_PATH)
    if not entries:
        print(f"(no entries in {LOG_PATH} — audit log is empty or missing)")
        return 0

    rotation_report = ""
    if args.rotate_older_than_days > 0:
        kept, archive_counts = rotate_old(entries, args.rotate_older_than_days)
        if archive_counts:
            _replace_log(kept)
            entries = kept
            rotation_lines = [f"Rotated {sum(archive_counts.values())} entries to archive:"]
            for month, count in sorted(archive_counts.items()):
                rotation_lines.append(f"  {ARCHIVE_DIR}/{month}.jsonl: +{count} entries")
            rotation_report = "\n".join(rotation_lines) + "\n"

    window_days = args.weeks * 7 if args.weeks else args.rotate_older_than_days
    stats = aggregate(entries, active_window_days=window_days)

    # Write stats JSON for /eval_dashboard.
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATS_PATH.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=True)

    if rotation_report:
        print(rotation_report)
    print(render_console_summary(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
