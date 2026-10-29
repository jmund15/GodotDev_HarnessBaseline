#!/usr/bin/env python3
"""The sidecar review fold's prompt carries a `Bar:` line, and each row its `counts` at that bar, when its ledger task
resolves a rigor, and neither when the record is unreadable. The launch is stubbed to a dry run: only the prompt file each fold author reads is under test.

    python3 .claude/tests/test_sidecar_fanout_review_fold_bar.py
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import sidecar_fanout  # noqa: E402

FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(f"{label} :: {detail}")


def fold_prompt(task, records):
    """The fold author's prompt text, written for a one-row fold with ledger task `task`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        records_dir = root / "tasks"
        records_dir.mkdir()
        for name, level in records.items():
            (records_dir / (name + ".json")).write_text(json.dumps(
                {"id": name, "updated": "2026-10-26T10:00:00", "rigor": level}), encoding="utf-8")
        subject = types.SimpleNamespace(root=root, files=["plan.md"], new_paths=[],
                                        manifest_path=root / "snapshot.json")
        review = {"view": [{"id": "F1", "action": "FIX", "critical": False, "file": "plan.md:1",
                            "description": "fixture", "minRigor": "thorough"}]}
        args = types.SimpleNamespace(fold=".claude/commands/agents/review_fold.md", fold_subject=["spec"],
                                     ledger_task=task, ledger_premise_commits=None, max_parallel=1,
                                     authorize=None, dry_run=True)
        fc = sidecar_fanout.fold_check
        saved = (fc.parse_subject_spec, fc.route, fc.independent_verdicts, sidecar_fanout.plan_job,
                 sidecar_fanout.add_concurrency_guard, sidecar_fanout.fold_probe, sidecar_fanout.print_plan)
        env = {k: os.environ.get(k) for k in ("HARNESS_TASK_RECORD_DIR", "CLAUDE_CODE_SESSION_ID")}
        os.environ["HARNESS_TASK_RECORD_DIR"] = str(records_dir)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        fc.parse_subject_spec = lambda spec: subject
        fc.route = lambda rows, subjects: ({"F1": 0}, [], [])
        fc.independent_verdicts = lambda rows, subjects, out: (root / "verdicts.json", root / "verdicts.json")
        sidecar_fanout.plan_job = lambda spec, data, out_dir, authorize, stage=None: {}
        sidecar_fanout.add_concurrency_guard = lambda planned, jobs, max_parallel, out_dir: []
        sidecar_fanout.fold_probe = lambda args, seats=(), data=None: {}
        sidecar_fanout.print_plan = lambda planned, max_parallel: None
        try:
            sidecar_fanout.run_review_fold(review, args, {}, root)
            label = sidecar_fanout.FOLD_LABEL.format(1)
            prompt, findings = root / (label + "_prompt.md"), root / (label + "_findings.json")
            rows = json.loads(findings.read_text(encoding="utf-8"))["findings"] if findings.exists() else []
            return (prompt.read_text(encoding="utf-8") if prompt.exists() else ""), rows
        finally:
            (fc.parse_subject_spec, fc.route, fc.independent_verdicts, sidecar_fanout.plan_job,
             sidecar_fanout.add_concurrency_guard, sidecar_fanout.fold_probe, sidecar_fanout.print_plan) = saved
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main():
    barred, rows = fold_prompt("T1", {"T1": "normal"})
    check("fold prompt carries Bar when the ledger task resolves a rigor",
          "Bar: normal" in barred, "no `Bar: normal` line in the fold prompt")
    check("a thorough row carries counts false at normal, in the findings file and the prompt",
          [row.get("counts") for row in rows] == [False] and '"counts": false' in barred, json.dumps(rows))
    plain, plain_rows = fold_prompt("T2", {"T2": "bogus"})
    check("fold prompt omits Bar when the ledger task's record is unreadable",
          "Bar:" not in plain, "a Bar line was written for an unreadable rigor")
    check("rows carry no counts when the rigor is unreadable",
          plain_rows and all("counts" not in row for row in plain_rows) and '"counts"' not in plain, json.dumps(plain_rows))
    if FAILURES:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
