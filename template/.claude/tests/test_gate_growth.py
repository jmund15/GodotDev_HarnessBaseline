"""Proof that a close names what its time grew from, against the closes recorded before it, with no fixed threshold.

- A proof whose load-normalized time is above every earlier normalized time of it (two or more earlier passes)
  is grown; a run where every proof is slower by the same factor grows nothing.
- An open growth entry stays while the proof stays above the range it had before, counts the closes that saw
  it, and leaves when the proof is back inside that range or its file is gone.
- The reuse line groups the proofs a close reran by the one part whose change made each rerun.
- A commit that touches a grown proof gets one advisory naming its times.

    python3 .claude/tests/test_gate_growth.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import gate_growth  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def run(ts, times, reused=(), misses=None, head="h"):
    misses = misses or {}
    proofs = [{"proof": name, "status": "pass", "run_s": float(s), "reused": False,
               **({"miss": misses[name]} if name in misses else {})} for name, s in times.items()]
    proofs += [{"proof": name, "status": "pass", "run_s": 0.0, "reused": True} for name in reused]
    return {"ts": ts, "mode": "close", "head": head * 40, "proofs": proofs,
            "selected": len(proofs), "reused": len(reused)}


BASE = {"a.py": 10, "b.py": 20, "c.py": 30, "d.py": 40}
history = [run(1, BASE), run(2, {k: v * 1.1 for k, v in BASE.items()})]

grew = run(3, dict(BASE, **{"a.py": 30}))
found = {g["proof"]: g for g in gate_growth.grown(grew, history)}
check("a proof slower than every earlier normalized pass is grown", list(found) == ["a.py"], found)
check("its before time is the top of its earlier normalized range",
      abs(found.get("a.py", {}).get("before_s", 0) - 10.0) < 0.01, found)

loaded = run(3, {k: v * 3 for k, v in BASE.items()})
check("a run slower by one factor for every proof grows nothing", gate_growth.grown(loaded, history) == [],
      gate_growth.grown(loaded, history))

check("growth that stays under the floor is not reported", gate_growth.grown(grew, history, floor_s=30.0) == [],
      gate_growth.grown(grew, history, floor_s=30.0))

thin = [run(1, BASE)]
check("one earlier pass is not a range: nothing grows", gate_growth.grown(grew, thin) == [],
      gate_growth.grown(grew, thin))

check("the close's own row is not its history",
      gate_growth.grown(grew, history + [grew]) == gate_growth.grown(grew, history))

# A proof the caller marks `beyond_spread` must top its range by the range's own width (here 10 -> 11, width 1).
spread = lambda proof: proof == "a.py"  # noqa: E731
wide = [run(1, BASE), run(2, dict(BASE, **{"a.py": 11}))]
edge = run(3, dict(BASE, **{"a.py": 11.5}))
check("a marked proof just above its range top is not grown", gate_growth.grown(edge, wide, beyond_spread=spread) == [],
      gate_growth.grown(edge, wide, beyond_spread=spread))
check("an unmarked proof just above its range top is grown", [g["proof"] for g in gate_growth.grown(edge, wide)] == ["a.py"],
      gate_growth.grown(edge, wide))
far = run(3, dict(BASE, **{"a.py": 12.5}))
check("a marked proof a range width above its top is grown",
      [g["proof"] for g in gate_growth.grown(far, wide, beyond_spread=spread)] == ["a.py"],
      gate_growth.grown(far, wide, beyond_spread=spread))

# The store: open entries persist, count closes, and leave on recovery or removal.
root = tempfile.mkdtemp(prefix="gate_growth_proof_")
try:
    tests = os.path.join(root, "tests")
    os.makedirs(tests)
    for name in BASE:
        open(os.path.join(tests, name), "w").close()
    store = os.path.join(root, "gate_growth.json")
    gate_growth.update_store(store, grew, history, tests)
    held = json.load(open(store, encoding="utf-8"))
    check("a grown proof opens one entry", set(held) == {"a.py"} and held["a.py"]["closes"] == 1, held)

    again = run(4, dict(BASE, **{"a.py": 29}))
    gate_growth.update_store(store, again, history + [grew], tests)
    held = json.load(open(store, encoding="utf-8"))
    check("a later close still above the earlier range counts it again",
          held.get("a.py", {}).get("closes") == 2 and held["a.py"]["before_s"] == 10.0, held)

    back = run(5, BASE)
    gate_growth.update_store(store, back, history + [grew, again], tests)
    held = json.load(open(store, encoding="utf-8"))
    check("a proof back inside its earlier range closes its entry", held == {}, held)

    gate_growth.update_store(store, grew, history, tests)
    os.remove(os.path.join(tests, "a.py"))
    gate_growth.update_store(store, run(6, {"b.py": 20}), history, tests)
    held = json.load(open(store, encoding="utf-8"))
    check("an entry whose proof file is gone leaves", held == {}, held)

    os.remove(store)
    open(os.path.join(tests, "a.py"), "w").close()
    stayed = [run(3, dict(BASE, **{"a.py": 30})), run(4, dict(BASE, **{"a.py": 30}))]
    seen = gate_growth.update_store(store, run(5, dict(BASE, **{"a.py": 30})), history + stayed, tests)
    check("a new store is seeded from the recorded closes, so growth older than it still shows",
          seen.get("a.py", {}).get("closes") == 3 and seen["a.py"]["before_s"] == 10.0, seen)
    os.remove(store)

    gate_growth.update_store(store, grew, history, tests)
    open(os.path.join(tests, "a.py"), "w").close()
    lines = gate_growth.close_lines(grew, history, store, tests)
    check("the close prints a growth line naming the proof and both times",
          any(l.startswith("harness_tests: close growth: a.py 30.0s") and "10.0s" in l for l in lines), lines)

    advice = gate_growth.commit_lines([".claude/tests/a.py", ".claude/tools/x.py"], store)
    check("a commit touching a grown proof gets one advisory", len(advice) == 1 and "a.py" in advice[0], advice)
    check("a commit touching no grown proof gets none",
          gate_growth.commit_lines([".claude/tests/b.py"], store) == [])
    check("a missing store gives no advisory",
          gate_growth.commit_lines([".claude/tests/a.py"], os.path.join(root, "absent.json")) == [])
finally:
    shutil.rmtree(root, ignore_errors=True)

# Reuse lost: each rerun proof counted under the one part that changed, else "several"; no row at all is "no pass".
row = run(7, {"a.py": 10, "b.py": 20, "c.py": 30, "d.py": 40}, reused=("e.py",),
          misses={"a.py": ["runner"], "b.py": ["runner"], "c.py": ["files", "runner"]})
causes = {cause: (n, s) for cause, n, s in gate_growth.reuse_lost(row)}
check("reruns group by their one changed part", causes.get("runner") == (2, 30.0), causes)
check("a rerun with several changed parts is 'several'", causes.get("several") == (1, 30.0), causes)
check("a rerun with no recorded pass is 'no pass'", causes.get("no pass") == (1, 40.0), causes)
line = gate_growth.reuse_line(row)
check("the reuse line leads with the reused count and the largest cause",
      line.startswith("harness_tests: close reuse: 1 of 5 reused; reran for no pass 1 (40s)"), line)

# Inputs by file: a close row's `inputs` ({file: [rerun seconds, reran proofs, readers]}) names the files behind its
# reruns. The reuse line prints the leading ones; an input that leads two closes running is an open entry.
inputs_row = dict(row, inputs={".claude/skills/a.json": [60.0, 3, 272], ".claude/hooks/h.py": [20.0, 1, 40]})
lost = gate_growth.reuse_inputs(inputs_row)
check("inputs rank by the rerun seconds they caused",
      [f for f, _, _, _ in lost] == [".claude/skills/a.json", ".claude/hooks/h.py"], lost)
line = gate_growth.reuse_line(inputs_row)
check("the reuse line names the leading input with its seconds, reruns and readers",
      "inputs: .claude/skills/a.json 60s (3 reran, read by 272)" in line, line)
check("a row with no inputs field names no input", "inputs:" not in gate_growth.reuse_line(row),
      gate_growth.reuse_line(row))
iroot = tempfile.mkdtemp(prefix="gate_growth_inputs_")
try:
    itests = os.path.join(iroot, "tests")
    os.makedirs(itests)
    for name in BASE:
        open(os.path.join(itests, name), "w").close()
    istore = os.path.join(iroot, "gate_growth.json")
    one = dict(run(10, BASE), inputs={"x.json": [50.0, 2, 9], "y.py": [10.0, 1, 3]})
    two = dict(run(11, BASE), inputs={"x.json": [40.0, 2, 9]})
    three = dict(run(12, BASE), inputs={"y.py": [90.0, 4, 3]})
    gate_growth.close_lines(one, history, istore, itests)
    held = gate_growth.open_inputs(istore)
    check("one close led by an input opens no entry", held == {}, held)
    lines = gate_growth.close_lines(two, history + [one], istore, itests)
    held = gate_growth.open_inputs(istore)
    check("an input leading two closes running opens an entry with its totals across closes",
          set(held) == {"x.json"} and held["x.json"]["total_s"] == 90.0 and held["x.json"]["led"] == 2
          and held["x.json"]["readers"] == 9, held)
    check("the close names the open input", any(l.startswith("harness_tests: close input: x.json") for l in lines), lines)
    gate_growth.close_lines(three, history + [one, two], istore, itests)
    held = gate_growth.open_inputs(istore)
    check("another input leading a close closes the entry", held == {}, held)
    check("the growth store keeps only proof entries", all(not k.endswith((".json", ".py")) or k in BASE
                                                          for k in json.load(open(istore, encoding="utf-8"))))
finally:
    shutil.rmtree(iroot, ignore_errors=True)

# A partial run (a proof that reused some of its own cases) is not a time of the proof: it must set no fastest pass, no
# load and no range. Without this, a 7 s partial run of a 90 s proof makes every full run read 13x slow.
partial = run(3, {"a.py": 2.0})
partial["proofs"][0]["cases_reused"] = 5
check("a partial run is no timed pass", gate_growth._timed(partial) == {}, gate_growth._timed(partial))
pair = [run(1, {"a.py": 10, "b.py": 20}), run(2, {"a.py": 10, "b.py": 20}), partial]
check("a partial run sets no fastest pass, so b.py's unchanged time is not growth",
      gate_growth.grown(run(4, {"b.py": 20}), pair) == [], gate_growth.grown(run(4, {"b.py": 20}), pair))

# `lasting`: growth is a level shift, so a proof is grown only when its last two normalized times both top its earlier
# range by that range's width. One slow run is load or chance: a rare pass among fast ones.
steady = history + [run(3, BASE)]
check("lasting: one slow run is not growth", gate_growth.grown(grew, steady, lasting=True) == [],
      gate_growth.grown(grew, steady, lasting=True))
spiked = steady + [run(4, dict(BASE, **{"a.py": 30}))]
check("lasting: a slow run then a fast one is not growth",
      gate_growth.grown(run(5, BASE), spiked, lasting=True) == [], gate_growth.grown(run(5, BASE), spiked, lasting=True))
twice = gate_growth.grown(run(5, dict(BASE, **{"a.py": 30})), spiked, lasting=True)
check("lasting: two slow runs in a row are grown, against the range before them",
      [g["proof"] for g in twice] == ["a.py"] and abs(twice[0]["before_s"] - 10.0) < 0.01, twice)
slow_all = {k: v * 3 for k, v in BASE.items()}
check("lasting: two runs slowed by one factor for every proof are not growth",
      gate_growth.grown(run(5, slow_all), steady + [run(4, slow_all)], lasting=True) == [])
wide_steady = wide + [run(3, BASE)]
edge_twice = wide_steady + [run(4, dict(BASE, **{"a.py": 11.5}))]
check("lasting: two runs just above a range top, inside its width, are not growth",
      gate_growth.grown(run(5, dict(BASE, **{"a.py": 11.5})), edge_twice, lasting=True) == [],
      gate_growth.grown(run(5, dict(BASE, **{"a.py": 11.5})), edge_twice, lasting=True))
check("without lasting, one slow run is still grown", [g["proof"] for g in gate_growth.grown(grew, steady)] == ["a.py"])

lroot = tempfile.mkdtemp(prefix="gate_growth_lasting_")
try:
    ltests = os.path.join(lroot, "tests")
    os.makedirs(ltests)
    for name in BASE:
        open(os.path.join(ltests, name), "w").close()
    lstore = os.path.join(lroot, "gate_growth.json")
    gate_growth.update_store(lstore, run(4, dict(BASE, **{"a.py": 30})), steady, ltests, lasting=True)
    check("lasting: one slow run opens no entry", json.load(open(lstore, encoding="utf-8")) == {})
    gate_growth.update_store(lstore, run(5, dict(BASE, **{"a.py": 30})), spiked, ltests, lasting=True)
    held = json.load(open(lstore, encoding="utf-8"))
    check("lasting: the second slow run opens the entry at the range before them",
          set(held) == {"a.py"} and held["a.py"]["before_s"] == 10.0, held)
    lines = gate_growth.close_lines(run(5, dict(BASE, **{"a.py": 30})), spiked, os.path.join(lroot, "other.json"),
                                    ltests, lasting=True)
    check("lasting: the close line names the proof only after its second slow run",
          lines[0].startswith("harness_tests: close growth: a.py 30.0s"), lines)
finally:
    shutil.rmtree(lroot, ignore_errors=True)

if FAILURES:
    print("\n%d failure(s):" % len(FAILURES))
    for failure in FAILURES:
        print("  " + failure)
    sys.exit(1)
print("\nall checks passed")
