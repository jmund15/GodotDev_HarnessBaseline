"""Re-runnable proof for tools/analyze_eval_archive.py.

Runs the analyzer as a subprocess against a fixture archive containing a
`shape`-bearing entry and a non-enum-outcome entry, then asserts the WARNING
fires and that `shape` survives into `stats.json`'s `recent_10` row.

    python3 .claude/tests/test_analyze_eval_archive.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALYZER = os.path.join(ROOT, "tools", "analyze_eval_archive.py")

FIXTURE = {
    "Self_Evaluate_Themes": {"patterns": {"A": "fixture", "B": "fixture", "C": "fixture", "D": "fixture", "E": "fixture"}},
    "structured_entries": [
        {
            "session_id": "sess-shape",
            "id": 1,
            "title": "Entry with shape",
            "date": "2026-09-01",
            "shape": {"compactions": 2, "duration_min": 45, "slices": 3, "drive_command": "/part_execute"},
            "outcome": "clean",
            "pattern": None,
            "domains": ["meta"],
            "corrections": [],
            "skills_used": [],
            "memory_searches": 0,
            "memory_hits": [],
            "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
            "key_takeaway": "shape carries through",
            "notes": "",
        },
        {
            "session_id": "sess-badoutcome",
            "id": 2,
            "title": "Entry with non-enum outcome",
            "date": "2026-09-02",
            "outcome": "mixed",
            "pattern": None,
            "domains": ["meta"],
            "corrections": [],
            "skills_used": [],
            "memory_searches": 0,
            "memory_hits": [],
            "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
            "key_takeaway": "non-enum outcome",
            "notes": "",
        },
        # Pattern enum: the guard exempts id <= LEGACY_MAX_ID; the analyzer must agree.
        {"session_id": "sess-legacy-pattern", "id": 5, "title": "legacy non-enum pattern",
         "date": "2026-09-03", "outcome": "correction", "pattern": "ZZ", "domains": ["meta"],
         "corrections": [], "skills_used": [], "memory_searches": 0, "memory_hits": [],
         "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
         "key_takeaway": "", "notes": ""},
        {"session_id": "sess-new-pattern", "id": 900, "title": "new non-enum pattern",
         "date": "2026-09-04", "outcome": "correction", "pattern": "ZZ", "domains": ["meta"],
         "corrections": [], "skills_used": [], "memory_searches": 0, "memory_hits": [],
         "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
         "key_takeaway": "", "notes": ""},
    ],
    "legacy_entries": [],
}


def run_analyzer(repo, out_dir):
    env = dict(os.environ, HARNESS_EVAL_OUT=out_dir)
    return subprocess.run(
        [sys.executable, ANALYZER],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def main():
    failures = []
    repo = tempfile.mkdtemp(prefix="analyzefixture_")
    claude_dir = os.path.join(repo, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(claude_dir, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump(FIXTURE, f)
    with open(os.path.join(claude_dir, "self_evaluate_archive.jsonl"), "w", encoding="utf-8") as f:
        revised = dict(FIXTURE["structured_entries"][0], title="Revised shape entry",
                       date="2026-09-06")
        added = dict(FIXTURE["structured_entries"][0], session_id="sess-ledger", id=3,
                     title="Ledger entry", date="2026-09-05")
        f.write(json.dumps(revised) + "\n")
        f.write(json.dumps(added) + "\n")
    out_dir = tempfile.mkdtemp(prefix="analyzeout_")

    result = run_analyzer(repo, out_dir)

    # Case 1: WARNING for the non-enum outcome fires on stdout.
    ok = "non-enum outcome" in result.stdout and "id=2" in result.stdout
    print("%-4s %s" % ("ok" if ok else "FAIL", "non-enum outcome WARNING fires naming id=2"))
    if not ok:
        failures.append("expected WARNING naming id=2, stdout=%r" % result.stdout[:500])

    # Case 2: analyzer exits 0 (a warning is not a failure).
    ok = result.returncode == 0
    print("%-4s %s" % ("ok" if ok else "FAIL", "analyzer exits 0 despite the warning"))
    if not ok:
        failures.append("expected exit 0, got %d, stderr=%r" % (result.returncode, result.stderr[:500]))

    # Case 3: stats.json written to the HARNESS_EVAL_OUT override, not /tmp/eval_out.
    stats_path = os.path.join(out_dir, "stats.json")
    stats = {}
    ok = os.path.isfile(stats_path)
    print("%-4s %s" % ("ok" if ok else "FAIL", "stats.json written under HARNESS_EVAL_OUT"))
    if not ok:
        failures.append("expected stats.json at %s" % stats_path)

    # Case 4: the shape-bearing entry's shape survives into recent_10.
    if ok:
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        recent = stats.get("recent_10", [])
        shaped = [r for r in recent if r.get("id") == 1]
        ok2 = bool(shaped) and shaped[0].get("shape") == FIXTURE["structured_entries"][0]["shape"]
        print("%-4s %s" % ("ok" if ok2 else "FAIL", "recent_10 carries `shape` for the entry that has it"))
        if not ok2:
            failures.append("recent_10 rows=%r" % recent)

    # Case 5/6: pattern warning honours the guard's LEGACY_MAX_ID — one home for the exemption.
    ok = "id=900" in result.stdout and "non-enum pattern" in result.stdout
    print("%-4s %s" % ("ok" if ok else "FAIL", "non-enum pattern WARNING fires for id=900 (> LEGACY_MAX_ID)"))
    if not ok:
        failures.append("expected pattern WARNING naming id=900, stdout=%r" % result.stdout[:600])
    ok = "id=5 has non-enum pattern" not in result.stdout
    print("%-4s %s" % ("ok" if ok else "FAIL", "no pattern WARNING for id=5 (legacy, guard-exempt)"))
    if not ok:
        failures.append("unexpected pattern WARNING for legacy id=5")

    # Case 7: bounded ledger rows join the legacy snapshot and newest upsert wins.
    if os.path.isfile(stats_path):
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        recent = stats.get("recent_10", [])
        revised = [row for row in recent if row.get("id") == 1]
        ledger = [row for row in recent if row.get("id") == 3]
        ok = (stats.get("raw_count") == 6 and stats.get("unique_count") == 5
              and revised and revised[0].get("title") == "Revised shape entry"
              and ledger)
    else:
        ok = False
    print("%-4s %s" % ("ok" if ok else "FAIL", "analyzer streams ledger rows and keeps the newest session upsert"))
    if not ok:
        failures.append("bounded ledger rows were not merged into analyzer stats")

    # Case 8: ledger revisions are named as expected history, not archive corruption.
    ok = "Superseded-row rate:" in result.stdout and "Duplicate-rate:" not in result.stdout
    print("%-4s %s" % ("ok" if ok else "FAIL", "analyzer names superseded ledger rows without a duplicate alarm"))
    if not ok:
        failures.append("expected superseded-row terminology")

    # Cases 9-11: every effective row lands in a known or unknown outcome bucket.
    if os.path.isfile(stats_path):
        with open(stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        outcome_population = sum(stats.get("structured_outcomes", {}).values())
        ok = (stats.get("status") == "partial"
              and stats.get("structured_population") == 5
              and stats.get("structured_known") == 4
              and stats.get("structured_unknown") == 1)
    else:
        outcome_population = -1
        ok = False
    print("%-4s %s" % ("ok" if ok else "FAIL", "malformed outcomes are explicit unknown evidence"))
    if not ok:
        failures.append("malformed outcome did not produce partial/unknown coverage")
    ok = outcome_population == stats.get("structured_population") if os.path.isfile(stats_path) else False
    print("%-4s %s" % ("ok" if ok else "FAIL", "structured outcome buckets sum to the effective population"))
    if not ok:
        failures.append("structured outcome buckets do not conserve population")
    ok = (stats.get("total_sessions") == 5
          and stats.get("total_clean") == 2
          and stats.get("total_correction") == 2
          and stats.get("total_unknown_outcomes") == 1
          and stats.get("clean_pct") == 40.0
          and stats.get("correction_pct") == 40.0)
    print("%-4s %s" % ("ok" if ok else "FAIL", "combined rates use the full effective population"))
    if not ok:
        failures.append("combined rates excluded unknown rows from the population denominator")
    meta = next((row for row in stats.get("domain_perf", []) if row.get("domain") == "meta"), {})
    ok = (meta.get("total") == 5 and meta.get("known") == 4 and meta.get("unknown") == 1
          and sum(meta.get(key, 0) for key in ("clean", "correction", "failure", "unknown")) == 5)
    print("%-4s %s" % ("ok" if ok else "FAIL", "domain buckets expose and reconcile full population"))
    if not ok:
        failures.append("domain aggregate does not expose a reconciled population")
    ok = (stats.get("recent_window", {}).get("population") == 5
          and stats.get("recent_window", {}).get("known") == 4
          and stats.get("recent_window", {}).get("unknown") == 1
          and stats.get("prior_window", {}).get("population") == 0
          and stats.get("prior_window", {}).get("clean_pct") is None)
    print("%-4s %s" % ("ok" if ok else "FAIL", "short and absent trend denominators remain explicit"))
    if not ok:
        failures.append("trend windows use a fixed /10 or zero for an absent prior population")

    # Case 12: a valid empty archive is a no-data state, never a zero-rate result.
    empty_repo = tempfile.mkdtemp(prefix="analyzeempty_")
    empty_claude = os.path.join(empty_repo, ".claude")
    empty_out = tempfile.mkdtemp(prefix="analyzeemptyout_")
    os.makedirs(empty_claude, exist_ok=True)
    with open(os.path.join(empty_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump({"Self_Evaluate_Themes": {"patterns": {}},
                   "structured_entries": [], "legacy_entries": []}, f)
    empty_result = run_analyzer(empty_repo, empty_out)
    empty_stats_path = os.path.join(empty_out, "stats.json")
    empty_stats = {}
    if os.path.isfile(empty_stats_path):
        with open(empty_stats_path, "r", encoding="utf-8") as f:
            empty_stats = json.load(f)
    ok = (empty_result.returncode == 0 and empty_stats.get("status") == "no-data"
          and empty_stats.get("total_sessions") == 0
          and empty_stats.get("clean_pct") is None
          and empty_stats.get("date_first") is None)
    print("%-4s %s" % ("ok" if ok else "FAIL", "valid no-data archive reports unknown rates without crashing"))
    if not ok:
        failures.append("empty archive was not represented as no-data: stdout=%r stderr=%r stats=%r"
                        % (empty_result.stdout[-300:], empty_result.stderr[-300:], empty_stats))

    # Case 13: a malformed population is UNKNOWN and produces no plausible stats file.
    malformed_repo = tempfile.mkdtemp(prefix="analyzemalformed_")
    malformed_claude = os.path.join(malformed_repo, ".claude")
    malformed_out = tempfile.mkdtemp(prefix="analyzemalformedout_")
    os.makedirs(malformed_claude, exist_ok=True)
    with open(os.path.join(malformed_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump({"Self_Evaluate_Themes": {}, "structured_entries": {}, "legacy_entries": []}, f)
    malformed_result = run_analyzer(malformed_repo, malformed_out)
    ok = (malformed_result.returncode == 2 and "UNKNOWN:" in malformed_result.stdout
          and "Traceback" not in malformed_result.stderr
          and not os.path.exists(os.path.join(malformed_out, "stats.json")))
    print("%-4s %s" % ("ok" if ok else "FAIL", "malformed archive fails as unknown without emitting stats"))
    if not ok:
        failures.append("malformed archive did not fail closed: stdout=%r stderr=%r"
                        % (malformed_result.stdout[-300:], malformed_result.stderr[-300:]))

    # Case 14: malformed metadata fails closed instead of becoming empty or crashing Counter.
    for field, value in (("domains", "meta"),
                         ("skills_used", [{"name": "bad"}]),
                         ("memory_hits", {"name": "bad"})):
        metadata_repo = tempfile.mkdtemp(prefix="analyzemetadata_%s_" % field)
        metadata_claude = os.path.join(metadata_repo, ".claude")
        metadata_out = tempfile.mkdtemp(prefix="analyzemetadataout_%s_" % field)
        os.makedirs(metadata_claude, exist_ok=True)
        malformed_doc = json.loads(json.dumps(FIXTURE))
        malformed_doc["structured_entries"][0][field] = value
        with open(os.path.join(metadata_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
            json.dump(malformed_doc, f)
        metadata_result = run_analyzer(metadata_repo, metadata_out)
        metadata_ok = (metadata_result.returncode == 2 and "UNKNOWN:" in metadata_result.stdout
                       and "metadata" in metadata_result.stdout
                       and "Traceback" not in metadata_result.stderr
                       and not os.path.exists(os.path.join(metadata_out, "stats.json")))
        print("%-4s %s metadata is rejected as malformed (%s)" %
              ("ok" if metadata_ok else "FAIL", "", field))
        if not metadata_ok:
            failures.append("malformed %s metadata was not rejected: stdout=%r stderr=%r"
                            % (field, metadata_result.stdout[-300:], metadata_result.stderr[-300:]))

    # Case 15: console review stays bounded while stats retain the full population.
    large_repo = tempfile.mkdtemp(prefix="analyzelarge_")
    large_claude = os.path.join(large_repo, ".claude")
    large_out = tempfile.mkdtemp(prefix="analyzelargeout_")
    os.makedirs(large_claude, exist_ok=True)
    large_entries = []
    for index in range(200):
        row = dict(FIXTURE["structured_entries"][2])
        row.update(session_id="large-%03d" % index, id=1000 + index,
                   title="Large correction %03d" % index, date="2026-09-07",
                   pattern="A")
        large_entries.append(row)
    with open(os.path.join(large_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump({"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"],
                   "structured_entries": large_entries, "legacy_entries": []}, f)
    large_result = run_analyzer(large_repo, large_out)
    with open(os.path.join(large_out, "stats.json"), "r", encoding="utf-8") as f:
        large_stats = json.load(f)
    ok = (large_result.returncode == 0 and len(large_stats.get("all_corrections", [])) == 200
          and len(large_result.stdout.splitlines()) < 140
          and "older correction row(s) omitted" in large_result.stdout)
    print("%-4s %s" % ("ok" if ok else "FAIL", "stdout is bounded while stats retain every correction"))
    if not ok:
        failures.append("large analyzer output was not bounded: lines=%d stats=%d"
                        % (len(large_result.stdout.splitlines()),
                           len(large_stats.get("all_corrections", []))))

    noisy_repo = tempfile.mkdtemp(prefix="analyzenoisy_")
    noisy_claude = os.path.join(noisy_repo, ".claude")
    noisy_out = tempfile.mkdtemp(prefix="analyzenoisyout_")
    os.makedirs(noisy_claude, exist_ok=True)
    noisy_entries = []
    for index in range(200):
        row = dict(FIXTURE["structured_entries"][0])
        long_value = "x" * 1000
        row.update(session_id="noisy-%03d" % index, id=2000 + index,
                   title="Noisy %03d" % index,
                   date="2026-09-08-%03d-%s" % (index, long_value),
                   outcome="invalid-%03d" % index,
                   pattern="invalid-%03d-%s" % (index, long_value),
                   domains=["domain-" + long_value],
                   skills_used=["skill-" + long_value],
                   memory_hits=["memory-" + long_value])
        noisy_entries.append(row)
    with open(os.path.join(noisy_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump({"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"],
                   "structured_entries": noisy_entries, "legacy_entries": []}, f)
    noisy_result = run_analyzer(noisy_repo, noisy_out)
    with open(os.path.join(noisy_out, "stats.json"), "r", encoding="utf-8") as f:
        noisy_stats = json.load(f)
    ok = (noisy_result.returncode == 0
          and noisy_stats.get("structured_population") == 200
          and noisy_stats.get("structured_unknown") == 200
          and len(noisy_result.stdout.splitlines()) < 180
          and len(noisy_result.stdout.encode("utf-8")) < 20000
          and "warning row(s) omitted" in noisy_result.stdout)
    print("%-4s %s" % ("ok" if ok else "FAIL", "malformed warning and pattern logs stay bounded"))
    if not ok:
        failures.append("malformed analyzer output was not bounded: lines=%d bytes=%d"
                        % (len(noisy_result.stdout.splitlines()),
                           len(noisy_result.stdout.encode("utf-8"))))

    # Cases 21-22: memory hits written as a path, a `.md` name or with a trailing reason count
    # under one memory name; a legacy entity name with spaces stays whole.
    hits_repo = tempfile.mkdtemp(prefix="analyzehits_")
    hits_claude = os.path.join(hits_repo, ".claude")
    hits_out = tempfile.mkdtemp(prefix="analyzehitsout_")
    os.makedirs(hits_claude, exist_ok=True)
    shapes = [
        ["gotcha_shared_checkout", "Legacy Entity Name v4.2"],
        ["archive/gotcha_shared_checkout.md — drove pathspec-only commits", "Legacy Entity Name v4.2"],
        ["gotcha_shared_checkout (caught a peer write)", "gotcha_shared_checkout.md",
         "Legacy Entity Name v4.2"],
        ["rules/harness_tooling.md 'a guard matches the ACTION' - framed the fix",
         "MEMORY.md 'Trusting delegate output' — drove the battery"],
    ]
    hit_entries = []
    for index, hits in enumerate(shapes):
        row = dict(FIXTURE["structured_entries"][0])
        row.update(session_id="hits-%d" % index, id=3000 + index, title="Hits %d" % index,
                   date="2026-09-1%d" % index, memory_hits=hits)
        hit_entries.append(row)
    with open(os.path.join(hits_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump({"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"],
                   "structured_entries": hit_entries, "legacy_entries": []}, f)
    hits_result = run_analyzer(hits_repo, hits_out)
    hits_stats = {}
    if os.path.isfile(os.path.join(hits_out, "stats.json")):
        with open(os.path.join(hits_out, "stats.json"), "r", encoding="utf-8") as f:
            hits_stats = json.load(f)
    top = {row.get("entity"): row.get("count") for row in hits_stats.get("top_memory_hits", [])}
    ok = hits_result.returncode == 0 and top.get("gotcha_shared_checkout") == 3
    print("%-4s %s" % ("ok" if ok else "FAIL",
                       "hit shapes of one memory count once per session under its name"))
    if not ok:
        failures.append("memory hits were not normalized: top=%r stderr=%r" % (top, hits_result.stderr[-300:]))
    ok = top.get("Legacy Entity Name v4.2") == 3 and not any(" — " in str(k) for k in top)
    print("%-4s %s" % ("ok" if ok else "FAIL", "a legacy entity name with spaces stays whole"))
    if not ok:
        failures.append("legacy entity names were split or reasons kept: top=%r" % top)

    counts = hits_stats.get("memory_hit_counts") or {}
    ok = counts.get("MEMORY") == 1 and counts.get("harness_tooling") == 1 and counts.get("gotcha_shared_checkout") == 3
    print("%-4s %s" % ("ok" if ok else "FAIL", "memory_hit_counts keeps every stem, including single citations"))
    if not ok:
        failures.append("memory_hit_counts missing or incomplete: %r" % counts)

    total = 23
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
