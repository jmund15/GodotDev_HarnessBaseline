"""Proof that a C# gate run names what its time grew from, against the gate's own recorded runs, with no fixed threshold.

Fixtures under fixtures/gate_trend/ are real `gate_run_*.out` lines (TREE, SUITE, BATCH, STAGES, VERDICT) of six runs.

- A unit (Sanity, Logic, each Integration batch) and the pool as a whole are timed from the output a gate prints;
  a batch restored from a prior green (no elapsed), a RED batch and a pool that did not run every batch are not.
- A unit above every earlier load-normalized time of it is named with its earlier top; a run slower by one factor for
  every unit names nothing, and a run that grew nothing prints nothing.
- The history is the newest few recorded runs: a cut-short output and an unreadable file add what they hold and fail nothing.
- A test class added since the previous recorded run is named under the batch whose unit folder holds its file.

    python3 .claude/tests/test_gate_trend.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import gate_trend  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "gate_trend")
RUNS = sorted(os.listdir(FIX))
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s: %s" % (label, detail))


def text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def scaled(src, factor, only=None):
    """`src` with each elapsed/dur seconds multiplied by `factor` (only the BATCH label `only` when given)."""
    def bump(m):
        return "%s%.1fs" % (m.group(1), float(m.group(2)) * factor)
    out = []
    for line in src.splitlines():
        if only is None and (line.startswith("BATCH ") or line.startswith("SUITE ")):
            line = re.sub(r"((?:elapsed|dur)=)([\d.]+)s", bump, line)
        elif only is not None and line.startswith("BATCH ") and (" label=%s " % only) in line:
            line = re.sub(r"(elapsed=)([\d.]+)s", bump, line)
        elif only is None and line.startswith("STAGES "):
            line = re.sub(r"(pool=)([\d.]+)s", bump, line)
        out.append(line)
    return "\n".join(out) + "\n"


def write_history(directory, names, transform=None):
    for name in names:
        src = text(name)
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(transform(src) if transform else src)


last_name = RUNS[-1]
last = text(last_name)
row = gate_trend.parse_out(last, last_name)
proofs = {p["proof"]: p["run_s"] for p in row["proofs"]}
check("Sanity, Logic, every batch and the pool are timed from a full pass",
      {"Sanity", "Logic", "Integration_B1", "Integration_B16", "Render", "pool"} <= set(proofs)
      and len([p for p in proofs if p.startswith("Integration_B")]) == 16, sorted(proofs))
check("a batch's elapsed and the pool's stage seconds are the run seconds",
      abs(proofs["Integration_B3"] - 63.0) < 0.01 and abs(proofs["pool"] - 268.7) < 0.01, proofs)
check("the run's start comes from its file name", row["ts"] < gate_trend.parse_out(last, "gate_run_29991111-102846-1.out")["ts"], row["ts"])
check("the tree head is kept for the class lookup", row["head"].startswith("f97b0041a1"), row["head"])

restored = last.replace("elapsed=63s expected=62.5s", "elapsed=0s expected=62.5s")
rproofs = {p["proof"] for p in gate_trend.parse_out(restored, last_name)["proofs"]}
check("a batch restored from a prior green is not timed, and the pool it leaves incomplete is not timed",
      "Integration_B3" not in rproofs and "pool" not in rproofs and "Integration_B4" in rproofs, sorted(rproofs))

red = last.replace("label=Integration_B5 status=GREEN", "label=Integration_B5 status=RED")
check("a RED batch is not timed", "Integration_B5" not in {p["proof"] for p in gate_trend.parse_out(red, last_name)["proofs"]})

cut = "\n".join(last.splitlines()[:12]) + "\n"
cproofs = {p["proof"] for p in gate_trend.parse_out(cut, last_name)["proofs"]}
check("a cut-short output keeps the units it finished and has no pool",
      "Sanity" in cproofs and "Integration_B1" in cproofs and "pool" not in cproofs, sorted(cproofs))
check("an empty output parses to no units", gate_trend.parse_out("", last_name)["proofs"] == [])

tmp = tempfile.mkdtemp(prefix="gate_trend_")
try:
    write_history(tmp, RUNS)
    current_id = "20261112-000000-1"
    rerun = text(RUNS[3])

    lines = gate_trend.report(rerun, current_id, tmp)
    check("a rerun of a recorded run prints nothing", lines == [], lines)

    lines = gate_trend.report(scaled(rerun, 3.0), current_id, tmp)
    check("a run slower by one factor for every unit prints nothing", lines == [], lines)

    lines = gate_trend.report(scaled(rerun, 4.0, only="Integration_B7"), current_id, tmp)
    check("one batch far above its range is named with the top it had before",
          len(lines) == 1 and "Integration_B7" in lines[0] and "at most" in lines[0], lines)
    check("only the grown batch is named", len(lines) == 1 and "Integration_B8" not in lines[0], lines)

    lines = gate_trend.report(rerun.replace("pool=190.4s", "pool=900.0s"), current_id, tmp)
    check("the pool as a whole is named when it ran above its range",
          len(lines) == 1 and "pool" in lines[0], lines)


    before = len(gate_trend.history(tmp, current_id, limit=100))
    with open(os.path.join(tmp, "gate_run_20200101-000000-1.out"), "w") as f:
        f.write("not a gate output\n\x00\x01")
    with open(os.path.join(tmp, "gate_run_20200101-000001-1.out"), "w") as f:
        f.write("\n".join(text(RUNS[0]).splitlines()[:5]) + "\n")
    check("an unreadable or cut-short file in the history fails nothing", len(gate_trend.history(tmp, current_id, limit=100)) >= before)

    check("the history is the newest few recorded runs, oldest first",
          [r["name"] for r in gate_trend.history(tmp, current_id, limit=3)] == sorted(os.listdir(tmp))[-3:],
          [r["name"] for r in gate_trend.history(tmp, current_id, limit=3)])
    own = [r["name"] for r in gate_trend.history(tmp, "20261111-070510-38248", limit=100)]
    check("the current run and every later one are never part of its history",
          "gate_run_20261111-070510-38248.out" not in own and "gate_run_20261111-102846-1416.out" not in own, own)

    before_one = gate_trend.report(scaled(rerun, 4.0, only="Integration_B7"), current_id, tmp)
    check("one earlier pass is not a range: a lone recorded run names nothing",
          gate_trend.report(scaled(rerun, 4.0, only="Integration_B7"), current_id, tmp, limit=1) == [], before_one)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

repo = tempfile.mkdtemp(prefix="gate_trend_repo_")
try:
    def git(*args):
        return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(args),
                              capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q")
    os.makedirs(os.path.join(repo, "Tests", "Integration", "NPCs", "AI"))
    with open(os.path.join(repo, "Tests", "Integration", "NPCs", "AI", "Old.cs"), "w") as f:
        f.write("namespace A;\n[TestSuite]\npublic class OldTests {}\n")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    base = git("rev-parse", "HEAD")
    with open(os.path.join(repo, "Tests", "Integration", "NPCs", "AI", "Fresh.cs"), "w") as f:
        f.write("namespace A;\n[TestSuite]\npublic class FreshTests {}\n[TestSuite]\npublic class SecondTests {}\n")
    git("add", "-A")
    git("commit", "-q", "-m", "two")
    with open(os.path.join(repo, "Tests", "Integration", "Loose.cs"), "w") as f:
        f.write("namespace A;\n[TestSuite]\npublic class LooseTests {}\n")
    found = gate_trend.added_classes(repo, base[:10], ["NPCs.AI", "Loose", "Other"])
    check("a class added since the previous head is named under the unit that holds its file",
          found == {"NPCs.AI": ["FreshTests", "SecondTests"], "Loose": ["LooseTests"]}, found)
    check("no class is named when the previous head is unknown", gate_trend.added_classes(repo, "", ["NPCs.AI"]) == {})

    tmp = tempfile.mkdtemp(prefix="gate_trend_")
    try:
        write_history(tmp, RUNS, lambda src: src.replace("head=f97b0041a1", "head=" + base[:10]))
        state = os.path.join(tmp, "integration_batches.json")
        rerun = text(RUNS[3])
        grown_text = scaled(rerun, 4.0, only="Integration_B7")
        with open(state, "w") as f:
            f.write('{"runId": "20261112-000000-1", "batches": [{"label": "Integration_B7", "segs": ["NPCs.AI"]}]}')
        lines = gate_trend.report(grown_text, "20261112-000000-1", tmp, state=state, repo=repo)
        check("a grown batch names the classes added since the previous recorded run under its units",
              len(lines) == 1 and "FreshTests (NPCs.AI)" in lines[0] and "SecondTests (NPCs.AI)" in lines[0]
              and "LooseTests" not in lines[0], lines)
        with open(state, "w") as f:
            f.write('{"runId": "another-run", "batches": [{"label": "Integration_B7", "segs": ["NPCs.AI"]}]}')
        lines = gate_trend.report(grown_text, "20261112-000000-1", tmp, state=state, repo=repo)
        check("a state file of another run names no class", len(lines) == 1 and "added since" not in lines[0], lines)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
finally:
    shutil.rmtree(repo, ignore_errors=True)

if FAILURES:
    print("\n%d FAILED" % len(FAILURES))
    for failure in FAILURES:
        print(" - " + failure)
    sys.exit(1)
print("\nall ok")
