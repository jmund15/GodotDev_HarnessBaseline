#!/usr/bin/env python3
"""RED-first proof: after planning, the fan-out hands the jobs it placed on the SESSION's own plan to a native
`dispatch.js` Workflow call and keeps every other job a sidecar.

Planted: the session's plan (`CLAUDE_CODE_TRANSPORT` plus a session id), each job's plan (a named `plan`, so the
pick is not a live capacity read) and the capacity refusals. `_main` runs in-process with `--detach`; the one
stubbed seam is `detach_driver`, recorded so a case can say whether a driver would have started and on which jobs
file. No process is a launcher and nothing is billed. The real-input observation (the live registry, the live
plan reads) is `--dry-run` over the ledger jobs file, run by hand and quoted in the commit.

Run: python3 .claude/tests/test_sidecar_fanout_native_split.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
spec = importlib.util.spec_from_file_location("sf_native_split", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)
import model_registry  # noqa: E402

DATA = model_registry.load()
SESSION, OTHER = "anthropic", "anthropic-secondary"
SCRATCH = os.path.join(str(sf.REPO), ".claude", "scratch")
os.makedirs(SCRATCH, exist_ok=True)
ROOT = tempfile.mkdtemp(prefix="nsplit_", dir=SCRATCH)
PROMPT = os.path.join(ROOT, "brief.md")
with open(PROMPT, "w", encoding="utf-8") as fh:
    fh.write("Do the slice.\n")

sf.admission_refusal = lambda *_a, **_k: None
sf.spare_facts = lambda *_a, **_k: []
sf.fit_facts = lambda *_a, **_k: []
sf.launcher_fit_refusal = lambda *_a, **_k: None
sf.doctrine_refusal = lambda *_a, **_k: None

ROOM = [None]
sf._dispatch_checks_module().native_width = lambda *_a, **_k: {"room": ROOM[0], "live": [], "pressure": None, "note": None}

DRIVERS = []
LAST = []


def fake_driver(argv, jobs, out_dir, claim=None, log_path=None, rest_file=None):
    DRIVERS.append(str(rest_file or jobs))
    DRIVERS.append(list(argv))
    return 0


sf.detach_driver = fake_driver


def job(label, plan=SESSION, **extra):
    return dict({"label": label, "kind": "scout", "alias": "haiku", "effort": "high", "plan": plan,
                 "promptFile": PROMPT}, **extra)


def run(spec, *flags, env=None, out=None, detach=True, jobs_file=None):
    """(exit code, stdout, out dir, jobs file) of one in-process `_main` over `spec` as a jobs file, or over the
    existing `jobs_file`."""
    out = out or tempfile.mkdtemp(dir=ROOT, prefix="out_")
    if jobs_file is None:
        jobs_file = os.path.join(tempfile.mkdtemp(dir=ROOT, prefix="in_"), "jobs.json")
        with open(jobs_file, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(spec, fh)
    keep = {k: os.environ.get(k) for k in ("CLAUDE_CODE_TRANSPORT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR",
                                           "CLAUDE_CONFIG_DIR")}
    base = {"CLAUDE_CODE_TRANSPORT": SESSION, "CLAUDE_CODE_SESSION_ID": "sess-proof", "CLAUDE_CODE_SIDECAR": None,
            "CLAUDE_CONFIG_DIR": None}
    base.update(env or {})
    for key, value in base.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    DRIVERS.clear()
    buf = io.StringIO()
    argv = [jobs_file, "--out-dir", out, *flags] + (["--detach"] if detach else [])
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = sf._main(argv)
    finally:
        for key, value in keep.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    LAST[:] = [buf.getvalue(), list(DRIVERS)]
    return code, buf.getvalue(), out, jobs_file


def read(out, name):
    path = os.path.join(out, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


CASES = []


def case(name, fn):
    try:
        ok, detail = bool(fn()), ""
    except Exception as exc:  # noqa: BLE001 - the proof reports every case
        ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
    if not ok and not detail:
        detail = "  last run: " + repr(LAST)[:700]
    CASES.append((name, ok, detail))


# ---- a job placed on the session's plan becomes native.args.json, and no driver starts ------------------------
code, text, out, _ = run([job("nat1")])
args = read(out, "native.args.json")
case("a session-plan job writes native.args.json and starts no driver",
     lambda: code == 0 and args is not None and [j["label"] for j in args["jobs"]] == ["nat1"] and DRIVERS == [])
case("the native job carries the engine's fields and no injected key",
     lambda: args["jobs"][0].get("shape") == "survey" and args["jobs"][0].get("agentType") == "general-purpose"
     and args["jobs"][0].get("effort") == "high" and args["jobs"][0].get("model")
     and os.path.isabs(args["jobs"][0]["promptPath"])
     and not any(k.startswith("__") or k in ("currency", "currencyReason", "checker", "plan", "alias", "kind")
                 for k in args["jobs"][0]) and not any(k.startswith("__") for k in args))
case("spillDir is the out dir, repo-relative under .claude/scratch",
     lambda: args["spillDir"].startswith(".claude/scratch/") and ".." not in args["spillDir"].split("/"))
case("the one printed call names the plan, the count and the args file",
     lambda: "NATIVE 1 on %s: Workflow(" % SESSION in text and "native.args.json" in text and "jobsFile" in text)
case("native.jobs.json holds the same job in fan-out names with the session plan excluded",
     lambda: read(out, "native.jobs.json")["jobs"][0]["excludePlans"] == [SESSION]
     and read(out, "native.jobs.json")["jobs"][0]["alias"] == "haiku")
case("an empty remainder writes no sidecar.jobs.json", lambda: read(out, "sidecar.jobs.json") is None)

# ---- a job placed on another plan stays a sidecar ---------------------------------------------------------------
code, text, out, jobs_file = run([job("nat2"), job("side1", plan=OTHER)])
rest = read(out, "sidecar.jobs.json")
case("a secondary-plan job stays in the sidecar remainder, which carries excludePlans",
     lambda: [j["label"] for j in rest["jobs"]] == ["side1"] and rest["jobs"][0]["excludePlans"] == [SESSION]
     and [j["label"] for j in read(out, "native.args.json")["jobs"]] == ["nat2"])
case("the driver is handed the remainder file, not the original",
     lambda: len(DRIVERS) == 2 and os.path.normcase(os.path.abspath(DRIVERS[0])) != os.path.normcase(os.path.abspath(jobs_file))
     and os.path.basename(DRIVERS[0]) == "sidecar.jobs.json")
case("the stay line names the plan that pays", lambda: "side1" in text and OTHER in text and "sidecar" in text)

# ---- fail-closed enablers -------------------------------------------------------------------------------------
for name, env in (("an unreadable session plan", {"CLAUDE_CODE_TRANSPORT": None, "CLAUDE_CONFIG_DIR": ROOT + "/nowhere"}),
                  ("no session id", {"CLAUDE_CODE_SESSION_ID": None}),
                  ("a sidecar child (it has no Workflow tool)", {"CLAUDE_CODE_SIDECAR": "1"})):
    code, text, out, _ = run([job("closed1")], env=env)
    case("%s keeps every job a sidecar" % name,
         lambda: code == 0 and read(out, "native.args.json") is None and len(DRIVERS) == 2)

for flag in (["--max-parallel", "2"], ["--authorize"], ["--compare"]):
    code, text, out, _ = run([job("flag1")], *flag)
    case("%s keeps every job a sidecar" % " ".join(flag), lambda: read(out, "native.args.json") is None)

parser = sf.build_parser()
case("--review, --fold, --refold keep the whole run a sidecar (split_enabled)",
     lambda: all(not sf.split_enabled(parser.parse_args(["j.json", *flags]))[0] for flags in (
         ["--review"], ["--review", "--fold-alias", "a", "--fold-effort", "e", "--fold-subject", "r::m::f"],
         ["--out-dir", "x", "--refold"])))
case("a plain run is eligible to split", lambda: sf.split_enabled(parser.parse_args(["j.json"]))[0] is True)

# ---- a job only a process can run stays a sidecar, on the session plan ----------------------------------------
schema = os.path.join(ROOT, "schema.json")
SCHEMA_OBJ = {"type": "object", "properties": {"verdict": {"type": "string"}}}
with open(schema, "w", encoding="utf-8") as fh:
    json.dump(SCHEMA_OBJ, fh)
code, text, out, _ = run([job("needs-schema", schemaFile=schema), job("plain")])
nat = read(out, "native.args.json")
case("a schemaFile job routes native and native.args.json holds its schema object",
     lambda: code == 0 and [j["label"] for j in nat["jobs"]] == ["needs-schema", "plain"]
     and nat["jobs"][0]["schema"] == SCHEMA_OBJ and "schema" not in nat["jobs"][1]
     and "schemaFile" not in nat["jobs"][0] and read(out, "sidecar.jobs.json") is None)
bad_json = os.path.join(ROOT, "bad_schema.json")
open(bad_json, "w").write("{not json")
bad_list = os.path.join(ROOT, "list_schema.json")
open(bad_list, "w").write("[1]")
for what, path in (("unreadable JSON", bad_json), ("a non-object", bad_list)):
    code, text, out, _ = run([job("schema-bad", schemaFile=path)])
    case("a schemaFile holding %s is refused at plan time, naming the job and the file" % what,
         lambda: code != 0 and "schema-bad" in text and os.path.basename(path) in text
         and read(out, "native.args.json") is None and DRIVERS == [])
code, text, out, _ = run([job("sch-other", plan=OTHER, schemaFile=bad_list)])
case("a bad schemaFile on a job that stays a sidecar is the sidecar's concern, not a refusal",
     lambda: code == 0 and read(out, "native.args.json") is None)
code, text, out, _ = run([job("at-root", workdir=str(sf.REPO))])
nat = read(out, "native.args.json")
case("a workdir equal to the repo root routes native and the job carries no workdir",
     lambda: nat is not None and [j["label"] for j in nat["jobs"]] == ["at-root"] and "workdir" not in nat["jobs"][0])
code, text, out, _ = run([job("at-root-dot", workdir=os.path.join(str(sf.REPO), "x", ".."))])
case("...compared normalized, so a spelled-differently root is the root",
     lambda: read(out, "native.args.json") is not None)
elsewhere = tempfile.mkdtemp(prefix="nsplit_checkout_")
code, text, out, _ = run([job("elsewhere", workdir=elsewhere)])
case("a workdir naming another checkout stays a sidecar and the reason names the checkout cause",
     lambda: read(out, "native.args.json") is None and len(DRIVERS) == 2
     and "elsewhere: stays a sidecar (`workdir` needs a process started in another checkout)" in text
     and "no Workflow equivalent" not in text)
shutil.rmtree(elsewhere, ignore_errors=True)
code, text, out, _ = run([job("root-and-window", workdir=str(sf.REPO), window="full")])
case("a repo-root workdir job kept a sidecar by another field names only that field",
     lambda: read(out, "native.args.json") is None and len(DRIVERS) == 2 and "`window` has no Workflow equivalent" in text
     and "workdir" not in text.split("root-and-window", 1)[1].splitlines()[0])
code, text, out, _ = run([job("no-effort", effort=None)])
case("a job with no effort stays a sidecar: the Workflow engine fills none",
     lambda: read(out, "native.args.json") is None and "effort" in text)
code, text, out, _ = run([job("own-ctx", contextFiles=[PROMPT])])
nat = read(out, "native.args.json")
case("a per-job contextFiles job routes native with contextPaths",
     lambda: nat is not None and nat["jobs"][0]["contextPaths"] == [os.path.abspath(PROMPT).replace("\\", "/")]
     and "contextFiles" not in nat["jobs"][0])
shared = os.path.join(ROOT, "shared.md")
open(shared, "w").write("shared")
code, text, out, _ = run({"jobs": [job("with-shared", contextFiles=[shared, PROMPT])], "contextPath": shared})
nat = read(out, "native.args.json")
case("a contextFiles entry equal to the shared contextPath is not repeated in contextPaths",
     lambda: nat is not None and nat["jobs"][0]["contextPaths"] == [os.path.abspath(PROMPT).replace("\\", "/")]
     and nat["contextPath"] == os.path.abspath(shared).replace("\\", "/"))

# ---- the out dir must sit under .claude/scratch ---------------------------------------------------------------
outside = tempfile.mkdtemp(prefix="nsplit_outside_")
code, text, out, _ = run([job("outside1")], out=outside)
case("an out dir outside .claude/scratch keeps every job a sidecar, with the reason printed",
     lambda: read(out, "native.args.json") is None and ".claude/scratch" in text)
shutil.rmtree(outside, ignore_errors=True)

# ---- lanes go native whole or not at all ----------------------------------------------------------------------
lane = {"chains": [{"name": "A", "exclusiveResource": "wt", "jobs": [job("a1"), job("a2")]},
                   {"name": "B", "jobs": [job("b1"), job("b2", plan=OTHER)]}], "sharedSingleFlight": ["port 80"]}
code, text, out, _ = run(lane)
native = read(out, "native.args.json")
rest = read(out, "sidecar.jobs.json")
case("a fully native lane yields dispatch_chains args and a partly native lane stays sidecar whole",
     lambda: [c["name"] for c in native["chains"]] == ["A"] and native["chains"][0]["exclusiveResource"] == "wt"
     and native["sharedSingleFlight"] == ["port 80"] and [c["name"] for c in rest["chains"]] == ["B"]
     and [j["label"] for j in rest["chains"][0]["jobs"]] == ["b1", "b2"])
case("a downgraded lane job paid by the session plan is not excluded from it, one paid elsewhere is",
     lambda: "excludePlans" not in rest["chains"][0]["jobs"][0] and rest["chains"][0]["jobs"][1]["excludePlans"] == [SESSION])

# ---- the remainder never picks the session plan again ---------------------------------------------------------
excluded = {"alias": "haiku", "excludePlans": [SESSION]}
case("job_routes drops an excluded plan from the plans a job may pick",
     lambda: SESSION not in sf.job_routes(excluded, DATA)[1] and OTHER in sf.job_routes(excluded, DATA)[1])


def every_plan_excluded():
    plans = sf.job_routes({"alias": "haiku"}, DATA)[1]
    try:
        sf.job_routes({"alias": "haiku", "excludePlans": plans}, DATA)
    except sf.JobError as exc:
        return "excludePlans" in str(exc)
    return False


case("job_routes refuses a job whose every plan is excluded", every_plan_excluded)

# ---- the driver reruns the remainder without splitting again, and a rerun clears a stale hand-off --------------
code, text, out, _ = run([job("nat3"), job("side3", plan=OTHER)])
before = read(out, "native.args.json")
code2, text2, _o, _j = run(None, out=out, jobs_file=os.path.join(out, "sidecar.jobs.json"))
case("the remainder file is the driver's input: it neither splits nor clears the hand-off",
     lambda: read(out, "native.args.json") == before and "NATIVE" not in text2)
code, text, out, _ = run([job("stale1")], out=out)
case("a rerun in the same out dir replaces the previous hand-off files",
     lambda: [j["label"] for j in read(out, "native.args.json")["jobs"]] == ["stale1"]
     and read(out, "sidecar.jobs.json") is None)
code, text, out, _ = run([job("stale2", plan=OTHER)], out=out)
case("a rerun that hands nothing off removes the previous hand-off files",
     lambda: read(out, "native.args.json") is None and read(out, "native.jobs.json") is None)

# ---- --dry-run names the routing and writes no hand-off file ---------------------------------------------------
code, text, out, _ = run([job("dry1"), job("dry2", plan=OTHER)], "--dry-run", detach=False)
case("--dry-run prints which job routes native and which stays, and writes no hand-off file",
     lambda: code == 0 and "dry1" in text and "native" in text and "dry2" in text
     and read(out, "native.args.json") is None and DRIVERS == [])

sf.doctrine_refusal = lambda *_a, **_k: "Dispatch DENIED: planted"
code, text, out, _ = run([job("doctrine1"), job("doctrine2", plan=OTHER)])
case("a doctrine refusal comes before any hand-off file is written",
     lambda: code == 2 and "Dispatch DENIED" in text and read(out, "native.args.json") is None and DRIVERS == [])
sf.doctrine_refusal = lambda *_a, **_k: None

# ---- a hot five-hour window keeps at most the shared predicate's room native -------------------------------
ROOM[0] = 1
code, text, out, _ = run([job("w1"), job("w2"), job("w3")])
args = read(out, "native.args.json")
resume = read(out, "resume-jobs.json")
case("hot, room 1: one session-plan job goes native and the named-plan rest is not run, written to resume-jobs.json",
     lambda: code == 0 and [j["label"] for j in args["jobs"]] == ["w1"] and "not run: native width" in text
     and [j["label"] for j in resume["jobs"]] == ["w2", "w3"] and read(out, "sidecar.jobs.json") is None
     and DRIVERS == [])
ROOM[0] = 0
code, text, out, _ = run([job("z1")])
case("hot, room 0: nothing goes native, the job is not run, no driver starts",
     lambda: code == 0 and read(out, "native.args.json") is None
     and [j["label"] for j in read(out, "resume-jobs.json")["jobs"]] == ["z1"] and DRIVERS == [])
ROOM[0] = 1
code, text, out, _ = run([job("m1", plan=OTHER), job("w4"), job("w5")])
case("hot: a sidecar job on another plan still runs from the remainder file",
     lambda: [j["label"] for j in read(out, "native.args.json")["jobs"]] == ["w4"]
     and [j["label"] for j in read(out, "sidecar.jobs.json")["jobs"]] == ["m1"]
     and len(DRIVERS) == 2 and DRIVERS[0].endswith("sidecar.jobs.json"))
code, text, out, _ = run({"chains": [{"name": "L", "jobs": [job("l1"), job("l2")]}]})
case("hot, room 1: a lane counts as one native unit",
     lambda: [c["name"] for c in read(out, "native.args.json")["chains"]] == ["L"]
     and read(out, "resume-jobs.json") is None)
sf.SLOTS["mv1"] = sf.Slot(SESSION, (), (SESSION, OTHER))
sf.SLOTS["st1"] = sf.Slot(SESSION, (), ())
movable, stuck = job("mv1"), job("st1")
case("width_unrun holds a cut job no other plan holds and keeps one another plan can take",
     lambda: sf.width_unrun([movable, stuck], None, SESSION) == {"st1"})
case("a lane moves whole, so a stuck lane-mate holds the lane's cut jobs",
     lambda: sf.width_unrun([movable, stuck], [("L", ["mv1", "st1"])], SESSION) == {"mv1", "st1"})
out = tempfile.mkdtemp(dir=SCRATCH, prefix="out_hand_")
routed = {"nat9": None, "mv1": sf.WIDTH_CUT, "st1": sf.WIDTH_CUT}
sf.PAID_BY.update({"nat9": SESSION, "mv1": SESSION, "st1": SESSION})
handed = sf.hand_off(routed, [job("nat9"), movable, stuck], None, SESSION, None, out, DATA, True)
rest = read(out, "sidecar.jobs.json")
case("a width-cut job another plan can take reaches the remainder file with the session plan excluded",
     lambda: sorted(handed) == ["nat9", "st1"] and [j["label"] for j in rest["jobs"]] == ["mv1"]
     and rest["jobs"][0]["excludePlans"] == [SESSION]
     and [j["label"] for j in read(out, "resume-jobs.json")["jobs"]] == ["st1"])
shutil.rmtree(out, ignore_errors=True)
ROOM[0] = None
code, text, out, _ = run([job("c1"), job("c2")])
case("a cool window keeps every session-plan job native",
     lambda: [j["label"] for j in read(out, "native.args.json")["jobs"]] == ["c1", "c2"])

def brief(name, text):
    path = os.path.join(ROOT, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


ORDER = brief("order.md", "Survey the hooks.\nThen run dispatch.js with the jobs file.\n")
TERMS = brief("terms.md", "Run `git grep` over `.claude/` for: `sidecar_fanout`, `fan-out`, `dispatch.js`, `Workflow`.\n")
code, text, out, _ = run([job("ok1", promptFile=TERMS), job("bad1", promptFile=ORDER)])
case("a brief that orders a fan-out is refused in the fan-out's own call, naming the job and orchestration section 0",
     lambda: code != 0 and "bad1" in text and "orchestration §0" in text
     and read(out, "native.args.json") is None and DRIVERS == [])
code, text, out, _ = run([job("ok2", promptFile=TERMS), job("ok3", plan=OTHER, promptFile=TERMS)])
case("a brief that only names the engine as a search term plans, native and sidecar alike",
     lambda: code == 0 and [j["label"] for j in read(out, "native.args.json")["jobs"]] == ["ok2"])
code, text, out, _ = run([job("bad2", plan=OTHER, promptFile=ORDER)])
case("...and a sidecar-bound job is refused the same way",
     lambda: code != 0 and "bad2" in text and DRIVERS == [])

shutil.rmtree(ROOT, ignore_errors=True)
failed = 0
for name, ok, detail in CASES:
    failed += not ok
    print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
sys.exit(1 if failed else 0)
