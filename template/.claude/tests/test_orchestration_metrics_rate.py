"""Re-runnable proof for orchestration_metrics.py's cheap-rating surface.

`--rate` writes one exact `run_id:label` verdict, `--accept-rest` marks the rater's consumed and
accepted work clean, saturated lens groups are sampled 1-in-5, and a large pending file names the
sessions to archive without deleting anything. Every case monkeypatches the module's path constants
to a temp tree; the live verdict file and archive are never touched.

    python3 .claude/tests/test_orchestration_metrics_rate.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _om_session_fixture as fixture  # noqa: E402
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")

spec = importlib.util.spec_from_file_location("orchestration_metrics", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)

MODEL = "claude-opus-5-5"


def write_run(wdir, run_id, agents, workflow_name=None, pins=True, phases=None):
    """agents: [(label, state)] or [(label, state, model)]; effort pinned via a PINS log line.
    phases: {label: phaseTitle} as the engine records it."""
    progress, pin_map = [], {}
    for i, agent in enumerate(agents):
        label, state = agent[0], agent[1]
        model = agent[2] if len(agent) > 2 else MODEL
        progress.append({"type": "workflow_agent", "agentId": f"{run_id}-a{i}", "label": label,
                         "state": state, "model": model})
        if phases and label in phases:
            progress[-1]["phaseTitle"] = phases[label]
        pin_map[label] = "medium"
    run = {"runId": run_id, "workflowProgress": progress,
           "logs": ["PINS " + json.dumps(pin_map)] if pins else []}
    if workflow_name:
        run["workflowName"] = workflow_name
    with open(os.path.join(wdir, f"{run_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh)


def write_archive(rows):
    with open(om.ARCHIVE, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def verdicts():
    return om._read_json_object(om.PENDING_VERDICTS) or {}


def run_main(argv):
    real = sys.argv
    sys.argv = [MODULE_PATH] + argv
    buf = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(err):
            rc = om.main()
    except SystemExit as exc:
        rc = exc.code
    finally:
        sys.argv = real
    return rc, buf.getvalue() + err.getvalue()


# The crc32 1-in-5 pick for run wf_s, labels sat:lens-<i>, recorded once. A change to the hash, the
# key spelling or SAMPLE_EVERY changes which pairs are rated and must fail here.
KEPT_WF_S = {9, 13, 14, 17, 18, 22, 25, 29, 30, 31}


def kept(run, label):
    return run == "wf_s" and int(label.rsplit("-", 1)[1]) in KEPT_WF_S


def rated_rows(lens, n, outcome="clean", model=MODEL, **extra):
    return [dict({"run": f"old_{lens}_{i}", "label": f"{lens}-{i}", "model": model, "workflow": "review-fanout",
                  "phase": "Review", "effort": "medium", "outcome": outcome}, **extra) for i in range(n)]


def real_session_cases(cases):
    """D3/D4 of the 2026-10-18 unratable-dispatch defect: a refusal that guides, and one collection per call.
    The session is cut from real launches and records of session 30b7e19d plus 12 native pending dispatches."""
    root = tempfile.mkdtemp(prefix="ommrate_real_")
    session = fixture.materialize(root)
    write_run(os.path.join(session, "workflows"), "wf_n", [(f"native:{i}", "completed") for i in range(12)])
    om.PENDING_VERDICTS = os.path.join(root, "orchestration_verdicts.json")
    om.LEGACY_PENDING_VERDICTS = os.path.join(root, "legacy_verdicts.json")
    om.ARCHIVE = os.path.join(root, "orchestration_metrics.jsonl")
    write_archive([])
    write_json(om.PENDING_VERDICTS, {})
    common = ["--session", session]
    real_collect = om.collect_session_sidecar
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real_collect(*args, **kwargs)

    om.collect_session_sidecar = counting
    try:
        sidecar_keys = {r["label"]: f"{r['run']}:{r['label']}" for r in real_collect(session, [])}

        def key_of(label):
            return sidecar_keys.get(label, "<no sidecar row for %s>" % label)
        gate_record = fixture.record_path(root, "exec_out", "exec-gatefix")

        del calls[:]
        rc, out = run_main(common + ["--rate", "codify", "clean"])
        cases.append(("D3 a refusal lists the pending keys that match the request",
                      rc == 1 and all(key_of(n) in out for n in
                                      ("codify-4", "codify-5", "codify-exec-watch", "codify-exec-floor"))))
        cases.append(("D3 a refusal counts the pending dispatches it does not list",
                      "13 other pending" in out and "native:0" not in out and key_of("exec-gatefix") not in out))
        cases.append(("D4 a refused --rate reads the session's sidecar records once", len(calls) == 1))

        rc, out = run_main(common + ["--rate", "nothing-like-it", "clean"])
        cases.append(("D3 a request matching nothing lists the first pending keys and the count it hides",
                      rc == 1 and "17 pending" in out and "7 more" in out))

        rc, out = run_main(common + ["--rate", gate_record, "clean", "--effort", "high"])
        key = key_of("exec-gatefix")
        cases.append(("D2 a stopped run rates by its record path as a dispatch with an outcome and no tokens",
                      rc == 0 and verdicts().get(key) == {"outcome": "clean", "effort": "high"}))
        rc, out = run_main(common + ["--rate", "exec-gatefix", "clean"])
        cases.append(("D2 a stopped run has no effort pin, so --rate asks for one",
                      rc == 1 and "--effort" in out))
        write_json(om.PENDING_VERDICTS, {})

        batch = os.path.join(root, "batch.json")
        write_json(batch, [
            {"label": "codify-4", "outcome": "clean"},
            {"label": "codify-5", "outcome": "defects", "note": "rework needed", "shape": "over"},
            {"label": "no-such-dispatch", "outcome": "clean"},
            {"label": "exec-gatefix", "outcome": "discarded", "note": "stopped", "effort": "high"}])
        del calls[:]
        rc, out = run_main(common + ["--rate-batch", batch])
        got = verdicts()
        cases.append(("D4 --rate-batch rates every valid entry in one call",
                      got.get(key_of("codify-4")) == "clean"
                      and got.get(key_of("codify-5"), {}).get("outcome") == "defects"
                      and got.get(key_of("codify-5"), {}).get("shape") == "over"
                      and got.get(key_of("exec-gatefix")) == {"outcome": "discarded", "note": "stopped", "effort": "high"}))
        cases.append(("D4 --rate-batch names each refusal beside the rated lines and exits 1",
                      rc == 1 and 'REFUSED: "no-such-dispatch"' in out and f"rated {key_of('codify-4')}: clean" in out
                      and len(got) == 3))
        cases.append(("D4 --rate-batch reads the session's sidecar records once for all entries", len(calls) == 1))

        seat = {"plc-a": {"outcome": "findings", "unique": 2, "duplicate": 0}}
        for form, shape in (("object", seat), ("JSON string", json.dumps(seat))):
            write_json(om.PENDING_VERDICTS, {})
            write_json(batch, [{"label": "codify-4", "outcome": "clean", "mandates": shape}])
            rc, out = run_main(common + ["--rate-batch", batch])
            cases.append((f"D5 a --rate-batch entry's mandates as a {form} is written as the panel-seat shape",
                          rc == 0 and verdicts().get(key_of("codify-4"), {}).get("mandates") == seat))
        write_json(om.PENDING_VERDICTS, {})
        write_json(batch, [{"label": "codify-4", "outcome": "clean", "mandates": ["plc-a"]}])
        rc, out = run_main(common + ["--rate-batch", batch])
        cases.append(("D5 a batch entry with a malformed mandates names the shape and that an entry takes it",
                      rc == 1 and "{mandate: {outcome: findings|clean|not-reached, unique, duplicate}}" in out
                      and "--rate-batch" in out and verdicts() == {}))
        write_json(om.PENDING_VERDICTS, {})
        rc, out = run_main(common + ["--rate", "wf_1:review:x", "clean", "--mandates", '{"x": "findings"}'])
        cases.append(("a --rate --mandates refusal names the object shape and one valid call",
                      rc == 1 and "{mandate: {outcome: findings|clean|not-reached, unique, duplicate}}" in out
                      and "accepts:" in out and "example:" in out and verdicts() == {}))
        flat = " ".join(run_main(["--help"])[1].split())
        cases.append(("D5 --help states the mandates shape beside --rate-batch",
                      "mandates (an object" in flat
                      and "{mandate: {outcome: findings|clean|not-reached, unique, duplicate}}" in flat))

        write_json(om.PENDING_VERDICTS, {})
        write_json(batch, [{"label": "codify-4", "outcome": "clean"}, {"label": "codify-4", "outcome": "defects", "note": "n"}])
        rc, out = run_main(common + ["--rate-batch", batch])
        cases.append(("D4 a dispatch named twice in one batch is rated once and the repeat is refused",
                      rc == 1 and verdicts() == {key_of("codify-4"): "clean"}))

        for bad in ('{"label": "codify-4"}', '[{"label": "codify-4"}]', "[1]", "not json"):
            write_json(om.PENDING_VERDICTS, {})
            with open(batch, "w", encoding="utf-8") as fh:
                fh.write(bad)
            rc, out = run_main(common + ["--rate-batch", batch])
            cases.append((f"D4 a malformed batch {bad!r} is refused and writes nothing",
                          rc == 1 and "REFUSED" in out and verdicts() == {}))
        rc, out = run_main(common + ["--rate-batch", os.path.join(root, "absent.json")])
        cases.append(("D4 a missing batch file is refused", rc == 1 and "REFUSED" in out))
    finally:
        om.collect_session_sidecar = real_collect


def main():
    cases = []
    root = tempfile.mkdtemp(prefix="ommrate_")
    om.PENDING_VERDICTS = os.path.join(root, "orchestration_verdicts.json")
    om.LEGACY_PENDING_VERDICTS = os.path.join(root, "legacy_verdicts.json")
    om.ARCHIVE = os.path.join(root, "orchestration_metrics.jsonl")
    write_archive([])

    session = os.path.join(root, "session")
    wdir = os.path.join(session, "workflows")
    os.makedirs(wdir)
    write_run(wdir, "wf_1", [("review:x", "completed"), ("review:solo", "completed")])
    write_run(wdir, "wf_2", [("review:x", "completed"), ("author:run", "running"),
                             ("author:fail", "failed")])
    common = ["--session", session]

    # --- A1: --rate resolves labels to exact keys ---------------------------------
    write_json(om.PENDING_VERDICTS, {})
    rc, _ = run_main(common + ["--rate", "review:solo", "clean"])
    cases.append(("bare unique label writes the exact run:label key",
                  rc == 0 and verdicts() == {"wf_1:review:solo": "clean"}))

    rc, out = run_main(common + ["--rate", "review:x", "clean"])
    cases.append(("ambiguous bare label is refused and names each exact key",
                  rc == 1 and "wf_1:review:x" in out and "wf_2:review:x" in out
                  and "wf_1:review:x" not in verdicts()))

    rc, _ = run_main(common + ["--rate", "wf_2:review:x", "clean"])
    cases.append(("exact run:label is written as given",
                  rc == 0 and verdicts().get("wf_2:review:x") == "clean"))

    rc, out = run_main(common + ["--rate", "review:nope", "clean"])
    cases.append(("unknown label is refused", rc == 1 and "review:nope" not in json.dumps(verdicts())))

    rc, out = run_main(common + ["--rate", "wf_1:review:x", "bogus"])
    cases.append(("unknown outcome is refused", rc == 1 and "wf_1:review:x" not in verdicts()))

    # --- A2: exceptions carry a note ----------------------------------------------
    rc, out = run_main(common + ["--rate", "wf_1:review:x", "defects"])
    cases.append(("non-clean outcome without --note is refused",
                  rc == 1 and "wf_1:review:x" not in verdicts()))
    rc, _ = run_main(common + ["--rate", "wf_1:review:x", "defects", "--note", "see D13", "--probe"])
    cases.append(("non-clean with --note writes a dict carrying note and probe",
                  rc == 0 and verdicts().get("wf_1:review:x")
                  == {"outcome": "defects", "note": "see D13", "probe": True}))

    row = {"run": "wf_1", "label": "review:x"}
    om.apply_verdict(row, {"outcome": "defects", "note": "see D13", "probe": True})
    cases.append(("apply_verdict keeps note and probe from the dict form",
                  row.get("outcome") == "defects" and row.get("note") == "see D13"
                  and row.get("probe") is True))

    # --- Shape is an explicit judgment, independent of outcome ------------------------
    for shape in ("right", "over", "under"):
        try:
            rc, _ = run_main(common + ["--rate", "review:solo", "clean", "--shape", shape,
                                      "--note", "same result at less width"])
        except SystemExit as exc:
            rc = exc.code
        ent = verdicts().get("wf_1:review:solo")
        cases.append((f"--shape {shape} stores its value and note",
                      rc == 0 and isinstance(ent, dict) and ent.get("shape") == shape
                      and ent.get("note") == "same result at less width"))
        row = {"run": "wf_1", "label": "review:solo"}
        om.apply_verdict(row, ent)
        cases.append((f"shape {shape} reaches the archived row", row.get("shape") == shape))
    before = verdicts()
    try:
        rc, _ = run_main(common + ["--rate", "review:solo", "clean", "--shape", "oversized"])
    except SystemExit as exc:
        rc = exc.code
    cases.append(("invalid --shape is refused without writing", rc == 2 and verdicts() == before))
    try:
        rc = om.rate(session, "review:solo", "clean", shape="bogus")
    except TypeError:
        rc = None
    cases.append(("rate API rejects invalid shape without writing", rc == 1 and verdicts() == before))
    rc, _ = run_main(common + ["--rate", "review:solo", "clean", "--note", "read"])
    cases.append(("rating without shape stays unjudged",
                  rc == 0 and "shape" not in verdicts()["wf_1:review:solo"]))

    # --- A2: --accept-rest ------------------------------------------------------------
    write_json(om.PENDING_VERDICTS, {"wf_1:review:x": "rework"})
    rc, out = run_main(common + ["--accept-rest"])
    v = verdicts()
    cases.append(("accept-rest writes clean for completed pending pairs only",
                  rc == 0 and v.get("wf_1:review:solo") == "clean"
                  and v.get("wf_2:review:x") == "clean"))
    cases.append(("accept-rest never overwrites an existing verdict",
                  v.get("wf_1:review:x") == "rework"))
    cases.append(("accept-rest leaves running and failed agents pending",
                  "wf_2:author:run" not in v and "wf_2:author:fail" not in v))

    # --- A1: sidecar rows rate by exact key; unresolved effort needs --effort; panel metadata --
    real_side = om.collect_session_sidecar
    om.collect_session_sidecar = lambda *a, **k: [
        {"run": "sidecar-77-side:x", "label": "side:x", "state": "completed", "effort": "max"}]
    try:
        write_json(om.PENDING_VERDICTS, {})
        rc, _ = run_main(["--session", session, "--rate", "side:x", "clean"])
    finally:
        om.collect_session_sidecar = real_side
    cases.append(("a sidecar label is written as its exact run:label key",
                  rc == 0 and verdicts() == {"sidecar-77-side:x:side:x": "clean"}))

    write_run(wdir, "wf_nopin", [("exec:np", "completed")], pins=False)
    rc, _ = run_main(common + ["--rate", "exec:np", "clean"])
    cases.append(("an unresolved effort pin without --effort is refused",
                  rc == 1 and "wf_nopin:exec:np" not in verdicts()))
    rc, _ = run_main(common + ["--rate", "exec:np", "clean", "--effort", "high"])
    cases.append(("--effort is written with the outcome",
                  rc == 0 and verdicts().get("wf_nopin:exec:np") == {"outcome": "clean", "effort": "high"}))

    mandates = {"plc-a": {"outcome": "findings", "unique": 2, "duplicate": 0}}
    write_run(wdir, "wf_panel", [("review:seat", "completed")], workflow_name="review-fanout")
    rc, _ = run_main(common + ["--rate", "review:seat", "clean", "--tier", "executor",
                               "--mandates", json.dumps(mandates)])
    ent = verdicts().get("wf_panel:review:seat")
    cases.append(("--tier and --mandates are written in the panel-seat shape",
                  rc == 0 and ent == {"outcome": "clean", "tier": "executor", "mandates": mandates}))
    row = {"run": "wf_panel", "label": "review:seat"}
    om.apply_verdict(row, ent)
    cases.append(("panel metadata reaches the archived row",
                  row.get("mandates") == mandates and row.get("tier") == "executor"))

    write_json(om.PENDING_VERDICTS, {})
    rc, _ = run_main(common + ["--rate", "review:seat", "clean", "--tier", "validation"])
    cases.append(("--tier validation is written as its role tier fanout",
                  rc == 0 and verdicts().get("wf_panel:review:seat") == {"outcome": "clean", "tier": "fanout"}))
    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--rate", "review:seat", "clean", "--tier", "Standard"])
    cases.append(("--tier Standard is refused with the legal list and writes nothing",
                  rc == 1 and "orchestrator, architect, executor, fanout, scout" in out and verdicts() == {}))
    recorded = {"run": "wf_panel", "label": "review:seat", "tier": "architect"}
    om.apply_verdict(recorded, {"outcome": "clean", "tier": "scout"})
    cases.append(("a tier recorded at launch survives a disagreeing verdict tier",
                  recorded.get("tier") == "architect" and recorded.get("outcome") == "clean"))
    bare = {"run": "wf_panel", "label": "review:seat"}
    om.apply_verdict(bare, {"outcome": "clean", "tier": "scout", "note": "n"})
    cases.append(("a verdict tier fills a row with no recorded tier", bare.get("tier") == "scout"))

    write_run(wdir, "wf_panel2", [("review:a+b", "completed"), ("review:single", "completed"),
                                  ("review:consolidate", "completed"), ("review:verify", "completed"),
                                  ("review:fold", "completed")],
              workflow_name="review-fanout",
              phases={"review:a+b": "Review", "review:single": "Review", "review:consolidate": "Merge",
                      "review:verify": "Verify", "review:fold": "Fold"})
    write_run(wdir, "wf_plain", [("exec:job", "completed")], workflow_name="dispatch")
    write_run(wdir, "wf_merged_plain", [("lens-a+lens-b", "completed")], workflow_name="dispatch")
    write_json(om.PENDING_VERDICTS, {})
    rc, out = run_main(common + ["--accept-rest", "--note", "read in full"])
    v = verdicts()
    cases.append(("accept-rest never judges shape right",
                  all(not isinstance(ent, dict) or "shape" not in ent for ent in v.values())))
    cases.append(("accept-rest skips merged panel seats and ?-effort pairs",
                  rc == 0 and "wf_panel2:review:a+b" not in v and "wf_nopin:exec:np" not in v
                  and "wf_merged_plain:lens-a+lens-b" not in v))
    cases.append(("accept-rest leaves a single-lens review-fanout seat pending",
                  "wf_panel2:review:single" not in v))
    cases.append(("accept-rest names each review seat with its --rate --mandates form",
                  "--rate wf_panel2:review:single <outcome> --mandates '<json>'" in out
                  and "--rate wf_panel2:review:a+b <outcome> --mandates '<json>'" in out))
    cases.append(("accept-rest still rates an ordinary dispatch and writes its --note",
                  v.get("wf_plain:exec:job") == {"outcome": "clean", "note": "read in full"}))
    cases.append(("accept-rest rates the review engine's Merge-phase consolidator",
                  v.get("wf_panel2:review:consolidate") == {"outcome": "clean", "note": "read in full"}))
    cases.append(("accept-rest rates the review engine's Verify and Fold stages as dispatches, not seats",
                  v.get("wf_panel2:review:verify") == {"outcome": "clean", "note": "read in full"}
                  and v.get("wf_panel2:review:fold") == {"outcome": "clean", "note": "read in full"}))
    cases.append(("accept-rest names what it skipped", "review:a+b" in out and "exec:np" in out))

    # --effort and --mandates are validated before anything is written.
    before = verdicts()
    for bad in (["--effort", "hi"], ["--effort", "high", "--mandates", "not json"],
                ["--effort", "high", "--mandates", "[1]"],
                ["--effort", "high", "--mandates", json.dumps({"m": "not_reached"})],
                ["--effort", "high", "--mandates", json.dumps({"m": {"outcome": "not_reached"}})]):
        rc, _ = run_main(common + ["--rate", "exec:np", "clean"] + bad)
        cases.append((f"--rate refuses {' '.join(bad)[:48]} and writes nothing",
                      rc == 1 and verdicts() == before))
    rc, _ = run_main(common + ["--rate", "exec:np", "clean", "--effort", "opus/high"])
    cases.append(("--effort is normalized to its bare pin",
                  rc == 0 and verdicts().get("wf_nopin:exec:np") == {"outcome": "clean", "effort": "high"}))
    for path in ("wf_nopin", "wf_panel", "wf_panel2", "wf_plain", "wf_merged_plain"):
        os.remove(os.path.join(wdir, path + ".json"))

    # --- A4: saturation ---------------------------------------------------------------
    # S46: "The predicate checks workflow and group, not actual lens-seat phase."
    # Saturation fixtures must identify source seats; unknown/auxiliary phases stay owed.
    cases.append(("lens strips a trailing instance number",
                  om.lens_of("p5host:plan-fold-1") == "p5host:plan-fold"
                  and om.lens_of("review:consolidate") == "review:consolidate"))

    sat = om.saturated_groups(rated_rows("sat:lens", 10))
    cases.append(("10 clean rated rows saturate the group", ("sat:lens", MODEL, "medium") in sat))
    sat = om.saturated_groups(rated_rows("sat:lens", 9))
    cases.append(("9 rated rows do not saturate", ("sat:lens", MODEL, "medium") not in sat))
    rows = rated_rows("sat:lens", 10)
    rows[7]["outcome"] = "defects"
    cases.append(("a non-clean row among the last 5 unsaturates",
                  ("sat:lens", MODEL, "medium") not in om.saturated_groups(rows)))
    rows = rated_rows("sat:lens", 10)
    rows[0]["outcome"] = "defects"
    cases.append(("an old non-clean row outside the last 5 does not unsaturate",
                  ("sat:lens", MODEL, "medium") in om.saturated_groups(rows)))
    rows = rated_rows("sat:lens", 10)
    rows[0]["mandates"] = {"m": {"outcome": "clean"}}
    cases.append(("a group with panel mandates never saturates",
                  ("sat:lens", MODEL, "medium") not in om.saturated_groups(rows)))
    rows = rated_rows("sat:lens", 10) + [dict(r, outcome="unrated") for r in rated_rows("sat:lens", 3)]
    cases.append(("unrated rows do not count and do not break saturation",
                  ("sat:lens", MODEL, "medium") in om.saturated_groups(rows)))
    cases.append(("a non-lens workflow never samples, even in a saturated group",
                  not om.sampled_out({"run": "wf_s", "label": "sat:lens-0", "model": MODEL,
                                      "effort": "medium", "workflow": "dispatch"},
                                     om.saturated_groups(rated_rows("sat:lens", 10)))))
    cases.append(("dispatch-workflow ratings never saturate a group",
                  not om.saturated_groups([dict(r, workflow="dispatch") for r in rated_rows("sat:lens", 10)])))
    cases.append(("a new model id is a new group",
                  ("sat:lens", "claude-opus-6", "medium") not in om.saturated_groups(rated_rows("sat:lens", 10))))

    # Pending pairs in a saturated group: kept 1-in-5 by crc32, the rest sampled out.
    sat_session = os.path.join(root, "sat_session")
    swdir = os.path.join(sat_session, "workflows")
    os.makedirs(swdir)
    labels = [f"sat:lens-{i}" for i in range(40)]
    # explore-fanout samples like review-fanout but has no review seats, so --accept-rest may rate it.
    write_run(swdir, "wf_s", [(lab, "completed") for lab in labels], workflow_name="explore-fanout",
              phases={lab: "Explore" for lab in labels})
    write_archive(rated_rows("sat:lens", 10))
    write_json(om.PENDING_VERDICTS, {})
    detail = om.pending_detail(sat_session)
    expect_kept = {("wf_s", f"sat:lens-{i}") for i in KEPT_WF_S}
    cases.append(("fixture has both kept and sampled-out pairs",
                  0 < len(expect_kept) < len(labels)))
    cases.append(("pending keeps exactly the crc32 1-in-5 sample",
                  set(detail["remaining"]) == expect_kept))
    cases.append(("sampled-out pairs are reported as a set, not debt",
                  set(detail["sampled_out"]) == {("wf_s", lab) for lab in labels} - expect_kept))
    rc, out = run_main(["--session", sat_session, "--pending"])
    cases.append(("--pending counts only the sample",
                  out.startswith(f"unrated dispatches: {len(expect_kept)}")))

    run_main(["--session", sat_session, "--accept-rest"])
    v = verdicts()
    cases.append(("accept-rest does not write sampled-out pairs",
                  set(v) == {f"{r}:{l}" for r, l in expect_kept}))

    # Archive: a sampled-out pair with no verdict archives unrated + sampled_out.
    write_json(om.PENDING_VERDICTS, {})
    one_out = next(lab for lab in labels if not kept("wf_s", lab))
    one_in = next(lab for lab in labels if kept("wf_s", lab))
    write_json(om.PENDING_VERDICTS, {f"wf_s:{one_in}": "clean"})
    archived = []
    real_collect, real_archive, real_report = om.collect, om._archive_rows_atomic, om.report
    om.collect = lambda *_: [
        {"run": "wf_s", "label": one_out, "model": MODEL, "effort": "medium", "state": "completed",
         "workflow": "review-fanout", "phase": "Review"},
        {"run": "wf_s", "label": one_in, "model": MODEL, "effort": "medium", "state": "completed",
         "workflow": "review-fanout", "phase": "Review"},
    ]
    om._archive_rows_atomic = lambda rows: (archived.extend(rows) or (rows, 0, []))
    om.report = lambda *_: None
    try:
        rc, _ = run_main(["--session", sat_session, "--archive"])
    finally:
        om.collect, om._archive_rows_atomic, om.report = real_collect, real_archive, real_report
    by = {r["label"]: r for r in archived}
    cases.append(("archive tags a sampled-out unrated row",
                  rc == 0 and by.get(one_out, {}).get("outcome") == "unrated"
                  and by[one_out].get("sampled_out") is True))
    cases.append(("a rated row never carries sampled_out",
                  by.get(one_in, {}).get("outcome") == "clean" and "sampled_out" not in by[one_in]))

    write_json(om.PENDING_VERDICTS, {})
    archived = []
    om.collect = lambda *_: [
        {"run": "wf_s", "label": one_out, "model": MODEL, "effort": "medium", "state": "completed",
         "workflow": "review-fanout", "phase": "Review"}]
    om._archive_rows_atomic = lambda rows: (archived.extend(rows) or (rows, 0, []))
    om.report = lambda *_: None
    try:
        rc, _ = run_main(["--session", sat_session, "--archive"])
    finally:
        om.collect, om._archive_rows_atomic, om.report = real_collect, real_archive, real_report
    cases.append(("an all-sampled-out run with no verdicts still archives",
                  rc == 0 and [r.get("sampled_out") for r in archived] == [True]))

    # Benchmark instrument stages never reach the archive, whatever the verdict file says.
    write_json(om.PENDING_VERDICTS, {"wf_real:exec:a": "clean", "wf_bench:cell:1": "clean"})
    archived = []
    om.collect = lambda *_: [
        {"run": "wf_real", "label": "exec:a", "workflow": "dispatch", "model": MODEL, "effort": "medium",
         "state": "completed"},
        {"run": "wf_bench", "label": "cell:1", "workflow": "harness-bloat-probe", "model": MODEL, "effort": "medium",
         "state": "completed"}]
    om._archive_rows_atomic = lambda rows: (archived.extend(rows) or (rows, 0, []))
    om.report = lambda *_: None
    try:
        rc, out = run_main(["--session", sat_session, "--archive"])
    finally:
        om.collect, om._archive_rows_atomic, om.report = real_collect, real_archive, real_report
    cases.append(("a measurement-workflow row is never archived",
                  rc == 0 and [r["run"] for r in archived] == ["wf_real"] and "harness-bloat-probe" in out))

    # --- A5: a large pending file names the sessions to archive and deletes nothing -----
    projects = os.path.join(root, "projects", "proj")
    other = os.path.join(projects, "other-sess")
    os.makedirs(os.path.join(other, "workflows"))
    write_run(os.path.join(other, "workflows"), "wf_other", [("review:o", "completed")])
    big = {f"k{i}": "clean" for i in range(200)}
    big["wf_other:review:o"] = "clean"
    write_json(om.PENDING_VERDICTS, big)
    om._candidate_project_dirs = lambda *a, **k: [projects]
    rc, out = run_main(["--session", session, "--pending"])
    cases.append(("--pending past 200 keys names the other session and its archive command",
                  "other-sess" in out and "--session" in out and "1 verdict" in out))
    cases.append(("the warning deletes nothing", verdicts() == big))
    cases.append(("the warning counts keys no other session can drain", "200 key(s) belong to" in out))
    write_json(om.PENDING_VERDICTS, {f"k{i}": "clean" for i in range(200)})
    rc, out = run_main(["--session", session, "--pending"])
    cases.append(("--pending at 200 keys prints no warning", "pending verdict file" not in out))

    # --- A6: a review seat's clean/defects is computed from its invalid and fabricated counts ------
    solo = "wf_1:review:solo"

    def seat(mandates, outcome="clean", note=None, extra=()):
        write_json(om.PENDING_VERDICTS, {})
        argv = common + ["--rate", "review:solo", outcome, "--mandates", json.dumps(mandates)] + list(extra)
        if note is not None:
            argv += ["--note", note]
        return run_main(argv)

    nine = {"lens-a": {"outcome": "findings", "unique": 20, "duplicate": 5, "invalid": 9}}
    rc, out = seat(nine)
    cases.append(("9 invalid of 25 findings records defects with the precision note, passed clean",
                  rc == 0 and verdicts().get(solo) == {"outcome": "defects", "mandates": nine,
                                                       "note": "precision: 9 of 25 findings invalid, 0 fabricated"}))
    cases.append(("the printed line names the computed outcome and the passed one",
                  "rated wf_1:review:solo: defects (precision: 9 of 25 invalid, 0 fabricated); passed clean" in out))

    rc, _ = seat({"lens-a": {"outcome": "findings", "unique": 1, "duplicate": 0, "invalid": 0, "fabricated": 1}})
    cases.append(("1 fabricated finding and 0 invalid records defects",
                  rc == 0 and verdicts().get(solo, {}).get("outcome") == "defects"))

    quarter = {"lens-a": {"outcome": "findings", "unique": 15, "duplicate": 5, "invalid": 5}}
    rc, _ = seat(quarter)
    cases.append(("exactly 1 in 4 invalid (5 of 20) records clean",
                  rc == 0 and verdicts().get(solo) == {"outcome": "clean", "mandates": quarter}))

    over = {"lens-a": {"outcome": "findings", "unique": 2, "duplicate": 1, "invalid": 4}}
    rc, out = seat(over)
    cases.append(("invalid greater than unique plus duplicate is refused and writes nothing",
                  rc == 1 and "REFUSED" in out and solo not in verdicts()))

    for bad in (-1, "2", 1.5):
        rc, out = seat({"lens-a": {"outcome": "findings", "unique": 4, "duplicate": 0, "invalid": bad}})
        cases.append((f"invalid={bad!r} is refused and writes nothing",
                      rc == 1 and "REFUSED" in out and solo not in verdicts()))

    rc, _ = seat(nine, outcome="rework", note="see D5")
    cases.append(("passed rework with counts keeps rework",
                  rc == 0 and verdicts().get(solo, {}).get("outcome") == "rework"))

    rc, _ = seat(nine, outcome="defects")
    cases.append(("passed defects with counts needs no --note and records the computed defects",
                  rc == 0 and verdicts().get(solo, {}).get("outcome") == "defects"))

    rc, _ = seat(quarter, outcome="defects")
    cases.append(("passed defects with counts that compute clean records clean",
                  rc == 0 and verdicts().get(solo, {}).get("outcome") == "clean"))

    write_json(om.PENDING_VERDICTS, {})
    rc, _ = run_main(common + ["--rate", "review:solo", "clean", "--mandates",
                               json.dumps({"lens-a": {"outcome": "clean", "unique": 0, "duplicate": 1}})])
    cases.append(("counts absent: the passed outcome stands with no precision note",
                  rc == 0 and verdicts().get(solo) == {"outcome": "clean",
                                                       "mandates": {"lens-a": {"outcome": "clean", "unique": 0,
                                                                               "duplicate": 1}}}))

    write_json(om.PENDING_VERDICTS, {})
    rc, _ = run_main(common + ["--rate", "review:solo", "clean", "--measurement"])
    cases.append(("--measurement writes a verdict entry carrying measurement true",
                  rc == 0 and verdicts().get(solo) == {"outcome": "clean", "measurement": True}))

    # --- A7: same-labelled sidecar dispatches are told apart by their record paths ------
    real_side = om._unarchived_sidecar_rows
    twins = [{"run": f"sidecar-{n}", "label": "review-fold-1", "source": "sidecar", "model": MODEL,
              "effort": "high", "state": "completed",
              "record_path": os.path.join(root, f"rt{n}", "out", "review-fold-1.record.json")} for n in (5, 6)]
    om._unarchived_sidecar_rows = lambda *a, **k: [dict(t) for t in twins]
    try:
        write_json(om.PENDING_VERDICTS, {})
        rc, out = run_main(common + ["--rate", "review-fold-1", "clean"])
        cases.append(("an ambiguous label lists each candidate with its record path",
                      rc == 1 and all(t["record_path"] in out for t in twins)))
        rc, out = run_main(common + ["--rate", twins[1]["record_path"], "clean"])
        cases.append(("a record path rates the one dispatch that wrote it",
                      rc == 0 and verdicts().get("sidecar-6:review-fold-1") in ("clean", {"outcome": "clean"})
                      and "sidecar-5:review-fold-1" not in verdicts()))
    finally:
        om._unarchived_sidecar_rows = real_side

    real_session_cases(cases)

    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
