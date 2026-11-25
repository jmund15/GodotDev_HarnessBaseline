#!/usr/bin/env python3
"""Proof for `explore_result.py`: the one place an explore fan-out's lens claims are stamped, consolidated and checked.

The golden is a recorded `explore-fanout` Workflow run (tests/fixtures/workflow_replay): its journal holds the four
lens results the engine consolidated, its record the result it returned. Consolidation over those same lens
results must reproduce that record, so the move out of the Workflow engine changed no rule. Transport parity:
the same lens results delivered as a sidecar's `<label>.md` and as a Workflow journal give one result.

    python3 .claude/tests/test_explore_result.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOOLS = REPO / ".claude" / "tools"
sys.path.insert(0, str(TOOLS))
import explore_result  # noqa: E402

RUN = HERE / "fixtures" / "workflow_replay" / "session"
RUN_ID = "wf_7c3b636b-5e2"
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append("%s :: %s" % (label, str(detail)[:500]))


def recorded():
    record = json.loads((RUN / "workflows" / (RUN_ID + ".json")).read_text(encoding="utf-8"))
    journal = RUN / "subagents" / "workflows" / RUN_ID / "journal.jsonl"
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    labels = {e["agentId"]: e["label"] for e in events if e.get("type") == "started" and e.get("phase") == "Explore"}
    lenses = [(label.split(":", 1)[1], next(e["result"] for e in events
                                            if e.get("type") == "result" and e["agentId"] == agent))
              for agent, label in labels.items()]
    return record["result"], lenses


def nothing_checked(rows):
    return {"status": "unavailable", "counts": {"VERIFIED": 0, "UNVERIFIED": 0, "UNCHECKED": len(rows)},
            "verdicts": {row["id"]: "UNCHECKED" for row in rows}, "note": "planted"}


def lens_object(claims=(), *, tools=("Grep:x",), stopped="exhausted-leads", basis="searched", gaps=()):
    return {"claims": list(claims), "checked": {"toolsUsed": list(tools), "stoppedAt": stopped, "basis": basis},
            "gaps": list(gaps)}


def claim(subject, polarity="exists", bearing="constraint", **extra):
    base = {"subject": subject, "polarity": polarity, "claim": subject + " is so", "bearing": bearing,
            "confidence": "unverified", "evidence": [{"file": "a.md", "quote": "so"}]}
    base.update(extra)
    return base


def golden():
    expected, lenses = recorded()
    got = explore_result.consolidate(lenses, check=nothing_checked)
    for name in ("claims", "contradictions", "flags", "gaps", "counts", "perLens"):
        check("golden: %s match the recorded engine result" % name, got.get(name) == expected.get(name),
              json.dumps(got.get(name))[:300] + " != " + json.dumps(expected.get(name))[:300])
    check("golden: an unavailable check leaves every claim UNCHECKED, as the recorded run did",
          got["verification"]["status"] == "unavailable"
          and got["verification"]["counts"] == expected["verification"]["counts"], got["verification"])


def coverage_flags():
    results = [("none", None), ("bad", {"claims": "x"}), ("idle", lens_object(tools=(), stopped="exhausted-leads")),
               ("trig", lens_object(stopped="trigger-not-met", basis="no precondition", tools=())),
               ("quiet", lens_object()), ("blocked", lens_object([claim("s")], stopped="blocked"))]
    got = explore_result.consolidate(results, check=nothing_checked)
    kinds = {(f["kind"], f.get("lens")) for f in got["flags"]}
    for want in (("lens-no-return", "none"), ("lens-invalid-shape", "bad"), ("lens-did-not-run", "idle"),
                 ("lens-trigger-not-met", "trig"), ("lens-empty-verified", "quiet"), ("lens-blocked", "blocked")):
        check("flag %s for %s" % want, want in kinds, sorted(kinds))
    status = {row["key"]: row["status"] for row in got["perLens"]}
    check("a lens with no return is failed, a did-not-run lens uncovered, a quiet one completed",
          status["none"] == "failed" and status["idle"] == "uncovered" and status["quiet"] == "completed"
          and got["counts"]["uncoveredLenses"] == 3, status)
    check("coverage names requested, failed and uncovered lenses",
          got["coverage"]["requested"] == 6 and got["coverage"]["failed"] == 1 and got["coverage"]["uncovered"] == 2
          and got["status"] == "uncovered", got["coverage"])


def claim_rules():
    results = [("a", lens_object([claim("Thing", confidence="verified", evidence=None),
                                   claim("gone", "absent", confidence="verified", evidence=[{"file": "x", "quote": "y"}]),
                                   {"subject": "", "polarity": "exists"}])),
               ("b", lens_object([claim("thing", "absent", evidence=[{"command": "git grep -n q", "output": ""}]),
                                   claim("Other", "exists", confidence="unverified")])),
               ("c", lens_object([claim("other", "exists")]))]
    got = explore_result.consolidate(results, check=nothing_checked)
    by_id = {c["id"]: c for c in got["claims"]}
    kinds = [f["kind"] for f in got["flags"]]
    check("verified with no evidence is downgraded and flagged",
          "downgraded-no-evidence" in kinds and by_id["C1"]["confidence"] == "unverified", kinds)
    check("an absence claimed verified with no proving command is downgraded and flagged",
          "downgraded-unproven-absence" in kinds, kinds)
    check("a malformed claim is skipped, keeps its id, and does not shift a neighbour's",
          "lens-invalid-entry" in kinds and "C3" not in by_id and "C4" in by_id, sorted(by_id))
    check("lenses that disagree on a subject (case-folded) are a contradiction and both claims are contested",
          len(got["contradictions"]) == 1 and by_id["C1"]["contested"] and by_id["C4"]["contested"],
          got["contradictions"])
    check("lenses that agree corroborate each other, nothing is merged",
          by_id["C5"].get("corroboratedBy") == ["c"] and by_id["C6"].get("corroboratedBy") == ["b"], by_id)
    check("contested claims rank first", got["claims"][0].get("contested") is True, got["claims"][0])


def merged_seat_lens_keys():
    seat = "exp-prior-art+exp-integration-surface"
    results = [(seat, lens_object([claim("owned", lens="exp-prior-art"),
                                   claim("consumer", lens="exp-integration-surface"),
                                   claim("untagged")]))]
    got = explore_result.consolidate(results, check=nothing_checked)
    by_subject = {c["subject"]: c["lens"] for c in got["claims"]}
    check("a job carrying several lenses stamps each claim with the lens it names",
          by_subject == {"owned": "exp-prior-art", "consumer": "exp-integration-surface", "untagged": seat},
          by_subject)
    stray = [("a+b", lens_object([claim("elsewhere", lens="c")]))]
    got = explore_result.consolidate(stray, check=nothing_checked)
    check("a claim naming a lens its seat does not carry is credited to the seat",
          [c["lens"] for c in got["claims"]] == ["a+b"], [c["lens"] for c in got["claims"]])
    agreeing = [("a+b", lens_object([claim("Thing", "exists", lens="a"), claim("thing", "exists", lens="b")]))]
    got = explore_result.consolidate(agreeing, check=nothing_checked)
    check("two lenses inside one job corroborate each other like two jobs would",
          [c.get("corroboratedBy") for c in got["claims"]] == [["b"], ["a"]],
          [c.get("corroboratedBy") for c in got["claims"]])


def verification_is_the_evidence_checker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "note.md").write_text("the rule says so\n", encoding="utf-8")
        results = [("a", lens_object([
            claim("held", evidence=[{"file": "note.md", "quote": "the rule says so"}], confidence="verified"),
            claim("fake", evidence=[{"file": "note.md", "quote": "never written"}], confidence="verified")]))]
        got = explore_result.consolidate(results, root=root)
        verdicts = {c["subject"]: (c["verdict"], c["confidence"]) for c in got["claims"]}
        check("the real checker verifies a quoted line and rejects an invented one",
              verdicts == {"held": ("VERIFIED", "verified"), "fake": ("UNVERIFIED", "unverified")}, verdicts)
        check("the verification names status, counts and the non-verified ids",
              got["verification"]["status"] == "fail" and got["verification"]["nonVerified"] == ["a#1"]
              and got["counts"]["verified"] == 1, got["verification"])


def deliver_as_sidecar(directory, lenses):
    for key, obj in lenses:
        if obj is not None:
            (directory / ("explore-%s.md" % key)).write_text(json.dumps(obj, indent=2), encoding="utf-8")


def deliver_as_workflow(run_dir, lenses):
    run_dir.mkdir(parents=True)
    rows = [{"type": "launched"}]
    for number, (key, obj) in enumerate(lenses):
        rows.append({"type": "started", "key": "k%d" % number, "agentId": "a%d" % number,
                     "label": "explore-%s" % key, "phase": "Explore"})
        if obj is not None:
            rows.append({"type": "result", "key": "k%d" % number, "agentId": "a%d" % number, "result": obj})
    (run_dir / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def transport_parity():
    _, lenses = recorded()
    jobs = [{"label": "explore-%s" % key, "seat": key} for key, _ in lenses]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        native, sidecar, mixed = tmp / "native", tmp / "sidecar", tmp / "mixed"
        for d in (native, sidecar, mixed):
            d.mkdir()
        deliver_as_sidecar(sidecar, lenses)
        deliver_as_workflow(tmp / "wf", lenses)
        deliver_as_sidecar(mixed, lenses[:2])
        deliver_as_workflow(tmp / "wf2", lenses[2:])
        with contextlib.redirect_stdout(io.StringIO()):
            a = explore_result.collect(jobs, sidecar, check=nothing_checked)
            b = explore_result.collect(jobs, native, workflow_dirs=[tmp / "wf"], check=nothing_checked)
            c = explore_result.collect(jobs, mixed, workflow_dirs=[tmp / "wf2"], check=nothing_checked)
        check("a sidecar-only run, a Workflow-only run and a mixed run give one result", a == b == c,
              [json.dumps(x["counts"]) for x in (a, b, c)])
        check("the result is the recorded engine's, not a lens-count artifact",
              a["counts"]["claims"] == 86 and a["counts"]["contradictions"] == 1, a["counts"])
        missing = explore_result.collect(jobs, tmp / "empty", check=nothing_checked)
        check("a lens with no delivered file is a failed lens, never a clear one",
              missing["counts"]["uncoveredLenses"] == 4 and missing["status"] == "failed", missing["coverage"])


def claims_contract_channel():
    text = explore_result.claims_contract()
    check("the contract's return channel is one json fence, not a prose-free object",
          "```json" in text and "return ONLY the JSON object" not in text, text[-500:])
    check("the contract cites _is_claim and _is_checked as the object's one home",
          "_is_claim" in text and "_is_checked" in text, text[:600])
    check("the contract no longer claims a schema validates shape", "the schema validates shape" not in text, text[:300])
    check("the contract names the per-claim lens key a multi-lens seat sets", "`lens`" in text, text[:800])
    for name, values in (("stoppedAt", explore_result.STOPPED), ("polarity", explore_result.POLARITIES),
                         ("bearing", explore_result.BEARINGS)):
        check(f"the contract names every {name} value _is_checked/_is_claim accepts",
              all(f"`{v}`" in text for v in values), [v for v in values if f"`{v}`" not in text])


def deliverable_parsing():
    obj = lens_object([claim("s")])
    prose = "The lens swept three targets.\n\n```json\n" + json.dumps(obj, indent=2) + "\n```\n"
    check("a deliverable of prose plus one json fence delivers its claims",
          explore_result.parse_lens_object(prose) == obj, explore_result.parse_lens_object(prose))
    malformed = "prose only\n\n```json\n{\"claims\": \n```\n"
    check("a fence that does not parse is no claims object", explore_result.parse_lens_object(malformed) is None,
          explore_result.parse_lens_object(malformed))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "explore-a.md").write_text(prose, encoding="utf-8")
        (tmp / "explore-b.md").write_text(malformed, encoding="utf-8")
        got = explore_result.collect([{"label": "explore-a", "seat": "a"}, {"label": "explore-b", "seat": "b"}], tmp,
                                     check=nothing_checked)
        status = {row["key"]: row["status"] for row in got["perLens"]}
        check("a fenced deliverable completes its lens and a malformed fence fails it",
              status == {"a": "completed", "b": "failed"} and got["counts"]["claims"] == 1, status)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        run, out = tmp / "wf", tmp / "out"
        run.mkdir()
        out.mkdir()
        rows = [{"type": "started", "key": "k0", "agentId": "a0", "label": "explore-a", "phase": "Explore"},
                {"type": "result", "key": "k0", "agentId": "a0", "result": prose},
                {"type": "started", "key": "k1", "agentId": "a1", "label": "explore-b", "phase": "Explore"},
                {"type": "result", "key": "k1", "agentId": "a1", "result": ""}]
        (run / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            got = explore_result.collect([{"label": "explore-a", "seat": "a"}, {"label": "explore-b", "seat": "b"}],
                                         out, workflow_dirs=[run], check=nothing_checked)
        status = {row["key"]: row["status"] for row in got["perLens"]}
        check("a native lens's final message lands as its deliverable, and an empty one fails it",
              status == {"a": "completed", "b": "failed"} and got["counts"]["claims"] == 1, status)


def command_line():
    _, lenses = recorded()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out = tmp / "fanout"
        out.mkdir()
        deliver_as_sidecar(out, lenses[:3])
        jobs = tmp / "jobs_explore.json"
        jobs.write_text(json.dumps({"spillDir": str(out), "jobs": [
            {"label": "explore-%s" % key, "seat": key} for key, _ in lenses]}), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(TOOLS / "explore_result.py"), str(jobs)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120, env=dict(os.environ))
        saved = tmp / "explore_result.json"
        check("the command writes the full result beside the jobs file", saved.is_file(), proc.stderr[-300:])
        body = json.loads(saved.read_text(encoding="utf-8")) if saved.is_file() else {}
        check("a lens that delivered nothing makes the exit 1 and is named UNCOVERED",
              proc.returncode == 1 and "UNCOVERED" in proc.stdout and lenses[3][0] in proc.stdout,
              (proc.returncode, proc.stdout[-400:]))
        check("the printed summary carries the counts and the result path",
              "claims" in proc.stdout and str(saved).replace("\\", "/") in proc.stdout.replace("\\", "/"),
              proc.stdout[-400:])
        check("the saved result holds every claim", len(body.get("claims", [])) > 0 and body.get("coverage"), list(body))
        proc = subprocess.run([sys.executable, str(TOOLS / "explore_result.py"), str(tmp / "absent.json")],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        check("an unreadable jobs file exits 2", proc.returncode == 2, (proc.returncode, proc.stderr[-300:]))


def main():
    for case in (golden, coverage_flags, claim_rules, merged_seat_lens_keys, verification_is_the_evidence_checker,
                 claims_contract_channel, deliverable_parsing, transport_parity, command_line):
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
