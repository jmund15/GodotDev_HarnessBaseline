"""One-shot analysis script for /eval_dashboard.

Streams the legacy self-evaluation snapshot plus its bounded JSONL ledger,
keeps the newest structured row per session, classifies legacy entries
heuristically, and writes dashboard statistics.
"""
import json
import os
import re
import sys
from collections import Counter

PRINT_LIMIT = 20

# Windows consoles default stdout to cp1252; session titles carry non-Latin-1
# glyphs (em-dash, arrow) that crash the final print loop BEFORE stats.json is
# written. Force UTF-8 so the fast path never depends on PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from self_eval_archive_store import load_archive  # noqa: E402
sys.path.insert(0, os.path.join(TOOLS, "..", "hooks"))
from self_eval_archive_guard import OUTCOMES, LEGACY_MAX_ID  # noqa: E402

archive_path = ".claude/self_evaluate_archive.json"
try:
    data, raw_entries = load_archive(archive_path)
except ValueError as exc:
    print("UNKNOWN: " + str(exc))
    sys.exit(2)
legacy = data.get("legacy_entries")
themes = data.get("Self_Evaluate_Themes")
pattern_defs = themes.get("patterns") if isinstance(themes, dict) else None
if not isinstance(legacy, list) or not isinstance(pattern_defs, dict):
    print("UNKNOWN: legacy_entries must be a list and Self_Evaluate_Themes.patterns an object")
    sys.exit(2)
data["structured_entries"] = raw_entries

# Ledger order is oldest to newest, so later upserts win.
effective = {}
for entry in raw_entries:
    session_id = entry.get("session_id")
    key = (("session", session_id) if session_id
           else ("legacy", entry.get("title"), entry.get("date")))
    effective[key] = entry
unique = list(effective.values())


def _outcome(entry):
    value = entry.get("outcome")
    return value if value in OUTCOMES else "unknown"


def _brief(value, limit=60):
    return str(value)[:limit]


METADATA_FIELDS = ("domains", "skills_used", "memory_hits")


def _metadata_errors(entries):
    errors = []
    for entry in entries:
        for field in METADATA_FIELDS:
            if field not in entry:
                continue
            value = entry[field]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                errors.append(
                    f"id={_brief(entry.get('id'), 20)} field={field}: expected a list of strings"
                )
    return errors


metadata_errors = _metadata_errors(unique)
if metadata_errors:
    suffix = (f", ... {len(metadata_errors) - PRINT_LIMIT} row(s) omitted"
              if len(metadata_errors) > PRINT_LIMIT else "")
    print("UNKNOWN: malformed structured metadata: "
          + "; ".join(metadata_errors[:PRINT_LIMIT]) + suffix)
    sys.exit(2)


malformed = [entry for entry in unique if _outcome(entry) == "unknown"]
if malformed:
    preview = malformed[:PRINT_LIMIT]
    suffix = (f", ... {len(malformed) - PRINT_LIMIT} row(s) omitted"
              if len(malformed) > PRINT_LIMIT else "")
    print(f"WARNING: {len(malformed)} structured entr(y/ies) have unknown outcome evidence: "
          + ", ".join(f"id={_brief(entry.get('id'), 20)} date={_brief(entry.get('date'), 40)}"
                      for entry in preview) + suffix)
invalid_outcomes = [entry for entry in unique
                    if "outcome" in entry and entry["outcome"] not in OUTCOMES]
for entry in invalid_outcomes[:PRINT_LIMIT]:
    print(f"WARNING: id={_brief(entry.get('id'), 20)} has non-enum outcome "
          f"{_brief(entry['outcome'], 40)!r} — counted as unknown")
if len(invalid_outcomes) > PRINT_LIMIT:
    print(f"WARNING: {len(invalid_outcomes) - PRINT_LIMIT} non-enum outcome warning row(s) omitted")

PATTERNS = set(pattern_defs) | {None}
invalid_patterns = [entry for entry in unique
                    if isinstance(entry.get("id"), int)
                    and entry["id"] > LEGACY_MAX_ID
                    and entry.get("pattern") not in PATTERNS]
for entry in invalid_patterns[:PRINT_LIMIT]:
    print(f"WARNING: id={_brief(entry.get('id'), 20)} has non-enum pattern "
          f"{_brief(entry.get('pattern'), 40)!r} — normalize it")
if len(invalid_patterns) > PRINT_LIMIT:
    print(f"WARNING: {len(invalid_patterns) - PRINT_LIMIT} non-enum pattern warning row(s) omitted")

print("=== DEDUP RESULTS ===")
print(f"Raw structured entries: {len(raw_entries)}")
print(f"Unique structured entries: {len(unique)}")
superseded_pct = ((1 - len(unique) / len(raw_entries)) * 100
                  if raw_entries else None)
print("Superseded-row rate: "
      + (f"{superseded_pct:.1f}%" if superseded_pct is not None else "n/a (no rows)"))

# LEGACY HEURISTIC
legacy_clean = 0
legacy_correction = 0
legacy_failure = 0
legacy_unknown = 0
legacy_total = 0
for entry in legacy:
    legacy_total += 1
    if not isinstance(entry, str):
        legacy_unknown += 1
        continue
    s = entry.upper()
    if "#8-#19" in entry:
        legacy_clean += 12
        legacy_total += 11
        continue
    if "CRITICAL" in s or "FAILURE CASCADE" in s:
        legacy_failure += 1
    elif (
        "USER CORRECTION" in s
        or "CORRECTION" in s
        or "GAP" in s
        or "DISCIPLINE" in s
        or "SKIPPED" in s
        or "ASSUMED" in s
        or "PREMATURE" in s
    ):
        legacy_correction += 1
    elif "CLEAN" in s or "ZERO CORRECTIONS" in s:
        legacy_clean += 1
    else:
        legacy_unknown += 1

print("\n=== LEGACY (heuristic) ===")
print(f"Total legacy sessions: {legacy_total}")
print(f"  Clean:       {legacy_clean}")
print(f"  Correction:  {legacy_correction}")
print(f"  Failure:     {legacy_failure}")
print(f"  Unknown:     {legacy_unknown}")

# STRUCTURED OUTCOME COUNTS
outcomes = Counter(_outcome(entry) for entry in unique)
patterns = Counter(entry.get("pattern", "unknown") for entry in unique)


def _norm_pattern(e):
    """Clean sessions with an unset pattern follow the legacy 'C = clean'
    convention, but the current /self_evaluate spec writes null for clean — so
    the two coexist and split the clean count between 'C' and 'None'. Normalize
    null+clean -> C so the dashboard's clean-pattern tally reflects reality. A
    null pattern on a correction is the genuine classification gap, left as-is."""
    p = e.get("pattern")
    if p in (None, "None", "", "unknown") and e.get("outcome") == "clean":
        return "C"
    return p if p else "None"


patterns_normalized = Counter(_norm_pattern(e) for e in unique)

print("\n=== STRUCTURED OUTCOMES ===")
for o, c in outcomes.most_common():
    print(f"  {o}: {c}")
print("\n=== STRUCTURED PATTERNS (raw) ===")
pattern_rows = patterns.most_common()
for p, c in pattern_rows[:PRINT_LIMIT]:
    print(f"  {_brief(p)}: {c}")
if len(pattern_rows) > PRINT_LIMIT:
    print(f"  ... {len(pattern_rows) - PRINT_LIMIT} pattern row(s) omitted")
print("=== STRUCTURED PATTERNS (normalized: null+clean -> C) ===")
normalized_rows = patterns_normalized.most_common()
for p, c in normalized_rows[:PRINT_LIMIT]:
    print(f"  {_brief(p)}: {c}")
if len(normalized_rows) > PRINT_LIMIT:
    print(f"  ... {len(normalized_rows) - PRINT_LIMIT} normalized pattern row(s) omitted")

# COMBINED OVERVIEW
total_clean = outcomes.get("clean", 0) + legacy_clean
total_correction = outcomes.get("correction", 0) + legacy_correction
total_failure = outcomes.get("failure", 0) + legacy_failure
total_unknown = outcomes.get("unknown", 0) + legacy_unknown
total_sessions = total_clean + total_correction + total_failure + total_unknown
total_known = total_sessions - total_unknown
analysis_status = ("no-data" if total_sessions == 0
                   else "partial" if total_unknown else "complete")


def _pct(numerator, denominator):
    return numerator * 100 / denominator if denominator else None


def _pct_text(value):
    return f"{value:.1f}%" if value is not None else "unknown"


clean_pct = _pct(total_clean, total_sessions)
correction_pct = _pct(total_correction, total_sessions)
failure_pct = _pct(total_failure, total_sessions)
print("\n=== COMBINED OVERVIEW ===")
print(f"Status: {analysis_status}")
print(f"Total sessions: {total_sessions}")
print(f"Clean:       {total_clean} ({_pct_text(clean_pct)})")
print(f"Correction:  {total_correction} ({_pct_text(correction_pct)})")
print(f"Failure:     {total_failure} ({_pct_text(failure_pct)})")
print(f"Unknown:     {total_unknown}")

# DOMAIN PERFORMANCE
domain_total = Counter()
domain_clean = Counter()
domain_corr = Counter()
domain_fail = Counter()
domain_unknown = Counter()
for entry in unique:
    domains = entry.get("domains")
    for domain in domains if isinstance(domains, list) else []:
        domain_total[domain] += 1
        bucket = _outcome(entry)
        if bucket == "clean":
            domain_clean[domain] += 1
        elif bucket == "correction":
            domain_corr[domain] += 1
        elif bucket == "failure":
            domain_fail[domain] += 1
        else:
            domain_unknown[domain] += 1

print("\n=== DOMAIN PERFORMANCE (structured) ===")
header = f"{'Domain':<20} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Unk':>4} {'Cln%':>7}"
print(header)
domain_rows = [(domain, population) for domain, population in domain_total.most_common()
               if population >= 2]
if len(domain_rows) > PRINT_LIMIT:
    print(f"  ... {len(domain_rows) - PRINT_LIMIT} lower-frequency domain row(s) omitted")
for domain, population in domain_rows[:PRINT_LIMIT]:
    known = population - domain_unknown[domain]
    clean_rate = _pct(domain_clean[domain], known)
    print(f"{_brief(domain, 20):<20} {population:>4} {domain_clean[domain]:>4} {domain_corr[domain]:>4} "
          f"{domain_fail[domain]:>4} {domain_unknown[domain]:>4} {_pct_text(clean_rate):>7}")

# SKILL PERFORMANCE
skill_total = Counter()
skill_clean = Counter()
skill_corr = Counter()
skill_fail = Counter()
skill_unknown = Counter()
for entry in unique:
    skills = entry.get("skills_used")
    for skill in skills if isinstance(skills, list) else []:
        skill_total[skill] += 1
        bucket = _outcome(entry)
        if bucket == "clean":
            skill_clean[skill] += 1
        elif bucket == "correction":
            skill_corr[skill] += 1
        elif bucket == "failure":
            skill_fail[skill] += 1
        else:
            skill_unknown[skill] += 1

print("\n=== SKILL PERFORMANCE (>=3 loads) ===")
print(f"{'Skill':<28} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Unk':>4} {'Cln%':>7}")
skill_rows = [(skill, population) for skill, population in skill_total.most_common()
              if population >= 3]
if len(skill_rows) > PRINT_LIMIT:
    print(f"  ... {len(skill_rows) - PRINT_LIMIT} lower-frequency skill row(s) omitted")
for skill, population in skill_rows[:PRINT_LIMIT]:
    known = population - skill_unknown[skill]
    clean_rate = _pct(skill_clean[skill], known)
    print(f"{_brief(skill, 28):<28} {population:>4} {skill_clean[skill]:>4} {skill_corr[skill]:>4} "
          f"{skill_fail[skill]:>4} {skill_unknown[skill]:>4} {_pct_text(clean_rate):>7}")

# TREND ANALYSIS
def _sort_key(entry):
    date = entry.get("date") if isinstance(entry.get("date"), str) else ""
    entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
    return date, entry_id


def _skills(entry):
    value = entry.get("skills_used")
    return value if isinstance(value, list) else []


sorted_unique = sorted(unique, key=_sort_key)
recent = sorted_unique[-10:]
prior = sorted_unique[-20:-10]
recent_clean_n = sum(1 for entry in recent if _outcome(entry) == "clean")
prior_clean_n = sum(1 for entry in prior if _outcome(entry) == "clean")
recent_known_n = sum(1 for entry in recent if _outcome(entry) != "unknown")
prior_known_n = sum(1 for entry in prior if _outcome(entry) != "unknown")
recent_clean_pct = _pct(recent_clean_n, recent_known_n)
prior_clean_pct = _pct(prior_clean_n, prior_known_n)
print("\n=== TREND (recent 10 vs prior 10, structured) ===")
if sorted_unique:
    print(f"Date range: {_brief(_sort_key(sorted_unique[0])[0] or 'unknown', 40)} - "
          f"{_brief(_sort_key(sorted_unique[-1])[0] or 'unknown', 40)}")
else:
    print("Date range: unknown (no structured sessions)")
print(f"Recent window: clean={recent_clean_n}/{recent_known_n} known, "
      f"population={len(recent)}, unknown={len(recent) - recent_known_n}")
print(f"Prior window:  clean={prior_clean_n}/{prior_known_n} known, "
      f"population={len(prior)}, unknown={len(prior) - prior_known_n}")

print("\nPer-skill trend:")
trend_rows = []
trend_prints = []
for skill in skill_total:
    if skill_total[skill] < 3:
        continue
    rec_rows = [entry for entry in recent if skill in _skills(entry)]
    prior_rows = [entry for entry in prior if skill in _skills(entry)]
    if not rec_rows and not prior_rows:
        continue
    rec_known = [entry for entry in rec_rows if _outcome(entry) != "unknown"]
    prior_known = [entry for entry in prior_rows if _outcome(entry) != "unknown"]
    rec_clean = sum(1 for entry in rec_known if _outcome(entry) == "clean")
    prior_clean = sum(1 for entry in prior_known if _outcome(entry) == "clean")
    rec_pct = _pct(rec_clean, len(rec_known))
    prior_pct = _pct(prior_clean, len(prior_known))
    if rec_pct is None or prior_pct is None:
        direction = "UNKNOWN"
    else:
        difference = rec_pct - prior_pct
        direction = "UP" if difference > 10 else ("DOWN" if difference < -10 else "FLAT")
    low_n = len(rec_rows) < 6
    trend_rows.append((skill, rec_clean, len(rec_known), rec_pct,
                       prior_clean, len(prior_known), prior_pct, direction, low_n,
                       len(rec_rows), len(prior_rows)))
    trend_prints.append(
        f"  {_brief(skill, 28):<28} rec={rec_clean}/{len(rec_known)}({_pct_text(rec_pct)}) "
        f"prior={prior_clean}/{len(prior_known)}({_pct_text(prior_pct)}) {direction}"
        + (" (low N)" if low_n else ""))
if len(trend_prints) > PRINT_LIMIT:
    print(f"  ... {len(trend_prints) - PRINT_LIMIT} skill trend row(s) omitted")
for line in trend_prints[:PRINT_LIMIT]:
    print(line)

# MEMORY HITS
# Rows name a hit as a bare stem, a path, a `.md` name or any of those plus a reason.
MEMORY_HIT_HEAD = re.compile(
    r"^\s*([A-Za-z0-9_./\\-]+?)(?:\.md)?(?=\s*$|\s*:|\s+(?:—|–|-|\(|'|\"))")


def _memory_key(text):
    """The memory's file stem when the hit starts with a name or path; a legacy entity name with
    spaces stays whole."""
    match = MEMORY_HIT_HEAD.match(text)
    if not match:
        return text.strip()
    return match.group(1).replace("\\", "/").rsplit("/", 1)[-1]


mem_hits = Counter()
for entry in unique:
    hits = entry.get("memory_hits")
    for memory in {_memory_key(hit) for hit in (hits if isinstance(hits, list) else [])}:
        mem_hits[memory] += 1

print("\n=== TOP MEMORY ENTITIES (>=3 hits, structured) ===")
memory_rows = [(memory, count) for memory, count in mem_hits.most_common() if count >= 3]
if len(memory_rows) > PRINT_LIMIT:
    print(f"  ... {len(memory_rows) - PRINT_LIMIT} lower-frequency memory row(s) omitted")
for memory, count in memory_rows[:PRINT_LIMIT]:
    print(f"  {_brief(memory, 80)}: {count}")

# STREAKS
streak_now = 0
longest_streak = 0 if sorted_unique else None
for entry in sorted_unique:
    if _outcome(entry) == "clean":
        streak_now += 1
        longest_streak = max(longest_streak, streak_now)
    else:
        streak_now = 0
if not sorted_unique or _outcome(sorted_unique[-1]) == "unknown":
    current_streak = None
else:
    current_streak = 0
    for entry in reversed(sorted_unique):
        if _outcome(entry) == "clean":
            current_streak += 1
        else:
            break

print("\n=== STREAKS (structured only) ===")
print("Current clean streak: " + (str(current_streak) if current_streak is not None else "unknown"))
print("Longest clean streak: " + (str(longest_streak) if longest_streak is not None else "unknown"))

# RECENT 10
print("\n=== RECENT 10 STRUCTURED SESSIONS ===")
for entry in sorted_unique[-10:]:
    pattern = _brief(entry.get("pattern") or "?", 12)
    entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
    date = _brief(entry.get("date"), 40) if isinstance(entry.get("date"), str) else "unknown"
    title = entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>"
    print(f"  #{entry_id:>3} {date} {_outcome(entry):<10} P{str(pattern):<2} {title[:60]}")

# ALL CORRECTIONS
print("\n=== ALL CORRECTION/FAILURE (structured, deduped) ===")
correction_rows = [entry for entry in sorted_unique
                   if _outcome(entry) in ("correction", "failure")]
if len(correction_rows) > PRINT_LIMIT:
    print(f"  ... {len(correction_rows) - PRINT_LIMIT} older correction row(s) omitted; "
          "stats.json retains all rows")
for entry in correction_rows[-PRINT_LIMIT:]:
    corrections = entry.get("corrections")
    n_corrections = len(corrections) if isinstance(corrections, list) else 0
    pattern = _brief(entry.get("pattern") or "?", 12)
    entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
    date = _brief(entry.get("date"), 40) if isinstance(entry.get("date"), str) else "unknown"
    title = entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>"
    print(f"  #{entry_id:>3} {date} {_outcome(entry):<10} P{str(pattern):<2} "
          f"corrs={n_corrections} | {title[:60]}")

# CHRONOLOGICAL TIMELINE for mermaid (date -> outcome)
print("\n=== TIMELINE (chronological, structured) ===")
date_outcome_count = Counter()
for entry in sorted_unique:
    date = entry.get("date") if isinstance(entry.get("date"), str) else "unknown"
    date_outcome_count[(date, _outcome(entry))] += 1
timeline_rows = sorted(date_outcome_count.items())
if len(timeline_rows) > PRINT_LIMIT:
    print(f"  ... {len(timeline_rows) - PRINT_LIMIT} older timeline row(s) omitted; "
          "stats.json retains aggregate counts")
for (date, outcome), count in timeline_rows[-PRINT_LIMIT:]:
    print(f"  {_brief(date, 40)} {outcome:<10} count={count}")

# DATE SPAN
dates = sorted({entry["date"] for entry in unique
                if isinstance(entry.get("date"), str) and entry["date"]})
print("\n=== DATE COVERAGE ===")
print(f"Earliest: {_brief(dates[0], 40) if dates else 'unknown'}")
print(f"Latest:   {_brief(dates[-1], 40) if dates else 'unknown'}")
print(f"Distinct dates: {len(dates)}")

# Output to JSON for the writer step
output = {
    "status": analysis_status,
    "unique_count": len(unique),
    "raw_count": len(raw_entries),
    "structured_population": len(unique),
    "structured_known": len(unique) - outcomes.get("unknown", 0),
    "structured_unknown": outcomes.get("unknown", 0),
    "legacy_count": legacy_total,
    "legacy_clean": legacy_clean,
    "legacy_correction": legacy_correction,
    "legacy_failure": legacy_failure,
    "legacy_unknown": legacy_unknown,
    "total_sessions": total_sessions,
    "total_known_outcomes": total_known,
    "total_unknown_outcomes": total_unknown,
    "total_clean": total_clean,
    "total_correction": total_correction,
    "total_failure": total_failure,
    "clean_pct": clean_pct,
    "correction_pct": correction_pct,
    "failure_pct": failure_pct,
    "current_streak": current_streak,
    "longest_streak": longest_streak,
    "date_first": dates[0] if dates else None,
    "date_last": dates[-1] if dates else None,
    "structured_outcomes": dict(outcomes),
    "structured_patterns": dict(patterns),
    "structured_patterns_normalized": dict(patterns_normalized),
    "timeline": [
        {"date": date, "outcome": outcome, "count": count}
        for (date, outcome), count in timeline_rows
    ],
    "domain_perf": [
        {
            "domain": domain,
            "total": population,
            "known": population - domain_unknown[domain],
            "unknown": domain_unknown[domain],
            "clean": domain_clean[domain],
            "correction": domain_corr[domain],
            "failure": domain_fail[domain],
            "clean_pct": (round(_pct(domain_clean[domain], population - domain_unknown[domain]), 1)
                          if population - domain_unknown[domain] else None),
        }
        for domain, population in domain_total.most_common()
        if population >= 2
    ],
    "skill_perf": [
        {
            "skill": skill,
            "total": population,
            "known": population - skill_unknown[skill],
            "unknown": skill_unknown[skill],
            "clean": skill_clean[skill],
            "correction": skill_corr[skill],
            "failure": skill_fail[skill],
            "clean_pct": (round(_pct(skill_clean[skill], population - skill_unknown[skill]), 1)
                          if population - skill_unknown[skill] else None),
        }
        for skill, population in skill_total.most_common()
        if population >= 3
    ],
    "recent_window": {
        "population": len(recent),
        "known": recent_known_n,
        "unknown": len(recent) - recent_known_n,
        "clean": recent_clean_n,
        "clean_pct": round(recent_clean_pct, 1) if recent_clean_pct is not None else None,
    },
    "prior_window": {
        "population": len(prior),
        "known": prior_known_n,
        "unknown": len(prior) - prior_known_n,
        "clean": prior_clean_n,
        "clean_pct": round(prior_clean_pct, 1) if prior_clean_pct is not None else None,
    },
    "skill_trends": [
        {
            "skill": skill,
            "recent_clean": recent_clean,
            "recent_total": recent_known,
            "recent_population": recent_population,
            "recent_pct": round(recent_pct, 1) if recent_pct is not None else None,
            "prior_clean": prior_clean,
            "prior_total": prior_known,
            "prior_population": prior_population,
            "prior_pct": round(prior_pct, 1) if prior_pct is not None else None,
            "direction": direction,
            "low_n": low_n,
        }
        for (skill, recent_clean, recent_known, recent_pct,
             prior_clean, prior_known, prior_pct, direction, low_n,
             recent_population, prior_population) in trend_rows
    ],
    "top_memory_hits": [
        {"entity": m, "count": c} for m, c in mem_hits.most_common() if c >= 3
    ],
    # Every stem and its citing-session count: the evidence /autolearn cites for a review-by review.
    "memory_hit_counts": dict(mem_hits.most_common()),
    "recent_10": [
        {
            "id": entry.get("id"),
            "date": entry.get("date"),
            "outcome": _outcome(entry),
            **({"raw_outcome": entry.get("outcome")} if _outcome(entry) == "unknown" else {}),
            "pattern": entry.get("pattern", "?"),
            "title": entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>",
            **({"shape": entry["shape"]} if "shape" in entry else {}),
        }
        for entry in sorted_unique[-10:]
    ],
    "all_corrections": [
        {
            "id": entry.get("id"),
            "date": entry.get("date"),
            "outcome": _outcome(entry),
            "pattern": entry.get("pattern", "?"),
            "title": entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>",
            "n_corrections": (len(entry.get("corrections"))
                              if isinstance(entry.get("corrections"), list) else 0),
            "key_takeaway": (entry.get("key_takeaway", "")[:200]
                             if isinstance(entry.get("key_takeaway", ""), str) else ""),
            **({"shape": entry["shape"]} if "shape" in entry else {}),
        }
        for entry in sorted_unique
        if _outcome(entry) in ("correction", "failure")
    ],
}
# HARNESS_EVAL_OUT overrides the output dir for the re-runnable proof; production runs
# keep writing to /tmp/eval_out.
out_dir = os.environ.get("HARNESS_EVAL_OUT", "/tmp/eval_out")
os.makedirs(out_dir, exist_ok=True)
stats_path = os.path.join(out_dir, "stats.json")
with open(stats_path, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)
print("\nStats written to %s" % stats_path)
