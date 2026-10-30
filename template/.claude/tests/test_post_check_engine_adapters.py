import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import post_check
import verify_claims

class EngineAdapters(unittest.TestCase):
    def test_review_merge_uses_all_original_evidence(self):
        from unittest.mock import patch
        items = [{'id': 'F1', 'finding': {'file': 'source', 'description': 'first', 'evidence': [{'file': 'source', 'quote': 'genuine'}]}}, {'id': 'F2', 'finding': {'file': 'source', 'description': 'second', 'evidence': [{'file': 'source', 'quote': 'fabricated'}]}}, {'id': 'F3', 'finding': {'file': 'other', 'description': 'outside', 'evidence': []}}]
        archive = {'items': items, 'merges': [{'id': 'M1', 'merged_from': ['F1', 'F2']}], 'status': 'completed'}
        with patch('session_digest.build_workflow_result_archive', return_value=archive):
            rows, _ = post_check._archive_rows(Path('fixture'))
        self.assertEqual({row['id'] for row in rows}, {'M1', 'F3'})
        merged = next(row for row in rows if row['id'] == 'M1')
        self.assertEqual([item['quote'] for item in merged['evidence']], ['genuine', 'fabricated'])
    def test_reachability_subject_root_and_no_self_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'source').write_text('genuine citation\n', encoding='utf-8')
            for quote, expected in [('genuine citation', 'VERIFIED'), ('fabricated citation', 'UNVERIFIED')]:
                rows, errors = post_check.result_claims('reach:task:item', {'id': 'item', 'root': temp, 'ruling': 'KEEP', 'cites': [{'path': 'source', 'quote': quote, 'exists': True, 'supports': 'yes'}]})
                self.assertEqual(errors, [])
                self.assertEqual([r['id'] for r in rows], ['reach:task:item#1'])
                report = verify_claims.check_claims(rows, root=root.parent)
                self.assertEqual(report['verdicts']['reach:task:item#1']['verdict'], expected)
    def test_bad_citation_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            source.write_text('genuine', encoding='utf-8')
            rows, errors = post_check.result_claims('reach:task:item', {'root': temp, 'ruling': 'KEEP', 'cites': [{'path': 'source', 'quote': 'genuine'}, {'path': 'source'}]})
            report = verify_claims.check_claims(rows)
            self.assertEqual(report['verdicts']['reach:task:item#1']['verdict'], 'UNCHECKED')
    def test_worklog_provenance_is_checked_even_without_overlaps(self):
        rows, errors = post_check.result_claims('worklog:relevance', {'overlaps': [], 'checked': {'basis': 'nothing overlaps', 'evidence': []}})
        self.assertEqual(errors, [])
        self.assertEqual([row['id'] for row in rows], ['worklog:relevance#checked'])
        self.assertEqual(verify_claims.check_claims(rows)['verdicts']['worklog:relevance#checked']['verdict'], 'UNCHECKED')
    def test_worklog_vault_title_and_empty_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp) / 'vault'
            vault.mkdir()
            worklog = vault / 'Worklog.md'
            worklog.write_text('## Domain\n### Genuine title\n', encoding='utf-8')
            rows, errors = post_check.result_claims('worklog:relevance', {'overlaps': [{'title': 'Genuine title', 'evidence': [{'file': str(worklog), 'quote': 'Genuine title'}]}, {'title': 'Fabricated title', 'evidence': [{'file': str(worklog), 'quote': 'Fabricated title'}]}, {'title': 'No evidence', 'evidence': []}]})
            self.assertEqual(errors, [])
            report = verify_claims.check_claims(rows)
            self.assertEqual([v['verdict'] for v in report['verdicts'].values()], ['VERIFIED', 'UNVERIFIED', 'UNCHECKED'])

if __name__ == '__main__':
    unittest.main()
