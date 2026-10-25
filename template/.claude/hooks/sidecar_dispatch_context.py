#!/usr/bin/env python3
"""
Hook: PreToolUse on Bash — gate sidecar launches.

Fires for every direct `*_sidecar.sh` launch or dispatching `sidecar_fanout.py` call that
`sidecar_argv.launcher_invocations` reads: any path; segments split on `;`, `&`, `|`, newlines and
subshell parentheses; brace-group and if/while/until/for bodies; `NAME=value` and wrapper prefixes;
and `$(...)`/backtick, `eval` and `bash -c`/`sh -c` strings. A launch carried in a variable or a
sourced file is not read by this hook. The shared launcher calls `--launcher <path> <argv...>`
for that path, using these same checks and dispatch_table.sidecar_entries. Successful hook checks
leave receipts for the observed children (step 2); the launcher consumes them and runs the checks they
do not cover. Receipts share the table's count and age bounds.
The PREFLIGHT and DOCTRINE gates use that reader, never the APPROVAL
recognizer: a gate keyed on the narrow recognizer fails open for every shape it does not recognize.

0. TIER FIT (deny path), the check the Workflow route runs (`hooks/_dispatch_checks.py`), per direct launch
   and per `sidecar_fanout.py` job (`alias` or `model`, `shape`). A job whose `-G`/`shape` row declares a
   tier (`guards/tool_profiles.json`) must pin a row serving it on the alias's own registry transport, else
   the launch is denied before preflight. An alias the registry does not know is left to the launcher's
   availability gate. A fan-out job's `plan` must be a live plan of its row's route for the approval in step 3.
   A fan-out whose jobs file the same call writes is checked by `sidecar_fanout.py` itself, through
   `fanout_fit_refusal`, once it can read the file and before it spawns or detaches.
1. PREFLIGHT (deny path). Direct launchers run `<launcher> --check [-m <alias>] [-A] [-U] [-W]`
   synchronously. A fan-out call has no launcher to check, so the hook denies it from cached capacity
   through `sidecar_fanout.admission_refusal` (`fanout_capacity_refusal`): a job whose named plan has no room,
   or whose route has no plan with room, is refused in the call, backgrounded or not. A stale, missing or
   unreadable reading decides nothing. The same launcher gates each fan-out child at runtime. Availability,
   budget-band, provider-capacity, balance, and pricing refusals remain owned by the launchers.
   A direct launch whose `-R` or `-l` value comes from a shell variable is denied: tracking
   reads those values from the command text (`reference/sidecar_dispatch.md` Procedure step 5).
2. DOCTRINE (deny). A launch while the orchestration skill or the model ladder is not in this
   context (a compaction clears both) is denied with each document's load route
   (`model_ladder_gate.doctrine_check`): the full Read, or `model_registry.py ladder-slice <tier>` for
   the tiers the launch's `-G`/`shape` rows imply; a deny for another reason carries the same routes. Fan-out
   calls include their `--fold-*` authors (also under `--refold`) in doctrine and tier-fit checks. Hook-written
   receipts bind each child's label, alias, effort, shape, output directory and legal paying plans. They survive
   compaction; each increasing record attempt consumes its own use, so queued seats, plan fallbacks and memory
   retries retain the call's checks. A fan-out set occupies one receipt slot, so the count bound never cuts
   children off a checked call. Direct receipts remain one-use. Runtime capacity checks still run for each
   fan-out launch. An unplanned child runs the current doctrine check. A fan-out whose jobs file the same call
   writes has no readable contract here: `sidecar_fanout.py --detach` runs `fanout_doctrine_refusal` in the
   foreground over the file it can then read, and each launcher checks at runtime. `--status`, `--dry-run` and `--help` fan-out calls
   dispatch nothing and get no output.
3. APPROVAL. A validated fan-out or canonical direct launch gets `permissionDecision: allow`.
   A whole, project-contained full review whose only added tool is Bash emits
   `permissionDecision: ask` after preflight. The static project
   `permissions.ask` rule (`bash .claude/scripts/*_sidecar.sh * -t +Bash *`) guarantees a
   visible prompt for every launch it matches, including shapes this hook does not validate; whether this hook's own `ask` surfaces
   under auto mode is unmeasured.
   The wide launch reader never authorizes that ask; shell compounds and extra grants fall through.
4. CONTEXT. The hook adds `additionalContext` only to report that the doctrine gate could not read its state.
   The recipe, the long-context rule, the size line, the plan rooms and the watch route reach the session
   before the call: `reference/sidecar_dispatch.md`, `/delegate`, the `[budget-posture]` line and
   `model_registry.py for-role <tier>`.

Wired in: settings.json hooks.PreToolUse matcher "Bash" (via pre_bash_dispatch.py).
"""

import json
import os
import re
import shlex
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bounded_run import run_bounded
import model_ladder_gate
try:
    import _dispatch_checks
except Exception:  # tier fit then runs nowhere; the launchers' own gates still hold
    _dispatch_checks = None

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import sidecar_argv  # noqa: E402
try:
    import approving_gates  # noqa: E402
    _APPROVING_GATES_IMPORT_ERROR = None
    _resolve = approving_gates._resolve
    _inside = approving_gates._inside
    _path_has_symlink = approving_gates._path_has_symlink
    _regular_project_file = approving_gates._regular_project_file
except Exception as exc:
    approving_gates = None
    _APPROVING_GATES_IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)
try:
    import model_registry  # noqa: E402
except Exception:
    model_registry = None
try:
    import sidecar_fanout  # noqa: E402
except Exception:
    sidecar_fanout = None

PREFLIGHT_TIMEOUT = 45   # under pre_bash_dispatch's 75 s; the codex probe is one network call
CAPACITY_EXIT = 8        # lib/sidecar_common.sh: the capacity runner is missing, failed or malformed; no override
CAPACITY_REFUSAL_EXIT = 10  # pre-spawn floor, spent allowance or live usage-limit marker; no override
PEAK_EXIT = 11           # lib/sidecar_common.sh: peak pricing window on a peakPolicy=refuse row; only -W lifts it
SAFE_JOB_FIELDS = frozenset({
    "label", "alias", "promptFile", "effort", "disclosure", "shape",
    "contextFiles", "schemaFile", "workdir", "transport", "plan",
})
REQUIRED_JOB_FIELDS = frozenset({"label", "alias", "promptFile", "shape"})
SAFE_SHAPES = frozenset({"any", "survey", "review", "author"})
SAFE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
SAFE_DISCLOSURES = frozenset({"bare", "pointer", "full"})
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
NON_DISPATCHING_FANOUT_ARGS = frozenset({"--status", "--dry-run", "--help", "-h"})
# The whole budget stays below pre_bash_dispatch.py's 75 s settings timeout;
# tests/test_sidecar_dispatch_context.py proves the bound.
HOOK_BUDGET_SECONDS = 70.0


def _git_bash():
    """Git Bash, never WSL's: Windows Python resolves a bare `bash` to WSL (different filesystem)."""
    for cand in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"),
                 "C:/Program Files/Git/bin/bash.exe", "C:/Program Files/Git/usr/bin/bash.exe"):
        if cand and os.path.exists(cand):
            return cand
    return "bash" if os.name != "nt" else None


def _variable_tracking_value(command, args, root):
    # Apply only shell assignments before the launch. Loop variables intentionally stay unresolved:
    # the sidecar table may enumerate them, but this gate requires a literal per-job tracking value.
    import orchestration_metrics
    command = sidecar_argv.strip_heredocs(command)
    _cwd, assignments, _loops = orchestration_metrics._command_cwd(command, root)
    flags = sidecar_argv.sidecar_flags(args)
    if any(
        isinstance(flags.get(flag), str)
        and sidecar_argv._SHELL_VAR.search(orchestration_metrics._expand_vars(flags[flag], assignments))
        for flag in ("R", "l")
    ):
        return True
    # The launch reader removes $(...) bodies before constructing argv. Preserve this operand-only
    # check on the heredoc-stripped command so command substitutions cannot bypass the deny.
    operand = re.compile(r'(?<!\S)-(R|l)\s+(?:"([^"]*)"|\'([^\']*)\'|(\$\([^)]*\)[^\s;&|]*|[^\s;&|]+))')
    return any("$(" in next(value for value in match.groups()[1:] if value is not None)
               for match in operand.finditer(command))


def sidecar_launch(cmd):
    """The first executed `*_sidecar.sh` Invocation in `cmd` that is not a `--check` probe, or None.
    A quoted mention, or the launcher named as another program's argument, is not executed."""
    inv = next((c for c in sidecar_argv.launcher_invocations(cmd) if c.kind == "sidecar"), None)
    return None if inv is None or inv.args[:1] == ["--check"] else inv


def fanout_launch(cmd):
    """The first executed `sidecar_fanout.py` Invocation in `cmd` that can dispatch jobs, or None."""
    for inv in sidecar_argv.launcher_invocations(cmd):
        if inv.kind != "fanout":
            continue
        if any(arg in NON_DISPATCHING_FANOUT_ARGS or arg.startswith("--status=") for arg in inv.args):
            continue
        return _assigned(inv, cmd)
    return None


def _assigned(inv, cmd):
    """`inv` with each `$NAME` the command assigned before it replaced by its literal value. Without it a jobs
    path held in a shell variable reads as a missing file and every per-job check passes silently; an
    unassigned or substituted value stays as written (`orchestration_metrics._launcher_states`)."""
    try:
        import orchestration_metrics
        _cwd, env, _loops = orchestration_metrics._command_cwd(sidecar_argv.strip_heredocs(cmd), os.getcwd())
    except Exception:
        return inv
    if not env:
        return inv
    return inv._replace(args=[orchestration_metrics._expand_vars(arg, env) for arg in inv.args])


def run_preflight(cmd, root, deadline=None):
    """Return (state, refusal): passed, refused, or skipped when no check ran. `deadline` is the monotonic
    time by which the hook must have answered; the check gets at most what remains of it, up to
    PREFLIGHT_TIMEOUT."""
    if os.environ.get("HARNESS_SIDECAR_PREFLIGHT") == "0":
        return "skipped", None
    inv = next((c for c in sidecar_argv.launcher_invocations(cmd) if c.kind == "sidecar"), None)
    bash = _git_bash()
    if inv is None or not bash:
        return "skipped", None
    if approving_gates is None:
        return "refused", ("REFUSED at preflight (%s) — safe launcher path checks are unavailable (%s). "
                            "Repair hooks/approving_gates.py before dispatch."
                            % (os.path.basename(inv.path), _APPROVING_GATES_IMPORT_ERROR))
    launcher = inv.path
    root_path = Path(root).resolve()
    candidate = Path(launcher)
    candidate = candidate if candidate.is_absolute() else root_path / candidate
    try:
        launcher_path = candidate.resolve(strict=True)
        trusted_dir = (root_path / ".claude" / "scripts").resolve(strict=True)
    except OSError:
        return "skipped", None
    if (candidate.is_symlink() or launcher_path.parent != trusted_dir
            or not launcher_path.is_file() or _path_has_symlink(root_path, launcher_path)):
        return "skipped", None
    flags = sidecar_argv.sidecar_flags(inv.args)
    args = [bash, str(launcher_path), "--check"]
    if isinstance(flags.get("m"), str):
        args += ["-m", flags["m"]]
    if flags.get("A") is True:
        args.append("-A")
    if flags.get("U") is True:
        args.append("-U")
    # -W: the user authorized a peak-pricing-window dispatch; -A does not cover it (lib exit 11).
    if flags.get("W") is True:
        args.append("-W")
    allowed = PREFLIGHT_TIMEOUT if deadline is None else min(PREFLIGHT_TIMEOUT, deadline - time.monotonic())
    if allowed < 1:
        return "refused", ("REFUSED at preflight (%s --check never started: the earlier dispatch checks used "
                "this hook's time budget) — availability and budget were not verified. Retry the dispatch."
                % os.path.basename(launcher))
    try:
        # Bounded: a check whose probes outlive it would otherwise hold this hook past its deadline.
        r = run_bounded(args, allowed, cwd=root, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        if r.returncode is None:
            return "refused", ("REFUSED at preflight (%s --check timed out after %d s) — availability and budget "
                    "were not verified. Retry after checking the launcher."
                    % (os.path.basename(launcher), allowed))
    except Exception as exc:
        return "refused", ("REFUSED at preflight (%s --check could not run: %s: %s) — availability and budget "
                "were not verified. Repair the launcher check before dispatch."
                % (os.path.basename(launcher), type(exc).__name__, exc))
    if r.returncode == 0:
        return "passed", None
    text = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
    if r.returncode == CAPACITY_EXIT:
        return "refused", ("REFUSED at preflight (%s --check exit %d) — the provider capacity check itself failed:\n%s\n"
                "-A does not lift it: no band was read. Repair the capacity check (tools/provider_capacity.py), "
                "then retry." % (os.path.basename(launcher), r.returncode, text[-1500:]))
    if r.returncode == CAPACITY_REFUSAL_EXIT:
        return "refused", ("REFUSED at preflight (%s --check exit %d) — capacity admission refused before spawn:\n%s\n"
                "-A does not lift this refusal; wait for reset or use another plan with room."
                % (os.path.basename(launcher), r.returncode, text[-1500:]))
    if r.returncode == PEAK_EXIT:
        return "refused", ("REFUSED at preflight (%s --check exit %d) — a peak pricing window is open:\n%s\n"
                "-A does not lift it. Pass -W only when the user explicitly authorized peak pricing in this "
                "conversation; otherwise wait for off-peak or route the work to another provider."
                % (os.path.basename(launcher), r.returncode, text[-1500:]))
    return "refused", ("REFUSED at preflight (%s --check exit %d) — the dispatch would have died the same way in the "
            "background:\n%s\nRe-select under the ladder, or pass -A and state the spend."
            % (os.path.basename(launcher), r.returncode, text[-1500:]))


def _parse_fanout_command(command, root):
    if approving_gates is None:
        return None
    words, _why = approving_gates.simple_argv(command)
    if words is None or len(words) < 3 or words[0] not in {"python", "python3"}:
        return None
    script = _resolve(root, words[1])
    expected = (root / ".claude" / "tools" / "sidecar_fanout.py").resolve()
    if script is None or script != expected or not script.is_file() or _path_has_symlink(root, script):
        return None

    jobs = None
    out_dir = None
    index = 2
    while index < len(words):
        word = words[index]
        if word == "--out-dir" and index + 1 < len(words) and out_dir is None:
            out_dir = words[index + 1]
            index += 2
        elif word == "--detach":
            index += 1
        elif word == "--max-parallel" and index + 1 < len(words):
            try:
                width = int(words[index + 1])
            except ValueError:
                return None
            if not 1 <= width <= 4:
                return None
            index += 2
        elif not word.startswith("-") and jobs is None:
            jobs = word
            index += 1
        else:
            return None
    if jobs is None:
        return None
    jobs_path = _resolve(root, jobs)
    scratch = (root / ".claude" / "scratch").resolve()
    if (jobs_path is None or not _inside(jobs_path, scratch) or jobs_path.suffix.lower() != ".json"
            or not jobs_path.is_file() or _path_has_symlink(root, jobs_path)):
        return None
    output_path = _resolve(root, out_dir) if out_dir else jobs_path.parent / "fanout"
    if output_path is None or not _inside(output_path, scratch) or _path_has_symlink(root, output_path):
        return None
    return jobs_path


def read_fanout_jobs(path, registry=None):
    """The jobs file's jobs in sidecar_fanout's own field names, or None where sidecar_fanout refuses it."""
    if sidecar_fanout is None:
        return None
    try:
        jobs, shared, _spill = sidecar_fanout.load_jobs(path)
        return [sidecar_fanout.normalize_job(job, registry, shared) for job in jobs]
    except Exception:
        return None


def _safe_fanout_job(job, root, registry):
    if not isinstance(job, dict) or not REQUIRED_JOB_FIELDS <= job.keys():
        return False
    if not job.keys() <= SAFE_JOB_FIELDS:
        return False
    if not isinstance(job["label"], str) or not LABEL_RE.fullmatch(job["label"]):
        return False
    if job.get("shape") not in SAFE_SHAPES or job.get("workdir", ".") != ".":
        return False
    if not _regular_project_file(root, job.get("promptFile")):
        return False
    context_files = job.get("contextFiles", [])
    if not isinstance(context_files, list) or not all(
        _regular_project_file(root, path) for path in context_files
    ):
        return False
    schema = job.get("schemaFile")
    if schema is not None and not _regular_project_file(root, schema):
        return False
    for field in ("alias", "effort", "disclosure", "transport", "plan"):
        if field in job and not isinstance(job[field], str):
            return False
    if ("effort" in job and job["effort"] not in SAFE_EFFORTS
            or "disclosure" in job and job["disclosure"] not in SAFE_DISCLOSURES):
        return False
    try:
        entry = model_registry.resolve(job["alias"], registry)
        available = model_registry.model_available(job["alias"], registry)
    except Exception:
        return False
    try:
        plans = model_registry.plans_of(entry.get("transport"), registry)
    except Exception:
        return False
    return (available
            and job.get("transport", entry.get("transport")) == entry.get("transport")
            and job.get("plan", entry.get("transport")) in plans)


def fanout_allowed(payload):
    if model_registry is None or approving_gates is None:
        return False
    root = approving_gates.trusted_root(payload)
    if root is None:
        return False
    jobs_path = _parse_fanout_command((payload.get("tool_input") or {}).get("command"), root)
    if jobs_path is None:
        return False
    try:
        registry = model_registry.load()
    except Exception:
        return False
    jobs = read_fanout_jobs(jobs_path, registry)
    return (isinstance(jobs, list)
            and 0 < len(jobs) <= 8
            and len({job.get("label") for job in jobs if isinstance(job, dict)}) == len(jobs)
            and all(_safe_fanout_job(job, root, registry) for job in jobs))


def _read_fanout(fanout, root):
    """(args, path, data, jobs, spill): the call's parsed arguments, jobs file, registry, normalized jobs
    and spill directory. Raises where `sidecar_fanout` refuses the call or its file."""
    args = sidecar_fanout.build_parser().parse_args(fanout.args)
    path = Path(args.jobs)
    path = path if path.is_absolute() else Path(root) / path
    raw, shared, spill = sidecar_fanout.load_jobs(path)
    data = model_registry.load()
    return args, path, data, [sidecar_fanout.normalize_job(job, data, shared) for job in raw], spill


def fanout_capacity_refusal(fanout, root, deadline):
    """`sidecar_fanout.admission_refusal` for the call's jobs, read from cached capacity only, else None.
    The cache rule is the launcher's (`provider_capacity.cached`: a reading no older than its TTL). A stale
    or missing reading decides nothing; nor does a call this hook cannot read or one past `deadline`, since
    each launcher still gates its own job. A refold launches no seat."""
    if (fanout is None or sidecar_fanout is None or model_registry is None
            or os.environ.get("HARNESS_SIDECAR_PREFLIGHT") == "0" or time.monotonic() >= deadline):
        return None
    try:
        args, _path, data, jobs, _spill = _read_fanout(fanout, root)
        if getattr(args, "refold", False):
            return None
        return sidecar_fanout.admission_refusal(
            jobs, data, read=lambda transport, table: sidecar_fanout.provider_bands.read_capacity(
                transport, table, live=False))
    except (Exception, SystemExit):
        return None


def fanout_contract(fanout, root):
    """Children observed in the call, including deferred authors and every legal paying-plan route.
    Room and memory admission may change before a queued child starts; neither changes its pin.
    """
    if fanout is None or sidecar_fanout is None or model_registry is None:
        return []
    try:
        args, path, data, jobs, spill = _read_fanout(fanout, root)
        directory = sidecar_argv.fanout_out_dir(str(path), args.out_dir, spill, str(sidecar_fanout.REPO))
        directory = Path(directory)
        directory = directory if directory.is_absolute() else Path(root) / directory
        if getattr(args, "refold", False):
            directory = sidecar_fanout.refold_dir(directory)
        children = [] if getattr(args, "refold", False) else list(jobs)
        if args.fold_alias and args.fold_effort and args.fold_subject:
            fold_plan = args.fold_plan
            payers = {job.get("plan") for job in jobs}
            if not fold_plan and len(payers) == 1 and None not in payers:
                payer = next(iter(payers))
                route = model_registry.resolve(args.fold_alias, data)["transport"]
                if payer in model_registry.plans_of(route, data):
                    fold_plan = payer
            children += [{"label": sidecar_fanout.FOLD_LABEL.format(index), "alias": args.fold_alias,
                          "effort": args.fold_effort, "shape": "author", "plan": fold_plan}
                         for index in range(1, len(args.fold_subject) + 1)]
        contracts = []
        for job in children:
            alias = job.get("alias")
            if not isinstance(alias, str):
                continue
            route = model_registry.resolve(alias, data)["transport"]
            plans = [job["plan"]] if job.get("plan") else model_registry.plans_of(route, data)
            legal = model_registry.plans_of(route, data)
            if not plans or any(plan not in legal for plan in plans):
                raise ValueError("fan-out paying plan does not serve its alias")
            contracts.append({"job": (str(job.get("label") or alias), alias, job.get("shape"), job.get("plan")),
                              "effort": job.get("effort"), "plans": plans,
                              "directory": os.path.normcase(os.path.abspath(directory))})
        return contracts
    except (Exception, SystemExit):
        return []


def launch_jobs(direct, fanout, root, contracts=None):
    """[(label, alias, shape or None, plan or None)] for the direct launch or each readable fan-out
    job, else []. The plan is who pays: the launcher's own plan (`model_registry.plan_for_launcher`)
    for a direct launch, a fan-out job's `plan` key; None leaves the default to `currency`."""
    if direct is not None:
        flags = sidecar_argv.sidecar_flags(direct.args)
        alias = flags.get("m")
        if not isinstance(alias, str):
            return []
        shape = flags.get("G") if isinstance(flags.get("G"), str) else None
        label = flags.get("l") if isinstance(flags.get("l"), str) else alias
        try:
            plan = model_registry.plan_for_launcher(direct.path) if model_registry is not None else None
        except Exception:
            plan = None
        return [(label, alias, shape, plan)]
    return [contract["job"] for contract in (fanout_contract(fanout, root) if contracts is None else contracts)]


def fanout_jobs_unwritten(fanout, cmd, root):
    """True when the fan-out's jobs file does not exist where the launch will look (the command's own cwd after
    its `cd` segments): whatever writes it in this call, this hook cannot read its jobs, so each job's launcher
    runs the doctrine check once the file exists. A file nothing writes never launches."""
    parsed = sidecar_argv.parse_fanout_args(fanout.args) if fanout is not None else None
    if not parsed or not parsed[0]:
        return False
    cwd = root
    try:
        import orchestration_metrics
        cwd = orchestration_metrics._command_cwd(sidecar_argv.strip_heredocs(cmd), root)[0] or root
    except Exception:
        pass
    path = Path(parsed[0])
    path = path if path.is_absolute() else Path(cwd) / path
    return not path.exists()


def launch_tiers(direct, fanout, root, contracts=None):
    """The sorted tiers the launch's jobs imply through their `-G`/`shape` rows, else None when a job names no
    tiered shape or the launch has no readable job: the doctrine gate then asks for the full ladder."""
    try:
        by_shape = _dispatch_checks.shape_tiers()
        tiers = {model_registry.canonical_tier(by_shape[shape])
                 for _label, _alias, shape, _plan in launch_jobs(direct, fanout, root, contracts)}
    except Exception:
        return None
    return sorted(tiers) or None


def dispatch_checks(jobs):
    """The refusal for a job whose shape implies a tier its alias's row does not serve, else None."""
    if _dispatch_checks is None or model_registry is None or not jobs:
        return None
    try:
        data = model_registry.load()
    except Exception:
        return None
    tiers = _dispatch_checks.shape_tiers()
    unfit = []
    for label, alias, shape, _plan in jobs:
        try:
            row = model_registry.resolve(alias, data)
        except Exception:
            continue
        try:
            why = _dispatch_checks.tier_fit(alias, tiers.get(shape), row["transport"], data)
        except Exception:
            why = None
        if why:
            unfit.append(f"`{label}`: {why}")
    if unfit:
        return ("Sidecar launch outside the tier its shape implies (orchestration §5) — " + "; ".join(unfit[:8])
                + ". A `-G`/`shape` row's tier (guards/tool_profiles.json) binds the job whatever it pins. "
                "Launch a row of that tier (`python3 .claude/tools/model_registry.py for-role <tier>`) or a "
                "shape whose tier the alias serves; raise effort before tier.")
    return None


CHECKS_KEY = "sidecar_launch_checks"
TRACKING_REASON = ("Launch with literal -R and -l values, one call per job, or put the jobs "
                   "in a jobs file for python3 .claude/tools/sidecar_fanout.py "
                   "(reference/sidecar_dispatch.md Procedure step 5).")


def checked_job(job):
    pin = list(job)
    pin[3] = pin[3] or model_registry.resolve(pin[1])["transport"]
    return pin


def remember_checks(payload, direct, fanout, root, preflight, contracts=None):
    """Hook receipts for the checked snapshot; direct launches keep the resolver's one-use identity."""
    import dispatch_table as table
    import _hook_state
    sid = payload.get("session_id")
    if not sid:
        return
    now = time.time()
    entries, _ = table.sidecar_entries(dict(payload, cwd=root)) if direct else ([], [])
    jobs = launch_jobs(direct, fanout, root, contracts)
    flags = sidecar_argv.sidecar_flags(direct.args) if direct else {}
    receipts = []
    for entry in entries:
        job = jobs[0] if direct and jobs else next((j for j in jobs if j[0] == table._label(entry)), None)
        entry.setdefault("issued_at", now)
        receipts.append({"entry": entry, "job": checked_job(job) if job else None, "at": now, "used": False,
                         "call": payload.get("tool_use_id"),
                         "preflight": preflight == "passed",
                         "capacity_flags": {k: flags[k] for k in ("m", "A", "U", "W") if k in flags}})

    if fanout:
        receipts = []
        for contract in (fanout_contract(fanout, root) if contracts is None else contracts):
            label, alias, _shape, _plan = contract["job"]
            observed, _ = table.sidecar_entries(payload, jobs=[{
                "label": label, "model": alias, "effort": contract["effort"], "kind": "fanout",
                "launcher": "sidecar_fanout", "fanout_dir": contract["directory"], "record": None}])
            entry = observed[0]
            entry["issued_at"] = now
            receipts.append({"entry": entry, "fanout": contract, "at": now, "last_attempt": 0,
                             "call": payload.get("tool_use_id")})
        if receipts:
            receipts = [{"entry": receipts[0]["entry"], "children": receipts, "at": now,
                         "call": payload.get("tool_use_id")}]

    def save(state):
        kept = [r for r in state.get(CHECKS_KEY) or [] if now - r.get("at", 0) < table.STALE_SECONDS]
        state[CHECKS_KEY] = (kept + receipts)[-table.MAX_PENDING:]
    _hook_state.update_json_locked(_hook_state.state_path(sid), save, raising=True)


def take_checks(payload, entry, job, flags):
    import dispatch_table as table
    import _hook_state
    found = []
    sid = payload.get("session_id")
    if not sid:
        return False, False

    def take(state):
        receipts = state.get(CHECKS_KEY) or []
        pending = [r["entry"] for r in receipts] + (state.get(table.STATE_KEY) or [])
        for receipt in (child for group in receipts for child in group.get("children", [group])):
            contract = receipt.get("fanout")
            if contract:
                if time.time() - receipt.get("at", 0) >= table.STALE_SECONDS:
                    continue
                label, alias, shape, _plan = contract["job"]
                if (list(job[:3]) != [label, alias, shape] or job[3] not in contract["plans"]
                        or flags.get("e") != contract["effort"]):
                    continue
                record = entry.get("record")
                if not record or os.path.normcase(os.path.abspath(os.path.dirname(record))) != contract["directory"]:
                    continue
                match = re.fullmatch(re.escape(label) + r"(?:\.attempt([2-9]|[1-9][0-9]+))?\.record\.json",
                                     os.path.basename(record))
                attempt = int(match.group(1) or 1) if match else 0
                if attempt <= receipt.get("last_attempt", 0):
                    continue
                receipt["last_attempt"] = attempt
                found.append((True, False))
                break
            if (receipt.get("used") or time.time() - receipt.get("at", 0) >= table.STALE_SECONDS
                    or (receipt.get("job") is not None and receipt["job"] != checked_job(job))):
                continue
            if not table.same_sidecar(receipt["entry"], entry, sid, pending):
                continue
            receipt["used"] = True
            capacity = {k: flags[k] for k in ("m", "A", "U", "W") if k in flags}
            found.append((receipt.get("job") is not None,
                          receipt.get("preflight") and receipt.get("capacity_flags") == capacity))
            break
    _hook_state.update_json_locked(_hook_state.state_path(sid), take, raising=True)
    return found[0] if found else (False, False)


def tracking_session():
    """The session whose dispatch table records this launch: none inside a tree whose owner tracks its own
    launches (`SIDECAR_DISPATCH_OWNER`, set by bench.py for its daemons, grids and studies)."""
    if os.environ.get("SIDECAR_DISPATCH_OWNER"):
        return None
    return os.environ.get("CLAUDE_CODE_SESSION_ID")


def fanout_admitted(session_id, record):
    """True when `record` belongs to a job of a fan-out inventory that this session's admitted fan-out call wrote and
    whose driver process still lives: its queued jobs met the doctrine at that call, and a compaction since must not
    deny them. The environment names the fan-out; the inventory on disk, which `sidecar_fanout.run_jobs` writes, proves
    it (an exported `SIDECAR_FANOUT_ID` alone proves nothing)."""
    fanout_id = os.environ.get("SIDECAR_FANOUT_ID")
    if not (fanout_id and session_id and isinstance(record, str)):
        return False
    import glob
    try:
        import watch_records
        gone = watch_records._process_line_gone
    except (ImportError, AttributeError):
        return False
    record = os.path.normcase(os.path.abspath(record))
    pattern = os.path.join(glob.escape(os.path.dirname(record)), "fanout-*.inventory.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as fh:
                inventory = json.load(fh)
        except (OSError, ValueError):
            continue
        if (isinstance(inventory, dict) and inventory.get("fanoutId") == fanout_id
                and inventory.get("parentSessionId") == session_id
                and gone(inventory.get("fanoutProcess")) is False
                and any(isinstance(job, dict) and os.path.normcase(os.path.abspath(str(job.get("recordPath")))) == record
                        for job in inventory.get("jobs") or [])):
            return True
    return False


def launcher_launch(launcher, argv):
    """The shared launcher's argv entrypoint; checks and registers before model spend."""
    import dispatch_table as table
    root = str(Path(__file__).resolve().parents[2])
    command = shlex.join(["bash", launcher, *argv])
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.getcwd(),
               "session_id": tracking_session()}
    payload["transcript_path"] = table._transcript(payload["session_id"])
    direct = sidecar_launch(command)
    if direct is None:
        return 0
    entries, untracked = table.sidecar_entries(payload)
    if payload["session_id"] and not entries:
        print(TRACKING_REASON, file=sys.stderr)
        return 2
    flags = sidecar_argv.sidecar_flags(argv)
    checked_direct = direct
    default = os.environ.get("SC_DISPATCH_DEFAULT_MODEL")
    if "m" not in flags and default:
        checked_direct = direct._replace(args=["-m", default, *argv])
    jobs = launch_jobs(checked_direct, None, root)
    checked, preflight = take_checks(payload, entries[0], jobs[0], flags) if entries and jobs else (False, False)
    started = time.monotonic()
    refusal = None if checked else dispatch_checks(jobs)
    if (refusal is None and not checked and payload["session_id"]
            and not fanout_admitted(payload["session_id"], flags.get("R"))):
        refusal, fault = model_ladder_gate.doctrine_check(
            payload, tiers=lambda: launch_tiers(checked_direct, None, root), embed_slice=False)
        if fault:
            print(fault, file=sys.stderr)
    if refusal is None and not preflight:
        _state, refusal = run_preflight(command, root, started + HOOK_BUDGET_SECONDS)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    for line in untracked:
        print(line, file=sys.stderr)
    if payload["session_id"]:
        table.register_sidecars(payload, entries, actual=True)
    else:
        owner = os.environ.get("SIDECAR_DISPATCH_OWNER")
        print("[sidecar] launch tracked by its owner (%s), not a session dispatch table." % owner if owner else
              "[sidecar] CLAUDE_CODE_SESSION_ID is unset: launch is not tracked in a session dispatch table.",
              file=sys.stderr)
    return 0


def fanout_doctrine_refusal(fanout_args, root):
    """The doctrine deny for a fan-out call a detached driver would otherwise refuse out of the session's sight,
    else None. `sidecar_fanout.py --detach` calls it in the foreground, over the jobs file the call can now read
    (the hook cannot read one the same call writes), for the session `tracking_session` names: the one each
    launcher checks. The deny carries the slice of the missing tiers and records them, so the re-issued call
    passes in the foreground and in every child launcher."""
    sid = tracking_session()
    if not sid:
        return None
    fanout = types.SimpleNamespace(args=list(fanout_args))
    contracts = fanout_contract(fanout, root)
    import dispatch_table
    refusal, _fault = model_ladder_gate.doctrine_check(
        {"session_id": sid, "transcript_path": dispatch_table._transcript(sid)},
        tiers=lambda: launch_tiers(None, fanout, root, contracts), embed_slice=True)
    return refusal


def fanout_fit_refusal(fanout_args, root):
    """`dispatch_checks` over the fan-out call's jobs, fold authors included, else None: the tier-fit deny this hook
    cannot make for a jobs file the same call writes. `sidecar_fanout.py` calls it in its own call, before it spawns or
    detaches anything, so a pin the launcher would refuse reaches the caller and not a detached child's `.err`. A job
    this hook already checked for the call (a fresh fan-out receipt with its label, pin, shape and directory) is not
    checked again. A call it cannot read decides nothing; each launcher still runs its own check."""
    fanout = types.SimpleNamespace(args=list(fanout_args))
    contracts = fanout_contract(fanout, root)
    held = _receipted_fanout_jobs()
    unchecked = {(tuple(c["job"][:3]), c["directory"]) for c in contracts} - held
    return dispatch_checks([job for job in launch_jobs(None, fanout, root, contracts)
                            if any(key[0] == tuple(job[:3]) for key in unchecked)])


def _receipted_fanout_jobs():
    """{((label, alias, shape), directory)} of each fresh fan-out receipt this session's hook wrote."""
    import dispatch_table as table
    import _hook_state
    sid = tracking_session()
    if not sid:
        return set()
    state = _hook_state.read_json_salvage(_hook_state.state_path(sid)) or {}
    now = time.time()
    return {(tuple(child["fanout"]["job"][:3]), child["fanout"]["directory"])
            for group in state.get(CHECKS_KEY) or [] for child in group.get("children", [group])
            if child.get("fanout") and now - child.get("at", 0) < table.STALE_SECONDS}


def main():
    started = time.monotonic()
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    tool_input = data.get("tool_input") or {}
    cmd = tool_input.get("command") or ""
    direct = sidecar_launch(cmd)
    fanout = fanout_launch(cmd)
    if not direct and not fanout:
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    call_root = data.get("cwd") or os.getcwd()
    launch_root = call_root
    if fanout:
        import orchestration_metrics
        launch_root = orchestration_metrics._command_cwd(sidecar_argv.strip_heredocs(cmd), call_root)[0] or call_root
    contracts = fanout_contract(fanout, launch_root)
    unwritten = fanout_jobs_unwritten(fanout, cmd, call_root)
    doctrine, doctrine_fault = (None, None) if unwritten else \
        model_ladder_gate.doctrine_check(data, tiers=lambda: launch_tiers(direct, fanout, root, contracts),
                                         embed_slice=True)

    def deny(reason):
        # One deny names every unmet precondition (instruction_quality §3): the doctrine retry rides along.
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": " ".join(r for r in (reason, doctrine) if r),
            }
        }))

    if direct:
        if _variable_tracking_value(cmd, direct.args, root):
            deny(TRACKING_REASON)
            return
    fit_refusal = dispatch_checks(launch_jobs(direct, fanout, root, contracts))
    if fit_refusal:
        deny(fit_refusal)
        return
    capacity_refusal = fanout_capacity_refusal(fanout, launch_root, started + HOOK_BUDGET_SECONDS)
    if capacity_refusal:
        deny(capacity_refusal)
        return
    fanout_is_allowed = bool(fanout) and fanout_allowed(data)

    preflight_state = "skipped"
    if direct:
        preflight_state, refusal = run_preflight(cmd, root, started + HOOK_BUDGET_SECONDS)
        if refusal:
            deny(refusal)
            return
    if doctrine:
        deny(None)
        return

    if not doctrine_fault and not unwritten:
        remember_checks(data, direct, fanout, root, preflight_state, contracts)

    output = {"hookEventName": "PreToolUse"}
    if fanout_is_allowed:
        output["permissionDecision"] = "allow"
        output["permissionDecisionReason"] = (
            "Auto-approved: the sidecar fan-out is structurally safe; "
            "its launchers retain their budget and quota gates"
        )
    approval_root = approving_gates.trusted_root(data) if approving_gates is not None else None
    if direct and preflight_state == "passed" and approval_root is not None:
        ok, _why = approving_gates.sidecar_launch_shape(cmd, approval_root)
        if ok:
            output.update(approving_gates.allow(
                "canonical sidecar launch; preflight passed"))
        else:
            asks, _why = approving_gates.sidecar_bash_ask_shape(cmd, approval_root)
            if asks:
                flags = sidecar_argv.sidecar_flags(direct.args)
                output["permissionDecision"] = "ask"
                output["permissionDecisionReason"] = (
                    "One-time approval: %s model %s requests +Bash for a full review in %s "
                    "using prompt %s. Bash can run commands and access project files; "
                    "approve this exact call only if that shell access is intended."
                    % (os.path.basename(direct.path), flags["m"], approval_root, flags["f"])
                )

    if doctrine_fault:
        output["additionalContext"] = doctrine_fault

    if len(output) > 1:
        sys.stdout.write(json.dumps({"hookSpecificOutput": output}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--launcher"]:
        try:
            sys.exit(launcher_launch(sys.argv[2], sys.argv[3:]))
        except Exception as exc:
            print("[sidecar] launch checks/tracking failed: %s: %s; nothing started." %
                  (type(exc).__name__, exc), file=sys.stderr)
            sys.exit(2)
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
