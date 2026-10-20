from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import post_check

class Reuse(unittest.TestCase):
    def test_normalizer_preserves_source_index_after_bad_line(self):
        text = '```claims\nnot-json\n{"id":"C1","evidence":[]}\n```'
        rows, errors = post_check.result_claims('seat', text, preserve=True, source_indices=True)
        self.assertTrue(errors)
        self.assertEqual(rows[0]['__sourceIndex'], 2)

    def test_sidecar_host_count_is_checked(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'e.txt').write_text('true quote')
            (root / 'review.json').write_text(json.dumps({'view': [{'id': 'F1', 'file': 'e.txt:1',
                'action': 'FIX', 'agent': 'seat', 'category': 'bug', 'description': 'defect', 'rationale': 'source',
                'evidence': [{'file': 'e.txt', 'quote': 'true quote'}]}]}))
            out = root / 'check.json'
            code = post_check.main(['review', '--sidecar-dir', tmp, '--root', tmp, '--expect-total', '2', '--out', str(out)])
            report = json.loads(out.read_text())
            self.assertEqual(code, 1, 'sidecar host count was ignored')
            self.assertEqual(report['failures'][0]['kind'], 'fold-routing')

    def test_main_forwards_host_only_independent_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(post_check, 'review', return_value=0) as review:
            cache = ({}, 'independent.json')
            self.assertEqual(post_check.main(['review', '--result', 'r.json', '--out', str(Path(tmp) / 'out.json')],
                                             independent=cache), 0)
            self.assertEqual(review.call_args.kwargs, {'independent': cache})

if __name__ == '__main__':
    unittest.main()
