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
launch, before it spawned) likewise means nothing ran, so a job that named no plan moves to the next plan
with room. Exit 10 that arrives later, from the running job, is a usage limit hit mid-run: work was done
and the session lives in that plan's login, so no other plan can resume it. It is reported, and when a
run ends with such a job the tool writes `<out-dir>/resume-jobs.json` (the stopped job pinned to its plan
with `-r <session>`, plus the jobs its chain lane never ran) and prints the one command that runs it after
the reset. Other failed runs are REPORTED -- with their resume id when the launcher persisted one -- and
the caller decides.

THE PICK COUNTS LOAD. Room is a pace ratio, not capacity: a short window with a small share left ranks
high whenever its pace is fine. A job that names no plan therefore goes to the roomiest plan only while
that plan's tightest window still holds the jobs already on it (this process's earlier picks and named
plans, plus every live launcher of the plan in the process table, any session's) at `JOB_DRAIN_PCT` each
(`provider_bands.pick_key`). A plan that no longer fits ranks below every plan that does; only plan-quota
plans are candidates, so no pay-per-token route is added.

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
fields this route has no use for (`agentType`, `readOnly`, `tier`) are ignored. Per job:
    label       required  attribution key; becomes `-l` and names the output files
    mandates              required by --review; catalog or lens keys the seat reports in checked.mandates
                          (findings|clean|not-reached per key; per-key checked.basis text is the fallback)
    mustRead              --review: the subject files the seat must read (each must exist under the
                          job's workdir, else the root, or the launch refuses before any seat runs), `{"path", "lines"}` entries
                          (`lens_briefs.py must-read`); an entry whose lines its stream's Read calls do not
                          cover and no legal gap names fails the seat (commands/agents/review_agents.md
                          *Seat coverage contract*)
    alias       required  registry alias or id, e.g. "muse", "opus"
    promptFile  required  path to the prompt; NEVER argv (Windows caps argv near 8K)
    effort                vendor rung -> `-e`; refused unless the transport's vocabulary serves it. Also refused
                          when the row's registry `effort.avoid` lists it, unless this run is --compare (SC_COMPARISON)
    disclosure            bare|pointer|full -> `-D`
    shape                 any|survey|review|author|arm -> `-G`
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
                          through its own `launcher`; omitted, the live plan with the most room pays
                          (stated on stderr), and no plan with room refuses the job
    pick, spec            both -> `-Y <pick>,<spec>`, the pin basis (`orchestration` reference `slice_pins.md`)
    extraArgs             list, appended verbatim, for a flag this wrapper does not model

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

EXIT: 0 every job exited 0; under --review, coverage is complete and the post-check passed.
      1 a job failed, review coverage is partial (each failed seat prints an `uncovered:` line naming
        its unread entries; it is never re-dispatched), or the post-check failed.
      2 jobs, arguments or source IDs are unusable.
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


def legal_rungs(transport, data):
    """The effort rungs `-e` may name on `transport`: its registry effort vocabulary. [] = undeclared.

    The TRANSPORT's vocabulary, not a row's: the ladder cell owns a row's rung (the registry keeps
    none), and what a launcher can actually forward is a transport fact. Checking a row's own
    narrower list would refuse a rung this launcher would have run.
    """
    return model_registry.transport_effort_values(transport, data)


def plan_room(transport, data, read, now):
    return provider_bands._plan_room(transport, data, read, now)


# label -> the plans `plan_job` left a job to fall back on; `main` clears it before planning and hands it to run_jobs.
FALLBACKS = {}
# plan -> jobs this process has placed on it so far (named or picked); `main` clears it with FALLBACKS.
PLACED = {}
_LIVE = {}  # {"load": plan -> live launchers}: read once per process, so a 13-job pass costs one table read


def live_plan_load(table, data):
    """{plan: jobs live on it} from a process table: one per distinct job (its `-R` record, else its `-l` label
    and `-f` prompt) among the bash launchers. The plan is the one whose registered launcher the job was
    INVOKED through; a job's re-exec snapshots are named for the shared base script (`anthropic_sidecar.sh.N.sh`
    for every Anthropic plan), so they decide only a job whose invoked launcher is gone. A script two plans
    share names no plan and counts for none."""
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
        (snapshots if "sidecar-snapshots" in script.replace("\\", "/") else invoked)[key] = next(iter(plans))
    load = {}
    for plan in {**snapshots, **invoked}.values():
        load[plan] = load.get(plan, 0) + 1
    return load


def plan_load(data):
    """{plan: jobs on it}: the live launchers (any session's: the quota is shared) plus this process's own
    placements. An unreadable process table counts no live launcher, which leaves the pace ranking."""
    if "load" not in _LIVE:
        import reap
        table = reap.process_table()
        if not table:
            print("[fanout] WARN: process table unreadable; the pick counts only this run's own jobs",
                  file=sys.stderr)
        _LIVE["load"] = live_plan_load(table, data)
    load = dict(_LIVE["load"])
    for plan, n in PLACED.items():
        load[plan] = load.get(plan, 0) + n
    return load


def pick_plan(label, transport, plans, data, read=None, rest=None, quiet=False, load=None):
    """The paying plan for a job that names none: the route's only live plan, else the one with the most
    room (`provider_bands.rank_room`) among those whose tightest window still holds the jobs already on
    them (`provider_bands.pick_key`; `load` is {plan: jobs}, default `plan_load`). No plan with room refuses
    the job, naming each plan's state.
    `read` is the capacity reader (default `provider_bands.read_capacity`); a caller with a deadline passes
    its own. A list `rest` receives the other plans with room, roomiest first: the launch fallbacks. `quiet`
    drops the stderr note, for a caller whose stdout and stderr are a channel (a hook)."""
    if len(plans) <= 1:
        return plans[0] if plans else transport
    now = time.time()
    read = read or provider_bands.read_capacity
    rows = [plan_room(name, data, read, now) for name in plans]
    fit = [row for row in rows if row.get("room") is not None]
    if not fit:
        raise JobError(f"job {label}: no plan of {transport} has room -- "
                       + "; ".join(f"{row['transport']}: {row.get('why')}" for row in rows)
                       + ". Name a `plan` only once one has room, or pick a row on another route.")
    load = plan_load(data) if load is None else load
    fit.sort(key=lambda row: provider_bands.pick_key(row, load.get(row["transport"], 0)), reverse=True)
    best = fit[0]
    if rest is not None:
        rest.extend(row["transport"] for row in fit[1:])
    if not quiet:
        print(f"[fanout] {label}: plan {best['transport']} (room {best['room']:.2f}"
              + (f", {load[best['transport']]} jobs on it" if load.get(best["transport"]) else "") + "; "
              + ", ".join(f"{row['transport']} {row['room']:.2f}" if row.get("room") is not None
                          else f"{row['transport']} {row.get('why')}" for row in rows if row is not best)
              + ")", file=sys.stderr)
    return best["transport"]


def admission_refusal(jobs, data, read=None, now=None):
    """The one capacity refusal for `jobs` (normalized), or None when every job may be admitted now.

    A job naming a plan is refused when that plan has no room (a pinned plan never falls back). A job
    naming none is refused only when no plan of its route has room; a route with one plan reads no capacity
    (`pick_plan`'s rule), so its unpinned job keeps its launcher's gate. A plan with no reading, a stale or
    unreadable one, decides nothing: the launcher's own gate still runs. One message names every refused
    job, grouped by reason, so one retry fixes them all.
    `read` is the capacity reader (default `provider_bands.read_capacity`); a caller with a deadline passes
    a cache-only one. `plan_job` and the PreToolUse hook both call this."""
    read = read or provider_bands.read_capacity
    now = time.time() if now is None else now
    seen = {}

    def reader(transport, table):
        seen[transport] = read(transport, table)
        return seen[transport]

    groups = {}
    for job in jobs:
        try:
            transport = model_registry.resolve(job["alias"], data)["transport"]
            plans = model_registry.plans_of(transport, data)
        except Exception:
            continue
        named = job.get("plan")
        candidates = [named] if named else plans
        if not candidates or any(name not in plans for name in candidates) or (not named and len(plans) == 1):
            continue
        states = []
        for name in candidates:
            row = plan_room(name, data, reader, now)
            why = "" if row.get("room") is not None else row.get("why") or ""
            if not why or why.startswith("unreadable"):
                break
            got = seen.get(name)
            entry = provider_bands.plan_entry(name, got, now) if isinstance(got, dict) else ""
            states.append(entry if entry and "band unknown" not in entry else f"{name}: {why}")
        else:
            reason = (f"plan {named} has no room -- {states[0]}" if named
                      else f"no plan of {transport} has room -- " + "; ".join(states))
            groups.setdefault(reason, []).append(str(job.get("label")))
    if not groups:
        return None
    lines = [f"  {'job' if len(labels) == 1 else 'jobs'} {', '.join(labels)}: {reason}"
             for reason, labels in groups.items()]
    return ("capacity admission refused, nothing started:\n" + "\n".join(lines)
            + "\nWait for the reset, or move the refused jobs to a plan with room.")


def plan_job(job, data, out_dir, authorize, stage=None):
    """One job -> (label, argv, stdout path). Validates everything BEFORE any process starts.

    `FALLBACKS[label]` becomes `[(plan, launcher), ...]` for a job that names no plan: the other plans with
    room, roomiest first, which `run_jobs` tries when the picked plan's launcher refuses.

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
    for name, val in (("label", label), ("alias", alias), ("promptFile", prompt)):
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
    # An excluded row is out of the set a dispatcher chooses from -- name the roster rather than
    # substituting one, because choosing the replacement needs the task's shape and the budget.
    if not model_registry.model_available(entry["id"], data):
        why = model_registry.unavailable_reasons(data).get(entry["id"], "excluded")
        roster = ", ".join(sorted(m["alias"] for m in model_registry.available_models(data)
                                  if m["transport"] == transport)) or "none"
        raise JobError(f"job {label}: {alias} is unavailable ({why}). "
                       f"Available on {transport}: {roster}. Re-select under the ladder.")

    effort = job.get("effort")
    legal = legal_rungs(transport, data)
    if effort and legal and effort not in legal:
        raise JobError(f"job {label}: effort {effort!r} is not in {transport}'s effort vocabulary. "
                       f"Legal rungs: {', '.join(legal)} (registry `transports.{transport}`; "
                       f"`model_registry.py effort-values {alias}` prints what this row forwards). "
                       f"Re-select under the ladder.")
    if effort in model_registry.avoided_rungs(entry) and not os.environ.get("SC_COMPARISON"):
        raise JobError(f"job {label}: effort {effort!r} is in {alias}'s registry effort.avoid: "
                       f"{model_registry.avoid_evidence(entry)} Pin the ladder's rung.")

    # `plan` names who pays: the route itself or a live plan whose `planOf` is the route. Its
    # launcher runs the job; the model vocabulary stays the route's. A job naming none pays with
    # the live plan holding the most room, never the route's default login by omission.
    plans = model_registry.plans_of(transport, data)
    refusal = admission_refusal([job], data)
    if refusal:
        raise JobError(refusal)
    rest = []
    plan = job.get("plan") or pick_plan(label, transport, plans, data, rest=rest)
    if plan not in plans:
        raise JobError(f"job {label}: plan {plan!r} does not pay for {transport}'s rows -- live plans "
                       f"of {transport}: {', '.join(plans)}. Drop `plan`, or name one of those.")
    launcher = (model_registry.transport_meta(plan, data) or {}).get("launcher")
    if not launcher:
        raise JobError(f"job {label}: plan {plan} registers no launcher -- cannot hop")
    PLACED[plan] = PLACED.get(plan, 0) + 1
    FALLBACKS.pop(label, None)
    spare = [(name, (model_registry.transport_meta(name, data) or {}).get("launcher")) for name in rest]
    if any(path for _name, path in spare):
        FALLBACKS[label] = [(name, path) for name, path in spare if path]

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
    for flag, key in (("-e", "effort"), ("-D", "disclosure"), ("-G", "shape"),
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
            refusal = tool_profiles.check_brief_files(
                builtins, [prompt] + list(job.get("contextFiles") or []), grammar="job",
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


def workdir_of(argv):
    """The `-d` operand: what the child actually reads, not what the job dict claimed."""
    try:
        return argv[argv.index("-d") + 1]
    except (ValueError, IndexError):
        return None


def progress_of(argv):
    """The `-P` operand: the live progress stream this job's launcher tees to, if any."""
    try:
        return argv[argv.index("-P") + 1]
    except (ValueError, IndexError):
        return None


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


def run_jobs(planned, max_parallel, out_dir, fallbacks=None, lanes=None, after_seats=False, resident=frozenset()):
    # A bounded pool: `max_parallel` workers draining a queue. The previous shape started one OS
    # thread per job and used the semaphore only to cap concurrent SUBPROCESSES, so a 200-job file
    # spawned 200 threads to run 4 at a time -- and N is caller-controlled from the jobs JSON.
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
                 # `workdir` lets the close tell a job writing in a worktree from one writing in its checkout.
                 "jobs": [dict({"label": label, "launchId": launch_id, "recordPath": os.path.abspath(record_path)},
                               **({"workdir": os.path.abspath(workdir_of(argv))} if workdir_of(argv) else {}))
                          for label, argv, _, launch_id, record_path in allocated]}
    try:
        with open(inventory_path, "x", encoding="utf-8") as fh:
            json.dump(inventory, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as err:
        raise RuntimeError(f"cannot persist sidecar inventory {inventory_path}: {err}") from err
    # A unit runs on one worker, its jobs in sequence: one job, or a chain lane (`lanes`: (name, labels) pairs).
    by_label = {job[0]: job for job in allocated}
    pending = [(name, [by_label[label] for label in labels if label in by_label]) for name, labels in lanes] \
        if lanes else [(None, [job]) for job in allocated]
    lock = threading.Lock()
    threads = []

    def save_inventory():
        tmp = inventory_path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(inventory, fh, separators=(',', ':'))
        os.replace(tmp, inventory_path)

    def one(label, argv, out_path, launch_id, record_path):
        # SIDECAR_FANOUT_ID marks siblings: jobs of one fan-out may share a brief, and are never
        # each other's duplicate launch (sidecar_common.sh sc_check_duplicate_launch).
        child_env = dict(os.environ, SIDECAR_LAUNCH_ID=launch_id, SIDECAR_FANOUT_ID=fanout_id,
                         SIDECAR_CHECK_OWNER=launch_id)
        # A job whose work keeps one subject resident compacts only at the model's full window (model_registry.anthropic_window).
        if label in resident:
            child_env["SIDECAR_RESIDENT_SUBJECT"] = "1"
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
            spare = list((fallbacks or {}).get(label) or []) if detached else []

            def rebind_record():
                nonlocal argv, record_path
                record_path = _posix(attempt_record_path(out_dir, label))
                at = argv.index('-R') + 1
                argv = argv[:at] + [record_path] + argv[at + 1:]
                with lock:
                    for job in inventory['jobs']:
                        if job['label'] == label:
                            job['recordPath'] = os.path.abspath(record_path)
                    save_inventory()

            def launch():
                with open(out_path, "w", encoding="utf-8") as so,                         open(err_path, "w", encoding="utf-8") as se:
                    return subprocess.call(argv, stdout=so, stderr=se, cwd=str(REPO), env=child_env)

            def child():
                nonlocal argv, died, retry, pre_spawn_refusal
                if retry and detached:
                    rebind_record()
                code = launch()
                # Only the launch's own pre-spawn refusal moves a job to its next plan: a detached launcher
                # exits 0 once it spawned, so a code here means nothing ran. The job named no plan (a named
                # one has no `spare`); a run that ended after spend is read below and never retried.
                while code in PLAN_REFUSAL_EXITS and spare:
                    plan, launcher = spare.pop(0)
                    print(f"[fanout] {label}: {LAUNCHER_EXITS[code]} (exit {code}), nothing ran; "
                          f"paying with plan {plan} instead", file=sys.stderr, flush=True)
                    argv = argv[:1] + [launcher] + argv[2:]
                    rebind_record()
                    code = launch()
                pre_spawn_refusal = detached and code in PLAN_REFUSAL_EXITS
                if detached and code == 0:
                    code, died = _wait_detached(record_path)
                    for source, target in ((record_path + '.out', out_path), (record_path + '.err', err_path)):
                        if os.path.isfile(source):
                            try:
                                shutil.copyfile(source, target)
                            except OSError:
                                pass
                retry = code == MEMORY_REFUSED
                return code
            try:
                code = supervised_launch(label, child, child_env)
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

    def lane_ended(lane, label, rest):
        """Record the unrun jobs of a lane that `label` ended, and take them off the inventory: nothing will
        write their records, so a watcher must not wait for them."""
        for later in rest:
            results[later[0]] = {"exit": None, "stdout": None, "stderr": None, "stream": None, "record": None,
                                 "error": f"not run: lane `{lane}` stopped after {label} did not finish"}
        with lock:
            gone = {later[0] for later in rest}
            inventory['jobs'] = [job for job in inventory['jobs'] if job['label'] not in gone]
            save_inventory()

    def worker():
        while True:
            with lock:
                if not pending:
                    return
                lane, unit = pending.pop(0)
            for at, job in enumerate(unit):
                one(*job)
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
                for event in stream_check._events(fh):
                    last = event
            cap = stream_check.output_cap((last or {}).get("result"))
        except OSError:
            continue
        if cap:
            return cap
    return None


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
                lines.append(f"BLOCKED  {label}  -> {r['stdout']}  (exit 0; {why})")
                continue
            lines.append(f"ok    {label}  -> {r.get('deliverable') or '(no final message)'}  "
                         f"stdout {r['stdout']}")
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
            lines.append(f"usage-limit  {label}  exit={USAGE_LIMIT_EXIT} ({r.get('meaning')})")
            lines.append(f"        salvage from: {r.get('stream') or '(no -P stream)'}")
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
            lines.append(f"FAIL  {label}  exit={r.get('exit')} ({r.get('meaning') or r.get('error')})")
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
        return pick_plan(seat.get("label", "?"), transport, model_registry.plans_of(transport, data), data)
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
    authors = []
    for job in jobs:
        if not job["rows"]:
            with open(job["digest"], "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"applied": [], "deferred": [], "decisions": [], "verdicts": [],
                           "mechanicalChecks": []}, stream)
            continue
        subject = job["subject"]
        findings = out_dir / f"{job['label']}_findings.json"
        findings.write_text(json.dumps({"findings": job["rows"]}, indent=2, ensure_ascii=False)
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
            "The findings and verdicts files already exist: read them and never rewrite them. The snapshot is the "
            "caller's: never write it.",
            f"Write only: {', '.join(writes + [_posix(job['digest'])])}. Return verdictPath: {_posix(verdicts)}.",
            "",
            f"CONSOLIDATED FINDINGS ({len(job['rows'])} ids: "
            + ", ".join(row["id"] for row in job["rows"]) + "):",
            data_block(job["rows"]),
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


def detach_driver(argv, jobs, out_dir, claim=None, log_path=None):
    """Rerun this fan-out without --detach as a process detached from the caller, and return 0.

    The caller's shell may be capped or killed (a backgrounded Bash call ends at the tool's limit);
    the driver it started would die with it, stranding queued jobs and the `<label>.md` writes. The
    driver keeps the caller's cwd, so every relative path resolves as it did here; its stdout, the
    summary, goes to `<out-dir>/fanout-driver.log`. The session watcher wakes the caller."""
    sys.path.insert(0, str(ROOT / "hooks"))
    from _bounded_run import detach_options, job_env
    child, swapped = [sys.executable, str(Path(__file__).resolve())], False
    for token in argv:
        if token == "--detach":
            continue
        if token == jobs and not swapped:
            token, swapped = str(Path(jobs).resolve()), True
        child.append(token)
    log = Path(log_path) if log_path else Path(out_dir) / "fanout-driver.log"
    with open(log, "ab") as fh:
        env = dict(os.environ, SIDECAR_REVIEW_CLAIM=claim) if claim else None
        proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                cwd=os.getcwd(), close_fds=True, env=job_env(env), **detach_options())
    print(f"fan-out detached: driver pid {proc.pid}; its summary goes to {_posix(log)}, and each job still "
          f"writes {_posix(out_dir)}/<label>.md. Arm the session watcher as its own Bash call with run_in_background: "
          f"true and timeout 7200000: python3 .claude/tools/watch_records.py --session --wake. Started any other way "
          f"(foreground, &, nohup, disown) it never wakes you and blocks a correct arm.")
    return 0


def main(argv=None):
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
        if args.refold:
            try:
                out_dir.mkdir(parents=True, exist_ok=False)
                run_review_fold(saved_review, args, data, out_dir, jobs)
            except (OSError, ValueError, JobError, fold_check.Unusable) as err:
                print(f"--refold: {err}", file=sys.stderr)
                return 2
        else:
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
    if args.detach:
        extra = {"log_path": out_dir.with_name(out_dir.name + "-driver.log")} if args.refold else {}
        return detach_driver(argv if argv is not None else sys.argv[1:], args.jobs, out_dir, claim=token, **extra)
    if args.refold:
        out_dir.mkdir(parents=True, exist_ok=False)
    # Keep the three-argument call for a run with no extra stages or scheduling.
    extras = {k: v for k, v in (('fallbacks', dict(FALLBACKS)), ('lanes', lanes),
                                ('after_seats', bool(args.review)),
                                ('resident', frozenset(j.get("label") for j in jobs if j.get("resident") is True)))
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
                         + (f"unread {', '.join(unread)}" if unread else "a gap names no listed item with a legal reason")
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
