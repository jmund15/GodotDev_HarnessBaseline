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

    total = 6
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for f in failures:
        print("  FAIL " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
