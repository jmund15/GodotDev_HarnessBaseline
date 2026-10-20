import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import review_result
import verify_claims

class Deferred(unittest.TestCase):
    def test_all_canonical_evidence_kinds_and_merge(self):
        finding = {'agent': 'seat', 'action': 'FIX', 'category': 'bug', 'description': 'defect',
                   'rationale': 'source', 'file': 'e.txt:1'}
        items = [{'file': 'e.txt', 'quote': 'q'}, {'command': 'git status --short', 'output': ''},
                 {'owner': 'words', 'task': 'task'}, {'url': 'https://example.org', 'quote': 'q', 'artifact': 'e.txt'}]
        for evidence in items:
            self.assertTrue(review_result._finding(dict(finding, evidence=[evidence])), evidence)
        sources = [{'id': 'F' + str(i), 'seat': str(i), 'finding': dict(finding, evidence=items)} for i in (1, 2)]
        merged = review_result.merge_view(sources, review_result.exact_groups(sources))
        self.assertEqual(merged['view'][0]['evidence'], items)
        self.assertFalse(review_result._finding(dict(finding, evidence=[{'command': 'git status --short'}])))

    def test_host_defers_only_evidence_not_consolidation(self):
        prepared = {'sources': [{'id': 'F1', 'seat': 'seat', 'finding': {
            'file': 'e.txt:1', 'description': 'one defect', 'action': 'FIX',
            'evidence': [{'command': 'git status --short', 'output': ''}]}}],
            'counts': {'completed': 1}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(verify_claims, 'verify_findings') as check:
            result = review_result.consolidate([{}], {}, Path(tmp), prepared=copy.deepcopy(prepared), verify=False)
            check.assert_not_called()
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['view'][0]['id'], 'F1')
            self.assertTrue((Path(tmp) / 'review_sources.json').is_file())

if __name__ == '__main__':
    unittest.main()
