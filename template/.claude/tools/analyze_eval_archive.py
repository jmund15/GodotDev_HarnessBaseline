"""Session statistics for /eval_dashboard: `compute(root)` is the library entry, the CLI writes stats.json.

Streams the legacy self-evaluation snapshot plus its bounded JSONL ledger,
keeps the newest structured row per session, classifies legacy entries
heuristically, and writes dashboard statistics.
"""
import argparse
import datetime
import json
import os
import re
import sys
from collections import Counter

PRINT_LIMIT = 20

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
from self_eval_archive_store import load_archive  # noqa: E402
sys.path.insert(0, os.path.join(TOOLS, "..", "hooks"))
from self_eval_archive_guard import OUTCOMES, LEGACY_MAX_ID, RULE_MISS_KINDS, check_recurrences  # noqa: E402


class AnalysisError(ValueError):
    pass


METADATA_FIELDS = ("domains", "skills_used", "memory_hits")
DURATION_EDGES = (300, 700)
BASE_PATTERNS = ("A", "B", "C", "D", "E", "none")
DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Rows name a hit as a bare stem, a path, a `.md` name or any of those plus a reason.
MEMORY_HIT_HEAD = re.compile(
    r"^\s*([A-Za-z0-9_./\\-]+?)(?:\.md)?(?=\s*$|\s*:|\s+(?:—|–|-|\(|'|\"))")



def effective_entries(raw_entries):
    """The newest row per session; ledger order is oldest to newest, so later upserts win."""
    effective = {}
    for entry in raw_entries:
        session_id = entry.get("session_id")
        key = (("session", session_id) if session_id
               else ("legacy", entry.get("title"), entry.get("date")))
        effective[key] = entry
    return list(effective.values())


def calendar_day(value):
    """`YYYY-MM-DD` when `value` is a string that opens with a real calendar day, else None."""
    if not isinstance(value, str) or not DATED.match(value):
        return None
    try:
        datetime.date.fromisoformat(value[:10])
    except ValueError:
        return None
    return value[:10]


def in_month(entry, month):
    day = calendar_day(entry.get("date"))
    return day is not None and day.startswith(month)


def dated_months(stats):
    """The sorted `YYYY-MM` of every month that holds a dated structured entry, from a `compute()` result.
    Legacy prose rows carry no reliable date and belong to no month."""
    return sorted({day[:7] for day in (calendar_day(item["date"]) for item in stats["timeline"]) if day})


def _outcome(entry):
    value = entry.get("outcome")
    return value if value in OUTCOMES else "unknown"


def _brief(value, limit=60):
    return str(value)[:limit]


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


def _pct(numerator, denominator):
    return numerator * 100 / denominator if denominator else None


def _pct_text(value):
    return f"{value:.1f}%" if value is not None else "unknown"


def _sort_key(entry):
    date = entry.get("date") if isinstance(entry.get("date"), str) else ""
    entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
    return date, entry_id


def _skills(entry):
    value = entry.get("skills_used")
    return value if isinstance(value, list) else []


def _domains(entry):
    value = entry.get("domains")
    return value if isinstance(value, list) else []


def _rate_parts(entry):
    """(corrections, owner_prompts) for an entry that carries both halves of the corrections-per-prompt
    rate, else (None, None). A row without the store-derived `facts.owner_prompts` is typed absent, never
    a zero-prompt session, so the rate's numerator and denominator come from the same rows."""
    corrections = entry.get("corrections")
    facts = entry.get("facts")
    prompts = facts.get("owner_prompts") if isinstance(facts, dict) else None
    if isinstance(prompts, bool) or not isinstance(prompts, int) or prompts < 0:
        prompts = None
    if not isinstance(corrections, list) or prompts is None:
        return None, None
    return len(corrections), prompts


def _driver_name(entry):
    """`<model> / <effort>` from the store-derived `facts.driver`, or None when the entry carries no
    driver — a row with no facts belongs to no driver row, never to an `unknown` one."""
    facts = entry.get("facts")
    driver = facts.get("driver") if isinstance(facts, dict) else None
    if not isinstance(driver, dict):
        return None
    model, effort = driver.get("model"), driver.get("effort")
    if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
        return None
    return "%s / %s" % (model, effort)


def _drivers_of(entry):
    """The entry's driver name as a one-item list, or an empty list when it carries none."""
    name = _driver_name(entry)
    return [name] if name else []


def _by_name(entries, names_of):
    """{name: outcome counts and rate sums} over entries, each name counted once per entry, in first-seen
    order. `corrections`, `prompts` and `facts_rows` sum over the entries that carry both halves of the
    rate, so a cell's rate divides its own population."""
    table = {}
    for entry in entries:
        bucket = _outcome(entry)
        corrections, prompts = _rate_parts(entry)
        for name in dict.fromkeys(names_of(entry)):
            cell = table.setdefault(name, {"n": 0, "clean": 0, "correction": 0, "failure": 0, "unknown": 0,
                                           "corrections": 0, "prompts": 0, "facts_rows": 0})
            cell["n"] += 1
            cell[bucket] += 1
            if corrections is not None:
                cell["corrections"] += corrections
                cell["prompts"] += prompts
                cell["facts_rows"] += 1
    return table


def _duration_bucket(entry):
    """`lt300 | 300_700 | gt700 | unknown`. New rows carry the store-derived
    `facts.duration_min`; a row written before that field keeps the model-typed
    `shape.duration_min`, and a row with neither is unknown — never a zero-minute session."""
    facts = entry.get("facts")
    minutes = facts.get("duration_min") if isinstance(facts, dict) else None
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        shape = entry.get("shape")
        minutes = shape.get("duration_min") if isinstance(shape, dict) else None
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        return "unknown"
    if minutes < DURATION_EDGES[0]:
        return "lt300"
    return "300_700" if minutes <= DURATION_EDGES[1] else "gt700"


def sessions_block(entries):
    """The counts a month snapshot stores for `entries` (structured, newest row per session): outcomes, the
    normalized pattern tally, the same tally over the correction and failure sessions alone (`causes`), clean and total per domain and per skill, and clean and total per duration
    bucket. Odd keys: the corrections-per-prompt sums (`corrections`, `prompts`, `facts_rows`), the driver
    table, the recorded `cause_counts` with the rows that recorded it, and the `rule_misses` session counts
    by kind with the rows that recorded them. Counts, never rates, so blocks merge and compare exactly;
    a field no row carries is absent (`{}` / `0` rows recorded), never a zero count. `compute` reads it too,
    so the report and the snapshot cannot disagree."""
    outcomes = Counter(_outcome(entry) for entry in entries)
    patterns = dict.fromkeys(BASE_PATTERNS, 0)
    causes = dict.fromkeys(BASE_PATTERNS, 0)
    duration = {name: {"n": 0, "clean": 0, "corrections": 0, "prompts": 0, "facts_rows": 0}
                for name in ("lt300", "300_700", "gt700")}
    duration["unknown"] = 0
    totals = {"corrections": 0, "prompts": 0, "facts_rows": 0}
    cause_counts, cause_counts_rows = Counter(), 0
    rule_kinds, rule_miss_rows = Counter(), 0
    for entry in entries:
        key = _norm_pattern(entry)
        key = "none" if key == "None" else key
        patterns[key] = patterns.get(key, 0) + 1
        if _outcome(entry) in ("correction", "failure"):
            causes[key] = causes.get(key, 0) + 1
        corrections, prompts = _rate_parts(entry)
        if corrections is not None:
            totals["corrections"] += corrections
            totals["prompts"] += prompts
            totals["facts_rows"] += 1
        bucket = _duration_bucket(entry)
        if bucket == "unknown":
            duration["unknown"] += 1
        else:
            duration[bucket]["n"] += 1
            duration[bucket]["clean"] += 1 if _outcome(entry) == "clean" else 0
            if corrections is not None:
                duration[bucket]["corrections"] += corrections
                duration[bucket]["prompts"] += prompts
                duration[bucket]["facts_rows"] += 1
        recorded = entry.get("cause_counts")
        if isinstance(recorded, dict):
            cause_counts_rows += 1
            cause_counts.update({code: count for code, count in recorded.items()
                                 if isinstance(count, int) and not isinstance(count, bool) and count >= 0})
        misses = entry.get("rule_misses")
        if isinstance(misses, list):
            rule_miss_rows += 1
            kinds = {item.get("kind") for item in misses if isinstance(item, dict)}
            for kind in sorted(kinds, key=str):     # sorted: stats.json is dumped unsorted, so a set's order would drift
                if kind in RULE_MISS_KINDS:
                    rule_kinds[kind] += 1
    # The stored domain and skill cells stay narrow (`n`, `clean` and the rate sums) so the month bytes
    # grow by the sums alone; the driver table is new and carries the outcome counts its reader shows.
    named = {field: {name: {"n": cell["n"], "clean": cell["clean"], "corrections": cell["corrections"],
                            "prompts": cell["prompts"], "facts_rows": cell["facts_rows"]}
                     for name, cell in _by_name(entries, get).items()}
             for field, get in (("domains", _domains), ("skills", _skills))}
    named["drivers"] = {name: dict(cell) for name, cell in _by_name(entries, _drivers_of).items()}
    return {"n": len(entries), "clean": outcomes.get("clean", 0), "correction": outcomes.get("correction", 0),
            "failure": outcomes.get("failure", 0), "unknown": outcomes.get("unknown", 0), "patterns": patterns,
            "causes": causes, "domains": named["domains"], "skills": named["skills"], "duration": duration,
            "drivers": named["drivers"], **totals,
            "cause_counts": dict(cause_counts), "cause_counts_rows": cause_counts_rows,
            "rule_misses": dict(rule_kinds), "rule_miss_rows": rule_miss_rows}


def empty_sessions():
    """The sessions block of a month that holds no structured entry."""
    return sessions_block([])


def rule_miss_table(entries):
    """The live per-rule table: one row per (rule, kind) with the sessions that named it and up to three
    ids, the most-named first. Live only — the month snapshot stores the per-kind counts, not this, so the
    month bytes stay small. A row without `rule_misses` is typed absent, never a row of zeroes."""
    table = {}
    for entry in entries:
        items = entry.get("rule_misses")
        if not isinstance(items, list):
            continue
        identity = entry.get("session_id") or entry.get("id")
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("rule"), str) or not item["rule"].strip():
                continue
            key = (item["rule"], item.get("kind"))
            if key in seen:
                continue
            seen.add(key)
            cell = table.setdefault(key, {"rule": item["rule"], "kind": item.get("kind"), "sessions": 0, "ids": []})
            cell["sessions"] += 1
            if identity is not None and len(cell["ids"]) < 3:
                cell["ids"].append(identity)
    return sorted(table.values(), key=lambda row: (-row["sessions"], row["rule"], str(row["kind"])))


def _memory_key(text):
    """The memory's file stem when the hit starts with a name or path; a legacy entity name with
    spaces stays whole."""
    match = MEMORY_HIT_HEAD.match(text)
    if not match:
        return text.strip()
    return match.group(1).replace("\\", "/").rsplit("/", 1)[-1]


def compute(root, *, month=None, emit=None):
    """The stats.json content for the archive under `root`; `emit` receives the console report.

    `month` (YYYY-MM) keeps structured entries dated in that month and drops the undated legacy prose.
    Raises AnalysisError when the archive cannot be trusted."""
    emit = emit or (lambda *args, **kwargs: None)
    archive_path = os.path.join(root, ".claude", "self_evaluate_archive.json")
    try:
        data, raw_entries = load_archive(archive_path)
    except ValueError as exc:
        raise AnalysisError(str(exc)) from exc
    legacy = data.get("legacy_entries")
    themes = data.get("Self_Evaluate_Themes")
    pattern_defs = themes.get("patterns") if isinstance(themes, dict) else None
    if not isinstance(legacy, list) or not isinstance(pattern_defs, dict):
        raise AnalysisError("legacy_entries must be a list and Self_Evaluate_Themes.patterns an object")
    data["structured_entries"] = raw_entries

    unique = effective_entries(raw_entries)
    if month:
        raw_entries = [entry for entry in raw_entries if in_month(entry, month)]
        unique = [entry for entry in unique if in_month(entry, month)]
        legacy = []

    recurrences = []
    for entry in unique:
        if 'codified_recurrences' not in entry:
            continue
        errors = check_recurrences(entry['codified_recurrences'], f"id={entry.get('id')}")
        if errors:
            raise AnalysisError('; '.join(errors))
        for row in entry['codified_recurrences']:
            changed = row['behaviorChanged']
            status = ('unclassified' if changed is None else 'changed behavior' if changed
                      else 'reach-only repair recurred' if row.get('repairKind') == 'reach-only'
                      else 'text-only fix recurred')
            recurrences.append(dict(row, session_id=entry.get('session_id'),
                                    id=entry.get('id'), date=entry.get('date'), status=status))
    if recurrences:
        emit('\n=== CODIFIED RECURRENCES (explicit evidence, no fuzzy matching) ===')
    for row in recurrences[:PRINT_LIMIT]:
        emit(f"  {row['session_id']}: {row['lessonId']} / {row['claimId']} / "
             f"{row['failureKey']}: {row['status']}")
    if len(recurrences) > PRINT_LIMIT:
        emit(f'  ... {len(recurrences) - PRINT_LIMIT} more; stats.json retains every recurrence')

    metadata_errors = _metadata_errors(unique)
    if metadata_errors:
        suffix = (f", ... {len(metadata_errors) - PRINT_LIMIT} row(s) omitted"
                  if len(metadata_errors) > PRINT_LIMIT else "")
        raise AnalysisError("malformed structured metadata: "
                            + "; ".join(metadata_errors[:PRINT_LIMIT]) + suffix)


    malformed = [entry for entry in unique if _outcome(entry) == "unknown"]
    if malformed:
        preview = malformed[:PRINT_LIMIT]
        suffix = (f", ... {len(malformed) - PRINT_LIMIT} row(s) omitted"
                  if len(malformed) > PRINT_LIMIT else "")
        emit(f"WARNING: {len(malformed)} structured entr(y/ies) have unknown outcome evidence: "
              + ", ".join(f"id={_brief(entry.get('id'), 20)} date={_brief(entry.get('date'), 40)}"
                          for entry in preview) + suffix)
    invalid_outcomes = [entry for entry in unique
                        if "outcome" in entry and entry["outcome"] not in OUTCOMES]
    for entry in invalid_outcomes[:PRINT_LIMIT]:
        emit(f"WARNING: id={_brief(entry.get('id'), 20)} has non-enum outcome "
              f"{_brief(entry['outcome'], 40)!r} — counted as unknown")
    if len(invalid_outcomes) > PRINT_LIMIT:
        emit(f"WARNING: {len(invalid_outcomes) - PRINT_LIMIT} non-enum outcome warning row(s) omitted")

    PATTERNS = set(pattern_defs) | {None}
    invalid_patterns = [entry for entry in unique
                        if isinstance(entry.get("id"), int)
                        and entry["id"] > LEGACY_MAX_ID
                        and entry.get("pattern") not in PATTERNS]
    for entry in invalid_patterns[:PRINT_LIMIT]:
        emit(f"WARNING: id={_brief(entry.get('id'), 20)} has non-enum pattern "
              f"{_brief(entry.get('pattern'), 40)!r} — normalize it")
    if len(invalid_patterns) > PRINT_LIMIT:
        emit(f"WARNING: {len(invalid_patterns) - PRINT_LIMIT} non-enum pattern warning row(s) omitted")

    emit("=== DEDUP RESULTS ===")
    emit(f"Raw structured entries: {len(raw_entries)}")
    emit(f"Unique structured entries: {len(unique)}")
    superseded_pct = ((1 - len(unique) / len(raw_entries)) * 100
                      if raw_entries else None)
    emit("Superseded-row rate: "
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

    emit("\n=== LEGACY (heuristic) ===")
    emit(f"Total legacy sessions: {legacy_total}")
    emit(f"  Clean:       {legacy_clean}")
    emit(f"  Correction:  {legacy_correction}")
    emit(f"  Failure:     {legacy_failure}")
    emit(f"  Unknown:     {legacy_unknown}")

    # STRUCTURED OUTCOME COUNTS
    outcomes = Counter(_outcome(entry) for entry in unique)
    patterns = Counter(entry.get("pattern", "unknown") for entry in unique)


    patterns_normalized = Counter(_norm_pattern(e) for e in unique)

    emit("\n=== STRUCTURED OUTCOMES ===")
    for o, c in outcomes.most_common():
        emit(f"  {o}: {c}")
    emit("\n=== STRUCTURED PATTERNS (raw) ===")
    pattern_rows = patterns.most_common()
    for p, c in pattern_rows[:PRINT_LIMIT]:
        emit(f"  {_brief(p)}: {c}")
    if len(pattern_rows) > PRINT_LIMIT:
        emit(f"  ... {len(pattern_rows) - PRINT_LIMIT} pattern row(s) omitted")
    emit("=== STRUCTURED PATTERNS (normalized: null+clean -> C) ===")
    normalized_rows = patterns_normalized.most_common()
    for p, c in normalized_rows[:PRINT_LIMIT]:
        emit(f"  {_brief(p)}: {c}")
    if len(normalized_rows) > PRINT_LIMIT:
        emit(f"  ... {len(normalized_rows) - PRINT_LIMIT} normalized pattern row(s) omitted")

    # COMBINED OVERVIEW
    total_clean = outcomes.get("clean", 0) + legacy_clean
    total_correction = outcomes.get("correction", 0) + legacy_correction
    total_failure = outcomes.get("failure", 0) + legacy_failure
    total_unknown = outcomes.get("unknown", 0) + legacy_unknown
    total_sessions = total_clean + total_correction + total_failure + total_unknown
    total_known = total_sessions - total_unknown
    analysis_status = ("no-data" if total_sessions == 0
                       else "partial" if total_unknown else "complete")


    clean_pct = _pct(total_clean, total_sessions)
    correction_pct = _pct(total_correction, total_sessions)
    failure_pct = _pct(total_failure, total_sessions)
    emit("\n=== COMBINED OVERVIEW ===")
    emit(f"Status: {analysis_status}")
    emit(f"Total sessions: {total_sessions}")
    emit(f"Clean:       {total_clean} ({_pct_text(clean_pct)})")
    emit(f"Correction:  {total_correction} ({_pct_text(correction_pct)})")
    emit(f"Failure:     {total_failure} ({_pct_text(failure_pct)})")
    emit(f"Unknown:     {total_unknown}")

    # DOMAIN PERFORMANCE
    domain_stats = _by_name(unique, _domains)
    domain_total = Counter({name: cell["n"] for name, cell in domain_stats.items()})

    emit("\n=== DOMAIN PERFORMANCE (structured) ===")
    header = f"{'Domain':<20} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Unk':>4} {'Cln%':>7}"
    emit(header)
    domain_rows = [(domain, population) for domain, population in domain_total.most_common()
                   if population >= 2]
    if len(domain_rows) > PRINT_LIMIT:
        emit(f"  ... {len(domain_rows) - PRINT_LIMIT} lower-frequency domain row(s) omitted")
    for domain, population in domain_rows[:PRINT_LIMIT]:
        cell = domain_stats[domain]
        known = population - cell["unknown"]
        clean_rate = _pct(cell["clean"], known)
        emit(f"{_brief(domain, 20):<20} {population:>4} {cell['clean']:>4} {cell['correction']:>4} "
              f"{cell['failure']:>4} {cell['unknown']:>4} {_pct_text(clean_rate):>7}")

    # SKILL PERFORMANCE
    skill_stats = _by_name(unique, _skills)
    skill_total = Counter({name: cell["n"] for name, cell in skill_stats.items()})

    emit("\n=== SKILL PERFORMANCE (>=3 loads) ===")
    emit(f"{'Skill':<28} {'Tot':>4} {'Cln':>4} {'Cor':>4} {'Fal':>4} {'Unk':>4} {'Cln%':>7}")
    skill_rows = [(skill, population) for skill, population in skill_total.most_common()
                  if population >= 3]
    if len(skill_rows) > PRINT_LIMIT:
        emit(f"  ... {len(skill_rows) - PRINT_LIMIT} lower-frequency skill row(s) omitted")
    for skill, population in skill_rows[:PRINT_LIMIT]:
        cell = skill_stats[skill]
        known = population - cell["unknown"]
        clean_rate = _pct(cell["clean"], known)
        emit(f"{_brief(skill, 28):<28} {population:>4} {cell['clean']:>4} {cell['correction']:>4} "
              f"{cell['failure']:>4} {cell['unknown']:>4} {_pct_text(clean_rate):>7}")

    # TREND ANALYSIS
    sorted_unique = sorted(unique, key=_sort_key)
    recent = sorted_unique[-10:]
    prior = sorted_unique[-20:-10]
    recent_clean_n = sum(1 for entry in recent if _outcome(entry) == "clean")
    prior_clean_n = sum(1 for entry in prior if _outcome(entry) == "clean")
    recent_known_n = sum(1 for entry in recent if _outcome(entry) != "unknown")
    prior_known_n = sum(1 for entry in prior if _outcome(entry) != "unknown")
    recent_clean_pct = _pct(recent_clean_n, recent_known_n)
    prior_clean_pct = _pct(prior_clean_n, prior_known_n)
    emit("\n=== TREND (recent 10 vs prior 10, structured) ===")
    if sorted_unique:
        emit(f"Date range: {_brief(_sort_key(sorted_unique[0])[0] or 'unknown', 40)} - "
              f"{_brief(_sort_key(sorted_unique[-1])[0] or 'unknown', 40)}")
    else:
        emit("Date range: unknown (no structured sessions)")
    emit(f"Recent window: clean={recent_clean_n}/{recent_known_n} known, "
          f"population={len(recent)}, unknown={len(recent) - recent_known_n}")
    emit(f"Prior window:  clean={prior_clean_n}/{prior_known_n} known, "
          f"population={len(prior)}, unknown={len(prior) - prior_known_n}")

    emit("\nPer-skill trend:")
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
        emit(f"  ... {len(trend_prints) - PRINT_LIMIT} skill trend row(s) omitted")
    for line in trend_prints[:PRINT_LIMIT]:
        emit(line)

    # MEMORY HITS

    mem_hits = Counter()
    for entry in unique:
        hits = entry.get("memory_hits")
        for memory in sorted({_memory_key(hit) for hit in (hits if isinstance(hits, list) else [])}):
            mem_hits[memory] += 1

    emit("\n=== TOP MEMORY ENTITIES (>=3 hits, structured) ===")
    memory_rows = [(memory, count) for memory, count in mem_hits.most_common() if count >= 3]
    if len(memory_rows) > PRINT_LIMIT:
        emit(f"  ... {len(memory_rows) - PRINT_LIMIT} lower-frequency memory row(s) omitted")
    for memory, count in memory_rows[:PRINT_LIMIT]:
        emit(f"  {_brief(memory, 80)}: {count}")

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

    emit("\n=== STREAKS (structured only) ===")
    emit("Current clean streak: " + (str(current_streak) if current_streak is not None else "unknown"))
    emit("Longest clean streak: " + (str(longest_streak) if longest_streak is not None else "unknown"))

    # RECENT 10
    emit("\n=== RECENT 10 STRUCTURED SESSIONS ===")
    for entry in sorted_unique[-10:]:
        pattern = _brief(entry.get("pattern") or "?", 12)
        entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
        date = _brief(entry.get("date"), 40) if isinstance(entry.get("date"), str) else "unknown"
        title = entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>"
        emit(f"  #{entry_id:>3} {date} {_outcome(entry):<10} P{str(pattern):<2} {title[:60]}")

    # ALL CORRECTIONS
    emit("\n=== ALL CORRECTION/FAILURE (structured, deduped) ===")
    correction_rows = [entry for entry in sorted_unique
                       if _outcome(entry) in ("correction", "failure")]
    if len(correction_rows) > PRINT_LIMIT:
        emit(f"  ... {len(correction_rows) - PRINT_LIMIT} older correction row(s) omitted; "
              "stats.json retains all rows")
    for entry in correction_rows[-PRINT_LIMIT:]:
        corrections = entry.get("corrections")
        n_corrections = len(corrections) if isinstance(corrections, list) else 0
        pattern = _brief(entry.get("pattern") or "?", 12)
        entry_id = entry.get("id") if isinstance(entry.get("id"), int) else -1
        date = _brief(entry.get("date"), 40) if isinstance(entry.get("date"), str) else "unknown"
        title = entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>"
        emit(f"  #{entry_id:>3} {date} {_outcome(entry):<10} P{str(pattern):<2} "
              f"corrs={n_corrections} | {title[:60]}")

    # CHRONOLOGICAL TIMELINE for mermaid (date -> outcome)
    emit("\n=== TIMELINE (chronological, structured) ===")
    date_outcome_count = Counter()
    for entry in sorted_unique:
        date = entry.get("date") if isinstance(entry.get("date"), str) else "unknown"
        date_outcome_count[(date, _outcome(entry))] += 1
    timeline_rows = sorted(date_outcome_count.items())
    if len(timeline_rows) > PRINT_LIMIT:
        emit(f"  ... {len(timeline_rows) - PRINT_LIMIT} older timeline row(s) omitted; "
              "stats.json retains aggregate counts")
    for (date, outcome), count in timeline_rows[-PRINT_LIMIT:]:
        emit(f"  {_brief(date, 40)} {outcome:<10} count={count}")

    # DATE SPAN
    dates = sorted({entry["date"] for entry in unique
                    if isinstance(entry.get("date"), str) and entry["date"]})
    emit("\n=== DATE COVERAGE ===")
    emit(f"Earliest: {_brief(dates[0], 40) if dates else 'unknown'}")
    emit(f"Latest:   {_brief(dates[-1], 40) if dates else 'unknown'}")
    emit(f"Distinct dates: {len(dates)}")

    # Output to JSON for the writer step
    output = {
        "status": analysis_status,
        **({"codified_recurrences": recurrences} if recurrences else {}),
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
                "known": population - domain_stats[domain]["unknown"],
                "unknown": domain_stats[domain]["unknown"],
                "clean": domain_stats[domain]["clean"],
                "correction": domain_stats[domain]["correction"],
                "failure": domain_stats[domain]["failure"],
                "clean_pct": (round(_pct(domain_stats[domain]["clean"], population - domain_stats[domain]["unknown"]), 1)
                              if population - domain_stats[domain]["unknown"] else None),
                "corrections": domain_stats[domain]["corrections"],
                "prompts": domain_stats[domain]["prompts"],
                "facts_rows": domain_stats[domain]["facts_rows"],
            }
            for domain, population in domain_total.most_common()
            if population >= 2
        ],
        "skill_perf": [
            {
                "skill": skill,
                "total": population,
                "known": population - skill_stats[skill]["unknown"],
                "unknown": skill_stats[skill]["unknown"],
                "clean": skill_stats[skill]["clean"],
                "correction": skill_stats[skill]["correction"],
                "failure": skill_stats[skill]["failure"],
                "clean_pct": (round(_pct(skill_stats[skill]["clean"], population - skill_stats[skill]["unknown"]), 1)
                              if population - skill_stats[skill]["unknown"] else None),
                "corrections": skill_stats[skill]["corrections"],
                "prompts": skill_stats[skill]["prompts"],
                "facts_rows": skill_stats[skill]["facts_rows"],
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
        "sessions": sessions_block(unique),
        "rule_misses": rule_miss_table(unique),
        "all_corrections": [
            {
                "id": entry.get("id"),
                "date": entry.get("date"),
                "outcome": _outcome(entry),
                "pattern": entry.get("pattern", "?"),
                "title": entry.get("title") if isinstance(entry.get("title"), str) else "<untitled>",
                "n_corrections": (len(entry.get("corrections"))
                                  if isinstance(entry.get("corrections"), list) else 0),
                "domains": list(dict.fromkeys(_domains(entry))),
                "key_takeaway": (entry.get("key_takeaway", "")[:200]
                                 if isinstance(entry.get("key_takeaway", ""), str) else ""),
                **({"shape": entry["shape"]} if "shape" in entry else {}),
            }
            for entry in sorted_unique
            if _outcome(entry) in ("correction", "failure")
        ],
    }
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repo root holding .claude/ (default: the cwd)")
    args = parser.parse_args(argv)
    # Windows consoles default stdout to cp1252; session titles carry non-Latin-1
    # glyphs (em-dash, arrow) that crash the final print loop BEFORE stats.json is
    # written. Force UTF-8 so the fast path never depends on PYTHONIOENCODING.
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        output = compute(args.root, emit=print)
    except AnalysisError as exc:
        print("UNKNOWN: " + str(exc))
        return 2
    # HARNESS_EVAL_OUT overrides the output dir for the re-runnable proof; production runs
    # keep writing to /tmp/eval_out.
    out_dir = os.environ.get("HARNESS_EVAL_OUT", "/tmp/eval_out")
    os.makedirs(out_dir, exist_ok=True)
    stats_path = os.path.join(out_dir, "stats.json")
    with open(stats_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(output, f, indent=2)
    print("\nStats written to %s" % stats_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
