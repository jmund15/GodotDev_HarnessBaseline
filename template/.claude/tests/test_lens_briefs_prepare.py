#!/usr/bin/env python3
"""Proof for `lens_briefs.py prepare`: one call composes the fan-out prep the command docs hand-chain.

Every case runs the real `prepare` subcommand in a planted git checkout under its own temp root: a
planted capacity cache (PROVIDER_CAPACITY_CACHE) so no live probe runs, a planted task-record dir, and
scratch files inside that root. `review_ledger.py plan` and `session_audit_inputs.py freeze` are the real
owners, run in process.

    python3 .claude/tests/test_lens_briefs_prepare.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOLS = REPO / ".claude" / "tools"
EXTERNAL = json.loads((REPO / ".claude" / "reference" / "external_models.json").read_text(encoding="utf-8"))
sys.path[:0] = [str(TOOLS), str(REPO / ".claude" / "hooks"), str(HERE)]
import _mcp_plant  # noqa: E402
from _planted_checkout import enter_planted_checkout  # noqa: E402

enter_planted_checkout()  # the rigor reader asks git about the working directory

# The provider hook resolves each kind's MCP servers from the machine's and the project's registrations;
# the proof plants them, so it reads neither this machine's config nor this project's.
_mcp_plant.plant()
import lens  # noqa: E402
import lens_briefs  # noqa: E402  (the live pin and effort vocabulary, so a roster change never breaks this proof)

PINS = sorted(lens_briefs.NATIVE_PINS)
EFFORTS = sorted(lens_briefs.NATIVE_EFFORTS)
PANEL = "sonnet" if "sonnet" in PINS else PINS[0]
ARCH = "opus" if "opus" in PINS else PINS[-1]
EFFORT = "medium" if "medium" in EFFORTS else EFFORTS[0]
EFFORT_HI = "high" if "high" in EFFORTS else EFFORTS[-1]
# A consumer owns its review-lens prefix (adaptation `lens_prefixes`), and the shipped redteam registry may still
# carry an unrendered project-name placeholder in `rt-boundary`: the proof names the prefixed seat and a placeholder-free lens.
REVIEW_SEAT = lens.REGISTRIES["review"][1] + "code-reviewer"
RT_SEAT = "rt-abstraction"
FAILURES = []
SHIPPED = set(lens.shipped())
# A case that plants a seat or mandate from a registry skips when this install's layers ship none of it (a pure-only install).
NEEDS = {"mode_explore": "explore", "mode_plan_check": "plan_check", "mode_pr_review": "review",
         "mode_design_redteam": "redteam", "mode_pr_ready": "review", "mode_session_audit": "session_audit",
         "over_window_refusal": "redteam", "sidecar_target": "redteam", "stale_kfm_snapshot": "plan_check",
         "task_text_not_backticked": "explore", "later_round_diff": "explore",
         "mixed_roster_code_seat_keeps_its_files": "session_audit"}


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:500]))


def capacity_cache(path):
    now = time.time()
    cache = {}
    for name, cfg in EXTERNAL["transports"].items():
        entry = {"schemaVersion": 1, "transport": name, "costModel": cfg.get("costModel"),
                 "sourceKind": "live", "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
        if name == "anthropic":
            entry.update(status="available", balance=None, error=None, quota={
                "dispatchBand": "On pace", "routingBand": "On pace", "bindingWindow": "five_hour",
                "routingWindow": "five_hour",
                "windows": [{"name": "five_hour", "usedPercent": 10.0, "resetsAt": now + 3600,
                             "band": "On pace", "pressure": 0.3, "runoutRatio": 0.5,
                             "windowDurationMins": 300, "dispatchFloorPercent": 99}]})
        else:
            entry.update(status="network-error", quota=None, balance=None,
                         error={"kind": "network", "message": "planted"})
        cache[name] = entry
    path.write_text(json.dumps(cache), encoding="utf-8")


class Fixture:
    """A planted checkout: two commits, a scratch tree, a context file and one changed subject file."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="prep_proof_")).resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "proof@example.invalid")
        self.git("config", "user.name", "proof")
        self.git("config", "commit.gpgsign", "false")
        self.subject = self.write("src/thing.py", "def thing():\n    return 1\n")
        self.plan = self.write("docs/plan.md", "# Plan\n\n**Shape:** code\n\nA plan.\n\n## Job map\n\n| Slice | Route |\n|---|---|\n| S1 | job |\n")
        self.context = self.write("ctx/context.md", "# Context\n\nplanted\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.subject.write_text("def thing():\n    return 2\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.run_dir = self.scratch("run")
        self.state = self.root / "state"
        self.state.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.cache = self.root / "capacity.json"
        capacity_cache(self.cache)
        self.spec_path = self.run_dir / "spec.json"
        self.out = self.run_dir / "out"
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        CLAUDE_PROJECT_DIR=str(self.root), HARNESS_HOOK_STATE_DIR=str(self.state),
                        HARNESS_TASK_RECORD_DIR=str(self.tasks),
                        PROVIDER_CAPACITY_CACHE=str(self.cache),
                        CLAUDE_CODE_SESSION_ID="prepsess",
                        PYTHONPATH="")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def scratch(self, *parts):
        path = self.root / ".claude" / "scratch"
        for part in parts:
            path = path / part
        path.mkdir(parents=True, exist_ok=True)
        return path

    def spec(self, **fields):
        base = {"prefix": "p", "root": str(self.root), "context": str(self.context),
                "subjectFiles": [str(self.plan)]}
        base.update(fields)
        self.spec_path.write_text(json.dumps(base, indent=2), encoding="utf-8", newline="\n")
        return self.spec_path

    def prepare(self, **fields):
        if fields and "target" not in fields and fields.get("mode") != "explore":
            fields["target"] = "native"
        return self.prepare_as_written(**fields)

    def prepare_as_written(self, **fields):
        spec = self.spec(**fields) if fields else self.spec_path
        return subprocess.run([sys.executable, str(TOOLS / "lens_briefs.py"), "prepare",
                               "--spec", str(spec), "--out", str(self.out)],
                              capture_output=True, text=True, encoding="utf-8",
                              cwd=str(self.root), env=self.env, timeout=180)

    def args(self):
        path = self.out / "args.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def ok(proc):
    return proc.returncode == 0, (proc.stdout[-600:], proc.stderr[-1200:])


def check_record(label, f, kind, seats, read=None):
    """The run's prepared job record holds the one contract, and it seats `seats` for `kind`, each
    brief file existing; `read` names a file at least one seat must read."""
    path = f.out / "jobs_p.json"
    jobs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    problems = lens_briefs.record_problems(jobs, kind=kind) if isinstance(jobs, list) else ["no jobs_p.json"]
    check(f"{label}: the prepared job record holds its contract", bool(jobs) and not problems, problems)
    got = [job.get("seat") or job.get("key") for job in jobs] if isinstance(jobs, list) else []
    check(f"{label}: the record seats {', '.join(seats)}", got == list(seats), got)
    prompts = [job.get("promptPath") or job.get("promptFile") for job in jobs or []]
    check(f"{label}: every seat's brief file exists",
          bool(prompts) and all(isinstance(p, str) and Path(p).is_file() for p in prompts), prompts)
    if read is not None:
        reads = [str(entry.get("path")).replace("\\", "/") for job in jobs or []
                 for entry in job.get("mustRead") or []]
        check(f"{label}: a seat reads {read}", any(entry.endswith(read) for entry in reads), reads)


HOOK = REPO / ".claude" / "hooks" / "workflow_provider_guard.py"
REVIEW_ENGINE = REPO / ".claude" / "workflows" / "review_fanout.js"
FIXTURE_RESULT = {"findings": [], "claims": [], "gaps": [],
                  "checked": {"toolsUsed": ["Read:fixture"], "stoppedAt": "done", "basis": "fixture"}}
_HOOK = None


def _hook():
    """The real provider hook module, loaded once from its file."""
    global _HOOK
    if _HOOK is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("prepare_proof_provider_guard", str(HOOK))
        _HOOK = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_HOOK)
    return _HOOK


def inject(args, engine):
    """`args` as hooks/workflow_provider_guard.py leaves them for `engine`: the real registry, the real
    per-kind profile resolution. Returns (injected args, None) or (None, the hook's refusal text)."""
    hook = _hook()
    saved = os.environ.pop("HARNESS_WORKFLOW_ARGS_OWNER", None)
    try:
        payload = {"tool_name": "Workflow", "tool_input": {"scriptPath": str(engine), "args": args}}
        tool_input = hook.transport_injection(payload, "anthropic", hook._session_transport._registry())
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    finally:
        if saved is not None:
            os.environ["HARNESS_WORKFLOW_ARGS_OWNER"] = saved
    if tool_input is None:
        return None, "the provider hook injected nothing"
    injected = tool_input.get("args")
    return (json.loads(injected) if isinstance(injected, str) else injected), None


def engine_error(args, engine):
    """The real engine's own input-check error for `args`, or None when that check passed."""
    run = subprocess.run(["node", str(HERE / "_engine_run.js")],
                         input=json.dumps({"script": str(engine), "args": args, "reply": FIXTURE_RESULT}),
                         capture_output=True, text=True, encoding="utf-8", timeout=240)
    try:
        result = json.loads(run.stdout)
    except ValueError:
        return "engine produced no JSON (rc %s): %s" % (run.returncode, run.stderr[-400:])
    return (result or {}).get("error") if isinstance(result, dict) else None


def check_engine(label, f, engine):
    """prepare's `args.json`, injected by the real provider hook, passes the real engine's input check.

    A hook refusal fails the case; its detail adds the engine's own verdict on the same args, carrying
    only the hook's vocabulary, so a retired-key writer shows the cause the engine names."""
    args = f.args()
    if not args:
        check(label + ": prepare wrote args.json", False, "no args.json")
        return
    injected, refused = inject(args, engine)
    if refused:
        hook = _hook()
        data = hook._session_transport._registry()
        vocabulary = {"name": "anthropic", "ids": sorted(hook._legal_pins("anthropic", data))}
        efforts = hook._declared_efforts("anthropic", data)
        if efforts is not None:
            vocabulary["efforts"] = efforts
        diagnostic = dict(args, __transport=vocabulary)
        if "agents" in args:
            diagnostic["checker"] = {"model": vocabulary["ids"][0],
                                     "effort": (efforts or ["medium"])[0]}
        detail = refused
        raw = engine_error(diagnostic, engine)
        if raw:
            detail += " | the engine says: " + raw
        check(label + ": the provider hook injects the prepared args", False, detail)
        return
    error = engine_error(injected, engine)
    check(label + ": the engine accepts the prepared args", not error, error)


def mode_explore():
    f = Fixture()
    proc = f.prepare(mode="explore", lenses=["exp-integration-surface"],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    jobs_path = f.out / "jobs_p.json"
    document = json.loads(jobs_path.read_text(encoding="utf-8")) if jobs_path.exists() else {}
    jobs = document.get("jobs") if isinstance(document, dict) else None
    check("explore: prepare needs no engine and no checker pin", good, detail)
    check("explore: prepare writes no Workflow args that would pin the session's plan",
          good and not (f.out / "args.json").exists(), sorted(x.name for x in f.out.glob("*")))
    check("explore: the jobs file is the fan-out's object form with a shared context and a spill dir",
          isinstance(jobs, list) and jobs and document.get("contextPath") and document.get("spillDir"), document)
    check("explore: each lens is a lens-kind job listing its rows, with no plan, agentType or schemaFile",
          isinstance(jobs, list) and all(
              j.get("kind") == "lens" and j.get("rows") == {PANEL: EFFORT} and "agentType" not in j
              and not {"alias", "model", "effort", "plan", "schemaFile"} & set(j)
              and Path(j["promptFile"]).is_file() for j in jobs), jobs)

    def brief_contract(job):
        return Path(job["promptFile"]).read_text(encoding="utf-8")

    check("explore: each brief carries the claims contract, whose return channel is the json fence",
          isinstance(jobs, list) and all("CLAIMS CONTRACT" in brief_contract(j) and "```json" in brief_contract(j)
                                         for j in jobs), jobs)
    check("explore: the contract cites the claim checks as the object's one home",
          isinstance(jobs, list) and all("_is_claim" in brief_contract(j) and "_is_checked" in brief_contract(j)
                                         for j in jobs), jobs)
    naming = [p.relative_to(REPO).as_posix() for d in ("tools", "commands", "workflows")
              for p in (REPO / ".claude" / d).rglob("*")
              if p.is_file() and p.suffix in (".py", ".md", ".js", ".json")
              and "explore_fanout.schema" in p.read_text(encoding="utf-8", errors="replace")]
    check("explore: no source path names the deleted claims schema file", not naming, naming)
    import sidecar_fanout
    data = sidecar_fanout.model_registry.load()
    try:
        loaded, shared, _spill = sidecar_fanout.load_jobs(str(jobs_path))
        placed = [sidecar_fanout.normalize_job(j, data, shared) for j in loaded]
        kinds = [sidecar_fanout.check_job_kind(j, j["label"]) for j in placed]
        refused = ""
    except sidecar_fanout.JobError as exc:
        placed, kinds, refused = [], [], str(exc)
    check("explore: the fan-out accepts the jobs file and keeps the lens kind", kinds == ["lens"] and not refused,
          refused or kinds)
    import shlex
    line = next((l[len("next: "):] for l in proc.stdout.splitlines() if l.startswith("next: python3")), "")
    try:
        parsed = sidecar_fanout.build_parser().parse_args(shlex.split(line)[2:])
    except SystemExit:
        parsed = None
    check("explore: the printed next call is the fan-out, detached, over the jobs file",
          parsed is not None and parsed.detach and Path(parsed.jobs).name == "jobs_p.json" and not parsed.review, line)
    check("explore: the printed finish call names explore_result.py over the same jobs file",
          any(l.startswith("then: python3 .claude/tools/explore_result.py") and "jobs_p.json" in l
              for l in proc.stdout.splitlines()), proc.stdout[-500:])
    check("explore: no Workflow call is printed", "Workflow(" not in proc.stdout, proc.stdout[-300:])
    rowed = Fixture()
    proc = rowed.prepare(mode="explore", seats=[{"key": "exp-integration-surface", "rows": {PANEL: EFFORT, ARCH: EFFORT_HI}}],
                         spillDir=str(rowed.scratch("run", "spill")))
    listed = json.loads((rowed.out / "jobs_p.json").read_text(encoding="utf-8"))["jobs"] if proc.returncode == 0 else []
    check("explore: a seat's own rows pass to the fan-out unchanged",
          listed and listed[0].get("rows") == {PANEL: EFFORT, ARCH: EFFORT_HI}, (proc.stderr[-300:], listed))


def explore_manual_seat():
    f = Fixture()
    brief = f.write("ctx/res_official.md", "You are res-official. Fetch the docs.\n")
    proc = f.prepare(mode="explore", prefix="research",
                     seats=[{"key": "res-official", "brief": str(brief), "kind": "author", "tier": "fanout",
                             "rows": {PANEL: EFFORT}}],
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    document = json.loads((f.out / "jobs_research.json").read_text(encoding="utf-8")) if good else {}
    row = (document.get("jobs") or [{}])[0]
    check("explore: a seat with no lens-registry key runs as a fan-out job of its own kind and rows",
          good and row.get("kind") == "author" and row.get("rows") == {PANEL: EFFORT} and row.get("tier") == "fanout"
          and row.get("label") == "research-res-official", (detail, row))
    text = Path(row["promptFile"]).read_text(encoding="utf-8") if row.get("promptFile") else ""
    check("explore: a manual seat's brief keeps its mandate and carries the claims contract",
          "Fetch the docs." in text and "CLAIMS CONTRACT" in text, text[-400:])


def plan_check_roster():
    return [{"key": "plc-test-readiness", "effort": EFFORT},
            {"key": "plc-memory-alignment", "effort": EFFORT}]


def mode_plan_check():
    f = Fixture()
    proc = f.prepare(mode="plan-check", roster=plan_check_roster(),
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan),
                             "files": [str(f.plan)], "baseRef": f.base},
                     subjectPlans=["code:" + str(f.plan)],
                     fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    check("plan-check: prepare writes a dispatchable args.json", good and isinstance(args, dict), detail)
    check("plan-check: args carry agents mapped from the ledger roster",
          isinstance(args, dict) and args.get("agents") and all(a.get("promptPath") for a in args["agents"]), args)
    check("plan-check: each agent names the reviewer kind and no retired agentType (the engine refuses agentType)",
          isinstance(args, dict) and all(a.get("kind") == "reviewer" and "agentType" not in a
                                         for a in args.get("agents") or []), args)
    check_record("plan-check", f, "reviewer",
                 [seat["key"] for seat in plan_check_roster()], "docs/plan.md")
    check("plan-check: the ledger handle names the filled roster file",
          isinstance(args, dict) and (args.get("ledger") or {}).get("roster")
          and (f.out / Path(args["ledger"]["roster"]).name).is_file(), args.get("ledger"))
    check("plan-check: the fold handle carries the snapshot subject",
          isinstance(args, dict) and (args.get("fold") or {}).get("subjects")
          and (f.root / args["fold"]["subjects"][0]["snapshot"]).is_file(), args.get("fold"))
    check("plan-check: the fold tier is printed among the pinned tiers",
          good and "architect" in proc.stdout and "tiers pinned" in proc.stdout, proc.stdout[-500:])
    kfm = [p for p in f.out.glob("**/index.md")]
    check("plan-check: the seat declaring `kfm: index` gets a materialized snapshot",
          bool(kfm)
          and "Known failure modes" in (f.out / "p_plc-memory-alignment.md").read_text(encoding="utf-8")
          if (f.out / "p_plc-memory-alignment.md").exists() else False, list(map(str, kfm)))
    check_engine("plan-check", f, REVIEW_ENGINE)


def owner_pick_survives_the_ledger_plan():
    """A seat the owner pinned (`pick: owner`) keeps its written rung after the ledger rebuilds the roster: the
    rung above the ladder's computed one runs, never the capped one."""
    top = "xhigh" if "xhigh" in EFFORTS else EFFORTS[-1]
    f = Fixture()
    proc = f.prepare(mode="plan-check",
                     roster=[{"key": "plc-test-readiness", "pin": PANEL, "effort": top, "pick": "owner"},
                             {"key": "plc-memory-alignment", "effort": EFFORT}],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan),
                             "files": [str(f.plan)], "baseRef": f.base},
                     subjectPlans=["code:" + str(f.plan)],
                     fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI},
                     spillDir=str(f.scratch("run", "spill")))
    path = f.out / "jobs_p.json"
    jobs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    got = {job.get("seat") or job.get("key"): job.get("effort") for job in jobs}
    check("plan-check: an owner-picked seat keeps its written rung through the ledger plan",
          ok(proc)[0] and got.get("plc-test-readiness") == top, (got, ok(proc)[1]))


def mode_pr_review():
    f = Fixture()
    scope = f.write("scope.json", json.dumps({"files": ["src/thing.py"]}))
    proc = f.prepare(mode="pr-review", roster=[{"key": REVIEW_SEAT, "mandates": ["cq.C"], "effort": EFFORT}],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     prNum=1, branch="proof",
                     ledger={"stage": "pr-review", "subject": "pr:1", "scopeJson": str(scope),
                             "baseRef": f.base},
                     fold={"tier": "executor", "model": PANEL, "effort": EFFORT},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    check("pr-review: prepare writes a dispatchable args.json", good and isinstance(args, dict), detail)
    check_record("pr-review", f, "reviewer", [REVIEW_SEAT], "docs/plan.md")
    check("pr-review: args carry the review ledger handle with its scope",
          isinstance(args, dict) and (args.get("ledger") or {}).get("scopeJson"), args.get("ledger"))
    check("pr-review: prepare prints the crossFamily obligation it composed",
          good and "crossFamily:" in proc.stdout and "required" in proc.stdout, proc.stdout[-800:])
    check_engine("pr-review", f, REVIEW_ENGINE)


def mode_design_redteam():
    f = Fixture()
    design = f.write("docs/design.md", "# Design\n\nA design.\n")
    proc = f.prepare(mode="design-redteam", roster=[{"key": RT_SEAT, "effort": EFFORT_HI}],
                     pins={"panel": ARCH}, efforts={"panel": EFFORT_HI},
                     ledger={"stage": "design", "subject": "doc:" + str(design), "files": [str(design)],
                             "baseRef": f.base},
                     subjectFiles=[str(design)],
                     fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    check("design-redteam: prepare writes a dispatchable args.json with the design stage",
          good and isinstance(args, dict) and (args.get("ledger") or {}).get("stage") == "design",
          (detail, args.get("ledger") if isinstance(args, dict) else None))
    check_record("design-redteam", f, "reviewer", [RT_SEAT], "docs/design.md")
    check_engine("design-redteam", f, REVIEW_ENGINE)


PR_READY_SEATS = [
    {"key": "data-reach", "mandates": ["cq.C"], "tier": "executor", "effort": EFFORT_HI,
     "brief": "You are data-reach. Judge authored-data reachability.\n"},
    {"key": "wire-reach", "mandates": ["cq.D"], "tier": "executor", "effort": EFFORT_HI,
     "brief": "You are wire-reach. Judge production-wiring reachability.\n"},
]


def mode_pr_ready():
    f = Fixture()
    scope = f.write("scope_ready.json", json.dumps({"files": ["src/thing.py"]}))
    proc = f.prepare(mode="pr-ready", seats=PR_READY_SEATS,
                     pins={"data-reach": PANEL, "wire-reach": PANEL},
                     efforts={"panel": EFFORT_HI},
                     ledger={"stage": "pr-ready", "subject": "session:prepsess",
                             "files": [str(f.plan)], "scopeJson": str(scope), "baseRef": f.base},
                     fold={"tier": "executor", "model": PANEL, "effort": EFFORT_HI},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    check("pr-ready: prepare briefs registry-less seats from the spec's own brief text",
          good and isinstance(args, dict) and [a.get("key") for a in args.get("agents") or []]
          == ["data-reach", "wire-reach"], (detail, args))
    check("pr-ready: the spec effort reaches each seat without an --effort flag",
          isinstance(args, dict) and all(a.get("effort") == EFFORT_HI for a in args.get("agents") or []), args)
    check_record("pr-ready", f, "reviewer", [seat["key"] for seat in PR_READY_SEATS], "docs/plan.md")
    check_engine("pr-ready", f, REVIEW_ENGINE)


def mode_session_audit():
    f = Fixture()
    record = {"id": "task-1", "title": "planted", "requirements": ["Ship the planted feature."],
              "exclusions": ["Do not touch the ledger."]}
    (f.tasks / "task-1.json").write_text(json.dumps(record), encoding="utf-8")
    context = f.write("ctx/audit_context.md", "# Context\n\nplanted\n")
    proc = f.prepare(mode="session-audit", roster=[{"key": "sa-harness-quality", "effort": EFFORT}],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     context=str(context), task="task-1",
                     sessionAudit={"sessionBase": f.base, "baseRef": f.base, "files": ["src/thing.py"],
                                   "session": "prepsess", "task": "task-1", "plan": str(f.plan)},
                     ledger={"stage": "candidate", "subject": "session:prepsess",
                             "files": [str(f.plan)], "baseRef": f.base},
                     fold={"tier": "executor", "model": PANEL, "effort": EFFORT},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    ledger = (args or {}).get("ledger") or {}
    check("session-audit: prepare writes a dispatchable args.json", good and isinstance(args, dict), detail)
    check("session-audit: the frozen packet's premise, head and blob handles ride in the ledger",
          ledger.get("premiseCommits") and ledger.get("head") and ledger.get("blobs"), ledger)
    check_record("session-audit", f, "reviewer", ["sa-harness-quality"])
    check("session-audit: the harness seat's brief cites a frozen packet artifact",
          good and (f.out / "p_sa-harness-quality.md").exists()
          and "doctrine-commits.md" in (f.out / "p_sa-harness-quality.md").read_text(encoding="utf-8"),
          proc.stdout[-500:])
    brief = (f.out / "p_sa-harness-quality.md").read_text(encoding="utf-8") if good else ""
    check("session-audit: the brief points at the must-read subject diffs instead of inlining them",
          "diff --git" not in brief and "subject-" in brief, brief[-800:])
    text = context.read_text(encoding="utf-8")
    check("session-audit: the task record's text is saved into the context",
          "Ship the planted feature." in text, text[-500:])
    check("session-audit: the saved task text is never a command the seat must run",
          not any(line.strip().startswith("`python3") or line.strip().startswith("python3 .claude")
                  for line in text.splitlines()), text[-500:])

    bare = Fixture()
    (bare.tasks / "task-1.json").write_text(json.dumps(record), encoding="utf-8")
    proc = bare.prepare(mode="session-audit", seats=[{"key": "sa-harness-quality", "effort": EFFORT}],
                        pins={"panel": PANEL}, efforts={"panel": EFFORT}, task="task-1",
                        subjectFiles=["src/thing.py"],
                        sessionAudit={"sessionBase": bare.base, "baseRef": bare.base, "files": ["src/thing.py"],
                                      "session": "prepsess", "task": "task-1", "plan": str(bare.plan)},
                        fold={"tier": "executor", "model": PANEL, "effort": EFFORT})
    good, detail = ok(proc)
    ledger = (bare.args() or {}).get("ledger") or {}
    code = Fixture()
    (code.tasks / "task-1.json").write_text(json.dumps(record), encoding="utf-8")
    code.write("src/Thing.cs", "class Thing {}\n")
    both = ["src/thing.py", "src/Thing.cs"]
    proc_code = code.prepare(mode="session-audit", seats=[{"key": "sa-harness-quality", "effort": EFFORT}],
                             pins={"panel": PANEL}, efforts={"panel": EFFORT}, task="task-1", subjectFiles=both,
                             sessionAudit={"sessionBase": code.base, "baseRef": code.base, "files": both,
                                           "session": "prepsess", "task": "task-1", "plan": str(code.plan)},
                             fold={"tier": "executor", "model": PANEL, "effort": EFFORT})
    folded = [p for s in ((code.args() or {}).get("fold") or {}).get("subjects") or [] for p in s.get("files") or []]
    check("session-audit: a native fold leaves production code to the sidecar route and names it",
          ok(proc_code)[0] and "src/thing.py" in folded and "src/Thing.cs" not in folded
          and "src/Thing.cs" in proc_code.stdout, (folded, proc_code.stdout[-400:]))
    check("session-audit: the documented spec, with no `ledger`, records against session:<sid> and the task",
          good and ledger.get("subject") == "session:prepsess" and ledger.get("task") == "task-1"
          and ledger.get("stage") == "candidate", (detail, ledger))
    check_engine("session-audit", f, REVIEW_ENGINE)


def doc_defined_none_placeholder():
    f = Fixture()
    designs = f.write("ctx/designs.txt", "planted design list\n")
    audit = dict(mode="session-audit", seats=[{"key": "sa-architecture-sweep", "effort": EFFORT}],
                 pins={"panel": PANEL}, efforts={"panel": EFFORT}, subjectFiles=["src/thing.py"],
                 sessionAudit={"sessionBase": f.base, "baseRef": f.base, "files": ["src/thing.py"],
                               "session": "prepsess"},
                 fold={"tier": "executor", "model": PANEL, "effort": EFFORT})
    built = f.prepare(subs=["DESIGN_LIST=" + str(designs)], **audit)
    brief = f.out / "p_sa-architecture-sweep.md"
    check("a placeholder its doc defines as `none` (SHORT_PLAN, no plan) builds the brief with `none`",
          built.returncode == 0 and brief.exists() and "{{SHORT_PLAN}}" not in brief.read_text(encoding="utf-8"),
          (built.stdout[-300:], built.stderr[-600:]))
    g = Fixture()
    audit["sessionAudit"] = dict(audit["sessionAudit"], sessionBase=g.base, baseRef=g.base)
    undefined = g.prepare(**audit)
    check("a placeholder no doc defines as `none` (DESIGN_LIST) still refuses, and names only it",
          undefined.returncode != 0 and "['DESIGN_LIST']" in undefined.stderr, undefined.stderr[-600:])


def over_window_refusal():
    f = Fixture()
    big = f.write("docs/big.md", "x\n" * 1500000)
    proc = f.prepare(mode="design-redteam", roster=[{"key": RT_SEAT, "effort": EFFORT_HI}],
                     pins={"panel": ARCH}, efforts={"panel": EFFORT_HI},
                     ledger={"stage": "design", "subject": "doc:" + str(big), "files": [str(big)],
                             "baseRef": f.base},
                     subjectFiles=[str(big)],
                     fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI})
    check("an over-window required input refuses the whole run and names the seat",
          proc.returncode != 0 and RT_SEAT in (proc.stdout + proc.stderr), proc.stderr[-600:])
    check("an over-window refusal leaves no dispatchable args.json", not (f.out / "args.json").exists(),
          list(f.out.glob("*")) if f.out.exists() else "no out")


def refusals_state_their_shape():
    f = Fixture()
    bad_mode = f.prepare(mode="nope")
    check("a spec refusal ends with an accepts: line and an example: line",
          bad_mode.returncode != 0 and "\naccepts: " in bad_mode.stderr and "\nexample: " in bad_mode.stderr,
          bad_mode.stderr[-600:])
    missing = f.root / "absent_context.md"
    if "plan_check" not in SHIPPED:
        print("skip a context refusal case: the plan_check registry is not installed")
        return
    no_context = f.prepare(mode="plan-check", roster=plan_check_roster(),
                           pins={"panel": PANEL}, efforts={"panel": EFFORT},
                           ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan),
                                   "files": [str(f.plan)], "baseRef": f.base},
                           subjectPlans=["code:" + str(f.plan)], context=str(missing))
    check("a context refusal names the spec's `context` key and the missing path, never a --context flag",
          no_context.returncode != 0 and "--context" not in no_context.stderr
          and "`context`" in no_context.stderr and str(missing) in no_context.stderr, no_context.stderr[-600:])


def sidecar_target():
    f = Fixture()
    proc = f.prepare(mode="design-redteam", roster=[{"key": RT_SEAT, "effort": EFFORT_HI}],
                     target="sidecar", pins={"panel": PANEL}, efforts={RT_SEAT: EFFORT_HI},
                     ledger={"stage": "design", "subject": "doc:" + str(f.plan), "files": [str(f.plan)],
                             "baseRef": f.base},
                     subjectFiles=[str(f.plan)],
                     fold={"tier": "architect", "model": PANEL, "effort": EFFORT_HI},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    jobs = f.out / "jobs_p.json"
    roster = f.out / "roster.json"
    check("sidecar: a spec carrying the seat effort and no --effort flag prepares", good, detail)
    check("sidecar: prepare writes the jobs file in the sidecar schema",
          jobs.is_file() and all(j.get("kind") == "reviewer" for j in json.loads(jobs.read_text(encoding="utf-8"))),
          jobs.read_text(encoding="utf-8")[:400] if jobs.exists() else "absent")
    sys.path.insert(0, str(TOOLS))
    import review_result
    check("sidecar: the ledger roster prep writes passes sidecar_fanout's --ledger-roster check",
          roster.is_file() and review_result._roster_problems(str(roster)) == [],
          review_result._roster_problems(str(roster)) if roster.exists() else "absent")
    import shlex
    import sidecar_fanout
    line = next((l[len("next: "):] for l in proc.stdout.splitlines() if l.startswith("next: python3")), "")
    try:
        parsed = sidecar_fanout.build_parser().parse_args(shlex.split(line)[2:])
    except SystemExit:
        parsed = None
    check("sidecar: the printed next call carries the spec's fold and ledger",
          parsed is not None and parsed.fold_alias == PANEL and parsed.fold_effort == EFFORT_HI
          and parsed.fold_subject and parsed.ledger_stage == "design"
          and parsed.ledger_subject == "doc:" + str(f.plan).replace("\\", "/"), line)


def stale_kfm_snapshot():
    f = Fixture()
    f.prepare(mode="plan-check", roster=plan_check_roster(), pins={"panel": PANEL},
              efforts={"panel": EFFORT},
              ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan), "files": [str(f.plan)],
                      "baseRef": f.base},
              subjectPlans=["code:" + str(f.plan)],
              fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI},
              spillDir=str(f.scratch("run", "spill")))
    import hashlib
    sys.path.insert(0, str(TOOLS))
    import kfm
    sha = hashlib.sha256(kfm.CATALOG.read_bytes()).hexdigest()[:12]
    index = f.out / "kfm" / sha / "index.md"
    stale = index.is_file()
    if stale:
        index.write_text("# Known failure modes\n\nSTALE PLANT\n", encoding="utf-8")
        shutil.rmtree(f.run_dir / "out2", ignore_errors=True)
        f.out = f.run_dir / "out2"
        f.prepare(mode="plan-check", roster=plan_check_roster(), pins={"panel": PANEL},
                  efforts={"panel": EFFORT},
                  ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan), "files": [str(f.plan)],
                          "baseRef": f.base},
                  subjectPlans=["code:" + str(f.plan)],
                  fold={"tier": "architect", "model": ARCH, "effort": EFFORT_HI},
                  spillDir=str(f.scratch("run", "spill")))
        index = f.out / "kfm" / sha / "index.md"
    check("a stale KFM snapshot is rebuilt before a brief names it",
          stale and index.is_file() and "STALE PLANT" not in index.read_text(encoding="utf-8"),
          index if index.exists() else "absent")


def task_text_not_backticked():
    f = Fixture()
    record = {"id": "task-2", "title": "planted",
              "requirements": ["Every seat judges the planted requirement."],
              "exclusions": ["Never run `python3 .claude/tools/review_ledger.py record`."]}
    (f.tasks / "task-2.json").write_text(json.dumps(record), encoding="utf-8")
    context = f.write("ctx/task_context.md", "# Context\n")
    proc = f.prepare(mode="explore", lenses=["exp-integration-surface"], task="task-2",
                     engine=str(f.root / "engine.js"),
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     context=str(context),
                     checker={"model": PANEL, "effort": EFFORTS[0]})
    text = context.read_text(encoding="utf-8")
    check("a spec naming the task record saves its text into the context",
          proc.returncode == 0 and "Every seat judges the planted requirement." in text,
          (proc.stderr[-400:], text[-300:]))
    check("the saved task text is not a command line the seat must run",
          "python3 .claude/tools/review_ledger.py record" in text
          and not any(line.strip().startswith("python3 .claude") for line in text.splitlines()),
          text[-600:])


def later_round_diff():
    f = Fixture()
    reviewed = f.write("docs/reviewed_plan.md", "# Plan\n\nold line\n")
    context = f.write("ctx/round_context.md", "# Context\n")
    proc = f.prepare(mode="explore", lenses=["exp-integration-surface"],
                     engine=str(f.root / "engine.js"),
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     context=str(context), round={"reviewed": str(reviewed), "current": str(f.plan)},
                     checker={"model": PANEL, "effort": EFFORTS[0]})
    text = context.read_text(encoding="utf-8")
    check("a later-round spec writes the change diff into the seat's context",
          proc.returncode == 0 and "Change since the reviewed copy" in text and "```diff" in text,
          (proc.stderr[-300:], text[-400:]))


def mixed_roster_code_seat_keeps_its_files():
    f = Fixture()
    checklist = f.write("ctx/cds.md", "C1. planted check\n")
    proc = f.prepare(mode="session-audit",
                     seats=[{"key": "sa-design-semantics", "effort": EFFORT}, {"key": "sa-harness-quality", "effort": EFFORT}],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT}, subs=[f"CHECKLIST_CDS={checklist}"],
                     seatFiles={"sa-design-semantics": ["src/thing.py"]}, subjectFiles=["src/thing.py"],
                     sessionAudit={"sessionBase": f.base, "baseRef": f.base, "files": ["src/thing.py"],
                                   "session": "prepsess"})
    listing = f.out / "must_sa-design-semantics.txt"
    paths = listing.read_text(encoding="utf-8").split() if listing.exists() else []
    check("mixed roster: the code seat reads its own files, not the harness packet",
          any(p.replace("\\", "/").endswith("src/thing.py") for p in paths)
          and not any("commit-" in p or "doctrine-commits" in p for p in paths), (paths, proc.stdout[-300:]))
    harness = f.out / "must_sa-harness-quality.txt"
    check("mixed roster: the harness seat still reads the frozen packet",
          harness.exists() and "doctrine-commits" in harness.read_text(encoding="utf-8"), proc.stdout[-300:])


def base_sizes_past_a_command_line():
    tmp = Path(tempfile.mkdtemp(prefix="lbp_sizes_"))
    try:
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        (tmp / "kept.md").write_text("twelve bytes", encoding="utf-8", newline="\n")
        subprocess.run(["git", "-C", str(tmp), "add", "kept.md"], check=True)
        subprocess.run(["git", "-C", str(tmp), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a"],
                       check=True)
        files = ["kept.md"] + [f".claude/commands/subject_list_case_{i:05d}.md" for i in range(1500)]
        check("the subject list is longer than a Windows command line", sum(len(f) + 1 for f in files) > 32767)
        sizes = lens_briefs._base_sizes(tmp, "HEAD", files)
        check("base sizes read a subject list longer than a command line", sizes == {"kept.md": 12}, repr(sizes)[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def record_contract():
    """The prepared job record's one contract: a complete record passes, and each planted omission fails."""
    tmp = Path(tempfile.mkdtemp(prefix="lb_record_"))
    brief = tmp / "brief.md"
    brief.write_text("brief\n", encoding="utf-8")
    listed = tmp / "subject.md"
    listed.write_text("subject\n", encoding="utf-8")
    record = {"label": "p-seat", "seat": "seat", "mandates": ["seat"],
              "mustRead": [{"path": listed.as_posix(), "lines": 1}],
              "promptPath": brief.as_posix(), "model": PANEL, "tier": "executor",
              "effort": EFFORT, "kind": "reviewer"}
    verdict = lens_briefs.record_problems([record], kind="reviewer")
    check("a complete prepared record holds the one contract", verdict == [], verdict)
    breaks = [(f"missing {field}", {k: v for k, v in record.items() if k != field}, field)
              for field in ("label", "mandates", "mustRead", "promptPath", "effort", "kind")]
    breaks.append(("prompt path not POSIX", dict(record, promptPath=brief.as_posix().replace("/", "\\")), None))
    breaks.append(("another build's kind", dict(record, kind="author"), None))
    unmet = []
    for name, planted, field in breaks:
        problems = lens_briefs.record_problems([planted], kind="reviewer")
        if not problems or (field and field not in " ".join(problems)):
            unmet.append((name, problems))
    check("the one contract refuses every planted break, naming a missing field", unmet == [], unmet)
    side = {"label": "p-seat", "mandates": ["seat"], "mustRead": record["mustRead"],
            "promptFile": brief.as_posix(), "alias": "probe-alias", "tier": "architect", "effort": "high",
            "kind": "reviewer", "schemaFile": (tmp / "schema.json").as_posix(), "workdir": tmp.as_posix(),
            "contextFiles": [listed.as_posix()]}
    (tmp / "schema.json").write_text("{}", encoding="utf-8")
    side_problems = lens_briefs.record_problems([side], kind="reviewer")
    check("a complete sidecar record holds the same contract", side_problems == [], side_problems)


def spec_refused_before_any_write():
    f = Fixture()
    common = dict(mode="explore", lenses=["exp-integration-surface"], engine=str(f.root / "engine.js"),
                  pins={"panel": PANEL}, efforts={"panel": EFFORT}, spillDir=str(f.scratch("run", "spill")),
                  checker={"model": PANEL, "effort": EFFORT})
    proc = f.prepare(subjectFiles=[], seatFiles={"exp-integration-surface": [str(f.plan)]}, fold={"tier": "architect", "model": PANEL, "effort": EFFORT_HI}, **common)
    check("a fold with no subjectFiles is refused naming `subjectFiles`",
          proc.returncode != 0 and "subjectFiles" in proc.stderr, proc.stderr[-400:])
    check("the refused fold spec wrote no brief", not f.out.exists() or not any(x.is_file() for x in f.out.rglob("*")),
          sorted(x.name for x in f.out.glob("*")) if f.out.exists() else "")
    proc = f.prepare(sessionAudit={"plan": "none", "baseRef": f.base}, **common)
    check("a sessionAudit plan that is no file is refused naming `plan`",
          proc.returncode != 0 and "sessionAudit.plan" in proc.stderr, proc.stderr[-400:])
    check("the refused plan spec wrote no brief", not f.out.exists() or not any(x.is_file() for x in f.out.rglob("*")),
          sorted(x.name for x in f.out.glob("*")) if f.out.exists() else "")
    top = f.prepare(agentType="lane-author", **common)
    check("a spec naming the retired `agentType` is refused naming `kind`",
          top.returncode != 0 and "agentType" in top.stderr and "kind" in top.stderr, top.stderr[-400:])
    seat = f.prepare(seats=[{"key": "exp-integration-surface", "agentType": "lane-author"}],
                     pins={"panel": PANEL}, efforts={"panel": EFFORT}, mode="explore",
                     engine=str(f.root / "engine.js"),
                     checker={"model": PANEL, "effort": EFFORT})
    check("a seat naming the retired `agentType` is refused naming `kind`",
          seat.returncode != 0 and "agentType" in seat.stderr and "kind" in seat.stderr, seat.stderr[-400:])


def default_panels_run_through_the_fanout():
    """A spec naming no `target` prepares the fan-out's jobs file and prints the fan-out call, never a Workflow call:
    the fan-out places each seat by the ladder's selection line. `target: "native"` is the explicit engine path."""
    sys.path.insert(0, str(TOOLS))
    import shlex
    import sidecar_fanout
    builds = []
    if "plan_check" in SHIPPED:
        f = Fixture()
        builds.append(("plan-check", f, dict(
            mode="plan-check", roster=plan_check_roster(), pins={"panel": PANEL}, efforts={"panel": EFFORT},
            ledger={"stage": "plan-check", "subject": "plan:" + str(f.plan), "files": [str(f.plan)],
                    "baseRef": f.base},
            subjectPlans=["code:" + str(f.plan)], fold={"tier": "executor", "model": PANEL, "effort": EFFORT},
            spillDir=str(f.scratch("run", "spill")))))
    if "review" in SHIPPED:
        f = Fixture()
        scope = f.write("scope.json", json.dumps({"files": ["src/thing.py"]}))
        builds.append(("pr-review", f, dict(
            mode="pr-review", roster=[{"key": REVIEW_SEAT, "mandates": ["cq.C"], "effort": EFFORT}],
            pins={"panel": PANEL}, efforts={"panel": EFFORT}, prNum=1, branch="proof",
            ledger={"stage": "pr-review", "subject": "pr:1", "scopeJson": str(scope), "baseRef": f.base},
            fold={"tier": "executor", "model": PANEL, "effort": EFFORT},
            spillDir=str(f.scratch("run", "spill")))))
        f = Fixture()
        scope = f.write("scope_ready.json", json.dumps({"files": ["src/thing.py"]}))
        builds.append(("pr-ready", f, dict(
            mode="pr-ready", seats=PR_READY_SEATS, pins={"data-reach": PANEL, "wire-reach": PANEL},
            efforts={"panel": EFFORT_HI},
            ledger={"stage": "pr-ready", "subject": "session:prepsess", "files": [str(f.plan)],
                    "scopeJson": str(scope), "baseRef": f.base},
            fold={"tier": "executor", "model": PANEL, "effort": EFFORT_HI},
            spillDir=str(f.scratch("run", "spill")))))
    if "redteam" in SHIPPED:
        f = Fixture()
        builds.append(("design-redteam", f, dict(
            mode="design-redteam", roster=[{"key": RT_SEAT, "effort": EFFORT_HI}], pins={"panel": PANEL},
            efforts={RT_SEAT: EFFORT_HI},
            ledger={"stage": "design", "subject": "doc:" + str(f.plan), "files": [str(f.plan)], "baseRef": f.base},
            subjectFiles=[str(f.plan)], fold={"tier": "architect", "model": PANEL, "effort": EFFORT_HI},
            spillDir=str(f.scratch("run", "spill")))))
    if "session_audit" in SHIPPED:
        f = Fixture()
        (f.tasks / "task-1.json").write_text(json.dumps({"id": "task-1", "title": "planted",
                                                         "requirements": ["Ship it."], "exclusions": []}),
                                             encoding="utf-8")
        builds.append(("session-audit", f, dict(
            mode="session-audit", roster=[{"key": "sa-harness-quality", "effort": EFFORT}],
            pins={"panel": PANEL}, efforts={"panel": EFFORT}, task="task-1",
            sessionAudit={"sessionBase": f.base, "baseRef": f.base, "files": ["src/thing.py"],
                          "session": "prepsess", "task": "task-1", "plan": str(f.plan)},
            ledger={"stage": "candidate", "subject": "session:prepsess", "files": [str(f.plan)], "baseRef": f.base},
            fold={"tier": "executor", "model": PANEL, "effort": EFFORT},
            spillDir=str(f.scratch("run", "spill")))))
    check("every review mode the registries ship is built", len(builds) >= 1, [b[0] for b in builds])
    for mode, f, fields in builds:
        proc = f.prepare_as_written(**fields)
        jobs_path = f.out / "jobs_p.json"
        document = json.loads(jobs_path.read_text(encoding="utf-8")) if jobs_path.is_file() else None
        jobs = document["jobs"] if isinstance(document, dict) else document
        check("%s: with no target, prepare writes the fan-out's jobs file (alias, promptFile, kind reviewer, schemaFile)"
              % mode, proc.returncode == 0 and isinstance(jobs, list) and jobs
              and all(j.get("kind") == "reviewer" and j.get("alias") and j.get("promptFile") and j.get("schemaFile")
                      and "rows" not in j and "plan" not in j for j in jobs), (proc.stdout[-300:], proc.stderr[-800:]))
        check("%s: with no target, no Workflow call and no engine args are written" % mode,
              "Workflow(" not in proc.stdout and not (f.out / "args.json").exists(), proc.stdout[-400:])
        line = next((l[len("next: "):] for l in proc.stdout.splitlines() if l.startswith("next: python3")), "")
        try:
            parsed = sidecar_fanout.build_parser().parse_args(shlex.split(line)[2:])
        except SystemExit:
            parsed = None
        check("%s: the next call is the fan-out's --review run, detached, carrying the spec's ledger" % mode,
              parsed is not None and parsed.review and parsed.detach and parsed.ledger_stage
              == fields["ledger"]["stage"], line)
        check("%s: the printed text says what the fan-out prints and what closes the panel" % mode,
              "--review-close" in proc.stdout and "NATIVE" in proc.stdout, proc.stdout[-500:])
    if builds:
        mode, f, fields = builds[0]
        proc = f.prepare_as_written(**dict(fields, target="native"))
        check("an explicit native target still writes the engine args and the Workflow call",
              (f.out / "args.json").is_file() and "Workflow(" in proc.stdout, (proc.stdout[-300:], proc.stderr[-500:]))


def printed_tiers_are_the_guards():
    """The `tiers pinned` line names the slices the fan-out's doctrine gate asks for the printed call: the gate's own
    `launch_tiers` over that call (a job's written tier, else its kind row's, and a fold's author row)."""
    import re
    import shlex
    import types
    sys.path[:0] = [str(TOOLS), str(TOOLS.parent / "hooks")]
    import sidecar_dispatch_context as gate
    cases = {
        "a fold on the sidecar route": dict(
            roster=[{"key": RT_SEAT, "effort": EFFORT_HI}], fold={"tier": "architect", "model": PANEL, "effort": EFFORT_HI}),
        "seats with no fold": dict(roster=[{"key": RT_SEAT, "effort": EFFORT_HI}]),
    }
    for name, extra in cases.items():
        f = Fixture()
        proc = f.prepare(mode="design-redteam", target="sidecar", pins={"panel": PANEL}, efforts={RT_SEAT: EFFORT_HI},
                         ledger={"stage": "design", "subject": "doc:" + str(f.plan), "files": [str(f.plan)],
                                 "baseRef": f.base},
                         subjectFiles=[str(f.plan)], spillDir=str(f.scratch("run", "spill")), **extra)
        good, detail = ok(proc)
        line = next((l[len("next: "):] for l in proc.stdout.splitlines() if l.startswith("next: python3")), "")
        match = re.search(r"tiers pinned: ([a-z, ]+)", proc.stdout)
        printed = {t.strip() for t in match.group(1).split(",") if t.strip()} if match else set()
        fanout = types.SimpleNamespace(args=shlex.split(line)[2:])
        asked = set(gate.launch_tiers(None, fanout, str(f.root)) or ())
        check("the pinned-tier line is the doctrine gate's own set: " + name,
              good and bool(asked) and printed == asked, "printed %s, the gate asks %s\n%s" % (sorted(printed), sorted(asked), detail))


def main():
    check("test_lens_briefs_prepare.py finds the tool it proves", (TOOLS / "lens_briefs.py").is_file())
    for case in (mode_explore, explore_manual_seat, mode_plan_check, owner_pick_survives_the_ledger_plan, mode_pr_review, mode_design_redteam, mode_pr_ready,
                 mode_session_audit, over_window_refusal, refusals_state_their_shape, sidecar_target, stale_kfm_snapshot,
                 task_text_not_backticked, later_round_diff, base_sizes_past_a_command_line,
                 mixed_roster_code_seat_keeps_its_files, spec_refused_before_any_write,
                 doc_defined_none_placeholder, default_panels_run_through_the_fanout,
                 printed_tiers_are_the_guards, record_contract):
        if NEEDS.get(case.__name__, "") and NEEDS[case.__name__] not in SHIPPED:
            print("skip %s: the %s lens registry is not installed" % (case.__name__, NEEDS[case.__name__]))
            continue
        try:
            case()
        except Exception as exc:  # a crash is a failure, never a skip
            check("%s ran" % case.__name__, False, repr(exc))
    print("\n%d failed" % len(FAILURES) if FAILURES else "\nall pass")
    for failure in FAILURES:
        print("  FAIL " + failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
