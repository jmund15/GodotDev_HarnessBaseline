"""Re-runnable proof for tools/eval_dashboard.py: a planted archive becomes a note, a report and a snapshot.

Every case runs the real CLI as a subprocess against a planted --root and --vault in a temp directory
with --today fixed, and asserts on the files it wrote.

    python3 .claude/tests/test_eval_dashboard.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE = os.path.dirname(HERE)
TOOL = os.path.join(CLAUDE, "tools", "eval_dashboard.py")

PATTERNS = {"A": "p", "B": "p", "C": "p", "D": "p", "E": "p"}


def entry(n, date, outcome, pattern=None, domains=("meta",), skills=(), duration=None):
    row = {"session_id": "s-%03d" % n, "id": 400 + n, "title": "Entry %03d" % n, "date": date,
           "outcome": outcome, "pattern": pattern, "domains": list(domains),
           "corrections": [] if outcome == "clean" else ["c"], "skills_used": list(skills),
           "memory_searches": 0, "memory_hits": [],
           "tests": {"written": 0, "total_passing": 0, "tdd_followed": True},
           "key_takeaway": "t%d" % n, "notes": ""}
    if duration is not None:
        row["shape"] = {"duration_min": duration}
    return row


PLANTED = [
    entry(1, "2026-07-02", "clean"), entry(2, "2026-07-11", "correction", "A"),
    entry(3, "2026-08-03", "clean"), entry(4, "2026-08-09", "failure", "B"),
    entry(5, "2026-08-20", "clean"),
    entry(6, "2026-09-01", "clean", skills=("testing",)),
    entry(7, "2026-09-05", "correction", "A", domains=("meta", "godot"), skills=("testing",)),
    entry(8, "2026-09-12", "clean", duration=250),
    entry(9, "2026-09-20", "clean"), entry(10, "2026-09-27", "failure", "C"),
]


def wf(run, label, effort, outcome, day, **extra):
    row = {"run": run, "label": label, "workflow": "proof", "model": "proof-model", "effort": effort,
           "requested_effort": effort, "state": "done", "cost": 1000.0, "qcost": 1000.0, "turns": 4}
    if day:
        row["run_date"] = day
    if outcome is not None:
        row["outcome"] = outcome
    row.update(extra)
    return row


def side(label, model, effort, outcome, usd, day):
    return {"source": "sidecar", "run": "sidecar-%sT00:00:00Z-%s" % (day, label), "run_date": day, "label": label,
            "model": model, "effort": effort, "requested_effort": effort, "state": "completed",
            "cost_usd": usd, "outcome": outcome}


LEDGER = [
    wf("wf_aug", "a1", "medium", "clean", "2026-08-10"), wf("wf_aug", "a2", "medium", "rework", "2026-08-10"),
    wf("wf_sep", "s1", "medium", "clean", "2026-09-03", qcost=2000.0),
    wf("wf_sep", "s2", "medium", "defects", "2026-09-03", qcost=4000.0),
    wf("wf_sep", "s3", "high", "clean", "2026-09-03", qcost=8000.0),
    wf("wf_sep", "s4", "low", "unrated", "2026-09-03", qcost=None, cost=None),
    wf("wf_sep", "s5", "low", "unrated", "2026-09-03", sampled_out=True),
    wf("wf_sep", "s6", "high", None, "2026-09-03"),
    wf("wf_sep", "s7", "high", "bogus", "2026-09-15"),
    wf("wf_undated", "u1", "low", "rework", None),
    side("x1", "m-one", "low", "clean", 2.0, "2026-09-02"),
    side("x2", "m-one", "low", "clean", None, "2026-09-04"),
    side("x3", "m-two", "high", "defects", 3.0, "2026-09-06"),
    {"run": "wf_ignored", "ignored": True, "reason": "synthetic", "date": "2026-09-01"},
]


def plant_root(entries=None, ledger=None):
    root = tempfile.mkdtemp(prefix="evaldash_root_")
    claude = os.path.join(root, ".claude")
    os.makedirs(claude)
    with open(os.path.join(claude, "self_evaluate_archive.json"), "w", encoding="utf-8") as handle:
        json.dump({"Self_Evaluate_Themes": {"patterns": PATTERNS}, "legacy_entries": [],
                   "structured_entries": list(PLANTED if entries is None else entries)}, handle)
    rows = LEDGER if ledger is None else ledger
    if rows:
        with open(os.path.join(claude, "orchestration_metrics.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    return root


def tiered(n, day, session, outcome="clean"):
    return {"source": "sidecar", "run": "sidecar-%s-t%d" % (day, n), "run_date": day, "label": "t%d" % n,
            "model": "proof-a", "effort": "medium", "requested_effort": "medium", "state": "completed",
            "cost_usd": 0.5, "outcome": outcome, "tier": "executor", "session_id": session, "transport": "deepseek"}


TIER_LEDGER = [tiered(i, "2026-09-%02d" % (i + 1), "sess-%d" % (i % 3)) for i in range(8)] + \
              [tiered(20 + i, "2026-08-1%d" % i, "sess-3") for i in range(2)]


def plant_registry(roles=("executor",)):
    """A registry of one synthetic row (claiming `roles`) cloned from a real row's shape; returns its path."""
    with open(os.path.join(CLAUDE, "reference", "external_models.json"), encoding="utf-8") as handle:
        data = json.load(handle)
    row = data["models"][0]
    row.update(id="proof-a", alias="proofa", family="proof-a", roles=list(roles), order=1)
    data["models"] = [row]
    data.pop("aliasHistory", None)
    path = os.path.join(tempfile.mkdtemp(prefix="evaldash_registry_"), "external_models.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle)
    return path


def run(root, vault, *extra, today="2026-09-28", env=None, cwd=None):
    return subprocess.run([sys.executable, TOOL, "--root", root, "--vault", vault, "--today", today, *extra],
                          capture_output=True, text=True, timeout=120, env=env, cwd=cwd)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def main():
    failures, ran = [], []

    def check(ok, label, detail=""):
        ran.append(label)
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
        if not ok:
            failures.append(label + (": " + detail if detail else ""))

    root, vault = plant_root(), tempfile.mkdtemp(prefix="evaldash_vault_")
    result = run(root, vault)
    check(result.returncode == 0, "tracer: the CLI exits 0 over a planted archive",
          "rc=%s stdout=%r stderr=%r" % (result.returncode, result.stdout[-300:], result.stderr[-300:]))

    note_path = os.path.join(vault, "Self Evaluation Dashboard.md")
    report_path = os.path.join(vault, "EVAL-DASHBOARD.html")
    note = read(note_path) if os.path.isfile(note_path) else ""
    check(bool(note), "tracer: the note is written under its stable name")
    check("| Clean sessions (the owner made no correction) |" in note and "### Dispatched agents, 2026-09" in note,
          "tracer: the note holds the sessions block and the dispatched agents block")
    link = re.search(r"\[Open the full report\]\((file:///[^)]+EVAL-DASHBOARD\.html)\)", note)
    check(bool(link), "tracer: the note links the report with a file:/// URL")
    if link:
        target = url2pathname(urlparse(link.group(1)).path)
        check(os.path.isfile(target) and os.path.samefile(target, report_path),
              "tracer: the linked report file exists in the vault", target)

    report = read(report_path) if os.path.isfile(report_path) else ""
    inlined = re.search(r'<script id="data" type="application/json">(.*?)</script>', report, re.S)
    data = None
    if inlined:
        try:
            data = json.loads(inlined.group(1))
        except ValueError:
            data = None
    check(isinstance(data, dict), "tracer: the report inlines JSON that parses")
    check(not re.search(r"https?://|<link|<script[^>]*src=", report),
          "tracer: the report holds no http://, https://, <link or <script src")

    history_path = os.path.join(root, ".claude", "eval_history.json")
    history = json.loads(read(history_path)) if os.path.isfile(history_path) else {}
    block = (history.get("months") or {}).get("2026-09", {})
    sessions = block.get("sessions", {})
    check(history.get("schema") == 3 and block.get("month") == "2026-09" and block.get("closed") is False,
          "tracer: eval_history.json holds an open 2026-09 block", repr(list((history.get("months") or {}))))
    check((sessions.get("n"), sessions.get("clean"), sessions.get("correction"), sessions.get("failure"),
           sessions.get("unknown")) == (5, 3, 1, 1, 0),
          "tracer: the 2026-09 counts equal the planted September entries", repr(sessions))
    check(sessions.get("patterns", {}).get("A") == 1 and sessions.get("patterns", {}).get("C") == 4
          and sessions.get("patterns", {}).get("B") == 0,
          "tracer: pattern counts follow the normalized convention (clean null counts as C)",
          repr(sessions.get("patterns")))
    check(sessions.get("causes") == {"A": 1, "B": 0, "C": 1, "D": 0, "E": 0, "none": 0},
          "tracer: causes count the pattern of the correction and the failure session alone", repr(sessions.get("causes")))
    check(sessions.get("duration", {}).get("lt300", {}).get("n") == 1
          and sessions.get("duration", {}).get("unknown") == 4,
          "tracer: duration buckets count the one entry that carries a duration", repr(sessions.get("duration")))
    check(sessions.get("domains", {}).get("meta", {}).get("n") == 5
          and sessions.get("skills", {}).get("testing", {}).get("n") == 2,
          "tracer: domain and skill counts are stored", repr(sessions.get("domains")))

    agents = block.get("agents", {})
    check((agents.get("rows"), agents.get("rated"), agents.get("unrated"), agents.get("sampled_out"),
           agents.get("absent"), agents.get("unparseable")) == (10, 6, 1, 1, 1, 1),
          "agents: the 2026-09 block reconciles rated + unrated + sampled_out + absent + unparseable = rows",
          repr(agents))
    def stored_cell(model, effort, **fields):
        base = {"model": model, "effort": effort, "wf": 0, "sc": 0, "n": 0, "clean": 0, "defects": 0, "rework": 0,
                "discarded": 0, "unrated": 0, "other": 0, "qcost": 0, "qcost_n": 0, "usd": 0, "usd_n": 0}
        return dict(base, **fields)

    check("by_effort" not in agents and "sidecar" not in agents and agents.get("cells") == [
        stored_cell("m-one", "low", sc=2, n=2, clean=2, usd=2.0, usd_n=1),
        stored_cell("m-two", "high", sc=1, n=1, defects=1, usd=3.0, usd_n=1),
        stored_cell("proof-model", "low", wf=2, n=2, unrated=2, qcost=1000.0, qcost_n=1),
        stored_cell("proof-model", "medium", wf=2, n=2, clean=1, defects=1, qcost=6000.0, qcost_n=2),
        stored_cell("proof-model", "high", wf=3, n=3, clean=1, other=2, qcost=10000.0, qcost_n=3)],
          "agents: the snapshot keeps one cell per model and requested effort with its route mix, outcome counts, "
          "`other` for a missing or unknown outcome, and each cost unit beside its priced count, and neither the "
          "per-effort table nor the per-model sidecar table", repr(agents))
    check(agents.get("paid") == {"usd": 0, "n": 0} and all(
        c["clean"] + c["defects"] + c["rework"] + c["discarded"] + c["unrated"] + c["other"] == c["n"]
        for c in agents.get("cells", [])),
          "agents: the snapshot stores the pay-per-token spend, and every stored cell's columns sum to its n",
          repr(agents.get("paid")))
    check(block.get("sources", {}).get("orchestration") == {"rows": 10, "undated": 1, "first": "2026-09-02",
                                                            "last": "2026-09-15"},
          "agents: sources.orchestration holds the month's rows, the archive's undated count and the date span",
          repr(block.get("sources")))
    check("11% of this month's agents are unrated; the rows above read on the rated rest." in note
          and "- No model at any effort is a problem cell this month." in note,
          "agents: the note carries the unrated caveat with its share and says when no cell is a problem")
    check("pay-per-token" not in note and "Quota cost" not in note and "Sidecar USD" not in note,
          "agents: a month with no pay-per-token run prints no money line and the note prints no pooled cost row",
          note[-900:])
    live = (data or {}).get("agents", {})
    check(live.get("rows") == 13 and live.get("coverage", {}).get("total", {}).get("rated") == 9,
          "agents: the report inlines the live all-time facts", repr(sorted(live)))
    check(sorted({c["model"] for c in live.get("cells", [])}) == ["m-one", "m-two", "proof-model"]
          and sum(c["sc"] for c in live.get("route_cells", [])) == 3
          and not any(key in live for key in ("workflow_cells", "sidecar_models", "usd", "usd_per_agent", "sidecar_usd")),
          "agents: the report's facts are one (model, effort) table with its route split and no blended sidecar figure",
          repr(sorted(live)))

    bare_root, bare_vault = plant_root(ledger=[]), tempfile.mkdtemp(prefix="evaldash_vault_")
    bare = run(bare_root, bare_vault)
    bare_block = json.loads(read(os.path.join(bare_root, ".claude", "eval_history.json")))["months"]["2026-09"] \
        if bare.returncode == 0 else {}
    check(bare.returncode == 0 and bare_block.get("agents", {}).get("rows") == 0
          and bare_block.get("sources", {}).get("orchestration", {}).get("rows") == 0,
          "agents: an archive with no orchestration ledger still publishes, with zero agent rows",
          "rc=%s %s" % (bare.returncode, (bare.stdout + bare.stderr)[-200:]))

    empty_ladder = (data or {}).get("ladder", {})
    check(all(month.get("ladder") == [] for month in history.get("months", {}).values())
          and empty_ladder.get("cells") == [] and empty_ladder.get("buckets", {}).get("tier_absent") == 9
          and "Ladder readout: 0 cells, 0 proposals" in note,
          "ladder: rows that record no tier make no cell and land in tier absent, in the history, report and note",
          repr((empty_ladder.get("buckets"), [m.get("ladder") for m in history.get("months", {}).values()])))

    registry = plant_registry()
    tier_root = plant_root(ledger=TIER_LEDGER)
    tier_vault = tempfile.mkdtemp(prefix="evaldash_vault_")
    tiered = run(tier_root, tier_vault, env=dict(os.environ, HARNESS_MODEL_REGISTRY=registry))
    tier_note = read(os.path.join(tier_vault, "Self Evaluation Dashboard.md")) if tiered.returncode == 0 else ""
    tier_report = read(os.path.join(tier_vault, "EVAL-DASHBOARD.html")) if tiered.returncode == 0 else ""
    tier_history = json.loads(read(os.path.join(tier_root, ".claude", "eval_history.json"))) if tiered.returncode == 0 else {}
    months = tier_history.get("months", {})
    os.environ["HARNESS_MODEL_REGISTRY"] = registry
    sys.path.insert(0, os.path.join(CLAUDE, "tools"))
    import eval_dashboard
    built_facts = eval_dashboard.build_facts(tier_root, "2026-09-28")
    built = built_facts["months"]
    check(isinstance(built_facts.get("effects"), list) and isinstance(built_facts.get("findings"), dict),
          "findings: build_facts on the planted root returns effects as a list beside findings",
          repr((type(built_facts.get("effects")), type(built_facts.get("findings")))))
    real_compute = eval_dashboard.eval_findings.compute
    eval_dashboard.eval_findings.compute = lambda *a, **k: (_ for _ in ()).throw(ValueError("git ancestry check failed"))
    try:
        degraded = eval_dashboard.build_facts(tier_root, "2026-09-28")
    finally:
        eval_dashboard.eval_findings.compute = real_compute
    degraded_rows = [r for r in eval_dashboard.build_inputs(tier_root, dict(degraded, history={"status": "ok"}), "2026-09-28",
                                                            {"source": "rules", "status": "ok"}, {"source": "routing", "status": "ok"},
                                                            {"status": "ok"}) if r["source"] == "findings"]
    check(degraded.get("findings") is None and degraded.get("effects") is None and degraded.get("trends") is None
          and degraded_rows and degraded_rows[0]["status"] == "unparseable" and "git ancestry" in degraded_rows[0].get("detail", "")
          and isinstance(degraded.get("months"), dict) and degraded["months"],
          "test_findings_failure_degrades_to_an_unparseable_inputs_row_and_the_rest_publishes", repr(degraded_rows))

    # S7a/H3: the Agents tab's footer needs the reasons, read from the rows at collection. Each named
    # reason is a pure function of the row (never stored); `sampled-out` stays out because the coverage
    # line already reports the deliberate exclusions.
    reasons_root = plant_root(ledger=[
        wf("wf_alone", "stalled", "low", "unrated", "2026-09-03", state="running"),
        wf("wf_pin", "pinned", "?", "unrated", "2026-09-03"),
        wf("wf_solo", "lonely", "medium", "unrated", "2026-09-03"),
        wf("wf_mix", "rated", "low", "clean", "2026-09-03"),
        wf("wf_mix", "debt", "low", "unrated", "2026-09-03")])
    reasons = eval_dashboard.build_facts(reasons_root, "2026-09-28")["agents"]["unrated_reasons"]
    check(reasons == {"not-completed": 1, "no-effort": 1, "session-never-rated": 1},
          "agents: unrated_reasons names why each unrated row is unrated, read at collection and never "
          "stored (a row in a rated session with no structural cause is plain debt, so it is not named)",
          repr(reasons))


    def raises(fn, *args):
        try:
            fn(*args)
        except eval_dashboard.InputError as exc:
            return str(exc)
        return ""

    cited = {"month": "2026-09", "state": "s", "moves": [{"do": "d", "why": "w", "see": "findings", "finding": ["f.a"], "metric": "f.a"}]}
    check("computed none" in raises(eval_dashboard.check_citations, cited, None)
          and "is not a finding" in raises(eval_dashboard.check_citations, cited, {"findings": {}, "metrics": {"f.a": {}}})
          and "is not a metric" in raises(eval_dashboard.check_citations, cited, {"findings": {"f.a": {}}, "metrics": {}})
          and raises(eval_dashboard.check_citations, cited, {"findings": {"f.a": {}}, "metrics": {"f.a": {}}}) == "",
          "test_check_citations_names_the_unknown_finding_metric_or_missing_findings")
    shaped = lambda **move: dict(cited, moves=[dict(cited["moves"][0], **move)])
    check("together" in raises(eval_dashboard.check_verdict, dict(cited, moves=[{"do": "d", "why": "w", "see": "findings", "metric": "f.a"}]), "2026-09")
          and raises(eval_dashboard.check_verdict, shaped(finding="f.a"), "2026-09") != ""
          and raises(eval_dashboard.check_verdict, shaped(metric=""), "2026-09") != ""
          and raises(eval_dashboard.check_verdict, shaped(finding=[]), "2026-09") != ""
          and raises(eval_dashboard.check_verdict, shaped(finding=["a\nb"]), "2026-09") != ""
          and raises(eval_dashboard.check_verdict, cited, "2026-09") == "",
          "test_check_verdict_rejects_malformed_finding_and_metric_pairs")
    def ledger_status(**diag):
        facts = {"today": "2026-09-28", "findings": {"diagnostics": {"ledger": dict({"rows": 3, "first": "2026-09-01", "last": "2026-09-20"}, **diag)}}}
        return eval_dashboard.ledger_inputs(facts)[0]["status"]
    check(ledger_status(absent=0, unparseable=0) == "ok" and ledger_status(absent=1, unparseable=0) == "absent"
          and ledger_status(absent=0, unparseable=2) == "unparseable" and eval_dashboard.ledger_inputs({"today": "2026-09-28"}) == [],
          "test_lesson_outcome_ledger_inputs_row_reads_absent_ok_and_unreadable")
    def denials_row(**cov):
        coverage = dict({"files": 1, "read": 1, "cached": 0, "unparseable": 0, "unreadable": 0, "roots": 1, "gaps": []}, **cov)
        return eval_dashboard.denials_inputs({"findings": {"diagnostics": {"denials": coverage}}})
    gap_row = denials_row(files=0, read=0, roots=0, gaps=["/x/projects"])[0]
    ok_row = denials_row()[0]
    check(gap_row["status"] == "absent" and "/x/projects" in gap_row["detail"] and gap_row["rows"] == 0
          and ok_row["status"] == "ok" and "1 root" in ok_row["detail"] and "1 file" in ok_row["detail"]
          and eval_dashboard.denials_inputs({}) == [],
          "test_denials_scan_inputs_row_names_roots_files_and_gaps", repr((gap_row, ok_row)))
    check(tiered.returncode == 0 and months.get("2026-09", {}).get("ladder") == [
        {"model_id": "proof-a", "tier": "executor", "n": 8, "sessions": 3, "clean": 8, "defects": 0, "rework": 0,
         "discarded": 0, "verdict": "holds"}]
          and built["2026-08"]["ladder"] == [
        {"model_id": "proof-a", "tier": "executor", "n": 2, "sessions": 1, "clean": 2, "defects": 0, "rework": 0,
         "discarded": 0, "verdict": "insufficient"}],
          "ladder: each month's snapshot holds the cells of that month's rows only, with counts and the verdict word",
          "rc=%s %r" % (tiered.returncode, {k: v.get("ladder") for k, v in built.items()}))
    tier_data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', tier_report, re.S).group(1)) \
        if tier_report else {}
    cell = next(iter(tier_data.get("ladder", {}).get("cells", [])), {})
    check((cell.get("model"), cell.get("tier"), cell.get("n"), cell.get("sessions")) == ("proof-a", "executor", 10, 4)
          and tier_data.get("ladder", {}).get("total") == 10,
          "ladder: the report inlines the all-time cell across both months", repr(tier_data.get("ladder")))
    check("Ladder readout: 1 cell, 0 proposals" in tier_note and "| proof-a · executor | 10 / 4 | holds |" in tier_note,
          "ladder: the note callout lists the cell with its n, sessions and verdict", tier_note[-500:])
    check("pay-per-token spend: $4.00 over 8 runs" in tier_note
          and months.get("2026-09", {}).get("agents", {}).get("paid") == {"usd": 4.0, "n": 8}
          and months.get("2026-08", {}).get("agents", {}).get("paid") == {"usd": 1.0, "n": 2},
          "agents: rows priced above zero on a registry route whose cost model is marginal-usd make the month's "
          "pay-per-token spend, stored per month and printed once in the note", tier_note[-900:])

    def write_file(name, content):
        path = os.path.join(tempfile.mkdtemp(prefix="evaldash_in_"), name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return path

    def stored(root_dir):
        path = os.path.join(root_dir, ".claude", "eval_history.json")
        return json.loads(read(path)) if os.path.isfile(path) else {}

    def report_data(vault_dir):
        path = os.path.join(vault_dir, "EVAL-DASHBOARD.html")
        if not os.path.isfile(path):
            return {}
        return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',
                                    read(path), re.S).group(1).replace("<\\/", "</"))

    def verdict_doc(**over):
        doc = {"month": "2026-09", "state": "Proof state sentence.",
               "moves": [{"do": "Raise the floor", "why": "two defects", "see": "agents"}], "watch": ["watch one"],
               "layers": dict({name: "no move: proof evidence" for name in ("sessions", "guards", "cascades", "ladder", "review")},
                              pins="move")}
        doc.update(over)
        return doc

    def fresh():
        return plant_root(), tempfile.mkdtemp(prefix="evaldash_vault_")

    good_root, good_vault = fresh()
    good_file = write_file("verdict.json", verdict_doc(milestone="milestone one"))
    good = run(good_root, good_vault, "--verdict", good_file)
    good_note = read(os.path.join(good_vault, "Self Evaluation Dashboard.md")) if good.returncode == 0 else ""
    check(good.returncode == 0 and not os.path.exists(good_file)
          and stored(good_root).get("months", {}).get("2026-09", {}).get("verdict") == verdict_doc(milestone="milestone one")
          and report_data(good_vault).get("verdict", {}).get("state") == "Proof state sentence."
          and "Proof state sentence." in good_note,
          "verdict: a valid verdict.json is stored with the month, shown in the note and report, then removed",
          "rc=%s file=%s %s" % (good.returncode, os.path.exists(good_file), (good.stdout + good.stderr)[-200:]))

    six = [{"do": "d", "why": "w", "see": "agents"}] * 6
    schema_cases = [
        ("moves", verdict_doc(moves=six)),
        ("see", verdict_doc(moves=[{"do": "d", "why": "w", "see": "bogus"}])),
        ("state", verdict_doc(state="s" * 301)),
        ("month", verdict_doc(month="2026-08")),
        ("watch", verdict_doc(watch=["a", "b", "c", "d"])),
        ("why", verdict_doc(moves=[{"do": "d", "see": "agents"}])),
        ("extra", dict(verdict_doc(), extra="x")),
    ]
    for field, doc in schema_cases:
        bad_root, bad_vault = fresh()
        bad_file = write_file("verdict.json", doc)
        bad = run(bad_root, bad_vault, "--verdict", bad_file)
        check(bad.returncode == 2 and field in bad.stdout and os.path.exists(bad_file) and not stored(bad_root),
              "verdict: an invalid %r exits 2 naming the field, keeps the file and publishes nothing" % field,
              "rc=%s %r" % (bad.returncode, bad.stdout[-200:]))
    for label, path in (("missing", os.path.join(tempfile.gettempdir(), "evaldash_no_such_verdict.json")),
                        ("unparseable", write_file("verdict.json", "{not json"))):
        bad_root, bad_vault = fresh()
        bad = run(bad_root, bad_vault, "--verdict", path)
        check(bad.returncode == 2 and os.path.basename(path) in bad.stdout,
              "verdict: a %s --verdict file exits 2 naming the file" % label, "rc=%s %r" % (bad.returncode, bad.stdout[-200:]))

    none_root, none_vault = fresh()
    none = run(none_root, none_vault)
    check(none.returncode == 0 and stored(none_root)["months"]["2026-09"]["verdict"] is None
          and report_data(none_vault).get("verdict") is None,
          "verdict: a run with no --verdict and none stored stores verdict null and the report carries null",
          "rc=%s" % none.returncode)
    keep_root, keep_vault = fresh()
    keep_file = write_file("verdict.json", verdict_doc())
    run(keep_root, keep_vault, "--verdict", keep_file)
    keep_path = os.path.join(keep_root, ".claude", "eval_history.json")
    before = read(keep_path) if os.path.isfile(keep_path) else None
    again = run(keep_root, keep_vault)
    check(again.returncode == 0 and before is not None and read(keep_path) == before
          and report_data(keep_vault).get("verdict", {}).get("state") == "Proof state sentence."
          and "Proof state sentence." in read(os.path.join(keep_vault, "Self Evaluation Dashboard.md")),
          "verdict: a later run without --verdict keeps the stored verdict, shows it, and leaves the history bytes unchanged",
          "rc=%s" % again.returncode)

    def one_move(**extra):
        return verdict_doc(moves=[dict({"do": "d", "why": "w", "see": "agents"}, **extra)])

    for label, move, field in (
            ("finding", {"finding": ["outcome.failure"]}, "metric"),
            ("metric", {"metric": "outcome.failure"}, "finding")):
        half_root, half_vault = fresh()
        half_file = write_file("verdict.json", one_move(**move))
        half = run(half_root, half_vault, "--verdict", half_file)
        check(half.returncode == 2 and "moves[0]" in half.stdout and "together" in half.stdout
              and os.path.exists(half_file) and not stored(half_root),
              "verdict: a move with %s but no %s exits 2 naming moves[0] and keeps the file" % (label, field),
              "rc=%s %r" % (half.returncode, half.stdout[-200:]))

    for label, move in (("finding", {"finding": ["no.such.id"], "metric": "outcome.failure"}),
                        ("metric", {"finding": ["outcome.failure"], "metric": "no.such.key"})):
        unk_root, unk_vault = fresh()
        unk_file = write_file("verdict.json", one_move(**move))
        unk = run(unk_root, unk_vault, "--verdict", unk_file)
        check(unk.returncode == 2 and "moves[0]" in unk.stdout and "is not a %s of this run" % label in unk.stdout
              and os.path.exists(unk_file) and not stored(unk_root),
              "verdict: an unknown %s in a cited move exits 2 naming moves[0], keeps the file and publishes nothing" % label,
              "rc=%s %r" % (unk.returncode, unk.stdout[-200:]))

    bare_move_root, bare_move_vault = fresh()
    bare_move = run(bare_move_root, bare_move_vault, "--verdict", write_file("verdict.json", one_move()))
    bare_move_note = read(os.path.join(bare_move_vault, "Self Evaluation Dashboard.md")) if bare_move.returncode in (0, 3) else ""
    check(bare_move.returncode in (0, 3) and " [not measured]" in bare_move_note,
          "verdict: a move with neither key is accepted and its note line reads not measured",
          "rc=%s %r" % (bare_move.returncode, bare_move.stdout[-200:]))

    cited_root, cited_vault = fresh()
    cited = run(cited_root, cited_vault, "--verdict",
                write_file("verdict.json", one_move(finding=["outcome.failure"], metric="outcome.failure")))
    cited_note = read(os.path.join(cited_vault, "Self Evaluation Dashboard.md")) if cited.returncode in (0, 3) else ""
    check(cited.returncode in (0, 3) and " [metric: outcome.failure]" in cited_note
          and stored(cited_root).get("months", {}).get("2026-09", {}).get("verdict", {}).get("moves", [{}])[0].get("metric")
          == "outcome.failure",
          "verdict: a move citing a real finding and metric publishes, and the note names both",
          "rc=%s %r" % (cited.returncode, cited.stdout[-200:]))

    def routing(day):
        return {"generated_at": day, "total_entries": 40, "silent_misses": 5, "active_window_days": 30,
                "by_week": {"2026-W38": {"silent_miss": 3}},
                "top_5_silent_miss_rules": [{"rule": "proof-rule", "count": 3, "trend_vs_prior_4wk_avg": "+12%"}]}

    def with_routing(content):
        root_dir, vault_dir = fresh()
        logs = os.path.join(root_dir, ".claude", "logs")
        os.makedirs(logs)
        if content is not None:
            with open(os.path.join(logs, "routing_audit_stats.json"), "w", encoding="utf-8") as handle:
                handle.write(content if isinstance(content, str) else json.dumps(content))
        verdict_path = write_file("verdict.json", verdict_doc())
        return root_dir, vault_dir, verdict_path, run(root_dir, vault_dir, "--verdict", verdict_path)

    def input_row(vault_dir, source):
        return next((i for i in report_data(vault_dir).get("inputs", []) if i["source"] == source), {})

    r_root, r_vault, r_verdict, fresh_run = with_routing(routing("2026-09-27T10:00:00"))
    check(fresh_run.returncode == 0 and input_row(r_vault, "routing audit").get("status") == "ok"
          and report_data(r_vault).get("routing", {}).get("total_entries") == 40 and not os.path.exists(r_verdict),
          "inputs: a fresh routing_audit_stats.json is ok, feeds the Routing card and exits 0",
          "rc=%s %r" % (fresh_run.returncode, input_row(r_vault, "routing audit")))
    s_root, s_vault, s_verdict, stale_run = with_routing(routing("2026-09-10T10:00:00"))
    check(stale_run.returncode == 3 and input_row(s_vault, "routing audit").get("status") == "stale"
          and stored(s_root).get("months", {}).get("2026-09") and not os.path.exists(s_verdict),
          "inputs: a routing audit older than STALE_DAYS is stale, exits 3, still publishes and removes the verdict",
          "rc=%s %r" % (stale_run.returncode, input_row(s_vault, "routing audit")))
    u_root, u_vault, u_verdict, bad_run = with_routing("{not json")
    check(bad_run.returncode == 3 and input_row(u_vault, "routing audit").get("status") == "unparseable"
          and report_data(u_vault).get("routing") is None,
          "inputs: an unparseable routing audit exits 3 and draws no Routing card",
          "rc=%s %r" % (bad_run.returncode, input_row(u_vault, "routing audit")))
    a_root, a_vault, a_verdict, absent_run = with_routing(None)
    check(absent_run.returncode == 0 and input_row(a_vault, "routing audit").get("status") == "absent"
          and report_data(a_vault).get("routing") is None,
          "inputs: an absent routing audit is listed absent and does not degrade the run",
          "rc=%s %r" % (absent_run.returncode, input_row(a_vault, "routing audit")))
    sources = [i["source"] for i in report_data(a_vault).get("inputs", [])]
    check(sources == ["self-evaluate archive", "orchestration ledger", "model registry", "routing audit", "rules feed",
                      "escape log", "review ledger", "lesson-outcome ledger", "denials scan", "history"]
          and input_row(a_vault, "history").get("status") == "ok"
          and input_row(a_vault, "lesson-outcome ledger").get("status") == "absent"
          and input_row(a_vault, "model registry").get("status") == "ok"
          and input_row(a_vault, "self-evaluate archive").get("rows") == len(PLANTED),
          "inputs: one row per producer, in a fixed order", repr(sources))

    feed = {"scanned": 30, "rules": 4, "live": [], "fired": [{"path": "rules/proof.md", "source": "file",
            "trigger": "review-by: 2026-09-01", "kind": "review-by", "reason": "review date 2026-09-01 reached"}],
            "undecidable": [{"path": "rules/u.md"}], "malformed": []}
    f_root, f_vault = fresh()
    feed_path = write_file("rule_retirement.json", feed)
    os.utime(feed_path, (1790450000, 1790450000))
    feed_run = run(f_root, f_vault, "--rules-json", feed_path)
    f_note = read(os.path.join(f_vault, "Self Evaluation Dashboard.md")) if feed_run.returncode in (0, 3) else ""
    f_month = stored(f_root).get("months", {}).get("2026-09", {})
    check(feed_run.returncode in (0, 3) and report_data(f_vault).get("rules", {}).get("fired", [{}])[0].get("path") == "rules/proof.md"
          and "rule past trigger" in f_note and "rules" not in f_month
          and not any(row["signal"] == "rule past trigger" for row in f_month.get("attention", [])),
          "rules: a fired rule from --rules-json appears in the note's attention list and is never stored in the month",
          "rc=%s %r" % (feed_run.returncode, f_note[-300:]))
    for label, body in (("missing", None), ("unparseable", "{not json")):
        b_root, b_vault = fresh()
        b_path = os.path.join(tempfile.gettempdir(), "evaldash_no_such_rules.json") if body is None \
            else write_file("rule_retirement.json", body)
        b_run = run(b_root, b_vault, "--rules-json", b_path)
        check(b_run.returncode == 2 and os.path.basename(b_path) in b_run.stdout,
              "rules: a %s --rules-json file exits 2 naming the file" % label, "rc=%s %r" % (b_run.returncode, b_run.stdout[-200:]))

    x_root, x_vault = fresh()
    with open(os.path.join(x_root, ".claude", "eval_history.json"), "w", encoding="utf-8") as handle:
        handle.write("{corrupt")
    x_verdict = write_file("verdict.json", verdict_doc())
    x_run = run(x_root, x_vault, "--verdict", x_verdict)
    check(x_run.returncode == 4 and os.path.exists(x_verdict)
          and os.path.isfile(os.path.join(x_vault, "Self Evaluation Dashboard.md")),
          "verdict: a refused history exits 4, still writes the note, and keeps verdict.json",
          "rc=%s %r" % (x_run.returncode, x_run.stdout[-200:]))
    w_root = plant_root()
    w_verdict = write_file("verdict.json", verdict_doc())
    w_run = run(w_root, os.path.join(tempfile.gettempdir(), "evaldash_no_such_vault"), "--verdict", w_verdict)
    check(w_run.returncode == 1 and os.path.exists(w_verdict),
          "verdict: a vault failure exits 1 and keeps verdict.json", "rc=%s" % w_run.returncode)

    floor_ledger = [wf("wf_f", "fam:f%d" % i, "medium", "clean", "2026-09-03") for i in range(7)] + \
                   [wf("wf_f", "fam:f%d" % i, "medium", "defects", "2026-09-03") for i in range(7, 10)]
    spread_ledger = [wf("wf_g", "fam%d:g" % i, "medium", "clean", "2026-09-03") for i in range(7)] + \
                    [wf("wf_g", "fam%d:g" % i, "medium", "defects", "2026-09-03") for i in range(7, 10)]
    s_root = plant_root(ledger=spread_ledger)
    s_run = run(s_root, tempfile.mkdtemp(prefix="evaldash_vault_"),
                env=dict(os.environ, HARNESS_MODEL_REGISTRY=plant_registry(roles=("architect",))))
    s_attention = stored(s_root).get("months", {}).get("2026-09", {}).get("attention", [])
    check(s_run.returncode in (0, 3) and not any(row["signal"] == "effort floor moved" for row in s_attention),
          "attention: defects spread one per family at one effort do not move a floor",
          "rc=%s %r" % (s_run.returncode, s_attention))
    p_root = plant_root(ledger=TIER_LEDGER[:8] + floor_ledger)
    p_vault = tempfile.mkdtemp(prefix="evaldash_vault_")
    p_run = run(p_root, p_vault, env=dict(os.environ, HARNESS_MODEL_REGISTRY=plant_registry(roles=("architect",))))
    p_attention = stored(p_root).get("months", {}).get("2026-09", {}).get("attention", [])
    p_note = read(os.path.join(p_vault, "Self Evaluation Dashboard.md")) if p_run.returncode in (0, 3) else ""
    check(p_run.returncode in (0, 3) and [row["signal"] for row in p_attention] == ["ladder proposal", "effort floor moved"]
          and p_attention[0]["tab"] == "ladder" and p_attention[1]["tab"] == "agents" and "proof-a" in p_attention[0]["evidence"]
          and p_attention[1]["evidence"].startswith("fam on proof-model at medium:")
          and "Needs a decision (" in p_note,
          "attention: a ladder proposal and a moved effort floor are stored with the month, proposal first, and listed in the note",
          "rc=%s %r" % (p_run.returncode, p_attention))
    q_root, q_vault = fresh()
    run(q_root, q_vault)
    check(stored(q_root)["months"]["2026-09"]["attention"] == [],
          "attention: a month with no signal stores an empty list")

    # --- review fixes -------------------------------------------------------------------------------
    check(stored(root)["months"]["2026-08"]["sources"]["orchestration"]["undated"] is None
          and stored(root)["months"]["2026-09"]["sources"]["orchestration"]["undated"] == 1,
          "undated: the archive-wide count is stored on the open month and null on a closed one")

    sys.path.insert(0, os.path.join(CLAUDE, "tools"))
    import analyze_eval_archive
    every = analyze_eval_archive.compute(root)
    months_stored = stored(root)["months"]
    check(all(months_stored[m]["sessions"] == analyze_eval_archive.compute(root, month=m)["sessions"]
              for m in months_stored)
          and (data or {}).get("stats", {}).get("sessions") == every["sessions"]
          and sum(months_stored[m]["sessions"]["n"] for m in months_stored) == every["sessions"]["n"]
          and sum(months_stored[m]["sessions"]["domains"].get("meta", {}).get("n", 0) for m in months_stored)
          == next(d["total"] for d in every["domain_perf"] if d["domain"] == "meta"),
          "sessions: each stored month block equals compute(month=), the report's all-time block sums from them, "
          "and the report's domain totals agree with the months",
          repr(sorted(months_stored)))

    messy = list(PLANTED) + [entry(11, None, "clean"), entry(12, "2026-09-28T10:00", "clean"),
                             entry(13, "sometime", "correction", "A"), entry(14, "2026-13-45", "clean")]
    m_root, m_vault = plant_root(entries=messy), tempfile.mkdtemp(prefix="evaldash_vault_")
    m_run = run(m_root, m_vault)
    m_row = report_data(m_vault) and next((i for i in report_data(m_vault).get("inputs", [])
                                           if i["source"] == "self-evaluate archive"), {})
    check(m_run.returncode == 0 and "Traceback" not in m_run.stderr and m_row.get("first") == "2026-07-02"
          and m_row.get("last") == "2026-09-28" and m_row.get("age_days") == 0,
          "dates: entries with a null, prose, out-of-range or timestamped date publish; only a real day sets first, last and age",
          "rc=%s %r %r" % (m_run.returncode, m_row, (m_run.stdout + m_run.stderr)[-300:]))
    m_weeks = report_data(m_vault).get("weekly", [])
    check(sum(w["n"] for w in m_weeks) == 11 and m_weeks[-1]["week"] == "2026-W40",
          "dates: the weekly series counts the timestamped entry in its week and drops the undated ones", repr(m_weeks[-2:]))

    for label, today in (("out-of-range month", "2026-13-45"), ("a day the month lacks", "2026-02-30"),
                         ("prose", "yesterday")):
        d_root, d_vault = fresh()
        d_run = run(d_root, d_vault, today=today)
        check(d_run.returncode == 2 and "--today" in d_run.stdout and "Traceback" not in d_run.stderr
              and not os.listdir(d_vault),
              "args: --today with %s exits 2 naming the flag and writes nothing" % label,
              "rc=%s %r %r" % (d_run.returncode, d_run.stdout[-120:], d_run.stderr[-120:]))
    d_root, d_vault = fresh()
    d_run = run(d_root, d_vault, "--refreeze", "2026-13")
    check(d_run.returncode == 2 and "--refreeze" in d_run.stdout, "args: --refreeze with month 13 exits 2",
          "rc=%s" % d_run.returncode)

    bad_registry = write_file("external_models.json", "{not json")
    g_root, g_vault = fresh()
    g_run = run(g_root, g_vault, env=dict(os.environ, HARNESS_MODEL_REGISTRY=bad_registry))
    g_note = read(os.path.join(g_vault, "Self Evaluation Dashboard.md")) if os.path.isfile(
        os.path.join(g_vault, "Self Evaluation Dashboard.md")) else ""
    check(g_run.returncode == 3 and "Traceback" not in g_run.stderr
          and input_row(g_vault, "model registry").get("status") == "unparseable"
          and report_data(g_vault).get("ladder", {}).get("degraded")
          and "Ladder readout: unavailable" in g_note and "model registry: unparseable" in g_run.stdout
          and stored(g_root)["months"]["2026-09"]["ladder"] == [],
          "registry: an unreadable registry lists it as an unparseable input, exits 3 and still publishes the note and report",
          "rc=%s %r" % (g_run.returncode, (g_run.stdout + g_run.stderr)[-300:]))

    r_root, r_vault = fresh()
    run(r_root, r_vault, today="2026-09-28")
    r_before = read(os.path.join(r_root, ".claude", "eval_history.json"))
    r_run = run(r_root, r_vault, "--refreeze", "2026-08", env=dict(os.environ, HARNESS_MODEL_REGISTRY=bad_registry))
    check(r_run.returncode == 2 and "--refreeze" in r_run.stdout and "registry" in r_run.stdout
          and read(os.path.join(r_root, ".claude", "eval_history.json")) == r_before,
          "registry: --refreeze with an unreadable registry exits 2 and leaves the history, so a closed month's ladder is never emptied",
          "rc=%s %r" % (r_run.returncode, r_run.stdout[-200:]))

    legacy = [wf("wf_legacy_%d" % i, "l%d" % i, "medium", "clean", "2026-09-%02d" % (i + 1), model="proof-a",
                 served_model="proof-a", tier="executor") for i in range(8)]
    sessions_root = tempfile.mkdtemp(prefix="evaldash_sessions_")
    for name, run_ids in (("sess-a", (0, 1, 2)), ("sess-b", (3, 4, 5)), ("sess-c", (6, 7))):
        os.makedirs(os.path.join(sessions_root, name, "workflows"))
        for index in run_ids:
            with open(os.path.join(sessions_root, name, "workflows", "wf_legacy_%d.json" % index), "w") as handle:
                handle.write("{}")
    s_root, s_vault = plant_root(ledger=legacy), tempfile.mkdtemp(prefix="evaldash_vault_")
    foreign = tempfile.mkdtemp(prefix="evaldash_cwd_")
    env_reg = dict(os.environ, HARNESS_MODEL_REGISTRY=registry)
    s_run = run(s_root, s_vault, "--sessions-root", sessions_root, env=env_reg, cwd=foreign)
    s_cell = next(iter(report_data(s_vault).get("ladder", {}).get("cells", [])), {})
    check(s_run.returncode == 0 and (s_cell.get("n"), s_cell.get("sessions")) == (8, 3),
          "sessions-root: ledger rows that predate session_id resolve through --sessions-root from a foreign cwd",
          "rc=%s %r %r" % (s_run.returncode, s_cell, (s_run.stdout + s_run.stderr)[-200:]))
    n_run = run(s_root, tempfile.mkdtemp(prefix="evaldash_vault_"), "--sessions-root",
                os.path.join(tempfile.gettempdir(), "evaldash_no_such_sessions"), env=env_reg, cwd=foreign)
    check(n_run.returncode == 2 and "--sessions-root" in n_run.stdout, "sessions-root: a directory that does not exist exits 2",
          "rc=%s %r" % (n_run.returncode, n_run.stdout[-120:]))

    l_root, l_vault = fresh()
    with open(os.path.join(l_root, ".claude", "orchestration_metrics.jsonl"), "a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json\n")
    l_run = run(l_root, l_vault)
    l_row = input_row(l_vault, "orchestration ledger")
    check(l_run.returncode == 3 and l_row.get("status") == "unparseable" and "malformed" in l_row.get("detail", "")
          and "orchestration ledger: unparseable" in l_run.stdout,
          "ledger: a line that cannot be read marks the ledger unparseable and exits 3, not a quietly smaller month",
          "rc=%s %r" % (l_run.returncode, l_row))

    drift = [entry(n, "2026-09-%02d" % n, "clean" if n <= 10 else "correction", None if n <= 10 else "A", skills=("sk",))
             for n in range(1, 21)]
    k_root, k_vault = plant_root(entries=drift, ledger=[]), tempfile.mkdtemp(prefix="evaldash_vault_")
    run(k_root, k_vault, today="2026-09-28")
    open_rows = [r["signal"] for r in stored(k_root)["months"]["2026-09"]["attention"]]
    run(k_root, k_vault, today="2026-10-02")
    closed_rows = [r["signal"] for r in stored(k_root)["months"]["2026-09"]["attention"]]
    check(open_rows == ["skill drift"] and closed_rows == ["skill drift"]
          and "skill drift" in read(os.path.join(k_vault, "Eval Dashboard Archive", "Eval Dashboard 2026-09.md")),
          "attention: the skill-drift row recorded while a month was open survives the run that closes it, in the store and the month note",
          repr((open_rows, closed_rows)))

    all_none = {name: "no move: proof evidence" for name in ("sessions", "guards", "cascades", "pins", "ladder", "review")}
    no_layers = {k: v for k, v in verdict_doc().items() if k != "layers"}
    nl_root, nl_vault = fresh()
    nl_run = run(nl_root, nl_vault, "--verdict", write_file("verdict.json", no_layers))
    check(nl_run.returncode == 0, "test_verdict_without_layers_is_accepted", "rc=%s %r" % (nl_run.returncode, nl_run.stdout[-160:]))
    for label, doc in (("layers", verdict_doc(layers=dict(all_none, extra="move"))),
                       ("layers.guards", verdict_doc(layers=dict(all_none, guards="maybe"))),
                       ("layers.guards", verdict_doc(layers=dict(all_none, guards="no move: "))),
                       ("layers names a move", verdict_doc(moves=[], layers=dict(all_none, pins="move")))):
        l_root, l_vault = fresh()
        l_file = write_file("verdict.json", doc)
        l_bad = run(l_root, l_vault, "--verdict", l_file)
        check(l_bad.returncode == 2 and label in l_bad.stdout and os.path.exists(l_file),
              "test_verdict_with_a_malformed_layers_map_exits_2_naming_%s" % label,
              "rc=%s %r" % (l_bad.returncode, l_bad.stdout[-160:]))
    for label, doc in (("state", verdict_doc(state="one\ntwo")),
                       ("do", verdict_doc(moves=[{"do": "a\nb", "why": "w", "see": "agents"}])),
                       ("why", verdict_doc(moves=[{"do": "d", "why": "w\r\nx", "see": "agents"}])),
                       ("watch", verdict_doc(watch=["a\nb"])),
                       ("milestone", verdict_doc(milestone="m\nn")),
                       ("strengths", verdict_doc(profile={"strengths": ["s\nt"], "weaknesses": []}))):
        n_root, n_vault = fresh()
        n_file = write_file("verdict.json", doc)
        n_bad = run(n_root, n_vault, "--verdict", n_file)
        check(n_bad.returncode == 2 and label in n_bad.stdout and "single-line" in n_bad.stdout and os.path.exists(n_file),
              "verdict: a line break in %r exits 2 naming the field and keeps the file" % label,
              "rc=%s %r" % (n_bad.returncode, n_bad.stdout[-160:]))

    def clean_registry():
        path = plant_registry()
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for key in ("probation", "driverNotes"):
            data["models"][0].pop(key, None)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle)
        return path

    def review_env(registry):
        env = {k: v for k, v in os.environ.items() if k not in ("HARNESS_ESCAPE_LOG", "HARNESS_REVIEW_LEDGER")}
        return dict(env, HARNESS_MODEL_REGISTRY=registry)

    def seat_row(n):
        return {"run": "wf_rev%d" % n, "label": "review:s1", "workflow": "review-fanout", "model": "proof-a",
                "effort": "high", "requested_effort": "high", "state": "done", "cost": 1.0, "qcost": 1.0,
                "run_date": "2026-09-%02d" % (10 + n), "outcome": "clean", "author_model": "proof-b",
                "domain": ["sync"], "review_stage": "pre-merge", "ledger_new": 1, "ledger_join": "joined",
                "mandates": {"lens-a": {"outcome": "rated", "unique": 0, "duplicate": 1}}}

    v_root = plant_root(ledger=LEDGER + [seat_row(n) for n in (1, 2, 3)])
    with open(os.path.join(v_root, ".claude", "escape_log.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"id": "e1", "ts": "2026-09-20T00:00:00Z", "sha": "abcdef12", "paths": ["a/b.py"],
                                 "shouldHaveCaught": "lens-z", "writer": "self_evaluate", "authorModel": "proof-b",
                                 "tier": "executor", "triage": "unrecorded", "domain": ["untagged"],
                                 "evidence": "e"}) + "\n")
    v_vault = tempfile.mkdtemp(prefix="evaldash_vault_")
    v_run = run(v_root, v_vault, env=review_env(clean_registry()))
    v_data = report_data(v_vault) if v_run.returncode in (0, 3) else {}
    v_review = v_data.get("review", {})
    v_note = read(os.path.join(v_vault, "Self Evaluation Dashboard.md")) if v_run.returncode in (0, 3) else ""
    v_html = read(os.path.join(v_vault, "EVAL-DASHBOARD.html")) if v_run.returncode in (0, 3) else ""
    v_kinds = sorted(p["kind"] for p in v_review.get("proposals", []))
    check(v_run.returncode == 0 and v_review.get("escapes", {}).get("rows") == 1
          and v_kinds == ["sensitivity-tag", "triage-rule"] and v_review.get("join", {}).get("joined") == 3
          and "addTab('review','Review')" in v_html
          and "review proposals: 2 — see the Review tab" in v_note
          and input_row(v_vault, "escape log").get("status") == "ok" and input_row(v_vault, "escape log").get("rows") == 1,
          "review: the Review tab renders escapes, joins and proposals from planted data, and the note points at it",
          "rc=%s %r %r" % (v_run.returncode, v_kinds, v_run.stdout[-200:]))

    a_root, a_vault = fresh()
    a_run = run(a_root, a_vault, env=review_env(clean_registry()))
    a_data = report_data(a_vault) if a_run.returncode == 0 else {}
    a_note = read(os.path.join(a_vault, "Self Evaluation Dashboard.md")) if a_run.returncode == 0 else ""
    check(a_run.returncode == 0 and input_row(a_vault, "escape log").get("status") == "absent"
          and input_row(a_vault, "review ledger").get("status") == "absent"
          and a_data.get("review", {}).get("escapes", {}).get("rows") == 0
          and a_data.get("review", {}).get("escapes", {}).get("bytes") is None
          and "review proposals" not in a_note,
          "review: an absent escape log and ledger list absent Inputs rows, exit 0 and no note line",
          "rc=%s %r" % (a_run.returncode, a_run.stdout[-200:]))

    c_root = plant_root(ledger=LEDGER)
    with open(os.path.join(c_root, ".claude", "escape_log.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json\n")
    os.makedirs(os.path.join(c_root, ".claude", "logs"), exist_ok=True)
    with open(os.path.join(c_root, ".claude", "logs", "review_ledger.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json\n")
    c_vault = tempfile.mkdtemp(prefix="evaldash_vault_")
    c_run = run(c_root, c_vault, env=review_env(clean_registry()))
    c_ok = c_run.returncode in (0, 3)
    check(c_ok and input_row(c_vault, "escape log").get("status") == "unparseable"
          and input_row(c_vault, "review ledger").get("status") == "unparseable",
          "review: a corrupt escape-log line and a corrupt ledger line list unparseable Inputs rows, never ok",
          "rc=%s escape=%r ledger=%r" % (c_run.returncode, input_row(c_vault, "escape log") if c_ok else None,
                                         input_row(c_vault, "review ledger") if c_ok else None))

    sys.path.insert(0, os.path.join(CLAUDE, "tools"))
    import eval_dashboard
    guide = read(os.path.join(CLAUDE, "commands", "eval_dashboard.md"))
    check("review" in eval_dashboard.TABS and all(tab.capitalize() in guide for tab in eval_dashboard.TABS)
          and "nine tabs" in guide and "## Review calibration" in guide,
          "review: every TABS entry appears in eval_dashboard.md, which names nine tabs and a Review calibration section",
          repr(eval_dashboard.TABS))

    total = len(ran)
    print("\n%d/%d cases pass" % (total - len(failures), total))
    for line in failures:
        print("  FAIL " + line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
