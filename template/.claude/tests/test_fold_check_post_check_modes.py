"""post_check review needs canonical evidence and the routed subject root in fold_check."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude/tools'))
import fold_check


class CanonicalEvidenceTests(unittest.TestCase):
    def test_validation_keeps_each_evidence_kind_and_rejects_malformed_items(self):
        for item in ({'command': 'git status --short', 'output': ''}, {'owner': 'Owner words'},
                     {'url': 'https://example.com', 'quote': 'External words'},
                     {'file': 'source.txt', 'quote': 'Source words'}):
            row = {'id': 'F1', 'evidence': [item]}
            self.assertEqual(fold_check._validated([row], 'fixture'), [row])
        with self.assertRaises(fold_check.Unusable):
            fold_check._validated([{'id': 'F1', 'evidence': [{'command': 3, 'output': ''}]}], 'fixture')

    def test_independent_command_uses_the_routed_subject_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
            (root / 'subject.txt').write_text('Subject bytes', encoding='utf-8')
            subprocess.run(['git', '-C', str(root), 'add', 'subject.txt'], check=True)
            subject = fold_check.Subject(root, ['subject.txt'], fold_check.snapshot_blobs(root, ['subject.txt']))
            rows = [{'id': 'F1', 'file': 'subject.txt:1', 'evidence': [
                {'command': 'git ls-files -- subject.txt', 'output': 'subject.txt'}]}]
            verdicts, path = fold_check.independent_verdicts(rows, [subject], root / 'check')
            self.assertEqual(verdicts, {'F1': 'VERIFIED'})
            self.assertEqual(json.loads(Path(path).read_text(encoding='utf-8'))['verdicts'][0]['items'][0]['verdict'], 'VERIFIED')


class BarFoldTests(unittest.TestCase):
    def _check(self, bar, ruled=(), source=None, after='Object bytes', kind='fold-applied-below-bar', **row):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True)
            (root / 'subject.txt').write_text('Subject bytes', encoding='utf-8')
            subprocess.run(['git', '-C', str(root), 'add', 'subject.txt'], check=True)
            subject = fold_check.Subject(root, ['subject.txt'], fold_check.snapshot_blobs(root, ['subject.txt']))
            (root / 'subject.txt').write_text(after, encoding='utf-8')
            rows = [dict({'id': 'F1', 'action': 'FIX', 'category': 'bug', 'critical': False, 'file': 'subject.txt:1',
                          'description': 'd', 'rationale': 'r', 'old': 'Subject bytes', 'new': 'Object bytes',
                          'minRigor': 'thorough'}, **row)]
            entry = dict({'id': 'F1', 'change': 'x'}, **({'source': source} if source else {}))
            job = {'subject': subject, 'digestValue': {'applied': [entry], 'deferred': []},
                   'bar': bar, 'ruled': list(ruled)}
            failures = []
            fold_check.check_job(job, rows, {}, failures)
            return [failure for failure in failures if kind in (None, failure['kind'])]

    def _kinds(self, bar, **kwargs):
        return [failure['kind'] for failure in self._check(bar, kind=None, **kwargs)]

    def test_below_bar_finding_applied_verbatim_passes(self):
        self.assertEqual(self._check('light'), [])
        self.assertEqual(self._check('light', source='finding'), [])

    def test_below_bar_finding_applied_in_the_folds_own_words_fails_its_id(self):
        for source in ('adapted', 'fold', 'ruling'):
            failures = self._check('light', source=source)
            self.assertEqual([failure.get('ids') for failure in failures], [['F1']], source)

    def test_below_bar_deletion_applied_verbatim_passes(self):
        self.assertEqual(self._check('light', after='Subject ', old='bytes', new=''), [])

    def test_below_bar_deletion_left_in_place_fails_as_not_applied_not_below_bar(self):
        failures = self._check('light', after='Subject bytes', old='bytes', new='')
        self.assertEqual(failures, [])
        self.assertIn('fold-not-applied', self._kinds('light', after='Subject bytes', old='bytes', new=''))

    def test_below_bar_fix_labelled_finding_but_reworded_fails_its_id(self):
        failures = self._check('light', after='Thing bytes')
        self.assertEqual([failure.get('ids') for failure in failures], [['F1']])

    def test_below_bar_plan_or_pairless_fix_fails_its_id(self):
        for row in ({'action': 'PLAN', 'options': ['do it'], 'old': None, 'new': None}, {'old': None, 'new': None}):
            failures = self._check('light', **row)
            self.assertEqual([failure.get('ids') for failure in failures], [['F1']], row)

    def test_ruled_below_bar_finding_may_be_applied(self):
        self.assertEqual(self._check('light', ruled=['F1'], source='adapted'), [])

    def test_applied_counting_finding_passes_the_bar(self):
        self.assertEqual(self._check('thorough'), [])


if __name__ == '__main__':
    unittest.main()
