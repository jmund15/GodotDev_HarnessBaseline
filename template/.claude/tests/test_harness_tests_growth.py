"""Proof that the runner hands its gate-time growth to the people who can act on it, and clears what it leaks.

- A pass judged by its static key that no longer matches records the miss as `static`, so the close's reuse
  line can count it; before, such a proof reran with no reason recorded.
- The close's growth lines come from `tools/gate_growth.py` over the runner's own run history and store,
  asking for lasting growth: a proof slow in its last two runs is named.
- A commit touching a proof with an open growth entry gets that entry as an advisory.
- An orphaned `.tmp_*.json` beside the shared stores, older than the runner's stale-temp age, is removed;
  a younger one and any other file stay.

    python3 .claude/tests/test_harness_tests_growth.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import harness_tests  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


root = tempfile.mkdtemp(prefix="harness_growth_proof_")
try:
    proof = os.path.join(root, ".claude", "tests", "test_x.py")
    os.makedirs(os.path.dirname(proof))
    open(proof, "w").close()

    stub = types.SimpleNamespace(root=root, misses={}, miss_files={}, _reads={}, _checkout_name=lambda: "here",
                                 variants=lambda p: [{"key": "old", "observed": True}])
    matched = harness_tests._PassCache._matched(stub, proof, "new")
    check("a static-key row that no longer matches records the miss as static",
          matched is None and stub.misses.get(".claude/tests/test_x.py") == ["static"], stub.misses)
    stub.misses.clear()
    stub.variants = lambda p: [{"key": "same", "observed": True}]
    check("a static-key row that matches records no miss",
          harness_tests._PassCache._matched(stub, proof, "same") is not None and not stub.misses, stub.misses)

    store = os.path.join(root, "gate_growth.json")
    harness_tests.GROWTH_PATH = store
    check("the growth store follows its override", harness_tests.growth_path() == store)

    def run(ts, a):
        times = {"test_x.py": a, "b.py": 20.0, "c.py": 30.0}
        return {"ts": ts, "head": "f" * 40, "selected": 3, "reused": 0,
                "proofs": [{"proof": n, "status": "pass", "run_s": s, "reused": False, "miss": ["runner"]}
                           for n, s in times.items()]}
    history = [run(1, 12.0), run(2, 12.0), run(3, 12.0), run(4, 40.0)]
    row = run(5, 40.0)
    lines = harness_tests._growth_lines(row, history + [row], root)
    check("the close prints gate_growth's growth line for a grown proof",
          len(lines) == 2 and lines[0].startswith("harness_tests: close growth: test_x.py 40.0s"), lines)
    check("the close prints the reuse line", lines[1].startswith("harness_tests: close reuse: 0 of 3 reused"), lines)
    advice = harness_tests._growth_advisories([".claude/tests/test_x.py"])
    check("a commit touching a grown proof gets its growth advisory",
          len(advice) == 1 and advice[0].startswith("[harness commit] test_x.py grew"), advice)

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    harness_tests.STAMP_PATH = os.path.join(root, "stamp.json")
    result = harness_tests.run_commit(root, [".claude/tests/test_x.py"])
    check("run_commit carries the growth advisory",
          any(a.startswith("[harness commit] test_x.py grew") for a in result["advisories"]), result)

    os.remove(store)
    check("a grown proof under the inner budget is not named",
          "test_x.py" not in harness_tests._growth_lines(
              run(5, 9.0), [run(1, 3.0), run(2, 3.0), run(3, 3.0), run(4, 9.0)], root)[0])
    os.remove(store)
    check("the close does not name a proof after one slow run",
          "test_x.py" not in harness_tests._growth_lines(run(4, 40.0), [run(1, 12.0), run(2, 12.0), run(3, 12.0)], root)[0])

    shared = os.path.join(root, "harness")
    os.makedirs(shared)
    old, young, other = (os.path.join(shared, n) for n in (".tmp_old.json", ".tmp_young.json", "proof_passes.json"))
    for path in (old, young, other):
        with open(path, "w") as fh:
            fh.write("{}")
    stale = time.time() - harness_tests.STALE_TEMP_AGE_S - 60
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    removed = harness_tests.sweep_store_temp(shared)
    check("an orphaned store temp file past the stale age goes",
          removed == 1 and not os.path.exists(old), (removed, os.listdir(shared)))
    check("a young temp file and a store stay", os.path.exists(young) and os.path.exists(other), os.listdir(shared))
finally:
    shutil.rmtree(root, ignore_errors=True)

if FAILURES:
    print("\n%d failure(s):" % len(FAILURES))
    for failure in FAILURES:
        print("  " + failure)
    sys.exit(1)
print("\nall checks passed")
