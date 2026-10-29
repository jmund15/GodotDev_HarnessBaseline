#!/usr/bin/env python3
"""Fan a job list across sidecar launchers -- the cross-transport `dispatch.js`.

WHY THIS EXISTS. `Workflow` and `Agent` run on the session's own endpoint, so a provider seat
reaches every other model through a sidecar Bash call, one job per call. Hand-rolling that loop is
what makes a seat quietly downgrade itself to whatever is in-transport: the correct row is one hop
away and the hop costs a bespoke script every time. This makes the hop cost what an in-harness
fan-out costs. `model_registry.py for-role <tier>` names the row; this dispatches to it.

WHAT IT IS NOT. It writes no ledger and needs no reader change: every launcher's `-R` already
appends to the shared spend ledger, and `tools/orchestration_metrics.py collect_sidecar()` already
reads it keyed on `-l`. Passing `-R` and `-l` per job is the whole attribution story -- which is why
`-l` is REQUIRED here: an unlabelled row is skipped by that reader, so a job without one is spend
you cannot account for.

IT NEVER AUTO-RETRIES A BILLED CALL. Memory admission waits before each launch; exit 12
means nothing ran, so it waits and retries. Exit 5, 6, 8 or 10 from a detached launcher's OWN call (the
launch, before it spawned), or from its detached job with no `-P` stream written, likewise means nothing ran, so a job that named no plan moves to the next plan
with room. Exit 10 that arrives later, from the running job, is a usage limit hit mid-run: work was done
and the session lives in that plan's login, so no other plan can resume it. A job that named no plan CONTINUES:
the tool relaunches it as a fresh session on a plan that holds it (`Waves`), under the same label and inventory
row with a new attempt record, the stopped run's `-P` stream kept as `<label>.attemptN.salvage.jsonl`, and a `-C`
brief (`<label>.continuation.md`) telling the child to read its brief, keep its workdir's commits and uncommitted
edits, read its progress report `<label>.md` and return to the same paths. Each plan that stops a job is spent for
the run, so a job is continued at most once per plan. A job whose `plan` was named, or one no plan holds or whose
new plan refuses it before spawn, is reported, and the tool writes `<out-dir>/resume-jobs.json` (the stopped job
pinned to its plan with `-r <session>`, plus the jobs its chain lane never ran) and prints the one command that
runs it after the reset. Other failed runs are REPORTED -- with their resume id when the launcher persisted one --
and the caller decides.

THE PICK COUNTS LOAD; FORECASTS PACE WAVES. Among plans that hold this job's expected drain
(`provider_bands.job_drain`) plus live launchers and earlier picks, `provider_bands.pick_key` ranks.
If none holds the forecast, the plan with the most positive headroom pays; a named plan stays named.
Only observed lack of room refuses. An unreadable reading keeps the launcher's own gate.
A job names ONE row (`alias` + `effort`) or SEVERAL (`rows: {alias: effort}`, every row that meets its bar; which
rows qualify is the caller's judgment). Several rows rank the union of their routes' plans in the one ranking above,
and the cheapest listed row (the ladder `cost` cell, compared on one plan only) runs on the winner with its own
effort. A move or a launch fallback switches alias and effort to the next plan's cheapest listed row.

EACH PLAN PACES ITSELF, AND LEARNS FROM WHAT ITS JOBS DREW (`Waves`). A plan prices a job per window, since points of
two windows are different units: each window's headroom is held against that window's own history
(`provider_bands.job_drain`), and the plan holds the job only while every window does. Every readable plan is paced,
whatever its jobs' summed forecast, because a forecast is wrong by what nothing has measured yet. Every job end and
launch decision fresh-reads the plan: a job launches while each window holds it beside the jobs still running (at least
one job per plan), `--max-parallel` still bounds the total, and once a job has ended and a window's reading has moved,
the observed drain per job replaces that window's forecast for the plan's remaining jobs (scaled by each job's own
forecast). A job its plan cannot hold moves to a route sibling that holds it now, else waits for a running job to end.
A plan that reads spent, or whose job ended in a usage limit, moves a job that named no plan to a route sibling with
room, else leaves it `not run` and off the inventory (a chain lane's successors with it).

USAGE
    python3 .claude/tools/sidecar_fanout.py <jobs.json> [--max-parallel N] [--dry-run] [--detach]
                                            [--out-dir DIR] [--authorize] [--review]
                                            [[--fold <template>] --fold-alias <alias>
                                             --fold-effort <rung>
                                             --fold-subject <root>::<manifest>::<file>[,<file>...]
                                             [--fold-subject ...]]
                                            [--ledger-stage S --ledger-subject X --ledger-roster R
                                             [--ledger-scope-json J] [--ledger-task T] [--no-new-decision]
                                             [--ledger-premise-commits C --ledger-head H --ledger-blobs B]]
    python3 .claude/tools/sidecar_fanout.py <jobs.json> --out-dir <finished-run> --refold
                                            --fold-alias <alias> --fold-effort <rung>
                                            --fold-subject <root>::<manifest>::<file>[,<file>...]
                                            [--fold-subject ...] [--fold-plan PLAN] [--detach] [--ledger-* ...]
    python3 .claude/tools/sidecar_fanout.py --review-artifact <review.json> --select <F-or-M-id>
    python3 .claude/tools/sidecar_fanout.py --status <out-dir>

JOBS FILE -- a JSON list of jobs, or `workflows/dispatch.js`'s args object `{jobs, contextPath?,
spillDir?}`, or `workflows/dispatch_chains.js`'s `{chains: [{name, jobs}], contextPath?, spillDir?}`, so one
file serves either route. Chains run as dispatch_chains.js runs them: lanes in parallel, each lane's jobs in
sequence, each later job told its position and predecessors by a `-C` file; a job that fails (or exits 0
blocked) ends its lane, whose later jobs are not run, and the other lanes go on. Chains do not combine with
--review. The object's `contextPath` is every job's first `-C`;
its `spillDir` is the default --out-dir, and an --out-dir naming another directory refuses. A job
may use dispatch.js's names `promptPath`, `model` and `contextPath` for `promptFile`, `alias` and
`contextFiles`; a job carrying both names of one field with different values refuses. dispatch.js
fields this route has no use for (`agentType`, `readOnly`) are ignored. Per job:
    label       required  attribution key; becomes `-l` and names the output files
    mandates              required by --review; catalog or lens keys the seat reports in checked.mandates
                          (findings|clean|not-reached per key; per-key checked.basis text is the fallback)
    mustRead              --review: the subject files the seat must read (each must exist under the
                          job's workdir, else the root, or the launch refuses before any seat runs), `{"path", "lines"}` entries
                          (`lens_briefs.py must-read`); an entry whose lines its stream's Read calls do not
                          cover and no legal gap names fails the seat (commands/agents/review_agents.md
                          *Seat coverage contract*)
    alias       required  registry alias or id, e.g. "muse", "opus" (or `rows`, below)
    rows                  {alias: effort}: every row that meets the job's bar, in place of `alias` + `effort`
                          (both given refuses). Each row is validated as `alias` is, its tier claim included
    promptFile  required  path to the prompt; NEVER argv (Windows caps argv near 8K)
    effort                vendor rung -> `-e`; refused unless the transport's vocabulary serves it. Also refused
                          when the row's registry `effort.avoid` lists it, unless this run is --compare (SC_COMPARISON)
    disclosure            bare|pointer|full -> `-D`
    shape                 any|survey|review|author|arm -> `-G`
    tier                  the work's tier -> `-Q`: the pin must serve it. Omitted, the `shape` row's tier
                          (`guards/tool_profiles.json`) applies; an unknown tier refuses with the known list
    window                "full" -> the job compacts at its model's own window, not the login's cap
                          (`SIDECAR_FULL_WINDOW`); any other value refuses
    tools                 {add:[], remove:[]} -> a signed `-t` (`+Bash,-mcp:ai-worker`); items are
                          built-in names or `mcp:<server>`
    toolsCsv              an absolute legacy `-t` list; cannot be combined with `tools`
    baseBuiltins          the `arm` shape's task row -> `-B`; required when an arm job carries a
                          `tools` delta, which cannot share that arm's unsigned `-t` CSV
    contextFiles          list of paths -> repeated `-C`
    schemaFile            path -> `-S`
    workdir               -> `-d`
    longContext           true -> `-M` (codex only): the max window for the WHOLE run, long-context billing;
                          only for a job that resumes (-r) a run already past the default window
    transport            must agree with the alias's own registry row (its route)
    plan                  the paying plan: the route or a live plan whose `planOf` is the route, run
                          through its own `launcher`; it never moves, and a usage limit that stops the job is reported
                          with the command that resumes it after that plan's reset. Omitted, the plan `provider_bands.pick_key` ranks first among
                          those that hold the forecast pays; if none fits, most positive headroom pays
    pick, spec            both -> `-Y <pick>,<spec>`, the pin basis (`orchestration` reference `slice_pins.md`)
    extraArgs             list, appended verbatim, for a flag this wrapper does not model
    wakeWith              a group name -> the job's inventory row. The session watcher wakes the orchestrator
                          when a job ends cleanly unless another job of the same group in this fan-out still
                          runs; `reference/sidecar_dispatch.md` step 6 says when to group.
                          A job with no group wakes the session on its own end. A failure wakes at once

CITER COVERAGE. Process guard, advisory until plan C's live proof permits promotion.
Beside tool-profile brief validation, citer_coverage validates meta subjects' current
sweeps without producing them. plan_check.md Phase 2 owns the materialization step.

RESULT -- a job that wrote its report to `<out-dir>/<label>.md` during the run keeps it as the delivery,
and a differing final message is set aside as `<label>.reply.md` (the result's `reply`). Any other job
that ends has its final message (the stdout `result` event its `-R` record summarizes) written to
`<label>.md`, named as dispatch.js names a spill file, so a child needs no Write tool to return a full
deliverable; the result's `deliveryFlag` warns when that message answered a later turn. `tools/dispatch_result.py`
owns the extraction.

CONCURRENCY -- more than one flat job at --max-parallel above 1 hands every child the single-flight
guard dispatch.js injects, as a `-C` file rendered from `guards/single_flight.json` (the shell or
no-shell wording by the job's resolved built-ins). A chain lane instead gets dispatch_chains.js's lane
guard: it owns its lane (a chain's `exclusiveResource`, such as its worktree, so it may build and run its
own tests), and only the object's `sharedSingleFlight` list is barred.

FOLD (--review with --fold-alias, --fold-effort and --fold-subject; --fold names another brief than
.claude/commands/agents/review_fold.md) routes each review view row to exactly one --fold-subject (the subject whose
files hold the row's cited file), writes its findings and quote verdicts, and launches one `author` job
per subject that received rows, its workdir the subject's root. The subject's snapshot manifest
(`post_check.py snapshot`, taken before the run) names the pre-fold bytes; this tool copies no file.
`--plan` is retired. `--refold` reads a complete saved `<out-dir>/review.json` and runs only the fold
and post-check, with the same fold and ledger flags. It writes under `<out-dir>/refold`, then
`refold.attempt2`, etc.; every earlier artifact stays in place, including failed digests and author files.
The jobs file supplies paying-plan defaults, not seats to dispatch.

REVIEW LAUNCH CHECKS. Before any seat runs, --review defaults the claims and quote root to the one
workdir every job names (an explicit --root wins; mixed workdirs without --root refuse), and refuses,
naming every bad entry at once: a --ledger-roster mandate that is not a lens-registry mandate id, a
--ledger-blobs entry that is not a blob object of the root (a gitlink or a missing object), and a
`mustRead` or context path that does not exist (`review_result.launch_problems`). A blob under a submodule
is checked in the submodule's store. --ledger-* flags without --ledger-blobs, --ledger-scope-json or
--fold-subject refuse too: the post-check would find no reviewed file.

POST-CHECK. Every --review run, folded or not, ends by calling `post_check.review` in-process and
writing `<out-dir>/post_check.json`: quote verdicts over every finding, the fold checks, the ledger rows
`--ledger-*` name and the next round's seats. A folded run also writes `fold_digest.json` with one
disposition per source F-id; the check's failures, never the author's exit or digest, decide the fold.

REFUSALS IN YOUR OWN CALL. These refusals a job's launcher would make from the job row alone happen here,
before anything spawns or detaches, in `--dry-run` too: an unknown alias or shape, an unavailable row, an effort
outside the transport's vocabulary or in the row's registry `effort.avoid` (each row of `rows` checked alike), a plan
that does not pay for the route (of any listed row), `rows` beside `alias` or `effort`, and a pin outside the job's tier (`tier`, else its `shape` row's in `guards/tool_profiles.json`; the refusal text is the
launcher's, printed to stdout, naming every unfit job). Every exit before an inventory exists also writes its
text, exit code and UTC time to `<out-dir>/launch-refused.txt`, which a launch that writes an inventory removes.
A `--detach` run returns only once each first-wave job is printed `admitted`, `refused ... nothing ran`, `queued` or `undecided` (a `<label>.stream` written since the launch marks a job past every gate), and a child's capacity refusal moves the job to its next plan. Not decided here, because only the shell launcher owns them: a `disclosure`
outside bare|pointer|full and `longContext` on a row with no long-context step (`sidecar_common.sh`).

EXIT: 0 every job exited 0; under --review, coverage is complete and the post-check passed.
      1 a job failed, review coverage is partial (each failed seat prints an `uncovered:` line naming
        its unread entries; it is never re-dispatched), or the post-check failed.
      2 jobs, arguments or source IDs are unusable, or a launcher would refuse a job (above).
      13 a live launcher or seat child already runs this job (its prompt file under its label or record) or
         resumes its session; nothing was dispatched, and stderr names each PID with the reap command that stops it.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import NamedTuple
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent          # .claude/
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "tools"))
import model_registry  # noqa: E402
import provider_bands  # noqa: E402  -- owns a plan's room; a job naming no plan pays where it is largest
import sidecar_argv  # noqa: E402  -- owns the out-dir rule the launch hook reads too
import sidecar_launch  # noqa: E402  -- owns the shell choice; see plan_job's argv[0]
from _supervised_launch import launch as supervised_launch, MEMORY_REFUSED
import sidecar_resume_check as stream_check  # noqa: E402  -- owns stream-event classification
import tool_profiles  # noqa: E402  -- the one defaults table, resolver and `tools` parser
import citer_coverage
import review_result  # noqa: E402  -- source IDs, coverage and quote verdicts
import fold_check  # noqa: E402  -- the shared post-fold checker; its verdict decides the fold
import post_check  # noqa: E402  -- closes every --review run: verdicts, fold checks, ledger rows, next seats
import finding_bar  # noqa: E402  -- the run's bar line for the fold prompt
import dispatch_result  # noqa: E402  -- one `<label>.md` writer for both dispatch routes
import sidecar_completion  # noqa: E402 -- shared source keys and fail-closed verdict application

GUARD_SOURCE = ROOT / "guards" / "single_flight.json"

# A PROCESS cap, chosen here rather than read from the registry's `limits.concurrency`: that field
# is a vendor API/token ceiling (or null), not a count of child CLIs. The default rests on one
# 7-wide codex fan-out that died 403 at a single instant, cause unverified
# (`gotcha_luna_sidecar_concurrency_cap`). That is a hypothesis, not a measurement, so the number
# is a flag rather than a rule.
DEFAULT_MAX_PARALLEL = 4

# The review deliverable's shape, shipped so `-S` needs no caller decision. Structurally identical
# to `review_fanout.js` FINDINGS_SCHEMA: both engines return one object, so a consumer never has to
# know which produced it.
DEFAULT_REVIEW_SCHEMA = ROOT / "schemas" / "review_findings.json"

# Exit codes the launchers share (sidecar_common.sh header). Reported by name, never renumbered.
# 9 is retired with the stall kill; a silent run writes `<record>.stall` and keeps running.
LAUNCHER_EXITS = {
    0: "ok",
    1: "the child CLI failed or stopped early",
    2: "bad usage, unresolvable model, or unusable registry",
    3: "credential missing",
    4: "child CLI missing",
    5: "band gate refusal (pass --authorize to override)",
    6: "balance floor unmet (NOT overridable)",
    7: "model unavailable in the registry",
    8: "provider capacity/band-reading failure (NOT overridable; repair the capacity check)",
    10: "capacity admission refused before spawn (dispatch floor, spent allowance or usage-limit marker); "
        "after spawn, provider usage limit (NOT overridable; wait for reset)",
    11: "peak pricing window refused (-W only on the user's word)",
    12: "launch refused for Windows memory headroom; nothing started (NOT overridable)",
    13: "duplicate launch: a live launcher or seat child already runs this job (this prompt under the "
        "same label or record) or resumes this session; stop it first (NOT overridable)",
    15: "re-pay refused: the spend ledger holds a finished row for this work, or cannot be read; nothing started "
        "(finished work repeats under its own label, an unreadable ledger is repaired first; NOT overridable)",
}
DUPLICATE_LAUNCH_EXIT = 13
USAGE_LIMIT_EXIT = 10
# A detached launch that exits one of these from its own call refused before it spawned (band gate, balance
# floor, capacity read, dispatch floor, spent allowance or usage-limit marker): nothing ran and nothing was billed.
PLAN_REFUSAL_EXITS = frozenset({5, 6, 8, USAGE_LIMIT_EXIT})

# How often `_wait_detached` re-checks a detached job's `<record>.exit`. No timeout gates this
# loop (Design §3, sidecar-detach.md: "The fan-out has no timeout today, so it gets none") -- only
# the job's own exit file, or its detached pid dying with none written, ends the wait. Overridable
# so a proof can poll fast without the real interval flaking a multi-second fake job's timing.
POLL_INTERVAL_SEC = float(os.environ.get("SIDECAR_FANOUT_POLL_SEC", "0.5"))


# The launcher refuses any other -Y (lib/sidecar_common.sh); checking here refuses it at plan time,
# --dry-run included, instead of at each launch.
PIN_PICKS = ("cell", "room", "owner", "default")
PIN_SPECS = ("tight", "judged")


class JobError(Exception):
    """A job that cannot be dispatched. Raised before anything is spent."""


# Which paths a comparison's freeze must actually cover. Engine-generated churn outside these
# (engine `.import` sidecars, asset caches) is expected in any fresh worktree and is not a thaw.
FROZEN_PATHS = (".claude",)


def frozen_input_error(workdir):
    """None if `workdir` is a frozen input, else why it is not.

    A COMPARISON run's input is a control. If it moves between arms, a later arm reading an applied
    fix reports the defect ABSENT -- byte-identical to a genuine miss -- so the comparison scores the
    orchestrator's own edit as that model's blind spot. Nothing downstream can detect this: the run
    record's `harnessSession` sha does not move for uncommitted edits, so every arm reports the same
    provenance while having read different bytes (`orchestration` section 0).

    A LINKED worktree at a DETACHED head is the structural answer: edits to the main checkout cannot
    reach it, so the freeze does not depend on anyone remembering the rule.
    """
    if not workdir:
        return ("no `workdir` -- a comparison arm must read a frozen worktree, not the live tree")
    if not os.path.isdir(workdir):
        return f"workdir does not exist: {workdir}"

    def git(*a):
        r = subprocess.run(["git", "-C", workdir, *a], capture_output=True, text=True)
        return r.returncode, (r.stdout or "").strip()

    rc, gitdir = git("rev-parse", "--absolute-git-dir")
    if rc != 0:
        return f"workdir is not a git tree: {workdir}"
    if "worktrees" not in gitdir.replace("\\", "/").split("/"):
        return (f"workdir is the MAIN checkout, which you can edit mid-run. Use a linked worktree: "
                f"git worktree add --detach <path> <commit>")
    if git("symbolic-ref", "-q", "HEAD")[0] == 0:
        return ("worktree HEAD is on a BRANCH -- a commit or checkout moves it under the arms. "
                "Re-create it with `git worktree add --detach`.")
    # Dirt in the COMPARED INPUT only. A fresh worktree is never globally clean: the engine rewrites
    # every `.import` sidecar on first open (`gotcha_fresh_worktree_import_cache_mass_false_red`),
    # and refusing on that would make the guard fire on every correct setup -- a guard that matches
    # the noun instead of the action, which gets disabled rather than obeyed.
    rc, dirty = git("status", "--porcelain", "--", *FROZEN_PATHS)
    # `rc != 0` is UNKNOWN, not clean. Treating a failed status as frozen made the one command that
    # can detect a thaw fail open: the arms would run, the comparison would look controlled, and
    # nothing downstream can tell that afterwards.
    if rc != 0:
        return ("cannot read the worktree's status, so the input cannot be shown frozen: "
                "`git -C %s status --porcelain` failed" % workdir)
    if dirty:
        listed = "\n  ".join(dirty.splitlines()[:5])
        return ("the worktree's compared input is dirty, so it is not frozen:\n  " + listed
                + "\n  (checked: " + ", ".join(FROZEN_PATHS) + ")")
    return None


def load_jobs(path):
    """-> (jobs, shared contextPath or None, spillDir or None) from a list or dispatch.js args."""
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except Exception as err:
        raise JobError(f"cannot read jobs file {path}: {err}")
    return parse_jobs(spec, path)


def parse_jobs(spec, where):
    """load_jobs over an already-parsed spec; `where` names its source in an error."""
    shared = spill = None
    if isinstance(spec, dict):
        shared, spill = spec.get("contextPath"), spec.get("spillDir")
        for name, value in (("contextPath", shared), ("spillDir", spill)):
            if value is not None and not (isinstance(value, str) and value.strip()):
                raise JobError(f"{where}: `{name}` must be a non-empty string")
        if "chains" in spec:
            if "jobs" in spec:
                raise JobError(f"{where}: give `jobs` or `chains`, not both")
            single = spec.get("sharedSingleFlight") or []
            if not (isinstance(single, list) and all(isinstance(s, str) and s.strip() for s in single)):
                raise JobError(f"{where}: `sharedSingleFlight` must be a list of non-empty strings")
            spec = [dict(j, _shared_single_flight=single) for j in _chain_jobs(spec["chains"], where)]
        else:
            spec = spec.get("jobs")
    if not isinstance(spec, list) or not spec:
        raise JobError(f"{where} must hold a non-empty JSON list of jobs, or an object whose `jobs` is one")
    return spec, shared, spill


def _chain_jobs(chains, where):
    """The jobs of `chains`, flattened in lane order; a malformed lane refuses."""
    if not isinstance(chains, list) or not chains:
        raise JobError(f"{where}: `chains` must be a non-empty list of {{name, jobs}}")
    flat, names = [], set()
    for chain in chains:
        name = chain.get("name") if isinstance(chain, dict) else None
        jobs = chain.get("jobs") if isinstance(chain, dict) else None
        if not (isinstance(name, str) and name.strip()) or not isinstance(jobs, list) or not jobs \
                or not all(isinstance(j, dict) for j in jobs):
            raise JobError(f"{where}: every chain needs {{name, jobs: [job objects]}} with at least one job")
        if name in names:
            raise JobError(f"{where}: two chains are named {name!r}; a lane's name is its identity")
        names.add(name)
        exclusive = chain.get("exclusiveResource")
        flat += [dict(j, _lane=name, **({"_exclusive": exclusive} if exclusive else {})) for j in jobs]
    return flat


def wake_groups(jobs):
    """{label: group} for the jobs that name a `wakeWith` group; a blank or non-string group refuses."""
    groups = {}
    for job in jobs:
        if isinstance(job, dict) and "wakeWith" in job:
            group = job["wakeWith"]
            if not (isinstance(group, str) and group.strip()):
                raise JobError(f"job {job.get('label') or '<unlabelled>'}: `wakeWith` must be a non-empty string")
            groups[job.get("label")] = group.strip()
    return groups


def job_lanes(jobs):
    """[(name, [label, ...])] in lane order for jobs `parse_jobs` flattened from `chains`, else None."""
    lanes = {}
    for job in jobs:
        if isinstance(job, dict) and "_lane" in job:
            lanes.setdefault(job["_lane"], []).append(job.get("label"))
    return list(lanes.items()) or None


# dispatch.js's name for a field -> this tool's name. One job spec serves either route.
DISPATCH_FIELDS = (("promptPath", "promptFile"), ("model", "alias"))


def _bare_model(name):
    """`name` without a legal context suffix (`claude-sonnet-5-5[1m]` -> `claude-sonnet-5-5`). An unknown
    suffix stays, so the registry lookup refuses the whole spelling."""
    if isinstance(name, str):
        for suffix in model_registry._LEGAL_CONTEXT_SUFFIXES:
            if name.lower().endswith(suffix) and len(name) > len(suffix):
                return name[:-len(suffix)]
    return name


def _same_model(a, b, data):
    if a == b:
        return True
    try:
        return (model_registry.resolve(_bare_model(a), data)["id"]
                == model_registry.resolve(_bare_model(b), data)["id"])
    except Exception:
        return False


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def normalize_job(job, data=None, shared_context=None):
    """A job in this tool's field names. A dispatch.js name is renamed; one that disagrees with the
    native name beside it refuses, because no rule could say which of the two the caller meant."""
    if not isinstance(job, dict):
        return job
    label = job.get("label") or "<unlabelled>"
    out = dict(job)
    for theirs, ours in DISPATCH_FIELDS:
        if theirs not in out:
            continue
        value = out.pop(theirs)
        if not isinstance(value, str) or not value.strip():
            raise JobError(f"job {label}: dispatch.js field `{theirs}` must be a non-empty string")
        mine = out.get(ours)
        same = _same_model if ours == "alias" else (lambda a, b, _d: _same_path(a, b))
        if mine is not None and not (isinstance(mine, str) and same(mine, value, data)):
            raise JobError(f"job {label}: `{ours}` {mine!r} and `{theirs}` {value!r} name different "
                           f"values. Keep one of the two fields.")
        out[ours] = mine if mine is not None else value
    if "contextPath" in out:
        value = out.pop("contextPath")
        if not isinstance(value, str) or not value.strip():
            raise JobError(f"job {label}: dispatch.js field `contextPath` must be a non-empty string")
        mine = out.get("contextFiles")
        if mine is not None and not (isinstance(mine, list) and len(mine) == 1
                                     and isinstance(mine[0], str) and _same_path(mine[0], value)):
            raise JobError(f"job {label}: `contextFiles` {mine!r} and `contextPath` {value!r} differ. "
                           f"Keep one of the two fields.")
        out["contextFiles"] = [value]
    if "rows" in out:
        rows = out["rows"]
        if "alias" in out or "effort" in out:
            raise JobError(f"job {label}: give `rows` or `alias` + `effort`, not both. `rows` lists every row "
                           f"that meets the job's bar as {{alias: effort}}; `alias` + `effort` is one row.")
        if not isinstance(rows, dict) or not rows or not all(
                isinstance(k, str) and k.strip() and (v is None or isinstance(v, str)) for k, v in rows.items()):
            raise JobError(f"job {label}: `rows` must be a non-empty object of {{alias: effort}}")
        out["rows"] = {_bare_model(k): v for k, v in rows.items()}
    # The launcher appends `[1m]` itself for a 1M-window row (`model_registry anthropic-window`), so the
    # bare row carries the long-context intent; the suffix would only fail the registry lookup.
    if "alias" in out:
        out["alias"] = _bare_model(out["alias"])
    if shared_context:
        mine = out.get("contextFiles") or []
        if isinstance(mine, list) and not any(isinstance(p, str) and _same_path(p, shared_context)
                                              for p in mine):
            out["contextFiles"] = [shared_context] + mine
    return out


def _posix(p):
    """argv for a BASH child, so every path is POSIX -- always, not only when it looks wrong.

    `str(Path)` on Windows yields backslashes, and bash reads a backslash as an escape: the launcher
    path collapsed to `C:UsersjmundGame_Dev...` and every job died exit 127 before the launcher ran.
    The registry stores `launcher` repo-relative already, and cwd is the repo, so nothing here needs
    an absolute path.
    """
    return str(p).replace("\\", "/")


def with_row(argv, launcher, alias=None, effort=None):
    """`argv` run through `launcher`; given an `alias`, also its `-m` and its `-e` (dropped when `effort` is None)."""
    argv = argv[:1] + [launcher] + argv[2:]
    if alias is None:
        return argv
    at = argv.index("-m") + 1
    argv = argv[:at] + [alias] + argv[at + 1:]
    if "-e" in argv[:-1]:
        at = argv.index("-e")
        return argv[:at] + (["-e", effort] if effort else []) + argv[at + 2:]
    return argv[:at + 1] + (["-e", effort] if effort else []) + argv[at + 1:]


def legal_rungs(transport, data):
    """The effort rungs `-e` may name on `transport`: its registry effort vocabulary. [] = undeclared.

    The TRANSPORT's vocabulary, not a row's: the ladder cell owns a row's rung (the registry keeps
    none), and what a launcher can actually forward is a transport fact. Checking a row's own
    narrower list would refuse a rung this launcher would have run.
    """
    return model_registry.transport_effort_values(transport, data)


def plan_room(transport, data, read, now):
    return provider_bands.plan_room(transport, data, read, now)


# label -> the plans `plan_job` left a job to fall back on; `main` clears it before planning and hands it to run_jobs.
FALLBACKS = {}
# plan -> the signatures (model id, effort, shape) of the jobs this process has placed on it so far (named or
# picked); `main` clears it with FALLBACKS.
PLACED = {}
FULL_WINDOW = set()  # labels of jobs that opt out of the login's compaction cap (`window: "full"`)
_LIVE = {}  # {"load": plan -> live launchers}: read once per process, so a 13-job pass costs one table read
# label -> the `Slot` `plan_job` placed the job in; `main` clears it with FALLBACKS and hands the planned jobs' slots to `Waves`.
SLOTS = {}


class Slot(NamedTuple):
    """Where one planned job runs: its paying `plan`, its `signature` (model id, effort, shape: what
    `provider_bands.job_drain` prices) and the plans it may move among before it starts (empty for a named
    plan, which never moves). `picks` is {plan: (alias, effort, signature)} for a job listing `rows`: the row
    that would run on each plan."""
    plan: str
    signature: tuple
    candidates: tuple
    picks: dict = None


def job_signature(job, data):
    """(model id, effort, shape) of a job: what `provider_bands.job_drain` prices. The model is None when the
    alias does not resolve, which prices the job at the default."""
    try:
        model = model_registry.resolve(job["alias"], data)["id"]
    except Exception:
        model = None
    return model, job.get("effort"), job.get("shape")


def listed_rows(job):
    """[(alias, effort)] a job lists: its `rows` in order, else its one `alias` + `effort`."""
    if job.get("rows"):
        return list(job["rows"].items())
    return [(job.get("alias"), job.get("effort"))]


def row_on_plan(job, plan, data):
    """(alias, effort) of the cheapest listed row whose route `plan` pays for (`model_registry.cheapest_first`: the
    ladder `cost` cell, a higher number cheaper), else None. Cost compares rows on one plan only."""
    paying = {}
    for alias, effort in listed_rows(job):
        try:
            entry = model_registry.resolve(alias, data)
            if plan in model_registry.plans_of(entry["transport"], data):
                paying.setdefault(entry["id"], (entry, alias, effort))
        except Exception:
            continue
    if not paying:
        return None
    first = model_registry.cheapest_first([entry for entry, _a, _e in paying.values()], data)[0]
    return paying[first["id"]][1:]


def job_on(job, plan, data):
    """`job` as one row: its `alias` and `effort` are the row `row_on_plan` runs on `plan`, and `rows` is gone."""
    row = row_on_plan(job, plan, data)
    if row is None or not job.get("rows"):
        return job
    return {**{k: v for k, v in job.items() if k != "rows"}, "alias": row[0], "effort": row[1]}


def job_routes(job, data):
    """(route text, plans): the transports of the rows a job lists, joined, and the union of their plans, deduped in
    listed order. Raises where a listed row does not resolve."""
    transports = list(dict.fromkeys(model_registry.resolve(alias, data)["transport"]
                                    for alias, _effort in listed_rows(job)))
    plans = list(dict.fromkeys(plan for t in transports for plan in model_registry.plans_of(t, data)))
    return ", ".join(transports), plans


def plan_signature(job, data):
    """What `place_job` prices a job at: `job_signature`, or for a job listing `rows` a function of the plan
    giving the signature of the row that would run there."""
    if not job.get("rows"):
        return job_signature(job, data)
    return lambda plan: job_signature(job_on(job, plan, data), data)


def live_plan_load(table, data):
    """{plan: [signature, ...]} from a process table: one signature per distinct job (its `-R` record, else its
    `-l` label and `-f` prompt) among the bash launchers, read from the launcher's `-m`, `-e` and `-G`. The plan is
    the one whose registered launcher the job was INVOKED through; a job's re-exec snapshots are named for the
    shared base script (`anthropic_sidecar.sh.N.sh` for every Anthropic plan), so they decide only a job whose
    invoked launcher is gone. A script two plans share names no plan and counts for none."""
    import reap
    data = data if data is not None else model_registry.load()
    scripts = {}
    for name, cfg in (data.get("transports") or {}).items():
        launcher = (cfg or {}).get("launcher") if isinstance(cfg, dict) else None
        if launcher:
            scripts.setdefault(os.path.basename(launcher).lower(), set()).add(name)
    invoked, snapshots = {}, {}
    for row in table.values():
        if not reap.is_launcher(row):
            continue
        # The script must be what bash RUNS (its first operand), not a word in a `-c` string that names it.
        tokens = [tok.strip('"') for tok in re.findall(r'"[^"]*"|\S+', row.get("cmd") or "")][1:]
        script = next((tok for tok in tokens if not tok.startswith("-")), "")
        found = reap.LAUNCHER_SCRIPT.fullmatch(os.path.basename(script.replace("\\", "/")).split(".sh")[0] + ".sh")
        plans = scripts.get(found.group(0).lower()) if found else None
        if not plans or len(plans) != 1:
            continue
        flags = sidecar_argv.sidecar_flags(reap.launcher_args(row.get("cmd")))
        key = flags.get("R") or (flags.get("l"), flags.get("f"))
        alias = flags.get("m")
        sig = job_signature({"alias": alias if isinstance(alias, str) else None,
                             "effort": flags.get("e") if isinstance(flags.get("e"), str) else None,
                             "shape": flags.get("G") if isinstance(flags.get("G"), str) else None}, data)
        (snapshots if "sidecar-snapshots" in script.replace("\\", "/") else invoked)[key] = (next(iter(plans)), sig)
    load = {}
    for plan, sig in {**snapshots, **invoked}.values():
        load.setdefault(plan, []).append(sig)
    return load


def plan_load(data):
    """{plan: [signature, ...]}: the live launchers (any session's: the quota is shared) plus this process's own
    placements. An unreadable process table counts no live launcher, which leaves the pick counting only this run."""
    if "load" not in _LIVE:
        import reap
        table = reap.process_table()
        if not table:
            print("[fanout] WARN: process table unreadable; the pick counts only this run's own jobs",
                  file=sys.stderr)
        _LIVE["load"] = live_plan_load(table, data)
    load = {plan: list(sigs) for plan, sigs in _LIVE["load"].items()}
    for plan, sigs in PLACED.items():
        load.setdefault(plan, []).extend(sigs)
    return load


def _holds(row, load, signature):
    """(holds, drain, committed) of one readable plan row for a job. Each window prices every job by its own history
    (`provider_bands.job_drain`), since points of two windows are different units, and the plan holds the job only
    while every window does (`provider_bands.fits`), beside the jobs `load` already puts on the plan. `drain` and
    `committed` are the binding window's, the one that holds the fewest more of this job: `drain` carries its
    `window`, `headroom` and `resetsAt`. A row that states no window holds it, and its launcher's own gate decides."""
    plan, windows = row["transport"], provider_bands.row_windows(row)
    if not windows:
        return True, provider_bands.job_drain(plan, None, *signature), 0.0
    holds, binding = True, None
    for window in windows:
        drain = provider_bands.job_drain(plan, window["name"], *signature)
        committed = sum(provider_bands.job_drain(plan, window["name"], *sig)["pct"] for sig in load.get(plan, ()))
        holds = holds and provider_bands.fits({"headroom": window["headroom"]}, committed, drain["pct"])
        more = (window["headroom"] - committed) / drain["pct"] if drain["pct"] > 0 else float("inf")
        if binding is None or more < binding[0]:
            binding = (more, dict(drain, window=window["name"], headroom=window["headroom"],
                                  resetsAt=window.get("resetsAt")), committed)
    return holds, binding[1], binding[2]


def _drain_text(drain):
    window = f" on {drain['window']}" if drain.get("window") else ""
    return f"drain {drain['pct']:.1f} {drain['basis']}{window}"


def _headroom_text(drain):
    """`headroom 96` of the window `drain` is priced on."""
    return f"headroom {drain['headroom']:.0f}" if drain.get("headroom") is not None else "headroom unread"


def _unfit_text(row, drain, committed, jobs_on, now):
    """One plan's reason for not holding a job: its binding window's headroom against what is committed and this
    job's estimate."""
    reset = drain.get("resetsAt")
    return (f"{row['transport']}: {_headroom_text(drain)} points, {jobs_on} job{'' if jobs_on == 1 else 's'} "
            f"on it commit {committed:.1f}, this job {_drain_text(drain)}"
            + (f", resets {provider_bands.reset_text(reset, now)}" if reset is not None else ""))


class Placement(NamedTuple):
    rows: list
    held: dict
    unfit: dict
    fit: list
    blind: bool
    load: dict


def place_job(candidates, data, read, now, load, signature):
    """Where a job with `signature` (a tuple, or a function of the plan for a job listing rows) may go among the
    plans `candidates`: every plan's room row, `held` (plan ->
    (holds, drain, committed)) and `unfit` (plan -> its `_unfit_text`) for the readable ones, `fit` the holding rows
    best first (`provider_bands.pick_key`), or positive-headroom rows most headroom first when none fits,
    `blind` when some plan's reading decides nothing, and the `load` used
    ({plan: [signature]}, `plan_load` when None, read only once a plan is readable). The one ranking `pick_plan`
    and `admission_refusal` share; charging the winner to a load is the caller's."""
    rows = [plan_room(name, data, read, now) for name in candidates]
    blind = any(row.get("room") is None and _decides_nothing(row) for row in rows)
    held, unfit = {}, {}
    for row in rows:
        if row.get("room") is None:
            continue
        if load is None:
            load = plan_load(data)
        name = row["transport"]
        held[name] = _holds(row, load, signature(name) if callable(signature) else signature)
        if not held[name][0]:
            unfit[name] = _unfit_text(row, held[name][1], held[name][2], len(load.get(name, ())), now)
    fit = sorted((row for row in rows if held.get(row["transport"], (False,))[0]),
                 key=provider_bands.pick_key, reverse=True)
    if not fit:
        fit = sorted((row for row in rows if row.get("headroom", 0) > 0),
                     key=lambda row: row["headroom"], reverse=True)
    return Placement(rows, held, unfit, fit, blind, load)


def pick_plan(label, transport, plans, data, read=None, rest=None, quiet=False, load=None, job=None):
    """The paying plan, or a JobError when every candidate has no room. Forecast fits rank as before;
    otherwise most positive headroom wins. A caller restricting `plans` to a named plan keeps it named.
    `load` is {plan: [signature]}, default `plan_load`; `job` is priced by `provider_bands.job_drain`.
    `read` is the capacity reader (default `provider_bands.read_capacity`); a caller with a deadline passes
    its own. A list `rest` receives other forecast fits, or other positive-headroom plans when none fits:
    the launch fallbacks. `quiet` drops the stderr note for a hook's output channel."""
    if not plans:
        return transport
    now = time.time()
    read = read or provider_bands.read_capacity
    placed = place_job(plans, data, read, now, load, plan_signature(job or {}, data))
    rows, held, fit, load = placed.rows, placed.held, placed.fit, placed.load
    if len(plans) == 1 and not held and placed.blind:
        return plans[0]
    if not held:
        raise JobError(f"job {label}: no plan of {transport} has room -- "
                       + "; ".join(f"{row['transport']}: {row.get('why')}" for row in rows)
                       + ". Name a `plan` only once one has room, or pick a row on another route.")
    if not fit:
        raise JobError(f"job {label}: no plan of {transport} holds this job -- "
                       + "; ".join(placed.unfit[row["transport"]] for row in rows if row["transport"] in held)
                       + ". Wait for the reset, or move the job to a plan with room.")
    best = fit[0]
    if rest is not None:
        rest.extend(row["transport"] for row in fit[1:])
    if not quiet:
        def clause(row):
            if row.get("room") is None:
                return f"{row['transport']} {row.get('why')}"
            ok, drain, committed = held[row["transport"]]
            if not ok:
                return f"{row['transport']} does not hold it: {_headroom_text(drain)}, {_drain_text(drain)}"
            return f"{row['transport']} {_pick_facts(row)}, {_headroom_text(drain)}, {_drain_text(drain)}"
        on = len(load.get(best["transport"], ()))
        others = ", ".join(clause(row) for row in rows if row is not best)
        ran = row_on_plan(job or {}, best["transport"], data)
        named = f", row {ran[0]}" + (f" ({ran[1]})" if ran[1] else "") if ran and ran[0] else ""
        drain = held[best["transport"]][1]
        print(f"[fanout] {label}: plan {best['transport']}{named} ({_pick_facts(best)}, {_headroom_text(drain)}, "
              f"{_drain_text(drain)}" + (f", {on} jobs on it" if on else "")
              + (f"; {others}" if others else "") + ")", file=sys.stderr)
    return best["transport"]


def _decides_nothing(row):
    """True for a plan row whose reading could not be taken, so its launcher's own gate decides."""
    why = row.get("why") or ""
    return not why or why.startswith("unreadable")


def _pick_facts(row):
    """`room R`, led by `~N% unused at reset in Dd` when the plan's projection reaches whole percents."""
    pct = provider_bands.spare_pct(row)
    days = (row.get("unusedAtReset") or {}).get("daysLeft")
    room = f"room {row['room']:.2f}"
    if pct is not None and isinstance(days, (int, float)):
        return f"~{pct}% unused at reset in {days:.1f}d, {room}"
    return room


def admission_refusal(jobs, data, read=None, now=None, load=None):
    """Refuse only observed lack of room, never a forecast-only shortfall; else return None.

    A named plan stays named. Other jobs use forecast-fitting plans first, or the most positive headroom
    when none fits, matching `pick_plan`. `load` is {plan: [signature]} (default `plan_load`); admitted jobs
    add their signatures so a file counts its summed forecast. Pacing what the forecast cannot hold is `Waves`'s.
    Stale, missing or unreadable readings decide nothing: the launcher's own gate still runs.
    `read` defaults to `provider_bands.read_capacity`; the PreToolUse hook passes a cache-only reader.
    One message groups every refused job by reason."""
    read = read or provider_bands.read_capacity
    now = time.time() if now is None else now
    seen = {}
    counted = None if load is None else {plan: list(sigs) for plan, sigs in load.items()}

    def reader(transport, table):
        seen[transport] = read(transport, table)
        return seen[transport]

    groups = {}
    for job in jobs:
        try:
            transport, plans = job_routes(normalize_job(job, data), data)
        except Exception:
            continue
        named = job.get("plan")
        candidates = [named] if named else plans
        if not candidates or any(name not in plans for name in candidates):
            continue
        signature = plan_signature(normalize_job(job, data), data)
        placed = place_job(candidates, data, reader, now, counted, signature)
        if placed.blind and not placed.fit:
            continue
        counted = placed.load
        if placed.fit:
            won = placed.fit[0]["transport"]
            counted.setdefault(won, []).append(signature(won) if callable(signature) else signature)
            continue
        room_left = bool(placed.held)
        states = []
        for row in placed.rows:
            name = row["transport"]
            if name in placed.unfit:
                states.append(placed.unfit[name])
                continue
            got = seen.get(name)
            entry = provider_bands.plan_entry(name, got, now) if isinstance(got, dict) else ""
            states.append(entry if entry and "band unknown" not in entry else f"{name}: {row['why']}")
        if not room_left:
            reason = (f"plan {named} has no room -- {states[0]}" if named
                      else f"no plan of {transport} has room -- " + "; ".join(states))
        else:
            reason = (f"plan {named} cannot hold the job -- {states[0]}" if named
                      else f"no plan of {transport} holds the job -- " + "; ".join(states))
        groups.setdefault(reason, []).append(str(job.get("label")))
    if not groups:
        return None
    lines = [f"  {'job' if len(labels) == 1 else 'jobs'} {', '.join(labels)}: {reason}"
             for reason, labels in groups.items()]
    return ("capacity admission refused, nothing started:\n" + "\n".join(lines)
            + "\nWait for the reset, or move the refused jobs to a plan with room.")


IN_FLIGHT_SHARE = 0.5  # a job still running is taken to have drawn half of what it will


class Verdict(NamedTuple):
    """`Waves.decide`'s answer: `launch`, `wait` (a job of the plan is running; its end re-decides) or `stop`
    (`error` is the job's `not run` text)."""
    kind: str
    error: str = None


LAUNCH = Verdict("launch")


class _Track:
    """One window of a paced plan: `first` its headroom at the plan's first launch, `head` the latest read, `observed`
    the points a job has drawn from it (None until a job ended and the reading moved)."""

    def __init__(self, head):
        self.first, self.head, self.observed = None, head, None


class _PlanWave:
    """One plan's pace in a fan-out: `tracks` {window: _Track}, `flying` {label: forecast} of its running jobs and
    `ended` the forecasts of the jobs that ran. A forecast is {window: points} (`Waves.forecasts`): points of two
    windows are different units, so each window is tracked, priced and held on its own."""

    def __init__(self):
        self.tracks, self.flying, self.ended = {}, {}, []

    def mean(self, window):
        """The mean points the jobs that ran were forecast to draw from `window`; None when none was."""
        seen = [forecast[window] for forecast in self.ended if window in forecast]
        return sum(seen) / len(seen) if seen else None

    def price(self, window, forecast):
        """What a job forecast at `forecast` points of `window` is expected to draw: the forecast until a drain is
        observed there, then the observed drain per job scaled by the job's forecast against the mean forecast of the
        jobs that ran."""
        observed, mean = self.tracks[window].observed, self.mean(window)
        if observed is None or mean is None:
            return forecast
        return observed * forecast / mean if mean > 0 else observed

    def reserve(self):
        """{window: points} the running jobs are expected to draw."""
        return {window: sum(self.price(window, forecast[window]) for forecast in self.flying.values()
                            if window in forecast) for window in self.tracks}

    def holds(self, forecast, reserve):
        """True while every window's latest headroom, less `reserve`, covers the price of a job forecast at `forecast`."""
        return all(track.head - reserve[window] >= self.price(window, forecast[window])
                   for window, track in self.tracks.items())

    def binding(self, forecast, reserve):
        """The window that holds the fewest more jobs like `forecast` beside `reserve`, or None with no window."""
        def more(window):
            price = self.price(window, forecast[window])
            return (self.tracks[window].head - reserve[window]) / price if price > 0 else float("inf")
        return min(self.tracks, key=more, default=None)


class Waves:
    """Per-plan pacing of one fan-out's jobs, from `slots` ({label: Slot}, planned order). Callers hold one lock around
    every method; `read(plan, data)` is the capacity reader and `say(line)` prints (default stderr).

    Every plan whose reading states a window is paced, whatever its jobs' summed forecast (`provider_bands.job_drain`,
    priced per window): a forecast is wrong by what nothing has measured yet, and only a reading taken while jobs run
    sees it. `decide(label)` fresh-reads the plan and answers for a job: launch while every window's headroom, less
    what its running jobs are expected to draw, holds this job's drain (a plan with nothing running always takes one
    job), else move it to a route sibling that holds it now (a job with a named `plan` never moves), else wait for a
    running job to end. `ended(label, result)` reads again and, once a job on the plan has ended and a window's
    reading has moved, replaces that window's forecast with the observed drain per job. A plan that reads spent, or
    hit a usage limit, moves a job whose plan was not named to a route sibling with room, else stops it with its
    `not run` error; `requeue(label)` puts a job a usage limit stopped mid-run back through `decide`. An unreadable
    reading launches: the launcher's own gate decides."""

    def __init__(self, slots, data, read, say=None):
        self.slots, self.data, self.states, self.limited = dict(slots), data, {}, {}
        self.queued = list(self.slots)
        self._read, self._reads = read, {}
        self._say = say or (lambda line: print(line, file=sys.stderr, flush=True))

    def round(self):
        """Forget this round's readings: the next decision reads fresh."""
        self._reads.clear()

    def plan_of(self, label):
        return self.slots[label].plan

    def row_of(self, label):
        """(alias, effort) the job runs on its current plan when it lists `rows`, else None."""
        slot = self.slots[label]
        pick = (slot.picks or {}).get(slot.plan)
        return pick[:2] if pick else None

    def forecasts(self, plan, signature):
        """{window: points} a job of `signature` is forecast to draw from each window of `plan` read so far."""
        return {window: provider_bands.job_drain(plan, window, *signature)["pct"]
                for window in self.states[plan].tracks}

    def price(self, label):
        """The points a queued job is expected to draw now on its plan, from its binding window (a paced plan)."""
        slot = self.slots[label]
        state = self.states[slot.plan]
        forecast = self.forecasts(slot.plan, slot.signature)
        window = state.binding(forecast, state.reserve())
        return state.price(window, forecast[window]) if window else provider_bands.job_drain(
            slot.plan, None, *slot.signature)["pct"]

    def _reader(self, plan, data=None):
        if plan not in self._reads:
            try:
                self._reads[plan] = self._read(plan, self.data)
            except Exception as exc:
                self._reads[plan] = exc
        got = self._reads[plan]
        if isinstance(got, Exception):
            raise got
        return got

    def _row(self, plan):
        return plan_room(plan, self.data, self._reader, time.time())

    def _spent(self, plan, row):
        return (plan in self.limited or row.get("headroom", 1) <= 0
                or str(row.get("why") or "").startswith("exhausted"))

    def _reset(self, plan):
        got = self._reads.get(plan)
        epoch = provider_bands.spent_reset(got if isinstance(got, dict) else None)
        return provider_bands.reset_text(epoch, time.time()) if epoch is not None else "the plan resets"

    def _take(self, state, row):
        """Bring `state`'s windows to the reading `row`: a window read for the first time gets a track."""
        for window in provider_bands.row_windows(row):
            track = state.tracks.setdefault(window["name"], _Track(window["headroom"]))
            track.head = window["headroom"]

    def _count(self, plan, state):
        """How many of the plan's queued jobs would launch now."""
        if not state.tracks or min(track.head for track in state.tracks.values()) <= 0:
            return 0
        reserve = state.reserve()
        count = 0
        for label in self.queued:
            slot = self.slots[label]
            if slot.plan != plan:
                continue
            forecast = self.forecasts(plan, slot.signature)
            if (count or state.flying) and not state.holds(forecast, reserve):
                break
            count += 1
            for window in reserve:
                reserve[window] += state.price(window, forecast[window])
        return count

    def pace(self):
        """Read each plan the fan-out uses and pace it: one stderr line each, naming the plan's binding window. A plan
        that reads no window and is not spent is left to its launcher's gate."""
        self.round()
        for plan in dict.fromkeys(slot.plan for slot in self.slots.values()):
            labels = [label for label, slot in self.slots.items() if slot.plan == plan]
            row = self._row(plan)
            spent = self._spent(plan, row)
            if not provider_bands.row_windows(row) and not spent:
                continue
            state = self.states[plan] = _PlanWave()
            self._take(state, row)
            for track in state.tracks.values():
                track.head = max(track.head, 0)
            forecasts = [self.forecasts(plan, self.slots[label].signature) for label in labels]
            totals = {window: sum(forecast[window] for forecast in forecasts) for window in state.tracks}
            window = min(totals, key=lambda w: state.tracks[w].head / totals[w] if totals[w] > 0 else float("inf"),
                         default=None)
            head = state.tracks[window].head if window else 0
            total = totals[window] if window else sum(
                provider_bands.job_drain(plan, None, *self.slots[label].signature)["pct"] for label in labels)
            self._say(f"[fanout] wave {plan}: {len(labels)} jobs, headroom {head:.0f}, summed forecast "
                      f"{total:.1f}, first wave {self._count(plan, state)}" + (f" on {window}" if window else ""))
        self.round()

    def _observe(self, plan, state, row):
        """Take the reading `row`, and for each window a job has drawn from, replace its forecast with the observed
        drain per job."""
        self._take(state, row)
        for window, track in state.tracks.items():
            if not state.ended or track.first is None or track.head >= track.first or state.mean(window) is None:
                continue
            seen = (track.first - track.head) / (len(state.ended) + IN_FLIGHT_SHARE * len(state.flying))
            if track.observed is not None and abs(seen - track.observed) < 1e-6:
                continue
            track.observed = seen
            self._say(f"[fanout] resize {plan}: observed drain {seen:.1f}/job replaces forecast "
                      f"{state.mean(window):.1f} on {window}, next launch {self._count(plan, state)}")

    def _start(self, label, state, forecast):
        for track in state.tracks.values():
            if track.first is None:
                track.first = track.head
        state.flying[label] = forecast
        self.queued.remove(label)
        return LAUNCH

    def _move(self, label, slot, tried, holds=False):
        """Move a job with route siblings to the one `place_job` ranks first among those with room (an unreadable
        one too: its launcher decides), or with `holds`, among those that hold it now beside their running jobs;
        False when there is none."""
        candidates = [name for name in slot.candidates if name not in tried and name not in self.limited]
        if not candidates:
            return False
        load = {plan: [self.slots[running].signature for running in state.flying]
                for plan, state in self.states.items()}
        sign = (lambda name: slot.picks[name][2]) if slot.picks else slot.signature
        placed = place_job(candidates, self.data, self._reader, time.time(), load, sign)
        if holds:
            target = next((row for row in placed.fit if placed.held[row["transport"]][0]), None)
        else:
            target = placed.fit[0] if placed.fit else next(
                (row for row in placed.rows if row.get("room") is None and _decides_nothing(row)), None)
        if target is None:
            return False
        name = target["transport"]
        self.slots[label] = slot._replace(plan=name, signature=slot.picks[name][2] if slot.picks else slot.signature)
        self.states.setdefault(name, _PlanWave())
        why = "cannot hold it beside its running jobs" if holds else "has no room"
        self._say(f"[fanout] {label}: plan {slot.plan} {why}; moved to plan {name} ("
                  + (_headroom_text(placed.held[name][1]) if name in placed.held else "its launcher decides") + ")")
        return True

    def decide(self, label):
        """`launch`, `wait` or `stop` for a queued job (see the class docstring). A launch charges the job to its plan's
        running jobs; a move changes `plan_of(label)`."""
        tried = set()
        while True:
            slot = self.slots[label]
            plan = slot.plan
            state = self.states.get(plan)
            if state is None and plan not in self.limited:
                self.queued.remove(label)
                return LAUNCH
            state = self.states.setdefault(plan, _PlanWave())
            row = self._row(plan)
            if not self._spent(plan, row):
                if not provider_bands.row_windows(row):
                    return self._start(label, state, {})
                self._observe(plan, state, row)
                forecast = self.forecasts(plan, slot.signature)
                if state.holds(forecast, state.reserve()):
                    return self._start(label, state, forecast)
                tried.add(plan)
                if self._move(label, slot, tried, holds=True):
                    continue
                if not state.flying:
                    return self._start(label, state, forecast)
                return Verdict("wait")
            tried.add(plan)
            reset = self.limited.get(plan) or self._reset(plan)
            if self._move(label, slot, tried):
                continue
            self.queued.remove(label)
            return Verdict("stop", f"not run: plan {plan} has no room (read before launch); rerun after {reset}")

    def ended(self, label, result):
        """A launched job's `result` is in: a usage-limit exit marks the plan it names spent for the rest of the run,
        and a job that drew quota joins the plan's observed drain."""
        self.round()
        plan = self.slots[label].plan
        if result.get("exit") == USAGE_LIMIT_EXIT:
            hit, at = result.get("plan") or plan, result.get("limitResetsAt")
            self._row(hit)
            self.limited[hit] = (provider_bands.reset_text(at, time.time())
                                 if isinstance(at, (int, float)) and not isinstance(at, bool) else self._reset(hit))
        state = self.states.get(plan)
        if state is None or label not in state.flying:
            return
        forecast = state.flying.pop(label)
        if not result.get("preSpawnRefusal"):
            state.ended.append(forecast)
        row = self._row(plan)
        if provider_bands.row_windows(row) and not self._spent(plan, row):
            self._observe(plan, state, row)

    def requeue(self, label):
        """Queue a job again after a usage limit stopped it mid-run: `decide` then places it on a plan that holds it,
        else stops it, and the plan the limit named is spent for it."""
        self.queued.append(label)


def fanout_waves(planned, data):
    """The `Waves` of the planned jobs with its start lines printed, or None when no plan reads a window: each such
    plan's launcher gate decides alone."""
    waves = Waves({label: SLOTS[label] for label, _, _ in planned if label in SLOTS}, data,
                  lambda plan, table: provider_bands.read_capacity(plan, table, live=True))
    waves.pace()
    return waves if waves.states else None


def _dispatch_checks_module():
    sys.path.insert(0, str(ROOT / "hooks"))
    import _dispatch_checks
    return _dispatch_checks


def check_row(job, label, alias, effort, data):
    """The transport of one row a job lists (`alias` + `effort`, or an entry of `rows`), after the refusals a launcher
    would make from the row alone: an unknown or unavailable row, a `transport` that disagrees with the row's, an
    effort outside the transport's vocabulary or in the row's `effort.avoid`. A job listing `rows` also refuses a
    row outside its tier, since the PreToolUse tier check reads one `alias` per job."""
    try:
        entry = model_registry.resolve(alias, data)
    except Exception as err:
        raise JobError(f"job {label}: {err}")
    # A `transport` override selects the LAUNCHER; the alias resolves against a registry row that
    # already names its own. Pairing them freely sends one transport's alias to another's launcher,
    # which authenticates, bills, and then fails on a model it has never heard of. Keep the key --
    # a job naming its transport is self-documenting -- but let it only AGREE.
    transport = job.get("transport") or entry["transport"]
    if transport != entry["transport"]:
        raise JobError(f"job {label}: transport {transport!r} does not own {alias} -- the registry "
                       f"places it on {entry['transport']!r}, and a row's transport names the "
                       f"launcher that speaks its API. Drop the override, or pick an alias on "
                       f"{transport}.")
    # An excluded row is out of the set a dispatcher chooses from -- name the roster rather than
    # substituting one, because choosing the replacement needs the task's shape and the budget.
    if not model_registry.model_available(entry["id"], data):
        why = model_registry.unavailable_reasons(data).get(entry["id"], "excluded")
        roster = ", ".join(sorted(m["alias"] for m in model_registry.available_models(data)
                                  if m["transport"] == transport)) or "none"
        raise JobError(f"job {label}: {alias} is unavailable ({why}). "
                       f"Available on {transport}: {roster}. Re-select under the ladder.")
    legal = legal_rungs(transport, data)
    if effort and legal and effort not in legal:
        raise JobError(f"job {label}: effort {effort!r} is not in {transport}'s effort vocabulary. "
                       f"Legal rungs: {', '.join(legal)} (registry `transports.{transport}`; "
                       f"`model_registry.py effort-values {alias}` prints what this row forwards). "
                       f"Re-select under the ladder.")
    if effort in model_registry.avoided_rungs(entry) and not os.environ.get("SC_COMPARISON"):
        raise JobError(f"job {label}: effort {effort!r} is in {alias}'s registry effort.avoid: "
                       f"{model_registry.avoid_evidence(entry)} Pin the ladder's rung.")
    if job.get("rows"):
        checks = _dispatch_checks_module()
        try:
            tier, source = checks.job_tier(job.get("shape"), job.get("tier"))
        except ValueError as err:
            raise JobError(f"job {label}: {err}")
        why = checks.tier_fit(alias, tier, transport, data)
        if why:
            raise JobError(f"job {label}: row {alias} is outside tier `{tier}` (from {source}): {why}")
    return transport


def plan_job(job, data, out_dir, authorize, stage=None):
    """One job -> (label, argv, stdout path). Validates everything BEFORE any process starts.

    `FALLBACKS[label]` becomes `[(plan, launcher), ...]` for a job that names no plan: the other
    candidates `pick_plan` ranked, which `run_jobs` tries when the picked plan's launcher refuses.
    `SLOTS[label]` records the plan, signature and route plans the job may move among (none for a named plan),
    which `Waves` paces from.

    Every refusal the planning pass can see happens in this pass, capacity included (`admission_refusal`), so
    a bad job in a 10-job file costs nothing rather than nine paid runs and one error. A queued job can still
    meet changed capacity: its launcher's gate runs at launch.
    """
    if not isinstance(job, dict):
        raise JobError(f"each job must be an object, got {type(job).__name__}")
    job = normalize_job(job, data)
    label = job.get("label")
    alias = job.get("alias")
    prompt = job.get("promptFile")
    for name, val in (("label", label), ("alias", alias or job.get("rows")), ("promptFile", prompt)):
        if not val:
            raise JobError(f"job {label or '<unlabelled>'}: `{name}` is required")
    if not os.path.exists(prompt):
        raise JobError(f"job {label}: promptFile not found: {prompt}")
    # Children run with cwd=REPO: a caller-relative path must reach them resolved, or it names another file.
    prompt = job["promptFile"] = os.path.abspath(prompt)
    # `label` names four output files AND keys the spend ledger, so it must be a bare filename:
    # a `/` or `..` writes outside --out-dir, or dies as a raw OSError inside a worker thread --
    # after this function's docstring promised every refusal happens before anything starts.
    if str(label) in (".", "..") or str(label) != os.path.basename(str(label).replace("\\", "/")):
        raise JobError(f"job {label!r}: `label` names this job's output files and its ledger row -- "
                       f"it must be a bare filename, with no path separator and no `..`")
    # On Windows `p:x.record.json` names an alternate data stream of file `p`, so the record lands
    # where no reader looks. One rule on every platform keeps a jobs file portable.
    bad = sorted({c for c in str(label) if c in ':*?"<>|' or ord(c) < 32})
    if bad:
        raise JobError(f"job {label!r}: `label` names this job's output files, and {''.join(bad)!r} "
                       f"is not allowed in a Windows file name -- use `-` or `.` instead")

    for row_alias, row_effort in listed_rows(job):
        transport = check_row(job, label, row_alias, row_effort, data)
    if job.get("window") is not None:
        if job["window"] != "full":
            raise JobError(f"job {label}: `window` must be \"full\" or absent, got {job['window']!r}")
        FULL_WINDOW.add(label)
    # A bare string here iterates per CHARACTER, so a malformed job passes planning and dispatches a
    # billed launcher with argv full of single letters.
    for key in ("contextFiles", "extraArgs"):
        if job.get(key) is not None and not isinstance(job[key], list):
            raise JobError(f"job {label}: `{key}` must be a list, not "
                           f"{type(job[key]).__name__} -- a string iterates per character.")
    if job.get("contextFiles"):
        job["contextFiles"] = [os.path.abspath(str(c)) for c in job["contextFiles"]]
    for key in ("schemaFile", "workdir"):
        if job.get(key):
            job[key] = os.path.abspath(str(job[key]))
    if job.get("schemaFile") and not os.path.isfile(job["schemaFile"]):
        raise JobError(f"job {label}: schemaFile not found: {job['schemaFile']}")
    # `plan` names who pays: the route itself or a live plan whose `planOf` is the route. Its
    # launcher runs the job; the model vocabulary stays the route's. A job naming none pays with
    # the plan `pick_plan` ranks first, never the route's default login by omission. A job listing
    # `rows` ranks the plans of every listed row's route, and runs the cheapest listed row on the winner.
    transport, plans = job_routes(job, data)
    refusal = admission_refusal([job], data) if job.get("plan") else None
    if refusal:
        raise JobError(refusal)
    rest = []
    plan = job.get("plan") or pick_plan(label, transport, plans, data, rest=rest, job=job)
    if plan not in plans:
        raise JobError(f"job {label}: plan {plan!r} does not pay for {transport}'s rows -- live plans "
                       f"of {transport}: {', '.join(plans)}. Drop `plan`, or name one of those.")
    launcher = (model_registry.transport_meta(plan, data) or {}).get("launcher")
    if not launcher:
        raise JobError(f"job {label}: plan {plan} registers no launcher -- cannot hop")
    picks = None
    if job.get("rows"):
        picks = {name: (*row_on_plan(job, name, data), job_signature(job_on(job, name, data), data))
                 for name in plans}
        job = job_on(job, plan, data)
        alias = job["alias"]
    signature = job_signature(job, data)
    PLACED.setdefault(plan, []).append(signature)
    SLOTS[label] = Slot(plan, signature, () if job.get("plan") else tuple(plans), picks)
    FALLBACKS.pop(label, None)
    spare = []
    for name in rest:
        path = (model_registry.transport_meta(name, data) or {}).get("launcher")
        if path:
            spare.append((name, path, *(picks[name][:2] if picks else ())))
    if spare:
        FALLBACKS[label] = spare

    out_dir = Path(out_dir)
    # argv[0] comes from sidecar_launch.git_bash(), never the literal "bash": Windows Python
    # resolves a bare `bash` to the System32 WSL shim, a different filesystem view whose $HOME
    # holds none of this user's credentials. Measured here 2026-09-08 -- the codex jobs exited 3
    # ("run claude-code-proxy codex auth login") on a machine that is logged in, and the opencode
    # jobs died on a litellm proxy started under /mnt/c. Both symptoms name the launcher, not the
    # shell. sidecar_launch owns this choice for every Python caller.
    argv = [sidecar_launch.git_bash(), _posix(launcher), "-m", alias, "-f", _posix(prompt),
            "-l", label,                                   # attribution: the metrics reader keys on
            "-X",                                          # every fan-out child runs detached
            "-R", _posix(attempt_record_path(out_dir, label))]  # unlabelled rows are skipped by it
    for flag, key in (("-e", "effort"), ("-D", "disclosure"), ("-G", "shape"), ("-Q", "tier"),
                      ("-S", "schemaFile"), ("-d", "workdir")):
        if job.get(key):
            argv += [flag, _posix(job[key])]
    # `tools.add` / `tools.remove` reach the launcher as the ONE signed `-t` grammar
    # (`+Bash,-mcp:ai-worker`), so a fan-out job and a command-line caller resolve through the
    # same contract. An unsigned `tools` value is refused, never read as a profile; the legacy
    # absolute list rides `toolsCsv`, and mixing it with a delta is refused, never merged.
    try:
        add, remove = tool_profiles.tools_delta(job.get("tools"))
    except ValueError as err:
        raise JobError(f"job {label}: {err}") from None
    if add or remove:
        if job.get("toolsCsv"):
            raise JobError(f"job {label}: tools CSV is absolute and cannot be combined with "
                           f"tools.add/tools.remove. Use the signed -t grammar.")
        argv += ["-t", tool_profiles.signed_csv(add, remove)]
    elif job.get("toolsCsv"):
        argv += ["-t", str(job["toolsCsv"])]
    # `baseBuiltins` is the `arm` shape's task row when a DELTA rides on top of it: the two grammars
    # cannot share one `-t` CSV, and an arm without a base (or a `-t` task row) refuses before spend.
    if job.get("baseBuiltins"):
        argv += ["-B", str(job["baseBuiltins"])]
    if job.get("pick") or job.get("spec"):
        if not (job.get("pick") and job.get("spec")):
            raise JobError(f"job {label}: `pick` and `spec` record one pin basis (-Y <pick>,<spec>); "
                           f"give both, or neither.")
        if job["pick"] not in PIN_PICKS or job["spec"] not in PIN_SPECS:
            raise JobError(f"job {label}: invalid pin basis '{job['pick']},{job['spec']}' (pick in "
                           f"{'|'.join(PIN_PICKS)}, spec in {'|'.join(PIN_SPECS)}).")
        argv += ["-Y", f"{job['pick']},{job['spec']}"]
    if job.get("longContext") is True:
        argv += ["-M"]  # the registry's max window; codex-only, the launcher refuses it elsewhere
    for cf in job.get("contextFiles") or []:
        if not os.path.exists(cf):
            raise JobError(f"job {label}: contextFile not found: {cf}")
        argv += ["-C", _posix(cf)]
    # The launcher refuses a profile that does not cover its brief; refusing here keeps the promise
    # that a bad job costs nothing, even beside nine good ones. An arm's task row is never widened.
    shape = job.get("shape") or "any"
    try:
        citer_reason = citer_coverage.coverage_reason(job, root=job.get("workdir") or REPO, stage=stage)
    except Exception:
        citer_reason = "citer coverage could not be checked. " + citer_coverage.REMEDY if citer_coverage.BLOCKING else None
    if citer_reason:
        if citer_coverage.BLOCKING:
            raise JobError(citer_reason)
        print("warning: " + citer_reason, file=sys.stderr)
    if shape != "arm":
        try:
            builtins = job_builtins(job)
            orders = tool_profiles.brief_files(shape, prompt, job.get("contextFiles"))
            refusal = tool_profiles.check_brief_files(
                builtins, orders, grammar="job",
                root=job.get("workdir") or None, widen=True,
                removed=tool_profiles.tools_delta(job.get("tools"))[1])
        except ValueError as err:
            raise JobError(f"job {label}: {err}")
        if refusal:
            raise JobError(f"job {label}: {refusal}")
    # The ladder pins this to the EFFORT rung, not the shape: a `max` run compacts and returns
    # prose without `-P` (model_ladder_evidence.md, §Role guidance). Schema and review shape stay as
    # additional triggers.
    # -P is also the stall watchdog's switch (sc_run_watched watches this stream), so EVERY job
    # gets one: an unattended fan-out child is the exact case the watchdog exists for.
    argv += ["-P", _posix(out_dir / f"{label}.stream")]
    # `-P` alone only lets the child RESUME after a compaction; `-S` is what makes it resume into a
    # STRUCTURED deliverable rather than handing back the compaction summary as prose. A review
    # lens therefore gets the canonical schema by default -- remembering the pair is exactly what
    # does not happen, and the prose that came back from a fan-out missing it was read as the
    # model's ceiling rather than as the dispatch error it was.
    if job.get("shape") == "review" and not job.get("schemaFile"):
        if DEFAULT_REVIEW_SCHEMA.is_file():
            argv += ["-S", _posix(DEFAULT_REVIEW_SCHEMA)]
        else:
            raise JobError(f"job {label}: shape `review` needs a schema and the default is missing "
                           f"({DEFAULT_REVIEW_SCHEMA}). Pass `schemaFile`, or restore that file -- "
                           f"a review lens without one returns prose on any compaction.")
    if authorize:
        argv.append("-A")
    argv += [str(a) for a in (job.get("extraArgs") or [])]
    return label, argv, _posix(out_dir / f"{label}.out.json")


def job_builtins(job):
    """The built-in tools the job's child launches with: its shape's resolved profile, or an `arm`
    job's task row (`baseBuiltins` or `toolsCsv`) with its delta applied."""
    add, remove = tool_profiles.tools_delta(job.get("tools"))
    if (job.get("shape") or "any") == "arm":
        base = [n.strip() for n in str(job.get("baseBuiltins") or job.get("toolsCsv") or "").split(",")
                if n.strip()]
        return [n for n in dict.fromkeys(base + [a for a in add if not a.startswith("mcp:")])
                if n not in remove]
    return tool_profiles.resolve(
        job.get("shape") or "any", tools_csv="" if (add or remove) else str(job.get("toolsCsv") or ""),
        add=add, remove=remove, walk_mcp=False)[0]["builtins"]


def concurrency_guard_text(shell):
    """The single-flight guard dispatch.js injects, rendered from its one source file."""
    src = json.loads(GUARD_SOURCE.read_text(encoding="utf-8"))
    return (src["header"] + "\n" + src["rule"] + (src["withShell"] if shell else src["noShell"])
            + src["stop"] + "\n")


def add_concurrency_guard(planned, jobs, max_parallel, out_dir):
    """`planned` with the guard's `-C` file inserted before each `-P` when the jobs run at once."""
    if len(planned) < 2 or max_parallel < 2:
        return planned
    guarded = []
    for (label, argv, out_path), job in zip(planned, jobs):
        shell = "Bash" in job_builtins(normalize_job(job))
        path = Path(out_dir) / ("single_flight_guard%s.md" % ("" if shell else "_noshell"))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(concurrency_guard_text(shell))
        at = argv.index("-P") if "-P" in argv else len(argv)
        guarded.append((label, argv[:at] + ["-C", _posix(path)] + argv[at:], out_path))
    return guarded


def lane_guard_text(several_lanes, exclusive, shared):
    """dispatch_chains.js's guardFor: a lane runs its own jobs one at a time, so it owns its resources (a
    worktree's build and test lock); only the machine-wide tools in `sharedSingleFlight` are barred."""
    lines = ["=== ORCHESTRATION GUARD ==="]
    if several_lanes:
        lines.append("Other agents are running CONCURRENTLY in other lanes. You are the ONLY agent in your own lane"
                     + (" (" + exclusive + "), which you own outright for the duration of your task." if exclusive
                        else "."))
    if shared:
        lines.append("MACHINE-WIDE single-flight — do NOT use these under any circumstances, even though you own "
                     "your lane: " + "; ".join(shared) + ". If your brief mandates one, STOP and report that it "
                     "needs a serialized dispatch.")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def add_lane_guard(planned, jobs, lanes, out_dir):
    """`planned` with each lane job's guard `-C` file inserted before its `-P`, in place of the flat pool's
    single-flight bar."""
    guarded = []
    for (label, argv, out_path), job in zip(planned, jobs):
        text = lane_guard_text(len(lanes) > 1, job.get("_exclusive"), job.get("_shared_single_flight") or [])
        if not text:
            guarded.append((label, argv, out_path))
            continue
        path = Path(out_dir) / f"{label}.lane_guard.md"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        at = argv.index("-P") if "-P" in argv else len(argv)
        guarded.append((label, argv[:at] + ["-C", _posix(path)] + argv[at:], out_path))
    return guarded


def add_chain_handoff(planned, lanes, out_dir):
    """`planned` with a `-C` file inserted before each `-P` for every lane job after its lane's first: the
    position and predecessors dispatch_chains.js's handoff text tells a later step."""
    lane_of = {label: (name, labels, labels.index(label))
               for name, labels in lanes for label in labels if label}
    handed = []
    for label, argv, out_path in planned:
        name, labels, at = lane_of.get(label, (None, None, 0))
        if not at:
            handed.append((label, argv, out_path))
            continue
        path = Path(out_dir) / f"{label}.handoff.md"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("=== CHAIN POSITION ===\n"
                     f"You are step {at + 1} of {len(labels)} in lane `{name}`. Earlier steps in this lane ("
                     + ", ".join(f"`{p}`" for p in labels[:at])
                     + ") have ALREADY edited this working tree and their changes are present. Read the current "
                       "file bytes rather than assuming the pre-chain state, and if a change of yours would revert "
                       "or contradict an earlier step, report the conflict instead of silently overwriting it.\n")
        place = argv.index("-P") if "-P" in argv else len(argv)
        handed.append((label, argv[:place] + ["-C", _posix(path)] + argv[place:], out_path))
    return handed


def continuation_text(attempt, stopped, plan, brief, workdir, start_head, report):
    """The `-C` brief of a job's next attempt after a usage limit stopped attempt `attempt - 1` on plan `stopped`: a fresh
    session on `plan`, told where the stopped run left its work (`workdir` from `start_head`, its `report`) and to
    return as `brief` says."""
    where = workdir or "your working directory"
    log = f"git log --oneline {start_head}..HEAD" if start_head else "git log --oneline"
    return ("=== CONTINUATION ===\n"
            f"A provider usage limit stopped the earlier run of this job on plan {stopped} partway through its work. "
            f"You are a fresh session on plan {plan} (attempt {attempt}), with none of that run's memory. "
            "Nothing it did is lost.\n"
            f"- Read the brief {brief} in full first, then its `.amend.md` beside it if present.\n"
            f"- {where} holds the earlier run's commits (`{log}`) and its uncommitted edits (`git status --short`). "
            "Keep both: read each uncommitted diff, finish or correct it, and commit it under the brief's rules. "
            "Never discard it.\n"
            f"- Read the earlier run's progress report {report} and any files it names; redo nothing it records as "
            "done with evidence.\n"
            "- Return exactly as the brief's Return section says, to the same paths.\n")


def attempt_number(record_path):
    """The attempt a record path names: `<label>.attemptN.record.json` is N, the plain `<label>.record.json` is 1."""
    found = re.search(r"\.attempt(\d+)\.record\.json$", str(record_path))
    return int(found.group(1)) if found else 1


def attempt_record_path(out_dir, label):
    """The next unused attempt's record path for `label` under `out_dir`.

    Attempt 1 keeps the label's plain `<label>.record.json` name, so a job's first run writes the
    same path this tool always has (an existing consumer sees no change). `-X` refuses a used
    record path, checked against its `.out`/`.err`/`.exit`/`.pid` siblings -- so relaunching the
    same jobs file into the same --out-dir, the only way this tool re-plans a label it already
    ran, steps the attempt number until it finds one none of those four siblings claim. A prior
    attempt's files are never touched: this only ever picks a path, never deletes or renames one.
    """
    out_dir = Path(out_dir)
    n = 1
    while True:
        name = f"{label}.record.json" if n == 1 else f"{label}.attempt{n}.record.json"
        record = out_dir / name
        if not any(Path(str(record) + suffix).exists()
                   for suffix in (".out", ".err", ".exit", ".pid")):
            return record
        n += 1


def latest_attempt_record_path(out_dir, label):
    """The highest-numbered attempt's record path for `label` that some run has actually touched.

    Mirrors attempt_record_path's own numbering so the two can never disagree on what "attempt N"
    means. "Touched" means at least one `.out`/`.err`/`.exit`/`.pid`/`.memory` sibling exists; an untouched
    candidate is only where the NEXT attempt would land, not one anyone dispatched. None if the
    label has never been planned into this out_dir at all.
    """
    out_dir = Path(out_dir)
    best = None
    n = 1
    while True:
        name = f"{label}.record.json" if n == 1 else f"{label}.attempt{n}.record.json"
        record = out_dir / name
        touched = any(Path(str(record) + suffix).exists()
                      for suffix in (".out", ".err", ".exit", ".pid", ".memory"))
        if not touched:
            return best
        best = record
        n += 1


def resume_id(record_path):
    """The launcher's persisted session id, so a failure can be resumed DELIBERATELY."""
    try:
        with open(record_path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec.get("sessionId") or rec.get("session_id")
    except Exception:
        return None


def freeze_fingerprint(workdir, extra_paths=()):
    """Digest of the compared input: the worktree HEAD, the CONTENT of every dirty path under
    FROZEN_PATHS, and the content of each caller-named `extra_paths` entry.

    Taken on both sides of each child so a mid-run write is attributable to the arms that were still
    running when it landed, rather than invisible to all of them.
    """
    if not workdir:
        return None
    def git(*a):
        r = subprocess.run(["git", "-C", workdir, *a], capture_output=True, text=True)
        return (r.stdout or "") if r.returncode == 0 else "?"

    status = git("status", "--porcelain", "--", *FROZEN_PATHS)
    parts = [git("rev-parse", "HEAD"), status]

    # Status output names the changed paths, never their CONTENT -- so once a path is dirty, every
    # later edit to it produces the same line and moves nothing. Between two arms that is invisible:
    # arm A reads version 1, an edit lands, arm B reads version 2, and both report the same
    # fingerprint. Hash the bytes of each dirty path, so a second write to an already-dirty file is
    # as visible as the first.
    for line in status.splitlines():
        rel = line[3:].strip().strip('"')
        rel = rel.split(" -> ")[-1]                      # renames report `old -> new`
        parts.append(rel)
        parts.append(_content_digest(os.path.join(workdir, rel)))

    # The compared input is whatever the arms READ, and FROZEN_PATHS covers `.claude` alone. A
    # promptFile, a context file or a reviewed source tree outside it moves without touching any of
    # the above, so the caller names those paths and they are hashed by content too -- tracked or
    # not, since a gitignored brief is still an input.
    for extra in sorted(set(extra_paths or ())):
        parts.append(str(extra))
        parts.append(_content_digest(extra if os.path.isabs(str(extra))
                                     else os.path.join(workdir, str(extra))))

    return hashlib.sha1("\0".join(parts).encode("utf-8", "replace")).hexdigest()[:12]


def _content_digest(path):
    """sha1 of a file's bytes, or a marker naming why there are none.

    A missing or unreadable path returns a DISTINCT marker rather than a constant: `absent` and
    `unreadable` must not collide, or a file deleted mid-run fingerprints the same as one that was
    never there. Directories hash by their sorted entry names -- enough to catch an added or removed
    file without walking a tree of unknown size on every fingerprint.
    """
    try:
        if os.path.isdir(path):
            return "dir:" + hashlib.sha1(
                "\0".join(sorted(os.listdir(path))).encode("utf-8", "replace")).hexdigest()[:12]
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:12]
    except FileNotFoundError:
        return "absent"
    except OSError as err:
        return "unreadable:%s" % err.__class__.__name__


def inputs_of(argv):
    """Every file the child READS: the `-f` prompt, each `-C` context file, the `-S` schema.

    Read off argv rather than the job dict for the same reason as `workdir_of` -- these are what the
    child is actually handed. `-P` and `-R` are outputs and are deliberately absent: they differ per
    arm by design, and including them would make every comparison look confounded.
    """
    found = []
    for i, tok in enumerate(argv):
        if tok in ("-f", "-C", "-S") and i + 1 < len(argv):
            found.append(argv[i + 1])
    return sorted(set(found))


def _head_of(workdir):
    done = subprocess.run(["git", "-C", workdir, "rev-parse", "HEAD"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def workdir_of(argv):
    """The `-d` operand: what the child actually reads, not what the job dict claimed."""
    try:
        return argv[argv.index("-d") + 1]
    except (ValueError, IndexError):
        return None


def brief_of(argv):
    """The `-f` operand: the prompt file the launcher reads, whose `.amend.md` the job is told to re-read."""
    try:
        return argv[argv.index("-f") + 1]
    except (ValueError, IndexError):
        return None


def launcher_of(argv):
    """The launcher a job runs, spelled as its launched line shows it (`anthropic_secondary` for
    `anthropic_secondary_sidecar.sh`), or None for an argv that runs no launcher script."""
    script = os.path.basename(argv[1]) if len(argv) > 1 else ""
    return script[:-len("_sidecar.sh")] if script.endswith("_sidecar.sh") else None


def progress_of(argv):
    """The `-P` operand: the live progress stream this job's launcher tees to, if any."""
    try:
        return argv[argv.index("-P") + 1]
    except (ValueError, IndexError):
        return None


def stream_written(stream_path, since):
    """True when the job's `-P` stream was written at or after `since` (epoch seconds).

    Every launcher truncates `-P` right after its last gate and before the model child, so a stream
    written since the launch means the job passed every pre-spend check; an absent or older one means
    no model call was made. One second of slack covers coarse file timestamps.
    """
    try:
        return bool(stream_path) and os.path.getmtime(stream_path) >= since - 1
    except OSError:
        return False


def record_path_of(argv):
    """The `-R` operand this child actually received, or None with no `-R` at all.

    Read off argv, matching workdir_of/progress_of/inputs_of -- a relaunch's record path is an
    ATTEMPT-numbered path chosen once at plan time (attempt_record_path), so reconstructing
    `<out_dir>/<label>.record.json` here instead would silently read attempt 1 forever.
    """
    try:
        return argv[argv.index("-R") + 1]
    except (ValueError, IndexError):
        return None


def _pid_alive(pid):
    """Best-effort PID-existence check: on Windows, MSYS `/proc/<pid>` through Git Bash (the
    launchers' pids are MSYS pids), else OpenProcess (query-only); `os.kill(pid, 0)` on POSIX.

    Existence-only: it cannot tell a live detached job from an unrelated process that later reused
    the same PID. That matches `_wait_detached`'s own tolerance -- a stale "alive" reading costs
    one more poll, never a hang, and a stale "dead" reading is exactly what its one-more-poll rule
    is for.
    """
    if pid is None:
        return False
    if platform.system() == "Windows":
        # Launchers record `$BASHPID`/`$!`: an MSYS pid, which Git Bash numbers apart from Windows
        # pids, so OpenProcess cannot see it. MSYS's own /proc is the authority when bash runs.
        bash = sidecar_launch.git_bash()
        if bash and os.path.isabs(bash):
            try:
                return subprocess.run([bash, "-c", "test -e /proc/%d" % int(pid)],
                                      capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _detached_pid(record_path):
    """The most recently recorded pid in `<record>.pid`, or None.

    `sc_detach_launch` appends one line per claim/spawn step -- the claiming process first, then
    the actual detached job's own pid -- so the LAST `pid=<N>` line is the one that runs the job.
    """
    try:
        with open(str(record_path) + ".pid", encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        if "pid=" in line:
            try:
                return int(line.rsplit("pid=", 1)[1].strip())
            except ValueError:
                continue
    return None


def _wait_detached(record_path, poll_interval=None):
    """Block until `<record>.exit` appears, or the detached pid dies with none written.

    Returns (exit_code, died_without_exit). No timeout: Design §3 gives this loop none, so a
    genuinely long job only ever ends via its own exit file. The single exception is the
    detached job's own process dying without writing one -- a crash, not a long run -- caught by
    giving the pid's death exactly one more poll before believing it: a transient race between
    "the pid just exited" and "the file just landed" costs one extra sleep, never a false "died".
    """
    poll_interval = POLL_INTERVAL_SEC if poll_interval is None else poll_interval
    exitf = str(record_path) + ".exit"
    grace_used = False
    while True:
        if os.path.isfile(exitf):
            try:
                with open(exitf, encoding="utf-8", errors="replace") as fh:
                    code = fh.read().strip()
                # An empty file is a write caught between open and flush: poll again.
                if code:
                    return int(code), False
            except OSError:
                pass
            except ValueError:
                return None, False
        pid = _detached_pid(record_path)
        if pid is not None and not _pid_alive(pid):
            if grace_used:
                return None, True
            grace_used = True
        else:
            grace_used = False
        time.sleep(poll_interval)


def bound_finished_stream(stream_path, out_path):
    """Replace a finished byte-identical progress stream with a pointer to stdout.

    A resumed launcher appends every segment to `-P` but replaces captured stdout on each resume.
    When those files differ, the progress stream is the only full record and must stay intact.
    """
    if not stream_path or not os.path.isfile(stream_path):
        return
    try:
        with open(stream_path, "rb") as stream, open(out_path, "rb") as output:
            if stream.read() != output.read():
                return
    except OSError:
        return
    pointer = {"seeAlso": os.path.basename(str(out_path)),
               "note": "progress stream bounded after the run completed; its bytes match "
                       "the sibling .out.json"}
    try:
        with open(stream_path, "w", encoding="utf-8") as fh:
            json.dump(pointer, fh)
    except OSError:
        pass  # bounding is a disk-space cleanup, never worth failing a completed run over


def duplicate_launches(planned):
    """The refusal text naming each planned job whose `-f` prompt a live launcher or seat child already
    runs as that job (same `-l` label or `-R` record), or whose `-r` session one resumes
    (`reap.py live-launch`); "" when none, or when the process table is unreadable (a warning: the
    launchers check again)."""
    import reap
    table = reap.process_table()
    if not table:
        print("[dispatch] WARN: process table unreadable; the duplicate-launch check is skipped", file=sys.stderr)
        return ""
    blocks = []
    for label, argv, _out in planned:
        flags = {tok: argv[i + 1] for i, tok in enumerate(argv[:-1]) if tok in ("-f", "-r", "-l", "-R")}
        hits = reap.live_launches(table, prompt=flags.get("-f"), resume=flags.get("-r"),
                                  label=flags.get("-l") or label, record=flags.get("-R"))
        if hits:
            blocks.append("%s:\n%s" % (label, reap.describe_launches(table, hits)))
    return "\n".join(blocks)


REVIEW_CLAIM = "review.claim"


def _inventory_job(label, argv, launch_id, record_path, wake_with=None):
    job = {"label": label, "launchId": launch_id, "recordPath": os.path.abspath(record_path)}
    if wake_with:
        job["wakeWith"] = wake_with
    launcher = launcher_of(argv)
    if launcher:
        job["launcher"] = launcher
    if workdir_of(argv):
        job.update(workdir=os.path.abspath(workdir_of(argv)), startHead=_head_of(workdir_of(argv)))
    if brief_of(argv):
        job["brief"] = os.path.abspath(brief_of(argv))
    return job


def run_jobs(planned, max_parallel, out_dir, fallbacks=None, lanes=None, after_seats=False, waves=None,
             wake_groups=None):
    # A bounded pool: `max_parallel` workers draining a queue. The previous shape started one OS
    # thread per job and used the semaphore only to cap concurrent SUBPROCESSES, so a 200-job file
    # spawned 200 threads to run 4 at a time -- and N is caller-controlled from the jobs JSON.
    # `waves` (a `Waves`, or None when every plan holds its forecast) picks which queued unit starts next and when a
    # job waits, moves or stops; every call into it runs under `lock`, and a job end wakes the workers waiting on it.
    results = {}
    if not planned:
        return results
    parent_session = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    fanout_id = str(uuid.uuid4())
    inventory_path = Path(out_dir) / ("fanout-" + fanout_id + ".inventory.json")
    allocated = []
    for label, argv, out_path in planned:
        launch_id = str(uuid.uuid4())
        # Read off argv, never reconstructed from (out_dir, label): an attempt-numbered relaunch's
        # `-R` is not `<label>.record.json`, and reconstructing it here would silently poll attempt
        # 1's files forever. A job with no `-R` at all (a caller invoking run_jobs directly, never
        # through plan_job) falls back to the legacy guess so it keeps whatever behavior it had.
        record_path = record_path_of(argv) or str(Path(out_dir) / f"{label}.record.json")
        allocated.append((label, argv, out_path, launch_id, record_path))
    process = os.getpid()
    process_line = "%s %spid=%d" % (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "winpid=%d " % process if os.name == "nt" else "", process)
    inventory = {"schemaVersion": 1, "fanoutId": fanout_id,
                 "parentSessionId": parent_session, "fanoutProcess": process_line, "afterSeats": after_seats,
                 # Absolute: the inventory's readers (the Stop hook, the watcher) run from other cwds.
                 # `workdir` lets the close tell a job writing in a worktree from one writing in its checkout;
                 # `startHead` is the commit it was cut from, the base of the changes the job made.
                 "jobs": [_inventory_job(label, argv, launch_id, record_path, (wake_groups or {}).get(label))
                          for label, argv, _, launch_id, record_path in allocated]}
    try:
        with open(inventory_path, "x", encoding="utf-8") as fh:
            json.dump(inventory, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as err:
        raise RuntimeError(f"cannot persist sidecar inventory {inventory_path}: {err}") from err
    _LAUNCH["inventory"] = True
    # A unit runs on one worker, its jobs in sequence: one job, or a chain lane (`lanes`: (name, labels) pairs).
    by_label = {job[0]: job for job in allocated}
    pending = [(name, [by_label[label] for label in labels if label in by_label]) for name, labels in lanes] \
        if lanes else [(None, [job]) for job in allocated]
    lock = threading.Lock()
    turn = threading.Condition(lock)
    threads = []

    def save_inventory():
        tmp = inventory_path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(inventory, fh, separators=(',', ':'))
        os.replace(tmp, inventory_path)

    def point_inventory_at(label, record_path):
        """The job's inventory row follows its latest attempt, so the watcher sees one job whichever attempt runs."""
        with lock:
            for job in inventory['jobs']:
                if job['label'] == label:
                    job['recordPath'] = os.path.abspath(record_path)
            save_inventory()

    def one(label, argv, out_path, launch_id, record_path, continuing=False):
        # SIDECAR_FANOUT_ID marks siblings: jobs of one fan-out may share a brief, and are never
        # each other's duplicate launch (sidecar_common.sh sc_check_duplicate_launch).
        child_env = dict(os.environ, SIDECAR_LAUNCH_ID=launch_id, SIDECAR_FANOUT_ID=fanout_id,
                         SIDECAR_CHECK_OWNER=launch_id)
        if label in FULL_WINDOW:
            child_env["SIDECAR_FULL_WINDOW"] = "1"
        identity = {"launchId": launch_id, "parentSessionId": parent_session,
                    "inventoryPath": str(inventory_path)}
        since = time.time()
        if True:
            print(f"[dispatch] {label}: {' '.join(argv[:6])} ...", flush=True)
            # Built from the same (out_dir, label) pair as `out_path`, not by substring surgery on
            # it: `.replace` rewrites EVERY `.out.json` in the path, so an out-dir carrying that
            # name in a parent segment silently redirected stderr outside the run.
            err_path = str(Path(out_dir) / f"{label}.err")
            wd, reads = workdir_of(argv), inputs_of(argv)
            before = freeze_fingerprint(wd, reads)
            detached = "-X" in argv
            died = False
            retry = False
            pre_spawn_refusal = False
            # A continuation was placed by a fresh reading of the plans; a refusal of it ends the continuation.
            spare = list((fallbacks or {}).get(label) or []) if detached and not continuing else []

            def rebind_record():
                nonlocal argv, record_path
                record_path = _posix(attempt_record_path(out_dir, label))
                at = argv.index('-R') + 1
                argv = argv[:at] + [record_path] + argv[at + 1:]
                point_inventory_at(label, record_path)

            def launch():
                with open(out_path, "w", encoding="utf-8") as so,                         open(err_path, "w", encoding="utf-8") as se:
                    return subprocess.call(argv, stdout=so, stderr=se, cwd=str(REPO), env=child_env)

            def child():
                nonlocal argv, died, retry, pre_spawn_refusal
                if retry and detached:
                    rebind_record()
                while True:
                    began = time.time()
                    code = launch()
                    # A detached launcher exits 0 once it spawned, so a plan-refusal code from the launch call
                    # means nothing ran. One read from the job's own `.exit` means nothing ran only while its
                    # `-P` stream is unwritten: the launcher truncates it after the last gate, before the model.
                    pre_spawn_refusal = detached and code in PLAN_REFUSAL_EXITS
                    if detached and code == 0:
                        code, died = _wait_detached(record_path)
                        for source, target in ((record_path + '.out', out_path), (record_path + '.err', err_path)):
                            if os.path.isfile(source):
                                try:
                                    shutil.copyfile(source, target)
                                except OSError:
                                    pass
                        pre_spawn_refusal = code in PLAN_REFUSAL_EXITS and not stream_written(progress_of(argv), began)
                    # The job named no plan (a named one has no `spare`); a run that ended after spend is read
                    # below and never retried.
                    if not (pre_spawn_refusal and spare):
                        break
                    plan, launcher, *row = spare.pop(0)
                    print(f"[fanout] {label}: {LAUNCHER_EXITS[code]} (exit {code}), nothing ran; "
                          f"paying with plan {plan} instead" + (f", row {row[0]}" if row else ""),
                          file=sys.stderr, flush=True)
                    argv = with_row(argv, launcher, *row)
                    rebind_record()
                retry = code == MEMORY_REFUSED
                return code
            try:
                code = supervised_launch(label, child, child_env, argv)
            except Exception as err:
                results[label] = {**identity, "exit": None, "error": str(err),
                                  "frozenInput": {"before": before, "after": before, "held": True}}
                return
            if died:
                after = freeze_fingerprint(wd, reads)
                results[label] = {
                    **identity, "exit": None,
                    "error": f"detached pid died without writing {record_path}.exit",
                    "stdout": out_path, "stderr": err_path, "record": None, "resumeWith": None,
                    **_delivery(out_dir, label, out_path, since),
                    "frozenInput": {"before": before, "after": after, "held": before == after},
                }
                return
            # Collapse `-P` only when it duplicates stdout. Resumed runs append to `-P` while
            # stdout holds the last segment, so a differing progress file is the full archive.
            # A usage-limit stream is the salvage source the summary names; keep it whole.
            if code != USAGE_LIMIT_EXIT:
                bound_finished_stream(progress_of(argv), out_path)
            after = freeze_fingerprint(wd, reads)
            rec = record_path
            try:
                with open(rec, encoding="utf-8") as fh:
                    record = json.load(fh)
                if not isinstance(record, dict) or record.get("launchId") != launch_id or record.get("parentSessionId") != parent_session:
                    record = None
            except (OSError, ValueError):
                record = None
            results[label] = {
                **identity,
                "exit": code,
                "meaning": LAUNCHER_EXITS.get(code, "unrecognised launcher exit"),
                "preSpawnRefusal": pre_spawn_refusal,
                "stdout": out_path,
                "stderr": err_path,
                "stream": progress_of(argv),
                "record": rec if record is not None else None,
                **_delivery(out_dir, label, out_path, since),
                # Reported, never acted on. A retry here would re-bill a call the caller has not
                # seen the failure of.
                "resumeWith": (record.get("sessionId") or record.get("session_id")) if code != 0 and record is not None else None,
                # A mid-run usage limit: the session lives in this plan's login, so a resume names the plan and its reset.
                **({"plan": record.get("transport"), "limitResetsAt": record.get("usageLimitResetsAt")}
                   if code == USAGE_LIMIT_EXIT and record is not None else {}),
                "frozenInput": {"before": before, "after": after, "held": before == after},
            }

    def lane_ended(lane, label, rest, error=None):
        """Record the unrun jobs of a lane that `label` ended, and take them off the inventory: nothing will
        write their records, so a watcher must not wait for them."""
        for later in rest:
            results[later[0]] = {"exit": None, "stdout": None, "stderr": None, "stream": None, "record": None,
                                 "error": error or f"not run: lane `{lane}` stopped after {label} did not finish"}
        with lock:
            gone = {later[0] for later in rest}
            inventory['jobs'] = [job for job in inventory['jobs'] if job['label'] not in gone]
            save_inventory()

    def claim():
        """The next unit to run and its verdict: with `waves`, the first pending unit whose head launches or stops
        now. While every head waits, some job of its plan is running, and its end wakes this worker."""
        with turn:
            while pending:
                if waves is None:
                    lane, unit = pending.pop(0)
                    return lane, unit, LAUNCH
                waves.round()
                for at, (lane, unit) in enumerate(pending):
                    verdict = waves.decide(unit[0][0])
                    if verdict.kind != "wait":
                        del pending[at]
                        return lane, unit, verdict
                turn.wait()
        return None

    def acquire(label):
        """The verdict for a chain lane's next job, waiting while its plan holds it back."""
        if waves is None:
            return LAUNCH
        with turn:
            while True:
                waves.round()
                verdict = waves.decide(label)
                if verdict.kind != "wait":
                    return verdict
                turn.wait()

    def retarget(job):
        """`job` with the launcher of the plan `waves` left it on: a job moved before it started."""
        if waves is None:
            return job
        plan = waves.plan_of(job[0])
        launcher = _posix((model_registry.transport_meta(plan, waves.data) or {}).get("launcher") or "")
        argv = with_row(job[1], launcher, *(waves.row_of(job[0]) or ())) if launcher else job[1]
        if argv == job[1]:
            return job
        with lock:
            for row in inventory["jobs"]:
                if row["label"] == job[0] and launcher_of(argv):
                    row["launcher"] = launcher_of(argv)
            save_inventory()
        if fallbacks:
            fallbacks[job[0]] = [entry for entry in fallbacks.get(job[0], []) if entry[0] != plan]
        return (job[0], argv) + tuple(job[2:])

    def done(label):
        if waves is None:
            return
        with turn:
            waves.ended(label, results.get(label) or {})
            turn.notify_all()

    def continuation(job, stopped):
        """-> (the next attempt of `job`, the stopped attempt's kept stream or None, its record): a fresh record, the
        stopped run's `-P` stream set aside (the launcher truncates it), no `-r` (its session lives in `stopped`'s
        login), and a `-C` brief that tells the child what the stopped run left."""
        label, argv, out_path, launch_id, _ = job
        was = latest_attempt_record_path(out_dir, label) or record_path_of(argv)
        record = _posix(attempt_record_path(out_dir, label))
        stream, kept = progress_of(argv), None
        if stream and os.path.isfile(stream):
            kept = _posix(Path(out_dir) / f"{label}.attempt{attempt_number(was)}.salvage.jsonl")
            try:
                os.replace(stream, kept)
            except OSError:
                kept = None  # the launcher truncates the stream; the salvage copy is lost, the continuation is not
        row = next(j for j in inventory["jobs"] if j["label"] == label)
        brief = Path(out_dir) / f"{label}.continuation.md"
        brief.write_text(continuation_text(
            attempt_number(record), stopped, waves.plan_of(label), brief_of(argv),
            _posix(row["workdir"]) if row.get("workdir") else None, row.get("startHead"),
            _posix(dispatch_result.deliverable_path(out_dir, label))),
            encoding="utf-8", newline="\n")
        argv = list(argv)
        argv[argv.index("-R") + 1] = record
        while "-r" in argv[:-1]:
            at = argv.index("-r")
            del argv[at:at + 2]
        if _posix(brief) not in argv:
            at = argv.index("-P") if "-P" in argv else len(argv)
            argv[at:at] = ["-C", _posix(brief)]
        point_inventory_at(label, record)
        return (label, argv, out_path, launch_id, record), kept, _posix(was)

    def continue_stopped(job):
        """While a usage limit stops `job` mid-run, relaunch it as a fresh session on the plan `waves` places it on, and
        record each stopped attempt on the result as `continuedFrom`. A job `waves` places nowhere (a named `plan`, or
        every plan spent) keeps its stopped result, as does one the new plan's launcher refuses before it spawns."""
        label = job[0]
        while waves is not None and _stopped_mid_run(results.get(label)):
            prior, stopped = results[label], waves.plan_of(label)
            with turn:
                waves.requeue(label)
            if acquire(label).kind == "stop":
                return
            try:
                nxt, kept, was = continuation(job, prior.get("plan") or stopped)
            except Exception:
                done(label)
                raise
            try:
                one(*retarget(nxt), continuing=True)
            finally:
                done(label)
            now = results[label]
            if now.get("preSpawnRefusal"):
                results[label] = dict(prior, refusedContinuation=waves.plan_of(label))
                return
            now["continuedFrom"] = (prior.get("continuedFrom") or []) + [
                {"plan": prior.get("plan") or stopped, "exit": prior.get("exit"), "record": was, "stream": kept,
                 "resetsAt": prior.get("limitResetsAt"), "continuedOn": waves.plan_of(label)}]

    def worker():
        while True:
            picked = claim()
            if picked is None:
                return
            lane, unit, verdict = picked
            for at, job in enumerate(unit):
                if at:
                    verdict = acquire(job[0])
                if verdict.kind == "stop":
                    lane_ended(lane, job[0], unit[at:], error=verdict.error)
                    break
                try:
                    one(*retarget(job))
                finally:
                    done(job[0])
                try:
                    continue_stopped(job)
                except Exception as err:
                    print(f"[fanout] {job[0]}: continuation failed ({type(err).__name__}: {err}); "
                          f"the stopped result stands", file=sys.stderr, flush=True)
                ran = results.get(job[0]) or {}
                if ran.get("exit") != 0 or _blocked(ran.get("stdout")):
                    if unit[at + 1:]:
                        lane_ended(lane, job[0], unit[at + 1:])
                    break

    for _ in range(min(max_parallel, len(pending))):
        t = threading.Thread(target=worker, daemon=False)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return results


def _stopped_mid_run(result):
    """True for a result that ended in a provider usage limit after the model ran (a refusal before spawn ran nothing)."""
    return bool(result) and result.get("exit") == USAGE_LIMIT_EXIT and not result.get("preSpawnRefusal")


def _delivery(out_dir, label, out_path, since):
    """The ended job's result fields: `deliverable` (the report it wrote to `<label>.md`, else its final
    message; None when the run left neither), plus `reply` (a final message that followed a written
    report) and `deliveryFlag` (the final message may be a trailing reply) when they apply."""
    try:
        got = dispatch_result.capture_delivery(out_dir, label, out_path, since)
    except OSError:
        return {"deliverable": None}
    fields = {"deliverable": _posix(got["path"]) if got["path"] else None}
    if got["reply"]:
        fields["reply"] = _posix(got["reply"])
    if got["flag"]:
        fields["deliveryFlag"] = got["flag"]
    return fields


def _blocked(out_path):
    """The child's first gap when it stopped `blocked` with no findings, else None. An exit-0,
    schema-valid lens with an empty finding list otherwise reads as clean coverage; one blocked on a
    sub-part that still delivered findings is partial work, which its own `gaps` already name."""
    try:
        text = open(out_path, encoding="utf-8", errors="replace").read()
    except (OSError, TypeError):
        return None
    try:
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]
    except ValueError:  # a -P run writes one event per line
        rows = []
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    for row in rows:
        so = row.get("structured_output") if isinstance(row, dict) else None
        checked = so.get("checked") if isinstance(so, dict) else None
        if isinstance(checked, dict) and checked.get("stoppedAt") == "blocked" and not so.get("findings"):
            gaps = so.get("gaps") or []
            return (review_result.gap_text(gaps[0]) or str(gaps[0]))[:200] if gaps else "stoppedAt: blocked"
    return None


def _output_cap(r):
    """The cap in a classifier-confirmed terminal output-cap event; None for other failures."""
    for path in (r.get("stream"), r.get("stdout")):
        if not path or not os.path.isfile(path):
            continue
        state, _ = stream_check.stream_status(path, time.time())
        if state != "output-cap":
            continue
        try:
            last = None
            with open(path, encoding="utf-8", errors="replace") as fh:
                for event in stream_check.iter_stream_events(fh):
                    last = event
            cap = stream_check.output_cap((last or {}).get("result"))
        except OSError:
            continue
        if cap:
            return cap
    return None


def _continued_tag(result):
    """`  (continued after a usage limit on A, B; ran on C)` for a job whose attempts a usage limit stopped, else ''."""
    chain = result.get("continuedFrom") or []
    if not chain:
        return ""
    return (f"  (continued after a usage limit on {', '.join(dict.fromkeys(str(c.get('plan')) for c in chain))}; "
            f"ran on {chain[-1].get('continuedOn')})")


def summarize(planned, results, compare):
    """-> (results text, exit code). A usage-limit stop is its own outcome, not a failure; a child
    that exited 0 but reports itself blocked fails the run."""
    lines, failed, limited = ["", "--- results ---"], [], []
    for label, _, _ in planned:
        r = results.get(label) or {"exit": None, "error": "never ran"}
        if r.get("exit") == 0:
            why = _blocked(r.get("stdout"))
            if why:
                failed.append(label)
                lines.append(f"BLOCKED  {label}  -> {r['stdout']}  (exit 0; {why})" + _continued_tag(r))
                continue
            lines.append(f"ok    {label}  -> {r.get('deliverable') or '(no final message)'}  "
                         f"stdout {r['stdout']}" + _continued_tag(r))
            if r.get("reply"):
                lines.append(f"        later reply kept apart: {r['reply']}")
            if r.get("deliveryFlag"):
                lines.append(f"        WARNING {r['deliveryFlag']}")
            continue
        if r.get("preSpawnRefusal"):
            failed.append(label)
            lines.append(f"refused-before-spawn  {label}  exit={r.get('exit')} ({r.get('meaning')})")
            if r.get("stderr"):
                text = Path(r["stderr"]).read_text(encoding="utf-8", errors="replace").strip()
                lines.append("        " + text[:1500])
            continue
        if r.get("exit") == USAGE_LIMIT_EXIT:
            limited.append(label)
            lines.append(f"usage-limit  {label}  exit={USAGE_LIMIT_EXIT} ({r.get('meaning')})" + _continued_tag(r))
            lines.append(f"        salvage from: {r.get('stream') or '(no -P stream)'}")
            if r.get("refusedContinuation"):
                lines.append(f"        plan {r['refusedContinuation']} refused the continuation before it spawned")
            if r.get("resumeWith"):
                lines.append(f"        resumable on plan {r.get('plan') or '(unrecorded)'} after "
                             f"{_utc(r.get('limitResetsAt')) or 'its reset'}: {RESUME_FILE}")
            continue
        failed.append(label)
        cap = _output_cap(r)
        if cap:
            lines.append(f"FAIL  {label}  exit={r.get('exit')} (output-cap: the final message outgrew the child's "
                         f"{cap}-token output cap)")
            lines.append(f"        {stream_check.OUTPUT_CAP_ADVICE}")
        else:
            lines.append(f"FAIL  {label}  exit={r.get('exit')} ({r.get('meaning') or r.get('error')})"
                         + _continued_tag(r))
        lines.append(f"        stderr: {r.get('stderr')}")
        if r.get("resumeWith"):
            # The launcher persisted a session. Resuming is the caller's call, deliberately: this
            # tool never re-bills a run on its own.
            lines.append(f"        resumable: re-run that job's launcher with -r {r['resumeWith']}")
    # An arm whose input moved under it read different bytes from its peers, so a defect it
    # reports absent is indistinguishable from one the edit removed. Exit non-zero under --compare:
    # the numbers are the deliverable there, and wrong numbers beat no numbers only in appearance.
    moved = [l for l, _, _ in planned
             if (results.get(l) or {}).get("frozenInput", {}).get("held") is False]
    if moved and compare:
        lines.append("\nCOMPARISON INVALID: inputs changed under %d arm(s): %s" % (len(moved), ", ".join(moved)))
        lines.append("  Preserve the artifacts and identify the changed inputs before deciding whether to repeat the comparison.")
    elif moved:
        lines.append("\nInputs changed during %d job(s): %s" % (len(moved), ", ".join(moved)))
        lines.append("  Expected for authoring; verify relevant source hashes before accepting read-only claims. No automatic rerun.")

    done = len(planned) - len(failed) - len(limited)
    lines.append(f"\n{done}/{len(planned)} exited 0"
                 + (f"; usage-limit: {', '.join(limited)}" if limited else "")
                 + (f"; failed: {', '.join(failed)}" if failed else ""))
    return "\n".join(lines), 1 if (moved and compare) or failed or limited else 0


RESUME_FILE = "resume-jobs.json"


def _utc(epoch):
    """`epoch` seconds as UTC ISO text, else None."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def resume_plan(jobs, results, out_dir, lanes):
    """-> (path, text) for the jobs a usage limit stopped mid-run: `<out-dir>/resume-jobs.json`, a jobs file the
    same tool runs as written, and the message that names the command; (None, "") when none stopped.

    A stopped job that persisted a session is pinned to the plan whose login holds it and carries
    `-r <session>`; one that persisted none reruns fresh. A chain lane resumes from its stopped job, with the
    jobs the lane never ran after it. Nothing is launched: the plan refuses until its reset."""
    out_dir = Path(out_dir)
    by_label = {j.get("label"): j for j in jobs if isinstance(j, dict)}
    stopped = {label for label, r in results.items() if (r or {}).get("exit") == USAGE_LIMIT_EXIT
               and not (r or {}).get("preSpawnRefusal")}
    if not stopped:
        return None, ""

    def entry(label):
        job = {k: v for k, v in by_label[label].items() if k not in ("_lane", "_exclusive", "_shared_single_flight")}
        for key in ("promptFile", "schemaFile", "workdir"):
            if job.get(key):
                job[key] = os.path.abspath(str(job[key]))
        if job.get("contextFiles"):
            job["contextFiles"] = [os.path.abspath(str(c)) for c in job["contextFiles"]]
        r = results.get(label) or {}
        if label in stopped and r.get("resumeWith"):
            extra, kept = list(job.get("extraArgs") or []), []
            while extra:
                flag = extra.pop(0)
                if flag == "-r":
                    extra[:1] = []
                else:
                    kept.append(flag)
            job["extraArgs"] = kept + ["-r", r["resumeWith"]]
            if r.get("plan"):
                job["plan"] = r["plan"]
        return job

    spec = {"spillDir": str(out_dir)}
    if lanes:
        chains = []
        for name, labels in lanes:
            at = next((i for i, label in enumerate(labels) if label in stopped), None)
            if at is not None:
                chains.append({"name": name, "jobs": [entry(label) for label in labels[at:] if label in by_label]})
        spec["chains"] = chains
        count = sum(len(c["jobs"]) for c in chains)
    else:
        spec["jobs"] = [entry(label) for label in by_label if label in stopped]
        count = len(spec["jobs"])
    path = out_dir / RESUME_FILE
    path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8", newline="\n")
    resets = {}
    for label in stopped:
        r = results[label] or {}
        if r.get("plan"):
            known = [t for t in (resets.get(r["plan"]), r.get("limitResetsAt")) if _utc(t)]
            resets[r["plan"]] = max(known) if known else None
    when = ", ".join(f"{plan} resets {_utc(t) or 'at an unrecorded time'}" for plan, t in sorted(resets.items()))
    text = (f"\nusage-limit stop: {len(stopped)} job(s) stopped mid-run; {count} job(s) in {_posix(path)}"
            + (f" ({when})" if when else "")
            + f"\n  after the reset, run: python3 .claude/tools/sidecar_fanout.py {_posix(path)} --detach")
    return path, text


def _status_labels(out_dir):
    """Every label with an attempt record sibling or a `-P` stream under `out_dir`."""
    labels = set()
    for path in out_dir.iterdir():
        name = path.name
        if name.endswith(".stream"):
            labels.add(name[:-len(".stream")])
            continue
        head, sep, _ = name.partition(".record.json.")
        if not sep:
            continue
        base, dot, number = head.rpartition(".attempt")
        labels.add(base if dot and number.isdigit() else head)
    return sorted(labels)


def _attempt_state(record):
    """(state, detail) for one attempt from `<record>.exit` and `<record>.pid`, the same two files
    `_wait_detached` polls: finished, running, died (pid gone, no exit file), pending (no pid yet),
    or no-attempt when `record` is None."""
    if record is None:
        return "no-attempt", "no attempt record"
    where = _posix(record)
    try:
        with open(str(record) + ".exit", encoding="utf-8") as fh:
            return "finished", f"exit={fh.read().strip()}  {where}"
    except OSError:
        pass
    pid = _detached_pid(record)
    if pid is None:
        return "pending", f"no pid recorded yet  {where}"
    if _pid_alive(pid):
        return "running", f"pid {pid}  {where}"
    return "died", f"pid {pid} gone, no exit file  {where}"


def print_status(out_dir):
    """One `<label> <state> <detail>` line per job under `out_dir`. The LATEST attempt's `.exit`
    and `.pid` say whether it finished or died, never attempt 1 once a relaunch exists; its `-P`
    stream's latest events say what a live or unattempted job is doing, and a live one whose stream
    sat on its result past `RESULT_LINGER_S` is lingering (`watch_records.result_lingering`, the session
    watcher's LINGERING); its record's stopReason
    says how a finished one stopped. Returns 2 when `out_dir` holds no job, else 0."""
    from watch_records import memory_wait, result_lingering
    out_dir = Path(out_dir)
    labels = _status_labels(out_dir) if out_dir.is_dir() else []
    if not labels:
        print(f"no job records or streams in {out_dir}", file=sys.stderr)
        return 2
    now = __import__("time").time()
    stall_sec = int(os.environ.get("SIDECAR_STALL_SEC") or 900)
    limit = int(os.environ.get("SIDECAR_USAGE_LIMIT_RETRIES") or 10)
    for label in labels:
        record = latest_attempt_record_path(out_dir, label)
        state, detail = _attempt_state(record)
        waiting = memory_wait(record) if record is not None and state in ('pending', 'running') else None
        if waiting:
            state, detail = 'waiting-memory', f'{waiting}  {detail}'
        stream = out_dir / f"{label}.stream"
        lingering = result_lingering(record, now) if state == "running" else None
        if lingering is not None:
            state, detail = "lingering", (f"stream ended on a result {int(lingering)}s ago and the launcher still "
                                          f"runs  {detail}")
        elif state in ("no-attempt", "pending", "running") and stream.is_file():
            live, live_detail = stream_check.stream_status(str(stream), now, stall_sec, limit)
            state, detail = live, f"{live_detail}  {detail}"
        if record is not None:
            try:
                with open(record, encoding="utf-8") as fh:
                    stop = (json.load(fh) or {}).get("stopReason")
                state = {"provider-usage-limit": "usage-limit", "stall": "stalled",
                         "output-cap": "incomplete"}.get(stop, "finished")
                detail = f"record stopReason {stop}  {detail}"
            except (OSError, ValueError, AttributeError):
                pass
        print(f"{label:<28} {state:<11} {detail}")
    return 0


FOLD_LABEL = "review-fold-{}"
FOLD_TEMPLATE = ".claude/commands/agents/review_fold.md"


def data_block(value):
    """`value` as fenced JSON: the reader parses it as data, and a finding that quotes a command is never read as
    an instruction to run it (`tool_profiles.instruction_text` blanks fenced blocks). Backticks are escaped, so a
    fence quoted inside a finding cannot close the block early; the parsed value is unchanged."""
    return "```json\n" + json.dumps(value, ensure_ascii=False).replace("`", "\\u0060") + "\n```"


def _seat_payer(seat, data):
    """The plan a seat pays: its `plan`, else the one `pick_plan` picks for its alias's route; None when unknown."""
    if seat.get("plan"):
        return seat["plan"]
    try:
        transport = model_registry.resolve(seat["alias"], data)["transport"]
        return pick_plan(seat.get("label", "?"), transport, model_registry.plans_of(transport, data), data, job=seat)
    except Exception:
        return None


def fold_probe(args, seats=(), data=None):
    """The author job shape --fold launches, planned once before any seat spends. It pays
    `--fold-plan`, else the one plan every seat pays when that plan is legal for the fold's transport (a plan
    outside `plans_of` that transport would be refused), so the fold spends the review's currency. Otherwise
    the fold keeps its own default plan."""
    spec = {"label": FOLD_LABEL.format(0), "alias": args.fold_alias, "effort": args.fold_effort,
            "promptFile": args.fold, "shape": "author", "disclosure": "full",
            "contextFiles": [args.fold]}
    payers = {_seat_payer(seat, data) for seat in seats}
    payer = getattr(args, "fold_plan", None) or (payers.pop() if len(payers) == 1 else None)
    if payer and not getattr(args, "fold_plan", None) and data:
        try:
            legal = model_registry.plans_of(model_registry.resolve(args.fold_alias, data)["transport"], data)
        except Exception:
            legal = []
        payer = payer if payer in legal else None
    if payer:
        spec["plan"] = payer
    return spec


def print_plan(planned, max_parallel):
    for label, argv, out_path in planned:
        print(f"{label}\n  {' '.join(argv)}\n  -> {out_path}")
    print(f"\n{len(planned)} job(s), {max_parallel} at a time. Nothing dispatched.")


def run_review_fold(review, args, data, out_dir, seats=()):
    """Route each view row to one subject and launch one author per affected subject, its workdir the subject's
    root. Python writes the findings and the quote verdicts; the author writes only its subject files and its
    digest. `fold_jobs.json` names each subject for `post_check.review`, which decides the fold."""
    rows = review["view"]
    subjects = [fold_check.parse_subject_spec(spec) for spec in args.fold_subject]
    routed, _unrouted, _ambiguous = fold_check.route(rows, subjects)
    jobs = []
    for index, subject in enumerate(subjects, 1):
        label = FOLD_LABEL.format(index)
        jobs.append({"subject": subject, "label": label, "digest": str(out_dir / f"{label}_digest.json"),
                     "rows": [row for row in rows if routed.get(row["id"]) == index - 1]})
    _independent, verdicts = fold_check.independent_verdicts(rows, subjects, out_dir / "fold_input")
    rigor = finding_bar.run_rigor(args.ledger_task, REPO)
    authors = []
    for job in jobs:
        if not job["rows"]:
            with open(job["digest"], "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"applied": [], "deferred": [], "designDelta": [], "changedPaths": []}, stream)
            continue
        subject = job["subject"]
        rows = finding_bar.stamp(job["rows"], rigor)
        findings = out_dir / f"{job['label']}_findings.json"
        findings.write_text(json.dumps({"findings": rows}, indent=2, ensure_ascii=False)
                            + "\n", encoding="utf-8", newline="\n")
        writes = [_posix(subject.root / item) for item in subject.files + subject.new_paths]
        prompt = out_dir / f"{job['label']}_prompt.md"
        prompt.write_text("\n".join(line for line in [
            f"FOLD STAGE of a review. Read {_posix(args.fold)} with the Read tool and follow it.",
            f"Root: {_posix(subject.root)}",
            f"Snapshot: {_posix(subject.manifest_path)}",
            f"Files: {', '.join(subject.files)}",
            f"New paths: {', '.join(subject.new_paths)}" if subject.new_paths else None,
            f"Findings file: {_posix(findings)}",
            f"Verdicts file: {_posix(verdicts)}",
            f"Digest file: {_posix(job['digest'])}",
            f"Bar: {rigor} — each finding's `counts` says whether it counts at it (review_fold.md step 5)." if rigor else None,
            "The findings and verdicts files already exist: read them and never rewrite them. The snapshot is the "
            "caller's: never write it.",
            f"Write only: {', '.join(writes + [_posix(job['digest'])])}.",
            "",
            f"CONSOLIDATED FINDINGS ({len(job['rows'])} ids: "
            + ", ".join(row["id"] for row in job["rows"]) + "):",
            data_block(rows),
            "",
        ] if line is not None), encoding="utf-8", newline="\n")
        authors.append((job, dict(fold_probe(args, seats, data), label=job["label"], promptFile=str(prompt),
                                  workdir=str(subject.root))))
    planned = add_concurrency_guard([plan_job(spec, data, out_dir, args.authorize) for _job, spec in authors],
                                    [spec for _job, spec in authors], args.max_parallel, out_dir)
    if getattr(args, "dry_run", False):
        print_plan(planned, args.max_parallel)
        return None
    results = run_jobs(planned, args.max_parallel, out_dir, after_seats=True) if planned else {}
    author_failures = []
    for job, spec in authors:
        run = results.get(spec["label"]) or {}
        if run.get("exit") != 0:
            author_failures.append({"kind": "fold-author-failed", "subject": str(job["subject"].root),
                                    "detail": f"{spec['label']} exited {run.get('exit')!r}"})
    with open(out_dir / "fold_jobs.json", "w", encoding="utf-8", newline="\n") as stream:
        json.dump([dict(job["subject"].describe(), digest=job["digest"],
                        foldActor={"run": (results.get(job["label"]) or {}).get("record"),
                                   "label": job["label"], "source": "sidecar"},
                        artifacts=[str(out_dir / f"{job['label']}_findings.json"), str(verdicts)]) for job in jobs],
                  stream, indent=2)
        stream.write("\n")
    return {"jobs": jobs, "authors": authors, "results": results, "failures": author_failures,
            "independent": (_independent, str(verdicts))}


def finish_fold(review, fold, report, out_dir):
    """`fold_digest.json`: its status comes from the post-check's fold report and the author exits; each author's digest
    supplies only the applied ids `appliedWithAlternative` lists."""
    fold_report = json.loads(Path(report["foldReport"]).read_text(encoding="utf-8")) if report.get("foldReport") else {
        "failures": [], "dispositions": [], "independentVerdictPath": None}
    applied_ids = []
    for job in fold["jobs"]:
        try:
            value = json.loads(Path(job["digest"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        applied_ids += [entry.get("id") for entry in value.get("applied") or []
                        if isinstance(entry, dict) and isinstance(entry.get("id"), str)]
    failures = fold_report["failures"] + fold["failures"]
    if report.get('outcome', report['status']) != 'pass' and not failures:
        failures.append({'kind': 'post-check-incomplete', 'detail': f"post-check outcome is {report.get('outcome', report['status'])}"})
    digest = {"status": "fail" if failures else "pass", "failures": failures,
              "dispositions": fold_report["dispositions"],
              "appliedWithAlternative": review_result.applied_with_alternative(review["view"], applied_ids),
              "foldCheck": report.get("foldReport"),
              "report": str(out_dir / "post_check.json"),
              "independentVerdictPath": fold_report.get("independentVerdictPath"),
              "authors": [{"label": spec["label"], "subject": str(job["subject"].root), "digest": job["digest"],
                           "exit": (fold["results"].get(spec["label"]) or {}).get("exit")}
                          for job, spec in fold["authors"]]}
    if "reviewerSelection" in review:
        digest["reviewerSelection"] = review["reviewerSelection"]
    with (out_dir / "fold_digest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(digest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return digest


def publish_child_checks(checked, results, out_dir, *, report=None):
    if report is None:
        report = json.loads(Path(checked['verification']['report']).read_text(encoding='utf-8'))
    for label, result in (results or {}).items():
        record = result.get('record')
        if not record:
            continue
        rows = [row for row in checked['claims'] if row.get('lens') == label]
        keys = [row.get('sourceKey') or row['id'] for row in rows]
        verdicts = {key: report['verdicts'][key] for key in keys}
        counts = {v: sum(row['verdict'] == v for row in verdicts.values()) for v in sidecar_completion.VERDICTS}
        subset = dict(report, verdicts=verdicts, counts=counts)
        if report['status'] != 'unavailable':
            subset['status'] = 'fail' if counts['UNVERIFIED'] or counts['UNCHECKED'] or report.get('errors') else 'pass'
        path = str(record) + '.post_check.json'
        post_check._write(path, subset)
        text_path = dispatch_result.deliverable_path(out_dir, label)
        text = text_path.read_text(encoding='utf-8') if text_path.is_file() else None
        sidecar_completion.publish(rows, path, str(record) + '.claims.json', result=text)


def publish_review_children(review, checked, results, out_dir, root, fold):
    report = json.loads(Path(checked['verification']['report']).read_text(encoding='utf-8'))
    by_source = {row['id']: row for row in checked['claims']}
    rows, verdicts = [], {}
    for source in review['sources']:
        original = by_source[source['id']]
        index = source['index'] + 1
        key = f"{source['seat']}#{index}"
        rows.append(dict(source['finding'], id=source['id'], lens=source['seat'], index=index,
                         sourceKey=key, __key=key, root=original.get('root', str(root))))
        verdicts[key] = report['verdicts'][source['id']]
    expanded = dict(report, verdicts=verdicts)
    publish_child_checks({'claims': rows}, results, out_dir, report=expanded)


def close_claims(args, out_dir, *, results=None):
    rows, errors = [], []
    root = args.root or REPO
    for label, text in post_check._sidecar_results(out_dir):
        # An out dir can hold several fan-outs; another run's jobs are not this close's to judge.
        if results is not None and label not in results:
            continue
        found, bad = sidecar_completion.source_rows(label, text)
        rows.extend(found)
        errors.extend(bad)
        if text is None:
            errors.append(f'{label}: producing job has no result')
    if not rows and not errors:
        return 0
    for row in rows:
        row['root'] = str(Path(root).resolve())
    source = out_dir / 'claims.input.json'
    report_path = out_dir / 'post_check_claims.json'
    post_check._write(source, {'claims': sidecar_completion.checker_rows(rows)})
    post_check.main(['claims', '--result', str(source), '--root', str(root), '--out', str(report_path)])
    checked = sidecar_completion.publish(rows, report_path, out_dir / 'claims.json', errors=errors)
    publish_child_checks(checked, results, out_dir)
    return 0 if checked['verification']['status'] == 'pass' else 1


def close_review(args, out_dir, *, independent=None, expect_total=None):
    """Run `post_check.review` over this run's `review.json` (and `fold_jobs.json`), writing `post_check.json`."""
    argv = ["review", "--sidecar-dir", str(out_dir), "--out", str(out_dir / "post_check.json")]
    for flag, value in (("--stage", args.ledger_stage), ("--subject", args.ledger_subject),
                        ("--roster", args.ledger_roster), ("--scope-json", args.ledger_scope_json),
                        ("--task", args.ledger_task), ("--premise-commits", args.ledger_premise_commits),
                        ("--head", args.ledger_head), ("--blobs", args.ledger_blobs)):
        if value:
            argv += [flag, value]
    if args.no_new_decision:
        argv.append("--no-new-decision")
    if expect_total is not None:
        argv += ['--expect-total', str(expect_total)]
    if getattr(args, 'root', None):
        argv += ['--root', args.root]
    code = post_check.main(argv, independent=independent)
    if code == 2:
        raise fold_check.Unusable("post_check refused the review (its reason is printed above)")
    return code


def refold_dir(out_dir):
    """Next unused fold-only attempt: refold, refold.attempt2, ...; prior bytes never move."""
    out_dir = Path(out_dir)
    attempt = 1
    while True:
        candidate = out_dir / ("refold" if attempt == 1 else f"refold.attempt{attempt}")
        if not candidate.exists():
            return candidate
        attempt += 1


def build_parser():
    ap = argparse.ArgumentParser(description="Fan a job list across sidecar launchers.")
    ap.add_argument("jobs", nargs="?", help="path to the jobs JSON file")
    ap.add_argument("--detach", action="store_true",
                    help="validate here, then run the fan-out in a detached driver and return: a capped or "
                         "killed caller shell then stops neither queued jobs nor the <label>.md writes")
    ap.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL,
                    help=f"concurrent child launchers (default {DEFAULT_MAX_PARALLEL})")
    ap.add_argument("--out-dir", default=None,
                    help="where per-job stdout/record/stream land (default: beside the jobs file)")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan and print every argv, spend nothing")
    ap.add_argument("--authorize", action="store_true",
                    help="pass -A to every job, overriding the band gates (states the spend)")
    ap.add_argument("--compare", action="store_true",
                    help="these arms will be COMPARED (ladder evidence, pin A/B): refuse to "
                         "dispatch unless every job reads a frozen detached worktree")
    ap.add_argument("--status", metavar="OUT_DIR", default=None,
                    help="print one line per job in OUT_DIR from its latest attempt; "
                         "dispatches nothing")
    ap.add_argument('--root', help='subject checkout for claims and non-folded review evidence')
    ap.add_argument("--review", action="store_true",
                    help="consolidate schema-checked review seats into review.json")
    ap.add_argument("--refold", action="store_true",
                    help="fold and post-check saved OUT_DIR/review.json, dispatching no seat; "
                         "preserve prior artifacts in place and write under refold[.attemptN]")
    ap.add_argument("--review-artifact", metavar="PATH",
                    help="read a prior review.json instead of dispatching")
    ap.add_argument("--select", metavar="ID",
                    help="return one F-id source, or one M-id view row with its contributors, "
                         "from --review-artifact")
    ap.add_argument("--fold", metavar="TEMPLATE",
                    help="with --review: one author job per affected --fold-subject follows TEMPLATE "
                         f"(default {FOLD_TEMPLATE} once any --fold-* flag is given); "
                         "post_check.py decides fold_digest.json")
    ap.add_argument("--fold-alias", help="registry alias for the fold author")
    ap.add_argument("--fold-effort", help="effort rung for the fold author")
    ap.add_argument("--fold-plan", help="paying plan for the fold author (default: the one plan every seat pays)")
    ap.add_argument("--fold-subject", action="append", default=[], metavar="ROOT::MANIFEST::FILE[,FILE...]",
                    help="a subject a finding may fold into: the checkout the author edits, the snapshot manifest "
                         "`post_check.py snapshot` wrote, and the files findings route to; an optional fourth "
                         "`::NEW[,NEW...]` field declares files the author may create (repeat for split subjects)")
    ap.add_argument("--plan", action="append", default=[], help=argparse.SUPPRESS)
    ap.add_argument("--ledger-stage", help="with --review: the review_ledger stage the post-check records under")
    ap.add_argument("--ledger-subject", help="with --review: the ledger subject (session|pr|plan|doc:<id>)")
    ap.add_argument("--ledger-roster", help="with --review: the roster file the post-check records and plans from")
    ap.add_argument("--ledger-scope-json", help="with --review: the scope file `review_ledger.py` record and plan read")
    ap.add_argument("--ledger-premise-commits", help="with --review: frozen audit commit list passed unchanged to record")
    ap.add_argument("--ledger-head", help="with --review: frozen reviewed commit passed unchanged to record")
    ap.add_argument("--ledger-blobs", help="with --review: frozen reviewed blob map passed unchanged to record")
    ap.add_argument("--ledger-task", help="with --ledger-*: the task id every ledger row carries")
    ap.add_argument("--no-new-decision", action="store_true",
                    help="with --ledger-*: record each row with noNewDecision (the caller's word, auditable)")
    return ap


def doctrine_refusal(argv):
    """The dispatch-doctrine deny for this call, else None. A detached driver's launchers would print it to a
    log the caller never reads, so `--detach` surfaces it in the foreground, where the caller's own Bash call
    returns it. The launchers still run their own check."""
    sys.path.insert(0, str(ROOT / "hooks"))
    try:
        import sidecar_dispatch_context
        return sidecar_dispatch_context.fanout_doctrine_refusal(
            [a for a in argv if a != "--detach"], os.getcwd())
    except Exception:
        return None  # the check could not run; each launcher still runs its own


def launcher_fit_refusal(argv):
    """The tier-fit refusal every job's launcher would print, else None. A job's tier (its own, else its shape's) is one its
    pin must serve (`hooks/_dispatch_checks.py` `tier_fit`); the PreToolUse hook cannot read a jobs file the same call writes,
    and a detached launcher would print the refusal into a `.err` the caller never reads. Run before anything
    spawns or detaches. `hooks/sidecar_dispatch_context.py` owns the check and its text."""
    sys.path.insert(0, str(ROOT / "hooks"))
    try:
        import sidecar_dispatch_context
    except Exception as err:
        return ("Sidecar launch not checked: the tier check could not load (sidecar_dispatch_context: "
                f"{type(err).__name__}: {err}). Fix the import before launching.")
    try:
        return sidecar_dispatch_context.fanout_fit_refusal([a for a in argv if a != "--detach"], os.getcwd())
    except Exception:
        return None  # the check could not run; each launcher still runs its own


def fit_facts(jobs):
    """Each pinned job's `dispatch_size.fit_lines` fact: its whole reading against the point it compacts at.
    Advisory, printed beside the tier-fit check so a dispatcher who skipped the preflight still sees it; a spec it
    cannot size prints nothing."""
    try:
        import dispatch_size
        data = model_registry.load()
        sized = []
        for job in jobs:
            if isinstance(job, dict) and job.get("rows"):
                # The tightest listed window: a reading that fits it fits every row the job may run.
                usable = {alias: dispatch_size.fit_tokens(alias, 0, data, job)["usable"] for alias in job["rows"]}
                known = {alias: u for alias, u in usable.items() if u}
                alias = min(known, key=known.get) if known else next(iter(job["rows"]))
                job = {**{k: v for k, v in job.items() if k != "rows"}, "alias": alias, "effort": job["rows"][alias]}
            sized.append(job)
        return dispatch_size.fit_lines(dispatch_size.dispatch_size(sized, os.getcwd()))
    except Exception:
        return []


def spare_facts(jobs, data, now=None, read=None):
    """One line per job listing a single row: `_dispatch_checks.spare_plan_fact`'s fact for the job's tier and route,
    when another plan serving the tier projects unused quota. A job listing several rows has already weighed those
    plans, so it gets none. Facts only; a job it cannot read gets no line."""
    checks = _dispatch_checks_module()
    out = []
    for job in jobs:
        try:
            job = normalize_job(job, data)
            rows = listed_rows(job)
            if len(rows) != 1:
                continue
            tier = checks.job_tier(job.get("shape"), job.get("tier"))[0]
            transport = job.get("transport") or model_registry.resolve(rows[0][0], data)["transport"]
            fact = checks.spare_plan_fact(tier, transport, data, now, read)
        except Exception:
            continue
        if fact:
            out.append(f"spare plan: `{job.get('label')}` runs {rows[0][0]} on {transport}; {fact}")
    return out


def detach_driver(argv, jobs, out_dir, claim=None, log_path=None):
    """Rerun this fan-out without --detach as a process detached from the caller, and return 0.

    The caller's shell may be capped or killed (a backgrounded Bash call ends at the tool's limit);
    the driver it started would die with it, stranding queued jobs and the `<label>.md` writes. The
    driver keeps the caller's cwd, so every relative path resolves as it did here; its stdout, the
    summary, goes to `<out-dir>/fanout-driver.log`. The session watcher wakes the caller."""
    sys.path.insert(0, str(ROOT / "hooks"))
    from _bounded_run import detach_options
    from watch_records import ARM_CALL, ARM_COMMAND
    child, swapped = [sys.executable, str(Path(__file__).resolve())], False
    for token in argv:
        if token == "--detach":
            continue
        if token == jobs and not swapped:
            token, swapped = str(Path(jobs).resolve()), True
        child.append(token)
    log = Path(log_path) if log_path else Path(out_dir) / "fanout-driver.log"
    began = time.time()
    with open(log, "ab") as fh:
        env = dict(os.environ, SIDECAR_REVIEW_CLAIM=claim) if claim else None
        proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                cwd=os.getcwd(), close_fds=True, **detach_options(env))
    _LAUNCH["driver"] = (proc, began)
    print(f"fan-out detached: driver pid {proc.pid}; its summary goes to {_posix(log)}, and each job still "
          f"writes {_posix(out_dir)}/<label>.md. Arm the session watcher {ARM_CALL}: "
          f"{ARM_COMMAND}. Started any other way "
          f"(foreground, &, nohup, disown) it never wakes you and blocks a correct arm.")
    return 0


# Admission is a local check (gates, a capacity read), so the bound is seconds; a slow model cannot hold it.
ADMIT_WAIT_SEC = float(os.environ.get("SIDECAR_FANOUT_ADMIT_WAIT_SEC", "60"))


def _fanout_records(out_dir, since):
    """label -> current record path, from the newest inventory a driver started since `since` wrote."""
    newest = None
    for path in Path(out_dir).glob("fanout-*.inventory.json"):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if stamp >= since - 1 and (newest is None or stamp > newest[0]):
            newest = (stamp, path)
    if newest is None:
        return {}
    try:
        with open(newest[1], encoding="utf-8") as fh:
            return {job["label"]: job["recordPath"] for job in json.load(fh).get("jobs", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _refusal_line(record):
    """The launcher's refusal line from `<record>.err`: its first `REFUSING` line, else its first line."""
    try:
        with open(record + ".err", encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return "(no .err was written)"
    return next((ln for ln in lines if "REFUSING" in ln), lines[0] if lines else "(.err is empty)")


def _admission_state(record, stream, since):
    """(state, exit code) for one detached attempt: `refused` (exited, no model call), `admitted` (the stream
    was written since the launch and the process lives or already exited), `starting` (launched, past no gate
    yet, or ended with no exit written), or `unstarted` (the launcher has not claimed the record)."""
    code = None
    try:
        with open(record + ".exit", encoding="utf-8", errors="replace") as fh:
            code = int(fh.read().strip())
    except (OSError, ValueError):
        pass
    written = stream_written(stream, since)
    if code is not None:
        return ("admitted" if written else "refused"), code
    pid = _detached_pid(record)
    if written and pid is not None and _pid_alive(pid):
        return "admitted", None
    return ("starting" if pid is not None else "unstarted"), None


def await_admission(driver, out_dir, planned, fallbacks, max_parallel, since, wait=None, poll=None, settle=2.0):
    """Print, and return as {label: outcome}, whether each job of the first wave was admitted or refused.

    Reads the detached attempts' own files, so no launcher changes: `refused` is an `.exit` with no `-P` stream
    written since the launch (see `stream_written`), `admitted` a stream with a live pid. A refusal the driver can
    still move (a plan-refusal exit and a spare plan in `fallbacks`) waits for the inventory's new record path,
    then prints `moved to <plan>` and judges the new attempt. A job the driver has not started once every started
    job is decided is `queued`; a job still undecided at the bound is `undecided`, never `admitted`."""
    wait = ADMIT_WAIT_SEC if wait is None else wait
    poll = 0.25 if poll is None else poll
    argvs = {label: argv for label, argv, _ in planned}
    outcome, seen, attempt, pending = {}, {}, {label: 0 for label in argvs}, {}
    deadline, idle_since = time.time() + wait, None

    def plan_of(label):
        step = attempt[label]
        if step:
            return fallbacks[label][step - 1][0]
        return (launcher_of(argvs[label]) or "?").replace("_", "-")

    def flag(label, name):
        step = attempt[label]
        if step and len(fallbacks[label][step - 1]) > 2:
            return fallbacks[label][step - 1][2 if name == "-m" else 3] or "?"
        argv = argvs[label]
        return argv[argv.index(name) + 1] if name in argv[:-1] else "?"

    def say(label, text, result):
        print(f"[fanout] {label}: {text}", flush=True)
        outcome[label] = result

    def stream_of(label):
        return progress_of(argvs[label])

    while True:
        records = _fanout_records(out_dir, since)
        states = {}
        for label in argvs:
            if label in outcome:
                continue
            record = records.get(label)
            if record is None:
                states[label] = "unstarted"
                continue
            if label in seen and record != seen[label]:
                before = plan_of(label)
                attempt[label] += 1
                refusal = f"refused on {before}, nothing ran: {pending.pop(label)}; " if label in pending else ""
                print(f"[fanout] {label}: {refusal}moved to {plan_of(label)}", flush=True)
            seen[label] = record
            state, code = _admission_state(record, stream_of(label), since)
            if state == "refused":
                line = _refusal_line(record)
                if code in PLAN_REFUSAL_EXITS and len(fallbacks.get(label) or []) > attempt[label]:
                    pending[label] = line
                    states[label] = "moving"
                    continue
                say(label, f"refused on {plan_of(label)}, nothing ran: {line}", "refused")
            elif state == "admitted":
                note = f"; it already exited {code}" if code is not None else ""
                say(label, f"admitted on {plan_of(label)} ({flag(label, '-m')}, {flag(label, '-e')}){note}", "admitted")
            else:
                states[label] = state
        left = [label for label in argvs if label not in outcome]
        if not left:
            return outcome
        status = driver.poll()
        if status is not None:
            for label in left:
                say(label, f"undecided, the driver exited {status} before deciding it (see its log)", "undecided")
            return outcome
        waiting = [label for label in left if states.get(label) != "unstarted"]
        if outcome and not waiting:
            idle_since = idle_since or time.time()
            if time.time() - idle_since >= settle:
                for label in left:
                    say(label, f"queued, not launched yet (waits on --max-parallel {max_parallel}, its plan's room "
                               f"or memory headroom); the session watcher reports it", "queued")
                return outcome
        else:
            idle_since = None
        if time.time() >= deadline:
            for label in left:
                if label in pending:
                    say(label, f"refused on {plan_of(label)}, nothing ran: {pending[label]}; the driver has not "
                               f"moved it to its next plan within {wait:g}s", "refused")
                else:
                    say(label, f"undecided after {wait:g}s ({states.get(label)}: its launcher has not passed "
                               f"every pre-spend check); the session watcher reports it", "undecided")
            return outcome
        time.sleep(poll)


REFUSED_FILE = "launch-refused.txt"
_LAUNCH = {"inventory": False, "out_dir": None}


class _Tee:
    """Passes writes through to `stream` and keeps the head of them for `launch-refused.txt`."""
    LIMIT = 20000

    def __init__(self, stream):
        self.stream, self.kept = stream, []
        self.size = 0

    def write(self, text):
        if self.size < self.LIMIT:
            self.kept.append(text)
            self.size += len(text)
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _called_out_dir(argv):
    for i, token in enumerate(argv):
        if token == "--out-dir" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if token.startswith("--out-dir="):
            return Path(token.split("=", 1)[1]).resolve()
    return None


def _clear_refused():
    out_dir = _LAUNCH["out_dir"]
    if out_dir is not None:
        (out_dir / REFUSED_FILE).unlink(missing_ok=True)


def _write_refused(out_dir, code, tees):
    out_dir = _LAUNCH["out_dir"] or out_dir
    if out_dir is None:
        return
    body = "".join("".join(tee.kept) for tee in tees)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / REFUSED_FILE).write_text(f"launch refused (exit {code}) at {stamp}\n{body}",
                                            encoding="utf-8", newline="\n")
    except OSError:
        pass


def main(argv=None):
    """Run the fan-out; an exit before an inventory exists also lands its text in `<out-dir>/launch-refused.txt`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _LAUNCH.update(inventory=False, out_dir=None)
    tees = (_Tee(sys.stdout), _Tee(sys.stderr))
    real = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = tees
    exit_raised = None
    try:
        code = _main(argv)
    except SystemExit as err:
        exit_raised, code = err, err.code if isinstance(err.code, int) else (0 if err.code is None else 1)
    finally:
        sys.stdout, sys.stderr = real
    if code and not _LAUNCH["inventory"]:
        _write_refused(_called_out_dir(argv), code, tees)
    if exit_raised is not None:
        raise exit_raised
    return code


def _main(argv):
    ap = build_parser()
    retired =[flag for flag in (argv if argv is not None else sys.argv[1:])
               if flag.split("=", 1)[0] in ("--merge-alias", "--merge-effort")]
    if retired:
        print(f"{', '.join(retired)} retired: --review now groups exact same-file, same-claim "
              "findings itself with no merge sidecar; drop the flag", file=sys.stderr)
        return 2
    args = ap.parse_args(argv)
    if args.refold:
        if args.review_artifact or args.select or args.compare:
            print("--refold does not combine with --review-artifact, --select or --compare", file=sys.stderr)
            return 2
        args.review = True
    # SC_COMPARISON is the launchers' proof of an observable comparison: only --compare sets it, so an
    # inherited value never admits an avoided rung.
    if args.compare:
        os.environ["SC_COMPARISON"] = "fanout --compare"
    else:
        os.environ.pop("SC_COMPARISON", None)

    if args.review_artifact:
        if args.jobs or not args.select:
            print("--review-artifact needs --select <F-or-M-id> and no jobs file", file=sys.stderr)
            return 2
        try:
            artifact = json.loads(Path(args.review_artifact).read_text(encoding="utf-8"))
            sources = artifact["sources"]
            if args.select.startswith("M"):
                row = next(row for row in artifact["view"] if row.get("id") == args.select)
                by_id = {source["id"]: source for source in sources}
                selected = {"id": row["id"], "finding": row,
                            "sources": [by_id[item] for item in row["merged_from"]]}
            else:
                selected = next(row for row in sources if row.get("id") == args.select)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as err:
            print(f"review source {args.select!r} unavailable: {err}", file=sys.stderr)
            return 2
        print(json.dumps(selected, ensure_ascii=False))
        return 0
    if args.select:
        print("--select needs --review-artifact", file=sys.stderr)
        return 2
    if args.status:
        return print_status(args.status)
    if args.review and args.compare:
        print("--review and --compare have different input contracts", file=sys.stderr)
        return 2
    if args.plan:
        print("--plan is retired: pass --fold-subject <root>::<snapshot manifest>::<file>[,<file>...] "
              "(take the snapshot first: python3 .claude/tools/post_check.py snapshot --root <root> "
              "--files <file>... --out <manifest>)", file=sys.stderr)
        return 2
    ledger_flags = (args.ledger_stage, args.ledger_subject, args.ledger_roster)
    if (any(ledger_flags) or args.ledger_scope_json or args.ledger_task or args.no_new_decision
            or args.ledger_premise_commits or args.ledger_head or args.ledger_blobs):
        if not args.review or not all(ledger_flags):
            print("--ledger-stage, --ledger-subject and --ledger-roster go together and need --review "
                  "(all other --ledger-* flags and --no-new-decision need them)", file=sys.stderr)
            return 2
        if not (args.ledger_scope_json or args.ledger_blobs or args.fold_subject):
            print("--ledger-* needs the reviewed files: pass --ledger-blobs (path to blob sha), "
                  "--ledger-scope-json or --fold-subject, or the post-check records no row after every seat has run",
                  file=sys.stderr)
            return 2
    fold_args = args.fold or args.fold_alias or args.fold_effort or args.fold_subject or args.fold_plan or args.refold
    if fold_args:
        args.fold = args.fold or FOLD_TEMPLATE
        missing = [name for name, value in (("--review", args.review),
                                            ("--fold-alias", args.fold_alias),
                                            ("--fold-effort", args.fold_effort),
                                            ("--fold-subject", args.fold_subject)) if not value]
        unreadable = [path for path in [args.fold or ""] if path and not Path(path).is_file()]
        if missing or unreadable:
            print("--fold needs " + ", ".join(missing) if missing else
                  "--fold template is not a file: " + ", ".join(unreadable), file=sys.stderr)
            return 2
        try:
            subjects = [fold_check.parse_subject_spec(spec) for spec in args.fold_subject]
        except fold_check.Unusable as err:
            print(f"--fold-subject: {err}", file=sys.stderr)
            return 2
        claimed = [fold_check.path_key(item, subject.root) for subject in subjects for item in subject.files]
        if len(set(claimed)) != len(claimed):
            print("--fold-subject repeats a file", file=sys.stderr)
            return 2
    if not args.jobs:
        print("a jobs file is required unless --status is given", file=sys.stderr)
        return 2
    if args.max_parallel < 1:
        print("--max-parallel must be at least 1", file=sys.stderr)
        return 2

    try:
        jobs, shared_context, spill_dir = load_jobs(args.jobs)
        lanes = job_lanes(jobs)
        groups = wake_groups(jobs)
        if lanes and args.review:
            raise JobError("chains do not combine with --review: a review consolidates independent seats")
        selection = None
        if args.review:
            try:
                document = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise JobError(f"cannot read selection source {args.jobs}: {exc}") from exc
            if isinstance(document, dict) and "reviewerSelection" in document:
                selection = document["reviewerSelection"]
                if not isinstance(selection, dict):
                    raise JobError("jobs reviewerSelection must be an object")
        data = model_registry.load()
        jobs = [normalize_job(j, data, shared_context) for j in jobs]
        if spill_dir:
            spill_dir = Path(spill_dir) if Path(spill_dir).is_absolute() else REPO / spill_dir
            if args.out_dir and Path(args.out_dir).resolve() != spill_dir.resolve():
                raise JobError(f"--out-dir {args.out_dir} and the jobs file's spillDir {_posix(spill_dir)} "
                               f"name different directories. Drop one of the two.")
    except (JobError, model_registry.RegistryError) as err:
        print(str(err), file=sys.stderr)
        return 2
    # resolved against the caller's cwd: children run with cwd=REPO, where a relative path names another directory
    out_dir = Path(sidecar_argv.fanout_out_dir(args.jobs, args.out_dir, str(spill_dir) if spill_dir else None,
                                               str(REPO))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    _LAUNCH["out_dir"] = out_dir
    saved_review = None
    if args.refold:
        try:
            saved_review = json.loads((out_dir / "review.json").read_text(encoding="utf-8"))
            if (saved_review.get("status") != "complete" or not isinstance(saved_review.get("view"), list)
                    or not isinstance(saved_review.get("sources"), list)):
                raise ValueError("refold requires a complete saved review with view and sources")
        except (OSError, ValueError, TypeError, AttributeError) as err:
            print(f"--refold: {err}", file=sys.stderr)
            return 2
        out_dir = refold_dir(out_dir)

    try:
        if args.review and not args.refold:
            if any(isinstance(j, dict) and "rows" in j for j in jobs):
                raise JobError("--review seats name one `alias` + `effort`; `rows` is for flat and chained fan-outs")
            args.root = review_result.review_root(jobs, args.root)
            problems = review_result.launch_problems(jobs, root=args.root, roster=args.ledger_roster,
                                                     blobs=args.ledger_blobs)
            if problems:
                raise JobError("review launch refused before any seat ran:\n  " + "\n  ".join(problems))
            review_result.validate_review_jobs(jobs)
            review_paths = ("review.json", "review_sources.json", "review_verdicts.json",
                            "fold_digest.json", "post_check.json", "fold_jobs.json")
            reserved = [job.get("label") for job in jobs
                        if str(job.get("label", "")).startswith(FOLD_LABEL.format(""))]
            if reserved and args.fold:
                raise JobError("seat labels collide with the fold author labels: "
                               + ", ".join(reserved))
            occupied = [name for name in review_paths if (out_dir / name).exists()]
            claim = out_dir / REVIEW_CLAIM
            # review.json lands only at a run's end, so a launch while an earlier review still runs
            # in this out-dir sees none of review_paths; the claim, made at launch, is what it sees.
            if claim.exists() and claim.read_text(encoding="utf-8") != os.environ.get("SIDECAR_REVIEW_CLAIM"):
                occupied.append(REVIEW_CLAIM)
            if occupied:
                raise JobError("review artifacts already exist (" + ", ".join(occupied)
                               + "); use a new --out-dir to preserve their evidence")
    except (JobError, ValueError) as err:
        print(str(err), file=sys.stderr)
        return 2

    try:
        FALLBACKS.clear()
        PLACED.clear()
        FULL_WINDOW.clear()
        SLOTS.clear()
        _LIVE.clear()
        refusal = None if args.refold else admission_refusal(jobs, data)
        if refusal:
            raise JobError(refusal)
        planned = [] if args.refold else [
            plan_job(j, data, out_dir, args.authorize, stage=args.ledger_stage) for j in jobs]
        if args.fold and not args.refold:
            plan_job(fold_probe(args, jobs, data), data, out_dir, args.authorize)
    except (JobError, model_registry.RegistryError) as err:
        print(str(err), file=sys.stderr)
        return 2

    fit = launcher_fit_refusal(argv if argv is not None else sys.argv[1:])
    if fit:
        print(fit)
        return 2
    for fact in fit_facts(jobs):
        print(fact)
    if not args.refold:
        for fact in spare_facts(jobs, data):
            print(fact)

    seen = set()
    for label, _, _ in planned:
        # `<label>.md` sanitizes like dispatch.js, so two labels can share one deliverable file.
        key = dispatch_result.deliverable_path(out_dir, label).name.lower()
        if label in seen or key in seen:
            print(f"duplicate label {label!r}: outputs, ledger rows or <label>.md deliverables "
                  f"would overwrite each other", file=sys.stderr)
            return 2
        seen.update((label, key))
    if lanes:
        planned = add_chain_handoff(add_lane_guard(planned, jobs, lanes, out_dir), lanes, out_dir)
    else:
        planned = add_concurrency_guard(planned, jobs, args.max_parallel, out_dir)

    if args.compare:
        # Refuse BEFORE spending: an unfrozen comparison produces confident numbers that measure
        # nothing, which is worse than no run at all.
        bad = []
        for label, a, _out in planned:
            why = frozen_input_error(workdir_of(a))
            if why:
                bad.append(f"  {label}: {why}")
        if bad:
            print("--compare: these arms would not read a frozen input, so the comparison would "
                  "measure nothing:\n" + "\n".join(bad)
                  + "\n\nFreeze it:  git worktree add --detach .claude/worktrees/<slug> HEAD"
                  + "\n(orchestration section 0 -- arms that will be compared read a frozen input)",
                  file=sys.stderr)
            return 2

        # Each arm being frozen is not the same as the arms sharing ONE input. Two detached
        # worktrees at different commits each pass the check above, and the run then measures the
        # code difference between them as if it were a difference between models -- the exact
        # failure `--compare` exists to prevent, arrived at from the other side.
        prints = {}
        for label, a, _out in planned:
            wd, ins = workdir_of(a), inputs_of(a)
            prints.setdefault(freeze_fingerprint(wd, ins), []).append(
                "%s\n      workdir %s\n      reads   %s" % (label, wd, ", ".join(ins) or "(none)"))
        if len(prints) > 1:
            listed = "\n".join("  %s\n    %s" % (fp, "\n    ".join(labels))
                               for fp, labels in sorted(prints.items()))
            print("--compare: the arms do NOT share one input, so any difference between them is "
                  "confounded with the difference between their inputs:\n" + listed
                  + "\n\nEvery arm needs the SAME detached worktree AND the same prompt, context "
                    "and schema files. The groups above differ in at least one of those.",
                  file=sys.stderr)
            return 2

    if args.dry_run:
        _clear_refused()
        if args.refold:
            try:
                out_dir.mkdir(parents=True, exist_ok=False)
                run_review_fold(saved_review, args, data, out_dir, jobs)
            except (OSError, ValueError, JobError, fold_check.Unusable) as err:
                print(f"--refold: {err}", file=sys.stderr)
                return 2
        else:
            fanout_waves(planned, data)
            print_plan(planned, args.max_parallel)
        return 0

    duplicates = duplicate_launches(planned)
    if duplicates:
        print("DUPLICATE LAUNCH refused (exit %d): a live launch already runs these jobs; nothing was "
              "dispatched.\n%s\nStop the previous attempt's whole tree (fan-out driver, launchers, seat, "
              "servers), confirm none remain, then relaunch." % (DUPLICATE_LAUNCH_EXIT, duplicates), file=sys.stderr)
        return DUPLICATE_LAUNCH_EXIT

    if args.review and not args.refold and not os.environ.get("SIDECAR_REVIEW_CLAIM"):
        token = str(uuid.uuid4())
        try:
            with open(out_dir / REVIEW_CLAIM, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(token)
        except FileExistsError:
            print(f"review artifacts already exist ({REVIEW_CLAIM}): another review launch claimed "
                  f"{_posix(out_dir)}; use a new --out-dir to preserve their evidence", file=sys.stderr)
            return 2
    else:
        token = None
    _clear_refused()
    if args.detach:
        refusal = doctrine_refusal(argv if argv is not None else sys.argv[1:])
        if refusal:
            print(refusal)
            return 2
        extra = {"log_path": out_dir.with_name(out_dir.name + "-driver.log")} if args.refold else {}
        code = detach_driver(argv if argv is not None else sys.argv[1:], args.jobs, out_dir, claim=token, **extra)
        driver = _LAUNCH.pop("driver", None)
        if code == 0 and driver:
            await_admission(driver[0], out_dir, planned, dict(FALLBACKS), args.max_parallel, driver[1])
        return code
    if args.refold:
        out_dir.mkdir(parents=True, exist_ok=False)
    # Keep the three-argument call for a run with no extra stages or scheduling.
    extras = {k: v for k, v in (('fallbacks', dict(FALLBACKS)), ('lanes', lanes),
                                ('after_seats', bool(args.review)), ('wake_groups', groups),
                                ('waves', None if args.refold else fanout_waves(planned, data)))
              if v}
    results = {} if args.refold else run_jobs(planned, args.max_parallel, out_dir, **extras)
    text, code = summarize(planned, results, args.compare)
    if not args.review:
        text += resume_plan(jobs, results, out_dir, lanes)[1]
    if args.review:
        try:
            review = saved_review if args.refold else review_result.consolidate(jobs, results, out_dir, verify=False)
            if args.refold:
                post_check._write(out_dir / "review.json", review)
            if selection is not None and not args.refold:
                review["reviewerSelection"] = selection
                post_check._write(out_dir / "review.json", review)
        except (OSError, ValueError) as err:
            print(f"review consolidation failed: {err}", file=sys.stderr)
            return 1
        text += f"\nreview: {review['status']} — {out_dir / 'review.json'}"
        for entry in review["coverage"]:
            if entry["status"] == "failed":
                unread = entry.get("unread") or []
                text += (f"\nuncovered: {entry['label']} failed — "
                         + (f"unread {', '.join(unread)}" if unread else "a gap has no legal reason and the seat has no must-read list")
                         + "; fix the cause before any second run")
        if review["status"] != "complete":
            code = 1
        fold = None
        fold_error = None
        if args.fold and review["view"]:
            try:
                fold = run_review_fold(review, args, data, out_dir, jobs)
            except (OSError, ValueError, JobError, fold_check.Unusable) as err:
                fold_error = f"review fold failed before its check: {err}"
                print(fold_error, file=sys.stderr)
                code = 1
        try:
            checked = close_review(args, out_dir, independent=(fold or {}).get('independent'),
                                   expect_total=len(review['view']))
        except (OSError, ValueError, fold_check.Unusable) as err:
            print(f"post-check failed before its report: {err}", file=sys.stderr)
            for failure in (fold or {}).get("failures") or []:
                print(f"{failure['kind']}: {failure['detail']}", file=sys.stderr)
            return 1
        report_path = out_dir / "post_check.json"
        if fold_error:
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['status'] = 'fail'
            report.setdefault('errors', []).append({'kind': 'fold-precheck-failed', 'detail': fold_error})
            post_check._write(report_path, report)
        roots = {source: str(job['subject'].root) for job in (fold or {}).get('jobs', [])
                 for row in job['rows'] for source in fold_check._sources(row)}
        originals = [dict(source['finding'], id=source['id'],
                          root=roots.get(source['id'], str(args.root or REPO))) for source in review['sources']]
        applied = sidecar_completion.publish(originals, report_path, out_dir / 'claims.json',
                                             errors=[] if review['status'] == 'complete' else
                                             [f"review coverage is {review['status']}"])
        publish_review_children(review, applied, results, out_dir, args.root or REPO, fold)
        by_id = {row['id']: row for row in applied['claims']}
        for source in review['sources']:
            source['finding'] = by_id[source['id']]
        for view in review['view']:
            members = [by_id[key]['verdict'] for key in fold_check._sources(view)]
            verdict = next(v for v in ('UNVERIFIED', 'UNCHECKED', 'VERIFIED') if v in members)
            view.update(verdict=verdict, confidence='verified' if verdict == 'VERIFIED' else 'unverified')
        review['verification'] = applied['verification']
        review['verdicts'] = [{'id': row['id'], 'verdict': row['verdict']} for row in applied['claims']]
        review['verdictPath'] = str(report_path)
        review['quoteCounts'] = applied['verification']['counts']
        post_check._write(out_dir / 'review.json', review)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        outcome, counts = report.get('outcome', report['status']), report.get('counts') or {}
        text += (f"\npost-check: {outcome} — claims {counts.get('VERIFIED', 0)} verified, {counts.get('UNVERIFIED', 0)} "
                 f"unverified, {counts.get('UNCHECKED', 0)} unchecked; {len(report.get('held') or [])} held — {report_path}")
        if checked != 0 or outcome != 'pass':
            code = 1
        if fold is not None:
            digest = finish_fold(review, fold, report, out_dir)
            text += f"\nfold: {digest['status']} — {out_dir / 'fold_digest.json'}"
            if digest["status"] != "pass":
                code = 1
    else:
        if close_claims(args, out_dir, results=results) != 0:
            code = 1
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
