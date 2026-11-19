#!/usr/bin/env python3
"""Re-runnable proof for tools/lens_briefs.py: the caller passes model pins and the effort rung. A seat's tier defaults to its lens's. An absent effort is refused, never derived from that tier.

The pins below are real host aliases; the
tier cases name tiers only.

The sidecar cases resolve their alias through a synthetic `HARNESS_MODEL_REGISTRY` (one extra row
derived from a real row, so the registry's own validator still vets it): the sidecar target accepts
any registered alias, so a fixed host alias would make the case depend on this machine's roster.

    python3 .claude/tests/test_lens_briefs_project.py
"""
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BRIEFS = os.path.join(ROOT, ".claude", "tools", "lens_briefs.py")
FANOUT = os.path.join(ROOT, ".claude", "tools", "sidecar_fanout.py")
REGISTRY = os.path.join(ROOT, ".claude", "reference", "external_models.json")
# The refusal cases plant no owner text: a caller's session id would make them read its owner turns.
os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
# A delegate session exports CLAUDE_CODE_SIDECAR and its own session id; these fixtures plant their own
# session text, so a tool call here inherits neither.
BASE_ENV = {name: value for name, value in os.environ.items()
            if name != "CLAUDE_CODE_SESSION_ID" and not name.startswith("CLAUDE_CODE_SIDECAR")}


def _planted_head():
    """A git root holding the files `citers` resolves at HEAD. A consumer checkout has no commit and the
    baseline template sits inside a repo whose HEAD names other paths, so the sweep is pointed at this one."""
    import shutil
    head = Path(tempfile.mkdtemp(prefix="lens_head_"))
    subprocess.run(["git", "init", "-q", str(head)], check=True, capture_output=True)
    for rel in (".claude/tools/lens.py", ".claude/commands/plan_check.md"):
        dest = head / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), dest)
    for argv in (["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "head"]):
        subprocess.run(["git", "-C", str(head), *argv], check=True, capture_output=True)
    return {"GIT_DIR": str(head / ".git"), "GIT_WORK_TREE": str(ROOT)}


def _head_has_subject():
    return subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:.claude/tools/lens.py"],
                          capture_output=True).returncode == 0


# Empty in a checkout whose HEAD carries the tree (the project, the shipped-set judge); planted otherwise.
CITER_ENV = {} if _head_has_subject() else _planted_head()

sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
import lens  # noqa: E402

# The review registry's key prefix, read from its one owner (adaptation.json `lens_prefixes`, via lens.py);
# the suffixes below name review lenses every registry of this shape carries.
REVIEW = lens.REGISTRIES["review"][1]
CODE_REVIEWER = REVIEW + "code-reviewer"
ERROR_HUNTER = REVIEW + "error-hunter"
TEST_ANALYZER = REVIEW + "test-analyzer"
PANEL_LENS = REVIEW + "type-reviewer"
NOT_A_LENS = REVIEW + "not-a-lens"
# A native pin that serves every lens tier the fixtures use: a seat's pin must be a row serving its lens tier.
with open(REGISTRY, encoding="utf-8") as _fh:
    _SERVING_ROW = next(m for m in json.load(_fh)["models"] if m.get("transport") == "anthropic"
                        and {"architect", "executor", "fanout"} <= set(m.get("roles") or []))
SERVING_PIN, SERVING_ALIAS = _SERVING_ROW["id"], _SERVING_ROW["alias"]

# Captured by the fixture run inside `main()` (same seats, prefix, panel pin and tier), with the context path normalized to `<CTX>`.
# The native route's bytes are a contract for every existing caller, so a refactor that reaches it must break these.
# The digests also cover the templates' text, so a template edit re-captures them here.
# Preloaded doctrine bodies are normalized out: the preload cases prove those bytes, and a doctrine edit is not a renderer change.
# The KFM snapshot's root and sha are normalized to `.claude/scratch/kfm/<SHA>/`.
NATIVE_FIXTURE = {
    "fx-plc-memory-alignment": "76d1efa908361c97",
    "fx-plc-instruction-quality+plc-doctrine-consistency": "325e043e38fae9ec",
}

# Normalize the temp output root to <OUT>; code plans are never swept, so the code-subject fixtures above need none.
SMALL_INBOUND_FIXTURE = "f8ff52399b38b232"

SIDECAR_KEYS = ["label", "alias", "promptFile", "effort", "kind", "schemaFile",
                "workdir", "contextFiles", "mandates", "mustRead", "tier"]

# A planted window: usable room is the effective window less the output cap (model_registry max-output),
# 100000 - 10000 = 90000 tokens, which is 225000 bytes at the 2.5 characters per token the
# `.claude/tools/dispatch_size.py` counts (`CHARS_PER_TOKEN`).
SMALL_LIMITS = {"contextTokens": 100000, "maxOutputTokens": 10000}
SMALL_USABLE_TOKENS = 90000
# A native (Anthropic) seat compacts earlier: the client keeps a further 13000 tokens in reserve (CLIENT_RESERVE), so 77000.
NATIVE_USABLE_TOKENS = 77000


# Every brief run binds `kfm_section` to this proof's temp root, so a KFM-declaring seat never
# materializes into the live checkout's `.claude/scratch` (harness_tooling.md: a proof writes only
# under its own temp root).
KFM_ROOT = tempfile.mkdtemp(prefix="lens_briefs_kfm_")
KFM_LAUNCH = ("import functools, sys; sys.path.insert(0, sys.argv[1]); import lens_briefs; "
              "lens_briefs.kfm_section = functools.partial(lens_briefs.kfm_section, sys.argv[2]); "
              "sys.argv = [lens_briefs.__file__] + sys.argv[3:]; lens_briefs.main()")


MUST_LIST = os.path.join(KFM_ROOT, "must_read.txt")
with open(MUST_LIST, "w", encoding="utf-8") as _fh:
    _fh.write(".claude/tools/lens.py\n")


JOB_MAP = "\n## Job map\n\n| Slice | Job |\n|---|---|\n| S1 | inline |\n"
SUBJECT_PLAN = os.path.join(KFM_ROOT, "subject_plan.md")
with open(SUBJECT_PLAN, "w", encoding="utf-8") as _fh:
    _fh.write("# Plan\n" + JOB_MAP)


CITER_LENSES = ("plc-evidence-grounding", "plc-doctrine-consistency")


def run(args, cwd=ROOT, env=None, must_read=True, plan=True, subject=True):
    """One lens_briefs run; a case naming no must-read flag gets the shared one-entry panel list, and a case
    naming several seat keys gets them as a `--plan` roster (`plan=False` passes the argv as written). A case
    naming a lens that declares `citers: plan` and no `--subject-plan` gets a code subject, which sweeps nothing
    (`subject=False` passes the argv as written)."""
    if subject and "--subject-plan" not in args and any(lens in " ".join(args) for lens in CITER_LENSES):
        args = args + ["--subject-plan", "code:" + SUBJECT_PLAN]
    if must_read and "--must-read" not in args and "--must-read-for" not in args:
        args = args + ["--must-read", MUST_LIST]
    if plan and "--keys" in args:
        start = args.index("--keys") + 1
        stop = next((i for i in range(start, len(args)) if args[i].startswith("--")), len(args))
        if stop - start > 1:
            path = os.path.join(KFM_ROOT, "plan_%d.json" % len(os.listdir(KFM_ROOT)))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"roster": [{"key": key} for key in args[start:stop]], "dropped": []}, fh)
            args = args[:start - 1] + ["--plan", path] + args[stop:]
    return subprocess.run([sys.executable, "-c", KFM_LAUNCH, os.path.dirname(BRIEFS), KFM_ROOT] + args,
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=120,
                          env=env or dict(BASE_ENV, PYTHONUTF8="1"))


def lines_of(rel):
    """The line count the Read tool numbers for a repo file: its lines, a final unterminated one included."""
    with open(os.path.join(ROOT, rel), encoding="utf-8", newline="") as fh:
        return len(fh.read().splitlines())


def kfm_normalized(text):
    """A brief with this proof's KFM root and the catalog sha replaced by the live layout's `<SHA>` form."""
    root = KFM_ROOT.replace(os.sep, "/")
    return re.sub(re.escape(root) + r"/[0-9a-f]{12}/", ".claude/scratch/kfm/<SHA>/", text)


PRELOAD_BLOCK = re.compile(r"^> Preloaded doctrine: (\S+)\n(?:>.*\n?)*", re.M)


def doctrine_normalized(text):
    """A brief with each preloaded doctrine body replaced by `> <DOCTRINE>`; the preload cases prove those bytes."""
    return PRELOAD_BLOCK.sub(lambda m: "> Preloaded doctrine: %s\n> <DOCTRINE>\n" % m.group(1), text)


def run_fanout(args, env):
    return subprocess.run([sys.executable, FANOUT] + args, capture_output=True, text=True,
                          encoding="utf-8", cwd=ROOT, timeout=120, env=env)


def sidecar_registry(path):
    """A registry holding one extra row a sidecar seat can be pinned to, vetted by the real validator."""
    with open(REGISTRY, encoding="utf-8") as fh:
        data = json.load(fh)
    row = copy.deepcopy(data["models"][0])
    row.update({"id": "sideseat-probe-1", "alias": "sideseat", "family": "sideseat-probe",
                "order": 999, "version": "SideSeat-Probe-1", "effortValues": ["low", "high", "max"],
                "effort": {"evidence": "see-ladder"}, "roles": ["architect", "executor", "fanout"]})
    row.pop("supplementary", None)
    data["models"].append(row)
    alternate = copy.deepcopy(row)
    alternate.update({"id": "sideseat-probe-2", "alias": "sideseat2",
                      "family": "sideseat-probe-2", "version": "SideSeat-Probe-2", "order": 1000})
    data["models"].append(alternate)
    scout = copy.deepcopy(row)
    scout.update({"id": "sideseat-probe-scout", "alias": "sideseat-scout",
                  "family": "sideseat-probe-scout", "version": "SideSeat-Probe-Scout",
                  "order": 1001, "roles": ["scout"]})
    data["models"].append(scout)
    small = copy.deepcopy(row)
    small.update({"id": "sideseat-probe-small", "alias": "sideseat-small", "family": "sideseat-probe-small",
                  "version": "SideSeat-Probe-Small", "order": 1002, "limits": dict(SMALL_LIMITS)})
    data["models"].append(small)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def main():
    cases = []
    tmp = tempfile.mkdtemp(prefix="lens_briefs_")
    ctx = os.path.join(tmp, "ctx.md")
    with open(ctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nnothing\n")
    base = ["--keys", CODE_REVIEWER, PANEL_LENS, "--out", tmp, "--prefix", "t1",
            "--pr-num", "1", "--branch", "x", "--context-path", ctx]

    selection = {"subject": "session:tracer", "lineageFingerprint": "lineage", "policyFingerprint": "policy"}
    plan_path = os.path.join(tmp, "selection.json")
    with open(plan_path, "w", encoding="utf-8") as fh:
        json.dump({"roster": [{"key": "plc-memory-alignment", "model": SERVING_PIN}],
                   "reviewerSelection": selection}, fh)
    r = run(["--plan", plan_path, "--target", "sidecar", "--out", tmp, "--prefix", "selection",
             "--pr-num", "1", "--branch", "x", "--context-path", ctx, "--model", SERVING_PIN,
             "--effort", "medium"])
    if r.returncode:
        print("E1 materializer fixture refused:", r.stderr)
    artifact = json.load(open(os.path.join(tmp, "jobs_selection.json"), encoding="utf-8")) if r.returncode == 0 else {}
    cases.append(("E1 materialized sidecar artifact retains selection beside jobs and roster",
                  isinstance(artifact, dict) and artifact.get("reviewerSelection") == selection
                  and artifact.get("roster") == [{"key": "plc-memory-alignment", "model": SERVING_PIN}]))

    records = os.path.join(tmp, "tasks")
    os.makedirs(records)
    with open(os.path.join(records, "goaltask.json"), "w", encoding="utf-8") as fh:
        json.dump({"task_id": "goaltask", "requirements": ["Authoring stays one edit per common change"],
                   "exclusions": ["No new node types"]}, fh)
    tenv = dict(BASE_ENV, PYTHONUTF8="1", HARNESS_TASK_RECORD_DIR=records)
    with_task = {}
    for target in ("native", "sidecar"):
        prefix = "acc" + target
        r = run(["--plan", plan_path, "--target", target, "--out", tmp, "--prefix", prefix, "--pr-num", "1",
                 "--branch", "x", "--context-path", ctx, "--model", SERVING_PIN, "--effort", "medium",
                 "--task", "goaltask"], env=tenv)
        brief = os.path.join(tmp, f"{prefix}_plc-memory-alignment.md")
        with_task[target] = open(brief, encoding="utf-8").read() if r.returncode == 0 else r.stderr
    cases.append(("--task writes the record's requirements and exclusions into every seat as its acceptance test",
                  all("## Acceptance test" in body and "Authoring stays one edit per common change" in body
                      and "No new node types" in body for body in with_task.values())))
    r = run(["--plan", plan_path, "--target", "sidecar", "--out", tmp, "--prefix", "accmiss", "--pr-num", "1",
             "--branch", "x", "--context-path", ctx, "--model", SERVING_PIN, "--effort", "medium",
             "--task", "no-such-task"], env=tenv)
    cases.append(("--task naming no record is refused before any brief is written",
                  r.returncode != 0 and "no task record no-such-task" in (r.stderr or "")
                  and not os.path.exists(os.path.join(tmp, "jobs_accmiss.json"))))

    r = run(base + ["--tier", "fanout"])
    cases.append(("omitting --model fails before any brief is written",
                  r.returncode != 0 and "--model" in (r.stderr or "")
                  and not os.path.exists(os.path.join(tmp, "jobs_t1.json"))))

    seat = CODE_REVIEWER + "+" + ERROR_HUNTER
    r = run(["--keys", seat, "exp-memory", "--out", tmp, "--prefix", "t0", "--pr-num", "1",
             "--branch", "x", "--context-path", ctx, "--model", SERVING_PIN, "--effort", "medium"])
    ok = r.returncode == 0
    jobs = json.load(open(os.path.join(tmp, "jobs_t0.json"), encoding="utf-8")) if ok else []
    by = {j["label"]: j.get("tier") for j in jobs}
    cases.append(("omitting --tier gives each seat its lens's tier (tools/lens.py seat_tier)",
                  ok and by == {"t0-" + seat: lens.seat_tier(seat), "t0-exp-memory": lens.seat_tier("exp-memory")}
                  and None not in by.values()))
    seated = {j["label"]: (j.get("seat"), j.get("mandates")) for j in jobs}
    cases.append(("each native job names its seat key and lens keys, the identity review_ledger's dispatch ingress joins on",
                  ok and seated == {"t0-" + seat: (seat, [CODE_REVIEWER, ERROR_HUNTER]),
                                    "t0-exp-memory": ("exp-memory", ["exp-memory"])}))

    two = ["--out", tmp, "--prefix", "tplan", "--pr-num", "1", "--branch", "x", "--context-path", ctx,
           "--model", SERVING_PIN, "--effort", "medium"]
    r = run(["--keys", CODE_REVIEWER, ERROR_HUNTER] + two, plan=False)
    cases.append(("two seat keys without --plan are refused, naming review_ledger.py plan and --plan",
                  r.returncode != 0 and "review_ledger.py plan" in r.stderr and "--plan" in r.stderr
                  and "Traceback" not in r.stderr and not os.path.exists(os.path.join(tmp, "jobs_tplan.json"))))
    plan_file = os.path.join(tmp, "plan.json")
    with open(plan_file, "w", encoding="utf-8") as fh:
        json.dump({"roster": [{"key": CODE_REVIEWER + "+" + ERROR_HUNTER, "mandates": ["x"]},
                              {"key": "exp-memory", "mandates": ["y"]}], "dropped": []}, fh)
    r = run(["--plan", plan_file] + two, plan=False)
    ok = r.returncode == 0
    jobs = json.load(open(os.path.join(tmp, "jobs_tplan.json"), encoding="utf-8")) if ok else []
    cases.append(("--plan seats the panel from the plan's roster keys, one job per seat",
                  ok and [j["seat"] for j in jobs] == [CODE_REVIEWER + "+" + ERROR_HUNTER, "exp-memory"]))
    r = run(["--plan", plan_file, "--keys", CODE_REVIEWER] + two, plan=False)
    cases.append(("--plan with --keys is refused",
                  r.returncode != 0 and "--keys" in r.stderr and "Traceback" not in r.stderr))
    with open(plan_file, "w", encoding="utf-8") as fh:
        json.dump({"roster": []}, fh)
    r = run(["--plan", plan_file] + two, plan=False)
    cases.append(("a plan with an empty roster is refused: nothing is owed",
                  r.returncode != 0 and "roster" in r.stderr and "Traceback" not in r.stderr))

    r = run(["--keys", CODE_REVIEWER, "--out", tmp, "--prefix", "teff", "--pr-num", "1",
             "--branch", "x", "--context-path", ctx, "--model", SERVING_PIN, "--tier", "executor"])
    cases.append(("a native seat with no --effort and no --effort-for is refused, naming the flag",
                  r.returncode == 2 and "--effort" in r.stderr
                  and "the lens tier does not select a reasoning rung" in r.stderr
                  and "Traceback" not in r.stderr
                  and not os.path.exists(os.path.join(tmp, "jobs_teff.json"))))

    r = run(base + ["--model", SERVING_PIN, "--tier", "bogus"])
    cases.append(("an unknown --tier is rejected by argument validation without traceback",
                  r.returncode == 2 and "invalid choice" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", SERVING_PIN, "--tier", "fanout", "--tier-for", ERROR_HUNTER + "=executor"])
    cases.append(("--tier-for rejects a valid but unselected lens",
                  r.returncode == 1 and "not selected" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", SERVING_PIN, "--tier", "fanout", "--model-for", CODE_REVIEWER + "=opus",
                    "--tier-for", CODE_REVIEWER + "=executor", "--effort", "medium",
                    "--effort-for", CODE_REVIEWER + "=high"])
    ok = r.returncode == 0
    jobs = json.load(open(os.path.join(tmp, "jobs_t1.json"), encoding="utf-8")) if ok else []
    by = {j["label"]: j for j in jobs}
    cases.append(("panel pin applies to every lens without an override",
                  ok and by.get("t1-" + PANEL_LENS, {}).get("model") == SERVING_PIN))
    cases.append(("--model-for overrides one lens",
                  ok and by.get("t1-" + CODE_REVIEWER, {}).get("model") == "opus"))
    cases.append(("every job carries its lens tier, whatever the panel tier or --tier-for writes",
                  ok and by.get("t1-" + PANEL_LENS, {}).get("tier") == "executor"
                  and by.get("t1-" + CODE_REVIEWER, {}).get("tier") == "executor"))
    cases.append(("--effort sets the panel rung and --effort-for raises one seat above it",
                  ok and by.get("t1-" + PANEL_LENS, {}).get("effort") == "medium"
                  and by.get("t1-" + CODE_REVIEWER, {}).get("effort") == "high"))
    cases.append(("every native job carries kind reviewer and neither agentType nor readOnly",
                  ok and bool(jobs) and all(j.get("kind") == "reviewer" and "agentType" not in j
                                            and "readOnly" not in j for j in jobs)))
    cases.append(("brief files exist at the recorded promptPath",
                  ok and all(os.path.isfile(j["promptPath"]) for j in jobs)))

    r = run(base + ["--model", SERVING_PIN, "--model-for", NOT_A_LENS + "=opus", "--tier", "fanout"])
    cases.append(("--model-for an unknown lens returns a semantic error without traceback",
                  r.returncode == 1 and "no such lens" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", SERVING_PIN, "--effort-for", NOT_A_LENS + "=high", "--tier", "fanout"])
    cases.append(("--effort-for an unknown lens returns a semantic error without traceback",
                  r.returncode == 1 and "no such lens" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", SERVING_PIN, "--effort-for", ERROR_HUNTER + "=high", "--tier", "fanout"])
    cases.append(("--effort-for rejects a valid but unselected lens",
                  r.returncode == 1 and "not selected" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", SERVING_PIN, "--model-for", ERROR_HUNTER + "=opus", "--tier", "fanout"])
    cases.append(("--model-for rejects a valid but unselected lens",
                  r.returncode == 1 and "not selected" in r.stderr and "Traceback" not in r.stderr))

    r = run(base + ["--model", "gpt-5.6-luna", "--tier", "fanout"])
    cases.append(("a vendor id is rejected by argument validation without traceback",
                  r.returncode == 2 and "invalid choice" in r.stderr and "Traceback" not in r.stderr))

    # A seat map puts mandates together in one seat (orchestration §2 *Sizing the width*): `a+b`
    # writes ONE brief carrying both mandates and one job, and the brief asks for per-mandate coverage.
    seat = CODE_REVIEWER + "+" + ERROR_HUNTER
    r = run(["--keys", seat, TEST_ANALYZER, "--out", tmp, "--prefix", "t2", "--pr-num", "1",
             "--branch", "x", "--context-path", ctx, "--model", "opus", "--tier", "executor",
             "--effort", "low", "--effort-for", seat + "=high"])
    ok = r.returncode == 0
    jobs = json.load(open(os.path.join(tmp, "jobs_t2.json"), encoding="utf-8")) if ok else []
    by = {j["label"]: j for j in jobs}
    merged = by.get("t2-" + seat, {})
    body = open(merged["promptPath"], encoding="utf-8").read() if merged.get("promptPath") else ""
    cases.append(("a+b writes one job per seat, not per mandate",
                  ok and sorted(by) == ["t2-" + seat, "t2-" + TEST_ANALYZER]))
    cases.append(("the merged brief carries both mandates and names each key",
                  "Mandate `" + CODE_REVIEWER + "`" in body and "Mandate `" + ERROR_HUNTER + "`" in body
                  and body.count("# Shared rules for every lens") == 1))
    cases.append(("the merged brief asks for per-mandate outcomes and finding keys",
                  "not-reached" in body and "mandate key" in body))
    cases.append(("--effort-for addresses the seat key", merged.get("effort") == "high"))

    r = run(["--keys", CODE_REVIEWER + "+plc-evidence-grounding", "--out", tmp, "--prefix", "t3",
             "--pr-num", "1", "--branch", "x", "--context-path", ctx, "--model", "opus", "--tier", "executor"])
    cases.append(("a seat mixing registries is refused without traceback",
                  r.returncode == 1 and "one registry" in r.stderr and "Traceback" not in r.stderr))

    r = run(["--keys", CODE_REVIEWER + "+" + NOT_A_LENS, "--out", tmp, "--prefix", "t4",
             "--pr-num", "1", "--branch", "x", "--context-path", ctx, "--model", "opus", "--tier", "executor"])
    cases.append(("an unknown mandate inside a seat is named",
                  r.returncode == 1 and NOT_A_LENS in r.stderr and "Traceback" not in r.stderr))

    # --- S4: the native route's bytes are a fixture, and a flag-shaped placeholder names its flag ---
    fxctx = os.path.join(tmp, "fxctx.md")
    with open(fxctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nfixture body\n")
    fxmust = os.path.join(tmp, "fxmust.txt")
    with open(fxmust, "w", encoding="utf-8") as fh:
        fh.write(fxctx + "\n")
    fx = ["--keys", "plc-memory-alignment", "plc-instruction-quality+plc-doctrine-consistency",
          "--out", tmp, "--prefix", "fx", "--pr-num", "7", "--branch", "fix/branch",
          "--context-path", fxctx, "--model", SERVING_PIN, "--tier", "architect", "--effort", "medium",
          "--must-read", fxmust]
    r = run(fx)
    ok = r.returncode == 0
    digests = {}
    if ok:
        with open(os.path.join(tmp, "jobs_fx.json"), encoding="utf-8") as fh:
            for j in json.load(fh):
                body = open(j["promptPath"], encoding="utf-8", newline="").read()
                digests[j["label"]] = hashlib.sha256(
                    doctrine_normalized(kfm_normalized(
                        body.replace(fxctx, "<CTX>").replace(fxctx.replace(os.sep, "/"), "<CTX>")))
                    .encode("utf-8")).hexdigest()[:16]
    cases.append(("materialized instruction-quality scope asks seven questions and defers literal wording",
                  ok and 'seven mandated questions' in body
                  and '2. **Is the instruction followable at speed?' not in body
                  and 'THAT WORDING' not in body and '**FIX** for wording' not in body
                  and 'Answer the seven questions per artifact' in body))
    cases.append(("native output is byte-for-byte unchanged (fixture digests)",
                  ok and digests == NATIVE_FIXTURE))
    cases.append(("briefs and the jobs file are written with LF line ends on every platform",
                  ok and "\r" not in body
                  and "\r" not in open(os.path.join(tmp, "jobs_fx.json"), encoding="utf-8", newline="").read()))
    raw = body.splitlines(keepends=True)
    doctrine_lines = [i + 4 for i, line in enumerate(raw)
                      if line.startswith("> Preloaded doctrine: ")] if ok else []
    edited = list(raw)
    for i in doctrine_lines[:1] + [len(raw) - 1] if doctrine_lines else []:
        edited[i] = "> an edited doctrine line" + raw[i][len(raw[i].rstrip("\r\n")):]
    cases.append(("a doctrine edit leaves the fixture digest unchanged; a template edit does not",
                  bool(doctrine_lines)
                  and doctrine_normalized("".join(edited)) == doctrine_normalized(body)
                  and doctrine_normalized(body.replace("Mandate `", "Mandate: `", 1)) != doctrine_normalized(body)))

    def brief_text(prefix, key):
        with open(os.path.join(tmp, f"{prefix}_{key}.md"), encoding="utf-8") as fh:
            return fh.read()
    r = run(["--keys", TEST_ANALYZER, "--out", tmp, "--prefix", "tqfm", "--pr-num", "7", "--branch", "x",
             "--context-path", fxctx, "--model", SERVING_PIN, "--tier", "executor", "--effort", "medium"])
    tq_brief = brief_text("tqfm", TEST_ANALYZER) if r.returncode == 0 else ""
    cases.append(("{{TEST_QUALITY_CHECKLIST}} injects test_quality.md below its frontmatter",
                  "# Shared Test Quality Checklist" in tq_brief and "description: Review tests" not in tq_brief))
    r = run(["--keys", "plc-memory-alignment", "plc-evidence-grounding", "--out", tmp, "--prefix", "kfmd",
             "--pr-num", "7", "--branch", "x", "--context-path", fxctx, "--model", SERVING_PIN,
             "--tier", "architect", "--effort", "medium"])
    ok = r.returncode == 0
    declaring = brief_text("kfmd", "plc-memory-alignment") if ok else ""
    plain = brief_text("kfmd", "plc-evidence-grounding") if ok else ""
    index_line = next((l for l in declaring.splitlines() if l.startswith("Read the index: ")), "")
    index_rel = index_line[len("Read the index: "):].strip()
    cases.append(("a KFM-declaring lens's brief carries the materialized index path, which exists",
                  ok and index_rel.endswith("/index.md") and os.path.isfile(index_rel)
                  and os.path.isfile(os.path.join(os.path.dirname(index_rel), "manifest.json"))))
    cases.append(("the proof's KFM snapshot lives under its temp root, never the live checkout's scratch",
                  ok and os.path.normcase(os.path.abspath(index_rel)).startswith(
                      os.path.normcase(os.path.abspath(KFM_ROOT)) + os.sep)))
    cases.append(("a lens that does not declare it gets no KFM section or path",
                  ok and "Known failure modes" not in plain and "kfm" not in plain))

    import lens_briefs
    import _session_transport
    import model_registry
    import kfm
    materialized = []
    real_materialize = kfm.materialize

    def counting_materialize(*a, **k):
        materialized.append(a[1] if len(a) > 1 else k.get("directory"))
        return real_materialize(*a, **k)
    kfm.materialize = counting_materialize
    try:
        first, second = lens_briefs.kfm_section(KFM_ROOT), lens_briefs.kfm_section(KFM_ROOT)
    finally:
        kfm.materialize = real_materialize
    cases.append(("kfm_section materializes once for every seat that declares kfm, into the root it is given",
                  first == second and len(materialized) == 1
                  and all(str(d).replace(os.sep, "/").startswith(KFM_ROOT.replace(os.sep, "/"))
                          for d in materialized)))
    cases.append(("the kfm section points at the index and leaves the scan rule to the index it carries",
                  "Read only the body files" not in first and "Scan every trigger" not in first))
    # Both fixture pins are read from the registry, never hand-picked: the vocabulary this tool
    # refuses on must be the one the ENDPOINT serves, and both halves of that drift with the roster.
    served = _session_transport.legal_model_ids("anthropic", seat="anthropic")
    cases.append(("the native pin vocabulary is the registry's dispatchable set, not a literal list",
                  set(lens_briefs.NATIVE_PINS) == set(served)))
    cases.append(("the native effort vocabulary is the transport's own, not a literal list",
                  list(lens_briefs.NATIVE_EFFORTS)
                  == _session_transport.legal_effort_values("anthropic")))
    # A full id (not a role alias) of a row serving the lens's `architect` tier, so the tier refusal stays out of it.
    architect_ids = [m["id"] for m in model_registry.load().get("models") or []
                     if m.get("transport") == "anthropic" and "architect" in (m.get("roles") or [])]
    full_id = next((i for i in reversed(served) if i in architect_ids), None)
    if full_id:
        overridden = run(["--keys", "plc-memory-alignment", "--out", tmp, "--prefix", "fxd",
                          "--context-path", fxctx, "--model", full_id, "--effort", "medium"])
        jobs = (json.load(open(os.path.join(tmp, "jobs_fxd.json"), encoding="utf-8"))
                if os.path.exists(os.path.join(tmp, "jobs_fxd.json")) else [])
        cases.append(("--model takes an id the registry serves beyond the role aliases",
                      overridden.returncode == 0 and jobs and jobs[0]["model"] == full_id))
    retired_rows = [m.get("alias") for m in model_registry.load().get("models") or []
                    if m.get("transport") == "anthropic" and m.get("alias") not in served]
    if retired_rows:
        retired = run(["--keys", "plc-memory-alignment", "--out", tmp, "--prefix", "fxe",
                       "--context-path", fxctx, "--model", retired_rows[0], "--effort", "medium"])
        cases.append(("--model still refuses an id the registry no longer serves",
                      retired.returncode != 0 and retired_rows[0] in retired.stderr
                      and "Traceback" not in retired.stderr))
    bad_effort = run(["--keys", "plc-memory-alignment", "--out", tmp, "--prefix", "fxb",
                      "--context-path", fxctx, "--model", SERVING_PIN, "--effort", "medium",
                      "--effort-for", "plc-memory-alignment=bogus"])
    cases.append(("--effort-for spells the native effort vocabulary from its one list",
                  bad_effort.returncode == 1
                  and "|".join(lens_briefs.NATIVE_EFFORTS) in bad_effort.stderr
                  and "Traceback" not in bad_effort.stderr))
    bad_model = run(["--keys", "plc-memory-alignment", "--out", tmp, "--prefix", "fxc",
                     "--context-path", fxctx, "--model", SERVING_PIN, "--effort", "medium",
                     "--model-for", "plc-memory-alignment=bogus"])
    cases.append(("--model-for spells the native pin vocabulary from its one list",
                  bad_model.returncode == 1
                  and "|".join(lens_briefs.NATIVE_PINS) in bad_model.stderr
                  and "Traceback" not in bad_model.stderr))

    r = run(["--keys", CODE_REVIEWER, "--out", tmp, "--prefix", "t5", "--context-path", ctx,
             "--model", SERVING_PIN, "--tier", "executor"])
    cases.append(("a template placeholder with no flag refuses, naming --pr-num and writing no jobs",
                  r.returncode == 1 and "--pr-num" in r.stderr and "Traceback" not in r.stderr
                  and not os.path.exists(os.path.join(tmp, "jobs_t5.json"))))

    r = run(["--keys", "plc-memory-alignment", "--out", tmp, "--prefix", "t6", "--pr-num", "1",
             "--branch", "x", "--context-path", os.path.join(tmp, "nope.md"), "--model", SERVING_PIN,
             "--tier", "architect"])
    cases.append(("a --context-path that does not resolve is refused before any brief is written",
                  r.returncode == 1 and "nope.md" in r.stderr and "Traceback" not in r.stderr
                  and not os.path.exists(os.path.join(tmp, "jobs_t6.json"))))

    # --- S4: --target sidecar ---
    reg = sidecar_registry(os.path.join(tmp, "registry.json"))
    senv = dict(BASE_ENV, PYTHONUTF8="1", HARNESS_MODEL_REGISTRY=reg)
    scctx = os.path.join(tmp, "scctx.md")
    with open(scctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nplan text and its source paths\n")
    sc = os.path.join(tmp, "sc")
    seat_meta = "plc-instruction-quality+plc-doctrine-consistency"
    scargs = ["--target", "sidecar", "--keys", seat_meta, "plc-memory-alignment", "--out", sc,
              "--prefix", "sc", "--context-path", scctx, "--model", "sideseat", "--tier", "architect",
              "--effort", "max"]
    r = run(scargs, env=senv)
    ok = r.returncode == 0
    jobs, by = [], {}
    if ok:
        with open(os.path.join(sc, "jobs_sc.json"), encoding="utf-8") as fh:
            jobs = json.load(fh)
        by = {j["label"]: j for j in jobs}
    cases.append(("sidecar materializes one job per seat, named <prefix>_<seat>",
                  ok and sorted(by) == ["sc-" + seat_meta, "sc-plc-memory-alignment"]
                  and all(os.path.isfile(j["promptFile"]) for j in jobs)))
    cases.append(("sidecar uses sidecar_fanout's job schema, including explicit mandate keys",
                  ok and all(sorted(j) == sorted(SIDECAR_KEYS) for j in jobs)
                  and by.get("sc-" + seat_meta, {}).get("mandates") == seat_meta.split("+")
                  and by.get("sc-plc-memory-alignment", {}).get("mandates") == ["plc-memory-alignment"]))
    cases.append(("sidecar resolves the alias through model_registry and sets kind reviewer",
                  ok and all(j["alias"] == "sideseat" and j["kind"] == "reviewer" for j in jobs)))
    cases.append(("sidecar names the canonical review schema and the repo root",
                  ok and all(os.path.isfile(j["schemaFile"]) and j["schemaFile"].endswith(
                      "/.claude/schemas/review_findings.json") for j in jobs)
                  and all(j["workdir"] == ROOT.replace("\\", "/") for j in jobs)))
    cases.append(("sidecar carries the context file as the seat's contextFiles entry",
                  ok and all(j["contextFiles"] == [scctx] for j in jobs)))
    cases.append(("sidecar uses the caller's explicit ladder effort pin",
                  ok and all(j["effort"] == "max" for j in jobs)))
    # review_result.prepare_review reads job["tier"]; a row with no tier fails review_ledger's depth clause and covers
    # nothing, so every sidecar review would record coverage that never counts.
    cases.append(("sidecar carries the caller's tier pin for the ledger's depth clause",
                  ok and all(j.get("tier") == "architect" for j in jobs)))

    merged = open(by.get("sc-" + seat_meta, {}).get("promptFile", os.devnull),
                  encoding="utf-8").read() if ok else ""
    rubric = open(os.path.join(ROOT, ".claude", "skills", "instruction_quality", "reference",
                               "skills_and_commands.md"), encoding="utf-8").read()
    cases.append(("the merged Meta seat carries both mandates' full text",
                  "Mandate `plc-instruction-quality`" in merged
                  and "Mandate `plc-doctrine-consistency`" in merged
                  and "the plan's edits per artifact" in merged and "inbound-reference rot" in merged))
    cases.append(("the merged Meta seat carries the per-mandate coverage contract",
                  "not-reached" in merged and "mandate key" in merged))
    cases.append(("the instruction_quality mandate carries its whole rubric, not its name alone",
                  "### 10. Procedure verifiability" in merged
                  and "Procedure invocation is not completion." in merged
                  and rubric.count("### 10. Procedure verifiability") == 1
                  and rubric.count("Procedure invocation is not completion.") == 1
                  and merged.count("Procedure invocation is not completion.") == 1))
    cases.append(("the sidecar brief names a parseable read-first context target",
                  "Read first:\n- " + scctx.replace("\\", "/") in merged))
    cases.append(("the seat states a read-only profile without denying loaded hooks",
                  "no shell" in merged and "loads none of the repository's skills, hooks or rail text" not in merged))
    # Sidecar seats carry no engine contract, so the brief must require a quoted evidence item per finding.
    cases.append(("the sidecar brief asks every finding for a quote of the text that shows the defect",
                  "Every finding carries `evidence`" in merged and "{file, quote}" in merged))

    sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
    import tool_profiles
    review_row = tool_profiles.resolve("reviewer", walk_mcp=False)[0]
    cases.append(("the brief's tool profile line names the resolved review row's built-ins, not a literal",
                  ok and f"You are a review seat with {', '.join(review_row['builtins'])}." in merged))
    r = run_fanout([os.path.join(sc, "jobs_sc.json"), "--dry-run", "--review"], senv)
    cases.append(("sidecar_fanout --review --dry-run accepts the generated jobs",
                  r.returncode == 0 and "Nothing dispatched" in r.stdout
                  and "-G review" in r.stdout and "-S " in r.stdout and "-C " in r.stdout))

    missing_effort = scargs[:-2].copy()
    missing_effort[missing_effort.index("sc")] = "scmissing"
    r = run(missing_effort, env=senv)
    cases.append(("sidecar refuses a missing explicit ladder effort pin",
                  r.returncode == 1 and "--effort" in r.stderr
                  and not os.path.exists(os.path.join(sc, "jobs_scmissing.json"))))

    partial = scargs[:-2].copy()
    partial[partial.index("sc")] = "scpartial"
    partial.extend(["--effort-for", seat_meta + "=max"])
    r = run(partial, env=senv)
    cases.append(("a panel whose later seat refuses writes no brief for any seat",
                  r.returncode == 1 and "plc-memory-alignment" in r.stderr
                  and not [name for name in os.listdir(sc) if name.startswith("scpartial")]))

    override = scargs.copy()
    override[override.index("sc")] = "scoverride"
    override.extend(["--model-for", "plc-memory-alignment=sideseat2"])
    r = run(override, env=senv)
    got = json.load(open(os.path.join(sc, "jobs_scoverride.json"), encoding="utf-8")) if r.returncode == 0 else []
    cases.append(("sidecar per-seat model override resolves a different registered alias",
                  r.returncode == 0 and {j["label"]: j["alias"] for j in got} == {
                      "scoverride-" + seat_meta: "sideseat", "scoverride-plc-memory-alignment": "sideseat2"}))

    weaker = scargs.copy()
    weaker[weaker.index("sc")] = "scweak"
    weaker[weaker.index("sideseat")] = "sideseat-scout"
    r = run(weaker, env=senv)
    cases.append(("sidecar refuses a row that does not claim the review tier",
                  r.returncode == 1 and "architect" in r.stderr and "sideseat-scout" in r.stderr
                  and not os.path.exists(os.path.join(sc, "jobs_scweak.json"))))

    # The same row, named in the session's own owner turn, briefs: the panel check and the brief builder agree.
    named = weaker.copy()
    named[named.index("scweak")] = "scnamed"
    config = tempfile.mkdtemp(dir=tmp)
    sid = "0a1b2c3d-0000-4000-8000-0000000000bb"
    store = os.path.join(config, "projects", "".join(c if c.isalnum() else "-" for c in ROOT))
    os.makedirs(store)
    with open(os.path.join(HERE, "fixtures", "owner_text", "live_rows.jsonl"), encoding="utf-8") as fh:
        row = next(item["row"] for item in map(json.loads, fh) if item["label"] == "owner_prompt")
    row["message"]["content"] = "seat it on sideseat-scout, max effort"
    with open(os.path.join(store, sid + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row) + "\n")
    r = run(named, env=dict(senv, CLAUDE_CODE_SESSION_ID=sid, CLAUDE_CONFIG_DIR=config, CLAUDE_PROJECT_DIR=ROOT))
    cases.append(("sidecar briefs an owner-named row that does not claim the review tier",
                  r.returncode == 0 and os.path.exists(os.path.join(sc, "jobs_scnamed.json"))))

    r = run(["--target", "sidecar", "--keys", "plc-memory-alignment", "--out", sc, "--prefix", "sclow",
             "--context-path", scctx, "--model", "sideseat", "--tier", "architect",
             "--effort-for", "plc-memory-alignment=xhigh"], env=senv)
    cases.append(("sidecar refuses an effort rung the alias's transport does not serve, naming the rungs",
                  r.returncode == 1 and "xhigh" in r.stderr and "low, high, max" in r.stderr
                  and not os.path.exists(os.path.join(sc, "jobs_sclow.json"))))

    r = run(["--target", "sidecar", "--keys", "plc-memory-alignment", "--out", sc, "--prefix", "scbad",
             "--context-path", scctx, "--model", "not-a-model", "--tier", "architect", "--effort", "max"], env=senv)
    cases.append(("sidecar refuses an alias its registry does not hold",
                  r.returncode == 1 and "not-a-model" in r.stderr and "Traceback" not in r.stderr))

    badctx = os.path.join(tmp, "sc_bad_ctx.md")
    with open(badctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\nRun `git log` to enumerate every change.\n")
    r = run(["--target", "sidecar", "--keys", "plc-memory-alignment", "--out", sc,
             "--prefix", "scbash", "--context-path", badctx, "--model", "sideseat",
             "--tier", "architect", "--effort", "max"], env=senv)
    cases.append(("a source line that names a command no longer refuses generation: wording never sets tools",
                  r.returncode == 0 and "Bash" not in r.stderr
                  and [name for name in os.listdir(sc) if name.startswith("scbash")]))

    # --- preload: a template's declared doctrine rides in the brief, once, whole and blockquoted ---
    def quoted(rel):
        text = open(os.path.join(ROOT, ".claude", *rel.split("/")), encoding="utf-8").read()
        return "\n".join(f"> {line}".rstrip() if line.strip() else ">" for line in text.splitlines())

    def carries_once(body, rel):
        return (body.count(f"> Preloaded doctrine: {rel}") == 1 and body.count(quoted(rel)) == 1)

    iq_rel = "skills/instruction_quality/SKILL.md"
    ap_rel = "skills/architecture_philosophy/SKILL.md"
    code_seat = "plc-pattern-fit+plc-architecture-quality"
    code_rels = [ap_rel, "skills/architecture_philosophy/structure_rules.md",
                 "rules/scene_authoring.md", "reference/rules/design_litmus.md"]
    for target, extra, env, prefix in (
            ("native", ["--model", SERVING_PIN, "--effort", "medium"], None, "pn"),
            ("sidecar", ["--target", "sidecar", "--model", "sideseat", "--effort", "max"], senv, "ps")):
        pout = os.path.join(tmp, "pre_" + prefix)
        r = run(["--keys", "plc-instruction-quality", code_seat, "plc-memory-alignment", "--out", pout,
                 "--prefix", prefix, "--context-path", scctx, "--tier", "architect"] + extra, env=env)
        ok = r.returncode == 0

        def brief(k):
            path = os.path.join(pout, f"{prefix}_{k}.md")
            return open(path, encoding="utf-8").read() if ok and os.path.isfile(path) else ""
        cases.append((f"{target}: a plc-instruction-quality brief carries its declared rubric once, whole and quoted",
                      ok and carries_once(brief("plc-instruction-quality"), iq_rel)))
        cases.append((f"{target}: a merged seat inlines each declared file once, shared ones included",
                      ok and all(carries_once(brief(code_seat), rel) for rel in code_rels)))
        cases.append((f"{target}: a lens declaring nothing carries no doctrine",
                      ok and "Preloaded doctrine" not in brief("plc-memory-alignment")))
    sidecar_iq = open(os.path.join(tmp, "pre_ps", "ps_plc-instruction-quality.md"), encoding="utf-8").read() \
        if os.path.isfile(os.path.join(tmp, "pre_ps", "ps_plc-instruction-quality.md")) else ""
    cases.append(("sidecar: the retired rubric_block header is gone (one inlining mechanism)",
                  sidecar_iq and "# Rubric for `plc-instruction-quality`" not in sidecar_iq))

    # --- must-read: every seat carries its list, a seat without one is refused, and an over-window seat
    # is refused before launch with its overage ---
    def must_list(name, *entries):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(entry + "\n" for entry in entries))
        return path

    def sized(name, size):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * size)
        return path.replace("\\", "/")

    seat_list = must_list("seat_list.txt", ".claude/tools/lens_briefs.py", ".claude/tools/lens.py")
    for target, extra, env, prefix in (
            ("native", ["--model", SERVING_PIN, "--effort", "medium"], None, "mrn"),
            ("sidecar", ["--target", "sidecar", "--model", "sideseat", "--effort", "max"], senv, "mrs")):
        mout = os.path.join(tmp, "must_" + prefix)
        r = run(["--keys", "plc-memory-alignment", "plc-evidence-grounding", "--out", mout, "--prefix", prefix,
                 "--context-path", scctx, "--tier", "architect", "--must-read", MUST_LIST,
                 "--must-read-for", "plc-evidence-grounding=" + seat_list] + extra, env=env)
        ok = r.returncode == 0
        jobs = json.load(open(os.path.join(mout, f"jobs_{prefix}.json"), encoding="utf-8")) if ok else []
        lists = {j["label"]: j.get("mustRead") for j in jobs}
        lens_entry = {"path": ".claude/tools/lens.py", "lines": lines_of(".claude/tools/lens.py")}
        briefs_entry = {"path": ".claude/tools/lens_briefs.py", "lines": lines_of(".claude/tools/lens_briefs.py")}
        cases.append((f"{target}: each job carries its seat's mustRead entries with each file's line count, "
                      "--must-read-for replacing the panel list",
                      ok and lists == {f"{prefix}-plc-memory-alignment": [lens_entry],
                                       f"{prefix}-plc-evidence-grounding": [briefs_entry, lens_entry]}))
        body = open(os.path.join(mout, f"{prefix}_plc-evidence-grounding.md"), encoding="utf-8").read() if ok else ""
        cases.append((f"{target}: the brief lists every must-read entry with its line count, the Read range form, "
                      "the done condition and the closed gap reasons",
                      "# Must read" in body
                      and f"- .claude/tools/lens_briefs.py ({briefs_entry['lines']} lines)" in body
                      and "Read:<path>:<first>-<last>" in body
                      and all(reason in body for reason in ("outside-lens", "capability", "missing"))
                      and "review_agents.md" in body))
        r = run(["--keys", "plc-memory-alignment", "--out", mout, "--prefix", prefix + "none",
                 "--context-path", scctx, "--tier", "architect"] + extra, env=env, must_read=False)
        cases.append((f"{target}: a seat with no must-read list is refused, naming both flags, before any job is written",
                      r.returncode == 1 and "--must-read" in r.stderr and "--must-read-for" in r.stderr
                      and "Traceback" not in r.stderr
                      and not os.path.exists(os.path.join(mout, f"jobs_{prefix}none.json"))))

    # --- must-read entries for a hand-built agents array: the same entries the jobs carry ---
    def entries(*args):
        r = subprocess.run([sys.executable, BRIEFS, "must-read", *args], capture_output=True, text=True,
                           encoding="utf-8", cwd=ROOT, timeout=60, env=dict(BASE_ENV, PYTHONUTF8="1"))
        try:
            return r, json.loads(r.stdout)
        except ValueError:
            return r, None
    unterminated = sized("unterminated.txt", 0)
    with open(unterminated, "w", encoding="utf-8", newline="") as fh:
        fh.write("one\ntwo\nthree")
    r, got = entries(must_list("entries.txt", ".claude/tools/lens.py", "src/not/there.cs", unterminated))
    cases.append(("must-read prints one {path, lines} entry per listed file: its Read line count, an unterminated "
                  "last line counted, and 0 for a path that does not exist",
                  r.returncode == 0 and got == [{"path": ".claude/tools/lens.py", "lines": lines_of(".claude/tools/lens.py")},
                                               {"path": "src/not/there.cs", "lines": 0},
                                               {"path": unterminated, "lines": 3}]))
    r, got = entries(must_list("folder.txt", ".claude/tools"))
    cases.append(("must-read refuses a directory entry and an empty list without traceback",
                  r.returncode == 1 and "directory" in r.stderr and "Traceback" not in r.stderr
                  and entries(must_list("empty.txt"))[0].returncode == 1))

    with open(REGISTRY, encoding="utf-8") as fh:
        native_small = json.load(fh)
    for row in native_small["models"]:
        if row.get("id") == SERVING_PIN:
            row["limits"] = dict(SMALL_LIMITS)
    small_reg = os.path.join(tmp, "registry_small_native.json")
    with open(small_reg, "w", encoding="utf-8") as fh:
        json.dump(native_small, fh)
    big = must_list("big.txt", sized("big.bin", 300000))
    halves = [must_list(f"half{n}.txt", sized(f"half{n}.bin", 150000)) for n in (1, 2)]
    for target, extra, env, prefix in (
            ("native", ["--model", SERVING_PIN, "--effort", "medium"],
             dict(BASE_ENV, PYTHONUTF8="1", HARNESS_MODEL_REGISTRY=small_reg), "szn"),
            ("sidecar", ["--target", "sidecar", "--model", "sideseat-small", "--effort", "max"], senv, "szs")):
        sout = os.path.join(tmp, "size_" + prefix)
        r = run(["--keys", "plc-memory-alignment", "plc-evidence-grounding", "--out", sout, "--prefix", prefix,
                 "--context-path", scctx, "--tier", "architect", "--must-read", MUST_LIST,
                 "--must-read-for", "plc-memory-alignment=" + big] + extra, env=env)
        over = re.search(r"plc-memory-alignment: (\d+) tokens .* over by (\d+)", r.stderr or "")
        usable = NATIVE_USABLE_TOKENS if target == "native" else SMALL_USABLE_TOKENS
        cases.append((f"{target}: a seat whose brief, context and must-read files pass its pin's usable window is refused "
                      "with the overage, before any brief is written",
                      r.returncode == 1 and over is not None and int(over.group(2)) >= 300000 // 2.5 - usable
                      and int(over.group(1)) - int(over.group(2)) == usable
                      and "plc-evidence-grounding" not in (r.stderr or "")
                      and (not os.path.exists(sout) or not os.listdir(sout))))
        r = run(["--keys", "plc-memory-alignment", "plc-evidence-grounding", "--out", sout, "--prefix", prefix + "split",
                 "--context-path", scctx, "--tier", "architect",
                 "--must-read-for", "plc-memory-alignment=" + halves[0],
                 "--must-read-for", "plc-evidence-grounding=" + halves[1]] + extra, env=env)
        cases.append((f"{target}: the same files split across two seats fit, and the list is never truncated",
                      r.returncode == 0 and sorted(len(j["mustRead"]) for j in json.load(open(
                          os.path.join(sout, f"jobs_{prefix}split.json"), encoding="utf-8"))) == [1, 1]))

    cases += seat_overage_cases()

    # A seat's pin must be a row serving its lens tier (`workflow_provider_guard` RULE 3), refused at brief
    # build. The tier comes from the registry the tree ships, and the row is one that does not claim it, so a
    # lane whose shared registry tier differs still reaches the refusal.
    lens_tier = lens.seat_tier("sa-harness-quality")
    registry_rows = json.load(open(REGISTRY, encoding="utf-8"))["models"]
    not_serving = next(m["id"] for m in registry_rows
                       if m.get("transport") == "anthropic" and m.get("lifecycle") != "retired"
                       and lens_tier not in (m.get("roles") or []))
    # Any Anthropic row claiming neither fanout nor architect, so a roster change never empties the lookup.
    scout_only = next(m["id"] for m in json.load(open(REGISTRY, encoding="utf-8"))["models"]
                      if m.get("transport") == "anthropic" and m.get("lifecycle") != "retired"
                      and not {"fanout", "architect"} & set(m.get("roles") or []))
    tier_out = os.path.join(tmp, "tier_fit")

    placeholder_file = os.path.join(tmp, "tier_fit_sub.md")
    with open(placeholder_file, "w", encoding="utf-8") as fh:
        fh.write("planted\n")

    def tier_run(keys, *flags):
        return run(["--keys", *keys, "--out", tier_out, "--prefix", "tf", "--context-path", fxctx,
                    "--effort", "medium", "--sub", "HARNESS_HUNKS=" + placeholder_file,
                    "--sub", "HOT_SET=" + placeholder_file,
                    "--sub", "DOCTRINE_COMMITS=" + placeholder_file, *flags])

    refused = tier_run(["sa-harness-quality"], "--model", not_serving)
    cases.append(("a lens pinned to a model that claims not its tier is refused at build, naming the seat, "
                  "its lens tier and the rows that serve it",
                  refused.returncode == 1 and "sa-harness-quality" in refused.stderr
                  and "`" + lens_tier + "`" in refused.stderr
                  and re.search(r"\b" + re.escape(SERVING_ALIAS) + r"\b", refused.stderr)
                  and "Traceback" not in refused.stderr
                  and (not os.path.exists(tier_out) or not os.listdir(tier_out))))
    cases.append(("a written --tier or --tier-for never replaces the lens tier, as in the guard",
                  all(tier_run(["sa-harness-quality"], "--model", not_serving, *flags).returncode == 1
                      for flags in (["--tier", "architect"], ["--tier-for", "sa-harness-quality=architect"]))))
    cases.append(("a pin among the lens tier's rows passes",
                  tier_run(["sa-harness-quality"], "--model", SERVING_PIN).returncode == 0))
    cases.append(("a --model-for override is checked against its own seat",
                  tier_run(["sa-harness-quality"], "--model", SERVING_PIN,
                           "--model-for", "sa-harness-quality=" + not_serving).returncode == 1))
    merged = tier_run(["sa-design-semantics+sa-harness-quality"], "--model", SERVING_PIN)
    cases.append(("a merged seat takes its strongest lens tier and passes a row that serves it",
                  merged.returncode == 0))
    cases.append(("...and refuses a model that serves neither lens",
                  tier_run(["sa-design-semantics+sa-harness-quality"], "--model", scout_only).returncode == 1))
    cases.extend(citer_cases(tmp, senv))
    cases.extend(bounded_citer_cases(tmp))
    cases.extend(supplementary_cases(tmp, scctx))
    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


def supplementary_registry(path, native):
    """A registry whose rows hold `architect` only as `supplementary`: the live haiku row (native pins), or the
    sidecar probe rows plus a supplementary-only `supseat` and a fanout-only `fanseat`."""
    if native:
        with open(REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        for row in data["models"]:
            if row["alias"] == "haiku":
                row["supplementary"] = ["architect"]
    else:
        sidecar_registry(path)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        base = next(m for m in data["models"] if m["id"] == "sideseat-probe-1")
        supp = copy.deepcopy(base)
        supp.update({"id": "supseat-probe-1", "alias": "supseat", "family": "supseat-probe", "order": 1100,
                     "version": "SupSeat-Probe-1", "roles": ["executor", "fanout"], "supplementary": ["architect"]})
        fan = copy.deepcopy(base)
        fan.update({"id": "fanseat-probe-1", "alias": "fanseat", "family": "fanseat-probe", "order": 1101,
                    "version": "FanSeat-Probe-1", "roles": ["fanout"]})
        fan.pop("supplementary", None)
        fansup = copy.deepcopy(base)
        fansup.update({"id": "fansup-probe-1", "alias": "fansup", "family": "fansup-probe", "order": 1102,
                       "version": "FanSup-Probe-1", "roles": ["fanout"], "supplementary": ["architect"]})
        data["models"] += [supp, fan, fansup]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def supplementary_cases(tmp, scctx):
    """A pin holding the lens tier only as `supplementary` builds beside a qualified seat that carries its
    mandates and refuses alone, on both targets; a written --tier still refuses on the sidecar target only."""
    out_dir = os.path.join(tmp, "supp")
    sreg = supplementary_registry(os.path.join(tmp, "supp_sidecar_registry.json"), native=False)
    nreg = supplementary_registry(os.path.join(tmp, "supp_native_registry.json"), native=True)
    senv = dict(BASE_ENV, PYTHONUTF8="1", HARNESS_MODEL_REGISTRY=sreg)
    nenv = dict(BASE_ENV, PYTHONUTF8="1", HARNESS_MODEL_REGISTRY=nreg)
    merged, lone = "plc-pattern-fit+plc-architecture-quality", "plc-pattern-fit"
    ctx = scctx
    out = []

    def sidecar(prefix, keys, *flags):
        return run(["--target", "sidecar", "--keys", *keys, "--out", out_dir, "--prefix", prefix,
                    "--context-path", ctx, "--effort", "max", *flags], env=senv)

    def native(prefix, keys, *flags):
        return run(["--keys", *keys, "--out", out_dir, "--prefix", prefix, "--pr-num", "1", "--branch", "x",
                    "--context-path", ctx, "--effort", "medium", *flags], env=nenv)

    def built(prefix):
        return os.path.exists(os.path.join(out_dir, "jobs_%s.json" % prefix))

    r = sidecar("ssup", [merged, lone], "--model", "sideseat", "--model-for", lone + "=supseat")
    out.append(("sidecar: a supplementary pin beside a qualified merged seat builds, with no --tier written",
                r.returncode == 0 and built("ssup")))
    r = sidecar("ssupt", [merged, lone], "--model", "sideseat", "--model-for", lone + "=supseat",
                "--tier", "architect")
    out.append(("sidecar: a written --tier architect over a supplementary pin beside a qualified seat builds",
                r.returncode == 0 and built("ssupt")))
    r = sidecar("slone", [lone], "--model", "supseat")
    out.append(("sidecar: a lone supplementary seat is refused before any brief is written, naming the rule",
                r.returncode == 1 and "supplementary review seat" in r.stderr and "Traceback" not in r.stderr
                and not built("slone")))
    r = native("nsup", [merged, lone], "--model", SERVING_PIN, "--model-for", lone + "=haiku")
    out.append(("native: a supplementary pin through --model-for beside a qualified merged seat builds",
                r.returncode == 0 and built("nsup")))
    r = native("nlone", [lone], "--model", "haiku")
    out.append(("native: a lone supplementary seat is refused before any brief is written, naming the rule",
                r.returncode == 1 and "supplementary review seat" in r.stderr and not built("nlone")))
    r = sidecar("sweak", ["exp-memory"], "--model", "fansup", "--tier", "executor")
    out.append(("sidecar: a supplementary architect claim serves only architect, never a weaker tier the row lacks",
                r.returncode == 1 and "fansup" in r.stderr and not built("sweak")))
    r = sidecar("stier", ["exp-memory"], "--model", "fanseat", "--tier", "architect")
    out.append(("sidecar: --tier architect over a fanout lens pinned to a row claiming fanout only is refused",
                r.returncode == 1 and "fanseat" in r.stderr and not built("stier")))
    r = native("ntier", ["exp-memory"], "--model", "haiku", "--tier", "architect")
    out.append(("native: the same written --tier is the job's own claim and builds",
                r.returncode == 0 and built("ntier")))
    return out


def citer_cases(tmp, sidecar_env):
    """`--subject-plan`: a seat declaring `citers: plan` carries the sweep of each meta subject plan, and the
    brief is refused when a meta plan has no usable `surfaces` block."""
    cases = []
    ctx = os.path.join(tmp, "cictx.md")
    with open(ctx, "w", encoding="utf-8") as fh:
        fh.write("# CONTEXT\ncitier fixture\n")
    novault = os.path.join(tmp, "no_vault")

    def plan(name, body):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def block(*rows):
        return "# Plan\n\n```surfaces\n" + "\n".join(rows) + "\n```\n" + JOB_MAP

    lens_row = json.dumps({"kind": "path", "surface": ".claude/tools/lens.py", "change": "rewrite"})
    meta_ok = plan("ci_meta_ok.md", block(lens_row))
    meta_bare = plan("ci_meta_bare.md", "# Plan\n\nNo block here.\n" + JOB_MAP)
    meta_kind = plan("ci_meta_kind.md", block(json.dumps({"kind": "shrug", "surface": "x", "change": "rewrite"})))
    meta_anchor = plan("ci_meta_anchor.md", block(json.dumps(
        {"kind": "section", "surface": ".claude/commands/plan_check.md \u00a7No such anchor xyz", "change": "rewrite"})))
    code_plan = plan("ci_code_plan.md", "# Code plan\n\nNo block needed on a code plan.\n" + JOB_MAP)

    def go(prefix, keys, subjects):
        args = ["--keys", *keys, "--out", tmp, "--prefix", prefix, "--context-path", ctx, "--model", SERVING_PIN,
                "--tier", "architect", "--effort", "medium", "--vault", novault]
        for subject in subjects:
            args += ["--subject-plan", subject]
        return run(args, env=dict(BASE_ENV, PYTHONUTF8="1", **CITER_ENV), subject=False)

    def brief(prefix, key):
        with open(os.path.join(tmp, f"{prefix}_{key}.md"), encoding="utf-8") as fh:
            return fh.read()

    r = go("ci1", ["plc-doctrine-consistency"], ["meta:" + meta_ok])
    body = brief("ci1", "plc-doctrine-consistency") if r.returncode == 0 else ""
    cases.append(("a meta subject with a surfaces block carries a bounded INBOUND_REFS summary in a citers seat's brief",
                  r.returncode == 0 and "# INBOUND_REFS" in body and ".claude/tools/lens.py" in body
                  and '"vaultVerified"' in body and meta_ok.replace(os.sep, "/") in body.replace(os.sep, "/")))
    r = go("ci2", ["plc-doctrine-consistency"], ["meta:" + meta_bare])
    cases.append(("a meta subject with no surfaces block refuses the brief, printing the grammar and its home",
                  r.returncode != 0 and '"kind"' in r.stderr and "plan_file_format.md" in r.stderr
                  and "\u00a7Surfaces touched" in r.stderr and "ci_meta_bare.md" in r.stderr and "Traceback" not in r.stderr))
    r = go("ci3", ["plc-doctrine-consistency"], ["meta:" + meta_ok, "code:" + code_plan])
    body = brief("ci3", "plc-doctrine-consistency") if r.returncode == 0 else ""
    cases.append(("a code subject in a mixed panel is never swept and needs no block",
                  r.returncode == 0 and "ci_code_plan.md" not in body and ".claude/tools/lens.py" in body))
    r = go("ci4", ["plc-doctrine-consistency"], ["meta:" + meta_bare, "code:" + code_plan])
    cases.append(("a mixed panel fails exactly when its meta subject lacks the block", r.returncode != 0))
    r = go("ci5", ["plc-doctrine-consistency"], ["meta:" + meta_kind])
    cases.append(("a block line with an unknown kind exits 2", r.returncode == 2 and "Traceback" not in r.stderr))
    r = go("ci6", ["plc-doctrine-consistency"], ["meta:" + meta_anchor])
    cases.append(("a section whose anchor matches no heading or bold lead at HEAD exits 1 and names it",
                  r.returncode == 1 and "No such anchor xyz" in r.stderr))
    r = go("ci7", ["plc-doctrine-consistency"], [])
    cases.append(("a citers seat with no --subject-plan is refused naming the flag",
                  r.returncode != 0 and "--subject-plan" in r.stderr))
    r = go("ci8", ["plc-doctrine-consistency"], ["notashape"])
    cases.append(("a --subject-plan without a meta: or code: shape is refused", r.returncode != 0 and "meta:" in r.stderr))
    r = go("ci9", ["plc-doctrine-consistency"], ["meta:" + os.path.join(tmp, "absent_plan.md")])
    cases.append(("a --subject-plan that does not exist is refused", r.returncode != 0 and "absent_plan.md" in r.stderr))
    r = go("ci10", ["plc-memory-alignment"], ["meta:" + meta_bare])
    cases.append(("every selected seat refuses a block-less typed meta subject before spend",
                  r.returncode != 0 and "Surfaces touched" in r.stderr))
    r = go("ci11", ["plc-memory-alignment"], ["meta:" + meta_ok])
    body = brief("ci11", "plc-memory-alignment") if r.returncode == 0 else ""
    cases.append(("non-citer seats carry meta coverage and HEAD provenance",
                  r.returncode == 0 and "Sweep file:" in body and "subject provenance:" in body))
    # The real producer's bytes must satisfy the shared boundary predicate, not only a planted pointer.
    import citer_coverage
    with open(meta_ok, "r", encoding="utf-8") as fh:
        before = fh.read()
    with open(meta_ok, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("**Shape:** meta\n" + before)
    r = go("ci12", ["plc-doctrine-consistency+plc-evidence-grounding"], ["meta:" + meta_ok])
    job = json.load(open(os.path.join(tmp, "jobs_ci12.json"), encoding="utf-8"))[0] if r.returncode == 0 else {}
    behavior_path = os.path.join(tmp, "ci12_behavior.json").replace("\\", "/")
    behavior = json.load(open(behavior_path, encoding="utf-8")) if os.path.isfile(behavior_path) else {}
    cases.append(("native merged grounding seat receives the full typed artifact as a sized must-read input",
                  r.returncode == 0 and all(key in behavior for key in
                      ("status", "counts", "units", "claims", "uncovered", "scope"))
                  and ".claude/tools/lens.py" in behavior.get("scope", {}).get("paths", [])
                  and behavior.get("proposalSubjects", [{}])[0].get("status") == "pending"
                  and any(entry["path"] == behavior_path for entry in job.get("mustRead", []))
                  and behavior_path in brief("ci12", "plc-doctrine-consistency+plc-evidence-grounding")
                  and "input/default/branch/output" in brief("ci12", "plc-doctrine-consistency+plc-evidence-grounding")))
    job["mustRead"] = [{"path": meta_ok, "lines": 1}]
    cases.append(("a real merged materialized native brief satisfies Runner 4",
                  r.returncode == 0 and citer_coverage.coverage_reason(job, review=True) is None))
    real_must = plan("ci_subject_list.txt", meta_ok + "\n")
    sr = run(["--keys", "plc-doctrine-consistency+plc-evidence-grounding", "--target", "sidecar",
              "--model", "sideseat", "--effort", "max", "--out", tmp, "--prefix", "ci_sidecar",
              "--context-path", ctx, "--must-read", real_must, "--subject-plan", "meta:" + meta_ok,
              "--vault", novault], env=dict(sidecar_env, **CITER_ENV), subject=False)
    import sidecar_fanout
    from unittest.mock import patch
    sidecar_allowed = False
    if sr.returncode == 0:
        sjob = json.load(open(os.path.join(tmp, "jobs_ci_sidecar.json"), encoding="utf-8"))[0]
        behavior_path = os.path.join(tmp, "ci_sidecar_behavior.json").replace("\\", "/")
        cases.append(("sidecar merged grounding seat receives the full behavior artifact without running proofs",
                      os.path.isfile(behavior_path)
                      and any(entry["path"] == behavior_path for entry in sjob["mustRead"])
                      and behavior_path in brief("ci_sidecar", "plc-doctrine-consistency+plc-evidence-grounding")))
        sdata = json.load(open(sidecar_env["HARNESS_MODEL_REGISTRY"], encoding="utf-8"))
        with patch.object(sidecar_fanout.citer_coverage, "BLOCKING", True):
            try:
                sidecar_fanout.plan_job(sjob, sdata, tmp, False)
                sidecar_allowed = True
            except sidecar_fanout.JobError:
                pass
    cases.append(("a real sidecar materialized brief passes blocking pre-launch validation", sidecar_allowed))
    sweep_path = os.path.join(tmp, "ci12_ci_meta_ok_citers.json")
    if r.returncode == 0:
        with open(sweep_path, encoding="utf-8") as fh:
            sweep = json.load(fh)
        for entry in sweep["subjects"]:
            entry["head"] = "unrelated-HEAD-move"
        with open(sweep_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(sweep, fh)
        cases.append(("sweep HEAD remains provenance for real materialized bytes",
                      citer_coverage.coverage_reason(job, review=True) is None))
        with open(meta_ok, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("content changed\n")
        cases.append(("real materialized bytes become stale when the subject content changes",
                      "stale" in (citer_coverage.coverage_reason(job, review=True) or "")))
    return cases


def bounded_citer_cases(tmp):
    """Hundreds of payload rows stay on disk; brief bytes depend only on surfaces."""
    from pathlib import Path
    from unittest.mock import patch
    import citers
    import lens_briefs
    out = Path(tmp) / "bounded"
    out.mkdir()
    subject = out / "subject.md"
    surface = {"kind": "path", "surface": ".claude/tools/lens.py", "change": "rewrite"}
    subject.write_text("# Plan\n```surfaces\n" + json.dumps(surface) + "\n```\n\n## Job map\n\n| Slice | Job |\n|---|---|\n| S1 | inline |\n",
                       encoding="utf-8")
    context = out / "context.md"
    context.write_text("# Context\n", encoding="utf-8")
    must = out / "must.txt"
    must.write_text(str(context) + "\n", encoding="utf-8")
    result = {
        "surfaces": [surface],
        "citers": [{"surface": surface["surface"], "path": f"caller{i}.md", "line": i + 1,
                    "kind": "bare-path", "text": "PAYLOAD_ONLY " + "x" * 200} for i in range(600)],
        "duties": [{"surface": surface["surface"], "sentence": f"PAYLOAD_ONLY duty {i} must run."}
                   for i in range(500)],
        "coverage": {"files": 700, "ignoredTests": 2, "vaultVerified": False,
                     "vault": [{"absent": True}], "excludedRows": {"subjectPlan": 4, "generatedJsonl": 7},
                     "truncated": [{"surface": surface["surface"], "path": f"caller{i}.md", "omitted": 2}
                                   for i in range(400)]},
    }
    argv = [BRIEFS, "--keys", "plc-doctrine-consistency", "--out", str(out), "--prefix", "bound",
            "--context-path", str(context), "--model", SERVING_PIN, "--effort", "high",
            "--must-read", str(must), "--subject-plan", "meta:" + str(subject)]
    with patch.object(sys, "argv", argv), patch.object(citers, "sweep", return_value=result):
        lens_briefs.main()
    body = (out / "bound_plc-doctrine-consistency.md").read_text(encoding="utf-8")
    section = body.split("# INBOUND_REFS\n", 1)[1].split("# Must read", 1)[0]
    sweep_file = out / "bound_subject_citers.json"
    stored = json.loads(sweep_file.read_text(encoding="utf-8")) if sweep_file.exists() else None
    cases = [
        ("hundreds of citers, duties and truncation rows yield an INBOUND_REFS section under 4096 bytes",
         len(section.encode("utf-8")) < 4096 and "PAYLOAD_ONLY" not in section),
        ("the sweep file beside the briefs preserves every payload row", stored == result),
        ("INBOUND_REFS names the sweep file and tells the seat to Grep judged surfaces",
         sweep_file.resolve().as_posix() in section and "Grep" in section and "surfaces" in section),
        ("the bounded section carries totals, coverage flags and per-surface counts",
         "citers: 600" in section and "duties: 500" in section and "truncated: 800" in section
         and '"vaultVerified": false' in section and "generatedJsonl" in section),
        ("the seed and summary stay blockquoted, never seat instructions",
         all(line.startswith(">") for line in section.splitlines() if line.strip())),
    ]
    smaller = copy.deepcopy(result)
    smaller["citers"] = smaller["citers"][:300]
    smaller["duties"] = smaller["duties"][:250]
    smaller["coverage"]["truncated"] = smaller["coverage"]["truncated"][:200]
    with patch.object(sys, "argv", argv), patch.object(citers, "sweep", return_value=smaller):
        lens_briefs.main()
    small = (out / "bound_plc-doctrine-consistency.md").read_text(encoding="utf-8")
    small = small.split("# INBOUND_REFS\n", 1)[1].split("# Must read", 1)[0]
    cases.append(("doubling payload rows does not grow the section when count digit widths stay fixed",
                  len(section.encode("utf-8")) == len(small.encode("utf-8"))))
    normalized = small.replace(str(out), "<OUT>").replace(out.resolve().as_posix(), "<OUT>")
    cases.append(("bounded summary bytes match the recaptured rendering fixture",
                  hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] == SMALL_INBOUND_FIXTURE))
    return cases


def seat_overage_cases():
    """[(label, ok)] for `seat_overage`: keyword arguments only, one reading and fit from `dispatch_size`."""
    sys.path.insert(0, os.path.join(ROOT, ".claude", "tools"))
    import lens_briefs
    import model_registry
    data = model_registry.load()
    tmp = tempfile.mkdtemp(prefix="lb_over_")
    ctx = os.path.join(tmp, "ctx.md")
    with open(ctx, "w", encoding="utf-8") as fh:
        fh.write("x" * 1000)
    listed = os.path.join(tmp, "must.md")
    with open(listed, "w", encoding="utf-8") as fh:
        fh.write("x" * 1000)
    must = [{"path": listed, "lines": 1}]
    kw = dict(key="seat-a", token="haiku", context_path=ctx, entries=must, data=data)
    try:
        lens_briefs.seat_overage("seat-a", "haiku", "b", ctx, must, data)
        positional = False
    except TypeError:
        positional = True
    fit = lens_briefs.seat_overage(body="small", **kw)
    usable = model_registry.usable_window(model_registry.resolve("haiku", data))
    body = "y" * math.ceil((usable + 1000) * 2.5)
    over = lens_briefs.seat_overage(body=body, **kw)
    unknown_row = "no-such-row"
    odd = lens_briefs.seat_overage(**dict(kw, token=unknown_row), body="b")
    tokens = math.ceil((len(body) + 2000) / 2.5)
    return [("seat_overage takes keyword arguments only", positional),
            ("a seat whose brief, context and must-read files fit its pin's window is not refused", fit is None),
            ("an oversize seat is refused with its tokens, window and overage",
             over == (f"seat-a: {tokens} tokens of brief, context and must-read files on haiku exceed its usable "
                      f"window of {usable} tokens; over by {tokens - usable}")),
            ("a token the registry does not resolve is refused naming it",
             isinstance(odd, str) and odd.startswith("seat-a: no-such-row does not resolve in the registry")),
            ("the repo-path resolver says what it resolves",
             hasattr(lens_briefs, "repo_path") and not hasattr(lens_briefs, "resolved"))]


if __name__ == "__main__":
    sys.exit(main())
