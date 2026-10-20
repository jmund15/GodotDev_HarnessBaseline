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
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "analyze_eval_archive")
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


def _entry(n, month, day, outcome, pattern=None, domains=("meta",), skills=(), hits=(), shape=None):
    row = {"session_id": "golden-%03d" % n, "id": 500 + n, "title": "Golden entry %03d" % n,
           "date": "%s-%02d" % (month, day), "outcome": outcome, "pattern": pattern,
           "domains": list(domains), "corrections": ["c"] if outcome != "clean" else [],
           "skills_used": list(skills), "memory_searches": 0, "memory_hits": list(hits),
           "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
           "key_takeaway": "takeaway %d" % n, "notes": ""}
    if shape:
        row["shape"] = shape
    return row


def golden_archive():
    """A fixed archive over three months, legacy prose rows included, for the characterization."""
    rows = []
    plan = [("2026-07", 3, "clean", None, ("meta",), ("testing",)),
            ("2026-07", 9, "correction", "A", ("meta", "gameplay"), ("testing", "debugging")),
            ("2026-08", 2, "clean", None, ("gameplay",), ("writing",)),
            ("2026-08", 12, "failure", "B", ("meta",), ("testing",)),
            ("2026-08", 20, "clean", None, ("meta", "gameplay"), ("testing", "writing")),
            ("2026-09", 1, "clean", None, ("meta",), ("testing",)),
            ("2026-09", 4, "correction", "A", ("gameplay",), ("writing",)),
            ("2026-09", 8, "clean", None, ("meta",), ("testing", "debugging")),
            ("2026-09", 15, "clean", None, ("meta", "gameplay"), ("testing",))]
    for n, (month, day, outcome, pattern, domains, skills) in enumerate(plan, 1):
        rows.append(_entry(n, month, day, outcome, pattern, domains, skills,
                           hits=["gotcha_%d" % (n % 3)],
                           shape={"duration_min": 100 * n} if n % 2 else None))
    doc = {"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"], "structured_entries": rows,
           "legacy_entries": ["#1 clean session", "#2 USER CORRECTION on scope", "#3 mystery"]}
    return doc


def plant_golden(repo):
    claude = os.path.join(repo, ".claude")
    os.makedirs(claude, exist_ok=True)
    with open(os.path.join(claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as f:
        json.dump(golden_archive(), f)
    with open(os.path.join(claude, "self_evaluate_archive.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(10, "2026-09", 20, "failure", "C", ("meta",), ("testing",))) + "\n")


def run_golden():
    repo = tempfile.mkdtemp(prefix="analyzegolden_")
    plant_golden(repo)
    out_dir = tempfile.mkdtemp(prefix="analyzegoldenout_")
    result = run_analyzer(repo, out_dir)
    console = result.stdout.replace(out_dir, "<OUT>").replace("\\", "/")
    with open(os.path.join(out_dir, "stats.json"), "rb") as handle:
        stats = handle.read()
    return repo, result, console, stats


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


    # Cases 24-25: characterization captured before the compute() extraction: the CLI's console
    # output and stats.json over one planted archive stay byte-identical.
    _, golden_result, console, stats_bytes = run_golden()
    with open(os.path.join(GOLDEN, "console.txt"), "rb") as handle:
        want_console = handle.read().decode("utf-8")
    with open(os.path.join(GOLDEN, "stats.json"), "rb") as handle:
        want_stats = handle.read()
    ok = golden_result.returncode == 0 and console == want_console
    print("%-4s %s" % ("ok" if ok else "FAIL", "CLI console output is byte-identical to the captured golden"))
    if not ok:
        failures.append("console drifted from the golden")
    ok = stats_bytes == want_stats
    print("%-4s %s" % ("ok" if ok else "FAIL", "stats.json is byte-identical to the captured golden"))
    if not ok:
        failures.append("stats.json drifted from the golden")

    # Case 26: compute(root) from a foreign cwd returns the dictionary the CLI writes (RED today: the
    # analyzer reads a cwd-relative archive path and has no importable compute()).
    compute_repo = tempfile.mkdtemp(prefix="analyzecompute_")
    plant_golden(compute_repo)
    probe = subprocess.run(
        [sys.executable, "-c",
         "import json, sys; sys.path.insert(0, %r); import analyze_eval_archive as a; "
         "print(json.dumps(a.compute(%r), indent=2))" % (os.path.join(ROOT, "tools"), compute_repo)],
        cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=60)
    want_text = want_stats.decode("utf-8").replace("\r\n", "\n").strip()
    ok = probe.returncode == 0 and probe.stdout.strip() == want_text
    print("%-4s %s" % ("ok" if ok else "FAIL", "compute(root) from a foreign cwd equals the CLI's stats.json"))
    if not ok:
        failures.append("compute(root) foreign cwd: rc=%d stderr=%r" % (probe.returncode, probe.stderr[-300:]))

    # Case 27: month filters structured entries by their date prefix and drops undated legacy prose.
    month_probe = subprocess.run(
        [sys.executable, "-c",
         "import json, sys; sys.path.insert(0, %r); import analyze_eval_archive as a; "
         "s = a.compute(%r, month='2026-08'); print(json.dumps([s['total_sessions'], s['total_clean'], "
         "s['total_failure'], s['legacy_count'], s['date_first'], s['date_last']]))"
         % (os.path.join(ROOT, "tools"), compute_repo)],
        cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=60)
    ok = month_probe.returncode == 0 and month_probe.stdout.strip() == '[3, 2, 1, 0, "2026-08-02", "2026-08-20"]'
    print("%-4s %s" % ("ok" if ok else "FAIL", "compute(month=) keeps only that month's structured entries"))
    if not ok:
        failures.append("compute(month=): %r %r" % (month_probe.stdout[-200:], month_probe.stderr[-200:]))

    # Case 28: a bad archive raises through compute() and the CLI still prints UNKNOWN and exits 2.
    bad_code = "\n".join([
        "import sys",
        "sys.path.insert(0, %r)" % os.path.join(ROOT, "tools"),
        "import analyze_eval_archive as a",
        "try:",
        "    a.compute(%r)" % os.path.join(tempfile.gettempdir(), "no-such-root-xyz"),
        "except a.AnalysisError as e:",
        "    print('raised', e)"])
    bad_probe = subprocess.run([sys.executable, "-c", bad_code], cwd=tempfile.gettempdir(),
                               capture_output=True, text=True, timeout=60)
    ok = bad_probe.returncode == 0 and "raised" in bad_probe.stdout
    print("%-4s %s" % ("ok" if ok else "FAIL", "compute() raises AnalysisError on an unreadable archive"))
    if not ok:
        failures.append("compute() bad archive: %r %r" % (bad_probe.stdout[-200:], bad_probe.stderr[-200:]))

    # Case 29: tied memory-hit counts come out in one order whatever the hash seed.
    tie_repo = tempfile.mkdtemp(prefix="analyzetie_")
    tie_claude = os.path.join(tie_repo, ".claude")
    os.makedirs(tie_claude, exist_ok=True)
    tie_rows = [_entry(n, "2026-09", n, "clean", hits=["mem_%02d" % k for k in range(12)]) for n in range(1, 4)]
    with open(os.path.join(tie_claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as handle:
        json.dump({"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"], "structured_entries": tie_rows,
                   "legacy_entries": []}, handle)
    with open(os.path.join(tie_claude, "self_evaluate_archive.jsonl"), "w", encoding="utf-8") as handle:
        handle.write("")
    orders = []
    for seed in ("1", "2", "3"):
        tie_out = tempfile.mkdtemp(prefix="analyzetieout_")
        probe = subprocess.run([sys.executable, ANALYZER], cwd=tie_repo, capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, HARNESS_EVAL_OUT=tie_out, PYTHONHASHSEED=seed))
        with open(os.path.join(tie_out, "stats.json"), encoding="utf-8") as handle:
            orders.append([row["entity"] for row in json.load(handle)["top_memory_hits"]])
    ok = orders[0] == orders[1] == orders[2] and len(orders[0]) == 12
    print("%-4s %s" % ("ok" if ok else "FAIL", "tied memory-hit counts keep one order across hash seeds"))
    if not ok:
        failures.append("memory-hit tie order differs by seed: %r" % (orders,))

    # Cases 30-35: the month snapshot's session block is compute(month=)'s, the one definition of a session statistic.
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import analyze_eval_archive as aea

    def plant_rows(rows):
        repo_dir = tempfile.mkdtemp(prefix="analyzemonth_")
        claude_dir = os.path.join(repo_dir, ".claude")
        os.makedirs(claude_dir)
        with open(os.path.join(claude_dir, "self_evaluate_archive.json"), "w", encoding="utf-8") as handle:
            json.dump({"Self_Evaluate_Themes": FIXTURE["Self_Evaluate_Themes"], "structured_entries": rows,
                       "legacy_entries": []}, handle)
        return repo_dir

    def case(ok, label, detail=""):
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    edge_rows = [_entry(i, "2026-09", 1 + i, "clean" if i % 2 else "correction", "A" if i % 2 == 0 else None,
                        shape={"duration_min": minutes})
                 for i, minutes in enumerate((299, 300, 301, 700, 701), 1)]
    edges = aea.compute(plant_rows(edge_rows), month="2026-09")["sessions"]["duration"]
    case(edges["lt300"]["n"] == 1 and edges["300_700"]["n"] == 3 and edges["gt700"]["n"] == 1 and edges["unknown"] == 0,
         "sessions: duration buckets split at 300 and 700 minutes (299 low, 300 301 700 middle, 701 high)", repr(edges))
    case(edges["lt300"]["clean"] + edges["300_700"]["clean"] + edges["gt700"]["clean"] == 3,
         "sessions: each duration bucket counts its clean entries", repr(edges))

    # Duration is store-derived on a new row (`facts.duration_min`) and was model-typed on an old
    # one (`shape.duration_min`); the bucket reads facts first, and a row with neither is unknown,
    # never a zero-minute session.
    facts_rows = [_entry(1, "2026-09", 1, "clean"), _entry(2, "2026-09", 2, "clean"),
                  _entry(3, "2026-09", 3, "clean"), _entry(4, "2026-09", 4, "clean")]
    facts_rows[0]["facts"] = {"duration_min": 299}
    facts_rows[1]["facts"] = {"driver": {"model": "m-three", "effort": "high"},
                              "owner_prompts": 3, "duration_min": 700}
    facts_rows[2]["facts"] = {"duration_min": 701}
    facts_rows[2]["shape"] = {"duration_min": 5}
    from_facts = aea.compute(plant_rows(facts_rows), month="2026-09")["sessions"]["duration"]
    case(from_facts["lt300"]["n"] == 1 and from_facts["300_700"]["n"] == 1
         and from_facts["gt700"]["n"] == 1 and from_facts["unknown"] == 1,
         "sessions: duration reads facts.duration_min first, shape beside it only when facts is absent",
         repr(from_facts))
    legacy_rows = [_entry(5, "2026-09", 5, "clean", shape={"duration_min": 400})]
    legacy = aea.compute(plant_rows(legacy_rows), month="2026-09")["sessions"]["duration"]
    case(legacy["300_700"]["n"] == 1,
         "sessions: a row with no facts still buckets from its shape.duration_min", repr(legacy))

    dup_rows = [_entry(1, "2026-09", 1, "clean", domains=("meta", "meta", "gameplay"), skills=("testing", "testing")),
                _entry(2, "2026-09", 2, "correction", "A", domains=("meta",), skills=("testing",)),
                _entry(3, "2026-09", 3, "clean", domains=("meta",), skills=("testing",))]
    dup_repo = plant_rows(dup_rows)
    dup_all = aea.compute(dup_repo)
    dup_month = aea.compute(dup_repo, month="2026-09")["sessions"]
    meta_perf = next(d for d in dup_all["domain_perf"] if d["domain"] == "meta")
    testing_perf = next(s for s in dup_all["skill_perf"] if s["skill"] == "testing")
    cell_keys = {"n": 3, "clean": 2, "corrections": 0, "prompts": 0, "facts_rows": 0}
    case(dup_month["domains"]["meta"] == cell_keys and dup_month["skills"]["testing"] == cell_keys
         and meta_perf["total"] == 3 and testing_perf["total"] == 3,
         "sessions: a domain or skill listed twice on one entry counts once, in the month block and the all-time tables",
         repr((dup_month["domains"], meta_perf["total"], testing_perf["total"])))

    golden_repo = tempfile.mkdtemp(prefix="analyzeagree_")
    plant_golden(golden_repo)
    every = aea.compute(golden_repo)
    months = aea.dated_months(every)
    blocks = {month: aea.compute(golden_repo, month=month)["sessions"] for month in months}
    summed_n = sum(block["n"] for block in blocks.values())
    summed_domains = {}
    for block in blocks.values():
        for name, cell in block["domains"].items():
            summed_domains[name] = summed_domains.get(name, 0) + cell["n"]
    case(months == ["2026-07", "2026-08", "2026-09"] and summed_n == every["structured_population"]
         and all(summed_domains[d["domain"]] == d["total"] for d in every["domain_perf"])
         and sum(b["clean"] for b in blocks.values()) == every["structured_outcomes"]["clean"]
         and sum(b["patterns"].get("C", 0) for b in blocks.values()) == every["structured_patterns_normalized"]["C"],
         "sessions: the month blocks sum to the all-time statistics (entries, outcomes, domains, normalized patterns)",
         repr((months, summed_n, summed_domains)))

    messy = [_entry(1, "2026-09", 1, "clean"), _entry(2, "2026-09", 2, "clean"), _entry(3, "2026-09", 3, "clean"),
             _entry(4, "2026-09", 4, "clean"), _entry(5, "2026-09", 5, "clean")]
    messy[0]["date"] = None
    messy[1]["date"] = "2026-13-45"
    messy[2]["date"] = "2026-09-28T10:00"
    messy[3]["date"] = "sometime"
    messy_repo = plant_rows(messy)
    messy_all = aea.compute(messy_repo)
    case(aea.dated_months(messy_all) == ["2026-09"] and aea.calendar_day("2026-09-28T10:00") == "2026-09-28"
         and aea.calendar_day("2026-13-45") is None and aea.calendar_day(None) is None
         and aea.compute(messy_repo, month="2026-09")["unique_count"] == 2
         and aea.compute(messy_repo, month="2026-13")["unique_count"] == 0,
         "sessions: only a valid calendar day places an entry in a month; None, 2026-13-45 and prose place none",
         repr((aea.dated_months(messy_all), aea.compute(messy_repo, month="2026-09")["unique_count"])))

    domain_rows = [_entry(1, "2026-09", 1, "correction", "A", domains=("meta", "gameplay")),
                   _entry(2, "2026-09", 2, "failure", "B", domains=()),
                   _entry(3, "2026-09", 3, "correction", "A", domains=("meta",))]
    corrections = aea.compute(plant_rows(domain_rows))["all_corrections"]
    case([row.get("domains") for row in corrections] == [["meta", "gameplay"], [], ["meta"]],
         "corrections: every all_corrections row carries the entry's domains", repr(corrections))

    cause_rows = [_entry(1, "2026-09", 1, "clean", "A"), _entry(2, "2026-09", 2, "correction", "A"),
                  _entry(3, "2026-09", 3, "failure", "E"), _entry(4, "2026-09", 4, "correction", None),
                  _entry(5, "2026-09", 5, "correction", "C"), _entry(6, "2026-09", 6, "clean"),
                  _entry(7, "2026-09", 7, "correction", "B")]
    causes = aea.compute(plant_rows(cause_rows), month="2026-09")["sessions"]["causes"]
    case(causes == {"A": 1, "B": 1, "C": 1, "D": 0, "E": 1, "none": 1} and aea.empty_sessions()["causes"] == dict.fromkeys(
        aea.BASE_PATTERNS, 0),
         "sessions: causes tallies the pattern of each correction or failure session alone, so a clean session that "
         "carries a pattern is not a cause and the split reconciles to the non-clean sessions", repr(causes))

    # S3: the month's sessions block carries the corrections-per-owner-prompt sums, per month, domain,
    # skill, duration bucket and driver. A row without `facts.owner_prompts` is typed absent, never a
    # zero-prompt session, and a row without `facts.driver` belongs to no driver row at all.
    rate_rows = [_entry(1, "2026-09", 1, "clean", domains=("meta",), skills=("testing",)),
                 _entry(2, "2026-09", 2, "correction", "A", domains=("meta", "gameplay"), skills=("testing",)),
                 _entry(3, "2026-09", 3, "failure", "B", domains=("gameplay",), skills=("writing",)),
                 _entry(4, "2026-09", 4, "clean", domains=("meta",))]
    rate_rows[0]["facts"] = {"driver": {"model": "m-one", "effort": "high"}, "owner_prompts": 10, "duration_min": 100}
    rate_rows[0]["corrections"] = ["a", "b"]
    rate_rows[1]["facts"] = {"driver": {"model": "m-one", "effort": "high"}, "owner_prompts": 30, "duration_min": 200}
    rate_rows[1]["corrections"] = ["a"]
    rate_rows[2]["facts"] = {"driver": {"model": "m-two", "effort": "low"}, "owner_prompts": 20, "duration_min": 800}
    rate_rows[2]["corrections"] = ["a", "b", "c"]
    rate_rows[3]["corrections"] = ["a"]
    rate_block = aea.compute(plant_rows(rate_rows), month="2026-09")["sessions"]
    case((rate_block.get("corrections"), rate_block.get("prompts"), rate_block.get("facts_rows")) == (6, 60, 3),
         "sessions: the month sums corrections and owner prompts over the rows that carry both", repr(rate_block))
    case(rate_block.get("domains", {}).get("meta") == {"n": 3, "clean": 2, "corrections": 3, "prompts": 40,
                                                       "facts_rows": 2}
         and rate_block.get("domains", {}).get("gameplay", {}).get("corrections") == 4
         and rate_block.get("domains", {}).get("gameplay", {}).get("prompts") == 50,
         "sessions: each domain cell sums the corrections and prompts of the sessions that name it",
         repr(rate_block.get("domains")))
    case(rate_block.get("duration", {}).get("lt300") == {"n": 2, "clean": 1, "corrections": 3, "prompts": 40, "facts_rows": 2}
         and rate_block.get("duration", {}).get("gt700") == {"n": 1, "clean": 0, "corrections": 3, "prompts": 20, "facts_rows": 1}
         and rate_block.get("duration", {}).get("unknown") == 1,
         "sessions: each duration bucket sums the corrections and prompts of the sessions in it",
         repr(rate_block.get("duration")))
    case(rate_block.get("drivers") == {
             "m-one / high": {"n": 2, "clean": 1, "correction": 1, "failure": 0, "unknown": 0,
                              "corrections": 3, "prompts": 40, "facts_rows": 2},
             "m-two / low": {"n": 1, "clean": 0, "correction": 0, "failure": 1, "unknown": 0,
                             "corrections": 3, "prompts": 20, "facts_rows": 1}},
         "sessions: the driver table keys each session by its store-derived model and effort, and a row with no "
         "facts belongs to no driver", repr(rate_block.get("drivers")))

    # S5: `cause_counts` sums the model-recorded per-correction cause letters, and `rule_misses` counts
    # sessions by kind beside a live table of rule, kind, sessions and example ids.
    cause_rows2 = [_entry(1, "2026-09", 1, "correction", "A"), _entry(2, "2026-09", 2, "failure", "B"),
                   _entry(3, "2026-09", 3, "clean")]
    cause_rows2[0]["corrections"] = ["a", "b"]
    cause_rows2[0]["cause_counts"] = {"A": 2}
    cause_rows2[1]["corrections"] = ["a"]
    cause_rows2[1]["cause_counts"] = {"B": 1}
    cause_block = aea.compute(plant_rows(cause_rows2), month="2026-09")["sessions"]
    case(cause_block.get("cause_counts") == {"A": 2, "B": 1} and cause_block.get("cause_counts_rows") == 2,
         "sessions: cause_counts sums the letters the sessions recorded, with the recording rows counted beside it",
         repr((cause_block.get("cause_counts"), cause_block.get("cause_counts_rows"))))
    bare_block = aea.compute(plant_rows([_entry(1, "2026-09", 1, "correction", "A")]), month="2026-09")["sessions"]
    case(bare_block.get("cause_counts") == {} and bare_block.get("cause_counts_rows") == 0,
         "sessions: a month whose rows recorded no cause_counts types it absent, never a zero count",
         repr((bare_block.get("cause_counts"), bare_block.get("cause_counts_rows"))))

    rule_rows = [_entry(1, "2026-09", 1, "correction", "A"), _entry(2, "2026-09", 2, "failure", "B")]
    rule_rows[0]["rule_misses"] = [{"rule": ".claude/CLAUDE.md", "kind": "not-read", "evidence": "U1"},
                                   {"rule": ".claude/CLAUDE.md", "kind": "not-followed", "evidence": "U2"}]
    rule_rows[1]["rule_misses"] = [{"rule": ".claude/CLAUDE.md", "kind": "not-read", "evidence": "an owner quote"}]
    rule_block = aea.compute(plant_rows(rule_rows), month="2026-09")
    case(rule_block["sessions"].get("rule_misses") == {"not-read": 2, "not-followed": 1}
         and rule_block["sessions"].get("rule_miss_rows") == 2,
         "sessions: rule_misses counts the sessions that missed each kind of rule, with the recording rows counted beside it",
         repr((rule_block["sessions"].get("rule_misses"), rule_block["sessions"].get("rule_miss_rows"))))
    case(rule_block.get("rule_misses") == [
             {"rule": ".claude/CLAUDE.md", "kind": "not-read", "sessions": 2,
              "ids": ["golden-001", "golden-002"]},
             {"rule": ".claude/CLAUDE.md", "kind": "not-followed", "sessions": 1, "ids": ["golden-001"]}],
         "sessions: the live rule_misses table lists rule, kind, the sessions naming it and example ids",
         repr(rule_block.get("rule_misses")))
    case(aea.compute(plant_rows([_entry(1, "2026-09", 1, "clean")]), month="2026-09").get("rule_misses") == [],
         "sessions: a month whose rows recorded no rule_misses has an empty table, not a row of zeroes")

    total = 45
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
