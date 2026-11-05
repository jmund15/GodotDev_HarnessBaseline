"""post_check.py closes a findings dispatch: snapshot, quote verdicts, fold checks, ledger rows, next-round seats."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".claude/tools"
sys.path.insert(0, str(TOOLS))
import fold_check  # noqa: E402
import post_check  # noqa: E402

PRE = "# Plan\n\nStep 3: teh default is local.\nStep 9: pick a transport.\n"
POST = "# Plan\n\nStep 3: the default is local.\nStep 9: pick a transport.\n"
MANDATE = "plc-pattern-fit"
SUBJECT = "doc:plan.md"


def git(root, *args, input_text=None):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True, input=input_text).stdout


class Env:
    """A temp git checkout holding one plan, a scratch ledger and the roster/findings/digest files."""

    def __init__(self, test):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        git(self.root, "init", "-q")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "init")
        self.plan = self.root / "plan.md"
        self.ledger = self.root / "ledger.jsonl"
        self.escape = self.root / "escape.jsonl"
        self.roster = self.root / "roster.json"
        self.roster.write_text(json.dumps([{"key": MANDATE, "mandates": [MANDATE], "tier": "executor",
                                            "effort": "high"}]), encoding="utf-8")
        self.manifest = self.root / "snapshot.json"
        self.digest_path = self.root / "digest.json"
        self.verdicts = self.root / "verdicts.json"
        self.out = self.root / "post_check.json"
        self.saved = {k: os.environ.get(k) for k in ("HARNESS_REVIEW_LEDGER", "HARNESS_ESCAPE_LOG")}
        os.environ["HARNESS_REVIEW_LEDGER"] = str(self.ledger)
        os.environ["HARNESS_ESCAPE_LOG"] = str(self.escape)
        test.addCleanup(self.restore)

    def restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def ledger_tool(self, *args):
        done = subprocess.run([sys.executable, str(TOOLS / "review_ledger.py"), *args], cwd=self.root,
                              capture_output=True, text=True)
        return done

    def plant_panel_row(self, mandate=MANDATE, path="plan.md"):
        done = self.ledger_tool("record", "--stage", "plan-check", "--subject", SUBJECT, "--inline", "panel",
                                "--outcome", "clean", "--mandate", mandate, "--files", path,
                                "--author-model", "unknown")
        assert done.returncode == 0, done.stderr

    def ledger_rows(self):
        done = self.ledger_tool("rows", "--subject", SUBJECT, "--json")
        return json.loads(done.stdout or "[]")

    def snapshot(self, files=("plan.md",), pre=None):
        if pre is not None:
            self.plan.write_text(pre, encoding="utf-8")
        code, _ = run(["snapshot", "--root", str(self.root), "--files", *files, "--out", str(self.manifest)])
        assert code == 0

    def findings(self, rows):
        path = self.root / "findings.json"
        path.write_text(json.dumps({"findings": rows}), encoding="utf-8")
        return path

    def fix_row(self, **over):
        row = {"id": "F1", "action": "FIX", "file": f"{self.plan}:3", "description": "typo", "agent": MANDATE,
               "old": "teh default", "new": "the default",
               "evidence": [{"file": f"{self.plan}:3", "quote": "teh default is local"}]}
        row.update(over)
        return row

    def digest(self, **over):
        value = {"applied": [{"id": "F1", "change": "typo", "source": "finding"}], "deferred": [],
                 "changedPaths": ["plan.md"]}
        value.update(over)
        self.digest_path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def review(self, rows, extra=()):
        return run(["review", "--findings", str(self.findings(rows)), "--digest", str(self.digest_path),
                    "--snapshot", str(self.manifest), "--stage", "plan-check", "--subject", SUBJECT,
                    "--roster", str(self.roster), *extra, "--out", str(self.out)])

    def report(self):
        return json.loads(self.out.read_text(encoding="utf-8"))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = post_check.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue() + err.getvalue()


class LongArgvTests(unittest.TestCase):
    def test_a_ledger_call_longer_than_a_command_line_reaches_the_ledger(self):
        env = Env(self)
        files = [f".claude/commands/subject_list_case_{i:05d}.md" for i in range(1500)]
        self.assertGreater(sum(len(f) + 1 for f in files), 32767)
        code, out, err = post_check.ledger(env.root, "uncovered", "--files", *files, "--mandates", MANDATE,
                                           "--roster", str(env.roster))
        self.assertEqual(code, 0, err[-400:])
        self.assertIn("subject_list_case_01499.md", out + err)


class InRunLedgerTests(unittest.TestCase):
    def test_live_panel_records_frozen_premises_without_a_host_record(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        git(env.root, 'add', 'plan.md')
        git(env.root, 'commit', '-qm', 'reviewed premise')
        head = git(env.root, 'rev-parse', 'HEAD').strip()
        blob = git(env.root, 'rev-parse', 'HEAD:plan.md').strip()
        mandate = 'harness-quality'
        env.roster.write_text(json.dumps([{'key': mandate, 'mandates': [mandate], 'tier': 'executor'}]))
        directory = env.root / 'sessions/s1/subagents/workflows/wf_live'
        directory.mkdir(parents=True)
        body = {'checked': {'toolsUsed': ['Read:plan.md'], 'stoppedAt': 'nothing-in-scope',
                            'basis': mandate + ': clean', 'mandates': {mandate: 'clean'}},
                'findings': [], 'gaps': []}
        events = [{'type': 'started', 'key': 'v2:live-seat', 'agentId': 'seat', 'label': 'review:' + mandate, 'phase': 'Review'},
                  {'type': 'result', 'key': 'v2:live-seat', 'agentId': 'seat', 'result': body}]
        (directory / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (directory / 'agent-seat.meta.json').write_text(json.dumps({'agentType': 'workflow-subagent', 'model': 'served-test'}))
        seats = env.root / 'seats.json'
        seats.write_text(json.dumps({'review:' + mandate: {'model': 'requested-test', 'effort': 'high',
                               'tier': 'executor', 'mandates': [mandate], 'mustRead': []}}))
        premises, blobs, scope = [env.root / n for n in ('premises.json', 'blobs.json', 'scope.json')]
        premises.write_text(json.dumps([head]))
        blobs.write_text(json.dumps({'plan.md': blob}))
        scope.write_text(json.dumps({'files': ['plan.md']}))
        self.assertFalse((directory.parents[2] / 'workflows/wf_live.json').exists())
        code, text = run(['review', '--workflow-dir', str(directory), '--seats-file', str(seats),
                          '--root', str(env.root), '--expect-total', '0', '--stage', 'candidate',
                          '--subject', SUBJECT, '--roster', str(env.roster), '--scope-json', str(scope),
                          '--premise-commits', str(premises), '--head', head, '--blobs', str(blobs),
                          '--out', str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.report()['ledger']['panelRows'], 1)
        row = env.ledger_rows()[0]
        self.assertEqual((row['mandate'], row['premise_commits'], row['head']), (mandate, [head], head))
        self.assertEqual(row['files'], {'plan.md': blob})
        self.assertEqual((row['model'], row['modelObserved']), ('served-test', True))

    def test_frozen_blobs_name_the_recorded_files_without_a_fold_or_scope(self):
        # Live B6 audit wf_d79fc27c-7eb: a fold-less harness review passed --blobs but no scope, and its
        # rows recorded `files: {}`, so the push guard still owed both doctrine paths.
        with tempfile.TemporaryDirectory() as tmp:
            blobs = Path(tmp) / 'blobs.json'
            blobs.write_text(json.dumps({'b.md': 'b' * 40, 'a.md': 'a' * 40}))
            args = post_check.build_parser().parse_args(['review', '--workflow-dir', 'live', '--seats-file', 'seats.json',
                '--stage', 'candidate', '--subject', SUBJECT, '--roster', 'roster.json', '--blobs', str(blobs),
                '--out', 'out.json'])
            src = post_check.Source()
            src.record = ('--workflow-dir', 'live')
            with mock.patch.object(post_check, 'ledger', return_value=(0, '', '')) as call:
                post_check.record_panel(ROOT, args, src, [])
        argv = list(call.call_args.args[1:])
        self.assertIn('--files', argv)
        self.assertEqual(argv[argv.index('--files') + 1:argv.index('--files') + 3], ['a.md', 'b.md'])

    def test_a_foldless_review_with_no_blobs_or_scope_refuses_to_record_a_row_that_covers_nothing(self):
        # Session 69255593: a fold-less architecture seat recorded `files: {}`; its review covered no file and
        # nothing said so, so whole-scope and the push guard still owed every file it read.
        args = post_check.build_parser().parse_args(['review', '--sidecar-dir', 'out', '--stage', 'candidate',
            '--subject', SUBJECT, '--roster', 'roster.json', '--out', 'out.json'])
        src = post_check.Source()
        src.record = ('--sidecar-result', 'out/a.record.json')
        with mock.patch.object(post_check, 'ledger', return_value=(0, '', '')) as call:
            code, _out, err = post_check.record_panel(ROOT, args, src, [])
        call.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertIn('--ledger-blobs', err)

    def test_a_foldless_review_with_a_scope_records(self):
        args = post_check.build_parser().parse_args(['review', '--sidecar-dir', 'out', '--stage', 'candidate',
            '--subject', SUBJECT, '--roster', 'roster.json', '--scope-json', 'scope.json', '--out', 'out.json'])
        src = post_check.Source()
        src.record = ('--sidecar-result', 'out/a.record.json')
        with mock.patch.object(post_check, 'ledger', return_value=(0, '', '')) as call:
            code, _out, _err = post_check.record_panel(ROOT, args, src, [])
        self.assertEqual(code, 0)
        self.assertIn('--scope-json', call.call_args.args)

    def test_saved_result_does_not_mix_live_and_completed_ledger_sources(self):
        args = post_check.build_parser().parse_args(['review', '--result', 'saved.json', '--seats-file', 'seats.json',
            '--stage', 'candidate', '--subject', SUBJECT, '--roster', 'roster.json', '--scope-json', 'scope.json',
            '--out', 'out.json'])
        src = post_check.Source()
        src.record = ('--result', 'saved.json')
        with mock.patch.object(post_check, 'ledger', return_value=(0, '', '')) as call:
            post_check.record_panel(ROOT, args, src, [])
        self.assertNotIn('--seats-file', call.call_args.args)

    def test_record_panel_forwards_frozen_flags_unchanged(self):
        args = post_check.build_parser().parse_args(['review', '--workflow-dir', 'live', '--seats-file', 'seats.json',
            '--stage', 'candidate', '--subject', SUBJECT, '--roster', 'roster.json',
            '--premise-commits', 'premises.json', '--head', 'frozen-head', '--blobs', 'frozen-blobs.json', '--out', 'out.json'])
        src = post_check.Source()
        src.record = ('--workflow-dir', 'live')
        with mock.patch.object(post_check, 'ledger', return_value=(0, '', '')) as call:
            post_check.record_panel(ROOT, args, src, [])
        argv = call.call_args.args[1:]
        for flag, value in [('--seats-file', 'seats.json'), ('--premise-commits', 'premises.json'),
                            ('--head', 'frozen-head'), ('--blobs', 'frozen-blobs.json')]:
            self.assertEqual(argv[argv.index(flag) + 1], value)


class SnapshotTests(unittest.TestCase):
    def test_full_selection_plan_roster_is_accepted_without_stripping_record_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            seats = [{"key": "seat", "mandates": ["code-quality"], "tier": "architect"}]
            selection = {"lineageFingerprint": "frozen", "policyFingerprint": "policy"}
            path.write_text(json.dumps({"roster": seats, "reviewerSelection": selection}), encoding="utf-8")
            self.assertEqual(post_check.load_roster(str(path)), seats)
            self.assertEqual(json.loads(path.read_text())["reviewerSelection"], selection)
            path.write_text(json.dumps(seats), encoding="utf-8")
            self.assertEqual(post_check.load_roster(str(path)), seats)

    def test_snapshot_writes_blobs_and_a_missing_path_is_null(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        code, _ = run(["snapshot", "--root", str(env.root), "--files", "plan.md", "new_test.py",
                       "--out", str(env.manifest)])
        manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(manifest["kind"], "blobs")
        self.assertIsNone(manifest["files"]["new_test.py"])
        blob = git(env.root, "cat-file", "blob", manifest["files"]["plan.md"])
        self.assertEqual(blob.replace("\r\n", "\n"), PRE)

    def test_snapshot_takes_its_path_lists_from_files_as_well_as_argv(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        listing = env.root / "files.txt"
        listing.write_text("plan.md\n", encoding="utf-8")
        new_listing = env.root / "new.txt"
        new_listing.write_text("new_test.py\n", encoding="utf-8")
        code, _ = run(["snapshot", "--root", str(env.root), "--files-from", str(listing),
                       "--new-paths-from", str(new_listing), "--out", str(env.manifest)])
        from_files = json.loads(env.manifest.read_text(encoding="utf-8"))
        code_argv, _ = run(["snapshot", "--root", str(env.root), "--files", "plan.md", "--new-paths", "new_test.py",
                            "--out", str(env.root / "argv_manifest.json")])
        from_argv = json.loads((env.root / "argv_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((code, code_argv), (0, 0))
        self.assertEqual(from_files, from_argv)

    def test_a_rev_snapshot_names_the_base_commit(self):
        env = Env(self)
        code, _ = run(["snapshot", "--root", str(env.root), "--base", "HEAD", "--out", str(env.manifest)])
        manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
        self.assertEqual((code, manifest["kind"], manifest["base"]),
                         (0, "rev", git(env.root, "rev-parse", "HEAD").strip()))

    def test_usage_is_printed_for_every_subcommand(self):
        for sub in ("review", "snapshot", "claims"):
            code, text = run([sub, "--help"])
            self.assertIn(code, (0, None))
            self.assertIn("usage", text.lower())
            self.assertIn("--out", text)


class BlobSubjectTests(unittest.TestCase):
    def test_blob_subject_fold_passes_and_records_inline(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        code, text = env.review([env.fix_row()])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["changedPaths"], ["plan.md"])
        self.assertEqual(report["ledger"]["inlineRows"], 1)
        inline = [row for row in env.ledger_rows() if str(row.get("reviewer", "")).startswith("inline:fold")]
        self.assertEqual([(row["mandate"], row["outcome"], sorted(row["files"])) for row in inline],
                         [(MANDATE, "clean", ["plan.md"])])
        self.assertEqual(report["counts"]["VERIFIED"], 1)

    def test_parent_receives_all_nonmechanical_ids_without_next_round(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        plan = {"id": "F2", "action": "PLAN", "file": f"{env.plan}:4", "description": "d", "agent": MANDATE,
                "question": "which transport?", "options": ["local (Recommended)", "remote"]}
        ask = {"id": "F3", "action": "ASK", "file": f"{env.plan}:4", "description": "d", "agent": MANDATE,
               "question": "which owner?"}
        taken = dict(plan, id="F4")
        env.digest(applied=[{"id": "F1", "change": "typo", "source": "finding"},
                            {"id": "F4", "change": "chose an option", "source": "finding"}],
                   deferred=[{"id": "F2", "reason": "a design choice"}, {"id": "F3", "reason": "owner input"}])
        code, text = env.review([env.fix_row(), plan, ask, taken])
        report = env.report()
        self.assertNotIn("nextRound", report)
        self.assertEqual({h["id"] for h in report["held"]}, {"F2", "F3", "F4"})
        self.assertEqual([(f["kind"], f["ids"]) for f in report["failures"]], [("fold-not-mechanical", ["F4"])])
        self.assertEqual(report["ledger"]["inlineRows"], 0, "the file holds a change the fold may not make")
        self.assertEqual(code, 0, text)

    def test_the_fold_s_own_findings_and_digest_files_are_not_out_of_subject(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest(changedPaths=["plan.md", "findings.json", "digest.json"])
        code, text = env.review([env.fix_row()])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual(report["failures"], [])

    def test_a_peer_s_test_edit_during_the_fold_is_not_the_fold_s_change(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        peer = env.root / ".claude" / "tests" / "test_peer.py"
        peer.parent.mkdir(parents=True)
        peer.write_text("assert True\n", encoding="utf-8")
        env.digest()
        code, text = env.review([env.fix_row()])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual(report["changedPaths"], ["plan.md"])

    def fold_files_review(self, env, claimed):
        spill = env.root / "spill"
        spill.mkdir()
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest_path = spill / "fold_digest.json"
        env.verdicts = spill / "fold_verdicts.json"
        env.digest(changedPaths=["plan.md", *claimed])
        for name in ("fold_prompt.md", "scratch_notes.md"):
            (spill / name).write_text("planted\n", encoding="utf-8")
        findings = spill / "fold_findings.json"
        findings.write_text(json.dumps({"findings": [env.fix_row()]}), encoding="utf-8")
        return run(["review", "--findings", str(findings), "--digest", str(env.digest_path),
                    "--snapshot", str(env.manifest), "--stage", "plan-check", "--subject", SUBJECT,
                    "--roster", str(env.roster), "--out", str(env.out)])

    def test_fold_own_prompt_file_claimed_is_not_out_of_subject(self):
        env = Env(self)
        code, text = self.fold_files_review(env, ["spill/fold_prompt.md", "spill/fold_verdicts.json",
                                                  "spill/fold_digest.json", "spill/fold_findings.json"])
        kinds = {f["kind"] for f in env.report()["failures"]}
        self.assertNotIn("fold-out-of-subject", kinds, text)

    def test_other_scratch_path_claimed_stays_out_of_subject(self):
        env = Env(self)
        code, text = self.fold_files_review(env, ["spill/scratch_notes.md"])
        failures = [f for f in env.report()["failures"] if f["kind"] == "fold-out-of-subject"]
        self.assertTrue(failures and "spill/scratch_notes.md" in json.dumps(failures), text)

    def test_out_of_subject_failure_preserves_subject_derived_rows(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest(changedPaths=["plan.md", "elsewhere.md"])
        code, text = env.review([env.fix_row()])
        report = env.report()
        self.assertEqual(code, 1, text)
        self.assertIn("fold-out-of-subject", {f["kind"] for f in report["failures"]})
        self.assertEqual(report["ledger"]["inlineRows"], 1)
        rows = [row for row in env.ledger_rows() if row.get("reviewer") == "inline:fold"]
        self.assertEqual(rows[0]["from"], env.ledger_rows()[0]["files"])

    def test_a_fold_that_changed_nothing_fails_as_unchanged_file(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.digest()
        code, _ = env.review([env.fix_row(old=None, new=None)])
        self.assertEqual(code, 0)
        self.assertIn("fold-unchanged-file", {f["kind"] for f in env.report()["failures"]})
        self.assertEqual([(h["id"], h["class"]) for h in env.report()["held"]], [("F1", "fold-not-mechanical")])

    def test_a_missing_snapshot_is_unusable(self):
        env = Env(self)
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        code, text = run(["review", "--findings", str(env.findings([env.fix_row()])), "--digest",
                          str(env.digest_path), "--snapshot", str(env.root / "none.json"), "--out", str(env.out)])
        self.assertEqual(code, 2, text)

    def test_an_unreadable_snapshot_blob_is_reported_as_fold_snapshot_missing(self):
        env = Env(self)
        env.snapshot(pre=PRE)
        manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
        manifest["files"]["plan.md"] = "0" * 40
        env.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        row = env.fix_row()
        del row["evidence"]
        code, text = env.review([row])
        self.assertEqual(code, 1, text)
        self.assertIn("fold-snapshot-missing", {f["kind"] for f in env.report()["failures"]})

    def test_an_unreadable_snapshot_blob_on_a_ledgered_run_still_writes_the_report(self):
        env = Env(self)
        env.snapshot(pre=PRE)
        manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
        manifest["files"]["plan.md"] = "0" * 40
        env.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        row = env.fix_row()
        del row["evidence"]
        result = {"findings": [row], "perAgent": [
            {"key": MANDATE, "status": "completed", "count": 1, "stoppedAt": "exhausted-leads", "tier": "executor",
             "effort": "high", "mandates": {MANDATE: "findings"}, "checkedTargets": ["Read:plan.md"]}]}
        path = env.root / "engine_result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        code, text = run(["review", "--findings", str(path), "--digest", str(env.digest_path),
                          "--snapshot", str(env.manifest), "--stage", "plan-check", "--subject", SUBJECT,
                          "--roster", str(env.roster), "--out", str(env.out)])
        self.assertEqual(code, 1, text)
        self.assertIn("fold-snapshot-missing", {f["kind"] for f in env.report()["failures"]})

    def test_a_bounded_preview_with_shed_findings_is_unusable(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        path = env.root / "bounded.json"
        path.write_text(json.dumps({"findings": [env.fix_row()], "delivery": {"payloadComplete": False}}),
                        encoding="utf-8")
        code, text = run(["review", "--findings", str(path), "--digest", str(env.digest_path),
                          "--snapshot", str(env.manifest), "--out", str(env.out)])
        self.assertEqual(code, 2, text)
        self.assertIn("bounded", text)

    def test_a_deferred_id_is_held_with_its_reason(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        ask = {"id": "F2", "action": "ASK", "file": f"{env.plan}:4", "description": "which?",
               "question": "which transport?", "agent": MANDATE}
        env.digest(deferred=[{"id": "F2", "reason": "needs the owner's transport"}])
        code, text = env.review([env.fix_row(), ask])
        self.assertEqual(code, 0, text)
        self.assertEqual([(h["id"], h["class"], h["reason"]) for h in env.report()["held"]],
                         [("F2", "deferred", "needs the owner's transport")])


class ScopedRecordingTests(unittest.TestCase):
    def test_mechanical_failure_voids_only_its_file_and_holds_its_id(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        other = env.root / "other.md"
        other.write_text(PRE, encoding="utf-8")
        for path in ("plan.md", "other.md"):
            env.plant_panel_row(path=path)
        env.snapshot(files=("plan.md", "other.md"))
        env.plan.write_text(POST, encoding="utf-8")
        other.write_text(PRE.replace("Step 9", "Step nine"), encoding="utf-8")
        row = env.fix_row(id="F2", file=str(other) + ":3")
        env.digest(applied=[{"id": "F1", "change": "a", "source": "finding"}, {"id": "F2", "change": "b", "source": "finding"}],
                   changedPaths=["plan.md", "other.md"])
        code, text = env.review([env.fix_row(), row])
        self.assertEqual(code, 0, text)
        inline = [r for r in env.ledger_rows() if r.get("reviewer") == "inline:fold"]
        self.assertEqual([list(r["files"]) for r in inline], [["plan.md"]])
        self.assertEqual([(h["id"], h["class"]) for h in env.report()["held"]], [("F2", "fold-not-applied")])

    def test_recording_uses_checked_post_bytes_not_a_later_disk_edit(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        checked_blob = git(env.root, "hash-object", "plan.md").strip()
        env.digest()
        real = post_check.ledger
        def ledger(cwd, *args):
            if args[0] == "rows":
                env.plan.write_text(POST + "peer's later line\n", encoding="utf-8")
            return real(cwd, *args)
        with mock.patch.object(post_check, "ledger", side_effect=ledger):
            code, text = env.review([env.fix_row()])
        self.assertEqual(code, 0, text)
        row = [r for r in env.ledger_rows() if r.get("reviewer") == "inline:fold"][0]
        self.assertEqual(row["files"], {"plan.md": checked_blob})

    def test_ledger_record_failure_names_all_subject_files(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        env.digest()
        with mock.patch.object(post_check, "record_panel", return_value=(1, "", "append failed")):
            code, text = env.review([env.fix_row()])
        self.assertEqual(code, 1, text)
        failure = next(f for f in env.report()["failures"] if f["kind"] == "ledger-record-failed")
        self.assertEqual(failure["files"], ["plan.md"])
        self.assertIsInstance(failure["ids"], list)

    def test_fold_less_clean_review_records_its_reviewed_snapshot(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        result = env.root / "result.json"
        result.write_text(json.dumps({"findings": [], "perAgent": [
            {"key": MANDATE, "status": "completed", "count": 0, "mandates": {MANDATE: "clean"}}]}), encoding="utf-8")
        code, text = run(["review", "--findings", str(result), "--reviewed-snapshot", str(env.manifest),
                          "--stage", "plan-check", "--subject", SUBJECT, "--roster", str(env.roster),
                          "--out", str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual([f for f in env.report()["failures"] if f["kind"] == "ledger-record-failed"], [])
        self.assertTrue(any("plan.md" in (r.get("files") or {}) for r in env.ledger_rows()), env.ledger_rows())


class AppliedTests(unittest.TestCase):
    def applied(self, env, rows, *extra):
        return run(["review", "--findings", str(env.findings(rows)), "--applied", "F1", "--files", "plan.md",
                    "--root", str(env.root), "--stage", "plan-check", "--subject", SUBJECT,
                    "--roster", str(env.roster), "--out", str(env.out), *extra])

    def test_unusable_review_reports_the_keys_consumers_read(self):
        env = Env(self)
        code, text = run(["review", "--findings", str(env.root / "absent.json"), "--root", str(env.root),
                          "--stage", "plan-check", "--subject", SUBJECT, "--roster", str(env.roster),
                          "--out", str(env.out)])
        self.assertEqual(code, 2, text)
        self.assertEqual(env.report()["status"], "unavailable")
        self.assertNotIn("nextRound", env.report())

    def test_partial_applier_checks_only_selected_originals(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        env.plant_panel_row()
        env.plan.write_text(POST, encoding='utf-8')
        ask = dict(env.fix_row(), id='F2', action='ASK', question='Which transport?', old=None, new=None)
        code, text = self.applied(env, [env.fix_row(), ask])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.report()['counts']['VERIFIED'], 1)
        self.assertEqual(set(env.report()['verdicts']), {'F1'})
        self.assertEqual(env.report()['held'], [])

    def test_any_applier_records_derived_fix(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        old = git(env.root, "hash-object", "plan.md").strip()
        env.plan.write_text(POST, encoding="utf-8")
        code, text = self.applied(env, [env.fix_row()])
        self.assertEqual(code, 0, text)
        row = env.ledger_rows()[-1]
        self.assertEqual(row["reviewer"], "inline:fold")
        self.assertEqual(row["from"], {"plan.md": old})

    def test_applier_over_a_file_inside_a_submodule_records_the_derived_fix(self):
        env = Env(self)
        lib = env.root.parent / (env.root.name + "_lib")
        self.addCleanup(shutil.rmtree, lib, True)
        subprocess.run(["git", "init", "-q", str(lib)], check=True)
        (lib / "plan.md").write_text(PRE, encoding="utf-8")
        git(lib, "add", "-A")
        git(lib, "commit", "-q", "-m", "plan")
        git(env.root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "lib")
        git(env.root, "commit", "-q", "-m", "link")
        env.plant_panel_row(path="lib/plan.md")
        old = git(env.root, "hash-object", "lib/plan.md").strip()
        (env.root / "lib" / "plan.md").write_text(POST, encoding="utf-8")
        row = env.fix_row(file=f"{env.root}/lib/plan.md:3", evidence=[
            {"file": f"{env.root}/lib/plan.md:3", "quote": "teh default is local"}])
        code, text = run(["review", "--findings", str(env.findings([row])), "--applied", "F1", "--files",
                          "lib/plan.md", "--root", str(env.root), "--stage", "plan-check", "--subject", SUBJECT,
                          "--roster", str(env.roster), "--out", str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.ledger_rows()[-1]["from"], {"lib/plan.md": old})

    def _two_mandate_roster(self, env):
        env.roster.write_text(json.dumps([{"key": "plc-pattern-fit+plc-evidence-grounding",
                                          "mandates": [MANDATE, "plc-evidence-grounding"],
                                          "tier": "executor", "effort": "high"}]), encoding="utf-8")

    def test_applier_without_common_reviewed_blob_lists_mandate_blobs(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.plan.write_text(PRE + "Step 10: extra." + chr(10), encoding="utf-8")
        env.plant_panel_row("plc-evidence-grounding")
        self._two_mandate_roster(env)
        env.plan.write_text(POST, encoding="utf-8")
        code, text = self.applied(env, [env.fix_row()])
        self.assertEqual(code, 2, text)
        self.assertIn("no common covered blob", text)
        self.assertIn(MANDATE, text)
        self.assertIn("plc-evidence-grounding", text)

    def test_applier_drops_a_mandate_that_never_covered_the_file(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        self._two_mandate_roster(env)
        env.plan.write_text(POST, encoding="utf-8")
        code, text = self.applied(env, [env.fix_row()])
        self.assertEqual(code, 0, text)

    def test_applier_refuses_when_no_mandate_covered_the_file(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        self._two_mandate_roster(env)
        env.plan.write_text(POST, encoding="utf-8")
        code, text = self.applied(env, [env.fix_row()])
        self.assertEqual(code, 2, text)
        self.assertIn("no common covered blob", text)

    def test_a_ruled_application_is_not_credited_as_reviewed(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.plan.write_text(POST.replace("the default", "an owner-chosen default"), encoding="utf-8")
        code, text = self.applied(env, [env.fix_row()], "--ruled", "F1")
        self.assertEqual(code, 0, text)
        self.assertNotIn("nextRound", env.report())
        self.assertEqual(env.report()["ledger"]["inlineRows"], 0)
        owed = json.loads(env.ledger_tool("plan", "--stage", "plan-check", "--subject", SUBJECT, "--roster",
                                          str(env.roster), "--files", "plan.md").stdout)["roster"]
        self.assertEqual([seat["mandates"] for seat in owed], [[MANDATE]],
                         "the session's own coverage query still owes the file")

    def test_ruled_without_applied_is_refused(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        code, text = run(["review", "--findings", str(env.findings([env.fix_row()])), "--ruled", "F1",
                          "--root", str(env.root), "--out", str(env.out)])
        self.assertEqual(code, 2, text)
        self.assertIn("--applied", text)


class HeldRowsTests(unittest.TestCase):
    def test_an_unfolded_run_holds_each_row_by_its_action(self):
        rows = [{"id": "A1", "action": "ASK", "question": "which transport?"},
                {"id": "P1", "action": "PLAN", "question": "which owner?"},
                {"id": "P2", "action": "PLAN", "question": None},
                {"id": "P3", "action": "PLAN", "question": "which owner?", "options": ["the shared owner"]},
                {"id": "F1", "action": "FIX"}]
        held = post_check.held_rows(rows, [])
        self.assertEqual([(h["id"], h["class"]) for h in held],
                         [("A1", "ask"), ("P1", "open-plan"), ("P2", "open-plan"), ("P3", "unfolded"), ("F1", "unfolded")])


    def test_a_deferred_entry_is_held_with_its_reason_whatever_legacy_class_it_carries(self):
        job = {"digestValue": {"deferred": [
            {"id": "F1", "reason": "needs a measurement"},
            {"id": "F2", "class": "own-design", "reason": "no reviewer decided"}]}}
        held = post_check.held_rows([], [job])
        self.assertEqual([(h["id"], h["reason"]) for h in held],
                         [("F1", "needs a measurement"), ("F2", "no reviewer decided")])


class UnfoldedRunTests(unittest.TestCase):
    def test_an_unfolded_run_holds_every_finding_it_can_act_on_for_the_standalone_fold(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        rows = [env.fix_row(),
                {"id": "F2", "action": "PLAN", "file": f"{env.plan}:4", "description": "d", "agent": MANDATE,
                 "options": ["a (Recommended)", "b"]},
                {"id": "F3", "action": "ASK", "file": f"{env.plan}:4", "description": "d", "agent": MANDATE,
                 "question": "which transport?"}]
        code, text = run(["review", "--findings", str(env.findings(rows)), "--out", str(env.out)])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual(sorted((h["id"], h["class"]) for h in report["held"]),
                         [("F1", "unfolded"), ("F2", "unfolded"), ("F3", "ask")])


class OutsideCheckoutTests(unittest.TestCase):
    def test_a_folder_outside_any_git_checkout_folds_against_the_project_object_database(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        doc = root / "note.md"
        doc.write_text(PRE, encoding="utf-8")
        objects = root / "objects"
        objects.mkdir()
        subprocess.run(["git", "-C", str(objects), "init", "-q"], check=True)
        manifest = root / "snapshot.json"
        manifest.write_text(json.dumps(fold_check.snapshot_blobs(root, ["note.md"], objects=objects)), encoding="utf-8")
        written = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIsNotNone(written["files"]["note.md"])
        self.assertEqual(written["objects"], str(objects))
        doc.write_text(POST, encoding="utf-8")
        digest = root / "digest.json"
        digest.write_text(json.dumps({
            "applied": [{"id": "F1", "change": "typo", "source": "finding"}], "deferred": [],
            "changedPaths": ["note.md"]}), encoding="utf-8")
        findings = root / "findings.json"
        findings.write_text(json.dumps({"findings": [{
            "id": "F1", "action": "FIX", "file": f"{doc}:3", "description": "typo", "agent": MANDATE,
            "old": "teh default", "new": "the default",
            "evidence": [{"file": f"{doc}:3", "quote": "teh default is local"}]}]}), encoding="utf-8")
        out = root / "post_check.json"
        code, text = run(["review", "--findings", str(findings), "--digest", str(digest), "--snapshot", str(manifest),
                          "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual(report["changedPaths"], ["note.md"])


class WorkflowDirTests(unittest.TestCase):
    def test_dispatch_review_fence_keeps_authored_ids(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        helper = ClaimsTests()
        helper.addCleanup = self.addCleanup
        text = '```findings\n' + json.dumps(env.fix_row(id='parity#1')) + '\n```'
        directory, _ = helper.journal_dir('parity', text)
        code, printed = run(['review', '--workflow-dir', str(directory), '--out', str(env.out)])
        self.assertEqual(code, 0, printed)
        self.assertEqual(set(env.report()['verdicts']), {'parity#1'})

    def test_review_spill_and_live_fixture_modes(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        directory = self.run_dir(env, {})
        events = [dict(type='started', key='k', agentId='a', label='review:one', phase='Review'),
                  dict(type='result', key='k', agentId='a', result='See the spill.')]
        (directory / 'journal.jsonl').write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
        spill = env.root / 'spill'
        spill.mkdir()
        (spill / 'review:one.md').write_text('```findings\n' + json.dumps(self.seat_row(env, id='F1')) + '\n```', encoding='utf-8')
        code, text = run(['review', '--workflow-dir', str(directory), '--spill-dir', str(spill),
                          '--root', str(env.root), '--expect-total', '1', '--out', str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.report()['counts']['VERIFIED'], 1)

    def test_review_count_flags_check_the_count_each_branch_reads(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        directory = self.run_dir(env, {})
        events = []
        for n, seat in enumerate(('one', 'two'), 1):
            events += [dict(type='started', key=f'k{n}', agentId=f'a{n}', label=f'review:{seat}', phase='Review'),
                       dict(type='result', key=f'k{n}', agentId=f'a{n}', result='```findings\n' + json.dumps(
                           self.seat_row(env, id=f'F{n}')) + '\n```')]
        (directory / 'journal.jsonl').write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
        base = ['review', '--workflow-dir', str(directory), '--root', str(env.root), '--out', str(env.out)]
        code, text = run(base + ['--expect-total', '1', '--expect-sources', '2'])
        self.assertEqual(code, 0, text)
        self.assertFalse(env.report()['errors'], env.report()['errors'])
        code, text = run(base + ['--expect-sources', '3'])
        self.assertEqual(code, 1, text)
        self.assertIn('expected 3 source findings, read 2', json.dumps(env.report()['errors']))
        code, text = run(base + ['--expect-total', '1'])
        self.assertEqual(code, 0, 'raw reads ignore --expect-total: a merged count cannot be checked there')

    def test_review_archive_branch_compares_merged_total_and_raw_sources(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        directory = self.run_dir(env, {MANDATE: [self.seat_row(env, id='F1')], 'other': [self.seat_row(env, id='F1')]})
        base = ['review', '--workflow-dir', str(directory), '--root', str(env.root), '--out', str(env.out)]
        code, text = run(base + ['--expect-total', '2', '--expect-sources', '2'])
        self.assertEqual(code, 0, text)
        code, text = run(base + ['--expect-total', '1'])
        self.assertEqual(code, 1, text)
        self.assertIn('expected 1 findings, read 2', json.dumps(env.report()['errors']))
        code, text = run(base + ['--expect-sources', '1'])
        self.assertEqual(code, 1, text)
        self.assertIn('expected 1 source findings, read 2', json.dumps(env.report()['errors']))

    def test_subject_root_applies_to_command_evidence_in_folded_review(self):
        env = Env(self)
        git(env.root, 'add', 'roster.json')
        env.snapshot(pre=PRE)
        env.plan.write_text(POST, encoding='utf-8')
        env.digest()
        row = env.fix_row(evidence=[{'command': 'git ls-files -- roster.json', 'output': 'roster.json'}])
        code, text = run(['review', '--findings', str(env.findings([row])), '--digest', str(env.digest_path),
                          '--snapshot', str(env.manifest), '--out', str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.report()['verdicts']['F1']['verdict'], 'VERIFIED')

    def test_claims_and_review_read_the_live_review_journal_fixture(self):
        env = Env(self)
        fixture = ROOT / '.claude/tests/fixtures/evidence/review'
        directory = env.root / 'session/subagents/workflows/wf_fixture'
        directory.mkdir(parents=True)
        (directory / 'journal.jsonl').write_bytes((fixture / 'journal.jsonl').read_bytes())
        for mode in ('claims', 'review'):
            code, text = run([mode, '--workflow-dir', str(directory), '--root', str(fixture / 'tree'),
                              '--out', str(env.out)])
            self.assertIn(code, (0, 1), text)
            report = env.report()
            self.assertEqual(sum(report['counts'].values()), 16)
            self.assertFalse(report['errors'], report['errors'])
            self.assertEqual((report['mode'], report['source']), (mode, ['--workflow-dir', str(directory.resolve())]),
                             'the Stop audit replays this source through the same reader')

    def test_live_review_expect_total_replaces_missing_host_record(self):
        env = Env(self)
        env.snapshot(pre=PRE)
        env.plan.write_text(POST, encoding='utf-8')
        row = env.fix_row()
        prompt = '\n'.join(['FOLD STAGE of a review. Read t.md.', f'Root: {env.root}',
                            f'Snapshot: {env.manifest}', 'Files: plan.md',
                            f'Findings file: {env.root / "f.json"}', '',
                            'CONSOLIDATED FINDINGS (1 ids: F1):', '```json', json.dumps([row]), '```'])
        directory = self.run_dir(env, {MANDATE: [self.seat_row(env)]}, prompt, env.digest(), total=None)
        for total, expected in ((1, 0), (2, 1), (0, 0)):
            code, text = run(['review', '--workflow-dir', str(directory), '--expect-total', str(total),
                              '--out', str(env.out)])
            self.assertEqual(code, expected, text)
            report = env.report()
            self.assertEqual(report['counts']['VERIFIED'], 1)
            self.assertEqual(report['verdicts']['F1']['verdict'], 'VERIFIED')
            self.assertIn('errors', report)
        journal = directory / 'journal.jsonl'
        with journal.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(type='started', key='verify', agentId='verifier',
                                        label='verify-review', phase='Verify')) + '\n')
        import session_digest
        with mock.patch.object(session_digest, 'projects_dir', return_value=env.root):
            code, text = run(['review', '--run-label', 'verify-review', '--expect-total', '1',
                              '--out', str(env.out)])
        self.assertEqual(code, 0, text)
        self.assertEqual(env.report()['verdicts']['F1']['verdict'], 'VERIFIED')
        self.assertEqual(env.report()['source'], ['--workflow-dir', str(directory.resolve()), '--expect-total', '1'],
                         'the Stop audit replays the source the run label resolved to')

    def test_review_result_and_sidecar_use_root_and_common_report(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        rows = [self.seat_row(env, id='F1'), self.seat_row(env, id='F2', evidence=[
            {'file': 'plan.md:3', 'quote': 'Fabricated sentence.'}])]
        path = env.findings(rows)
        sidecar = env.root / 'sidecar'
        sidecar.mkdir()
        (sidecar / 'review.json').write_text(json.dumps({'findings': rows}), encoding='utf-8')
        for flags, source in ((['--result', str(path)], ['--findings', str(Path(path).resolve())]),
                              (['--sidecar-dir', str(sidecar)], ['--sidecar-dir', str(sidecar.resolve())])):
            code, text = run(['review', *flags, '--root', str(env.root), '--out', str(env.out)])
            # A fabricated quote is the finding's defect, not the run's: the claims status fails, the run's
            # outcome passes, and F2 reaches the session through `unverified`.
            self.assertEqual(code, 0, text)
            self.assertEqual((env.report()['status'], env.report()['outcome']), ('fail', 'pass'))
            self.assertEqual(env.report()['unverified'], ['F2'])
            self.assertIn('1 unverified', text)
            self.assertEqual(env.report()['source'], source)
            self.assertEqual(env.report()['counts'], dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=0))
            self.assertEqual(env.report()['verdicts']['F1']['verdict'], 'VERIFIED')
            for key in ('held', 'failures', 'ledger', 'changedPaths', 'foldReport', 'errors'):
                self.assertIn(key, env.report())

    def test_a_fold_with_no_failed_check_passes_whatever_its_claims_status(self):
        import sidecar_fanout
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name)
        review = {'view': []}
        report = {'status': 'fail', 'outcome': 'pass', 'counts': {'VERIFIED': 1, 'UNVERIFIED': 0, 'UNCHECKED': 3},
                  'held': [{'id': 'F2', 'class': 'owner-input'}], 'foldReport': None}
        fold = {'jobs': [], 'failures': [], 'authors': [], 'results': {}}
        digest = sidecar_fanout.finish_fold(review, fold, report, out)
        self.assertEqual((digest['status'], digest['failures']), ('pass', []))
        failed = dict(report, outcome='fail')
        again = out / 'failed'
        again.mkdir()
        self.assertEqual(sidecar_fanout.finish_fold(review, fold, failed, again)['status'], 'fail')

    def run_dir(self, env, rows_by_seat, fold_prompt=None, digest=None, total=1):
        run = env.root / "sess" / "subagents" / "workflows" / "wf_test"
        run.mkdir(parents=True)
        if fold_prompt is not None and total is not None:
            record = env.root / "sess" / "workflows" / "wf_test.json"
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({"result": json.dumps({"counts": {"total": total}})}), encoding="utf-8")
        journal = []
        for n, (seat, rows) in enumerate(rows_by_seat.items(), 1):
            journal += [{"type": "started", "key": f"k{n}", "agentId": f"a{n}", "label": f"review:{seat}", "phase": "Review"},
                        {"type": "result", "key": f"k{n}", "agentId": f"a{n}", "result": {
                            "findings": rows, "gaps": [],
                            "checked": {"toolsUsed": ["Read:x"], "stoppedAt": "exhausted-leads", "basis": "read"}}}]
        if fold_prompt is not None:
            journal += [{"type": "started", "key": "kf", "agentId": "fold1", "label": "review:fold", "phase": "Fold"},
                        {"type": "result", "key": "kf", "agentId": "fold1", "result": digest}]
            (run / "agent-fold1.jsonl").write_text(json.dumps({"type": "user", "message": {
                "role": "user", "content": fold_prompt}}) + "\n", encoding="utf-8")
        (run / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in journal), encoding="utf-8")
        return run

    def seat_row(self, env, **over):
        row = {"agent": MANDATE, "action": "FIX", "category": "bug", "critical": False, "file": "plan.md:3",
               "description": "typo", "rationale": "typo", "old": "teh default", "new": "the default",
               "evidence": [{"file": "plan.md:3", "quote": "teh default is local"}]}
        row.update(over)
        return row

    def test_an_unfolded_review_gets_quote_verdicts_with_the_engine_ids(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        good = self.seat_row(env, evidence=[{"file": f"{env.plan}:3", "quote": "teh default is local"}])
        bad = self.seat_row(env, description="two", evidence=[
            {"file": f"{env.plan}:4", "quote": "a sentence the plan never held"}])
        workdir = self.run_dir(env, {MANDATE: [good, bad]})
        code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)])
        report = env.report()
        self.assertEqual((code, report["status"]), (0, "fail"), text + json.dumps(report))
        self.assertEqual((report["counts"]["VERIFIED"], report["counts"]["UNVERIFIED"]), (1, 1))
        self.assertEqual(report["unverified"], ["F2"])
        self.assertIsNone(report["foldReport"])

    def test_a_folded_workflow_review_passes_from_the_fold_prompt_and_journal_digest(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        row = env.fix_row()
        prompt = "\n".join(["FOLD STAGE of a review. Read t.md.", f"Root: {env.root}", f"Snapshot: {env.manifest}",
                            "Files: plan.md", f"Findings file: {env.root / 'f.json'}", "",
                            "CONSOLIDATED FINDINGS (1 ids: F1):", "```json", json.dumps([row]), "```", ""])
        workdir = self.run_dir(env, {MANDATE: [self.seat_row(env)]}, prompt, env.digest())
        code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report["failures"]))
        self.assertEqual(report["changedPaths"], ["plan.md"])

    def test_a_consolidated_finding_that_reached_no_fold_job_fails_fold_routing(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        row = env.fix_row()
        prompt = "\n".join(["FOLD STAGE of a review. Read t.md.", f"Root: {env.root}", f"Snapshot: {env.manifest}",
                            "Files: plan.md", f"Findings file: {env.root / 'f.json'}", "",
                            "CONSOLIDATED FINDINGS (1 ids: F1):", "```json", json.dumps([row]), "```", ""])
        workdir = self.run_dir(env, {MANDATE: [self.seat_row(env)]}, prompt, env.digest())
        record = workdir.parent.parent.parent / "workflows" / (workdir.name + ".json")
        record.parent.mkdir(parents=True, exist_ok=True)
        for total, failing in ((1, False), (2, True)):
            with self.subTest(total=total):
                record.write_text(json.dumps({"result": json.dumps({"counts": {"total": total}})}), encoding="utf-8")
                code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)])
                kinds = {f["kind"] for f in env.report()["failures"]}
                self.assertEqual(("fold-routing" in kinds, code), (failing, 1 if failing else 0), text)
                self.assertEqual(env.report()["outcome"], "fail" if failing else "pass")

    def test_a_named_finding_that_reached_no_fold_job_is_held_and_the_outcome_passes(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        prompt = "\n".join(["FOLD STAGE of a review. Read t.md.", f"Root: {env.root}", f"Snapshot: {env.manifest}",
                            "Files: plan.md", f"Findings file: {env.root / 'f.json'}", "",
                            "CONSOLIDATED FINDINGS (1 ids: F1):", "```json", json.dumps([env.fix_row()]), "```", ""])
        lost = self.seat_row(env, description="second defect", old="local", new="remote",
                             evidence=[{"file": "plan.md:3", "quote": "is local"}])
        workdir = self.run_dir(env, {MANDATE: [self.seat_row(env), lost]}, prompt, env.digest(), total=2)
        code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)])
        report = env.report()
        self.assertEqual((code, report["outcome"]), (0, "pass"), text + json.dumps(report["failures"]))
        self.assertIn({"id": "F2", "class": "unrouted"}, [{k: h[k] for k in ("id", "class")} for h in report["held"]])

    def test_an_engine_count_from_another_consolidation_fails_nothing_when_every_original_is_routed_or_held(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        prompt = "\n".join(["FOLD STAGE of a review. Read t.md.", f"Root: {env.root}", f"Snapshot: {env.manifest}",
                            "Files: plan.md", f"Findings file: {env.root / 'f.json'}", "",
                            "CONSOLIDATED FINDINGS (1 ids: F1):", "```json", json.dumps([env.fix_row()]), "```", ""])
        lost = [self.seat_row(env, description=f"lost {n}", old="local", new="remote",
                              evidence=[{"file": "plan.md:3", "quote": "is local"}]) for n in (2, 3)]
        for total in (2, None):
            with self.subTest(total=total):
                shutil.rmtree(env.root / "sess", ignore_errors=True)
                workdir = self.run_dir(env, {MANDATE: [self.seat_row(env), *lost]}, prompt, env.digest(), total=total)
                code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)])
                report = env.report()
                self.assertEqual((code, report["outcome"]), (0, "pass"), text + json.dumps(report["failures"]))
                held = {h["id"]: h["class"] for h in report["held"]}
                self.assertEqual((held.get("F2"), held.get("F3")), ("unrouted", "unrouted"))

    def test_a_two_seat_fold_numbers_sources_by_the_launched_seats_or_fails_as_order_unknown(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        prompt = "\n".join(["FOLD STAGE of a review. Read t.md.", f"Root: {env.root}", f"Snapshot: {env.manifest}",
                            "Files: plan.md", f"Findings file: {env.root / 'f.json'}", "",
                            "CONSOLIDATED FINDINGS (1 ids: F1):", "```json", json.dumps([env.fix_row()]), "```", ""])
        workdir = self.run_dir(env, {"first": [self.seat_row(env, agent="first")],
                                     "second": [self.seat_row(env, agent="second")]}, prompt, env.digest())
        seats = env.root / "post_check.seats.json"
        seats.write_text(json.dumps({"review:second": {}, "review:first": {}}), encoding="utf-8")
        for extra, unknown in (([], True), (["--seats-file", str(seats)], False)):
            with self.subTest(seats=bool(extra)):
                code, text = run(["review", "--workflow-dir", str(workdir), "--out", str(env.out)] + extra)
                kinds = {f["kind"] for f in env.report()["failures"]}
                self.assertEqual("fold-id-order-unknown" in kinds, unknown, text)


GROUNDING = "plc-evidence-grounding"
TWO_SEATS = [{"key": MANDATE, "mandates": [MANDATE], "tier": "architect", "effort": "high"},
             {"key": GROUNDING, "mandates": [GROUNDING], "tier": "fanout", "effort": "medium"}]
A_PRE = "# A\n\nStep 3: teh default is local.\nStep 9: pick a transport.\n"
A_POST = "# A\n\nStep 3: the default is local.\nStep 9: pick a transport.\n"
B_PRE = "# B\n\nStep 3: the cache is local.\nStep 9: pick a transport.\n"
B_POST = "# B\n\nStep 3: the cache is local.\nStep 9: pick a transport per job.\n"


class NextRoundTests(unittest.TestCase):
    """Two files: a.md takes a verbatim fix, b.md a fold-written change."""

    def setUp(self):
        self.env = env = Env(self)
        env.roster.write_text(json.dumps(TWO_SEATS), encoding="utf-8")
        self.a, self.b = env.root / "a.md", env.root / "b.md"
        self.a.write_text(A_PRE, encoding="utf-8")
        self.b.write_text(B_PRE, encoding="utf-8")
        for mandate in (MANDATE, GROUNDING):
            env.plant_panel_row(mandate, "a.md")
            env.plant_panel_row(mandate, "b.md")
        env.snapshot(("a.md", "b.md"))
        self.a.write_text(A_POST, encoding="utf-8")
        self.b.write_text(B_POST, encoding="utf-8")

    def rows(self, agent_b=f"{MANDATE}+{GROUNDING}"):
        a = {"id": "F1", "action": "FIX", "file": f"{self.a}:3", "description": "typo", "agent": MANDATE,
             "old": "teh default", "new": "the default",
             "evidence": [{"file": str(self.a), "quote": "teh default is local"}]}
        b = {"id": "F2", "action": "FIX", "file": f"{self.b}:4", "description": "transport", "agent": agent_b,
             "old": "pick a transport.", "new": "pick a transport per job.",
             "evidence": [{"file": str(self.b), "quote": "pick a transport."}]}
        return [a, b]

    def digest(self, **over):
        value = {"applied": [{"id": "F1", "change": "a", "source": "finding"},
                             {"id": "F2", "change": "b", "source": "fold"}], "deferred": [],
                 "changedPaths": ["a.md", "b.md"]}
        value.update(over)
        self.env.digest_path.write_text(json.dumps(value), encoding="utf-8")

    def review(self, rows, stage="plan-check", extra=()):
        findings = self.env.findings(rows)
        return run(["review", "--findings", str(findings), "--digest", str(self.env.digest_path),
                    "--snapshot", str(self.env.manifest), "--stage", stage, "--subject", SUBJECT,
                    "--roster", str(self.env.roster), *extra, "--out", str(self.env.out)])

    def test_a_mechanical_fold_credits_every_covered_mandate_and_makes_no_next_round(self):
        self.digest(applied=[{"id": "F1", "change": "a", "source": "finding"},
                             {"id": "F2", "change": "b", "source": "finding"}])
        code, text = self.review(self.rows())
        report = self.env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertNotIn("nextRound", report)
        inline = [row for row in self.env.ledger_rows() if str(row.get("reviewer", "")).startswith("inline:fold")]
        self.assertEqual(sorted((row["mandate"], sorted(row["files"])) for row in inline),
                         [(GROUNDING, ["a.md"]), (GROUNDING, ["b.md"]), (MANDATE, ["a.md"]), (MANDATE, ["b.md"])])
        owed = self.env.ledger_tool("plan", "--stage", "plan-check", "--subject", SUBJECT, "--roster",
                                    str(self.env.roster), "--files", "a.md", "b.md")
        self.assertEqual(json.loads(owed.stdout)["roster"], [])

    def test_a_reworded_fold_edit_is_held_and_its_file_stays_owed(self):
        self.b.write_text(B_POST.replace("per job", "for every job"), encoding="utf-8")
        self.digest(applied=[{"id": "F1", "change": "a", "source": "finding"},
                             {"id": "F2", "change": "b", "source": "adapted"}])
        code, text = self.review(self.rows())
        report = self.env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        self.assertEqual([(h["id"], h["class"]) for h in report["held"]], [("F2", "fold-not-applied")])
        credited = {rel for row in self.env.ledger_rows() if str(row.get("reviewer", "")).startswith("inline:fold")
                    for rel in row["files"]}
        self.assertEqual(credited, {"a.md"})
        owed = self.env.ledger_tool("plan", "--stage", "plan-check", "--subject", SUBJECT, "--roster",
                                    str(self.env.roster), "--files", "a.md", "b.md")
        self.assertEqual(sorted(m for seat in json.loads(owed.stdout)["roster"] for m in seat["mandates"]),
                         [GROUNDING, MANDATE])

    def test_the_panel_rows_hash_the_snapshot_bytes_so_the_later_round_merges(self):
        env = self.env
        self.digest()
        subject = "doc:unplanted"
        result = {"findings": self.rows(), "perAgent": [
            {"key": seat["key"], "status": "completed", "count": 1, "stoppedAt": "exhausted-leads",
             "tier": seat["tier"], "effort": seat["effort"], "mandates": {seat["key"]: "findings"},
             "checkedTargets": ["Read:a.md", "Read:b.md"]} for seat in TWO_SEATS]}
        path = env.root / "engine_result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        code, text = run(["review", "--findings", str(path), "--digest", str(env.digest_path),
                          "--snapshot", str(env.manifest), "--stage", "plan-check", "--subject", subject,
                          "--roster", str(env.roster), "--out", str(env.out)])
        report = env.report()
        self.assertEqual(code, 0, text + json.dumps(report))
        manifest = json.loads(env.manifest.read_text(encoding="utf-8"))
        reviewed = {rel: git(env.root, "hash-object", "--path", rel, "--stdin", input_text=git(
            env.root, "cat-file", "blob", sha)).strip() for rel, sha in manifest["files"].items()}
        rows = json.loads(env.ledger_tool("rows", "--subject", subject, "--json").stdout or "[]")
        panel = [row for row in rows if row.get("reviewer") == "panel"]
        self.assertEqual(sorted(row["mandate"] for row in panel), sorted([MANDATE, GROUNDING]))
        for row in panel:
            self.assertEqual(row["files"], reviewed, "a panel row hashes the bytes the panel read, not the folded disk")
        self.assertEqual(report["ledger"]["panelRows"], 2)



class ClaimsTests(unittest.TestCase):
    def claim(self, **over):
        row = {"subject": "post_check", "polarity": "exists", "claim": "it exists", "bearing": "context",
               "file": ".claude/tools/post_check.py:1", "evidence": "Close a findings dispatch with one command",
               "confidence": "verified"}
        row.update(over)
        return row

    def explore_dir(self, claims):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "sess" / "subagents" / "workflows" / "wf_explore"
        run_dir.mkdir(parents=True)
        rows = [{"type": "started", "key": "k1", "agentId": "a1", "label": "explore:one", "phase": "Explore"},
                {"type": "result", "key": "k1", "agentId": "a1", "result": {
                    "claims": claims, "gaps": [],
                    "checked": {"toolsUsed": ["Read:x"], "stoppedAt": "exhausted-leads", "basis": "read"}}}]
        (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return run_dir, Path(tmp.name) / "claims.json"

    def test_claims_flags_unverified_quote(self):
        run_dir, out = self.explore_dir([
            self.claim(),
            self.claim(claim="fabricated", evidence="a sentence the file never held"),
            self.claim(claim="absent claim", polarity="absent", evidence=None, verification="git grep returned none"),
            self.claim(claim="no file", file=None)])
        code, text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 1, text)
        self.assertEqual(report["counts"], dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=2))
        self.assertEqual(report["verdicts"]["one#1"]["verdict"], "UNVERIFIED")
        self.assertEqual([report["verdicts"][key]["verdict"] for key in ("one#2", "one#3")], ["UNCHECKED", "UNCHECKED"])

    def test_claims_passes_when_every_quote_resolves(self):
        run_dir, out = self.explore_dir([self.claim()])
        code, _text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((code, report["status"], report["counts"]["VERIFIED"]), (0, "pass", 1))

    def test_claims_on_a_missing_journal_is_unusable(self):
        code, text = run(["claims", "--workflow-dir", str(ROOT / ".claude" / "scratch" / "no_such_run"), "--out",
                          str(Path(self.explore_dir([])[1]).with_name("missing_claims_out.json"))])
        self.assertEqual(code, 2, text)


    def test_claims_reads_doc_audit_findings_that_carry_file_and_evidence(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "sess" / "subagents" / "workflows" / "wf_audit"
        run_dir.mkdir(parents=True)
        finding = {"agent": "da-structural", "check": "S2", "severity": "warning", "action": "FIX", "system": "X",
                   "description": "d", "recommendation": "r", "handler": None, "options": None, "scope": []}
        results = {"da-structural": [dict(finding, file=".claude/tools/post_check.py:1",
                                          evidence="Close a findings dispatch with one command"),
                                     dict(finding, file=".claude/tools/post_check.py:2", evidence="never written there"),
                                     dict(finding, file=None, evidence=None)],
                   "da-domain": []}
        rows = []
        for n, (label, found) in enumerate(results.items(), 1):
            rows += [{"type": "started", "key": f"k{n}", "agentId": f"a{n}", "label": label, "phase": "Audit"},
                     {"type": "result", "key": f"k{n}", "agentId": f"a{n}", "result": {"findings": found}}]
        (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        out = Path(tmp.name) / "claims.json"
        code, text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 1, text)
        self.assertEqual(report["counts"], dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=1))
        self.assertEqual(report["verdicts"]["da-structural#3"]["verdict"], "UNCHECKED")

    def journal_dir(self, label, result):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "sess" / "subagents" / "workflows" / "wf_run"
        run_dir.mkdir(parents=True)
        rows = [{"type": "started", "key": "k1", "agentId": "a1", "label": label, "phase": "Run"},
                {"type": "result", "key": "k1", "agentId": "a1", "result": result}]
        (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return run_dir, Path(tmp.name) / "claims.json"

    def test_an_explore_run_whose_lens_returned_no_object_fails_instead_of_passing_unchecked(self):
        run_dir, out = self.journal_dir("explore:one", "the lens answered in prose")
        code, text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((code, report["status"]), (1, "fail"), text)
        self.assertTrue(report["errors"])

    def test_a_doc_audit_lens_with_a_string_result_is_malformed_not_a_crash(self):
        run_dir, out = self.journal_dir("da-structural", "the lens answered in prose")
        code, text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((code, report["status"]), (1, "fail"), text)
        self.assertTrue(report["errors"])

    CLAIMS_TEXT = ("Report prose.\n\n```claims\n"
                   + json.dumps({"file": ".claude/tools/post_check.py:1",
                                 "evidence": "Close a findings dispatch with one command"}) + "\n"
                   + json.dumps({"id": "K7", "file": ".claude/tools/post_check.py:2", "evidence": "never written there"})
                   + "\n" + json.dumps({"file": None, "evidence": None}) + "\nnot json\n```\n")

    def test_claims_reads_the_fenced_claims_block_of_a_dispatch_job(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "sess" / "subagents" / "workflows" / "wf_dispatch"
        run_dir.mkdir(parents=True)
        rows = [{"type": "started", "key": "k1", "agentId": "a1", "label": "lens-a", "phase": "Dispatch"},
                {"type": "result", "key": "k1", "agentId": "a1", "result": self.CLAIMS_TEXT},
                {"type": "started", "key": "k2", "agentId": "a2", "label": "conflicts", "phase": "Dispatch"},
                {"type": "result", "key": "k2", "agentId": "a2", "result": "merge order: 12, 14"}]
        (run_dir / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        out = Path(tmp.name) / "claims.json"
        code, text = run(["claims", "--workflow-dir", str(run_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 1, text)
        self.assertEqual(report["counts"], dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=1))
        self.assertEqual(report["verdicts"]["K7"]["verdict"], "UNVERIFIED")
        self.assertEqual(len(report["errors"]), 2, report["errors"])
        self.assertIn("lens-a#4", report["errors"][0])
        self.assertIn('conflicts: no claims or findings block', report['errors'][1])

    def test_claims_reads_a_sidecar_out_dir_deliverable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out_dir = Path(tmp.name)
        (out_dir / "lens-a.out.json").write_text("{}", encoding="utf-8")
        (out_dir / "lens-a.md").write_text(self.CLAIMS_TEXT, encoding="utf-8")
        out = out_dir / "claims.json"
        code, text = run(["claims", "--sidecar-dir", str(out_dir), "--out", str(out)])
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(code, 1, text)
        self.assertEqual(report["counts"], dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=1))


class RevStandaloneTests(unittest.TestCase):
    """The standalone fold over a worktree: `post_check.py review --findings --digest --snapshot` with a rev manifest."""

    def setUp(self):
        self.env = env = Env(self)
        env.roster.unlink()
        (env.root / "doc.md").write_text("Step 1: teh default\n", encoding="utf-8", newline="\n")
        (env.root / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8", newline="\n")
        git(env.root, "add", "doc.md", "src.py")
        git(env.root, "commit", "-q", "-m", "base")
        self.base = git(env.root, "rev-parse", "HEAD").strip()
        self.art = env.root.parent / (env.root.name + "_art")
        self.art.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.art, ignore_errors=True))
        self.manifest = self.art / "snap.json"

    def snapshot(self, *extra):
        return run(["snapshot", "--root", str(self.env.root), "--base", self.base, *extra, "--out", str(self.manifest)])

    def review(self, digest, row):
        findings = self.art / "findings.json"
        findings.write_text(json.dumps({"findings": [row]}), encoding="utf-8")
        (self.art / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
        return run(["review", "--findings", str(findings), "--digest", str(self.art / "digest.json"),
                    "--snapshot", str(self.manifest), "--out", str(self.art / "report.json")])

    def report(self):
        return json.loads((self.art / "report.json").read_text(encoding="utf-8"))

    def commit(self, rel, text):
        root = self.env.root
        (root / rel).write_text(text, encoding="utf-8", newline="\n")
        git(root, "add", rel)
        git(root, "commit", "-q", "-m", "fold")

    def doc_row(self):
        return {"id": "F1", "action": "FIX", "file": "doc.md:1", "description": "typo", "old": "teh default",
                "new": "the default", "evidence": [{"file": "doc.md", "quote": "teh default"}]}

    def digest(self, *paths):
        return {"applied": [{"id": "F1", "change": "v", "source": "finding"}], "deferred": [],
                "changedPaths": list(paths)}

    def test_a_rev_snapshot_carries_the_declared_files_and_new_paths(self):
        code, _ = self.snapshot("--files", "doc.md", "--new-paths", "Tests/test_src.py")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual((code, manifest["files"], manifest["newPaths"]), (0, ["doc.md"], ["Tests/test_src.py"]))

    def test_a_committed_text_fix_passes_standalone(self):
        self.snapshot("--files", "doc.md")
        self.commit("doc.md", "Step 1: the default\n")
        code, text = self.review(self.digest("doc.md"), self.doc_row())
        self.assertEqual(code, 0, text + json.dumps(self.report()["failures"]))
        self.assertEqual(self.report()["changedPaths"], ["doc.md"])

    def test_a_fold_that_edits_code_is_held_for_the_parent(self):
        self.snapshot("--files", "src.py")
        self.commit("src.py", "def f():\n    return 2\n")
        row = {"id": "F1", "action": "FIX", "file": "src.py:2", "description": "wrong", "old": "return 1",
               "new": "return 2", "evidence": [{"file": "src.py", "quote": "return 1"}]}
        code, text = self.review(self.digest("src.py"), row)
        report = self.report()
        self.assertEqual((code, report["outcome"]), (1, "fail"), "a fold's edit of a code file fails the subject")
        self.assertEqual([(h["id"], h["class"]) for h in report["held"]], [("F1", "fold-not-mechanical")])
        self.assertIn("fold-out-of-subject", {f["kind"] for f in report["failures"]})

    def test_a_rev_manifest_naming_no_files_is_unusable(self):
        self.snapshot()
        self.commit("doc.md", "Step 1: the default\n")
        code, text = self.review(self.digest("doc.md"), self.doc_row())
        self.assertEqual(code, 2, text)
        self.assertIn("files", text)


class FoldSlipTests(unittest.TestCase):
    """A fold slip holds only the finding it touches; only a subject- or run-wide failure fails the outcome."""

    def slim(self, env, applied, deferred=(), changed=("plan.md",)):
        value = {"applied": [dict(id=i, change="edit", source="finding") for i in applied],
                 "deferred": [dict(id=i, reason="owner") for i in deferred], "changedPaths": list(changed)}
        env.digest_path.write_text(json.dumps(value), encoding="utf-8")

    def two_files(self, env):
        env.plan.write_text(PRE, encoding="utf-8")
        other = env.root / "other.md"
        other.write_text(PRE, encoding="utf-8")
        for path in ("plan.md", "other.md"):
            env.plant_panel_row(path=path)
        env.snapshot(files=("plan.md", "other.md"))
        env.plan.write_text(POST, encoding="utf-8")
        other.write_text(PRE.replace("Step 9", "Step nine"), encoding="utf-8")
        return [env.fix_row(), env.fix_row(id="F2", file=str(other) + ":3")]

    def test_an_id_scoped_failure_holds_only_its_id_and_the_outcome_stays_pass(self):
        env = Env(self)
        rows = self.two_files(env)
        self.slim(env, ["F1", "F2"], changed=("plan.md", "other.md"))
        code, text = env.review(rows)
        report = env.report()
        self.assertEqual((code, report["outcome"]), (0, "pass"), text)
        self.assertEqual([(h["id"], h["class"]) for h in report["held"]], [("F2", "fold-not-applied")])

    def test_an_absent_routed_id_is_held_unreported_and_the_outcome_stays_pass(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        self.slim(env, ["F1"])
        ask = {"id": "F2", "action": "FIX", "file": f"{env.plan}:4", "description": "pick", "agent": MANDATE,
               "old": "pick a transport", "new": "pick the transport"}
        code, text = env.review([env.fix_row(), ask])
        report = env.report()
        self.assertEqual((code, report["outcome"]), (0, "pass"), text)
        self.assertEqual([(h["id"], h["class"]) for h in report["held"]], [("F2", "unreported")])

    def test_an_out_of_subject_change_fails_the_subject(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding="utf-8")
        env.plant_panel_row()
        env.snapshot()
        env.plan.write_text(POST, encoding="utf-8")
        self.slim(env, ["F1"], changed=("plan.md", "elsewhere.md"))
        code, text = env.review([env.fix_row()])
        report = env.report()
        self.assertEqual((code, report["outcome"]), (1, "fail"), text)
        self.assertEqual([f["kind"] for f in report["failures"]], ["fold-out-of-subject"])
        self.assertEqual(report["held"], [])

    def test_held_rows_reads_a_ruling_from_the_applied_entry(self):
        row = {"id": "F2", "action": "ASK", "question": "which?"}
        job = {"digestValue": {"applied": [{"id": "F2", "change": "x", "source": "ruling"}], "deferred": []},
               "ruled": ["F2"]}
        self.assertEqual(post_check.held_rows([row], [job]), [])


class VerdictVocabularyTests(unittest.TestCase):
    def test_digest_unchecked_status_fails(self):
        import session_digest
        helper = ClaimsTests()
        helper.addCleanup = self.addCleanup
        directory, _ = helper.explore_dir([helper.claim(evidence=[])])
        archive = session_digest.build_workflow_result_archive(directory, 'explore')
        self.assertEqual(archive['verification']['status'], 'fail')

    def test_an_unchecked_claim_fails_the_claims_status_in_both_modes(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        rows = [env.fix_row(), env.fix_row(id='F2', evidence=[])]
        for mode in ('claims', 'review'):
            code, text = run([mode, '--result', str(env.findings(rows)), '--out', str(env.out)])
            # claims mode answers whether every claim verified; review mode's exit follows its run outcome
            self.assertEqual((code, env.report()['status']), (1 if mode == 'claims' else 0, 'fail'), text)
            self.assertEqual(env.report()['counts'], dict(VERIFIED=1, UNVERIFIED=0, UNCHECKED=1))

    def test_a_held_unchecked_ask_fails_the_claims_status_not_the_outcome(self):
        env = Env(self)
        env.snapshot(pre=PRE)
        env.plan.write_text(POST, encoding='utf-8')
        env.digest(deferred=[{'id': 'F2', 'class': 'owner-input', 'reason': 'owner-input'}])
        code, text = env.review([env.fix_row(), env.fix_row(id='F2', evidence=[], action='ASK')])
        self.assertEqual((code, env.report()['status'], env.report()['outcome']), (0, 'fail', 'pass'), text)
        self.assertEqual(env.report()['counts'], dict(VERIFIED=1, UNVERIFIED=0, UNCHECKED=1))

    def test_file_fingerprint_is_the_checked_blob(self):
        env = Env(self)
        env.plan.write_text(PRE, encoding='utf-8')
        code, text = run(['claims', '--result', str(env.findings([env.fix_row()])), '--out', str(env.out)])
        self.assertEqual(code, 0, text)
        item = env.report()['verdicts']['F1']['items'][0]
        expected = git(env.root, 'hash-object', '--no-filters', 'plan.md').strip()
        self.assertEqual(item.get('blobHash'), expected)
        self.assertEqual(item.get('path'), str(env.plan.resolve()))
        code, text = run(['claims', '--result', str(env.findings([env.fix_row(evidence=[
            {'file': str(env.plan), 'quote': ''}])])), '--out', str(env.out)])
        self.assertEqual(code, 1, text)
        self.assertEqual(env.report()['verdicts']['F1']['items'][0].get('blobHash'), expected)

    def test_counts_name_unchecked_and_never_crash_on_it(self):
        env = Env(self)
        path = env.root / 'claims.json'
        path.write_text(json.dumps({'claims': [{'id': ident, 'evidence': []} for ident in ('a', 'b')]}), encoding='utf-8')
        code, text = run(['claims', '--result', str(path), '--out', str(env.out)])
        self.assertEqual(code, 1, text)
        self.assertEqual(env.report()['counts'], {'VERIFIED': 0, 'UNVERIFIED': 0, 'UNCHECKED': 2})


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _case_pool import run_sharded
    sys.exit(run_sharded(__file__))
