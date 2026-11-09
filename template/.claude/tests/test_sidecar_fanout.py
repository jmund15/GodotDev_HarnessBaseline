#!/usr/bin/env python3
"""Proof for tools/sidecar_fanout.py -- the planning layer only.

Every job is a BILLED call, so nothing here dispatches: the cases exercise `plan_job`, which does
all the validation, and `--dry-run`, which prints the argv it would have run. One live end-to-end
case is deliberately out of scope -- it would spend quota to prove what `--dry-run` already shows.

The load-bearing cases are the refusals. A fan-out that dispatches nine jobs and then errors on the
tenth has already spent the money; every refusal must fire in the planning pass, before any process
starts. And `-l` must be on every argv: `orchestration_metrics.collect_sidecar()` skips a row with
no label, so an unlabelled job is spend that vanishes from the ledger.

Run: python3 .claude/tests/test_sidecar_fanout.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _transport_fixture  # noqa: E402
from _capacity_fixtures import write_capacity_cache  # noqa: E402
MOD = os.path.join(HERE, "..", "tools", "sidecar_fanout.py")
spec = importlib.util.spec_from_file_location("sf", MOD)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

TMP = tempfile.mkdtemp(prefix="sfanout_")
# Every plan has room, in-process and in the CLI children: a job's plan pick never reads the machine's quota.
os.environ["PROVIDER_CAPACITY_CACHE"] = write_capacity_cache(os.path.join(TMP, "capacity.json"), sf.model_registry.load())
os.environ["HARNESS_USAGE_SAMPLES"] = os.path.join(TMP, "usage.jsonl")
PROMPT = os.path.join(TMP, "p.md")
CTX = os.path.join(TMP, "c.md")
open(PROMPT, "w", encoding="utf-8").write("lens body\n")
open(CTX, "w", encoding="utf-8").write("shared context\n")

DATA = {
    "transports": {
        "anthropic": {"costModel": "plan-quota"},
        "codex": {"costModel": "plan-quota", "launcher": ".claude/scripts/codex_proxy_sidecar.sh",
                  "serverSidePins": {"effortValues": ["none", "low", "medium", "high", "xhigh", "max"]}},
        "opencode": {"costModel": "marginal-usd", "launcher": ".claude/scripts/opencode_sidecar.sh",
                     "effortValues": ["low", "medium", "high", "xhigh", "max"]},
        "nolauncher": {"costModel": "plan-quota"},
    },
    "models": [
        {"transport": "codex", "id": "gpt-5.6-luna", "alias": "luna", "roles": ["sonnet"],
         "effortValues": ["low", "max"],
         "effort": {"evidence": "see-ladder"}},
        {"transport": "codex", "id": "gpt-6-terra", "alias": "terra", "roles": [],
         "effort": {"evidence": "unmeasured"}},
        {"transport": "codex", "id": "gpt-6-astra", "alias": "astra", "roles": ["astra"],
         "status": {"state": "unavailable", "scope": "all", "reason": "owner decision"}},
        {"transport": "opencode", "id": "muse-spark-1.3", "alias": "muse", "roles": ["sonnet"]},
        {"transport": "nolauncher", "id": "orphan-1", "alias": "orphan", "roles": []},
    ],
}

JOB = {"label": "lens-a", "alias": "muse", "promptFile": PROMPT,
       "effort": "max", "kind": "reviewer", "contextFiles": [CTX]}


def plan(job, out=TMP, authorize=False):
    return sf.plan_job(dict(job) if isinstance(job, dict) else job, DATA, out, authorize)


def refuses(job, needle):
    try:
        plan(job)
    except sf.JobError as err:
        return needle in str(err)
    return False


_, ARGV, OUTPATH = plan(JOB)
GIT_BRIEF = os.path.join(TMP, "git_brief.md")
open(GIT_BRIEF, "w", encoding="utf-8").write("Enumerate with `git diff origin/main...HEAD`.\n")
WF_BRIEF = os.path.join(TMP, "wf_brief.md")
open(WF_BRIEF, "w", encoding="utf-8").write("Run `Workflow({script: 'x'})` over the three lenses.\n")


KJOB = {"label": "lens-a", "alias": "muse", "promptFile": PROMPT, "effort": "max", "contextFiles": [CTX]}


def flag(argv, f):
    return argv[argv.index(f) + 1] if f in argv else None


# A route with a second plan (`planOf`): a job's `plan` key picks the paying plan's launcher.
PLAN_DATA = {
    "transports": dict(DATA["transports"], **{
        "anthropic": {"costModel": "plan-quota", "launcher": ".claude/scripts/anthropic_sidecar.sh",
                      "effortValues": ["low", "medium", "high", "xhigh"]},
        "anthropic-secondary": {"costModel": "plan-quota", "planOf": "anthropic",
                                "launcher": ".claude/scripts/anthropic_secondary_sidecar.sh",
                                "status": {"state": "available"}},
    }),
    "models": DATA["models"] + [{"transport": "anthropic", "id": "host-exec-1", "alias": "h-exec",
                                 "roles": ["executor"]}],
}
PLAN_JOB = dict(JOB, alias="h-exec", effort="high")


# A route whose vocabulary carries `max` and whose rows differ on it: `effort.avoid` is a fact about a row.
AVOID_DATA = {
    "transports": dict(DATA["transports"], anthropic={
        "costModel": "plan-quota", "launcher": ".claude/scripts/anthropic_sidecar.sh",
        "effortValues": ["low", "medium", "high", "xhigh", "max"]}),
    "models": DATA["models"] + [
        {"transport": "anthropic", "id": "host-waste-1", "alias": "h-waste", "roles": ["executor"],
         "effort": {"evidence": "see-ladder", "avoid": ["max"], "avoidRef": "planted evidence line"}},
        {"transport": "anthropic", "id": "host-free-1", "alias": "h-free", "roles": ["executor"],
         "effort": {"evidence": "see-ladder"}}],
}


def with_comparison(value, check):
    """Run `check` with SC_COMPARISON = value (None unsets it); SC_MEASUREMENT free text is always set,
    because no free text may lift an avoided rung."""
    saved = {k: os.environ.get(k) for k in ("SC_COMPARISON", "SC_MEASUREMENT")}
    os.environ["SC_MEASUREMENT"] = "effort-rung measurement: a long free-text declaration"
    if value is None:
        os.environ.pop("SC_COMPARISON", None)
    else:
        os.environ["SC_COMPARISON"] = value
    try:
        return check()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def plan_on(job, data=PLAN_DATA):
    return sf.plan_job(dict(job), data, TMP, False)


def refuses_on(job, needle, data=PLAN_DATA):
    try:
        plan_on(job, data)
    except sf.JobError as err:
        return needle in str(err)
    return False


def with_rooms(rooms, check):
    """Run `check` with each plan's room read as `rooms[plan]` (None: exhausted); an unlisted plan is unreadable."""
    def room_of(transport, data, read, now):
        if transport not in rooms:
            raise AssertionError("capacity read for %s" % transport)
        value = rooms[transport]
        return ({"transport": transport, "room": value} if value is not None
                else {"transport": transport, "room": None, "why": "exhausted"})
    saved = sf.plan_room
    sf.plan_room = room_of
    try:
        return check()
    finally:
        sf.plan_room = saved


PLAN_OFF = {"transports": dict(PLAN_DATA["transports"], **{"anthropic-secondary": dict(
    PLAN_DATA["transports"]["anthropic-secondary"], status={"state": "unavailable", "reason": "planted"})}),
    "models": PLAN_DATA["models"]}


def relative_paths_reach_argv_absolute():
    """Children run with cwd=REPO, so a caller-relative schemaFile or workdir must reach argv resolved."""
    caller = tempfile.mkdtemp(prefix="caller_", dir=TMP)
    os.makedirs(os.path.join(caller, "wd"))
    open(os.path.join(caller, "s.json"), "w", encoding="utf-8", newline="\n").write("{}\n")
    here = os.getcwd()
    os.chdir(caller)
    try:
        _, argv, _ = plan(dict(JOB, schemaFile="s.json", workdir="wd"))
    finally:
        os.chdir(here)
    return (flag(argv, "-S") == sf._posix(os.path.join(caller, "s.json"))
            and flag(argv, "-d") == sf._posix(os.path.join(caller, "wd")))


def no_billed_retry():
    from unittest.mock import patch
    import _supervised_launch as supervisor

    def admitted(label, child, env, argv=None):
        with patch.object(supervisor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            return supervisor.launch(label, child, env, argv)

    with patch.object(sf, "supervised_launch", admitted):
        for final in (0, 1, 5, 8, 10):
            for codes in ([final], [sf.MEMORY_REFUSED, final]):
                directory = tempfile.mkdtemp(prefix="attempt_", dir=TMP)
                output = os.path.join(directory, "job.out.json")
                with patch.object(sf.subprocess, "call", side_effect=codes) as launch:
                    result = sf.run_jobs([("job", [sys.executable, "unused.py"], output)], 1, directory)
                if launch.call_count != len(codes) or result["job"]["exit"] != final:
                    return False
    return True


CASES = [
    # ---- the argv is the deliverable ---------------------------------------
    ("the launcher comes from the registry row, not from a hardcoded name",
     lambda: ARGV[1].endswith("opencode_sidecar.sh")),

    # A bare "bash" from Windows Python is the System32 WSL shim -- a different filesystem
    # root whose $HOME has none of this user's credentials. The launcher then exits 3 or 4 and
    # blames its own subsystem. sidecar_launch owns the shell choice for every Python caller.
    ("argv[0] is a resolved shell, never the literal `bash`",
     lambda: ARGV[0] != "bash"),

    ("...and never the WSL shim under System32",
     lambda: "system32" not in ARGV[0].lower()),

    ("...and it is the one sidecar_launch resolves, not a second copy of the rule",
     lambda: ARGV[0] == sf.sidecar_launch.git_bash()),

    # The launcher path is handed to a BASH child, which reads a backslash as an escape. `str(Path)`
    # on Windows collapsed `.claude/scripts/x.sh` to `C:Users<name>...` and every job died exit 127
    # BEFORE the launcher ran -- and `endswith(".sh")` above is true of the broken path too, which
    # is why that case alone did not catch it.
    # argv[0] is the Windows executable path handed to CreateProcess and is EXEMPT -- it never
    # reaches bash's parser. Everything after it does.
    ("no bash-parsed argv element carries a backslash -- bash reads it as an escape",
     lambda: not [a for a in ARGV[1:] if "\\" in a]),

    ("...including the -R record and -P stream paths, which are built from a Path",
     lambda: "\\" not in flag(ARGV, "-R") and "\\" not in flag(ARGV, "-P")),

    ("the launcher stays repo-relative -- cwd is the repo, so no absolute path is needed",
     lambda: ARGV[1] == ".claude/scripts/opencode_sidecar.sh"),

    ("every job carries -l, or its spend vanishes from the metrics ledger",
     lambda: flag(ARGV, "-l") == "lens-a"),

    ("every job carries -R, which is what appends to the shared spend ledger",
     lambda: flag(ARGV, "-R").endswith("lens-a.record.json")),

    ("the prompt goes as a FILE path, never as argv text",
     lambda: flag(ARGV, "-f") == sf._posix(PROMPT) and "lens body" not in " ".join(ARGV)),

    ("effort and kind map to -e/-G; the kind sets disclosure, so no -D rides",
     lambda: (flag(ARGV, "-e"), flag(ARGV, "-D"), flag(ARGV, "-G")) == ("max", None, "reviewer")),

    ("context files repeat -C",
     lambda: flag(ARGV, "-C") == sf._posix(CTX)),

    ("a review-shaped job gets -P, so a max-effort compaction auto-resumes",
     lambda: "-P" in ARGV),

    # `-P` resumes; `-S` decides whether it resumes into a deliverable or into prose. Every luna
    # arm dispatched on 2026-09-08 omitted the schema, and the prose that came back was misread as
    # the model's ceiling. The default removes the decision rather than documenting it.
    ("a review-shaped job gets -S by default, without the caller naming a schema",
     lambda: flag(ARGV, "-S") == sf._posix(sf.DEFAULT_REVIEW_SCHEMA)),

    ("...and a caller-supplied schemaFile still wins",
     lambda: flag(plan(dict(JOB, schemaFile=CTX))[1], "-S") == sf._posix(CTX)),

    ("a non-review shape gets no schema, so a survey still returns prose on purpose",
     lambda: "-S" not in plan(dict(JOB, kind="scout"))[1]),

    ("the shipped default schema exists and requires findings plus checked coverage",
     lambda: json.loads(sf.DEFAULT_REVIEW_SCHEMA.read_text(encoding="utf-8"))["required"]
             == ["findings", "checked"]),

    ("-A is absent unless the caller authorizes the spend",
     lambda: "-A" not in ARGV and "-A" in plan(JOB, authorize=True)[1]),

    ("longContext: true adds -M; absent or false adds nothing",
     lambda: "-M" in plan(dict(JOB, longContext=True))[1] and "-M" not in ARGV
             and "-M" not in plan(dict(JOB, longContext=False))[1]),

    ("pick and spec (dispatch.js's pin-basis fields) reach the launcher as one -Y; absent, no -Y",
     lambda: flag(plan(dict(JOB, pick="room", spec="tight"))[1], "-Y") == "room,tight" and "-Y" not in ARGV),

    ("pick without spec is refused: the launcher records the pair or nothing",
     lambda: refuses(dict(JOB, pick="cell"), "spec")),

    ("a pick or spec outside the launcher's enums is refused at plan time, so --dry-run catches it",
     lambda: refuses(dict(JOB, pick="executor-loose", spec="judged"), "cell|room|owner|default")
             and refuses(dict(JOB, pick="cell", spec="goal stated"), "tight|judged")),

    ("the pick and spec enums equal the launcher's -Y case list",
     lambda: {"%s,%s" % (p, s) for p in sf.PIN_PICKS for s in sf.PIN_SPECS} == set(
         re.search(r"case \"\$SC_PIN_BASIS\" in\s+([^)]*)\)",
                   open(os.path.join(HERE, "..", "scripts", "lib", "sidecar_common.sh"), encoding="utf-8").read())
         .group(1).split("|"))),

    ("extraArgs append verbatim for a flag this wrapper does not model",
     lambda: plan(dict(JOB, extraArgs=["-n", "40"]))[1][-2:] == ["-n", "40"]),

    # ---- every refusal fires BEFORE anything is spent ----------------------
    ("an unavailable alias is refused, and the roster is named",
     lambda: refuses(dict(JOB, alias="astra"), "unavailable")
             and refuses(dict(JOB, alias="astra"), "Available on codex")),

    ("...and it never substitutes a replacement by rule",
     lambda: refuses(dict(JOB, alias="astra"), "Re-select under the ladder")),

    ("a transport with no launcher is refused rather than half-built",
     lambda: refuses(dict(JOB, alias="orphan"), "registers no launcher")),

    ("an unknown alias is refused",
     lambda: refuses(dict(JOB, alias="nosuchmodel"), "job lens-a")),

    ("a missing promptFile is refused",
     lambda: refuses(dict(JOB, promptFile=os.path.join(TMP, "gone.md")), "promptFile not found")),

    ("a missing explicit schemaFile is refused at planning, before any job spends",
     lambda: refuses(dict(JOB, schemaFile=os.path.join(TMP, "gone.schema.json")), "schemaFile not found")),

    ("a caller-relative schemaFile and workdir reach the launcher argv absolute",
     relative_paths_reach_argv_absolute),

    ("a missing contextFile is refused",
     lambda: refuses(dict(JOB, contextFiles=["/nope.md"]), "contextFile not found")),

    ("a job with no label is refused -- unlabelled spend is unattributable",
     lambda: refuses({k: v for k, v in JOB.items() if k != "label"}, "`label` is required")),

    ("a job with no alias is refused",
     lambda: refuses({k: v for k, v in JOB.items() if k != "alias"}, "`alias` is required")),

    ("a non-object job is refused",
     lambda: refuses("just a string", "must be an object")),

    # `label` names four output files AND keys the spend ledger. A separator writes outside
    # --out-dir; `..` escapes it. Both used to reach a worker thread as a raw OSError, after the
    # docstring promised every refusal happens before anything starts.
    ("a label with a path separator is refused",
     lambda: refuses(dict(JOB, label="a/b"), "bare filename")
             and refuses(dict(JOB, label="a" + chr(92) + "b"), "bare filename")),

    ("a label of `..` is refused",
     lambda: refuses(dict(JOB, label=".."), "bare filename")),

    # On Windows `out/p:x.record.json` is file `p` with an alternate data stream `x.record.json`:
    # the launcher writes its record where no reader looks.
    ("a label with a character Windows forbids in a file name is refused",
     lambda: all(refuses(dict(JOB, label="p17re" + c + "x"), "file name") for c in ':*?"<>|')
             and not refuses(dict(JOB, label="p17-lens.a_b"), "file name")),

    # A registry row's transport names the launcher that speaks its API. A free `transport` override
    # sent one transport's alias to another's launcher -- which authenticates, bills, and only then
    # fails on a model it has never heard of. The override survives; disagreeing does not.
    ("a transport that does not own the resolved alias is refused",
     lambda: refuses(dict(JOB, transport="codex"), "does not own muse")),

    ("...and the refusal names the transport the registry actually places it on",
     lambda: refuses(dict(JOB, transport="codex"), "'opencode'")),

    ("a transport override that AGREES with the row still plans",
     lambda: plan(dict(JOB, transport="opencode"))[1][1].endswith("opencode_sidecar.sh")),

    # ---- the paying plan picks the launcher (seam 6) ------------------------
    ("a job with `plan: anthropic-secondary` is planned and launched with the secondary wrapper",
     lambda: with_rooms({"anthropic-secondary": 1.0},
                        lambda: plan_on(dict(PLAN_JOB, plan="anthropic-secondary"))[1][1]
                        .endswith("anthropic_secondary_sidecar.sh"))),
    ("a job with no `plan` pays with the plan that has the most room, never the route's default login",
     lambda: with_rooms({"anthropic": 0.2, "anthropic-secondary": 1.1},
                        lambda: plan_on(PLAN_JOB)[1][1].endswith("anthropic_secondary_sidecar.sh"))),
    ("...and the route itself when it has the most room",
     lambda: with_rooms({"anthropic": 1.4, "anthropic-secondary": 0.3},
                        lambda: plan_on(PLAN_JOB)[1][1].split("/")[-1] == "anthropic_sidecar.sh")),
    ("...an exhausted plan never pays, whatever the others read",
     lambda: with_rooms({"anthropic": None, "anthropic-secondary": 0.1},
                        lambda: plan_on(PLAN_JOB)[1][1].endswith("anthropic_secondary_sidecar.sh"))),
    ("...with no plan holding room the job is refused, naming each plan's state",
     lambda: with_rooms({"anthropic": None, "anthropic-secondary": None},
                        lambda: refuses_on(PLAN_JOB, "anthropic-secondary: exhausted"))),
    ("...a route with one live plan runs on it",
     lambda: with_rooms({"opencode": 0.5}, lambda: plan(JOB)[1][1].endswith("opencode_sidecar.sh"))),
    ("`transport: anthropic-secondary` is still refused: transport only agrees with the row",
     lambda: refuses_on(dict(PLAN_JOB, transport="anthropic-secondary"), "does not own h-exec")),
    ("a `plan` that does not pay for the row's route is refused, naming the route's plans",
     lambda: refuses_on(dict(PLAN_JOB, plan="codex"), "anthropic-secondary")),
    ("an unavailable plan is refused",
     lambda: refuses_on(dict(PLAN_JOB, plan="anthropic-secondary"), "does not pay", PLAN_OFF)),

    # A bare string is iterable, so it splattered into argv one CHARACTER at a time and dispatched a
    # billed launcher from a job that never passed a shape check.
    ("a string `extraArgs` is refused rather than iterated per character",
     lambda: refuses(dict(JOB, extraArgs="-x"), "must be a list")),

    ("a string `contextFiles` is refused too",
     lambda: refuses(dict(JOB, contextFiles=CTX), "must be a list")),

    ("...and the refusal says why a string is not merely the wrong type",
     lambda: refuses(dict(JOB, extraArgs="-x"), "per character")),

    ("a list `extraArgs` still reaches argv verbatim",
     lambda: plan(dict(JOB, extraArgs=["-x", "1"]))[1][-2:] == ["-x", "1"]),

    # ---- the kind sets the tools; the fan-out reads no brief wording ----------
    ("a reviewer job whose brief names `git diff` plans as -G reviewer",
     lambda: flag(plan(dict(JOB, promptFile=GIT_BRIEF))[1], "-G") == "reviewer"),
    ("a brief that orders a fan-out still plans: wording is not scanned",
     lambda: flag(plan(dict(JOB, promptFile=WF_BRIEF))[1], "-G") == "reviewer"),
    ("`tools.add: [Bash]` reaches the launcher as the signed -t",
     lambda: flag(plan(dict(JOB, promptFile=GIT_BRIEF, tools={"add": ["Bash"]}))[1], "-t") == "+Bash"),
    ("`tools.add: [Edit]` on a reviewer is refused before launch, naming kind author",
     lambda: refuses(dict(JOB, tools={"add": ["Edit"]}), "use kind author")),
    ("the author kind carries Bash without a delta",
     lambda: flag(plan(dict(JOB, promptFile=GIT_BRIEF, kind="author"))[1], "-G") == "author"),

    # The ladder pins the compaction behavior to the EFFORT rung: a `max` run without -P compacts
    # and returns prose, and this tool never retries a billed call.
    ("a max-effort job gets -P even when its kind is not `reviewer`",
     lambda: "-P" in plan(dict(JOB, kind="scout", effort="max"))[1]),

    ("a low-effort survey with no schema still gets -P: the stall watchdog watches that stream",
     lambda: "-P" in plan(dict(JOB, kind="scout", effort="low"))[1]),

    # ---- every job declares one `kind`; the kind alone sets -G, -D, tools and the schema -------
    ("a job with no kind is refused, naming `kind` and the public kinds",
     lambda: refuses(KJOB, "needs a kind (scout|lens|reviewer|author|judge)")),
    ("`kind author` plans -G author and no -D",
     lambda: (flag(plan(dict(KJOB, kind="author"))[1], "-G"), flag(plan(dict(KJOB, kind="author"))[1], "-D"))
     == ("author", None)),
    ("...and its resolved built-ins include Edit, Write and Bash",
     lambda: {"Edit", "Write", "Bash"} <= set(sf.job_builtins(dict(KJOB, kind="author")))),
    ("a job carrying `shape` is refused, naming `use kind`",
     lambda: refuses(dict(KJOB, kind="lens", shape="any"), "use kind")),
    ("a job carrying `disclosure` is refused, naming `use kind`",
     lambda: refuses(dict(KJOB, kind="lens", disclosure="full"), "use kind")),
    ("a job carrying `agentType` is refused, naming `use kind`",
     lambda: refuses(dict(KJOB, kind="lens", agentType="retired-seat"), "use kind")),
    ("the internal kind bench-score is refused on the fanout route, naming the public kinds",
     lambda: refuses(dict(KJOB, kind="bench-score"), "scout, lens, reviewer, author, judge")),
    ("`kind lens` plans -G lens and no -D",
     lambda: (flag(plan(dict(KJOB, kind="lens"))[1], "-G"), flag(plan(dict(KJOB, kind="lens"))[1], "-D"))
     == ("lens", None)),
    ("`kind scout` plans -G scout and no -D",
     lambda: (flag(plan(dict(KJOB, kind="scout"))[1], "-G"), flag(plan(dict(KJOB, kind="scout"))[1], "-D"))
     == ("scout", None)),
    ("no public kind plans a -D, and the legacy adapter is gone",
     lambda: all("-D" not in plan(dict(KJOB, kind=k))[1] for k in ("scout", "lens", "reviewer", "author", "judge"))
     and not hasattr(sf.tool_profiles, "kind_launcher_flags")),
    ("`toolsCsv` on a public kind is refused, naming a signed delta",
     lambda: refuses(dict(KJOB, kind="scout", toolsCsv="Read,Glob,Grep"), "signed")),
    ("`kind reviewer` gets the default schema -S",
     lambda: flag(plan(dict(KJOB, kind="reviewer"))[1], "-S") == sf._posix(sf.DEFAULT_REVIEW_SCHEMA)),
    ("`kind judge` plans -G judge and no schema",
     lambda: (flag(plan(dict(KJOB, kind="judge"))[1], "-G"), "-S" in plan(dict(KJOB, kind="judge"))[1])
     == ("judge", False)),
    ("a reviewer brief that says `git diff` plans: the fan-out no longer reads brief wording",
     lambda: flag(plan(dict(KJOB, kind="reviewer", promptFile=GIT_BRIEF))[1], "-G") == "reviewer"),
    ("bench-review plans -G bench-review with the default review schema and no -D",
     lambda: (flag(plan(dict(KJOB, kind="bench-review"))[1], "-G"), flag(plan(dict(KJOB, kind="bench-review"))[1], "-D"),
              flag(plan(dict(KJOB, kind="bench-review"))[1], "-S"))
     == ("bench-review", None, sf._posix(sf.DEFAULT_REVIEW_SCHEMA))),
    ("an arm job's -D comes from `armDisclosure`",
     lambda: flag(plan(dict(KJOB, kind="arm", armDisclosure="pointer", baseBuiltins="Read,Grep"))[1], "-D")
     == "pointer"),
    ("an arm job without `armDisclosure` is refused at planning, naming it",
     lambda: refuses(dict(KJOB, kind="arm", baseBuiltins="Read,Grep"), "armDisclosure")),
    ("`armDisclosure` on any other kind is refused",
     lambda: refuses(dict(KJOB, kind="lens", armDisclosure="pointer"), "armDisclosure")),

    # ---- `-e` against the TRANSPORT's effort vocabulary ---------------------------------------
    # The checked set is the transport's, not a row's own `converged`/`open` rungs: the ladder cell
    # owns a row's rung, so the registry carries none, and what a launcher can forward is a
    # transport fact. A rung the row's own list omits but the transport serves still plans.
    ("an effort outside the transport's vocabulary is refused before any launch",
     lambda: refuses(dict(JOB, alias="luna", effort="ultra"), "not in codex's effort vocabulary")),

    ("...and the refusal names that vocabulary, rung by rung",
     lambda: all(refuses(dict(JOB, alias="luna", effort="ultra"), r)
                 for r in ("low", "medium", "high", "xhigh"))),

    ("every rung the transport serves plans, the row's own shorter list included",
     lambda: all(flag(plan(dict(JOB, alias="luna", effort=r))[1], "-e") == r
                 for r in ("low", "medium", "max"))),

    ("the vocabulary checked is the JOB's transport, not the session's",
     lambda: refuses(dict(JOB, effort="ultra"), "not in opencode's effort vocabulary")
             and flag(plan(dict(JOB, effort="medium"))[1], "-e") == "medium"),

    ("an unmeasured row takes its transport's vocabulary too",
     lambda: flag(plan(dict(JOB, alias="terra", effort="medium"))[1], "-e") == "medium"),

    ("a transport that declares no vocabulary refuses no rung",
     lambda: not refuses(dict(JOB, alias="orphan", effort="medium"), "effort vocabulary")),

    ("the route's vocabulary carries `max`: a row that does not avoid it plans at max",
     lambda: with_comparison(None, lambda: flag(plan_on(dict(PLAN_JOB, alias="h-free", effort="max"),
                                                         AVOID_DATA)[1], "-e") == "max")),

    ("a row that avoids the rung is refused before any launch, naming its evidence",
     lambda: with_comparison(None, lambda: refuses_on(dict(PLAN_JOB, alias="h-waste", effort="max"),
                                                       "effort.avoid", AVOID_DATA)
                              and refuses_on(dict(PLAN_JOB, alias="h-waste", effort="max"),
                                             "planted evidence line", AVOID_DATA))),

    ("...unless the fan-out runs under --compare, and the row's other rungs never needed one",
     lambda: with_comparison("1", lambda: flag(plan_on(
         dict(PLAN_JOB, alias="h-waste", effort="max"), AVOID_DATA)[1], "-e") == "max")
             and with_comparison(None, lambda: flag(plan_on(
                 dict(PLAN_JOB, alias="h-waste", effort="xhigh"), AVOID_DATA)[1], "-e") == "xhigh")),

    ("a job with no effort is not refused",
     lambda: "-e" not in plan({k: v for k, v in dict(JOB, alias="luna").items() if k != "effort"})[1]),

    # ---- it never re-bills a call on its own -------------------------------
    ("only an unstarted memory refusal retries; every billed outcome returns once", no_billed_retry),

    ("a failure is reported with its resume id rather than resumed",
     lambda: "resumeWith" in open(MOD, encoding="utf-8").read()),

    ("the launcher exit codes are reported by MEANING, not as a bare number",
     lambda: sf.LAUNCHER_EXITS[5].startswith("band gate refusal")
             and sf.LAUNCHER_EXITS[7].startswith("model unavailable")),

    ("every exit code the launcher header declares has a meaning in the fan-out's table (RETIRED codes excepted)",
     lambda: not [code for code, text in re.findall(
         r"(?m)(?:^#\s+|\s·\s)(\d{1,2}) (\S+)",
         open(os.path.join(HERE, "..", "scripts", "lib", "sidecar_common.sh"), encoding="utf-8").read()[:6000])
                  if text != "RETIRED" and int(code) not in sf.LAUNCHER_EXITS]),

    # It appears in a comment explaining WHY it is not read; a comment cannot dispatch anything.
    ("`limits.concurrency` is never read as a process count (it holds 2500/500/null)",
     lambda: not [l for l in open(MOD, encoding="utf-8").read().splitlines()
                  if "limits" in l and not l.lstrip().startswith("#")]),

    # ---- an exit-0 child that reports it could not do the job is blocked coverage, not ok ----
    ("a lens whose output says checked.stoppedAt=blocked is reported BLOCKED and fails the run",
     lambda: _summary_of({"checked": {"stoppedAt": "blocked"}, "findings": [], "gaps": ["No Git or shell tool"]})
             == ("BLOCKED", 1)),
    ("...found in a child's event-array output too",
     lambda: _summary_of([{"type": "system"}, {"type": "result", "structured_output":
                          {"checked": {"stoppedAt": "blocked"}, "findings": []}}]) == ("BLOCKED", 1)),
    ("...found in a -P run's line-delimited output (the shape a streamed child writes)",
     lambda: _summary_of("jsonl") == ("BLOCKED", 1)),
    ("a lens blocked on one sub-part that still delivered findings stays ok (partial, not void)",
     lambda: _summary_of({"checked": {"stoppedAt": "blocked"}, "findings": [{"file": "a.md:1"}],
                          "gaps": ["hook proof not executed"]}) == ("ok", 0)),
    ("a lens that exhausted its leads stays ok",
     lambda: _summary_of({"checked": {"stoppedAt": "exhausted-leads"}, "findings": []}) == ("ok", 0)),
    ("an unreadable output file stays ok (the exit code is all it has)",
     lambda: _summary_of(None) == ("ok", 0)),
]


def _summary_of(structured):
    """summarize() over one exit-0 job whose -o json output wraps `structured` (None = unreadable)."""
    out = os.path.join(TMP, "blocked_probe.out.json")
    with open(out, "w", encoding="utf-8") as fh:
        if structured is None:
            fh.write("{not json")
        elif structured == "jsonl":
            fh.write('{"type":"system","subtype":"init"}\n' + json.dumps(
                {"type": "result", "structured_output": {"checked": {"stoppedAt": "blocked"}, "findings": []}}) + "\n")
        elif isinstance(structured, list):
            json.dump(structured, fh)
        else:
            json.dump({"type": "result", "structured_output": structured}, fh)
    text, code = sf.summarize([("probe", [], out)], {"probe": {"exit": 0, "stdout": out}}, compare=False)
    line = next((ln for ln in text.splitlines() if " probe " in " " + ln + " "), "")
    return (line.split()[0] if line else ""), code


def citer_boundary_cases():
    import contextlib
    import io
    from unittest.mock import patch
    from _citer_boundary_cases import cases, stage_cases
    rows = []
    for blocking in (False, True):
        for label, job, stage, bad in [(l, j, None, b) for l, j, b in cases(TMP)] + stage_cases(TMP):
            err = io.StringIO()
            denied = False
            fields = {k: v for k, v in job.items() if k not in ("label", "promptPath", "kind")}
            spec = dict(JOB, **fields, promptFile=job["promptPath"], kind=job["kind"])
            with patch.object(sf.citer_coverage, "BLOCKING", blocking), contextlib.redirect_stderr(err):
                try:
                    sf.plan_job(spec, DATA, TMP, False, stage=stage) if stage else plan(spec)
                except sf.JobError as exc:
                    denied = True
                    err.write(str(exc))
            text = err.getvalue()
            ok = denied == (bad and blocking) and (not bad or "Phase 2" in text and "lens_briefs.py" in text)
            rows.append((f"Runner 4 {'blocking' if blocking else 'advisory'} {label}", lambda ok=ok: ok))
    return rows


def main():
    failed = 0
    total = 0
    for name, fn in CASES + citer_boundary_cases():
        try:
            ok = bool(fn())
            detail = ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        total += 1
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))

    # ---- the CLI, end to end, spending nothing -----------------------------
    # The real registry with the fixture alias's transport forced available: the live roster may retire it.
    planted_path = _transport_fixture.available_transport(TMP, "opencode")
    cli_env = dict(os.environ, HARNESS_MODEL_REGISTRY=planted_path)
    jobs_path = os.path.join(TMP, "jobs.json")
    with open(jobs_path, "w", encoding="utf-8") as fh:
        json.dump([{"label": "a", "alias": "muse", "promptFile": PROMPT, "kind": "scout"},
                   {"label": "b", "alias": "luna", "promptFile": PROMPT, "kind": "scout", "effort": "max"}], fh)
    r = subprocess.run([sys.executable, MOD, jobs_path, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 0 and "Nothing dispatched" in r.stdout and "opencode_sidecar.sh" in r.stdout
    failed += not ok
    total += 1
    print("%s --dry-run plans both jobs and dispatches neither" % ("ok  " if ok else "FAIL"))

    # RED on the real input: six jobs and no --max-parallel. The width is not a count the caller set, so the
    # plan must not print a count it never chose ("4 at a time" was the retired default).
    six_path = os.path.join(TMP, "six.json")
    with open(six_path, "w", encoding="utf-8") as fh:
        json.dump([{"label": l, "alias": "muse", "promptFile": PROMPT, "kind": "scout"} for l in "abcdef"], fh)
    r = subprocess.run([sys.executable, MOD, six_path, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 0 and "6 job(s)" in r.stdout and " at a time" not in r.stdout
    failed += not ok
    total += 1
    print("%s six jobs with no --max-parallel print no count bound they never set" % ("ok  " if ok else "FAIL"))

    rungs = os.path.join(TMP, "rungs.json")
    with open(rungs, "w", encoding="utf-8") as fh:
        json.dump([{"label": "a", "alias": "muse", "promptFile": PROMPT, "kind": "scout"},
                   {"label": "b", "alias": "luna", "promptFile": PROMPT, "kind": "scout", "effort": "ultra"}], fh)
    # --dry-run, always: planning is where the refusal fires, and on a tool without the refusal a
    # run without it LAUNCHES both billed jobs (the RED run of this very case did, 2026-09-23).
    r = subprocess.run([sys.executable, MOD, rungs, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = (r.returncode == 2 and "not in codex's effort vocabulary" in r.stderr
          and "Nothing dispatched" not in r.stdout)
    failed += not ok
    total += 1
    print("%s an effort outside the real registry's codex vocabulary exits 2 before any launch"
          % ("ok  " if ok else "FAIL"))

    # A fan-out without --compare scrubs an inherited SC_COMPARISON and ignores free-text SC_MEASUREMENT.
    waste = os.path.join(TMP, "waste.json")
    with open(waste, "w", encoding="utf-8") as fh:
        json.dump([{"label": "a", "alias": "sonnet", "promptFile": PROMPT, "kind": "scout", "effort": "max"}], fh)
    r = subprocess.run([sys.executable, MOD, waste, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8",
                       env=dict(cli_env, SC_COMPARISON="1", SC_MEASUREMENT="a long free-text measurement"))
    ok = r.returncode == 2 and "effort.avoid" in r.stderr
    failed += not ok
    total += 1
    print("%s an avoided rung without --compare exits 2 even with SC_COMPARISON and SC_MEASUREMENT inherited"
          % ("ok  " if ok else "FAIL"))

    unsigned = os.path.join(TMP, "unsigned_tools.json")
    with open(unsigned, "w", encoding="utf-8") as fh:
        json.dump([{"label": "a", "alias": "muse", "promptFile": PROMPT, "kind": "scout", "tools": "Read,Grep"}], fh)
    r = subprocess.run([sys.executable, MOD, unsigned, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = (r.returncode == 2 and "Traceback" not in r.stderr and "job a:" in r.stderr
          and "signed delta" in r.stderr and "Nothing dispatched" not in r.stdout)
    failed += not ok
    total += 1
    print("%s an unsigned `tools` list is a per-job refusal (exit 2), never a traceback"
          % ("ok  " if ok else "FAIL"))

    dup = os.path.join(TMP, "dup.json")
    with open(dup, "w", encoding="utf-8") as fh:
        json.dump([{"label": "a", "alias": "muse", "promptFile": PROMPT, "kind": "scout"},
                   {"label": "a", "alias": "luna", "promptFile": PROMPT, "kind": "scout"}], fh)
    r = subprocess.run([sys.executable, MOD, dup, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 2 and "duplicate label" in r.stderr
    failed += not ok
    total += 1
    print("%s duplicate labels are refused -- their records would overwrite each other"
          % ("ok  " if ok else "FAIL"))

    r = subprocess.run([sys.executable, MOD, os.path.join(TMP, "gone.json"), "--dry-run"],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 2
    failed += not ok
    total += 1
    print("%s a missing jobs file exits 2" % ("ok  " if ok else "FAIL"))

    empty = os.path.join(TMP, "empty.json")
    open(empty, "w", encoding="utf-8").write("[]")
    r = subprocess.run([sys.executable, MOD, empty, "--dry-run"],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 2 and "non-empty" in r.stderr
    failed += not ok
    total += 1
    print("%s an empty job list exits 2 rather than reporting a clean 0-job run"
          % ("ok  " if ok else "FAIL"))

    r = subprocess.run([sys.executable, MOD, jobs_path, "--dry-run", "--max-parallel", "0"],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 2
    failed += not ok
    total += 1
    print("%s --max-parallel 0 is refused" % ("ok  " if ok else "FAIL"))

    refused = os.path.join(TMP, "refused.json")
    with open(refused, "w", encoding="utf-8") as fh:
        json.dump([{"label": "clean", "alias": "muse", "promptFile": PROMPT, "kind": "reviewer"},
                   {"label": "gitlens", "alias": "muse", "promptFile": WF_BRIEF, "shape": "review"}], fh)
    r = subprocess.run([sys.executable, MOD, refused, "--dry-run", "--out-dir", TMP],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    ok = r.returncode == 2 and "gitlens" in r.stderr and "Nothing dispatched" not in r.stdout
    failed += not ok
    total += 1
    print("%s --dry-run exits 2 naming a job that carries a retired shape instead of a kind"
          % ("ok  " if ok else "FAIL"))

    # ---- --compare: the frozen-input guard, proven by making it FIRE -------------------------
    # A comparison run's input is a control. If it moves between arms, a later arm reading an
    # applied fix reports the defect ABSENT -- byte-identical to a genuine miss -- and no
    # after-the-fact check can tell them apart (the run record's `harnessSession` sha does not move
    # for uncommitted edits). So the guard must refuse BEFORE spending, and must not over-refuse:
    # a fresh worktree is never globally clean, because an editor rewrites generated files such as `.import` sidecars.
    import subprocess as sp

    repo = tempfile.mkdtemp(prefix="sffreeze_")

    def g(*a, cwd=repo):
        return sp.run(["git", "-C", cwd, *a], capture_output=True, text=True)

    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    os.makedirs(os.path.join(repo, ".claude"), exist_ok=True)
    open(os.path.join(repo, ".claude", "doctrine.md"), "w", encoding="utf-8").write("rule\n")
    # An engine-generated file OUTSIDE the compared input, to prove the guard ignores it.
    open(os.path.join(repo, "asset.png.import"), "w", encoding="utf-8").write("v=1\n")
    g("add", "-A")
    g("commit", "-qm", "base")
    wt = os.path.join(repo, "wt")
    g("worktree", "add", "--detach", "-q", wt, "HEAD")

    def check(ok, msg):
        nonlocal failed, total
        failed += not ok
        total += 1
        print("%s %s" % ("ok  " if ok else "FAIL", msg))

    check(sf.frozen_input_error(None) is not None,
          "no workdir is refused -- an arm with no cwd reads the live tree")
    import contextlib
    import io
    real_template = sf.FOLD_TEMPLATE
    check(os.path.isfile(os.path.join(HERE, "..", "..", real_template)),
          "the default fold brief names a file that exists")
    sf.FOLD_TEMPLATE = os.path.join(TMP, "no_such_fold_brief.md")
    seen = io.StringIO()
    try:
        with contextlib.redirect_stderr(seen):
            code = sf.main([PROMPT, "--review", "--fold-alias", "a", "--fold-effort", "low",
                            "--fold-subject", "x::y::z"])
    finally:
        sf.FOLD_TEMPLATE = real_template
    check(code == 2 and "--fold template is not a file: " + os.path.join(TMP, "no_such_fold_brief.md") in seen.getvalue(),
          "fold flags without --fold read the default fold brief: %r" % seen.getvalue())
    check("does not exist" in (sf.frozen_input_error(os.path.join(repo, "nope")) or ""),
          "a missing workdir is refused")
    check("MAIN checkout" in (sf.frozen_input_error(repo) or ""),
          "the MAIN checkout is refused -- it is what you edit mid-run")
    check(sf.frozen_input_error(wt) is None,
          "a detached linked worktree is accepted: %r" % (sf.frozen_input_error(wt),))

    # Engine churn outside the compared input is NOT a thaw. A guard that fires here would fire on
    # every correct setup, and a guard that always fires gets bypassed rather than obeyed.
    open(os.path.join(wt, "asset.png.import"), "w", encoding="utf-8").write("v=2\n")
    check(sf.frozen_input_error(wt) is None,
          "churn outside FROZEN_PATHS is not a thaw (an editor rewrites .import files on open)")

    # A real thaw INSIDE the compared input must fire, and must name the file.
    open(os.path.join(wt, ".claude", "doctrine.md"), "a", encoding="utf-8").write("edited\n")
    why = sf.frozen_input_error(wt) or ""
    check("not frozen" in why and "doctrine.md" in why,
          "a thaw inside the compared input fires and names the file: %r" % why[:70])

    # A worktree on a BRANCH is not frozen either -- a commit moves it under the arms.
    g("worktree", "remove", "--force", wt)
    g("branch", "-q", "side")
    g("worktree", "add", "-q", wt, "side")
    check("BRANCH" in (sf.frozen_input_error(wt) or ""),
          "a worktree attached to a branch is refused")

    # ---- the MID-RUN half: a write that lands AFTER dispatch ---------------------------------
    # The refusal above runs once, before the first child starts. Arms finish at different times, so
    # a write landing mid-run splits one run into arms that read the old bytes and arms that read
    # the new ones -- while every arm still records the same `harnessSession` sha.
    g("worktree", "remove", "--force", wt)
    g("checkout", "-q", "main")
    g("worktree", "add", "--detach", "-q", wt, "HEAD")

    fp0 = sf.freeze_fingerprint(wt)
    check(fp0 == sf.freeze_fingerprint(wt), "the fingerprint is stable when nothing writes")
    check(sf.freeze_fingerprint(None) is None, "no workdir yields no fingerprint")

    open(os.path.join(wt, "asset2.png.import"), "w", encoding="utf-8").write("v=3\n")
    check(sf.freeze_fingerprint(wt) == fp0,
          "churn outside FROZEN_PATHS does not move the fingerprint")

    open(os.path.join(wt, ".claude", "doctrine.md"), "a", encoding="utf-8").write("mid-run\n")
    check(sf.freeze_fingerprint(wt) != fp0,
          "a write inside the compared input MOVES the fingerprint")

    # A SECOND write to an already-dirty path. `git status --porcelain` names the path and not its
    # bytes, so both edits emit the identical ` M .claude/doctrine.md` -- the fingerprint moved on
    # the first write and then sat still through every later one, which is precisely the window
    # between two arms. Content hashing is what closes it.
    fp_dirty = sf.freeze_fingerprint(wt)
    open(os.path.join(wt, ".claude", "doctrine.md"), "a", encoding="utf-8").write("again\n")
    check(sf.freeze_fingerprint(wt) != fp_dirty,
          "a SECOND write to an already-dirty path still moves the fingerprint")

    check(sf.workdir_of(["x", "-e", "max", "-d", "/w", "-G", "review"]) == "/w",
          "workdir_of reads the -d operand the child actually receives")
    check(sf.workdir_of(["x", "-e", "max"]) is None, "workdir_of returns None with no -d")

    # FROZEN_PATHS covers `.claude` alone, so a prompt or context file anywhere else moves without
    # touching git status at all. Those are named per job and hashed by content.
    brief = os.path.join(repo, "brief.md")
    open(brief, "w", encoding="utf-8").write("v1\n")
    fp_b = sf.freeze_fingerprint(wt, [brief])
    check(fp_b != sf.freeze_fingerprint(wt), "naming an extra input changes what is fingerprinted")
    open(brief, "w", encoding="utf-8").write("v2\n")
    check(sf.freeze_fingerprint(wt, [brief]) != fp_b,
          "editing a prompt file OUTSIDE FROZEN_PATHS moves the fingerprint")
    check(sf.freeze_fingerprint(wt, [os.path.join(repo, "gone.md")])
          != sf.freeze_fingerprint(wt, [brief]),
          "an absent extra input does not collide with a present one")

    check(sf.inputs_of(["x", "-f", "p.md", "-C", "a.md", "-C", "b.md", "-S", "s.json"])
          == ["a.md", "b.md", "p.md", "s.json"],
          "inputs_of collects the prompt, every context file and the schema")
    check(sf.inputs_of(["x", "-P", "run.stream", "-R", "rec.json", "-d", "/w"]) == [],
          "inputs_of ignores -P/-R outputs, which differ per arm by design")

    progress = os.path.join(TMP, "resumed.progress.jsonl")
    final_out = os.path.join(TMP, "resumed.out.json")
    open(progress, "w", encoding="utf-8").write("segment-one\nsegment-two\n")
    open(final_out, "w", encoding="utf-8").write("segment-two\n")
    sf.bound_finished_stream(progress, final_out)
    check(open(progress, encoding="utf-8").read() == "segment-one\nsegment-two\n",
          "G10 a resumed progress stream is kept when stdout has only the last segment")

    duplicate_progress = os.path.join(TMP, "duplicate.progress.jsonl")
    duplicate_out = os.path.join(TMP, "duplicate.out.json")
    open(duplicate_progress, "w", encoding="utf-8").write("same\n")
    open(duplicate_out, "w", encoding="utf-8").write("same\n")
    sf.bound_finished_stream(duplicate_progress, duplicate_out)
    pointer = json.loads(open(duplicate_progress, encoding="utf-8").read())
    check(pointer.get("seeAlso") == os.path.basename(duplicate_out),
          "byte-identical progress still collapses to its archival stdout pointer")

    # Each arm being frozen is NOT the arms sharing one input. Two detached worktrees at different
    # commits each pass the per-arm check, and the run then reads the code difference between them
    # as a difference between models. Fingerprints are what settle it.
    open(os.path.join(repo, ".claude", "doctrine.md"), "a", encoding="utf-8").write("second\n")
    g("add", "-A")
    g("commit", "-qm", "moved")
    wt2 = os.path.join(repo, "wt2")
    g("worktree", "add", "--detach", "-q", wt2, "HEAD")
    check(sf.frozen_input_error(wt2) is None, "a SECOND detached worktree is frozen on its own")
    check(sf.freeze_fingerprint(wt) != sf.freeze_fingerprint(wt2),
          "...but its fingerprint differs, which is what catches two arms on different commits")
    check(sf.freeze_fingerprint(wt) == sf.freeze_fingerprint(wt),
          "...and one worktree fingerprints identically to itself, so the check is not noise")

    # End to end: a child that writes into its own workdir must be REPORTED as having read a moving
    # input. Proven by making it fire -- a guard never seen firing and one that cannot fire emit the
    # same silence.
    g("checkout", "-q", "--", ".claude/doctrine.md", cwd=wt)
    thaw = os.path.join(wt, ".claude", "thaw.md").replace("\\", "/")
    argv = [sys.executable, "-c",
            "open(%r,'w').write('written by the child')" % thaw, "-d", wt]
    original_call = sf.subprocess.call
    seen_inventory = []

    def observe_launch(*args, **kwargs):
        inventories = [os.path.join(TMP, name) for name in os.listdir(TMP)
                       if name.endswith(".inventory.json")]
        newest = max(inventories, key=os.path.getmtime)
        inventory = json.load(open(newest, encoding="utf-8"))
        process = inventory.get("fanoutProcess", "")
        seen_inventory.append(bool(process) and process.endswith("pid=%d" % os.getpid())
                              and (os.name != "nt" or "winpid=%d " % os.getpid() in process))
        return original_call(*args, **kwargs)

    sf.subprocess.call = observe_launch
    try:
        res = sf.run_jobs([("thawer", argv, os.path.join(TMP, "thawer.out.json"))], 1, TMP)
    finally:
        sf.subprocess.call = original_call
    check(seen_inventory == [True], "fanoutProcess is persisted before the first launch")
    check(res["thawer"]["frozenInput"]["held"] is False,
          "an arm that writes into its own workdir is recorded as NOT holding the freeze")

    quiet = [sys.executable, "-c", "pass", "-d", wt]
    res2 = sf.run_jobs([("quiet", quiet, os.path.join(TMP, "quiet.out.json"))], 1, TMP)
    check(res2["quiet"]["frozenInput"]["held"] is True,
          "an arm that writes nothing is recorded as holding it -- the load-bearing negative")

    # A relative out dir or -R must reach the inventory absolute: its readers (the Stop hook's
    # dispatch table, the session watcher) run from another working directory.
    rel_root = tempfile.mkdtemp(prefix="fanout_rel_", dir=TMP)
    here = os.getcwd()
    os.chdir(rel_root)
    try:
        sf.run_jobs([("rel", [sys.executable, "-c", "pass", "-R", "rel.record.json"], "rel.out.json")], 1, ".")
    finally:
        os.chdir(here)
    rel_inventory = [n for n in os.listdir(rel_root) if n.endswith(".inventory.json")]
    rel_paths = [j.get("recordPath") for n in rel_inventory
                 for j in json.load(open(os.path.join(rel_root, n), encoding="utf-8"))["jobs"]]
    check(len(rel_paths) == 1 and os.path.isabs(rel_paths[0])
          and os.path.normcase(rel_paths[0]) == os.path.normcase(os.path.join(rel_root, "rel.record.json")),
          "a relative -R is written to the inventory as an absolute recordPath: %r" % rel_paths)

    # The close reads each job's workdir from the inventory to tell a worktree writer from one in its checkout.
    wd_root = tempfile.mkdtemp(prefix="fanout_wd_", dir=TMP)
    sf.run_jobs([("wd", [sys.executable, "-c", "pass", "-d", wt, "-R", os.path.join(wd_root, "wd.record.json")],
                  os.path.join(wd_root, "wd.out.json")),
                 ("nowd", [sys.executable, "-c", "pass", "-R", os.path.join(wd_root, "nowd.record.json")],
                  os.path.join(wd_root, "nowd.out.json"))], 2, wd_root)
    wd_jobs = {j["label"]: j for n in os.listdir(wd_root) if n.endswith(".inventory.json")
               for j in json.load(open(os.path.join(wd_root, n), encoding="utf-8"))["jobs"]}
    check(os.path.normcase(wd_jobs.get("wd", {}).get("workdir", "")) == os.path.normcase(os.path.abspath(wt))
          and "workdir" not in wd_jobs.get("nowd", {}),
          "the inventory names each job's -d workdir, absolute, and none for a job without one: %r" % wd_jobs)

    # The inventory names each planned job's launcher as a launched job's line does (its script minus `_sidecar.sh`),
    # so the Stop hook can name the plan before the job starts. A job that runs no launcher script names none.
    ln_root = tempfile.mkdtemp(prefix="fanout_ln_", dir=TMP)
    launcher_script = os.path.join(ln_root, "anthropic_secondary_sidecar.sh")
    open(launcher_script, "w", encoding="utf-8").write("pass\n")
    sf.run_jobs([("ln", [sys.executable, launcher_script, "-R", os.path.join(ln_root, "ln.record.json")],
                  os.path.join(ln_root, "ln.out.json")),
                 ("bare", [sys.executable, "-c", "pass", "-R", os.path.join(ln_root, "bare.record.json")],
                  os.path.join(ln_root, "bare.out.json"))], 2, ln_root)
    ln_jobs = {j["label"]: j for n in os.listdir(ln_root) if n.endswith(".inventory.json")
               for j in json.load(open(os.path.join(ln_root, n), encoding="utf-8"))["jobs"]}
    check(ln_jobs.get("ln", {}).get("launcher") == "anthropic_secondary" and "launcher" not in ln_jobs.get("bare", {}),
          "the inventory names each job's launcher, and none for a job that runs no launcher script: %r" % ln_jobs)
    # The paying plan is what the inventory names: a launcher's file stem is not a plan name (here it differs).
    paid = with_rooms({"anthropic-secondary": 1.0}, lambda: plan_on(dict(PLAN_JOB, plan="anthropic-secondary")))
    named = sf._inventory_job(paid[0], paid[1], "paid-launch", os.path.join(ln_root, "paid.record.json"))
    check(named.get("plan") == "anthropic-secondary",
          "the inventory names the plan that pays a job, not its launcher's file stem: %r" % named)

    # ---- one plan line per job, and a pre-start refusal says so on disk -----------------
    # The job build prints each paid pick once. The fold's payer probe re-picks the same plan for
    # every seat; a second print doubles the lines the caller reads for one launch.
    seats = [{"label": "seat-one", "alias": "h-exec", "effort": "high"},
             {"label": "seat-two", "alias": "h-exec", "effort": "high"}]
    fold_args = type("FoldArgs", (), {"fold_alias": "h-exec", "fold_effort": "high", "fold": PROMPT})()
    payer_err = io.StringIO()

    def payer_lines():
        payer_err.truncate(0)
        payer_err.seek(0)
        with contextlib.redirect_stderr(payer_err):
            with_rooms({"anthropic": 5.0, "anthropic-secondary": 4.0},
                       lambda: sf.fold_probe(fold_args, seats, PLAN_DATA))
        return [line for line in payer_err.getvalue().splitlines() if line.startswith("[fanout] ")]

    check(payer_lines() == [], "the fold's payer probe prints no second plan line per seat: %r" % payer_lines())

    # A launch that refuses before any seat writes <out dir>/fanout-<id>.refused.json, the one
    # record every reader uses to drop the entries that call registered.
    refusal_dir = tempfile.mkdtemp(prefix="sfrefusal_", dir=TMP)
    refusal_jobs = os.path.join(refusal_dir, "jobs.json")
    with open(refusal_jobs, "w", encoding="utf-8") as fh:
        json.dump([{"label": "refused-seat", "alias": "muse", "promptFile": PROMPT, "kind": "reviewer",
                    "contextFiles": [CTX]}], fh)
    roster = os.path.join(refusal_dir, "roster.json")
    with open(roster, "w", encoding="utf-8") as fh:
        json.dump([{"key": "seat-one"}], fh)
    blobs = os.path.join(refusal_dir, "blobs.json")
    with open(blobs, "w", encoding="utf-8") as fh:
        json.dump({}, fh)
    refusal_out = os.path.join(refusal_dir, "out")
    r = subprocess.run([sys.executable, MOD, refusal_jobs, "--review", "--dry-run", "--out-dir", refusal_out,
                        "--ledger-stage", "candidate", "--ledger-subject", "session:x",
                        "--ledger-roster", roster, "--ledger-blobs", blobs],
                       capture_output=True, text=True, timeout=60, encoding="utf-8", env=cli_env)
    written = ([name for name in os.listdir(refusal_out)
                if name.startswith("fanout-") and name.endswith(".refused.json")]
               if os.path.isdir(refusal_out) else [])
    refusal_record = json.load(open(os.path.join(refusal_out, written[0]), encoding="utf-8")) if written else {}
    check(r.returncode == 2 and "review launch refused before any seat ran" in r.stderr
          and len(written) == 1 and refusal_record.get("labels") == ["refused-seat"]
          and "no mandates list" in refusal_record.get("refusal", "")
          and isinstance(refusal_record.get("time"), (int, float)),
          "a pre-start refusal writes one fanout-<id>.refused.json naming its labels, text and time: %r"
          % refusal_record)

    intent_out = os.path.join(TMP, "intent-out")
    os.makedirs(intent_out, exist_ok=True)
    intent_jobs = os.path.join(TMP, "intent-jobs.json")
    sf.write_launch_intent(intent_out, "toolu_claimed", "sess-1", ["intent-claimed"], intent_jobs)
    check(sf.claim_launch_intent(intent_out, os.path.join(TMP, "other-jobs.json"), ["intent-claimed"], "x") is None,
          "an intent for another jobs file is never claimed")
    sf._LAUNCH.update(jobs=os.path.abspath(intent_jobs), call=None)
    try:
        sf.run_jobs([("intent-claimed", [sys.executable, "-c", "pass", "-R",
                                         os.path.join(intent_out, "intent-claimed.record.json")],
                      os.path.join(intent_out, "intent-claimed.out.json"))], 1, intent_out)
        intent_rows = [job for name in os.listdir(intent_out) if name.endswith(".inventory.json")
                       for job in json.load(open(os.path.join(intent_out, name), encoding="utf-8")).get("jobs", [])]
        check([job.get("call") for job in intent_rows] == ["toolu_claimed"],
              "the inventory row carries the call whose intent launched it: %r" % intent_rows)
        sf._LAUNCH.update(call=None)
        sf.run_jobs([("intent-bare", [sys.executable, "-c", "pass", "-R",
                                      os.path.join(intent_out, "intent-bare.record.json")],
                      os.path.join(intent_out, "intent-bare.out.json"))], 1, intent_out)
        bare_rows = [job for name in os.listdir(intent_out) if name.endswith(".inventory.json")
                     for job in json.load(open(os.path.join(intent_out, name), encoding="utf-8")).get("jobs", [])
                     if job.get("label") == "intent-bare"]
        check([job.get("call") for job in bare_rows] == [None],
              "a launch with no unclaimed intent records call null: %r" % bare_rows)
    finally:
        sf._LAUNCH.update(jobs=None, call=None)
    check(sf.claim_launch_intent(intent_out, intent_jobs, ["intent-claimed"], "again") is None,
          "a claimed intent is never claimed a second time")

    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
