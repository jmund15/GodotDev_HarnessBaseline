#!/usr/bin/env python3
"""What a harness close's time grew from, judged against the closes recorded before it.

Every close appends its row to `harness_runs.json` (each proof's run seconds, whether it was reused, and the parts
of its reuse key that changed). At the close's end `harness_tests.py` calls `close_lines`, which:

- names each proof that grew: its load-normalized time is above every earlier normalized time of it, over two or
  more earlier passes. A run's load is the median, over its proofs with history, of run seconds over that proof's
  fastest recorded pass; a proof's normalized time is its run seconds over its run's load. So a machine slower for
  every proof grows nothing, and no fixed threshold enters: the proof's own recorded range is the bar.
  The close asks for `lasting` growth: a level shift, not a slow run. A proof's last two normalized times must both
  top the range before them by its width, since load that the median misses (a spawn-heavy proof suffers more than
  the median proof) and chance lift single runs of one proof, and growth lifts all of them. A partial pass (a
  proof that reused some of its own cases) is no time and joins no range or load. Both recent raw times must also
  exceed its earlier fastest whole pass: the other proofs' pace cannot make a faster proof into growth.
  Lasting growth also needs enough earlier passes that chance alone cannot name the proof. The close tests every
  proof against its own range, and a proof whose last two passes are exchangeable with its n earlier ones tops them
  all with chance 1 in C(n + 2, 2). The close names a proof only when C(n + 2, 2) exceeds the number of proofs it
  tested, so chance alone expects under one false name. The median load cannot divide out a proof that spawns or
  case-pools: it slows several times between two closes at one load, and a thin range reads that as growth. Such a
  proof stays unjudged until its history is deep enough.
- keeps one open entry per grown proof in the shared store (`harness_tests.growth_path()`): the top of the range it
  had before (`before_s`), the passes that set that range (`samples`), its latest normalized time (`now_s`) and the
  closes that saw it above that range. A close that runs the proof inside `before_s` closes the entry; an entry
  whose proof file is gone leaves. An entry also leaves when the closes that saw the proof above its range outnumber
  `samples`: no pass came back inside the range, and the passes since outweigh the ones that set it, so the range was
  the exception (a quiet-machine pass, a thin history) and no pass under normal load will reach it. An entry from
  before `samples` counts the proof's whole passes in the run log before it opened, and at least the two any range
  needs, since the log keeps only its newest rows. A range built on more passes holds its entry for that many
  closes. Listing and commit advisories also reconcile later full passes from the run log or shared duration
  receipts. A scalar fastest time has no date and never closes newer growth. The store holds at most one row per
  proof.
- groups the proofs the close reran by the one reuse-key part whose change made each rerun (`reuse_lost`), and names
  the input files behind them with their rerun seconds and readers (`reuse_inputs`, from the row's `inputs`).
- keeps per-input rerun totals across closes beside the store (`inputs_path`). An input that led the reruns of two
  closes running is an open entry, listed with the grown proofs: a shared read that widened shows on the land that
  widened it, with its cost and reader count.

`commit_lines` gives a commit that touches a proof with an open entry one advisory: that session is editing the
proof, so splitting it costs least there. The tool reports times; how to cut a proof stays the session's judgment.

    python3 .claude/tools/gate_growth.py [--store PATH]                  # the open entries
    python3 .claude/tools/gate_growth.py --replay RUNS_JSON [--tests DIR] # replay recorded closes into a temp store
"""
import argparse
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from _hook_state import read_json_salvage, update_json_locked  # noqa: E402


def _timed(run):
    """{proof: run seconds} of the passes a run ran whole itself: a reused pass has no time, and a partial pass
    (`cases_reused`: some of its cases came from an earlier run) is a fraction of the proof's time."""
    out = {}
    for p in run.get("proofs", []) if isinstance(run, dict) else []:
        if (isinstance(p, dict) and p.get("status") == "pass" and not p.get("reused") and not p.get("cases_reused")
                and (p.get("run_s") or 0) > 0):
            out[p["proof"]] = float(p["run_s"])
    return out


def _earlier(row, runs):
    """The runs recorded before `row`, oldest first, its own row excluded."""
    ts = row.get("ts", float("inf"))
    return sorted((r for r in runs if isinstance(r, dict) and r.get("ts", 0) < ts), key=lambda r: r.get("ts", 0))


def _fastest(runs):
    found = {}
    for run in runs:
        for proof, seconds in _timed(run).items():
            found[proof] = min(seconds, found.get(proof, seconds))
    return found


def _load(times, fastest):
    """The median ratio of run seconds to fastest recorded seconds over the run's proofs with history, else None."""
    ratios = [s / fastest[p] for p, s in times.items() if p in fastest]
    return statistics.median(ratios) if ratios else None


def _normalized(run, fastest):
    times = _timed(run)
    load = _load(times, fastest)
    return {p: s / load for p, s in times.items()} if load else {}


def _ranges(earlier):
    """{proof: [normalized times]} over the earlier runs, normalized by the fastest passes among them."""
    fastest = _fastest(earlier)
    ranges = {}
    for run in earlier:
        for proof, value in _normalized(run, fastest).items():
            ranges.setdefault(proof, []).append(value)
    return ranges, fastest


def _now(row, earlier):
    """(ranges, this row's normalized times) against the earlier runs."""
    ranges, fastest = _ranges(earlier)
    return ranges, _normalized(row, fastest)


def grown(row, runs, floor_s=0.0, beyond_spread=lambda proof: False, lasting=False):
    """Each proof `row` ran whose normalized time is above all of its two or more earlier normalized times and above
    `floor_s`, as {proof, now_s, before_s, samples}, largest excess first. The close passes the runner's inner-tier
    budget as `floor_s`: under it a proof still runs at commit time, and replayed history showed growth there is
    sub-second spawn noise. A proof `beyond_spread` names must top its range by the range's own width (top minus
    bottom): it is one of many units that move with a repack, so by chance alone one of them tops its range on most runs.
    `lasting` (the close's setting) asks for a level shift instead of a slow run: the proof's last two normalized
    times must both top the range before them by its width. Load and chance lift one run of a proof and leave its
    fastest runs where they were; growth lifts every run, so one slow pass among faster ones names nothing.
    `lasting` also asks that chance alone cannot name the proof: its n earlier passes must make C(n + 2, 2) exceed
    the proofs this close tests, so the close expects under one false name."""
    earlier = _earlier(row, runs)
    ranges, fastest = _ranges(earlier)
    now = _normalized(row, fastest)
    current = _timed(row)
    tested = []
    for proof, value in now.items():
        past = ranges.get(proof, [])
        recent = [value]
        if lasting:
            past, recent = past[:-1], past[-1:] + recent
        if len(past) >= 2 and proof in fastest:
            tested.append((proof, value, past, recent))
    out = []
    for proof, value, past, recent in tested:
        if lasting and math.comb(len(past) + len(recent), len(recent)) <= len(tested):
            continue
        bar = max(past) + (max(past) - min(past) if lasting or beyond_spread(proof) else 0.0)
        if min(recent) > bar and value > floor_s and current[proof] > fastest[proof]:
            if lasting:
                previous = next((seconds for past_run in reversed(earlier)
                                 if (seconds := _timed(past_run).get(proof)) is not None), None)
                if previous is None or previous <= fastest[proof]:
                    continue
            out.append({"proof": proof, "now_s": round(value, 1), "before_s": round(max(past), 1),
                        "raw_before_s": fastest[proof], "samples": len(past)})
    return sorted(out, key=lambda g: g["before_s"] - g["now_s"])


def _proof_names(tests_dir):
    names = set()
    for _, _, files in os.walk(tests_dir):
        names.update(files)
    return names


def _outnumbered(entry, proof, runs):
    """Whether the closes that saw `entry`'s proof above its range outnumber the passes that set that range: the
    entry's stored `samples`, else (an entry from before `samples`) the proof's whole passes in `runs` before `since`,
    and at least the two passes any range needs: the log keeps only its newest rows, so an old entry's range passes
    may be gone, and its evidence is then the least it opened on.
    No later pass came back inside the range, and the passes since now outweigh the ones it was judged by, so the
    range was the exception and no pass under normal load will reach it."""
    samples = entry.get("samples")
    if isinstance(samples, bool) or not isinstance(samples, (int, float)):
        since = entry.get("since") or 0
        samples = max(2, sum(1 for r in runs if isinstance(r, dict) and (r.get("ts") or 0) < since
                             and proof in _timed(r)))
    return (entry.get("closes") or 0) > samples


def _fold(state, row, runs, present, floor_s, lasting):
    """Fold `row` into `state` in place; return the entries this close saw above their range."""
    earlier = _earlier(row, runs)
    _, now = _now(row, earlier)
    raw = _timed(row)
    fresh = {g["proof"]: g for g in grown(row, runs, floor_s, lasting=lasting)}
    seen = {}
    for proof in [p for p in state if p not in present or not isinstance(state[p], dict)]:
        state.pop(proof)
    for proof, value in now.items():
        entry = state.get(proof)
        if entry:
            if value > entry.get("before_s", 0) and raw[proof] > entry.get("raw_before_s", 0):
                entry.update(now_s=round(value, 1), closes=entry.get("closes", 0) + 1, last=row.get("ts"))
                if _outnumbered(entry, proof, earlier):
                    state.pop(proof)
                else:
                    seen[proof] = entry
            else:
                state.pop(proof)
        elif proof in fresh and proof in present:
            state[proof] = seen[proof] = {"before_s": fresh[proof]["before_s"], "now_s": fresh[proof]["now_s"],
                                          "raw_before_s": fresh[proof]["raw_before_s"],
                                          "samples": fresh[proof]["samples"],
                                          "closes": 1, "since": row.get("ts"), "last": row.get("ts"),
                                          "head": (row.get("head") or "")[:12]}
    return seen


def update_store(store, row, runs, tests_dir, floor_s=0.0, lasting=False):
    """Fold `row` into the open entries at `store`; return the entries this close saw above their range. A store
    that does not exist yet is first seeded by folding every earlier recorded close in order, so growth recorded
    before the store existed is not taken into the range it grew from."""
    present = _proof_names(tests_dir)
    seeding = not os.path.exists(store)
    seen = {}

    def update(state):
        if seeding:
            earlier = _earlier(row, runs)
            for i, past in enumerate(earlier):
                _fold(state, past, earlier[:i], present, floor_s, lasting)
        seen.update(_fold(state, row, runs, present, floor_s, lasting))
    update_json_locked(store, update)
    return seen


def reconcile(store, durations, runs=()):
    """Close entries on a later whole pass back in range, by the close's own test (`_fold`): a recorded run's
    normalized time at or under `before_s`, or a raw time at or under the earlier fastest pass. A duration receipt
    carries no load, so only its raw time counts. An entry whose closes above its range outnumber the passes that
    set it also leaves (`_outnumbered`). A scalar fastest time has no date: it may predate the growth, so it
    is never recovery evidence. New duration receipts are shared across checkouts; retained run rows also recover
    older entries whose receipt was never written."""
    if not os.path.exists(store):
        return {}
    state = read_json_salvage(store)
    lasts = [e.get("last") for e in state.values() if isinstance(e, dict) and isinstance(e.get("last"), (int, float))]

    def positive(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0

    observations, normalized = {}, {}
    for run in runs:
        if not isinstance(run, dict) or not positive(run.get("ts")):
            continue
        for proof, seconds in _timed(run).items():
            if positive(seconds):
                observations.setdefault(proof, []).append((run["ts"], seconds))
        if lasts and run["ts"] > min(lasts):
            for proof, value in _normalized(run, _fastest(_earlier(run, runs))).items():
                normalized.setdefault(proof, []).append((run["ts"], value))
    samples = durations.get("__last_passes__", {}) if isinstance(durations, dict) else {}
    for proof, sample in samples.items() if isinstance(samples, dict) else []:
        if isinstance(sample, dict) and positive(sample.get("ts")) and positive(sample.get("run_s")):
            observations.setdefault(proof, []).append((sample["ts"], sample["run_s"]))

    def recovered(proof, entry):
        if not isinstance(entry, dict):
            return False
        since, last = entry.get("since"), entry.get("last")
        if not positive(since) or not positive(last):
            return False
        past = [seconds for ts, seconds in observations.get(proof, []) if ts < since]
        bar = entry.get("raw_before_s") or (min(past) if past else entry.get("before_s"))
        if positive(bar) and any(ts > last and seconds <= bar for ts, seconds in observations.get(proof, [])):
            return True
        top = entry.get("before_s")
        return positive(top) and any(ts > last and value <= top for ts, value in normalized.get(proof, []))

    def leaves(proof, entry):
        return recovered(proof, entry) or (isinstance(entry, dict) and _outnumbered(entry, proof, runs))

    if any(leaves(p, e) for p, e in state.items()):
        def update(current):
            for proof in [p for p, entry in current.items() if leaves(p, entry)]:
                current.pop(proof)
        update_json_locked(store, update)
        state = read_json_salvage(store)
    return state


def _open_entries(store, runs=None):
    runner = _runner()
    durations = read_json_salvage(runner.durations_path())
    if runs is None:
        runs = read_json_salvage(runner.runs_path()).get("runs", [])
    return reconcile(store, durations, runs if isinstance(runs, list) else [])


def reuse_lost(row):
    """[(cause, proofs, seconds)] over the proofs `row` reran, largest seconds first: the one reuse-key part that
    changed (`other:<part>` when only another checkout's row was there to compare, `unobserved` when the checkout's
    own pass kept no key), `several` when more than one did, `no pass` when no recorded pass of the proof existed."""
    groups = {}
    for p in row.get("proofs", []):
        if not isinstance(p, dict) or p.get("reused"):
            continue
        miss = p.get("miss") or []
        cause = miss[0] if len(miss) == 1 else "several" if miss else "no pass"
        n, s = groups.get(cause, (0, 0.0))
        groups[cause] = (n + 1, s + float(p.get("run_s") or 0))
    return sorted(((c, n, s) for c, (n, s) in groups.items()), key=lambda g: -g[2])


def reuse_inputs(row):
    """[(file, seconds, reran, readers)] from the close row's `inputs`, largest seconds first: each input file whose
    change was among a rerun's causes, the run seconds of the proofs it reran (a proof that two changed files reran
    counts under both), how many it reran, and how many of the close's proofs read it."""
    found = []
    for name, value in (row.get("inputs") or {}).items():
        if isinstance(value, list) and len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
            found.append((name, float(value[0]), int(value[1]), int(value[2])))
    return sorted(found, key=lambda g: (-g[1], g[0]))


def reuse_line(row, top=3):
    lost = ", ".join("%s %d (%.0fs)" % g for g in reuse_lost(row)) or "nothing"
    inputs = "; ".join("%s %.0fs (%d reran, read by %d)" % g for g in reuse_inputs(row)[:top])
    return "harness_tests: close reuse: %d of %d reused; reran for %s%s" % (
        row.get("reused", 0), row.get("selected", 0), lost, "; inputs: " + inputs if inputs else "")


def inputs_path(store):
    """Beside the growth store: per-input rerun totals across closes."""
    base, ext = os.path.splitext(store)
    return base + "_inputs" + ext


def update_inputs(store, row):
    """Fold the close row's inputs into the per-input totals at `inputs_path(store)`; return the open entry this close
    keeps, or None. The close's leading input (most rerun seconds) extends its run of closes led; a close led by
    another input ends every other run. An input that led two closes running is an open entry: one leading close is
    one wide edit, and a read that stays wide leads again. Inputs no close named for 30 days leave."""
    ranked = reuse_inputs(row)
    if not ranked:
        return None
    now, leader = row.get("ts") or time.time(), ranked[0][0]
    kept = {}

    def update(state):
        for name in [n for n, e in state.items() if not isinstance(e, dict) or now - e.get("last", now) > 30 * 86400]:
            state.pop(name)
        for name, seconds, reran, readers in ranked:
            entry = state.setdefault(name, {"total_s": 0.0, "closes": 0, "led": 0})
            entry.update(total_s=round(entry["total_s"] + seconds, 1), closes=entry["closes"] + 1, last=now,
                         last_s=round(seconds, 1), readers=readers)
        for name, entry in state.items():
            entry["led"] = entry.get("led", 0) + 1 if name == leader else 0
        if state[leader]["led"] >= 2:
            kept.update(state[leader], input=leader)
    update_json_locked(inputs_path(store), update)
    return kept or None


def open_inputs(store):
    """{input: entry} of the inputs that led two or more closes running."""
    path = inputs_path(store)
    state = read_json_salvage(path) if os.path.exists(path) else {}
    return {n: e for n, e in state.items() if isinstance(e, dict) and e.get("led", 0) >= 2}


def _describe_input(name, entry):
    return "%s led the reruns of %d closes running (%.0fs this close, %.0fs over %d closes, read by %d proofs)" % (
        name, entry["led"], entry.get("last_s", 0), entry.get("total_s", 0), entry.get("closes", 0),
        entry.get("readers", 0))


def _describe(proof, entry):
    return "%s %.1fs vs at most %.1fs before (%d close%s)" % (
        proof, entry["now_s"], entry["before_s"], entry["closes"], "" if entry["closes"] == 1 else "s")


def close_lines(row, runs, store, tests_dir, floor_s=0.0, lasting=False):
    """The close's growth and reuse lines, after folding `row` into the store, and an input line when the close's
    leading input is an open entry (`update_inputs`)."""
    seen = update_store(store, row, runs, tests_dir, floor_s, lasting)
    held = reconcile(store, {}, list(runs) + [row])
    ordered = sorted(((p, e) for p, e in seen.items() if p in held), key=lambda kv: kv[1]["before_s"] - kv[1]["now_s"])
    growth = "; ".join(_describe(p, e) for p, e in ordered) or "none above its recorded range"
    lines = ["harness_tests: close growth: %s (load-normalized)" % growth, reuse_line(row)]
    leading = update_inputs(store, row)
    if leading:
        lines.append("harness_tests: close input: %s; `python3 .claude/tools/gate_growth.py` lists it"
                     % _describe_input(leading["input"], leading))
    return lines


def commit_lines(touched, store):
    """One advisory per touched proof with an open growth entry."""
    if not os.path.exists(store):
        return []
    state = _open_entries(store)
    out = []
    for path in touched:
        name = os.path.basename(path)
        entry = state.get(name) if path.replace("\\", "/").startswith(".claude/tests/") else None
        if isinstance(entry, dict):
            out.append("[harness commit] %s grew at the close: %s; no close that selects it ends sooner. "
                       "`python3 .claude/tools/gate_growth.py` lists every open entry."
                       % (name, _describe(name, entry).split(" ", 1)[1]))
    return out


def _runner():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import harness_tests
    return harness_tests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--store")
    parser.add_argument("--replay", help="a harness_runs.json to replay, oldest close first, into a temp store")
    parser.add_argument("--tests", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
    args = parser.parse_args(argv)
    if args.replay:
        runs = sorted((r for r in read_json_salvage(args.replay).get("runs", []) if isinstance(r, dict)
                       and r.get("mode") == "close" and r.get("proofs")), key=lambda r: r.get("ts", 0))
        temp = tempfile.mkdtemp(prefix="gate_growth_replay_")
        try:
            store = os.path.join(temp, "gate_growth.json")
            floor_s = _runner()._PROOF_BUDGET_S
            for i, row in enumerate(runs):
                lines = close_lines(row, runs[:i], store, args.tests, floor_s, lasting=True)
                print("%s %s %s" % (time.strftime("%m-%dT%H:%M", time.localtime(row.get("ts", 0))),
                                    (row.get("head") or "")[:9], lines[0].split(": ", 2)[2]))
            print("open at the end: %s" % json.dumps(read_json_salvage(store), sort_keys=True))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        return 0
    store = args.store or _runner().growth_path()
    state = _open_entries(store) if os.path.exists(store) else {}
    for proof, entry in sorted(state.items(), key=lambda kv: kv[1].get("before_s", 0) - kv[1].get("now_s", 0)):
        print(_describe(proof, entry))
    inputs = open_inputs(store)
    for name, entry in sorted(inputs.items(), key=lambda kv: -kv[1].get("total_s", 0)):
        print("input " + _describe_input(name, entry))
    if not state and not inputs:
        print("no open growth entry in %s" % store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
