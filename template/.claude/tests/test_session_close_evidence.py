"""Session-close evidence contracts; synthetic transcripts and receipts only."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('close_check', os.environ.get(
    'SESSION_CLOSE_MODULE', ROOT / '.claude/tools/session_end_check.py'))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def archive_entry(session_id, entry_id=1):
    return {
        'session_id': session_id,
        'id': entry_id,
        'title': 'fixture',
        'date': '2026-09-12',
        'outcome': 'clean',
        'pattern': None,
        'domains': ['meta'],
        'corrections': [],
    }


class CloseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.transcript = self.root / 'own-session.jsonl'

    def write_rows(self, rows):
        self.transcript.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')

    def test_reading_every_command_does_not_complete_every_phase(self):
        self.write_rows([{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': stem, 'name': 'Read', 'input': {
                'file_path': '.claude/commands/' + stem + '.md'}}]}}
            for _, stem, _ in check.PHASES])
        with contextlib.redirect_stdout(io.StringIO()):
            missing, _ = check.render(str(self.transcript), 'own-session', str(self.root / 'archive.json'), False)
        self.assertTrue(missing, 'Opening all command files is not a completion receipt')

    def test_user_embedded_tool_blocks_are_not_actual_calls(self):
        self.write_rows([{'type': 'user', 'message': {'content': [{
            'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '.claude/commands/worklog.md'}}]}}])
        self.assertNotIn('worklog', check.stems_seen(str(self.transcript)))

    def test_missing_project_never_falls_back_to_a_foreign_project(self):
        home = self.root / 'home'
        foreign = home / '.claude/projects/foreign'
        foreign.mkdir(parents=True)
        (foreign / 'own-session.jsonl').write_text('{}')
        with mock.patch.object(check.os.path, 'expanduser', return_value=str(home)):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(check.transcript_for('own-session', str(self.root / 'repo')))

    def test_ambiguous_prefix_never_chooses_the_newest(self):
        home = self.root / 'home'
        repo = str(self.root / 'repo')
        folder = home / '.claude/projects' / check.project_key(repo)
        folder.mkdir(parents=True)
        for sid in ('same-one', 'same-two'):
            (folder / (sid + '.jsonl')).write_text('{}')
        with mock.patch.object(check.os.path, 'expanduser', return_value=str(home)):
            self.assertIsNone(check.transcript_for('same', repo))

    def test_skill_is_seen_as_invocation_but_not_completion(self):
        self.write_rows([{'type': 'assistant', 'message': {'content': [{
            'type': 'tool_use', 'id': 'skill-1', 'name': 'Skill', 'input': {'skill': 'session_audit'}}]}}])
        observations = check.phase_observations(str(self.transcript))
        self.assertTrue(observations['session_audit']['invoked'])
        self.assertFalse(observations['session_audit']['completed'])

    def test_receipt_requires_unchanged_inputs_and_evidence(self):
        source = self.root / 'input.py'
        source.write_text('before')
        evidence = self.root / 'audit.json'
        evidence.write_text('{"findings":[]}')
        receipt = check.make_receipt('own-session', 'session_audit', 'completed',
                                     [str(source)], [str(evidence)], 'Audit consumed; no findings')
        self.assertEqual('completed', check.receipt_status(receipt, 'own-session'))
        source.write_text('after')
        self.assertEqual('stale', check.receipt_status(receipt, 'own-session'))

    def test_peer_receipt_is_not_our_completion(self):
        source = self.root / 'input.py'
        source.write_text('source')
        evidence = self.root / 'audit.json'
        evidence.write_text('{}')
        receipt = check.make_receipt('peer', 'session_audit', 'completed',
                                     [str(source)], [str(evidence)], 'Audit consumed')
        self.assertEqual('unknown', check.receipt_status(receipt, 'own-session'))

    def test_completed_receipt_needs_inputs_and_evidence(self):
        with self.assertRaises(ValueError):
            check.make_receipt('own-session', 'session_audit', 'completed', [], [], 'claimed')

    def test_skipped_receipt_needs_a_reason(self):
        with self.assertRaises(ValueError):
            check.make_receipt('own-session', 'sync_subsystems', 'skipped', [], [], '')


    def test_evidence_mutation_stales_receipt(self):
        source, evidence = self.root / 'input.py', self.root / 'audit.json'
        source.write_text('source')
        evidence.write_text('{}')
        receipt = check.make_receipt('own-session', 'session_audit', 'completed',
                                     [str(source)], [str(evidence)], 'Audit consumed')
        evidence.write_text('{"changed": true}')
        self.assertEqual('stale', check.receipt_status(receipt, 'own-session'))

    def test_dependency_change_invalidates_downstream(self):
        source, evidence = self.root / 'input.py', self.root / 'audit.json'
        source.write_text('source')
        evidence.write_text('{}')
        upstream = check.make_receipt('own-session', 'session_digest', 'completed',
                                      [str(source)], [str(evidence)], 'Full digest consumed')
        downstream = check.make_receipt('own-session', 'session_audit', 'completed',
                                        [str(evidence)], [str(evidence)], 'Audit consumed',
                                        dependencies={'session_digest': upstream})
        source.write_text('changed')
        self.assertEqual('stale', check.receipt_status(downstream, 'own-session',
                                                     {'session_digest': upstream}))

    def test_malformed_receipt_fields_return_unknown(self):
        source, evidence = self.root / 'source.py', self.root / 'audit.json'
        source.write_text('source')
        evidence.write_text('{}')
        good = check.make_receipt('own-session', 'session_audit', 'completed',
                                 [str(source)], [str(evidence)], 'Audit checked')
        for field, value in (('phase', []), ('phase', {}), ('status', []),
                             ('version', True), ('dependencies', [])):
            with self.subTest(field=field, value=value):
                self.assertEqual('unknown', check.receipt_status(dict(good, **{field: value}), 'own-session'))

    def test_dependency_cannot_use_another_phase_receipt(self):
        source, evidence = self.root / 'source.py', self.root / 'audit.json'
        source.write_text('source')
        evidence.write_text('{}')
        upstream = check.make_receipt('own-session', 'session_digest', 'completed',
                                     [str(source)], [str(evidence)], 'Digest checked')
        downstream = check.make_receipt('own-session', 'autolearn', 'completed',
                                       [str(source)], [str(evidence)], 'Learnings checked')
        downstream['dependencies'] = {'session_audit': check._receipt_hash(upstream)}
        self.assertNotEqual('completed', check.receipt_status(downstream, 'own-session',
                                                             {'session_audit': upstream}))

    def test_precommit_does_not_require_future_phases(self):
        self.assertNotIn('commit_push', check.required_phases('precommit'))
        self.assertNotIn('reindex_search', check.required_phases('precommit'))
        self.assertIn('harness_prune', check.required_phases('precommit'))
        self.assertIn('commit_push', check.required_phases('final'))

    def test_harness_prune_receipt_requires_both_report_sections(self):
        source = self.root / 'harness_prune.md'
        source.write_text('source')
        report = self.root / 'harness_prune_report.md'
        report.write_text('### Plans\nplan rows only\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertNotEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))
        report.write_text('### Plans\n\n### Worktrees\n\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertNotEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))
        report.write_text('### Plans\nplan rows\n\n### Worktrees\nworktree rows\n\n### Scratch\nbucket table\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))

    def test_harness_prune_receipt_requires_scratch_section(self):
        source = self.root / 'harness_prune.md'
        source.write_text('source')
        report = self.root / 'harness_prune_report.md'
        report.write_text('### Plans\nplan rows\n\n### Worktrees\nworktree rows\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertNotEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))
        report.write_text('### Plans\nplan rows\n\n### Worktrees\nworktree rows\n\n### Scratch\n\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertNotEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))
        report.write_text('### Plans\nplan rows\n\n### Worktrees\nworktree rows\n\n### Scratch\nbucket table\n')
        receipt = check.make_receipt('own-session', 'harness_prune', 'completed',
                                     [str(source)], [str(report)], 'Prune ran')
        self.assertEqual('completed', check.receipt_status(
            receipt, 'own-session', {'harness_prune': receipt}))

    def test_nonterminal_receipt_never_finishes_phase(self):
        receipt = check.make_receipt('own-session', 'session_audit', 'blocked', [], [], 'Permission denied')
        self.assertEqual('blocked', check.receipt_status(receipt, 'own-session'))


    def test_phase3_artifact_accepts_a_ledger_only_session(self):
        archive = self.root / 'archive.json'
        archive.write_text(json.dumps({
            'Self_Evaluate_Themes': {'patterns': {'A': 'fixture'}},
            'structured_entries': [],
            'legacy_entries': [],
        }), encoding='utf-8')
        (self.root / 'archive.jsonl').write_text(
            json.dumps(archive_entry('own-session')) + '\n', encoding='utf-8')
        self.assertTrue(check.phase3_artifact_ok(str(archive), 'own-session'))

    def test_phase3_artifact_rejects_a_malformed_selected_session(self):
        archive = self.root / 'archive.json'
        archive.write_text(json.dumps({
            'Self_Evaluate_Themes': {'patterns': {'A': 'fixture'}},
            'structured_entries': [{'id': 900, 'session_id': 'own-session'}],
            'legacy_entries': [],
        }), encoding='utf-8')
        self.assertFalse(check.phase3_artifact_ok(str(archive), 'own-session'))

    def test_cli_checks_receipts_and_excludes_future_phases(self):
        self.write_rows([])
        source, evidence = self.root / 'source.py', self.root / 'result.json'
        source.write_text('source')
        evidence.write_text('{}')
        archive = self.root / 'archive.json'
        archive.write_text(json.dumps({
            'Self_Evaluate_Themes': {'patterns': {'A': 'fixture'}},
            'structured_entries': [archive_entry('own-session')],
            'legacy_entries': [],
        }))
        directory = self.root / 'receipts'
        directory.mkdir()
        prune_report = self.root / 'harness-prune.md'
        prune_report.write_text('### Plans\nfixture\n\n### Worktrees\nfixture\n\n### Scratch\nfixture\n')
        for stem in check.required_phases('precommit'):
            phase_evidence = prune_report if stem == 'harness_prune' else evidence
            receipt = check.make_receipt('own-session', stem, 'completed',
                                         [str(source)], [str(phase_evidence)], 'Fixture phase evidence')
            (directory / (stem + '.json')).write_text(json.dumps(receipt))
        args = [sys.executable, '-B', check.__file__, '--transcript', str(self.transcript),
                '--session', 'own-session', '--repo', str(self.root), '--receipts', str(directory), '--archive', str(archive), '--artifacts']
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        precommit = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, precommit.returncode, precommit.stdout + precommit.stderr)
        final = subprocess.run(args + ['--stage', 'final'], env=environment,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(1, final.returncode, final.stdout + final.stderr)
        self.assertIn('commit_push', final.stdout)
        source.write_text('changed')
        stale = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(1, stale.returncode, stale.stdout + stale.stderr)
        self.assertIn('stale', stale.stdout)

    def test_cli_records_one_owned_receipt(self):
        self.write_rows([])
        source, evidence = self.root / 'source.py', self.root / 'result.json'
        source.write_text('source')
        evidence.write_text('{}')
        directory = self.root / 'receipts'
        args = [sys.executable, '-B', check.__file__, '--transcript', str(self.transcript),
                '--session', 'own-session', '--repo', str(self.root), '--receipts', str(directory), '--record', 'session_digest',
                '--status', 'completed', '--input', str(source), '--evidence', str(evidence),
                '--reason', 'Fixture completed']
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        receipt = json.loads((directory / 'session_digest.json').read_text())
        self.assertEqual('completed', check.receipt_status(receipt, 'own-session'))


    def test_transitive_dependencies_can_be_recorded_and_checked(self):
        source, evidence = self.root / 'source.py', self.root / 'result.json'
        source.write_text('source')
        evidence.write_text('{}')
        receipts = {}
        receipts['session_digest'] = check.make_receipt('own-session', 'session_digest', 'completed',
            [str(source)], [str(evidence)], 'Digest checked')
        receipts['session_audit'] = check.make_receipt('own-session', 'session_audit', 'completed',
            [str(source)], [str(evidence)], 'Audit checked', dependencies=receipts)
        receipt = check.make_receipt('own-session', 'autolearn', 'completed',
            [str(source)], [str(evidence)], 'Learnings checked',
            dependencies={'session_audit': receipts['session_audit']}, available=receipts)
        self.assertEqual('completed', check.receipt_status(receipt, 'own-session', receipts))

    def test_unavailable_search_plugin_is_a_valid_documented_skip(self):
        self.write_rows([])
        source = self.root / 'scope.json'
        source.write_text('{"search_plugin": "unavailable"}')
        receipt = check.make_receipt('own-session', 'reindex_search', 'skipped', [str(source)], [],
                                     'Plugin is unavailable in this session')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            check.render(str(self.transcript), 'own-session', str(self.root / 'archive.json'), False,
                         {'reindex_search': receipt}, 'final')
        self.assertNotIn('invalid-skip', output.getvalue())


    def test_cli_refuses_transcript_identity_mismatch_before_receipt_write(self):
        self.write_rows([])
        source = self.root / 'source.py'
        source.write_text('fixture')
        directory = self.root / 'receipts'
        args = [sys.executable, '-B', check.__file__, '--transcript', str(self.transcript),
                '--session', 'different-session', '--repo', str(self.root),
                '--receipts', str(directory), '--record', 'session_digest',
                '--status', 'completed', '--input', str(source), '--evidence', str(source),
                '--reason', 'Synthetic mismatched identity']
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('identity', result.stdout.lower())
        self.assertFalse(directory.exists())

    def test_cli_requires_explicit_identity_when_recording_a_proof_transcript(self):
        self.write_rows([])
        directory = self.root / 'receipts'
        args = [sys.executable, '-B', check.__file__, '--transcript', str(self.transcript),
                '--repo', str(self.root), '--receipts', str(directory), '--record', 'session_digest',
                '--status', 'blocked', '--reason', 'Synthetic missing identity']
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
        environment.pop('CLAUDE_CODE_SESSION_ID', None)
        result = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertFalse(directory.exists())


if __name__ == '__main__':
    unittest.main()
