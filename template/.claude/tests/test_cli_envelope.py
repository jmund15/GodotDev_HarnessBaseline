import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('cli_envelope_subject', ROOT / '.claude/scripts/lib/cli_envelope.py')
env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(env)
MINE = '11111111-2222-4333-8444-555555555555'
PEER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


class CliEnvelopeTests(unittest.TestCase):
    def test_single_object_is_one_event_and_the_result(self):
        obj = {'result': 'done', 'session_id': MINE}
        evts = env.events(json.dumps(obj))
        self.assertEqual(evts, [obj])
        self.assertEqual(env.result_event(evts), obj)

    def test_array_keeps_every_event_and_the_last_result_wins(self):
        rows = [{'type': 'system', 'session_id': MINE},
                {'type': 'result', 'result': 'first', 'session_id': MINE},
                {'type': 'assistant'},
                {'type': 'result', 'result': 'last', 'session_id': MINE}]
        evts = env.events(json.dumps(rows))
        self.assertEqual(evts, rows)
        self.assertEqual(env.result_event(evts)['result'], 'last')
        self.assertEqual(env.session_id(evts), MINE)

    def test_jsonl_keeps_parsed_dict_lines_only(self):
        raw = '\n'.join([json.dumps({'type': 'system', 'session_id': MINE}), 'not json', '[1, 2]',
                         json.dumps({'type': 'result', 'result': 'ok', 'session_id': MINE})])
        evts = env.events(raw)
        self.assertEqual([e['type'] for e in evts], ['system', 'result'])
        self.assertEqual(env.result_event(evts)['result'], 'ok')
        self.assertEqual(env.session_id(evts), MINE)

    def test_empty_and_garbage_read_as_nothing(self):
        for raw in ('', None, 'garbage {', '42', '"text"'):
            evts = env.events(raw)
            self.assertEqual(evts, [], raw)
            self.assertIsNone(env.result_event(evts))
            self.assertIsNone(env.session_id(evts))

    def test_non_uuid_session_id_is_not_identity(self):
        evts = env.events(json.dumps({'type': 'result', 'session_id': 'not-a-uuid'}))
        self.assertIsNone(env.session_id(evts))

    def test_two_distinct_session_ids_are_not_guessed(self):
        raw = '\n'.join(json.dumps({'type': 'result', 'session_id': sid}) for sid in (MINE, PEER))
        self.assertIsNone(env.session_id(env.events(raw)))

    def test_session_id_ignores_non_envelope_rows(self):
        evts = [{'type': 'result', 'session_id': MINE}, {'type': 'user', 'session_id': PEER}]
        self.assertEqual(env.session_id(evts), MINE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
