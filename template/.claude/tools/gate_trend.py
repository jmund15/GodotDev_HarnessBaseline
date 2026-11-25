#!/usr/bin/env python3
"""What a C# gate run's time grew from, judged against the gate's own recorded runs.

`regression_gate.ps1` pipes its run's printed lines to this tool at its single exit (`Complete-Gate`), and prints
what comes back before the verdict. The history is the newest `LIMIT` `gate_run_*.out` files beside the run, which
a gate writes line by line, so a run that failed or was cut short is in it with the units it finished.

- A unit is Sanity, Logic or one Integration batch, timed from its GREEN line; `pool` is the pool stage's seconds.
  A batch restored from a prior green (no elapsed), a RED batch, and a pool that did not time every batch are not timed.
- A unit is named when its load-normalized time is above every earlier normalized time of it over two or more earlier
  runs: the judgment `gate_growth.grown` makes for harness proofs, so a machine slower for every unit names nothing
  and no fixed threshold enters. A run that grew nothing prints nothing.
- A grown batch also names the test classes added since the previous recorded run (its `TREE head=`) whose file sits
  under the folder of one of the batch's units (`integration_batches.json` lists them). A class added to an existing
  file, or a file whose folder does not name its unit, is not named; a batch is a repack of units, so its label
  alone does not say the same work ran in it before.

The tool reports facts and changes no verdict.

    <gate output> | python3 .claude/tools/gate_trend.py --dir <test_runs> --run <runId> [--state integration_batches.json --repo <checkout>]
    python3 .claude/tools/gate_trend.py --replay <test_runs>      # what each recorded run would have printed
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
import gate_growth  # noqa: E402
from _git_run import run as git_run  # noqa: E402

LIMIT = 40
_NAME = re.compile(r"gate_run_(\d{8}-\d{6})-\d+\.out$")
_SUITE = re.compile(r"^SUITE (Sanity|Logic) .*status=GREEN .*dur=([\d.]+)s")
_BATCH = re.compile(r"^BATCH (\d+)/(\d+) label=(\S+) status=(\w+) .*elapsed=([\d.]+)s")
_POOL = re.compile(r"^STAGES .*\bpool=([\d.]+)s")
_HEAD = re.compile(r"^TREE head=(\w+)")
_CLASS = re.compile(r"\[(?:TestSuite|RequiresRenderer)[^\]]*\](?:\s*\[[^\]]*\])*\s*(?:(?:public|internal|sealed|partial|static)\s+)*class\s+(\w+)")


def _ts(name):
    m = _NAME.search(name)
    return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S")) if m else 0.0


def parse_out(text, name):
    """The row of one gate output in the shape `gate_growth.grown` reads: {ts, name, head, proofs: [{proof, status, run_s}]}."""
    times, head, planned, pool, integration = {}, "", 0, None, False
    for line in (text or "").splitlines():
        if m := _HEAD.match(line):
            head = m.group(1)
        elif m := _SUITE.match(line):
            times[m.group(1)] = float(m.group(2))
        elif line.startswith("SUITE Integration "):
            integration = True
        elif m := _BATCH.match(line):
            planned = int(m.group(2))
            if m.group(4) == "GREEN" and float(m.group(5)) > 0:
                times[m.group(3)] = float(m.group(5))
        elif m := _POOL.match(line):
            pool = float(m.group(1))
    batches = [k for k in times if k not in ("Sanity", "Logic")]
    if pool and integration and planned and len(batches) == planned and "Sanity" in times and "Logic" in times:
        times["pool"] = pool
    return {"ts": _ts(name), "name": name, "head": head,
            "proofs": [{"proof": k, "status": "pass", "run_s": v, "reused": False} for k, v in times.items()]}


def history(directory, run_id, limit=LIMIT):
    """The newest `limit` earlier recorded runs that timed anything, oldest first. A file that cannot be read is skipped."""
    current = "gate_run_%s.out" % run_id
    names = sorted(n for n in os.listdir(directory) if _NAME.search(n) and n < current) if os.path.isdir(directory) else []
    rows = []
    for name in reversed(names):
        try:
            with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as f:
                row = parse_out(f.read(), name)
        except OSError:
            continue
        if row["proofs"]:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows[::-1]


def _unit_for(path, units):
    parts = path.replace("\\", "/").split("/")[2:]
    dirs, stem = parts[:-1], os.path.splitext(parts[-1])[0]
    for k in range(len(dirs), 0, -1):
        if ".".join(dirs[:k]) in units:
            return ".".join(dirs[:k])
    return stem if stem in units and not dirs else None


def added_classes(repo, prev_head, units):
    """{unit: [test class]} for each test file added under Tests/Integration since `prev_head` (committed, staged or
    untracked) whose folder names one of `units`."""
    if not prev_head or not repo:
        return {}

    def git(*args):
        r = git_run(args, cwd=repo)
        return r.text.splitlines() if r.returncode == 0 else None
    added = git("diff", "--name-only", "--diff-filter=A", prev_head, "--", "Tests/Integration")
    if added is None:
        return {}
    added += git("ls-files", "--others", "--exclude-standard", "--", "Tests/Integration") or []
    found = {}
    for path in sorted(set(added)):
        unit = _unit_for(path, set(units))
        if not unit or not path.endswith(".cs"):
            continue
        try:
            with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as f:
                names = _CLASS.findall(f.read())
        except OSError:
            continue
        found.setdefault(unit, []).extend(names)
    return {u: sorted(set(c)) for u, c in found.items() if c}


def _segs(state_path, run_id):
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict) or state.get("runId") != run_id:
        return {}
    return {b["label"]: list(b.get("segs") or []) for b in state.get("batches", []) if isinstance(b, dict) and "label" in b}


def report(text, run_id, directory, limit=LIMIT, state=None, repo=None):
    """One line per unit of this run above its recorded range; [] when none is."""
    row = parse_out(text, "gate_run_%s.out" % run_id)
    runs = history(directory, run_id, limit)
    grown = gate_growth.grown(row, runs, beyond_spread=lambda unit: unit.startswith("Integration_B"))
    if not grown:
        return []
    segs = _segs(state, run_id) if state else {}
    prev_head = next((r["head"] for r in reversed(runs) if r["head"]), "")
    wanted = sorted({s for g in grown for s in segs.get(g["proof"], [])})
    added = added_classes(repo, prev_head, wanted) if wanted else {}
    lines = []
    for g in sorted(grown, key=lambda g: g["before_s"] - g["now_s"]):
        line = "gate_trend: %s %.1fs vs at most %.1fs before, over %d earlier runs (load-normalized)" % (
            g["proof"], g["now_s"], g["before_s"], g["samples"])
        new = ["%s (%s)" % (c, u) for u in segs.get(g["proof"], []) for c in added.get(u, [])]
        if new:
            line += "; test classes added since %s: %s" % (prev_head[:9], ", ".join(new))
        lines.append(line)
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dir")
    parser.add_argument("--run")
    parser.add_argument("--state")
    parser.add_argument("--repo")
    parser.add_argument("--replay", help="a test_runs directory: print what each recorded run would have printed")
    args = parser.parse_args(argv)
    if args.replay:
        names = sorted(n for n in os.listdir(args.replay) if _NAME.search(n))
        flagged = 0
        for name in names:
            with open(os.path.join(args.replay, name), encoding="utf-8", errors="replace") as f:
                text = f.read()
            if not parse_out(text, name)["proofs"]:
                continue
            for line in report(text, _NAME.search(name).group(0)[len("gate_run_"):-len(".out")], args.replay):
                flagged += 1
                print("%s %s" % (name[len("gate_run_"):-len(".out")], line[len("gate_trend: "):]))
        print("flagged %d unit(s) over %d recorded runs" % (flagged, len(names)))
        return 0
    if not args.dir or not args.run:
        parser.error("--dir and --run are required without --replay")
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    for line in report(text, args.run, args.dir, state=args.state, repo=args.repo):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
