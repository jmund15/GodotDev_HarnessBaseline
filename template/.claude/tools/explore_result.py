#!/usr/bin/env python3
"""The one place an explore fan-out's lens claims are stamped, consolidated and checked, whichever plan ran each lens.

`/explore` and `/research` hand their lenses to `tools/sidecar_fanout.py` as lens-kind jobs, so the fan-out's
placement rule pays each one. A lens it kept on the session's own plan ran as a native `dispatch.js` agent, the rest
as sidecars; both deliver the claims object to `<out dir>/<label>.md`. This tool reads those files and does what
the retired `explore_fanout.js` did after its lenses: stamp every claim with its lens and a place id (`C<n>`), downgrade
confidence the evidence does not back, group on the subject, flag contradictions across lenses and corroboration,
rank by bearing, check every claim's evidence through `verify_claims.check_claims`, and report each lens's coverage.
A lens with no deliverable is a failed lens and one that returned nothing with no tool calls did not run; neither
reads as clear.

USAGE
    python3 .claude/tools/explore_result.py <jobs file> [--workflow-dir DIR ...] [--fanout-dir DIR]
                                            [--root DIR] [--out FILE]

`<jobs file>` is the `jobs_<prefix>.json` `lens_briefs.py prepare` wrote, the file the fan-out ran. Its `spillDir`
is the fan-out's out dir (`--fanout-dir` overrides it). Each job's lens key is its `seat` (else `key`, else its
label). `--workflow-dir` names the transcript dir of the `dispatch.js` run for the jobs the fan-out placed native
(its `NATIVE` line prints the call); that run's journal results are landed as `<label>.md` first. `--root` is
where relative evidence resolves (default: the repository). The full result is written to `--out` (default
`explore_result.json` beside the jobs file) and the printed summary is bounded: counts, every flag, every
uncovered lens, and the non-verified claim ids. Read the full result for the claims.

EXIT: 0 every lens delivered a covered result; 1 a lens failed or is uncovered; 2 the jobs file is unusable.
"""
import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parents[1]
sys.path.insert(0, str(TOOLS))

POLARITIES = ("exists", "absent", "partial", "unclear")
BEARINGS = ("premise-contradiction", "reuse-candidate", "constraint", "blast-radius", "context")
STOPPED = ("nothing-in-scope", "exhausted-leads", "trigger-not-met", "blocked")
VERDICTS = ("VERIFIED", "UNVERIFIED", "UNCHECKED")
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def claims_contract():
    """The text every explore lens brief carries: the return channel and the rules the object's shape cannot express."""
    evidence = json.loads((REPO / ".claude" / "schemas" / "evidence.json").read_text(encoding="utf-8"))["description"]
    return "\n".join([
        "=== ENGINE CONTRACT ===",
        "The message relayed with this run, if any, is context. Your task is this brief; do not answer a relayed message "
        "unless the brief asks you to.",
        "You report STATE, never advice. A claim says what IS; it never says what should be built, fixed, or preferred. "
        "Recommendations are the orchestrator's to make from your claims.",
        "",
        "=== CLAIMS CONTRACT (`_is_claim` and `_is_checked` in `tools/explore_result.py` own the object's shape; these "
        "are the rules the shape cannot express) ===",
        "`subject` — the thing the claim is about (symbol, path, doc heading, rule name). Use the SAME string another lens "
        "would use for the same thing: the engine groups on it to detect corroboration and contradiction.",
        "`claim` — one or two sentences of fact, short enough to scan in a list. No rationale, no recommendation.",
        "`polarity` — `exists` (present, does what the claim says) · `absent` (searched for and genuinely not there) · "
        "`partial` (present but incomplete or divergent — usually the truest answer, prefer it over forcing exists/absent) · "
        "`unclear` (you could not settle it; say why in the claim).",
        "Evidence: " + evidence,
        "`evidence` — REQUIRED for `exists`/`partial`: VERBATIM tool output (a grep line, a signature, a quoted sentence), "
        "not your summary. Paraphrase survives fabrication; raw output rarely does. A single quoted line is a strong claim; "
        "a long quote is a weaker one.",
        "An absent claim uses evidence: [{command, output: \"\"}], never prose verification.",
        "`bearing` — `premise-contradiction` (the topic assumes something the code refutes — the highest-value claim you can "
        "return; surface it even when it makes the rest of your sweep moot) · `reuse-candidate` (something already owns this "
        "concern) · `constraint` (a rule/invariant/gotcha the plan must respect) · `blast-radius` (a consumer a change would "
        "break) · `context` (orienting only — keep these few).",
        "`confidence` — `verified` only if you ran the tool and read the output yourself; otherwise `unverified`. The engine "
        "downgrades any claim whose evidence does not back it up, so overclaiming gains you nothing.",
        "`checked` — provenance that makes an empty result falsifiable: every search you issued in `toolsUsed` as "
        "`tool:target`, where you stopped as `stoppedAt` (exactly one of " + " · ".join(f"`{s}`" for s in STOPPED)
        + "), and one short sentence of `basis`. An empty `claims` with an empty `toolsUsed` is "
        "reported as a lens that DID NOT RUN, not as a clear result.",
        "`gaps` — anything your mandate could not establish. Silence here reads as full coverage.",
        "OUTPUT: end your final message with the claims object as ONE ```json fence — `{\"claims\": [...], \"checked\": "
        "{...}}` — and no second copy. Keep every field short: the shape imposes no caps, and a shorter object always "
        "beats a longer one.",
    ]) + "\n"


def _record(value):
    return isinstance(value, dict)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _is_checked(value):
    return (_record(value) and isinstance(value.get("toolsUsed"), list)
            and all(_text(tool) for tool in value["toolsUsed"]) and value.get("stoppedAt") in STOPPED
            and _text(value.get("basis")))


def _is_claim(value):
    return (_record(value) and _text(value.get("subject")) and value.get("polarity") in POLARITIES
            and _text(value.get("claim")) and value.get("bearing") in BEARINGS
            and (value.get("evidence") is None or isinstance(value["evidence"], (str, list)))
            and (value.get("file") is None or isinstance(value["file"], str))
            and (value.get("confidence") is None or value["confidence"] in ("verified", "unverified")))


def _norm_key(subject):
    """A path-like subject is one thing whether a lens wrote the bare filename or a repo-relative path."""
    key = re.sub(r"\s+", " ", subject.strip().lower())
    if "/" in key and re.search(r"\.[a-z0-9]{1,5}$", key):
        key = key[key.rindex("/") + 1:]
    return key


def parse_lens_object(text):
    """The claims object a deliverable holds: the whole text as JSON, else its fenced JSON blocks; None when none parses to one."""
    if not isinstance(text, str):
        return None
    candidates = [text.strip()] + [m.strip() for m in _FENCE.findall(text)]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if _record(value):
            return value
    return None


def evidence_check(root):
    """The default `check`: every claim's evidence through `verify_claims.check_claims`, the one executable evidence contract."""
    import verify_claims

    def check(rows):
        try:
            report = verify_claims.check_claims(
                [dict(id=row["id"], evidence=verify_claims.items_of(row["claim"], root=root), polarity=row["claim"].get("polarity"))
                 for row in rows], root=root)
        except Exception as exc:  # noqa: BLE001 - an unreadable check is `unavailable`, never a pass
            return {"status": "unavailable", "counts": {"VERIFIED": 0, "UNVERIFIED": 0, "UNCHECKED": len(rows)},
                    "verdicts": {row["id"]: "UNCHECKED" for row in rows}, "note": f"{type(exc).__name__}: {exc}"}
        out = {"status": report["status"], "counts": report["counts"],
               "verdicts": {key: value["verdict"] for key, value in report["verdicts"].items()}}
        if report["errors"]:
            out["note"] = "; ".join(report["errors"])[:400]
        return out
    return check


def consolidate(results, *, check=None, root=None):
    """The consolidated result of `results = [(lens key, claims object | None)]` in lens order.

    `check(rows)` takes `[{"id", "claim"}]` and returns `{"status", "counts", "verdicts": {id: verdict}, "note"?}`;
    the default checks the evidence against `root`."""
    check = check or evidence_check(Path(root) if root else REPO)
    flags, gaps, per_lens, stamped = [], [], [], []
    next_id = 1
    for key, res in results:
        if not _record(res):
            flags.append({"kind": "lens-no-return", "lens": key,
                          "detail": "lens delivered no claims object — its dimension is UNCOVERED, not clear. Recover BEFORE "
                                    "re-dispatching: its stream and record sit beside its `<label>.md` in the fan-out out dir "
                                    "(or the Workflow journal for a native lens) and hold any paid-for work"})
            per_lens.append({"key": key, "claims": None, "stoppedAt": None, "basis": None, "toolsUsed": None,
                             "status": "failed", "rejected": 0})
            continue
        if not isinstance(res.get("claims"), list) or not _is_checked(res.get("checked")):
            flags.append({"kind": "lens-invalid-shape", "lens": key,
                          "detail": "lens returned a malformed claims/checked object — its dimension is UNCOVERED, "
                                    "not a completed zero"})
            per_lens.append({"key": key, "claims": None, "stoppedAt": None, "basis": None, "toolsUsed": None,
                             "status": "uncovered", "rejected": 0})
            continue
        checked = res["checked"]
        lens_gaps = 0
        for gap in res["gaps"] if isinstance(res.get("gaps"), list) else []:
            if _text(gap):
                gaps.append({"lens": key, "gap": gap})
                lens_gaps += 1
        rejected = valid = 0
        for index, claim in enumerate(res["claims"]):
            claim_id = f"C{next_id}"
            next_id += 1
            if not _is_claim(claim):
                rejected += 1
                flags.append({"kind": "lens-invalid-entry", "lens": key,
                              "detail": f"claim {claim_id} (entry {index}) fails the required claim shape — skipped; "
                                        "it keeps its id and valid neighbors keep theirs"})
                continue
            stamped.append({**claim, "id": claim_id, "lens": key, "index": index})
            valid += 1
        stopped_coverage = checked["stoppedAt"] == "blocked" or (
            not checked["toolsUsed"] and checked["stoppedAt"] != "trigger-not-met")
        row = {"key": key, "claims": valid, "observed": len(res["claims"]), "rejected": rejected,
               "stoppedAt": checked["stoppedAt"], "basis": checked["basis"], "toolsUsed": len(checked["toolsUsed"]),
               "gaps": lens_gaps,
               "status": "partial" if rejected or lens_gaps or stopped_coverage else "completed"}
        per_lens.append(row)
        if stopped_coverage and valid == 0 and checked["stoppedAt"] != "trigger-not-met":
            row["status"] = "uncovered"
        if checked["stoppedAt"] == "blocked":
            flags.append({"kind": "lens-blocked", "lens": key,
                          "detail": f"the lens stopped at a blocked read after {valid} valid claim(s); its coverage is "
                                    f"{row['status'].upper()}"})
        if lens_gaps:
            flags.append({"kind": "lens-gaps", "lens": key,
                          "detail": f"{lens_gaps} explicit gap(s) keep this lens from completed coverage"})
        if valid == 0 and rejected == 0:
            tools = len(checked["toolsUsed"])
            if checked["stoppedAt"] == "trigger-not-met":
                flags.append({"kind": "lens-trigger-not-met", "lens": key, "detail": checked["basis"]})
            elif checked["stoppedAt"] == "blocked" or tools == 0:
                flags.append({"kind": "lens-did-not-run", "lens": key,
                              "detail": f"zero claims with {tools} recorded tool calls (stoppedAt={checked['stoppedAt']}). "
                                        "Its dimension is UNCOVERED — re-dispatch or cover it inline before treating the "
                                        "topic as explored."})
            elif lens_gaps == 0:
                flags.append({"kind": "lens-empty-verified", "lens": key,
                              "detail": f"swept {tools} targets and found nothing (stoppedAt={checked['stoppedAt']})"})

    for claim in stamped:
        polarity, evidence = claim["polarity"], claim.get("evidence")
        proof = bool(evidence) if isinstance(evidence, list) else _text(evidence)
        if polarity in ("exists", "partial") and not proof:
            if claim.get("confidence") == "verified":
                flags.append({"kind": "downgraded-no-evidence", "lens": claim["lens"],
                              "detail": f"{claim['subject']} — claimed verified with no quoted evidence"})
            claim["confidence"] = "unverified"
        if polarity == "absent" and not (isinstance(evidence, list) and any(
                _record(item) and isinstance(item.get("command"), str) and item.get("output") == "" for item in evidence)):
            if claim.get("confidence") == "verified":
                flags.append({"kind": "downgraded-unproven-absence", "lens": claim["lens"],
                              "detail": f"{claim['subject']} — absence claimed verified with no proving command"})
            claim["confidence"] = "unverified"
        if claim.get("confidence") not in ("verified", "unverified"):
            claim["confidence"] = "unverified"

    groups = {}
    for claim in stamped:
        groups.setdefault(_norm_key(claim["subject"]), []).append(claim)
    contradictions, claims = [], []
    for key, group in groups.items():
        lens_keys = list(dict.fromkeys(c["lens"] for c in group))
        contested = len(lens_keys) > 1 and len({c["polarity"] for c in group}) > 1
        if contested:
            contradictions.append({
                "key": key, "subject": group[0]["subject"],
                "positions": [{"lens": c["lens"], "polarity": c["polarity"], "claim": c["claim"],
                               "confidence": c["confidence"], "evidence": c.get("evidence") or None,
                               "journalSelector": c["id"]} for c in group]})
        for claim in group:
            agreeing = [lens for lens in lens_keys if lens != claim["lens"]
                        and any(o["lens"] == lens and o["polarity"] == claim["polarity"] for o in group)]
            extra = {}
            if contested:
                extra["contested"] = True
            if agreeing:
                extra["corroboratedBy"] = agreeing
            claims.append({**claim, **extra})
    rank = {name: number for number, name in enumerate(BEARINGS)}
    claims.sort(key=lambda c: (0 if c.get("contested") else 1, rank.get(c["bearing"], 9),
                               0 if c["confidence"] == "verified" else 1))

    report = check([{"id": f"{c['lens']}#{c['index']}", "claim": c} for c in claims])
    verdicts = report.get("verdicts") or {}
    for claim in claims:
        verdict = verdicts.get(f"{claim['lens']}#{claim['index']}")
        claim["verdict"] = verdict if verdict in VERDICTS else "UNCHECKED"
        claim["confidence"] = "verified" if claim["verdict"] == "VERIFIED" else "unverified"
    verification = {"status": report.get("status"), "counts": report.get("counts"),
                    "verdicts": {f"{c['lens']}#{c['index']}": c["verdict"] for c in claims},
                    "nonVerified": [f"{c['lens']}#{c['index']}" for c in claims if c["verdict"] != "VERIFIED"]}
    if report.get("note"):
        verification["note"] = report["note"]

    counts = {
        "lenses": len(results), "claims": len(claims),
        "verified": sum(1 for c in claims if c["confidence"] == "verified"),
        "contested": sum(1 for c in claims if c.get("contested")),
        "corroborated": sum(1 for c in claims if c.get("corroboratedBy")),
        "premiseContradictions": sum(1 for c in claims if c["bearing"] == "premise-contradiction"),
        "reuseCandidates": sum(1 for c in claims if c["bearing"] == "reuse-candidate"),
        "contradictions": len(contradictions), "gaps": len(gaps),
        "rejected": sum(row.get("rejected") or 0 for row in per_lens),
        "uncoveredLenses": sum(1 for row in per_lens if row["status"] in ("failed", "uncovered")),
    }
    lens_rows = [{"key": r["key"], "claims": r["claims"], "status": r["status"], "rejected": r.get("rejected") or 0}
                 for r in per_lens]
    coverage = {"requested": len(results),
                **{name: sum(1 for r in lens_rows if r["status"] == name)
                   for name in ("completed", "partial", "failed", "uncovered")},
                "lenses": lens_rows}
    unavailable = coverage["failed"] + coverage["uncovered"]
    status = ("failed" if unavailable == coverage["requested"]
              else "uncovered" if unavailable else "partial" if coverage["partial"] else "completed")
    return {"claims": claims, "contradictions": contradictions, "flags": flags, "gaps": gaps, "counts": counts,
            "perLens": per_lens, "verification": verification, "coverage": coverage, "status": status}


def _jobs(spec):
    rows = spec.get("jobs") if isinstance(spec, dict) else spec
    if not isinstance(rows, list) or not rows or not all(isinstance(j, dict) and j.get("label") for j in rows):
        raise ValueError("the jobs file holds no job list with labels")
    return rows


def lens_key(job):
    return str(job.get("seat") or job.get("key") or job["label"])


def delivered(jobs, fanout_dir, workflow_dirs=()):
    """[(lens key, claims object | None)]: each job's `<label>.md` in `fanout_dir`, after landing the journal results of
    every `workflow_dirs` run there and any sidecar stdout the fan-out has not yet settled."""
    import dispatch_result
    fanout_dir = Path(fanout_dir)
    with contextlib.redirect_stdout(io.StringIO()):
        for run_dir in workflow_dirs:
            dispatch_result.materialize_workflow(Path(run_dir), fanout_dir)
        if fanout_dir.is_dir():
            dispatch_result.materialize_fanout(fanout_dir)
    out = []
    for job in jobs:
        path = dispatch_result.deliverable_path(fanout_dir, job["label"])
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        out.append((lens_key(job), parse_lens_object(text)))
    return out


def collect(jobs, fanout_dir, *, workflow_dirs=(), check=None, root=None):
    """`consolidate` over what `jobs` delivered to `fanout_dir`."""
    return consolidate(delivered(jobs, fanout_dir, workflow_dirs), check=check, root=root)


def summary(result, saved):
    counts = result["counts"]
    lines = [f"explore_result: {counts['lenses']} lenses -> {counts['claims']} claims ({counts['verified']} verified, "
             f"{counts['premiseContradictions']} premise-contradiction, {counts['reuseCandidates']} reuse-candidate), "
             f"{counts['contradictions']} contradictions, {counts['gaps']} gaps, {counts['uncoveredLenses']} UNCOVERED lenses; "
             f"status {result['status']}, evidence check {result['verification']['status']}"]
    lines += [f"flag {f['kind']} {f.get('lens') or ''}: {str(f['detail'])[:160]}" for f in result["flags"]]
    lines += [f"UNCOVERED {row['key']}: {row['status']}" for row in result["perLens"]
              if row["status"] in ("failed", "uncovered")]
    lines.append(f"non-verified claims: {len(result['verification']['nonVerified'])}")
    lines.append(f"result: {Path(saved).resolve().as_posix()}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("USAGE")[0].strip(), prog="explore_result.py")
    ap.add_argument("jobs", help="the jobs_<prefix>.json the fan-out ran")
    ap.add_argument("--workflow-dir", action="append", default=[],
                    help="transcript dir of the dispatch.js run for the jobs the fan-out placed native; repeatable")
    ap.add_argument("--fanout-dir", help="the fan-out's out dir (default: the jobs file's spillDir)")
    ap.add_argument("--root", help="where relative evidence resolves (default: the repository)")
    ap.add_argument("--out", help="the full result file (default: explore_result.json beside the jobs file)")
    a = ap.parse_args(argv)
    path = Path(a.jobs)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
        jobs = _jobs(spec)
    except (OSError, ValueError) as exc:
        print(f"explore_result: {path}: {exc}", file=sys.stderr)
        return 2
    spill = a.fanout_dir or (spec.get("spillDir") if isinstance(spec, dict) else None)
    if not spill:
        print("explore_result: the jobs file names no spillDir; pass --fanout-dir", file=sys.stderr)
        return 2
    fanout_dir = Path(spill) if Path(spill).is_absolute() else REPO / spill
    result = collect(jobs, fanout_dir, workflow_dirs=a.workflow_dir, root=a.root)
    saved = Path(a.out) if a.out else path.with_name("explore_result.json")
    saved.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(summary(result, saved))
    return 1 if result["counts"]["uncoveredLenses"] else 0


if __name__ == "__main__":
    sys.exit(main())
