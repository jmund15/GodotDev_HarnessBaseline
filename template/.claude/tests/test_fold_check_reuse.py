from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import fold_check

class Reuse(unittest.TestCase):
    def test_host_report_reused_no_checker_call(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fold_check, '_validated', return_value=[]), patch.object(fold_check, 'independent_verdicts') as check:
            result = fold_check.run(None, [], str(Path(tmp) / 'fold'), independent=({}, str(Path(tmp) / 'verdicts.json')))
            check.assert_not_called()
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(result['independentVerdicts'], [])
    def test_evidence_free_rows_need_no_cached_quote_verdict(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(fold_check, 'load_rows', return_value=[{'id': 'F3', 'evidence': []}]):
            result = fold_check.run('findings.json', [], str(Path(tmp) / 'fold'), independent=({}, 'verdicts.json'))
            self.assertEqual(result['dispositions'][0]['id'], 'F3')
            self.assertEqual(result['failures'][0]['kind'], 'fold-routing')

    def test_wrong_scope_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(fold_check, '_validated', return_value=[]):
            with self.assertRaisesRegex(fold_check.Unusable, 'do not cover'):
                fold_check.run(None, [], str(Path(tmp) / 'fold'), independent=({'invented': 'VERIFIED'}, 'x'))

if __name__ == '__main__':
    unittest.main()
