"""Behavioral acceptance regressions for the J3 conformance repair rows."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fold_check
import post_check
from test_fold_check import Fixture


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = Fixture(self.root)

    def check(self, row, decision, *, ruled=()):
        digest = {'applied': [dict(decision, id=row['id'], change='x')], 'deferred': []}
        failures = []
        fold_check.check_job({'subject': self.fx.subject(), 'digestValue': digest, 'ruled': ruled},
                             [row], {}, failures)
        return failures

    def test_r05_fileless_application_needs_changed_attributed_bytes(self):
        other = Fixture(self.root, 'other')
        subject = fold_check.Subject(self.root, [self.fx.rel, other.rel],
                                    {'kind': 'blobs', 'files': {**self.fx.subject().snapshot['files'],
                                                               **other.subject().snapshot['files']}})
        row = {'id': 'F1', 'action': 'FIX'}
        for decision, expected in (({'source': 'finding'}, 'fold-application-unattributed'),
                                   ({'source': 'finding', 'file': 'elsewhere.md'}, 'fold-application-unattributed'),
                                   ({'source': 'finding', 'file': other.rel}, 'fold-unchanged-file')):
            with self.subTest(decision=decision):
                other.plan.write_bytes(subject.pre_bytes(other.rel))
                subject._post.clear()
                digest = {'applied': [dict(decision, id='F1', change='x')], 'deferred': []}
                failures = []
                fold_check.check_job({'subject': subject, 'digestValue': digest}, [row], {}, failures)
                self.assertIn(expected, [f['kind'] for f in failures])
        subject._post.clear()
        digest['applied'][0]['file'] = self.fx.rel
        failures = []
        fold_check.check_job({'subject': subject, 'digestValue': digest}, [row], {}, failures)
        self.assertEqual(failures, [])

    def test_r05_basename_attribution_resolves_only_when_unique(self):
        def kinds(fixtures, named, cited):
            subject = fold_check.Subject(self.root, [f.rel for f in fixtures],
                                        {'kind': 'blobs', 'files': {k: v for f in fixtures
                                                                   for k, v in f.subject().snapshot['files'].items()}})
            row = {'id': 'F1', 'action': 'FIX', 'file': f'{cited}:3'}
            digest = {'applied': [{'id': 'F1', 'change': 'x', 'source': 'finding', 'file': named}], 'deferred': []}
            failures = []
            fold_check.check_job({'subject': subject, 'digestValue': digest}, [row], {}, failures)
            return [f['kind'] for f in failures]
        draft = Fixture(self.root, 'docs/draft/plan')
        notes = Fixture(self.root, 'docs/draft/notes')
        self.assertNotIn('fold-application-unattributed', kinds([draft, notes], 'plan.md', draft.rel))
        self.assertIn('fold-application-unattributed', kinds([draft, notes], 'notes.md', draft.rel))
        twin = Fixture(self.root, 'docs/old/plan')
        self.assertIn('fold-application-unattributed', kinds([draft, twin], 'plan.md', draft.rel))

    def test_r05_attributed_old_new_is_actually_checked(self):
        other = Fixture(self.root, 'other')
        subject = fold_check.Subject(self.root, [self.fx.rel, other.rel],
                                    {'kind': 'blobs', 'files': {**self.fx.subject().snapshot['files'],
                                                               **other.subject().snapshot['files']}})
        row = {'id': 'F1', 'action': 'FIX', 'old': 'pick a transport', 'new': 'pick another transport'}
        digest = {'applied': [{'id': 'F1', 'change': 'x', 'source': 'finding', 'file': self.fx.rel}], 'deferred': []}
        failures = []
        fold_check.check_job({'subject': subject, 'digestValue': digest}, [row], {}, failures)
        self.assertIn('fold-not-applied', [f['kind'] for f in failures])

    def test_r04_unresolved_holds_need_independent_ruling(self):
        for row in ({'id': 'F1', 'action': 'ASK'},
                    {'id': 'F1', 'action': 'PLAN', 'question': 'Which design?'},
                    {'id': 'F1', 'action': 'PLAN', 'agent': 'plc-test-readiness', 'question': 'Which fixture?'}):
            for source in ('finding', 'adapted', 'fold', 'ruling'):
                with self.subTest(row=row, source=source):
                    failures = self.check(row, {'source': source})
                    self.assertIn('fold-applied-held', [f['kind'] for f in failures])

    def test_r04_authorized_ruling_and_resolved_plan_are_allowed(self):
        for row in ({'id': 'F1', 'action': 'ASK'},
                    {'id': 'F1', 'action': 'PLAN', 'question': 'Which design?'}):
            failures = self.check(row, {'source': 'ruling'}, ruled=['F1'])
            self.assertNotIn('fold-applied-held', [f['kind'] for f in failures])
        failures = self.check({'id': 'F1', 'action': 'PLAN', 'options': ['Use existing owner']},
                              {'source': 'finding'})
        self.assertNotIn('fold-applied-held', [f['kind'] for f in failures])

    def test_a_finding_with_its_own_fix_or_first_option_is_applied_without_a_ruling(self):
        for agent in ('plc-test-readiness', 'plc-pattern-fit'):
            for row in ({'id': 'F1', 'action': 'FIX', 'agent': agent, 'old': 'pick a transport',
                         'new': 'pick another transport'},
                        {'id': 'F1', 'action': 'PLAN', 'agent': agent, 'question': 'Which fixture?',
                         'options': ['the shared fixture', 'a new one']}):
                with self.subTest(row=row):
                    failures = self.check(row, {'source': 'finding'})
                    self.assertNotIn('fold-applied-held', [f['kind'] for f in failures])


class OriginalAccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = Fixture(self.root)

    def review(self, *flags):
        out = self.root / 'post.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = post_check.main(['review', *flags, '--out', str(out)])
        return code, json.loads(out.read_text(encoding='utf-8'))

    def test_r02_group_checks_each_original_edit_and_verdict(self):
        first = self.fx.rows()[0]
        second = dict(first, id='F2', old='pick a transport', new='pick another transport',
                      evidence=[{'file': str(self.fx.plan), 'quote': 'absent quote'}])
        view = dict(first, id='M1', merged_from=['F1', 'F2'])
        path = self.root / 'review.json'
        path.write_text(json.dumps({'sources': [{'id': r['id'], 'finding': r} for r in [first, second]],
                                    'view': [view]}), encoding='utf-8')
        digest = {'applied': [{'id': 'M1', 'change': 'x', 'source': 'finding'}], 'deferred': []}
        self.fx.write(digest)
        code, report = self.review('--findings', str(path), '--digest', str(self.fx.digest_path),
                                   '--snapshot', str(self.fx.manifest))
        self.assertEqual((code, report['outcome']), (0, 'pass'), report)
        self.assertEqual(report['counts'], {'VERIFIED': 1, 'UNVERIFIED': 1, 'UNCHECKED': 0})
        self.assertEqual(set(report['verdicts']), {'F1', 'F2'})
        folded = json.loads(Path(report['foldReport']).read_text(encoding='utf-8'))
        self.assertEqual([r['id'] for r in folded['dispositions']], ['F1', 'F2'])
        self.assertTrue(any(f.get('id') == 'F2' and f['kind'] == 'fold-not-applied'
                            for f in report['failures']), report)
        self.assertEqual([h['id'] for h in report['held']], ['F2'])

    def test_r02_grouped_success_and_defer_count_originals(self):
        first = self.fx.rows()[0]
        second = dict(first, id='F2')
        view = dict(first, id='M1', merged_from=['F1', 'F2'])
        directory = self.root / 'sidecar'
        directory.mkdir()
        path = directory / 'review.json'
        path.write_text(json.dumps({'sources': [{'id': r['id'], 'finding': r} for r in [first, second]],
                                    'view': [view]}), encoding='utf-8')
        digest = {'applied': [{'id': 'M1', 'change': 'x', 'source': 'finding'}], 'deferred': []}
        self.fx.write(digest)
        (directory / 'fold_jobs.json').write_text(json.dumps([dict(self.fx.subject().describe(),
                                                                 digest=str(self.fx.digest_path))]), encoding='utf-8')
        code, report = self.review('--sidecar-dir', str(directory), '--expect-total', '1')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['counts']['VERIFIED'], 2)
        self.assertEqual(set(report['verdicts']), {'F1', 'F2'})
        digest.update(applied=[], deferred=[{'id': 'M1', 'class': 'spike', 'reason': 'measure first'}])
        self.fx.write(digest)
        code, report = self.review('--sidecar-dir', str(directory), '--expect-total', '1')
        self.assertEqual(code, 0, report)
        self.assertEqual([r['id'] for r in report['held']], ['F1', 'F2'])

    def test_r02_missing_original_bodies_are_unavailable_not_verified(self):
        path = self.root / 'review.json'
        row = dict(self.fx.rows()[0], id='M1', merged_from=['F1', 'F2'])
        path.write_text(json.dumps({'view': [row], 'sources': [{'id': 'F1'}, {'id': 'F2'}]}), encoding='utf-8')
        code, report = self.review('--findings', str(path))
        self.assertEqual((code, report['status']), (2, 'unavailable'))
        self.assertIn('original sources', report['errors'][0])

    def test_r04_public_checker_requires_caller_authorization(self):
        row = dict(self.fx.rows()[0], action='ASK', question='Which choice?')
        path = self.root / 'hold.json'
        path.write_text(json.dumps({'findings': [row]}), encoding='utf-8')
        digest = {'applied': [{'id': 'F1', 'change': 'x', 'source': 'ruling'}], 'deferred': []}
        self.fx.write(digest)
        flags = ('--findings', str(path), '--digest', str(self.fx.digest_path), '--snapshot', str(self.fx.manifest))
        code, report = self.review(*flags)
        self.assertEqual((code, report['outcome']), (0, 'pass'), report)
        self.assertIn(('F1', 'ask'), [(r['id'], r['class']) for r in report['held']])
        code, report = self.review(*flags, '--ruled', 'F1')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['held'], [])
        self.assertEqual(report['source'][-2:], ['--ruled', 'F1'])

    def test_r02_native_unrouted_ask_remains_visible(self):
        run = self.root / 'sess/subagents/workflows/wf'
        run.mkdir(parents=True)
        first = dict(self.fx.rows()[0], agent='plc-pattern-fit', category='bug', rationale='typo')
        ask = dict(first, id='F2', action='ASK', file='outside.md:1', question='Which choice?',
                   evidence=[])
        digest = {'applied': [{'id': 'F1', 'change': 'x', 'source': 'finding'}], 'deferred': []}
        self.fx.write(digest)
        prompt = '\n'.join(['FOLD STAGE of a review.', f'Root: {self.root}',
                            f'Snapshot: {self.fx.manifest}', f'Files: {self.fx.rel}',
                            'CONSOLIDATED FINDINGS (1 ids: F1):', json.dumps([first])])
        (run / 'agent-fold.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': prompt}}) + '\n', encoding='utf-8')
        events = [{'type': 'started', 'key': 'seat', 'agentId': 'seat', 'label': 'review:one', 'phase': 'Review'},
                  {'type': 'result', 'key': 'seat', 'agentId': 'seat', 'result': {'findings': [first, ask]}},
                  {'type': 'started', 'key': 'fold', 'agentId': 'fold', 'label': 'review:fold', 'phase': 'Fold'},
                  {'type': 'result', 'key': 'fold', 'agentId': 'fold', 'result': digest}]
        (run / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
        code, report = self.review('--workflow-dir', str(run), '--expect-total', '2')
        self.assertEqual(code, 0, report)
        self.assertIn(('F2', 'ask'), [(r['id'], r['class']) for r in report['held']])
        self.assertEqual(sum(report['counts'].values()), 2)


class ClaimsCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.good = '```claims\n' + json.dumps({'id': 'K1', 'evidence': [{'file': str(ROOT / '.claude/tools/post_check.py'),
                                                                     'quote': 'Close a findings dispatch'}]}) + '\n```'

    def report(self, *flags):
        out = self.root / 'report.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = post_check.main(['claims', *flags, '--out', str(out)])
        return code, json.loads(out.read_text(encoding='utf-8'))

    def test_r23_each_expected_workflow_job_needs_a_deliverable(self):
        run = self.root / 'sess/subagents/workflows/run'
        run.mkdir(parents=True)
        for bad in ('no claims here', None, 42, '```claims\nnot json\n```'):
            with self.subTest(bad=bad):
                events = [{'type': 'started', 'key': 'k1', 'agentId': 'a1', 'label': 'good'},
                          {'type': 'result', 'agentId': 'a1', 'result': self.good},
                          {'type': 'started', 'key': 'k2', 'agentId': 'a2', 'label': 'required'},
                          {'type': 'result', 'agentId': 'a2', 'result': bad}]
                (run / 'journal.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
                code, report = self.report('--workflow-dir', str(run))
                self.assertEqual(code, 1, report)
                self.assertTrue(any('required' in e for e in report['errors']), report)

    def test_r23_inventory_job_without_stdout_is_uncovered(self):
        (self.root / 'good.out.json').write_text('{}', encoding='utf-8')
        (self.root / 'good.md').write_text(self.good, encoding='utf-8')
        inventory = {'schemaVersion': 1, 'jobs': [{'label': 'good'}, {'label': 'missing'}]}
        (self.root / 'fanout-fixture.inventory.json').write_text(json.dumps(inventory), encoding='utf-8')
        code, report = self.report('--sidecar-dir', str(self.root))
        self.assertEqual(code, 1, report)
        self.assertTrue(any('missing' in e for e in report['errors']), report)

    def test_r23_explicit_empty_structured_deliverable_is_valid(self):
        path = self.root / 'empty.json'
        path.write_text(json.dumps({'claims': []}), encoding='utf-8')
        code, report = self.report('--result', str(path))
        self.assertEqual(code, 0, report)


class RequiredDeliverableScopeTests(unittest.TestCase):
    """R23 fails a blockless result only where the caller requires a claims deliverable."""

    def test_r23_blockless_prose_is_an_error_for_a_required_claims_job(self):
        rows, errors = post_check.result_claims('da-claims', 'done, no block here', required=True)
        self.assertEqual((rows, errors), ([], ['da-claims: no claims or findings block']))

    def test_r23_any_job_read_by_the_sidecar_may_return_prose(self):
        import sidecar_completion
        self.assertEqual(sidecar_completion.source_rows('author', 'deliverable\nsecond line'), ([], []))
        self.assertEqual(sidecar_completion.source_rows('author', None), ([], []))


if __name__ == '__main__':
    unittest.main()
