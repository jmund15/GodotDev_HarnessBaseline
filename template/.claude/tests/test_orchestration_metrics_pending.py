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

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")

spec = importlib.util.spec_from_file_location("orchestration_metrics", MODULE_PATH)
om = importlib.util.module_from_spec(spec)
spec.loader.exec_module(om)


def write_run(wdir, run_id, labels, workflow_name=None):
    with open(os.path.join(wdir, f"{run_id}.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "runId": run_id,
            **({"workflowName": workflow_name} if workflow_name else {}),
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

    sys.argv = [MODULE_PATH, "--session", session, "--no-sidecar", "--verdicts",
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
    sys.argv = [MODULE_PATH, "--session", session, "--no-sidecar"]
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

    # A concurrent correction after the snapshot is not the value that was archived.
    write_verdicts(om.PENDING_VERDICTS, {"wf_1:review:x": "clean"})
    sys.argv = [MODULE_PATH, "--session", session, "--no-sidecar"]
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
    sys.argv = [MODULE_PATH, "--session", session, "--no-sidecar"]
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
    sys.argv = [MODULE_PATH, "--session", session, "--no-sidecar"]
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

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
