#!/usr/bin/env python3
"""Measure the run time a change adds to one or more proof files, base against change on one machine.

    python3 .claude/tools/proof_cost.py [--base REV] [--runs N] [--json] PROOF [PROOF ...]

Every proof runs `--runs` times (default 3) on each side. Within a round both sides run adjacent, and the
side that goes first flips each round, so machine load moves the two sides alike. The base side is a private
sparse checkout of `--base` (default: the merge-base of HEAD and the remote default branch, or local main
if there is no remote default). If that default base equals HEAD and the named proofs are clean, pass `--base`
to choose a comparison explicitly. A proof absent at base has no base side; only its own times are reported.
Each run launches through the harness proof runner
(`_proof_runner.attempt.run`), so admission, environment and timeouts match a harness proof run, and a proof
takes a machine lane per run.

The report gives each side's median and range and the delta, with whether the delta exceeds the two sides'
combined spread. It states facts only: whether the cost is worth what the cases catch is the reader's call
(`skills/instruction_quality/SKILL.md` §3). A failing run is reported failing and never timed as a pass.
See `reference/mechanism_litmus.md` L3.
"""
import argparse
import json
import os
import statistics
import sys

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
_CLAUDE_DIR = os.path.dirname(_SCRIPTS_DIR)
REPO_ROOT = os.path.dirname(_CLAUDE_DIR)
sys.path.insert(0, _SCRIPTS_DIR)
import harness_tests  # noqa: E402
from _proof_runner import attempt as _attempt  # noqa: E402

DEFAULT_ROUNDS = 3


def side_orders(rounds):
    """The side order each round runs in: the base first on even rounds, the change first on odd ones."""
    return [("base", "change") if index % 2 == 0 else ("change", "base") for index in range(rounds)]


def _side(times, failure=None, absent=False):
    if absent:
        return {"absent": True, "failed": False, "detail": None,
                "median": None, "min": None, "max": None, "runs": []}
    if failure is not None:
        return {"absent": False, "failed": True, "detail": failure,
                "median": None, "min": None, "max": None, "runs": list(times)}
    if not times:
        raise ValueError("a side with no failed run must have at least one passing run")
    return {"absent": False, "failed": False, "detail": None,
            "median": statistics.median(times), "min": min(times), "max": max(times), "runs": list(times)}


def summarize(proof, base_times, change_times, base_failure=None, change_failure=None):
    """One proof's report: each side's median and range, the delta and whether it exceeds the combined spread
    (None when a side has one run: its spread is zero, so any delta would read as exceeding it).

    `base_times is None` means the proof is absent at the base revision. A side with `*_failure` set is
    reported failing: it carries no median and no delta, so a red is never counted as a pass timing.
    """
    base = _side(base_times or [], base_failure, absent=base_times is None)
    change = _side(change_times, change_failure)
    delta = spread = exceeds = None
    if not base["absent"] and not base["failed"] and not change["failed"]:
        delta = change["median"] - base["median"]
        spread = (base["max"] - base["min"]) + (change["max"] - change["min"])
        exceeds = abs(delta) > spread if len(base["runs"]) > 1 and len(change["runs"]) > 1 else None
    return {"proof": proof, "base": base, "change": change,
            "delta": delta, "combined_spread": spread, "exceeds_spread": exceeds}


def _failure_detail(answer):
    lines = [line.strip() for line in (answer.detail or "").splitlines() if line.strip()]
    return "%s: %s" % (answer.status, lines[-1] if lines else "no output")


def _proof_paths(repo_root, proofs):
    rels = []
    for proof in proofs:
        rel = os.path.relpath(os.path.abspath(proof), repo_root)
        if rel == ".." or rel.startswith(".." + os.sep):
            raise ValueError("proof outside the repository: %s" % proof)
        if not os.path.isfile(os.path.join(repo_root, rel)):
            raise FileNotFoundError("proof not found: %s" % proof)
        rels.append(rel.replace(os.sep, "/"))
    return rels


def measure(repo_root, proofs, base_rev, rounds=DEFAULT_ROUNDS, ctx=None):
    """One `summarize` report per proof, in the order given. `proofs` are repo-relative or absolute paths.

    Both sides run through `_proof_runner.attempt.run`, which takes and releases a machine lane per attempt.
    `ctx` overrides the runner context (a proof's test drives it); the default is the harness runner's own.
    """
    if rounds < 1:
        raise ValueError("--runs must be at least 1")
    rels = _proof_paths(repo_root, proofs)
    base_commit = harness_tests._git(repo_root, ["rev-parse", "--verify", "--end-of-options",
                                                base_rev + "^{commit}"]).strip()
    ctx = ctx or harness_tests._run_context(root=repo_root)
    durations = harness_tests.durations_path()
    known = harness_tests.read_json_salvage(durations) if os.path.exists(durations) else {}
    with harness_tests._revision_checkout(repo_root, base_commit) as base_root:
        present = {rel: os.path.isfile(os.path.join(base_root, rel)) for rel in rels}
        times = {rel: {"base": [] if present[rel] else None, "change": []} for rel in rels}
        failures = {rel: {"base": None, "change": None} for rel in rels}
        for order in side_orders(rounds):
            for rel in rels:
                for side in order:
                    if side == "base" and not present[rel]:
                        continue
                    root = base_root if side == "base" else repo_root
                    proof = os.path.join(root, rel)
                    answer = _attempt.run(ctx, _attempt.AttemptJob(
                        proof=proof, purpose="diagnostic", root=root, timeout=harness_tests._timeout_for(proof, known)))
                    if answer.status == "pass":
                        times[rel][side].append(answer.run_s)
                    else:
                        failures[rel][side] = failures[rel][side] or _failure_detail(answer)
        return [summarize(rel, times[rel]["base"], times[rel]["change"],
                          failures[rel]["base"], failures[rel]["change"]) for rel in rels]


def render(result):
    """A report's lines, one fact per line."""
    lines = [result["proof"]]
    for name in ("base", "change"):
        side = result[name]
        if side["absent"]:
            lines.append("  %-6s absent at the base revision" % name)
        elif side["failed"]:
            lines.append("  %-6s FAILED (%s), %d passing run(s) not counted"
                         % (name, side["detail"], len(side["runs"])))
        else:
            lines.append("  %-6s median %.3fs  range %.3f-%.3fs  n=%d"
                         % (name, side["median"], side["min"], side["max"], len(side["runs"])))
    if result["delta"] is None:
        lines.append("  new proof: no base side" if result["base"]["absent"] else "  no delta: a run failed")
    else:
        lines.append("  delta %+.3fs  combined spread %.3fs  %s"
                     % (result["delta"], result["combined_spread"],
                        "unjudged: one run on a side has no spread" if result["exceeds_spread"] is None
                        else "exceeds spread" if result["exceeds_spread"] else "inside spread"))
    return lines


def _default_base(repo_root):
    ref = harness_tests._default_ref(repo_root) or "main"
    return harness_tests._git_soft(repo_root, ["merge-base", "HEAD", ref])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure the run time a change adds to proof files.")
    parser.add_argument("proofs", nargs="+", help="proof files to measure, repo-relative or absolute")
    parser.add_argument("--base", default=None, help="base revision (default: merge-base with the default branch)")
    parser.add_argument("--runs", type=int, default=DEFAULT_ROUNDS, help="runs per side (default %d)"
                        % DEFAULT_ROUNDS)
    parser.add_argument("--json", action="store_true", help="print the reports as JSON")
    args = parser.parse_args(argv)
    base = args.base or _default_base(REPO_ROOT)
    if not base:
        print("proof_cost: no merge-base with the default branch; pass --base REV", file=sys.stderr)
        return 2
    try:
        if args.base is None and args.runs >= 1 and base == harness_tests._head(REPO_ROOT):
            paths = _proof_paths(REPO_ROOT, args.proofs)
            dirty = harness_tests._git(REPO_ROOT, ["--literal-pathspecs", "status", "--porcelain",
                                                   "--untracked-files=all", "--ignored=matching", "--", *paths])
            if not dirty:
                raise ValueError("base equals HEAD and the named proofs are clean; pass --base REV")
        reports = measure(REPO_ROOT, args.proofs, base, args.runs)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print("proof_cost: %s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            print("\n".join(render(report)))
    return 1 if any(r["base"]["failed"] or r["change"]["failed"] for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
