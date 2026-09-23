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
BASH = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe") if os.path.exists(c)), "bash")
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


# `<proxy> models` as 0.1.39 and 0.1.42 print it (trimmed): the model list is compiled into the build.
MODELS_0139 = ('codex: claude-opus-5, gpt-5.6-luna, gpt-5.6-luna-fast, gpt-6-astra, gpt-6-astra-fast, haiku\n'
               'opencode: opencode-go/gpt-5.6-luna, glm-5\n')
MODELS_0142 = ('codex: claude-opus-5, gpt-5.6-luna, gpt-6-astra, gpt-6-luna, gpt-6-luna-fast, gpt-6-sol, haiku\n'
               'opencode: opencode-go/gpt-5.6-luna, glm-5\n')


class ProbeServesTests(unittest.TestCase):
    """A registry remap to gpt-6-luna reached a 0.1.39 proxy that does not serve it: every dispatch
    died on `400 Unknown model` while --check printed OK (2026-09-22 and again 2026-09-23)."""

    def test_the_build_that_lists_the_id_serves_it(self):
        self.assertTrue(probe.serves(MODELS_0142, 'gpt-6-luna'))

    def test_an_older_build_does_not_serve_a_newer_id(self):
        self.assertFalse(probe.serves(MODELS_0139, 'gpt-6-luna'))

    def test_a_prefixed_or_suffixed_id_is_not_the_id(self):
        self.assertFalse(probe.serves('codex: gpt-6-luna-fast\nopencode: opencode-go/gpt-6-luna\n', 'gpt-6-luna'))

    def test_the_check_names_the_proxy_and_the_id_when_it_is_not_served(self):
        with patch.object(probe, 'proxy_models_text', return_value=MODELS_0139), \
             patch.object(probe, 'user_scope_ccp_bin', return_value=None):
            rc, msg = probe.serves_check('C:/p/ccp-0.1.39.exe', 'gpt-6-luna')
        self.assertEqual(rc, 1)
        self.assertIn('ccp-0.1.39.exe', msg)
        self.assertIn('gpt-6-luna', msg)

    def test_the_check_names_a_newer_user_scope_proxy(self):
        """The session inherited CCP_BIN at launch; the owner has since pointed user scope at a newer build."""
        with patch.object(probe, 'proxy_models_text', return_value=MODELS_0139), \
             patch.object(probe, 'user_scope_ccp_bin', return_value='C:/p/ccp-0.1.42.exe'):
            rc, msg = probe.serves_check('C:/p/ccp-0.1.39.exe', 'gpt-6-luna')
        self.assertEqual(rc, 1)
        self.assertIn('C:/p/ccp-0.1.42.exe', msg)

    def test_an_unreadable_model_list_is_unverifiable_not_served(self):
        with patch.object(probe, 'proxy_models_text', return_value=''):
            rc, _ = probe.serves_check('C:/p/ccp.exe', 'gpt-6-luna')
        self.assertEqual(rc, 3)

    def test_a_served_id_passes(self):
        with patch.object(probe, 'proxy_models_text', return_value=MODELS_0142):
            self.assertEqual(probe.serves_check('C:/p/ccp.exe', 'gpt-6-luna')[0], 0)

    @unittest.skipUnless(os.name == 'nt', 'the stub proxy is a .cmd file')
    def test_the_launcher_check_refuses_a_proxy_that_does_not_serve_the_model(self):
        """Wiring: `codex_proxy_sidecar.sh --check` runs the served-model check before any quota read."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            stub = Path(d) / 'oldccp.cmd'
            stub.write_text('@echo off\r\nif "%1"=="models" echo codex: gpt-5.6-luna, gpt-6-astra\r\n', encoding='utf-8')
            env = dict(os.environ, CCP_BIN=stub.as_posix(), CCP_BIN_SCOPE='process')
            r = subprocess.run([BASH, (ROOT / '.claude/scripts/codex_proxy_sidecar.sh').as_posix(), '--check', '-m', 'luna'],
                               capture_output=True, text=True, env=env, timeout=120, cwd=str(ROOT))
        out = r.stdout + r.stderr
        if 'no ' in out and 'auth.json' in out:
            self.skipTest('no proxy login on this machine')
        self.assertEqual(r.returncode, 4, out)
        self.assertIn('gpt-6-luna', out)

    def test_user_scope_bin_cli_prints_the_owner_value(self):
        with patch.object(probe, 'user_scope_ccp_bin', return_value='C:/p/ccp-0.1.42.exe'), \
             patch('sys.stdout') as out:
            self.assertEqual(probe.main(['ccp_probe.py', 'user-scope-bin']), 0)
        self.assertIn('C:/p/ccp-0.1.42.exe', ''.join(c.args[0] for c in out.write.call_args_list))
        with patch.object(probe, 'user_scope_ccp_bin', return_value=None):
            self.assertEqual(probe.main(['ccp_probe.py', 'user-scope-bin']), 1)

    def test_the_launcher_reads_the_user_scope_bin_first(self):
        """A session launched before the owner moved CCP_BIN keeps the old value in its env; the hook's
        preflight inherits that env too, so the launcher itself must read the owner's current value."""
        text = (ROOT / '.claude/scripts/codex_proxy_sidecar.sh').read_text(encoding='utf-8')
        body = text[text.index('ccp_bin() {'):text.index('\n}\n', text.index('ccp_bin() {'))]
        self.assertLess(body.index('user-scope-bin'), body.index('CCP_BIN:-'))
        self.assertIn('CCP_BIN_SCOPE', body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
