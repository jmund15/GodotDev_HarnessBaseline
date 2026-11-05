"""Sidecar review CLI consolidates full results and selects held F- and M-id evidence."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_ledger  # noqa: E402

needs_plc_mandate = unittest.skipUnless("plc-instruction-quality" in review_ledger.load_catalog(),
                                        "the coding layer's review mandates are not installed")


@pytest.fixture(autouse=True)
def isolated_session_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_STATE_DIR", str(tmp_path / "session-state"))
    # The CLI resolves its `.claude/` templates from the working directory; a runner may start elsewhere.
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    # The band gate reads provider capacity; a planted available reading keeps it off the live account's state.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _transport_fixture
    monkeypatch.setenv("PROVIDER_CAPACITY_CACHE", _transport_fixture._available_capacity_cache())
    run = subprocess.run

    def noninteractive(*args, **kwargs):
        if kwargs.get("input") is None and kwargs.get("stdin") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        if not kwargs.get("capture_output"):
            if kwargs.get("stdout") is None:
                kwargs["stdout"] = subprocess.PIPE
            if kwargs.get("stderr") is None:
                kwargs["stderr"] = subprocess.PIPE
        return run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", noninteractive)


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
SPEC = importlib.util.spec_from_file_location("sidecar_fanout", ROOT / ".claude/tools/sidecar_fanout.py")
fanout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fanout)
import fold_check  # noqa: E402


CLAUDE_MD = {"path": ".claude/CLAUDE.md",
             "lines": len((ROOT / ".claude/CLAUDE.md").read_text(encoding="utf-8").splitlines())}
CLAUDE_READS = [(CLAUDE_MD["path"], None, None)]


def plant_stream(out, label, reads):
    events = []
    for n, (path, offset, limit) in enumerate(reads):
        given = dict({"file_path": path}, **{k: v for k, v in (("offset", offset), ("limit", limit)) if v is not None})
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"c{n}", "name": "Read", "input": given}]}})
        events.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"c{n}", "content": "x", "is_error": False}]}})
    stream = out / f"{label}.stream.jsonl"
    stream.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return str(stream)


def reply(description):
    return {"type": "result", "is_error": False, "structured_output": {
        "checked": {"toolsUsed": ["Read:.claude/CLAUDE.md"],
                    "stoppedAt": "exhausted-leads", "basis": "plc-instruction-quality: findings"},
        "findings": [{"agent": "plc-instruction-quality", "mandate": "plc-instruction-quality",
                      "action": "FIX", "category": "bug", "critical": False,
                      "file": ".claude/plans/p.md:3", "description": description,
                      "rationale": "matched source",
                      "evidence": [{"file": ".claude/CLAUDE.md:1", "quote": "@CLAUDE.core.md"}]}],
        "gaps": []}}


class SidecarReviewCliTests(unittest.TestCase):
    def setUp(self):
        # The duplicate-launch guard lists every process on the machine (~1 s); test_sidecar_duplicate_launch owns it.
        patcher = patch.object(fanout, "duplicate_launches", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.jobs = self.out / "jobs.json"
        self.jobs.write_text("[]", encoding="utf-8")

    def run_review(self, *, two=False, missing=False, same_claim=False, extra=(), must_read=None, reads=None):
        """One `--review` run; `reads` plants seat one's stream with those `(file_path, offset, limit)` Read calls."""
        subject = self.out / "subject"
        wanted = [CLAUDE_MD] if must_read is None else must_read
        for entry in wanted:
            target = subject / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(((ROOT / entry["path"]).read_text(encoding="utf-8") if (ROOT / entry["path"]).is_file()
                               else "line\n" * entry["lines"]), encoding="utf-8")
        jobs = [{"label": "one", "kind": "reviewer", "mandates": ["plc-instruction-quality"],
                 "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": wanted, "workdir": str(subject)}]
        if two:
            jobs.append({"label": "two", "kind": "reviewer", "mandates": ["plc-instruction-quality"],
                         "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": [CLAUDE_MD],
                         "workdir": str(subject)})

        def plan(job, _data, out, _authorize, stage=None):
            label = job["label"]
            return label, ["stub", "-R", str(out / f"{label}.record.json")], str(out / f"{label}.out.json")

        def execute(planned, _parallel, _out, after_seats=False):
            results = {}
            for label, _argv, output in planned:
                self.launched.append(label)
                if not (missing and label == "two"):
                    description = (("Same   CLAIM" if label == "two" else "same claim")
                                   if same_claim else "finding from " + label)
                    Path(output).write_text(json.dumps(reply(description)), encoding="utf-8")
                    results[label] = {"exit": 0, "stdout": output, "record": str(self.out / f"{label}.record.json")}
                    results[label]["stream"] = plant_stream(
                        self.out, label, reads if label == "one" and reads is not None else CLAUDE_READS)
                    Path(results[label]["record"]).write_text(json.dumps({
                        "exitCode": 0, "schemaValid": True, "engagement": "proven"}), encoding="utf-8")
                else:
                    results[label] = {"exit": 1, "stdout": output, "record": None}
            return results

        roster = {"transports": {}, "models": []}
        self.launched = []
        output = io.StringIO()
        with patch.object(fanout, "load_jobs", return_value=(jobs, None, None)), \
             patch.object(fanout.model_registry, "load", return_value=roster), \
             patch.object(fanout, "plan_job", side_effect=plan), \
             patch.object(fanout, "run_jobs", side_effect=execute), \
             patch.object(fanout, "summarize", return_value=("stub summary", 0)), \
             contextlib.redirect_stdout(output):
            try:
                code = fanout.main([str(self.jobs), "--out-dir", str(self.out), "--review",
                                    *extra])
            except SystemExit as exc:
                code = exc.code
        self.printed = output.getvalue()
        path = self.out / "review.json"
        return code, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def test_refold_preserves_failed_attempt_and_launches_only_authors(self):
        from types import SimpleNamespace
        subject = SimpleNamespace(root=self.out, manifest_path=self.out / "snap.json", files=["a.md"],
                                  new_paths=[], describe=lambda: {"root": str(self.out)})
        self.jobs.write_text(json.dumps([{"label": "finished-seat", "alias": "sonnet",
                                         "kind": "reviewer", "promptFile": "unused"}]), encoding="utf-8")
        review = {"status": "complete", "coverage": [], "view": [{"id": "F1"}],
                  "sources": [{"id": "F1", "finding": {"description": "planted"}}]}
        prior = {"review.json": json.dumps(review), "fold_digest.json": '{"status":"fail"}',
                 "post_check.json": '{"status":"fail"}', "review-fold-1_digest.json": "bad author"}
        for name, text in prior.items():
            (self.out / name).write_text(text, encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        planned = []

        def plan(job, _data, directory, _authorize, stage=None):
            planned.append(job["label"])
            return job["label"], ["stub", "-R", str(directory / (job["label"] + ".record.json"))], "unused"

        def post(args, directory, **kwargs):
            (directory / "post_check.json").write_text(json.dumps({
                "status": "fail", "counts": {}, "held": [], "foldReport": None}), encoding="utf-8")
            return 1

        with patch.object(fanout.fold_check, "parse_subject_spec", return_value=subject), \
             patch.object(fanout.fold_check, "route", return_value=({"F1": 0}, [], [])), \
             patch.object(fanout.fold_check, "independent_verdicts", return_value=({}, self.out / "verdicts.json")), \
             patch.object(fanout, "plan_job", side_effect=plan), \
             patch.object(fanout, "run_jobs", return_value={"review-fold-1": {"exit": 2}}), \
             patch.object(fanout, "close_review", side_effect=post), \
             patch.object(fanout.sidecar_completion, "publish", return_value={
                 "claims": [{"id": "F1", "verdict": "UNCHECKED"}], "verification": {"counts": {}}}), \
             patch.object(fanout, "publish_review_children"), \
             patch.object(fanout.fold_check, "_sources", return_value=["F1"]), \
             patch.object(fanout, "finish_fold", return_value={"status": "fail"}), \
             patch.dict(os.environ, HARNESS_HOOK_STATE_DIR=str(self.out / "state")):
            try:
                code = fanout.main([str(self.jobs), "--out-dir", str(self.out), "--refold",
                                    "--fold-alias", "sonnet", "--fold-effort", "medium",
                                    "--fold-subject", ".::snap.json::a.md"])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 1, "--refold must reach the fold and its failed post-check")
        self.assertEqual(planned, ["review-fold-1"], "no seat and no review-fold-0 probe")
        for name, content in before.items():
            self.assertEqual((self.out / name).read_bytes(), content, name)
        self.assertTrue((self.out / "refold" / "post_check.json").is_file())

    def test_frozen_ledger_inputs_reach_post_check_unchanged(self):
        roster, blobs = self.out / 'roster.json', self.out / 'blobs.json'
        roster.write_text('[]', encoding='utf-8')
        blobs.write_text('{}', encoding='utf-8')
        flags = ['--ledger-stage', 'candidate', '--ledger-subject', 'doc:x', '--ledger-roster', str(roster),
                 '--ledger-premise-commits', 'premises.json', '--ledger-head', 'frozen-head',
                 '--ledger-blobs', str(blobs)]
        with patch.object(fanout.post_check, 'main', return_value=0) as call:
            self.run_review(extra=flags)
        argv = call.call_args.args[0]
        for flag, value in [('--premise-commits', 'premises.json'), ('--head', 'frozen-head'), ('--blobs', str(blobs))]:
            self.assertEqual(argv[argv.index(flag) + 1], value)

    def test_a_review_out_dir_a_running_review_claimed_refuses_a_second_launch(self):
        (self.out / "review.claim").write_text("other-run", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, review = self.run_review()
        self.assertEqual(code, 2)
        self.assertEqual(self.launched, [])
        self.assertIn("review.claim", err.getvalue())

    def test_the_detached_driver_of_the_claiming_launch_runs(self):
        (self.out / "review.claim").write_text("this-run", encoding="utf-8")
        with patch.dict(os.environ, {"SIDECAR_REVIEW_CLAIM": "this-run"}):
            code, review = self.run_review()
        self.assertEqual(code, 0)
        self.assertEqual(self.launched, ["one"])

    def test_a_review_launch_claims_its_out_dir(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIDECAR_REVIEW_CLAIM", None)
            code, _review = self.run_review()
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "review.claim").exists())

    def test_selection_survives_one_review_and_one_shared_post_check(self):
        selection = {"subject": "session:trace", "lineageFingerprint": "accepted", "policyFingerprint": "policy"}
        self.jobs.write_text(json.dumps({"jobs": [], "reviewerSelection": selection}), encoding="utf-8")
        original = fanout.post_check.main
        with patch.object(fanout.post_check, "main", wraps=original) as call:
            code, review = self.run_review()
        self.assertEqual(code, 0)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(review["reviewerSelection"], selection)

    def test_one_valid_review_has_full_source_and_verdict(self):
        code, review = self.run_review()
        self.assertEqual(code, 0)
        self.assertEqual(review["counts"]["completed"], 1)
        self.assertEqual(review["sources"][0]["id"], "F1")
        self.assertEqual(review["verdicts"][0]["id"], "F1")
        self.assertEqual(review["verdicts"][0]["verdict"], "VERIFIED")
        self.assertEqual(review["counts"]["valid"], 1)

    def test_every_review_run_closes_with_an_in_process_post_check(self):
        code, review = self.run_review()
        self.assertEqual(code, 0)
        report = json.loads((self.out / "post_check.json").read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["counts"]["VERIFIED"], report["unverified"]), ("pass", 1, []))
        self.assertIsNone(report["foldReport"])
        self.assertIn("post-check: pass", self.printed)

    def test_a_clean_review_with_no_findings_still_closes_with_a_post_check(self):
        jobs = [{"label": "one", "kind": "reviewer", "mandates": ["plc-instruction-quality"],
                 "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": [CLAUDE_MD]}]

        def execute(planned, _parallel, _out, after_seats=False):
            results = {}
            for label, _argv, output in planned:
                Path(output).write_text(json.dumps({"type": "result", "is_error": False, "structured_output": {
                    "checked": {"toolsUsed": ["Read:x"], "stoppedAt": "nothing-in-scope", "basis": "b"},
                    "findings": [], "gaps": []}}), encoding="utf-8")
                record = self.out / f"{label}.record.json"
                record.write_text(json.dumps({"exitCode": 0, "schemaValid": True, "engagement": "proven"}), encoding="utf-8")
                results[label] = {"exit": 0, "stdout": output, "record": str(record),
                                  "stream": plant_stream(self.out, label, CLAUDE_READS)}
            return results

        with patch.object(fanout, "load_jobs", return_value=(jobs, None, None)), \
             patch.object(fanout.model_registry, "load", return_value={"transports": {}, "models": []}), \
             patch.object(fanout, "plan_job", side_effect=lambda job, *_, **__: (job["label"], ["stub"], str(self.out / "one.out.json"))), \
             patch.object(fanout, "run_jobs", side_effect=execute), \
             patch.object(fanout, "summarize", return_value=("stub summary", 0)), \
             contextlib.redirect_stdout(io.StringIO()):
            code = fanout.main([str(self.jobs), "--out-dir", str(self.out), "--review"])
        report = json.loads((self.out / "post_check.json").read_text(encoding="utf-8"))
        self.assertEqual((code, report["status"], report["counts"]["VERIFIED"]), (0, "pass", 0))

    def test_a_doctrine_refused_detach_leaves_the_out_dir_free_for_its_corrected_relaunch(self):
        jobs = [{"label": "one", "kind": "reviewer", "mandates": ["plc-instruction-quality"],
                 "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": [CLAUDE_MD]}]
        argv = [str(self.jobs), "--out-dir", str(self.out), "--review", "--detach"]
        with patch.object(fanout, "load_jobs", return_value=(jobs, None, None)), \
             patch.object(fanout.model_registry, "load", return_value={"transports": {}, "models": []}), \
             patch.object(fanout, "plan_job", side_effect=lambda job, *_, **__: (job["label"], ["stub"], str(self.out / "one.out.json"))), \
             patch.object(fanout, "detach_driver", return_value=0) as detach, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with patch.object(fanout, "doctrine_refusal", return_value="Dispatch DENIED: load the ladder"):
                refused = fanout.main(argv)
            self.assertFalse((self.out / fanout.REVIEW_CLAIM).exists(), "a refused launch claimed the out dir")
            with patch.object(fanout, "doctrine_refusal", return_value=None):
                relaunched = fanout.main(argv)
        self.assertNotEqual(refused, 0)
        self.assertEqual(relaunched, 0)
        self.assertEqual(detach.call_count, 1)
        self.assertTrue((self.out / fanout.REVIEW_CLAIM).exists())

    def test_missing_seat_is_uncovered_not_clean(self):
        code, review = self.run_review(two=True, missing=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(review["counts"]["uncovered"], 1)
        self.assertEqual(review["counts"]["completed"], 1)
        self.assertEqual(len(review["sources"]), 1)

    CLAUDE_MD = CLAUDE_MD
    IMPACT = {"path": "src/Core/ImpactPhysics.cs", "lines": 900}

    def test_a_seat_whose_stream_read_a_must_read_entry_only_in_part_fails_and_the_panel_names_it(self):
        code, review = self.run_review(must_read=[self.CLAUDE_MD, self.IMPACT],
                                       reads=[(".claude/CLAUDE.md", None, None), (self.IMPACT["path"], 1, 437)])
        self.assertNotEqual(code, 0)
        entry = review["coverage"][0]
        self.assertEqual((entry["status"], entry["unread"]), ("failed", [self.IMPACT["path"]]))
        self.assertTrue(any("read 1-437 of 900" in issue for issue in entry["issues"]), entry["issues"])
        self.assertIn("uncovered: one failed", self.printed)
        self.assertIn(self.IMPACT["path"], self.printed)
        self.assertEqual(self.launched, ["one"], "a failed seat is reported, never re-dispatched")

    def test_a_seat_whose_stream_reads_together_cover_a_must_read_entry_completes(self):
        code, review = self.run_review(must_read=[self.CLAUDE_MD, self.IMPACT],
                                       reads=[(".claude/CLAUDE.md", None, None), (self.IMPACT["path"], 438, 463),
                                              (self.IMPACT["path"], 1, 437)])
        self.assertEqual(code, 0, review["coverage"][0]["issues"])
        self.assertEqual(review["coverage"][0]["status"], "completed")

    def test_a_seat_that_left_a_must_read_entry_unread_fails_and_the_panel_names_it(self):
        code, review = self.run_review(must_read=[self.CLAUDE_MD, {"path": "src/never_read.cs", "lines": 10}])
        self.assertNotEqual(code, 0)
        entry = review["coverage"][0]
        self.assertEqual((entry["status"], entry["unread"]), ("failed", ["src/never_read.cs"]))
        self.assertEqual(review["counts"]["failed"], 1)
        self.assertIn("uncovered: one failed", self.printed)
        self.assertIn("src/never_read.cs", self.printed)
        self.assertEqual(self.launched, ["one"], "a failed seat is reported, never re-dispatched")

    def test_same_claim_seats_merge_and_selector_preserves_contributors(self):
        code, review = self.run_review(two=True, same_claim=True)
        self.assertEqual(code, 0)
        self.assertEqual([row["id"] for row in review["sources"]], ["F1", "F2"])
        self.assertEqual([row["id"] for row in review["view"]], ["M1"])
        self.assertEqual(review["view"][0]["merged_from"], ["F1", "F2"])
        self.assertEqual(review["merge"]["mode"], "exact")
        self.assertEqual(self.launched, ["one", "two"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = fanout.main(["--review-artifact", str(self.out / "review.json"), "--select", "M1"])
        self.assertEqual(selected, 0)
        merged = json.loads(output.getvalue())
        self.assertEqual(merged["id"], "M1")
        self.assertEqual(merged["finding"]["merged_from"], ["F1", "F2"])
        self.assertEqual([row["id"] for row in merged["sources"]], ["F1", "F2"])
        self.assertEqual([row["seat"] for row in merged["sources"]], ["one", "two"])
        self.assertEqual(merged["sources"][1]["finding"]["description"], "Same   CLAIM")

    def test_grouped_publication_preserves_distinct_original_verdicts(self):
        original_reply = reply
        def distinct(description):
            value = original_reply(description)
            if description == 'Same   CLAIM':
                value['structured_output']['findings'][0]['evidence'][0]['quote'] = 'absent fixture quote'
            return value
        with patch.dict(globals(), reply=distinct):
            code, review = self.run_review(two=True, same_claim=True)
        self.assertEqual(code, 0, 'an UNVERIFIED quote fails its claim, not the run')
        self.assertEqual(review['quoteCounts'], {'VERIFIED': 1, 'UNVERIFIED': 1, 'UNCHECKED': 0})
        self.assertEqual(review['view'][0]['verdict'], 'UNVERIFIED')
        claims = json.loads((self.out / 'claims.json').read_text())['claims']
        self.assertEqual({row['id']: row['verdict'] for row in claims},
                         {'F1': 'VERIFIED', 'F2': 'UNVERIFIED'})
        for label, verdict in [('one', 'VERIFIED'), ('two', 'UNVERIFIED')]:
            child = json.loads((self.out / (label + '.record.json.post_check.json')).read_text())
            self.assertEqual(child['verdicts'][label + '#1']['verdict'], verdict)

    def test_distinct_claims_on_one_file_stay_separate_without_a_merge_launch(self):
        code, review = self.run_review(two=True)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(row["id"] for row in review["view"]), ["F1", "F2"])
        self.assertEqual(review["merge"]["mode"], "exact")
        self.assertEqual(review["merge"]["issues"], [])
        self.assertEqual(self.launched, ["one", "two"])

    def test_retired_merge_flags_name_their_replacement(self):
        for flag in (["--merge-alias", "any"], ["--merge-effort", "low"]):
            with self.subTest(flag=flag):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = fanout.main([str(self.jobs), "--review", "--dry-run", *flag])
                self.assertEqual(code, 2)
                self.assertIn("retired", err.getvalue())
                self.assertIn("exact same-file, same-claim", err.getvalue())

    def test_select_unknown_id_fails_closed(self):
        self.run_review()
        err = io.StringIO()
        for missing in ("M1", "F9"):
            with self.subTest(missing=missing), contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fanout.main(["--review-artifact", str(self.out / "review.json"),
                                              "--select", missing]), 2)

    def test_select_returns_full_immutable_source(self):
        _code, review = self.run_review()
        self.assertIsNotNone(review)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = fanout.main(["--review-artifact", str(self.out / "review.json"), "--select", "F1"])
        self.assertEqual(selected, 0)
        finding = json.loads(output.getvalue())
        self.assertEqual(finding["id"], "F1")
        self.assertEqual(finding["finding"]["description"], "finding from one")
        self.assertEqual(len(finding["finding"]["evidence"]), 1)


PREFOLD = "# Plan\n\nStep 3: teh default is local.\n"


def plan_finding(plan, agent="plc-instruction-quality", line=3, old="teh default",
                 new="the default", description="typo"):
    return {"agent": agent, "mandate": agent, "action": "FIX", "category": "bug",
            "critical": False, "file": f"{plan}:{line}", "description": description,
            "rationale": "matched source", "old": old, "new": new,
            "evidence": [{"file": str(ROOT / '.claude/CLAUDE.md') + ':1', "quote": "@CLAUDE.core.md"}]}


class SidecarFoldCliTests(unittest.TestCase):
    """--review --fold-subject routes each finding to one subject, launches one author per affected subject
    (stubbed: no spend) and derives fold_digest.json from the in-process post-check, never from the author."""

    def setUp(self):
        # The duplicate-launch guard lists every process on the machine (~1 s); test_sidecar_duplicate_launch owns it.
        patcher = patch.object(fanout, "duplicate_launches", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.art = self.root.parent / (self.root.name + "_art")
        self.art.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.art, ignore_errors=True))
        self.out = self.art / "out"
        self.out.mkdir()
        self.jobs = self.art / "jobs.json"
        self.jobs.write_text("[]", encoding="utf-8")
        self.template = self.art / "fold_template.md"
        self.template.write_text("Fold the findings.\n", encoding="utf-8")
        self.plans, self.manifests = [], []
        for name in ("a.md", "b.md"):
            plan = self.root / name
            plan.write_text(PREFOLD, encoding="utf-8")
            manifest = self.art / f"{name}.snapshot.json"
            manifest.write_text(json.dumps(fold_check.snapshot_blobs(self.root, [name])), encoding="utf-8")
            self.plans.append(plan)
            self.manifests.append(manifest)

    def subject_args(self, count):
        args = []
        for plan, manifest in list(zip(self.plans, self.manifests))[:count]:
            args += ["--fold-subject", f"{self.root}::{manifest}::{plan.name}"]
        return args

    def run_fold(self, seats, subjects, author="good", extra=(), runtime=None, inventory=False):
        jobs = [{"label": label, "kind": "reviewer", "mandates": ["plc-instruction-quality"],
                 "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": [CLAUDE_MD]} for label in seats]
        self.launched, self.fold_prompts, self.fold_texts, self.specs = [], {}, [], {}

        def plan(job, _data, out, _authorize, stage=None):
            label = job["label"]
            self.specs[label] = job
            return label, ["stub", "-f", job.get("promptFile", ""), "-R", str(out / f"{label}.record.json")], \
                str(out / f"{label}.out.json")

        def act_as_author(argv):
            prompt = Path(argv[2]).read_text(encoding="utf-8")
            self.fold_texts.append(prompt)
            field = lambda name: next(l[len(name):].strip() for l in prompt.splitlines()  # noqa: E731,E741
                                      if l.startswith(name))
            root, files = Path(field("Root:")), field("Files:").split(", ")
            digest_path = Path(field("Digest file:"))
            self.assertTrue(Path(field("Verdicts file:")).is_file())
            ids = [row["id"] for row in json.loads(Path(field("Findings file:")).read_text(
                encoding="utf-8"))["findings"]]
            self.fold_prompts[files[0]] = ids
            if author == "good":
                (root / files[0]).write_text(PREFOLD.replace("teh default", "the default"), encoding="utf-8")
            digest_path.write_text(json.dumps({
                "applied": [{"id": i, "change": "typo", "source": "finding"} for i in ids], "deferred": [],
                "changedPaths": files if author == "good" else [],
            }), encoding="utf-8")

        real_run_jobs = fanout.run_jobs

        def execute(planned, _parallel, _out, after_seats=False):
            # A completed run persisted its inventory before launching (sidecar_fanout.run_jobs); the stub
            # must keep that fact, or main() writes launch-refused.txt for a run that never refused.
            fanout._LAUNCH["inventory"] = True
            if inventory:
                inert = [(label, [sys.executable, '-c', 'pass', '-R',
                                  str(_out / f'{label}.record.json')], output)
                         for label, _argv, output in planned]
                real_run_jobs(inert, _parallel, _out, after_seats=after_seats)
            results = {}
            for label, argv, output in planned:
                self.launched.append(label)
                if label.startswith("review-fold-"):
                    act_as_author(argv)
                else:
                    Path(output).write_text(json.dumps({"type": "result", "is_error": False,
                                                        "structured_output": {
                        "checked": {"toolsUsed": ["Read:x"], "stoppedAt": "exhausted-leads",
                                    "basis": "plc-instruction-quality: findings"},
                        "findings": seats[label], "gaps": []}}), encoding="utf-8")
                record = _out / f"{label}.record.json"
                record.write_text(json.dumps({"exitCode": 0, "schemaValid": True,
                                              "engagement": "proven", "label": label,
                                              **(runtime or {})}), encoding="utf-8")
                results[label] = {"exit": 1 if author == "crash" and label.startswith("review-fold-") else 0,
                                 "stdout": output, "record": str(record)}
                if not label.startswith("review-fold-"):
                    results[label]["stream"] = plant_stream(self.out, label, CLAUDE_READS)
            return results

        args = [str(self.jobs), "--out-dir", str(self.out), "--review", "--fold", str(self.template),
                "--fold-alias", "any", "--fold-effort", "medium", *self.subject_args(subjects)]
        err = io.StringIO()
        with patch.object(fanout, "load_jobs", return_value=(jobs, None, None)), \
             patch.object(fanout.model_registry, "load", return_value={"transports": {}, "models": []}), \
             patch.object(fanout, "plan_job", side_effect=plan), \
             patch.object(fanout, "run_jobs", side_effect=execute), \
             patch.object(fanout, "summarize", return_value=("stub summary", 0)), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = fanout.main([*args, *extra])
        path = self.out / "fold_digest.json"
        return code, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None, err.getvalue()

    def post_check(self):
        return json.loads((self.out / "post_check.json").read_text(encoding="utf-8"))

    def incomplete_report(self, corrupt):
        def refuse(argv, **kwargs):
            if corrupt:
                Path(argv[argv.index('--out') + 1]).write_text('{')
            return 0
        with patch.object(fanout.post_check, 'main', side_effect=refuse):
            code, digest, err = self.run_fold({'one': [plan_finding(self.plans[0])]}, 1)
        self.assertEqual(code, 1, err)
        self.assertEqual(digest['status'], 'fail', 'missing report reads as a passing fold')
        review = json.loads((self.out / 'review.json').read_text())
        self.assertEqual(review['verification']['status'], 'unavailable')
        self.assertEqual(review['view'][0]['verdict'], 'UNCHECKED')

    def test_fold_and_refold_inventory_flags(self):
        seats = {'one': [plan_finding(self.plans[0])]}
        code, _digest, err = self.run_fold(seats, 1, author='crash', inventory=True)
        self.assertEqual(code, 1, err)
        inventories = list(self.out.glob('fanout-*.inventory.json'))
        self.assertEqual(len(inventories), 2)
        for path in inventories:
            self.assertIs(json.loads(path.read_text()).get('afterSeats'), True)
        code, _digest, err = self.run_fold(seats, 1, extra=('--refold',), inventory=True)
        self.assertEqual(code, 0, err)
        inventories = list((self.out / 'refold').glob('fanout-*.inventory.json'))
        self.assertEqual(len(inventories), 1)
        self.assertIs(json.loads(inventories[0].read_text()).get('afterSeats'), True)

    def test_refold_real_post_check_and_finish_keep_every_prior_byte(self):
        seats = {"one": [plan_finding(self.plans[0])]}
        code, digest, err = self.run_fold(seats, 1, author="crash")
        self.assertEqual((code, digest["status"]), (1, "fail"), err)
        for attempt in ("refold", "refold.attempt2"):
            before = {p: p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
            code, _digest, err = self.run_fold(seats, 1, extra=("--refold",))
            self.assertEqual(code, 0, err)
            self.assertEqual(self.launched, ["review-fold-1"])
            self.assertNotIn("review-fold-0", self.specs)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content, str(path))
            new = self.out / attempt
            self.assertEqual(json.loads((new / "fold_digest.json").read_text())["status"], "pass")
            self.assertEqual(json.loads((new / "post_check.json").read_text())["status"], "pass")

    def test_refold_dry_run_plans_authors_without_dispatching(self):
        seats = {"one": [plan_finding(self.plans[0])]}
        self.run_fold(seats, 1, author="crash")
        before = {p: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        code, _digest, err = self.run_fold(seats, 1, extra=("--refold", "--dry-run"))
        self.assertEqual(code, 0, err)
        self.assertEqual(list(self.specs), ["review-fold-1"])
        self.assertEqual(self.launched, [])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_refold_detach_preserves_the_original_driver_log(self):
        from types import SimpleNamespace
        seats = {"one": [plan_finding(self.plans[0])]}
        self.run_fold(seats, 1, author="crash")
        (self.out / "fanout-driver.log").write_text("original run\n", encoding="utf-8")
        before = {p: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        detach = fanout.detach_driver
        spawned = []

        def driver(*args, **kwargs):
            with patch.object(fanout.subprocess, "Popen", return_value=SimpleNamespace(pid=123)) as spawn:
                code = detach(*args, **kwargs)
                spawned.append(spawn.call_args)
                return code

        # The dispatch-doctrine gate a --detach call now runs (sidecar_fanout.doctrine_refusal) is not under
        # test here: its own proof is test_sidecar_fanout_detach.py. Mark the doctrine loaded in a throwaway
        # session state, as that proof does.
        sys.path.insert(0, str(ROOT / ".claude" / "hooks"))
        import _hook_state
        sid, state_dir = "refold_detach", tempfile.mkdtemp(prefix="refold_state_")
        saved = {k: os.environ.get(k) for k in ("HARNESS_HOOK_STATE_DIR", "CLAUDE_CODE_SESSION_ID",
                                                "SIDECAR_DISPATCH_OWNER")}
        os.environ["HARNESS_HOOK_STATE_DIR"], os.environ["CLAUDE_CODE_SESSION_ID"] = state_dir, sid
        os.environ.pop("SIDECAR_DISPATCH_OWNER", None)
        _hook_state.mark_skill_in_context(sid, "orchestration")
        _hook_state.update_json_locked(_hook_state.state_path(sid),
                                       lambda state: (state.__setitem__("model_ladder_ready", True), True)[1])
        try:
            with patch.object(fanout, "detach_driver", side_effect=driver):
                code, _digest, err = self.run_fold(seats, 1, extra=("--refold", "--detach"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual(code, 0, err)
        self.assertEqual(self.launched, [])
        self.assertIn("--refold", spawned[0].args[0])
        self.assertNotIn("--detach", spawned[0].args[0])
        self.assertEqual(Path(spawned[0].kwargs["stdout"].name), self.out / "refold-driver.log")
        self.assertFalse((self.out / "refold").exists(), "only the driver claims its fold directory")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_one_affected_subject_keeps_selection_and_fold_record_reference(self):
        selection = {"subject": "doc:a", "lineageFingerprint": "accepted", "policyFingerprint": "policy"}
        self.jobs.write_text(json.dumps({"jobs": [], "reviewerSelection": selection}), encoding="utf-8")
        original = fanout.post_check.main
        with patch.object(fanout.post_check, "main", wraps=original) as call:
            code, digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.launched.count("review-fold-1"), 1)
        self.assertEqual(call.call_count, 1)
        jobs = json.loads((self.out / "fold_jobs.json").read_text())
        self.assertEqual(jobs[0]["foldActor"], {"run": str(self.out / "review-fold-1.record.json"),
                                             "label": "review-fold-1", "source": "sidecar"})
        self.assertEqual(digest["reviewerSelection"], selection)
        self.assertEqual(self.post_check()["reviewerSelection"], selection)

    @needs_plc_mandate
    def test_candidate_executor_plan_fold_record_and_sidecar_metrics_join(self):
        import task_record
        import orchestration_metrics
        data = fanout.model_registry.load()
        executor = fanout.model_registry.resolve("sol", data)["id"]
        reviewer = fanout.model_registry.resolve("opus", data)["id"]
        subprocess.run(["git", "-C", str(self.root), "add", "a.md", "b.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qm", "fixture subject"], check=True)
        (self.root / ".claude").mkdir(exist_ok=True)
        (self.root / ".claude" / "CLAUDE.md").write_text((ROOT / ".claude/CLAUDE.md").read_text(encoding="utf-8"),
                                                         encoding="utf-8")
        with open(self.root / ".git" / "info" / "exclude", "a", encoding="utf-8") as exclude:
            exclude.write(".claude/\n")
        env = {"HARNESS_TASK_RECORD_DIR": str(self.art / "tasks"), "HARNESS_REVIEW_LEDGER": str(self.art / "ledger.jsonl"),
               "CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_CODE_SESSION_ID": "tracer-session"}
        actor = self.art / "executor.record.json"
        actor.write_text(json.dumps({"transport": "codex", "attestedModel": executor}), encoding="utf-8")
        roster = self.art / "roster.json"
        roster.write_text(json.dumps([{"key": "one", "mandates": ["plc-instruction-quality"],
                                      "tier": "architect", "effort": "high"}]), encoding="utf-8")
        with patch.dict(os.environ, env):
            task_record.open_record("trace", "tracer-session", "executor")
            task_record.add_job("trace", str(actor), "exec", "sidecar", "launched", subject=["a.md"])
            task_record.add_job("trace", str(actor), "exec", "sidecar", "done")
            task_record.add_job("trace", str(actor), "exec", "sidecar", "accepted", role="executor", subject=["a.md"])
            code, out, err = fanout.post_check.ledger(self.root, "plan", "--stage", "candidate", "--subject", "doc:a",
                "--task", "trace", "--roster", str(roster), "--files", "a.md", "--base-ref", "HEAD")
            self.assertEqual(code, 0, err)
            planned = json.loads(out)
            selection = planned["reviewerSelection"]
            self.assertEqual([row["group"] for row in selection["primary"]], ["openai"])
            roster.write_text(json.dumps(planned), encoding="utf-8")
            self.jobs.write_text(json.dumps(dict(planned, jobs=[])), encoding="utf-8")
            runtime = {"transport": "anthropic", "modelVersion": reviewer, "tier": "architect", "effort": "high",
                       "parentSessionId": "tracer-session", "toolProfile": {"shape": "review", "tier": "architect"}}
            original = fanout.post_check.record_panel
            with patch.object(fanout.post_check, "record_panel", wraps=original) as call:
                code, digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1, runtime=runtime,
                    extra=("--root", str(self.root), "--ledger-stage", "candidate", "--ledger-subject", "doc:a",
                           "--ledger-task", "trace", "--ledger-roster", str(roster)))
            self.assertEqual(code, 0, err)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(self.launched.count("review-fold-1"), 1)
            self.assertEqual(digest["reviewerSelection"], selection)
            review = json.loads((self.out / "review.json").read_text())
            self.assertEqual((review["coverage"][0]["model"], review["coverage"][0]["modelObserved"]), (reviewer, True))
            rows = [json.loads(line) for line in Path(env["HARNESS_REVIEW_LEDGER"]).read_text().splitlines()]
            panel = [row for row in rows if row["reviewer"] == "panel"]
            self.assertTrue(panel)
            self.assertTrue(all(row["reviewerSelection"] == selection and row["model"] == reviewer for row in panel))
            metric = orchestration_metrics._sidecar_record_row(str(self.out / "one.record.json"))
            orchestration_metrics.attach_review_ledger([metric], rows)
            self.assertEqual(metric["reviewer_selection"], selection)
            self.assertEqual(metric["reviewer_model"], reviewer)

    def test_missing_report_never_passes_fold_digest(self):
        self.incomplete_report(False)

    def test_unreadable_report_never_passes_fold_digest(self):
        self.incomplete_report(True)

    def test_fold_precheck_failure_still_closes_every_seat(self):
        seats = {'one': [plan_finding(self.plans[0])],
                 'two': [plan_finding(self.plans[1], description='second')]}
        with patch.object(fanout, 'run_review_fold', side_effect=fanout.JobError('planted precheck failure')):
            code, digest, err = self.run_fold(seats, 2)
        self.assertEqual(code, 1)
        self.assertIsNone(digest)
        self.assertTrue((self.out / 'post_check.json').is_file(), err)
        report = self.post_check()
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(set(report['verdicts']), {'F1', 'F2'})
        self.assertTrue(any('planted precheck failure' in str(error) for error in report['errors']))
        for label in seats:
            child = json.loads((self.out / (label + '.record.json.claims.json')).read_text())
            self.assertEqual(child['verification']['status'], 'fail')

    def audit_fold_quote(self, quote):
        sys.path.insert(0, str(ROOT / '.claude/hooks'))
        import dispatch_table
        finding = plan_finding(self.plans[0])
        finding['evidence'] = [{'file': 'a.md:3', 'quote': quote}]
        self.run_fold({'one': [finding]}, 1)
        entry = {'kind': 'sidecar', 'id': 'one', 'record': str(self.out / 'one.record.json')}
        rows, _, _, _, errors = dispatch_table._audit_source(entry, None, {'cwd': str(self.root)})
        self.assertEqual(errors, [])
        return dispatch_table._audit_check(rows, str(self.root), 'fixture', __import__('time').monotonic() + 5)

    def test_stop_audit_uses_prefold_snapshot_after_fold_edit(self):
        report = self.audit_fold_quote('Step 3: teh default is local.')
        self.assertNotIn('teh default', self.plans[0].read_text())
        self.assertEqual(report['verdicts']['one#1']['verdict'], 'VERIFIED', report)

    def test_stop_audit_refutes_quote_only_present_after_fold(self):
        report = self.audit_fold_quote('Step 3: the default is local.')
        self.assertEqual(report['verdicts']['one#1']['verdict'], 'UNVERIFIED', report)

    def test_snapshot_quote_and_command_checked_once_through_fold(self):
        import verify_claims
        finding = plan_finding(self.plans[0])
        finding['evidence'] = [{'file': 'a.md:3', 'quote': 'Step 3: teh default is local.'},
                               {'command': 'git status --short', 'output': ' M a.md'}]
        with patch.object(verify_claims, '_run_git', return_value=(0, ' M a.md', '')) as check:
            code, digest, err = self.run_fold({'one': [finding]}, 1)
        self.assertEqual(code, 0, err)
        self.assertEqual(check.call_count, 1)
        self.assertNotIn('teh default', self.plans[0].read_text())
        review = json.loads((self.out / 'review.json').read_text())
        self.assertEqual(review['view'][0]['verdict'], 'VERIFIED')
        self.assertEqual(self.post_check()['counts']['VERIFIED'], 1)
        self.assertEqual(digest['status'], 'pass')

    def test_rev_snapshot_quote_remains_verified_after_edit(self):
        subprocess.run(['git', '-C', str(self.root), 'add', 'a.md', 'b.md'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture base'], check=True)
        self.manifests[0].write_text(json.dumps(fold_check.snapshot_rev(self.root, 'HEAD', ['a.md'])))
        finding = plan_finding(self.plans[0])
        finding['evidence'] = [{'file': 'a.md:3', 'quote': 'Step 3: teh default is local.'}]
        code, digest, err = self.run_fold({'one': [finding]}, 1)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.post_check()['verdicts']['F1']['verdict'], 'VERIFIED')
        self.assertEqual(digest['status'], 'pass')

    def test_one_subject_fold_passes_the_shared_check_and_writes_post_check_json(self):
        code, digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1)
        self.assertEqual(code, 0, err + json.dumps(digest))
        self.assertEqual(digest["status"], "pass")
        self.assertEqual([(d["id"], d["disposition"]) for d in digest["dispositions"]],
                         [("F1", "applied")])
        self.assertEqual(self.launched, ["one", "review-fold-1"])
        self.assertFalse((self.out / "review-fold-1_prefold.md").exists(), "the engine copies no file any more")
        report = self.post_check()
        self.assertEqual((report["status"], report["changedPaths"]), ("pass", ["a.md"]))
        self.assertIsNotNone(report["foldReport"])

    def test_the_author_job_runs_in_the_subject_root_and_the_prompt_names_the_subject(self):
        self.run_fold({"one": [plan_finding(self.plans[0])]}, 1)
        spec = self.specs["review-fold-1"]
        self.assertEqual(Path(spec["workdir"]).resolve(), self.root)
        (prompt,) = self.fold_texts
        self.assertTrue(prompt.startswith("FOLD STAGE of a review."))
        self.assertIn(f"Root: {self.root}".replace("\\", "/"), prompt.replace("\\", "/"))
        self.assertIn("Files: a.md", prompt)
        self.assertNotIn("Pre-fold copy", prompt)
        self.assertNotIn("planPath", prompt)
        self.assertNotIn("verdictPath", prompt)

    def test_fold_digest_lists_each_applied_resolved_plan_with_its_alternative(self):
        resolved = dict(plan_finding(self.plans[0], description="pick the default"), action="PLAN",
                        question=None, scope=["a.md"],
                        options=["the default", "keep teh; wins when the typo is a name"])
        open_plan = dict(resolved, description="open fork", question="Which default?")
        single = dict(resolved, description="one option", options=["the default"])
        fix = plan_finding(self.plans[0], description="typo")
        _code, digest, err = self.run_fold({"one": [resolved, open_plan, single, fix]}, 1)
        self.assertIsNotNone(digest, err)
        self.assertEqual(digest.get("appliedWithAlternative"),
                         [{"id": "F1", "applied": "the default",
                           "alternative": "keep teh; wins when the typo is a name"}])

    def test_self_reported_pass_over_an_unchanged_file_fails(self):
        code, digest, _ = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1, author="liar")
        self.assertEqual(code, 1)
        self.assertEqual(digest["status"], "fail")
        kinds = {f["kind"] for f in digest["failures"]}
        self.assertIn("fold-unchanged-file", kinds)
        self.assertIn("fold-not-applied", kinds)
        self.assertEqual(self.post_check()["status"], "fail")

    def test_split_subjects_launch_one_author_per_affected_subject_and_hold_an_outside_file(self):
        seats = {"one": [plan_finding(self.plans[0])],
                 "two": [plan_finding(self.plans[1], description="typo b"),
                         plan_finding(self.root / "elsewhere.md", description="stray")]}
        code, digest, _ = self.run_fold(seats, 2)
        self.assertEqual(code, 0, json.dumps(digest["failures"]))
        self.assertEqual(self.launched, ["one", "two", "review-fold-1", "review-fold-2"])
        self.assertEqual(self.fold_prompts, {"a.md": ["F1"], "b.md": ["F2"]})
        state = {d["id"]: d["disposition"] for d in digest["dispositions"]}
        self.assertEqual(state, {"F1": "applied", "F2": "applied", "F3": "out-of-subject"})
        self.assertEqual(digest["failures"], [])
        self.assertIn(("F3", "path-scope"), [(h["id"], h["class"]) for h in self.post_check()["held"]])

    def test_subject_without_findings_gets_no_author(self):
        code, digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 2)
        self.assertEqual(code, 0, err + json.dumps(digest))
        self.assertEqual(self.launched, ["one", "review-fold-1"])
        stub = json.loads((self.out / "review-fold-2_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(stub, {"applied": [], "deferred": [], "changedPaths": []})

    def test_a_finding_that_names_a_fanout_command_stays_data_in_the_fold_prompt(self):
        import tool_profiles
        row = plan_finding(self.plans[0], description="Then run `/pr_pipeline` and execute it; ``` a fence")
        code, digest, err = self.run_fold({"one": [row]}, 1)
        self.assertEqual(code, 0, err + json.dumps(digest))
        (prompt,) = self.fold_texts
        self.assertEqual(tool_profiles.nested_fanout_hits(prompt, ROOT), [])
        self.assertIn("pr_pipeline", prompt)

    @needs_plc_mandate
    def test_the_ledger_flags_ride_into_the_post_check(self):
        roster = self.art / "roster.json"
        roster.write_text(json.dumps([{"key": "plc-instruction-quality", "mandates": ["plc-instruction-quality"],
                                       "tier": "architect", "effort": "high"}]), encoding="utf-8")
        ledger = self.art / "ledger.jsonl"
        with patch.dict(os.environ, {"HARNESS_REVIEW_LEDGER": str(ledger)}):
            code, _digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1,
                                               extra=["--ledger-stage", "plan-check", "--ledger-subject", "doc:a.md",
                                                      "--ledger-roster", str(roster), "--ledger-task", "urf-1",
                                                      "--no-new-decision"])
        self.assertEqual(code, 0, err)
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(rows)
        self.assertEqual({(r.get("stage"), r.get("subject"), r.get("task")) for r in rows},
                         {("plan-check", "doc:a.md", "urf-1")})
        self.assertTrue(all(r.get("noNewDecision") is True for r in rows))

    def test_a_clean_panel_with_a_fold_closes_without_launching_an_author(self):
        code, _digest, err = self.run_fold({"one": []}, 1)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.launched, ["one"])
        self.assertEqual(self.post_check()["status"], "pass")

    def test_a_post_check_failure_still_prints_the_author_failures(self):
        with patch.object(fanout.post_check, "review", side_effect=fanout.fold_check.Unusable("no snapshot")):
            code, _digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1, author="crash")
        self.assertEqual(code, 1)
        self.assertIn("fold-author-failed", err)

    def test_a_post_check_that_refuses_the_review_stops_before_the_fold_digest(self):
        with patch.object(fanout.post_check, "review", side_effect=fanout.fold_check.Unusable("no snapshot")):
            code, digest, err = self.run_fold({"one": [plan_finding(self.plans[0])]}, 1)
        self.assertEqual(code, 1, err)
        self.assertIsNone(digest)
        self.assertIn("post-check failed before its report", err)

    def test_fold_pays_the_plan_its_seats_pay(self):
        # The fold is one more job on the review's currency: with every seat on one paying plan it
        # pays that plan too, an explicit --fold-plan wins, and seats on several plans leave it unset.
        ns = lambda **kw: type("A", (), dict({"fold": "t.md", "fold_alias": "a", "fold_effort": "medium",
                                              "fold_plan": None}, **kw))()
        seats = [{"label": "a", "plan": "anthropic-secondary"}, {"label": "b", "plan": "anthropic-secondary"}]
        self.assertEqual(fanout.fold_probe(ns(), seats).get("plan"), "anthropic-secondary")
        self.assertEqual(fanout.fold_probe(ns(fold_plan="go"), seats).get("plan"), "go")
        mixed = seats + [{"label": "c", "plan": "go"}]
        self.assertNotIn("plan", fanout.fold_probe(ns(), mixed))
        self.assertNotIn("plan", fanout.fold_probe(ns(), [{"label": "d"}]))

    def test_the_author_job_keeps_its_paying_plan_when_its_workdir_changes(self):
        self.run_fold({"one": [plan_finding(self.plans[0])]}, 1, extra=["--fold-plan", "anthropic-secondary"])
        spec = self.specs["review-fold-1"]
        self.assertEqual((spec["plan"], Path(spec["workdir"]).resolve()), ("anthropic-secondary", self.root))

    def test_a_defaulted_seat_pays_the_plan_with_most_room_and_the_fold_takes_it_only_when_legal_for_it(self):
        data = {"transports": {"ta": {}, "ta-secondary": {"planOf": "ta"}, "tb": {}}, "models": [
            {"id": "m1", "alias": "m1", "transport": "ta"}, {"id": "m2", "alias": "m2", "transport": "ta"},
            {"id": "m3", "alias": "m3", "transport": "tb"}]}
        ns = lambda **kw: type("A", (), dict({"fold": "t.md", "fold_alias": "m1", "fold_effort": "medium",
                                              "fold_plan": None}, **kw))()
        defaulted = [{"label": "a", "alias": "m1"}, {"label": "b", "alias": "m2"}]
        rooms = {"ta": 0.2, "ta-secondary": 1.1}
        saved = fanout.plan_room
        fanout.plan_room = lambda t, d, r, n: {"transport": t, "room": rooms[t]}
        self.addCleanup(setattr, fanout, "plan_room", saved)
        self.assertEqual(fanout.fold_probe(ns(), defaulted, data).get("plan"), "ta-secondary")
        self.assertNotIn("plan", fanout.fold_probe(ns(fold_alias="m3"), defaulted, data))
        mixed = defaulted + [{"label": "c", "alias": "m1", "plan": "ta"}]
        self.assertNotIn("plan", fanout.fold_probe(ns(), mixed, data))
        self.assertEqual(fanout.fold_probe(ns(), [{"label": "a", "alias": "m1", "plan": "ta-secondary"}],
                                           data).get("plan"), "ta-secondary")

    def test_fold_arguments_are_checked_before_any_launch(self):
        retired = ["--plan", str(self.plans[0])]
        cases = {
            "missing template": ["--fold", str(self.root / "none.md")],
            "missing subject manifest": ["--fold-subject", f"{self.root}::{self.art / 'none.json'}::a.md"],
            "malformed subject": ["--fold-subject", "a.md"],
            "a retired --plan": retired,
        }
        for label, extra in cases.items():
            with self.subTest(label):
                err = io.StringIO()
                with patch.object(fanout, "run_jobs", side_effect=AssertionError("launched")), \
                        contextlib.redirect_stderr(err):
                    args = [str(self.jobs), "--out-dir", str(self.out), "--review",
                            "--fold", str(self.template), "--fold-alias", "any",
                            "--fold-effort", "medium", *self.subject_args(1), *extra]
                    with patch.object(fanout, "load_jobs", return_value=([
                            {"label": "one", "kind": "reviewer", "mandates": ["m"],
                             "contextFiles": [str(ROOT / ".claude/CLAUDE.md")], "mustRead": [CLAUDE_MD]}], None, None)):
                        self.assertEqual(fanout.main(args), 2)
                if label == "a retired --plan":
                    self.assertIn("--fold-subject", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fanout.main([str(self.jobs), "--fold", str(self.template)]), 2)
            self.assertEqual(fanout.main([str(self.jobs), "--review", "--ledger-stage", "plan-check"]), 2)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_cases, unittest_cases
    sys.exit(run_cases(unittest_cases(SidecarReviewCliTests, SidecarFoldCliTests)))
