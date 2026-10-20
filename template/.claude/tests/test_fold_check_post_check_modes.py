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


if __name__ == '__main__':
    unittest.main()
