import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ccp_probe_subject', ROOT / '.claude/scripts/lib/ccp_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
MINE = '11111111-2222-4333-8444-555555555555'
PEER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


class ProbeIdentityTests(unittest.TestCase):
    def capture(self, root, sid, model, stamp, prefix='004'):
        path = Path(root) / sid / 'request' / f'{prefix}-020-upstream-request.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'model': model, 'reasoning': {'effort': 'max'}}), encoding='utf-8', newline='\n')
        os.utime(path, (stamp, stamp))
        return path

    def test_no_session_cannot_attest_a_newer_peer(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, PEER, 'peer-model', 200)
            self.assertIsNone(probe.attest(100))

    def test_session_identity_beats_global_recency(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, MINE, 'my-model', 150)
            self.capture(root, PEER, 'peer-model', 200)
            self.assertEqual(probe.attest(100, MINE), ('my-model', 'max'))

    def test_capture_stage_number_is_not_identity(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, MINE, 'my-model', 150, prefix='003')
            self.assertEqual(probe.attest(100, MINE), ('my-model', 'max'))

    def test_old_own_capture_and_invalid_identity_are_unknown(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, MINE, 'my-model', 50)
            self.assertIsNone(probe.attest(100, MINE))
            self.assertIsNone(probe.attest(0, '../' + MINE))

    def test_unreadable_newest_capture_falls_back(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, MINE, 'my-model', 150, prefix='003')
            newest = self.capture(root, MINE, 'ignored-model', 200, prefix='004')
            newest.write_text('{', encoding='utf-8', newline='\n')
            self.assertEqual(probe.attest(100, MINE), ('my-model', 'max'))

    def test_compaction_capture_does_not_replace_run_attestation(self):
        with tempfile.TemporaryDirectory() as root, patch.object(probe, 'TRAFFIC_ROOT', root):
            self.capture(root, MINE, 'my-model', 150, prefix='003')
            newest = self.capture(root, MINE, 'my-model', 200, prefix='004')
            newest.write_text(json.dumps({
                'model': 'my-model',
                'reasoning': {'effort': 'low'},
                'input': [{
                    'type': 'message',
                    'role': 'developer',
                    'content': [{
                        'type': 'input_text',
                        'text': ('CRITICAL: Respond with TEXT ONLY. Do NOT call any tools. '
                                 'Your task is to create a detailed summary of the conversation so far'),
                    }],
                }],
            }), encoding='utf-8', newline='\n')
            self.assertEqual(probe.attest(100, MINE), ('my-model', 'max'))

    def test_g11_quoted_compaction_words_in_user_or_tool_input_are_not_skipped(self):
        quoted = (probe.COMPACT_MESSAGE_PREFIX + ' ' + probe.COMPACT_MESSAGE_TASK)
        for item in (
            {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': quoted}]},
            {'type': 'function_call_output', 'call_id': 'fixture', 'output': quoted},
        ):
            with self.subTest(item=item), tempfile.TemporaryDirectory() as root, \
                    patch.object(probe, 'TRAFFIC_ROOT', root):
                self.capture(root, MINE, 'older-model', 150, prefix='003')
                newest = self.capture(root, MINE, 'quoted-task-model', 200, prefix='004')
                newest.write_text(json.dumps({
                    'model': 'quoted-task-model', 'reasoning': {'effort': 'high'}, 'input': [item],
                }), encoding='utf-8', newline='\n')
                self.assertEqual(probe.attest(100, MINE), ('quoted-task-model', 'high'))

    def test_g9_record_persists_attested_effort_and_agreement(self):
        with tempfile.TemporaryDirectory() as root:
            record = Path(root) / 'run.record.json'
            ledger = Path(root) / 'ledger.jsonl'
            record.write_text(json.dumps({'effort': 'max', 'requestedModel': 'model'}), encoding='utf-8')
            probe.annotate_record_effort(record, 'high', ledger)
            stored = json.loads(record.read_text(encoding='utf-8'))
            self.assertEqual(stored['effort'], 'max')
            self.assertEqual(stored['attestedEffort'], 'high')
            self.assertFalse(stored['effortAttestationAgrees'])
            self.assertEqual(json.loads(ledger.read_text(encoding='utf-8'))['attestedEffort'], 'high')

    def test_b2_failed_annotation_appends_one_unannotated_ledger_row(self):
        with tempfile.TemporaryDirectory() as root:
            record = Path(root) / 'run.record.json'
            ledger = Path(root) / 'ledger.jsonl'
            record.write_text(json.dumps({'effort': 'max', 'requestedModel': 'model'}), encoding='utf-8')
            with patch.object(probe.os, 'replace', side_effect=OSError('planted rewrite failure')):
                rc = probe.main(['ccp_probe.py', 'record-effort', str(record), 'high', str(ledger)])
            self.assertEqual(rc, 1)
            rows = ledger.read_text(encoding='utf-8').splitlines() if ledger.exists() else []
            self.assertEqual(len(rows), 1)
            self.assertNotIn('attestedEffort', json.loads(rows[0]))
            self.assertEqual(json.loads(rows[0])['requestedModel'], 'model')

    def test_cli_envelope_identity_not_model_text(self):
        raw = json.dumps({'type': 'result', 'session_id': MINE, 'result': json.dumps({'session_id': PEER})})
        self.assertEqual(probe.session_from_output(raw), MINE)

    def test_verbose_json_array_output_yields_its_session(self):
        """`-o json` under the user setting `"verbose": true` is an array of events (live 2026-09-22:
        the codex sidecar lost its attestation because the array read as one non-dict record)."""
        raw = json.dumps([{'type': 'system', 'subtype': 'init', 'session_id': MINE},
                          {'type': 'assistant', 'message': {'content': []}},
                          {'type': 'result', 'session_id': MINE, 'result': json.dumps({'session_id': PEER})}])
        self.assertEqual(probe.session_from_output(raw), MINE)

    def test_mixed_session_output_is_not_guessed(self):
        raw = '\n'.join(json.dumps({'type': 'result', 'session_id': sid}) for sid in (MINE, PEER))
        self.assertIsNone(probe.session_from_output(raw))


if __name__ == '__main__':
    unittest.main(verbosity=2)
