"""One-shot analysis script for /eval_dashboard.

Loads .claude/self_evaluate_archive.json, dedupes structured entries by
(title, date), classifies legacy entries heuristically, computes domain
and skill performance with recent-vs-prior trend, and prints stats for
manual transcription into the Obsidian dashboard.
"""
import json
import sys
from collections import Counter

# Windows consoles default stdout to cp1252; session titles carry non-Latin-1
# glyphs (em-dash, arrow) that crash the final print loop BEFORE stats.json is
# written. Force UTF-8 so the fast path never depends on PYTHONIOENCODING.
sys.stdout.reconfigure(encoding="utf-8")

with open(".claude/self_evaluate_archive.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# DEDUPE STRUCTURED
seen = set()
unique = []
for e in data["structured_entries"]:
    key = (e["title"], e["date"])
    if key not in seen:
        seen.add(key)
        unique.append(e)

print("=== DEDUP RESULTS ===")
print(f"Raw structured entries: {len(data['structured_entries'])}")
print(f"Unique structured entries: {len(unique)}")
dup_pct = (1 - len(unique) / len(data["structured_entries"])) * 100
print(f"Duplicate-rate: {dup_pct:.1f}%")

# LEGACY HEURISTIC
legacy = data["legacy_entries"]
legacy_clean = 0
legacy_correction = 0
legacy_failure = 0
legacy_total = 0
for entry in legacy:
    s = entry.upper()
    if "#8-#19" in entry:
        legacy_clean += 12
        legacy_total += 12
        continue
    legacy_total += 1
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
        legacy_correction += 1

print("\n=== LEGACY (heuristic) ===")
print(f"Total legacy sessions: {legacy_total}")
print(f"  Clean:       {legacy_clean}")
print(f"  Correction:  {legacy_correction}")
print(f"  Failure:     {legacy_failure}")

# STRUCTURED OUTCOME COUNTS
outcomes = Counter(e["outcome"] for e in unique)
patterns = Counter(e.get("pattern", "unknown") for e in unique)


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
for p, c in patterns.most_common():
    print(f"  {p}: {c}")
print("=== STRUCTURED PATTERNS (normalized: null+clean -> C) ===")
for p, c in patterns_normalized.most_common():
    print(f"  {p}: {c}")

# COMBINED OVERVIEW
total_clean = outcomes.get("clean", 0) + legacy_clean
total_correction = outcomes.get("correction", 0) + legacy_correction
total_failure = outcomes.get("failure", 0) + legacy_failure
total_sessions = total_clean + total_correction + total_failure

print("\n=== COMBINED OVERVIEW ===")
print(f"Total sessions: {total_sessions}")
print(f"Clean:       {total_clean} ({total_clean*100/total_sessions:.1f}%)")
print(f"Correction:  {total_correction} ({total_correction*100/total_sessions:.1f}%)")
print(f"Failure:     {total_failure} ({total_failure*100/total_sessions:.1f}%)")

# DOMAIN PERFORMANCE
domain_total = Counter()
domain_clean = Counter()
domain_corr = Counter()
domain_fail = Counter()
for e in unique:
    for d in e.get("domains", []):
        domain_total[d] += 1
        if e["outcome"] == "clean":
            domain_clean[d] += 1
        elif e["outcome"] == "correction":
            domain_corr[d] += 1
        elif e["outcome"] == "failure":
            domain_fail[d] += 1

print("\n=== DOMAIN PERFORMANCE (structured) ===")
header = f"{'Domain':<20} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Cln%':>6}"
print(header)
for d, t in domain_total.most_common():
    if t < 2:
        continue
    cr = domain_clean[d] * 100 / t
    print(f"{d:<20} {t:>4} {domain_clean[d]:>4} {domain_corr[d]:>4} {domain_fail[d]:>4} {cr:>5.1f}%")

# SKILL PERFORMANCE
skill_total = Counter()
skill_clean = Counter()
skill_corr = Counter()
skill_fail = Counter()
for e in unique:
    for s in e.get("skills_used", []):
        skill_total[s] += 1
        if e["outcome"] == "clean":
            skill_clean[s] += 1
        elif e["outcome"] == "correction":
            skill_corr[s] += 1
        elif e["outcome"] == "failure":
            skill_fail[s] += 1

print("\n=== SKILL PERFORMANCE (>=3 loads) ===")
print(f"{'Skill':<28} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Cln%':>6}")
for s, t in skill_total.most_common():
    if t < 3:
        continue
    cr = skill_clean[s] * 100 / t
    print(f"{s:<28} {t:>4} {skill_clean[s]:>4} {skill_corr[s]:>4} {skill_fail[s]:>4} {cr:>5.1f}%")

# TREND ANALYSIS
sorted_unique = sorted(unique, key=lambda e: (e["date"], e["id"]))
print("\n=== TREND (recent 10 vs prior 10, structured) ===")
print(f"Date range: {sorted_unique[0]['date']} - {sorted_unique[-1]['date']}")
recent = sorted_unique[-10:]
prior = sorted_unique[-20:-10]
rec_clean_n = sum(1 for e in recent if e["outcome"] == "clean")
prior_clean_n = sum(1 for e in prior if e["outcome"] == "clean")
print(f"Recent 10:  clean={rec_clean_n}/10")
print(f"Prior 10:   clean={prior_clean_n}/10")

print("\nPer-skill trend:")
trend_rows = []
for skill in skill_total:
    if skill_total[skill] < 3:
        continue
    rec_count = sum(1 for e in recent if skill in e.get("skills_used", []))
    rec_clean = sum(1 for e in recent if skill in e.get("skills_used", []) and e["outcome"] == "clean")
    prior_count = sum(1 for e in prior if skill in e.get("skills_used", []))
    prior_clean = sum(1 for e in prior if skill in e.get("skills_used", []) and e["outcome"] == "clean")
    if rec_count == 0 and prior_count == 0:
        continue
    rec_pct = rec_clean * 100 / rec_count if rec_count else 0
    prior_pct = prior_clean * 100 / prior_count if prior_count else 0
    diff = rec_pct - prior_pct
    direction = "UP" if diff > 10 else ("DOWN" if diff < -10 else "FLAT")
    flag = " (low N)" if rec_count < 3 else ""
    trend_rows.append((skill, rec_clean, rec_count, rec_pct, prior_clean, prior_count, prior_pct, direction, flag))
    print(f"  {skill:<28} rec={rec_clean}/{rec_count}({rec_pct:.0f}%) prior={prior_clean}/{prior_count}({prior_pct:.0f}%) {direction}{flag}")

# MEMORY HITS
mem_hits = Counter()
for e in unique:
    for m in e.get("memory_hits", []):
        mem_hits[m] += 1

print("\n=== TOP MEMORY ENTITIES (>=3 hits, structured) ===")
for m, c in mem_hits.most_common():
    if c >= 3:
        print(f"  {m}: {c}")

# STREAKS
streak_now = 0
longest_streak = 0
for e in sorted_unique:
    if e["outcome"] == "clean":
        streak_now += 1
        longest_streak = max(longest_streak, streak_now)
    else:
        streak_now = 0
current_streak = 0
for e in reversed(sorted_unique):
    if e["outcome"] == "clean":
        current_streak += 1
    else:
        break

print("\n=== STREAKS (structured only) ===")
print(f"Current clean streak: {current_streak}")
print(f"Longest clean streak: {longest_streak}")

# RECENT 10
print("\n=== RECENT 10 STRUCTURED SESSIONS ===")
for e in sorted_unique[-10:]:
    pat = e.get('pattern') or '?'
    print(f"  #{e['id']:>3} {e['date']} {e['outcome']:<10} P{pat:<2} {e['title'][:60]}")

# ALL CORRECTIONS
print("\n=== ALL CORRECTION/FAILURE (structured, deduped) ===")
for e in sorted_unique:
    if e["outcome"] in ("correction", "failure"):
        n_corr = len(e.get("corrections", []))
        pat = e.get('pattern') or '?'
        print(f"  #{e['id']:>3} {e['date']} {e['outcome']:<10} P{pat:<2} corrs={n_corr} | {e['title'][:60]}")

# CHRONOLOGICAL TIMELINE for mermaid (date -> outcome)
print("\n=== TIMELINE (chronological, structured) ===")
date_outcome_count = Counter()
for e in sorted_unique:
    date_outcome_count[(e["date"], e["outcome"])] += 1
for (d, o), c in sorted(date_outcome_count.items()):
    print(f"  {d} {o:<10} count={c}")

# DATE SPAN
dates = sorted(set(e["date"] for e in unique))
print("\n=== DATE COVERAGE ===")
print(f"Earliest: {dates[0]}")
print(f"Latest:   {dates[-1]}")
print(f"Distinct dates: {len(dates)}")

# Output to JSON for the writer step
import os
os.makedirs("/tmp/eval_out", exist_ok=True)
output = {
    "unique_count": len(unique),
    "raw_count": len(data["structured_entries"]),
    "legacy_count": legacy_total,
    "legacy_clean": legacy_clean,
    "legacy_correction": legacy_correction,
    "legacy_failure": legacy_failure,
    "total_clean": total_clean,
    "total_correction": total_correction,
    "total_failure": total_failure,
    "current_streak": current_streak,
    "longest_streak": longest_streak,
    "date_first": dates[0],
    "date_last": dates[-1],
    "structured_outcomes": dict(outcomes),
    "structured_patterns": dict(patterns),
    "structured_patterns_normalized": dict(patterns_normalized),
    "domain_perf": [
        {
            "domain": d,
            "total": t,
            "clean": domain_clean[d],
            "correction": domain_corr[d],
            "failure": domain_fail[d],
            "clean_pct": round(domain_clean[d] * 100 / t, 1),
        }
        for d, t in domain_total.most_common()
        if t >= 2
    ],
    "skill_perf": [
        {
            "skill": s,
            "total": t,
            "clean": skill_clean[s],
            "correction": skill_corr[s],
            "failure": skill_fail[s],
            "clean_pct": round(skill_clean[s] * 100 / t, 1),
        }
        for s, t in skill_total.most_common()
        if t >= 3
    ],
    "skill_trends": [
        {
            "skill": skill,
            "recent_clean": rc,
            "recent_total": rt,
            "recent_pct": round(rp, 1),
            "prior_clean": pc,
            "prior_total": pt,
            "prior_pct": round(pp, 1),
            "direction": dir_,
            "low_n": flag.strip() != "",
        }
        for skill, rc, rt, rp, pc, pt, pp, dir_, flag in trend_rows
    ],
    "top_memory_hits": [
        {"entity": m, "count": c} for m, c in mem_hits.most_common() if c >= 3
    ],
    "recent_10": [
        {
            "id": e["id"],
            "date": e["date"],
            "outcome": e["outcome"],
            "pattern": e.get("pattern", "?"),
            "title": e["title"],
        }
        for e in sorted_unique[-10:]
    ],
    "all_corrections": [
        {
            "id": e["id"],
            "date": e["date"],
            "outcome": e["outcome"],
            "pattern": e.get("pattern", "?"),
            "title": e["title"],
            "n_corrections": len(e.get("corrections", [])),
            "key_takeaway": e.get("key_takeaway", "")[:200],
        }
        for e in sorted_unique
        if e["outcome"] in ("correction", "failure")
    ],
}
with open("/tmp/eval_out/stats.json", "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2)
print("\nStats written to /tmp/eval_out/stats.json")
