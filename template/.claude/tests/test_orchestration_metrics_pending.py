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

    # --- a bare verdict for an already-archived label is named, history is not ---
    write_verdicts(om.PENDING_VERDICTS, {"review:unique": "clean", "other-session:lens": "clean"})
    _, _, unmatched = om.pending_report(session)
    cases.append(("bare verdict on an archived label of this session is reported unmatched",
                  "review:unique" in unmatched))
    cases.append(("a bare verdict naming no label of this session is silent history",
                  "other-session:lens" not in unmatched))

    # --- pending_count wraps find_session_dir and never raises ---------------
    cases.append(("pending_count counts the same items",
                  om.pending_count(session) == len(om.pending_labels(session))))
    cases.append(("pending_count on a dir with no workflows/ is 0, not -1",
                  om.pending_count(os.path.join(root, "does-not-exist")) == 0))

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
    real_expand = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1)
    try:
        cases.append(("explicit id with no workflows dir -> None, not the peer's dir",
                      om.find_session_dir("this-session") is None))
        cases.append(("explicit id that exists -> its own dir",
                      (om.find_session_dir("peer-session") or "").endswith("peer-session")))
        cases.append(("no id at all still falls back to the scan",
                      (om.find_session_dir() or "").endswith("peer-session")))
    finally:
        os.path.expanduser = real_expand

    # --- an evaluation instrument's internal stages are not rating debt ---
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

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
