from pathlib import Path
import json
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dispatch_result

class StructuredCompletion(unittest.TestCase):
    def test_structured_claims_are_the_deliverable(self):
        payload = {'claims': [{'id': 'C1', 'evidence': []}]}
        raw = json.dumps({'type': 'result', 'result': 'schema output is attached', 'structured_output': payload})
        self.assertEqual(json.loads(dispatch_result.final_message(raw)), payload)
    def test_plain_and_synthetic_capture_still_use_real_result(self):
        raw = '\n'.join(map(json.dumps, [{'type': 'result', 'result': 'real answer'},
                                         {'type': 'result', 'result': 'limit', 'synthetic': True}]))
        self.assertEqual(dispatch_result.final_message(raw), 'real answer')

if __name__ == '__main__':
    unittest.main()
