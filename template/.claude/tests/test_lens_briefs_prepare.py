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
sys.path[:0] = [str(TOOLS), str(REPO / ".claude" / "hooks")]
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


def mode_explore():
    f = Fixture()
    proc = f.prepare(mode="explore", lenses=["exp-integration-surface"],
                     engine=str(f.root / "engine.js"),
                     pins={"panel": PANEL}, efforts={"panel": EFFORT},
                     checker={"model": PANEL, "effort": EFFORTS[0]},
                     spillDir=str(f.scratch("run", "spill")))
    good, detail = ok(proc)
    args = f.args()
    check("explore: prepare writes a dispatchable args.json", good and isinstance(args, dict), detail)
    check("explore: args carry lenses, a context prefix and the checker",
          isinstance(args, dict) and isinstance(args.get("lenses"), list) and args["lenses"]
          and args.get("contextPrefixPath") and args.get("checker", {}).get("model"), args)
    check("explore: each lens carries its own model and effort",
          isinstance(args, dict) and all(j.get("model") and j.get("effort") for j in args.get("lenses") or []), args)
    check("explore: the brief file each lens names exists",
          isinstance(args, dict) and all(Path(j["promptPath"]).is_file() for j in args.get("lenses") or []), args)
    check("explore: the printed next call passes the args file",
          good and "Workflow(" in proc.stdout and "argsFile" in proc.stdout, proc.stdout[-500:])


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
    check("pr-review: args carry the review ledger handle with its scope",
          isinstance(args, dict) and (args.get("ledger") or {}).get("scopeJson"), args.get("ledger"))
    check("pr-review: prepare prints the crossFamily obligation it composed",
          good and "crossFamily:" in proc.stdout and "required" in proc.stdout, proc.stdout[-800:])


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
    check("pr-ready: each registry-less seat's brief file exists",
          isinstance(args, dict) and all(Path(a["promptPath"]).is_file() for a in args.get("agents") or []), args)


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
          and parsed.ledger_subject == "doc:" + str(f.plan), line)


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


def main():
    check("test_lens_briefs_prepare.py finds the tool it proves", (TOOLS / "lens_briefs.py").is_file())
    for case in (mode_explore, mode_plan_check, mode_pr_review, mode_design_redteam, mode_pr_ready,
                 mode_session_audit, over_window_refusal, refusals_state_their_shape, sidecar_target, stale_kfm_snapshot,
                 task_text_not_backticked, later_round_diff, base_sizes_past_a_command_line,
                 mixed_roster_code_seat_keeps_its_files):
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
