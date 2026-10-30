from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fold_check


class SubjectPath(unittest.TestCase):
    """A fold job records its snapshot as an absolute path: a later reader (the Stop audit) resolves a relative one against its own cwd, which drifts."""

    def test_relative_manifest_recorded_absolute(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fold_check, 'REPO', Path(tmp)):
            (Path(tmp) / 'round').mkdir()
            (Path(tmp) / 'round' / 'snap.json').write_text(json.dumps({'kind': 'blobs', 'files': {'plan.md': 'abc'}}), encoding='utf-8')
            subject = fold_check.load_subject(tmp, 'round/snap.json', ['plan.md'])
            recorded = Path(subject.describe()['snapshot'])
            self.assertTrue(recorded.is_absolute(), recorded)
            self.assertEqual(recorded, Path(tmp) / 'round' / 'snap.json')

    def test_relative_manifest_from_manifest_recorded_absolute(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fold_check, 'REPO', Path(tmp)):
            (Path(tmp) / 'round').mkdir()
            (Path(tmp) / 'round' / 'snap.json').write_text(json.dumps({'kind': 'blobs', 'files': {'plan.md': 'abc'}}), encoding='utf-8')
            subject = fold_check.subject_from_manifest('round/snap.json', root=tmp)
            recorded = Path(subject.describe()['snapshot'])
            self.assertTrue(recorded.is_absolute(), recorded)
            self.assertEqual(recorded, Path(tmp) / 'round' / 'snap.json')

    def test_direct_subject_relative_manifest_path_recorded_absolute(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fold_check, 'REPO', Path(tmp)):
            subject = fold_check.Subject(tmp, ['plan.md'], {'kind': 'blobs', 'files': {'plan.md': 'abc'}}, manifest_path='round/snap.json')
            self.assertEqual(Path(subject.describe()['snapshot']), Path(tmp) / 'round' / 'snap.json')

    def test_absolute_manifest_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / 'snap.json'
            snap.write_text(json.dumps({'kind': 'blobs', 'files': {'plan.md': 'abc'}}), encoding='utf-8')
            subject = fold_check.load_subject(tmp, str(snap), ['plan.md'])
            self.assertEqual(Path(subject.describe()['snapshot']), snap)


if __name__ == '__main__':
    unittest.main()
