#!/usr/bin/env python3
"""RED-first proof: a review panel (`sidecar_fanout.py --review`) is placed by the ladder like any fan-out, so a seat
placed on the SESSION's plan runs native through the Workflow call the fan-out prints and the rest run as sidecars;
`--review-close` then consolidates every seat, whichever transport ran it, through the one review tail.

Planted: the session's plan (`CLAUDE_CODE_TRANSPORT` plus a session id), each seat's plan (a named `plan`, so the pick
is not a live capacity read), the seats' own files (a sidecar seat's `.out.json`, `.record.json` and stream; a native
seat's journal result and agent transcript). `_main` runs in-process; the stubbed seams are `detach_driver` and
`run_jobs`, so no process is a launcher and nothing is billed. The real-input observation is `--dry-run` over a
`lens_briefs.py prepare` jobs file, run by hand.

Run: python3 .claude/tests/test_sidecar_fanout_review_split.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shlex
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CLAUDE, "tools"))
sys.path.insert(0, os.path.join(CLAUDE, "hooks"))
spec = importlib.util.spec_from_file_location("sf_review_split", os.path.join(CLAUDE, "tools", "sidecar_fanout.py"))
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)
import review_ledger  # noqa: E402

SESSION, OTHER = "anthropic", "anthropic-secondary"
REPO = str(sf.REPO).replace("\\", "/")
SCRATCH = os.path.join(str(sf.REPO), ".claude", "scratch")
os.makedirs(SCRATCH, exist_ok=True)
ROOT = tempfile.mkdtemp(prefix="rsplit_", dir=SCRATCH)
PROMPT = os.path.join(ROOT, "brief.md")
with open(PROMPT, "w", encoding="utf-8") as fh:
    fh.write("Review the subject.\n")
SCHEMA = os.path.join(CLAUDE, "schemas", "review_findings.json")
CLAUDE_MD = {"path": ".claude/CLAUDE.md",
             "lines": len(open(os.path.join(CLAUDE, "CLAUDE.md"), encoding="utf-8").read().splitlines())}
MANDATE = "plc-instruction-quality"

sf.admission_refusal = lambda *_a, **_k: None
sf.spare_facts = lambda *_a, **_k: []
sf.fit_facts = lambda *_a, **_k: []
sf.launcher_fit_refusal = lambda *_a, **_k: None
sf.doctrine_refusal = lambda *_a, **_k: None
sf.duplicate_launches = lambda *_a, **_k: ""
sf._dispatch_checks_module().native_width = lambda *_a, **_k: {"room": None, "live": [], "pressure": None, "note": None}
sf._pid_alive = lambda pid: pid == 4242

DRIVERS, LAST = [], []


def fake_driver(argv, jobs, out_dir, claim=None, log_path=None, rest_file=None):
    DRIVERS.append((str(rest_file or jobs), list(argv), claim))
    return 0


sf.detach_driver = fake_driver


def seat(label, plan=SESSION, **extra):
    return dict({"label": label, "kind": "reviewer", "alias": "haiku", "effort": "high", "plan": plan,
                 "promptFile": PROMPT, "schemaFile": SCHEMA, "mandates": [MANDATE], "mustRead": [CLAUDE_MD],
                 "contextFiles": [os.path.join(CLAUDE, "CLAUDE.md")], "workdir": REPO}, **extra)


def reply(description):
    return {"checked": {"toolsUsed": ["Read:.claude/CLAUDE.md"], "stoppedAt": "exhausted-leads",
                        "basis": MANDATE + ": findings"},
            "findings": [{"agent": MANDATE, "mandate": MANDATE, "action": "FIX", "category": "bug",
                          "critical": False, "file": ".claude/plans/p.md:3", "description": description,
                          "rationale": "matched source",
                          "evidence": [{"file": ".claude/CLAUDE.md:1", "quote": "@CLAUDE.core.md"}]}],
            "gaps": []}


def transcript(path, reads):
    events = []
    for n, (file_path, limit) in enumerate(reads):
        given = {"file_path": file_path}
        if limit:
            given["limit"] = limit
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "c%d" % n, "name": "Read", "input": given}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "c%d" % n, "content": "x", "is_error": False}]}})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(event) + "\n" for event in events))
    return path


WHOLE_READ = [(".claude/CLAUDE.md", None)]


def plant_sidecar_seat(out, label, description, reads=WHOLE_READ):
    out_path = os.path.join(out, label + ".out.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "result", "is_error": False, "structured_output": reply(description)}) + "\n")
    transcript(os.path.join(out, label + ".stream"), reads)
    with open(os.path.join(out, label + ".record.json"), "w", encoding="utf-8") as fh:
        json.dump({"exitCode": 0, "schemaValid": True, "engagement": "proven"}, fh)


def plant_workflow(directory, seats):
    """One `dispatch.js` run dir: `seats` is {label: (agent id, result or None, reads)}."""
    os.makedirs(directory, exist_ok=True)
    rows = []
    for label, (agent, result, reads) in seats.items():
        rows.append({"type": "started", "agentId": agent, "label": label})
        if result is not None:
            rows.append({"type": "result", "agentId": agent, "result": result})
        transcript(os.path.join(directory, "agent-%s.jsonl" % agent), reads)
        with open(os.path.join(directory, "agent-%s.meta.json" % agent), "w", encoding="utf-8") as fh:
            json.dump({"model": "claude-haiku-planted"}, fh)
    with open(os.path.join(directory, "journal.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(row) + "\n" for row in rows))
    return directory


def main_in_process(argv, env=None):
    keep = {k: os.environ.get(k) for k in ("CLAUDE_CODE_TRANSPORT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SIDECAR",
                                           "CLAUDE_CONFIG_DIR", "SIDECAR_REVIEW_CLAIM")}
    base = {"CLAUDE_CODE_TRANSPORT": SESSION, "CLAUDE_CODE_SESSION_ID": "sess-proof", "CLAUDE_CODE_SIDECAR": None,
            "CLAUDE_CONFIG_DIR": None, "SIDECAR_REVIEW_CLAIM": None}
    base.update(env or {})
    for key, value in base.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    DRIVERS.clear()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = sf._main(argv)
    except SystemExit as exc:
        code = exc.code
    finally:
        for key, value in keep.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    LAST[:] = [buf.getvalue(), [d[0] for d in DRIVERS]]
    return code, buf.getvalue()


def write_jobs(seats):
    path = os.path.join(tempfile.mkdtemp(dir=ROOT, prefix="in_"), "jobs.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(seats, fh)
    return path


def launch(seats, *flags):
    jobs = write_jobs(seats)
    out = tempfile.mkdtemp(dir=ROOT, prefix="out_")
    code, text = main_in_process([jobs, "--out-dir", out, "--review", *flags, "--detach"])
    return code, text, out, jobs


def close(jobs, out, *flags, workflow=()):
    argv = [jobs, "--out-dir", out, "--review-close", *flags]
    for directory in workflow:
        argv += ["--workflow-dir", directory]
    return main_in_process(argv)


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
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - the proof reports every case
        ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
    if not ok and not detail:
        detail = "  last run: " + repr(LAST)[:900]
    CASES.append((name, ok, detail))


# ---- placement: a review panel splits like any fan-out ---------------------------------------------------------
parser = sf.build_parser()
case("--review and its fold flags no longer keep the whole run a sidecar",
     lambda: all(sf.split_enabled(parser.parse_args(["j.json", *flags]))[0] for flags in (
         ["--review"], ["--review", "--fold-alias", "a", "--fold-effort", "e", "--fold-subject", "r::m::f"])))
case("--refold and --review-close place no seat",
     lambda: all(not sf.split_enabled(parser.parse_args(["j.json", *flags]))[0] for flags in (
         ["--out-dir", "x", "--refold"], ["--review-close"])))

code, text, out, jobs = launch([seat("native-a"), seat("side-b", plan=OTHER)])
nat = read(out, "native.args.json")
rest = read(out, "sidecar.jobs.json")
case("a session-plan review seat is native and the other stays a sidecar",
     lambda: code == 0 and [j["label"] for j in nat["jobs"]] == ["native-a"]
     and [j["label"] for j in rest["jobs"]] == ["side-b"])
case("the native seat carries the reviewer kind, its schema object and its context file",
     lambda: nat["jobs"][0]["kind"] == "reviewer" and nat["jobs"][0]["schema"] == json.load(open(SCHEMA, encoding="utf-8"))
     and nat["jobs"][0]["contextPaths"] and "mandates" not in nat["jobs"][0])
case("the sidecar driver runs the remainder with --review, and the panel is not consolidated yet",
     lambda: len(DRIVERS) == 1 and DRIVERS[0][0].endswith("sidecar.jobs.json") and "--review" in DRIVERS[0][1]
     and read(out, "review.json") is None)
then = next((l for l in text.splitlines() if "--review-close" in l), "")
case("the fan-out prints the exact close call, with the original jobs file and one --workflow-dir slot",
     lambda: "NATIVE 1 on %s: Workflow(" % SESSION in text and "sidecar_fanout.py" in then and "--workflow-dir" in then
     and os.path.basename(jobs) in then and "--review-close" in then and "--detach" not in then)
case("the close call is a valid fan-out call",
     lambda: sf.build_parser().parse_args(shlex.split(then[then.index("python3"):])[2:]
                                          + ["--workflow-dir", "x"]).review_close)

code, text, out, jobs = launch([seat("n1"), seat("n2")])
case("an all-native panel starts no driver and prints the close call",
     lambda: code == 0 and DRIVERS == [] and len(read(out, "native.args.json")["jobs"]) == 2
     and "--review-close" in text and read(out, "sidecar.jobs.json") is None)

# ---- the remainder driver runs seats and leaves consolidation to --review-close ---------------------------------
code, text, out, jobs = launch([seat("n3"), seat("s3", plan=OTHER)])
claim = open(os.path.join(out, sf.REVIEW_CLAIM), encoding="utf-8").read()
sf.run_jobs = lambda planned, *a, **k: {label: {"exit": 0, "stdout": out_path, "record": None, "stream": None}
                                        for label, _argv, out_path in planned}
code, text = main_in_process([os.path.join(out, "sidecar.jobs.json"), "--out-dir", out, "--review"],
                             env={"SIDECAR_REVIEW_CLAIM": claim})
case("the remainder driver writes no review.json and names the close call",
     lambda: read(out, "review.json") is None and read(out, "post_check.json") is None and "--review-close" in text)

# ---- close: one consolidation over native and sidecar seats ------------------------------------------------------
JOBS2 = [seat("n4"), seat("s4", plan=OTHER)]


def mixed_close(reads_native=WHOLE_READ, native_result=None, workflow=True):
    jobs = write_jobs(JOBS2)
    out = tempfile.mkdtemp(dir=ROOT, prefix="close_")
    plant_sidecar_seat(out, "s4", "finding from s4")
    wf = plant_workflow(tempfile.mkdtemp(dir=ROOT, prefix="wf_"),
                        {"n4": ("ag1", reply("finding from n4") if native_result is None else native_result,
                                reads_native)})
    code, text = close(jobs, out, workflow=[wf] if workflow else [])
    return code, text, out, jobs, wf


code, text, out, jobs, wf = mixed_close()
review = read(out, "review.json") or {}
cover = {c["label"]: c for c in review.get("coverage", [])}
case("--review-close consolidates both transports into one complete review.json",
     lambda: code == 0 and review.get("status") == "complete" and sorted(cover) == ["n4", "s4"]
     and all(c["status"] == "completed" for c in cover.values()) and review["counts"]["seats"] == 2)
case("the native seat's must-read coverage is observed from its agent transcript",
     lambda: cover["n4"]["mustReadEvidence"] == "observed" and cover["s4"]["mustReadEvidence"] == "observed")
case("every seat's finding is a source and the quote check ran over both",
     lambda: len(review["sources"]) == 2 and review["verification"]["counts"]["VERIFIED"] == 2)
case("post_check.json and claims.json are written by the same tail",
     lambda: read(out, "post_check.json") is not None and read(out, "claims.json") is not None
     and "post-check:" in text)

claims = read(out, "claims.json") or {}
case("--review-close leaves the close's claims and verdict in each --workflow-dir it landed, for the Stop audit",
     lambda: claims and read(wf, "review_close.json") == claims)

# parity: the same seats run all as sidecars give the same sources, counts and seat statuses
out_s = tempfile.mkdtemp(dir=ROOT, prefix="allside_")
jobs_s = write_jobs([seat("n4", plan=OTHER), seat("s4", plan=OTHER)])
plant_sidecar_seat(out_s, "n4", "finding from n4")
plant_sidecar_seat(out_s, "s4", "finding from s4")
code_s, text_s = close(jobs_s, out_s)
review_s = read(out_s, "review.json") or {}
case("a native-landed seat consolidates exactly like a sidecar seat (sources, counts, statuses)",
     lambda: code_s == 0 and [s["finding"] for s in review_s["sources"]] == [s["finding"] for s in review["sources"]]
     and review_s["counts"] == review["counts"]
     and [c["status"] for c in review_s["coverage"]] == [c["status"] for c in review["coverage"]])

# observed reads decide a native seat's coverage, as they decide a sidecar seat's
code, text, out, jobs, wf = mixed_close(reads_native=[(".claude/CLAUDE.md", 1)])
review = read(out, "review.json") or {}
cover = {c["label"]: c for c in review.get("coverage", [])}
case("a native seat that read only part of its must-read list fails, naming the unread entry",
     lambda: cover["n4"]["status"] == "failed" and cover["n4"]["unread"] and review["status"] == "partial"
     and code == 1)

# a native seat whose agent returned no result, and a seat that left nothing at all
jobs_n = write_jobs(JOBS2)
out_n = tempfile.mkdtemp(dir=ROOT, prefix="noresult_")
plant_sidecar_seat(out_n, "s4", "finding from s4")
wf_n = plant_workflow(tempfile.mkdtemp(dir=ROOT, prefix="wf_"), {"n4": ("ag9", None, WHOLE_READ)})
code_n, text_n = close(jobs_n, out_n, workflow=[wf_n])
review_n = read(out_n, "review.json") or {}
cover_n = {c["label"]: c for c in review_n.get("coverage", [])}
case("a native seat that returned no result is uncovered, and the panel is partial, never clean",
     lambda: code_n == 1 and cover_n["n4"]["status"] == "uncovered" and cover_n["s4"]["status"] == "completed"
     and review_n["status"] == "partial")
out_z = tempfile.mkdtemp(dir=ROOT, prefix="none_")
code_z, text_z = main_in_process([jobs_n, "--out-dir", out_z, "--review-close"])
case("a seat that left no file anywhere is named as never run",
     lambda: code_z == 1 and "never ran" in text_z and "n4" in text_z and "s4" in text_z)

# a sidecar seat still running refuses the close
out_r = tempfile.mkdtemp(dir=ROOT, prefix="running_")
jobs_r = write_jobs([seat("s5", plan=OTHER)])
with open(os.path.join(out_r, "s5.record.json.pid"), "w", encoding="utf-8") as fh:
    fh.write("pid=4242\n")
code, text = close(jobs_r, out_r)
case("--review-close refuses while a sidecar seat is still running, and consolidates nothing",
     lambda: code == 2 and "s5" in text and "running" in text and read(out_r, "review.json") is None)

# a second close never overwrites the first
code, text = close(jobs, os.path.dirname(os.path.join(out, "x")), workflow=[wf])
case("a second --review-close over a consolidated out dir is refused",
     lambda: code == 2 and "already exist" in text)

# --review-close flag hygiene
code, text = main_in_process([jobs, "--out-dir", out, "--workflow-dir", wf])
case("--workflow-dir without --review-close is refused", lambda: code == 2 and "--review-close" in text)

# ledger roster: the close records the same rows a sidecar-only run records
CODING_CATALOG = os.path.join(CLAUDE, "skills", "project_subsystems", "adaptation.coding.defaults.json")
if os.path.exists(CODING_CATALOG):
    roster_ok = MANDATE in review_ledger.load_catalog()
    case("the mandate the proof plants is a catalog mandate", lambda: roster_ok)
else:
    print("SKIP the mandate the proof plants is a catalog mandate: the coding layer owns the review mandates "
          "and this profile does not install it")

failed = [c for c in CASES if not c[1]]
for name, ok, detail in CASES:
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name, "" if ok else "\n" + detail))
print("\n%d failed" % len(failed) if failed else "\nall pass")
sys.exit(1 if failed else 0)
