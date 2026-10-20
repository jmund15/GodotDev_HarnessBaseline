"""Re-runnable proof for orchestration_metrics.py's pending-verdict surface (S5).

`PENDING_VERDICTS` is the project-anchored, gitignored `.claude/orchestration_verdicts.json`; a legacy
scratch-dir copy merges in with root winning on key collision. `pending_labels`/`pending_count`
read only `workflows/*.json` (no per-agent transcripts) so they are cheap enough for a per-turn
hook. Verdict keys are `run_id:label`; a bare label resolves a pair only when it is the label of
exactly one unarchived candidate for the session -- ambiguous bare labels stay pending.

The module's own `.claude/orchestration_verdicts.json` and `.claude/orchestration_metrics.jsonl`
are never touched: every case monkeypatches the module's path constants to a temp tree.

    python3 .claude/tests/test_orchestration_metrics_pending.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _om_session_fixture as fixture  # noqa: E402
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")

spec = importlib.util.spec_from_file_location("orchestration_metrics", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


def write_run(wdir, run_id, labels, workflow_name=None, args=None):
    with open(os.path.join(wdir, f"{run_id}.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "runId": run_id,
            **({"workflowName": workflow_name} if workflow_name else {}),
            **({"args": args} if args is not None else {}),
            "workflowProgress": [
                {"type": "workflow_agent", "agentId": f"{run_id}-a{i}", "label": lab}
                for i, lab in enumerate(labels)
            ],
        }, fh)


def write_verdicts(path, verdicts):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(verdicts, fh)


def setup_session(root):
    session = os.path.join(root, "session")
    wdir = os.path.join(session, "workflows")
    os.makedirs(wdir, exist_ok=True)
    # Two runs share label review:x; wf_3 carries a unique label.
    write_run(wdir, "wf_1", ["review:x"])
    write_run(wdir, "wf_2", ["review:x"])
    write_run(wdir, "wf_3", ["review:unique"])
    return session


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def real_session_cases(cases):
    """D1/D2 of the 2026-10-18 unratable-dispatch defect, on launches and records cut from session 30b7e19d."""
    def fresh():
        root = tempfile.mkdtemp(prefix="ompending_real_")
        return root, fixture.materialize(root)

    def side_rows(session, diagnostics=None):
        return {r["label"]: r for r in om.collect_session_sidecar(session, diagnostics if diagnostics is not None else [])}

    def set_pid(root, text):
        with open(fixture.record_path(root, "exec_out", "exec-gatefix") + ".pid", "w", encoding="utf-8") as fh:
            fh.write(text)

    def set_exit(root, code):
        with open(fixture.record_path(root, "exec_out", "exec-gatefix") + ".exit", "w", encoding="utf-8") as fh:
            fh.write(code)

    root, session = fresh()
    launches = {norm(p) for p in om._sidecar_record_launches(session)}
    def launched(name, label):
        return norm(fixture.record_path(root, name, label)) in launches
    cases.append(("D1 `--out <dir> --detach 2>&1 | tail -4` (argparse abbreviation of --out-dir) attributes its records",
                  launched("run2", "codify-4") and launched("run2", "codify-5")))
    cases.append(("D1 a `for`/`python3 -c`/`;` compound ending in `--out <dir> --detach 2>&1 | tail -3` attributes its records",
                  launched("exec", "codify-exec-watch") and launched("exec", "codify-exec-floor")))
    cases.append(("D1 a bare `--out <dir>` launch with no redirect attributes its record",
                  launched("exec_out", "exec-gatefix")))
    rows = side_rows(session)
    cases.append(("D1 every record of those launches is a session sidecar row",
                  {"codify-4", "codify-5", "codify-exec-watch", "codify-exec-floor", "exec-gatefix"} <= set(rows)))

    gate = rows.get("exec-gatefix") or {}
    cases.append(("D2 a stopped run (empty record, no .exit, launcher pid gone) is a row",
                  bool(gate)))
    cases.append(("D2 the bodyless row keys on the launch id and invents no tokens, cost or model",
                  gate.get("run") == "sidecar-" + fixture.GATEFIX_LAUNCH and gate.get("source") == "sidecar"
                  and gate.get("inp") is None and gate.get("out") is None and gate.get("cost_usd") is None
                  and gate.get("model") == "?" and gate.get("state") == "no-record"
                  and norm(gate.get("record_path") or "") == norm(fixture.record_path(root, "exec_out", "exec-gatefix"))))
    detail = om.pending_detail(session, diagnostics=[])
    cases.append(("D2 the stopped run and the attributed records are pending",
                  ("sidecar-" + fixture.GATEFIX_LAUNCH, "exec-gatefix") in detail["remaining"]
                  and {"codify-4", "codify-exec-watch"} <= {lab for _, lab in detail["remaining"]}))

    diagnostics = []
    side_rows(session, diagnostics)
    cases.append(("D2 a stopped run raises no malformed-record diagnostic", not any(
        d.get("status") == "malformed" for d in diagnostics)))

    set_exit(root, "0")
    diagnostics = []
    cases.append(("D2 an empty record after a clean .exit is also a bodyless row, not malformed",
                  "exec-gatefix" in side_rows(session, diagnostics)
                  and not any(d.get("status") == "malformed" for d in diagnostics)))

    set_exit(root, "8")
    diagnostics = []
    cases.append(("D2 an empty record after a nonzero .exit stays a failed launch, not a dispatch",
                  "exec-gatefix" not in side_rows(session, diagnostics)
                  and any(d.get("status") == "launch-failed" for d in diagnostics)))
    os.remove(fixture.record_path(root, "exec_out", "exec-gatefix") + ".exit")

    key = "winpid=" if os.name == "nt" else "pid="
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    set_pid(root, "%s launch=%s %s%d pid=%d\n" % (stamp, fixture.GATEFIX_LAUNCH, "winpid=%d " % os.getpid()
                                                  if key == "winpid=" else "", os.getpid(), os.getpid()))
    cases.append(("D2 a run whose launcher pid is alive is still in progress, not a row",
                  "exec-gatefix" not in side_rows(session)))

    set_pid(root, "2026-10-18T12:00:00Z launch=%s winpid=%d pid=%d\n" % (fixture.GATEFIX_LAUNCH, 62696, 1313380))
    cases.append(("D2 a pid file stamped before this session's launch is another launch's, not a row",
                  "exec-gatefix" not in side_rows(session)))

    root, session = fresh()
    inventory = next(os.path.join(fixture.out_dir(root, "exec_out"), n)
                     for n in os.listdir(fixture.out_dir(root, "exec_out")) if n.endswith(".inventory.json"))
    with open(inventory, encoding="utf-8") as fh:
        data = json.load(fh)
    data["parentSessionId"] = "another-session"
    with open(inventory, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    cases.append(("D2 a bodyless record listed only by another session's inventory is not this session's",
                  "exec-gatefix" not in side_rows(session)))


def main():
    cases = []
    root = tempfile.mkdtemp(prefix="ommpending_")
    session = setup_session(root)
    om.PENDING_VERDICTS = os.path.join(root, "orchestration_verdicts.json")
    om.LEGACY_PENDING_VERDICTS = os.path.join(root, "scratch_orchestration_verdicts.json")
    om.ARCHIVE = os.path.join(root, "orchestration_metrics.jsonl")

    # --- exact run_id:label rates only that one run -------------------------
    write_verdicts(om.PENDING_VERDICTS, {"wf_1:review:x": "clean"})
    items = om.pending_labels(session)
    cases.append(("exact run_id:label rates only that run",
                  ("wf_1", "review:x") not in items and ("wf_2", "review:x") in items))

    # --- ambiguous bare label rates neither, --pending names it -------------
    write_verdicts(om.PENDING_VERDICTS, {"review:x": "clean"})
    items, ambiguous, unmatched = om.pending_report(session)
    cases.append(("ambiguous bare label rates neither run",
                  ("wf_1", "review:x") in items and ("wf_2", "review:x") in items))
    cases.append(("ambiguity is named", "review:x" in ambiguous))
    cases.append(("a live bare label is not reported unmatched", "review:x" not in unmatched))

    # --- bare key for a unique label rates it --------------------------------
    write_verdicts(om.PENDING_VERDICTS, {"review:unique": "clean"})
    items = om.pending_labels(session)
    cases.append(("bare key for a unique label rates it",
                  ("wf_3", "review:unique") not in items))
    cases.append(("the ambiguous pair is untouched by the unique verdict",
                  ("wf_1", "review:x") in items and ("wf_2", "review:x") in items))

    # --- archive applies the same exact/unique-key contract -------------------
    write_verdicts(om.PENDING_VERDICTS, {})
    archive_rows = [
        {"run": "wf_1", "label": "review:x", "effort": "low", "state": "completed"},
        {"run": "wf_2", "label": "review:x", "effort": "low", "state": "completed"},
    ]
    archived_rows = []
    real_argv = sys.argv
    real_collect = om.collect
    real_collect_sidecar = om.collect_session_sidecar
    real_ledger = om.load_record_ledger
    real_report = om.report
    real_archive = om._archive_rows_atomic

    def capture_archive(rows):
        archived_rows.extend(dict(row) for row in rows)
        return rows, 0, []

    sys.argv = [MODULE_PATH, "--session", session, "--verdicts",
                json.dumps({"wf_1:review:x": ["clean", "high"], "review:x": "defects"})]
    om.collect = lambda *_: [dict(row) for row in archive_rows]
    om.collect_session_sidecar = lambda *_: []
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None
    om._archive_rows_atomic = capture_archive
    try:
        archive_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    archived_outcomes = {row["run"]: row.get("outcome") for row in archived_rows}
    cases.append(("archive prefers exact keys and ignores ambiguous bare labels",
                  archive_rc == 0 and archived_outcomes == {
                      "wf_1": "clean", "wf_2": "unrated",
                  }))
    cases.append(("archived effort keeps requested and observed evidence separate",
                  {row["run"]: row.get("requested_effort") for row in archived_rows} == {
                      "wf_1": "high", "wf_2": "low",
                  } and all(row.get("observed_effort") is None for row in archived_rows)))

    # --- successful publication removes only the pending verdicts it consumed --
    write_verdicts(om.PENDING_VERDICTS, {
        "wf_1:review:x": "clean",
        "review:unique": "defects",
        "review:x": "rework",
        "foreign:label": "clean",
    })
    cleanup_rows = [
        {"run": "wf_1", "label": "review:x", "effort": "low", "state": "completed"},
        {"run": "wf_3", "label": "review:unique", "effort": "low", "state": "completed"},
    ]
    sys.argv = [MODULE_PATH, "--session", session, "--archive"]
    om.collect = lambda *_: [dict(row) for row in cleanup_rows]
    om.collect_session_sidecar = lambda *_: []
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None
    om._archive_rows_atomic = lambda rows: (rows, 0, [])
    try:
        cleanup_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    remaining = om._read_json_object(om.PENDING_VERDICTS)
    cases.append(("successful archive removes consumed exact and unique bare verdicts",
                  cleanup_rc == 0 and remaining == {
                      "review:x": "rework", "foreign:label": "clean",
                  }))

    # A bodyless sidecar row exists only for a run that ended (reaped, killed, or no output),
    # so it is terminal: it must not block the archive forever.
    write_verdicts(om.PENDING_VERDICTS, {"sidecar-x:exec-y": "clean"})
    sys.argv = [MODULE_PATH, "--session", session, "--archive"]
    om.collect = lambda *_: []
    om.collect_session_sidecar = lambda *_: [{"run": "sidecar-x", "label": "exec-y", "effort": "high",
                                             "state": "no-record", "source": "sidecar"}]
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None
    om._archive_rows_atomic = lambda rows: (rows, 0, [])
    try:
        bodyless_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    cases.append(("an ended sidecar run with an empty record is terminal, so the archive takes it",
                  bodyless_rc == 0))

    # A concurrent correction after the snapshot is not the value that was archived.
    write_verdicts(om.PENDING_VERDICTS, {"wf_1:review:x": "clean"})
    sys.argv = [MODULE_PATH, "--session", session, "--archive"]
    om.collect = lambda *_: [{"run": "wf_1", "label": "review:x", "effort": "low",
                                   "state": "completed"}]
    om.collect_session_sidecar = lambda *_: []
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None

    def archive_then_correct(rows):
        write_verdicts(om.PENDING_VERDICTS, {"wf_1:review:x": "rework"})
        return rows, 0, []

    om._archive_rows_atomic = archive_then_correct
    try:
        concurrent_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    cases.append(("archive cleanup preserves a concurrently changed verdict",
                  concurrent_rc == 0 and om._read_json_object(om.PENDING_VERDICTS) == {
                      "wf_1:review:x": "rework",
                  }))

    # Cleanup must hold the shared verdict lock across its read and replacement.
    write_verdicts(om.PENDING_VERDICTS, {"wf_1:review:x": "clean"})
    lock_observed = []
    real_atomic_write = om._atomic_write_json

    def observe_locked_write(*args, **kwargs):
        lock_observed.append(os.path.exists(om.PENDING_VERDICTS + ".lock"))
        return real_atomic_write(*args, **kwargs)

    om._atomic_write_json = observe_locked_write
    try:
        om._prune_pending_verdicts({"wf_1:review:x": "clean"}, {"wf_1:review:x"})
    finally:
        om._atomic_write_json = real_atomic_write
    cases.append(("pending cleanup writes while holding its shared lock", lock_observed == [True]))

    # An agent row exists at invocation time before its work is complete. It must
    # remain pending rather than becoming permanent calibration evidence.
    write_verdicts(om.PENDING_VERDICTS, {"wf_running:review:running": "clean"})
    archived_rows.clear()
    sys.argv = [MODULE_PATH, "--session", session, "--archive"]
    om.collect = lambda *_: [{
        "run": "wf_running", "label": "review:running", "effort": "low",
        "state": "running", "cost": 10, "turns": 1,
    }]
    om.collect_session_sidecar = lambda *_: []
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None
    om._archive_rows_atomic = capture_archive
    try:
        running_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    cases.append(("invocation without terminal completion is never archived",
                  running_rc == 1 and archived_rows == []
                  and om._read_json_object(om.PENDING_VERDICTS) == {
                      "wf_running:review:running": "clean",
                  }))

    # --- scratch merges with root winning on collision -----------------------
    write_verdicts(om.PENDING_VERDICTS, {"wf_3:review:unique": "clean"})
    write_verdicts(om.LEGACY_PENDING_VERDICTS, {"wf_1:review:x": "defects"})
    merged = om.load_pending_verdicts()
    cases.append(("legacy scratch entry merges in", "wf_1:review:x" in merged))
    cases.append(("root value wins on collision", merged.get("wf_3:review:unique") == "clean"))
    write_verdicts(om.PENDING_VERDICTS, {
        "wf_3:review:unique": "clean", "wf_1:review:x": "clean",
    })
    merged = om.load_pending_verdicts()
    cases.append(("root wins when both sides carry the same key",
                  merged.get("wf_1:review:x") == "clean"))

    # A verdict consumed from the legacy source must be removed from that source.
    write_run(os.path.join(session, "workflows"), "wf_legacy", ["review:legacy"])
    write_verdicts(om.PENDING_VERDICTS, {})
    write_verdicts(om.LEGACY_PENDING_VERDICTS, {"wf_legacy:review:legacy": "clean"})
    sys.argv = [MODULE_PATH, "--session", session, "--archive"]
    om.collect = lambda *_: [{
        "run": "wf_legacy", "label": "review:legacy", "effort": "low", "state": "completed",
    }]
    om.collect_session_sidecar = lambda *_: []
    om.load_record_ledger = lambda *_: (set(), set())
    om.report = lambda *_: None
    om._archive_rows_atomic = lambda rows: (rows, 0, [])
    try:
        legacy_cleanup_rc = om.main()
    finally:
        sys.argv = real_argv
        om.collect = real_collect
        om.collect_session_sidecar = real_collect_sidecar
        om.load_record_ledger = real_ledger
        om.report = real_report
        om._archive_rows_atomic = real_archive
    cases.append(("legacy verdict cleanup removes the consumed key",
                  legacy_cleanup_rc == 0
                  and om._read_json_object(om.LEGACY_PENDING_VERDICTS) == {}))

    # --- archived label excluded ---------------------------------------------
    write_verdicts(om.PENDING_VERDICTS, {})
    write_verdicts(om.LEGACY_PENDING_VERDICTS, {})
    with open(om.ARCHIVE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": "wf_3", "label": "review:unique", "outcome": "clean"}) + "\n")
    items = om.pending_labels(session)
    cases.append(("an archived (run, label) pair is excluded",
                  ("wf_3", "review:unique") not in items))
    cases.append(("a not-yet-archived pair stays pending",
                  ("wf_1", "review:x") in items and ("wf_2", "review:x") in items))
    with open(om.ARCHIVE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": "wf_1", "ignored": True}) + "\n")
    items = om.pending_labels(session)
    cases.append(("an ignored run is terminal and creates no pending rows",
                  all(run != "wf_1" for run, _ in items)))

    # --- a bare verdict for an already-archived label is named, history is not ---
    write_verdicts(om.PENDING_VERDICTS, {"review:unique": "clean", "other-session:lens": "clean"})
    _, _, unmatched = om.pending_report(session)
    cases.append(("bare verdict on an archived label of this session is reported unmatched",
                  "review:unique" in unmatched))
    cases.append(("a bare verdict naming no label of this session is silent history",
                  "other-session:lens" not in unmatched))

    # --- a label@run_id key rates nothing, so it is named with its run_id:label spelling ---
    write_verdicts(om.PENDING_VERDICTS, {"review:x@wf_2": "clean", "lens@wf_other": "clean"})
    misshaped_fn = getattr(om, "misshaped_verdict_keys", None)
    misshaped = misshaped_fn(session) if misshaped_fn else None
    cases.append(("a label@run_id key still leaves its pair pending",
                  ("wf_2", "review:x") in om.pending_labels(session)))
    cases.append(("a label@run_id key for this session is named with its run_id:label spelling",
                  misshaped == [("review:x@wf_2", "wf_2:review:x")]))
    write_run(os.path.join(session, "workflows"), "wf_at_label", ["review@wf_2"])
    write_verdicts(om.PENDING_VERDICTS, {"review@wf_2": "clean"})
    cases.append(("a bare label containing @ is not misshaped",
                  om.misshaped_verdict_keys(session) == []))
    write_verdicts(om.PENDING_VERDICTS, {"lane@muse-opencode": "clean", "other@muse-opencode": "clean"})
    try:
        sidecar_misshaped = om.misshaped_verdict_keys(session, sidecar_labels={"lane"})
    except TypeError:
        sidecar_misshaped = None
    cases.append(("a label@transport key on this session's sidecar label is named with its bare label",
                  sidecar_misshaped == [("lane@muse-opencode", "lane")]))

    # --- pending_count wraps find_session_dir and never raises ---------------
    cases.append(("pending_count counts the same items",
                  om.pending_count(session) == len(om.pending_labels(session))))
    missing_session_dir = os.path.join(root, "does-not-exist")
    cases.append(("pending_count on a missing workflows source is unknown",
                  om.pending_count(missing_session_dir) == -1))
    empty_session_dir = os.path.join(root, "empty-session")
    os.makedirs(os.path.join(empty_session_dir, "workflows"))
    cases.append(("pending_count on a present empty workflows source is zero",
                  om.pending_count(empty_session_dir) == 0))

    real_find = om.find_session_dir
    om.find_session_dir = lambda session_id=None: None
    try:
        cases.append(("pending_count never raises -- unknown session is -1",
                      om.pending_count() == -1))
    finally:
        om.find_session_dir = real_find

    # --- an explicit session id never falls through to the mtime scan ---------
    home = tempfile.mkdtemp(prefix="ommhome_")
    peer = os.path.join(home, ".claude", "projects", "proj", "peer-session", "workflows")
    os.makedirs(peer)
    write_run(peer, "wf_p", ["review:peer"])
    current_session = os.path.join(home, ".claude", "projects", "proj", "current-session")
    with open(current_session + ".jsonl", "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    real_expand = os.path.expanduser
    old_session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    old_config_dir = os.environ.pop("CLAUDE_CONFIG_DIR", None)  # a real second-account dir would override the planted home
    os.path.expanduser = lambda p: p.replace("~", home, 1)
    try:
        cases.append(("explicit id with no workflows dir -> None, not the peer's dir",
                      om.find_session_dir("this-session") is None))
        cases.append(("explicit id that exists -> its own dir",
                      (om.find_session_dir("peer-session") or "").endswith("peer-session")))
        os.environ["CLAUDE_CODE_SESSION_ID"] = "current-session"
        cases.append(("environment id resolves a transcript-only current session",
                      os.path.normpath(om.find_session_dir() or "") == os.path.normpath(current_session)))
        os.environ["CLAUDE_CODE_SESSION_ID"] = "missing-session"
        cases.append(("missing environment id never falls through to a peer",
                      om.find_session_dir() is None))
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        cases.append(("no id at all still falls back to the scan",
                      (om.find_session_dir() or "").endswith("peer-session")))
    finally:
        if old_session_id is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = old_session_id
        if old_config_dir is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = old_config_dir
        os.path.expanduser = real_expand

    # --- a benchmark instrument's internal stages are not rating debt (D29, 2026-09-09) ---
    wdir = os.path.join(session, "workflows")
    write_run(wdir, "wf_sc", ["judge:ARM-T1-X#1", "verify:ARM-T1-X", "persist:ARM-T1-X"], workflow_name="score-cell")
    write_run(wdir, "wf_kr", ["reach:F1.1"], workflow_name="key-reachability")
    write_run(wdir, "wf_rf", ["review:lens"], workflow_name="review-fanout")
    write_run(wdir, "wf_nn", ["review:noname"])
    write_verdicts(om.PENDING_VERDICTS, {})
    items = om.pending_labels(session)
    runs_pending = {rid for rid, _ in items}
    cases.append(("score-cell stages are not pending", "wf_sc" not in runs_pending))
    cases.append(("key-reachability judges are not pending", "wf_kr" not in runs_pending))
    cases.append(("a delivered-work workflow still is", ("wf_rf", "review:lens") in items))
    cases.append(("a run with no workflowName still counts", ("wf_nn", "review:noname") in items))

    # --- a declared probe proves one behavior and is not rating debt; an A/B arm is real comparison data ---
    write_run(wdir, "wf_pr", ["probe-old-1"], workflow_name="dispatch",
              args={"measurement": "text probe: old vs new rule", "probe": True})
    write_run(wdir, "wf_ab", ["ab-seat"], workflow_name="review-fanout",
              args={"measurement": "pin A/B: haiku:max vs sonnet:xhigh"})
    items = om.pending_labels(session)
    runs_pending = {rid for rid, _ in items}
    cases.append(("a run declaring args.probe is not pending", "wf_pr" not in runs_pending))
    cases.append(("an A/B arm that declares only measurement still is", ("wf_ab", "ab-seat") in items))

    # --- the legacy-merge notice never lands on stdout (a per-prompt hook's model channel) ---
    import contextlib
    import io
    write_verdicts(om.PENDING_VERDICTS, {})
    write_verdicts(om.LEGACY_PENDING_VERDICTS, {"wf_9:lens": "clean"})
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        om.load_pending_verdicts()
    cases.append(("legacy merge notice goes to stderr, stdout stays empty",
                  "Merged" in err.getvalue() and out.getvalue() == ""))

    # --- a bare-label verdict whose effort contradicts the row's attested pin is another run's ---
    row = {"run": "wf_mine", "label": "review:lens", "effort": "medium"}
    counts = {"review:lens": 1}
    theirs = {"review:lens": {"outcome": "clean", "effort": "xhigh", "tier": "Wide"}}
    cases.append(("a bare verdict naming a different effort than the attested pin is not applied",
                  om._select_verdict(row, theirs, counts) == (None, None)))
    cases.append(("a bare [outcome, effort] pair that contradicts the pin is not applied",
                  om._select_verdict(row, {"review:lens": ["clean", "high"]}, counts) == (None, None)))
    cases.append(("a bare verdict naming the attested effort is applied",
                  om._select_verdict(row, {"review:lens": ["defects", "medium"]}, counts)
                  == ("review:lens", ["defects", "medium"])))
    cases.append(("a bare outcome-only verdict is applied",
                  om._select_verdict(row, {"review:lens": "clean"}, counts) == ("review:lens", "clean")))
    cases.append(("a bare effort resolves an unresolved '?' pin",
                  om._select_verdict(dict(row, effort="?"), {"review:lens": ["clean", "high"]}, counts)
                  == ("review:lens", ["clean", "high"])))
    cases.append(("an exact run:label key wins even with a different effort",
                  om._select_verdict(row, {"wf_mine:review:lens": ["clean", "xhigh"]}, counts)
                  == ("wf_mine:review:lens", ["clean", "xhigh"])))
    cases.append(("an ambiguous bare label selects nothing",
                  om._select_verdict(row, {"review:lens": "clean"}, {"review:lens": 2}) == (None, None)))

    # --- this session's finished, unrated sidecar dispatches are rating debt ----------------------
    import contextlib
    import io
    sc_root = tempfile.mkdtemp(prefix="ommsidecar_")
    sc_session = os.path.join(sc_root, "session")
    os.makedirs(os.path.join(sc_session, "workflows"))
    write_run(os.path.join(sc_session, "workflows"), "wf_n", ["native:one"])
    sc_records = {name: os.path.join(sc_root, name + ".record.json")
                  for name in ("done", "rated", "archived", "live", "failed")}
    for name in ("done", "rated", "archived"):
        with open(sc_records[name], "w", encoding="utf-8") as handle:
            json.dump({"timestamp": "2026-09-10T10:01:00Z", "parentSessionId": "session",
                       "label": name, "transport": "opencode", "costModel": "marginal-usd",
                       "servedModel": "muse-spark", "requestedModel": "muse", "effort": "low",
                       "inputTokens": 1, "outputTokens": 1, "numTurns": 1, "durationMs": 1,
                       "costUSD": 0.0, "exitCode": 0}, handle)
    for name in ("live", "failed"):
        open(sc_records[name], "w").close()
    with open(sc_records["failed"] + ".exit", "w", encoding="utf-8") as handle:
        handle.write("8")
    real_launches = om._sidecar_record_launches
    om._sidecar_record_launches = lambda _s: {path: None for path in sc_records.values()}
    real_ledger = om.load_record_ledger
    write_verdicts(om.PENDING_VERDICTS, {})
    try:
        pairs = om.pending_labels(sc_session)
        sc_labels = {lab for _, lab in pairs}
        cases.append(("an unrated finished sidecar record is pending",
                      {"done", "rated", "archived"} <= sc_labels and "native:one" in sc_labels))
        cases.append(("an in-progress or launch-failed sidecar record is not debt",
                      "live" not in sc_labels and "failed" not in sc_labels))
        done_run = next((rid for rid, lab in pairs if lab == "done"), "sidecar-missing")
        cases.append(("a sidecar pair is keyed as --rate writes it (sidecar-...)",
                      done_run.startswith("sidecar-")))
        write_verdicts(om.PENDING_VERDICTS, {f"{done_run}:done": "clean"})
        cases.append(("a sidecar pair rated under its exact run:label key is not pending",
                      "done" not in {lab for _, lab in om.pending_labels(sc_session)}))
        write_verdicts(om.PENDING_VERDICTS, {})
        archived_run = next((rid for rid, lab in pairs if lab == "archived"), "sidecar-missing")
        om.load_record_ledger = lambda *_: ({(archived_run, "archived")}, set())
        cases.append(("an archived sidecar pair is not pending",
                      "archived" not in {lab for _, lab in om.pending_labels(sc_session)}))
        om.load_record_ledger = lambda *_: (set(), {archived_run})
        cases.append(("an ignored sidecar run is not pending",
                      "archived" not in {lab for _, lab in om.pending_labels(sc_session)}))
        om.load_record_ledger = real_ledger
        cases.append(("pending_count includes the sidecar debt",
                      om.pending_count(sc_session) == len(om.pending_labels(sc_session)) == 4))

        # The compaction hook's count needs each record's run and label, never its price: repricing
        # reads the record's transcript, which cost 31 s over 170 records on a long drive.
        repriced = []
        real_reprice = om.model_registry.reprice_record
        om.model_registry.reprice_record = lambda rec, *a, **k: (repriced.append(1), real_reprice(rec, *a, **k))[1]
        try:
            counted = om.pending_count(sc_session)
            count_reprices = len(repriced)
            del repriced[:]
            priced_rows = om.pending_detail(sc_session)["sidecar"]
            detail_reprices = len(repriced)
        finally:
            om.model_registry.reprice_record = real_reprice
        cases.append(("pending_count reads sidecar records without repricing them",
                      counted == 4 and count_reprices == 0))
        cases.append(("pending_detail still reprices the sidecar rows its caller reports",
                      len(priced_rows) == 3 and detail_reprices == 3))

        def run_pending(*extra):
            real_argv_local, out = sys.argv, io.StringIO()
            sys.argv = [MODULE_PATH, "--session", sc_session, "--pending", *extra]
            try:
                with contextlib.redirect_stdout(out):
                    om.main()
            finally:
                sys.argv = real_argv_local
            return out.getvalue()
        shown = run_pending()
        cases.append(("--pending lists the sidecar pair and counts it",
                      "unrated dispatches: 4" in shown and f"{done_run}:done" in shown))
        cases.append(("--pending reports the launch failure beside the debt",
                      "launch-failed" in shown))
        with contextlib.redirect_stdout(io.StringIO()):
            om.accept_rest(sc_session)
        cases.append(("--accept-rest never accepts a sidecar pair unseen",
                      not any(k.startswith("sidecar-") for k in om.load_pending_verdicts())))

        def raising_collect(*_):
            raise RuntimeError("sidecar read failed")
        real_collect_sc = om.collect_session_sidecar
        om.collect_session_sidecar = raising_collect
        try:
            failed_detail = om.pending_detail(sc_session, diagnostics=[])
            cases.append(("a sidecar read that raises keeps the native rows and names its error",
                          [lab for _, lab in failed_detail["remaining"]] == ["native:one"]
                          and "sidecar read failed" in str(failed_detail.get("sidecar_error"))))
            cases.append(("a sidecar read that raises makes pending_count unknown (-1), never a clean count",
                          om.pending_count(sc_session) == -1))
            failed_shown = run_pending()
            cases.append(("--pending prints the sidecar census failure and an unknown count",
                          "sidecar read failed" in failed_shown and "unrated dispatches: unknown" in failed_shown))
        finally:
            om.collect_session_sidecar = real_collect_sc

        sidecar_only = os.path.join(sc_root, "bare", "session")
        os.makedirs(sidecar_only)
        cases.append(("a session with only sidecar dispatches still counts them",
                      om.pending_count(sidecar_only) == 3))
    finally:
        om._sidecar_record_launches = real_launches
        om.load_record_ledger = real_ledger

    real_session_cases(cases)

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
